""" RewardSignal, PromptEvolution, WorkflowCompressor, SharedBlackboard, ReasoningScratchpad.
v29.0: dual-track analytical reward + archetype-specific prompt pools; see wiring doc """
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403

from essence.analytics.spine import get_analytical_spine  # noqa: F401
# REWARD SIGNAL SYSTEM
# ══════════════════════════════════════════════════════════════════════════════

@_dc.dataclass
class RewardSignal:
    "Computed reward for a completed task."
    task_id:               str
    reward:                float
    critic_pass_rate:      float = 0.0
    tool_success_rate:     float = 0.0
    token_efficiency:      float = 0.0
    hallucination_penalty: float = 0.0
    user_feedback:         float = 0.5
    model:                 str   = ""
    ts:                    float = _dc.field(default_factory=time.time)
    analytical_quality:    float = 0.0
    novelty_score:         float = 0.0
    robustness_score:      float = 0.0

def compute_reward(observer: "AgentObserver", budget: int = 0, user_feedback: float = 0.5, spine: AnalyticalStateBus | None = None) -> RewardSignal:
    "Compute composite reward from AgentObserver session summary (v29 analytical-aware)."
    s = observer.summary()
    cr = s.get("critic_pass_rate", 1.0)
    tr = 1.0 - s.get("tool_failure_rate", 0.0)
    tot = s.get("total_tokens_in", 0) + s.get("total_tokens_out", 0)
    eff = min(1.0, budget / max(tot, 1)) if budget > 0 and tot > 0 else 0.8
    hp = max(0.0, 1.0 - s.get("hallucination_count", 0) * 0.2)

    # v29 Analytical Quality
    aq = 0.0
    ns = 0.0
    rs = 0.0
    if spine and spine.active_findings:
        aq = sum(f.calibrated_confidence for f in spine.active_findings) / len(spine.active_findings)
        ns = sum(f.novelty for f in spine.active_findings) / len(spine.active_findings)
        rs = sum(f.robustness for f in spine.active_findings) / len(spine.active_findings)

    reward = 0.25 * cr + 0.20 * tr + 0.15 * eff + 0.10 * hp + 0.10 * user_feedback + 0.20 * aq

    return RewardSignal(task_id=s.get("session_id", ""), reward=round(max(0.0, min(1.0, reward)), 4),
        critic_pass_rate=round(cr, 4), tool_success_rate=round(tr, 4),
        token_efficiency=round(eff, 4), hallucination_penalty=round(hp, 4),
        user_feedback=user_feedback, model=s.get("model", ""),
        analytical_quality=round(aq, 4), novelty_score=round(ns, 4), robustness_score=round(rs, 4))

# ══════════════════════════════════════════════════════════════════════════════

# PROMPT EVOLUTION
# ══════════════════════════════════════════════════════════════════════════════

_PROMPT_EVOLVE_ENABLED = os.environ.get("Essence_PROMPT_EVOLVE", "0") == "1"

@_dc.dataclass
class PromptVariant:
    "A prompt variant in the evolution population."
    variant_id: str; role: str; text: str
    trials: int = 0; total_reward: float = 0.0
    created_at: float = _dc.field(default_factory=time.time)
    @property
    def avg_reward(self) -> float: return self.total_reward / max(self.trials, 1)

class PromptEvolution:
    "Evolutionary prompt optimisation with UCB1 selection."
    POPULATION_SIZE = int(os.environ.get("Essence_PROMPT_POP", "4"))
    MIN_TRIALS = 5
    def __init__(self, workspace: "Path"):
        self._ws = workspace; self._variants: dict[str, list[PromptVariant]] = {}
        self._lock = threading.Lock(); self._path = workspace / "logs" / "prompt_variants.json"
        self._load()
    def _load(self) -> None:
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                for role, vs in data.items(): self._variants[role] = [PromptVariant(**v) for v in vs]
            except Exception: pass
    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps({r: [_dc.asdict(v) for v in vs] for r, vs in self._variants.items()}, indent=2), encoding="utf-8")
        except Exception: pass
    def seed(self, role: str, base_prompt: str) -> None:
        with self._lock:
            if role not in self._variants or not self._variants[role]:
                self._variants[role] = [PromptVariant(variant_id=f"{role}_v0", role=role, text=base_prompt)]
                self._save()
    def select(self, role: str) -> str:
        import math
        with self._lock:
            vs = self._variants.get(role, [])
            if not vs: return ""
            total = sum(v.trials for v in vs) or 1
            return max(vs, key=lambda v: v.avg_reward + math.sqrt(2 * math.log(total) / max(v.trials, 1))).text
    def record(self, role: str, prompt_text: str, reward: float) -> None:
        with self._lock:
            for v in self._variants.get(role, []):
                if v.text == prompt_text: v.trials += 1; v.total_reward += reward; break
            self._save()

# ══════════════════════════════════════════════════════════════════════════════

# WORKFLOW COMPRESSION
# ══════════════════════════════════════════════════════════════════════════════

_WF_COMPRESS_THRESHOLD = int(os.environ.get("Essence_WF_COMPRESS_THRESHOLD", "3"))

@_dc.dataclass
class WorkflowPattern:
    "A detected repeated workflow pattern."
    pattern_hash: str; tool_sequence: list[str]; action_summary: str
    occurrences: int = 0; last_seen: float = _dc.field(default_factory=time.time)
    compressed: bool = False

class WorkflowCompressor:
    "Detects repeated multi-step patterns and compresses into skill templates."
    def __init__(self, workspace: "Path"):
        self._ws = workspace; self._patterns: dict[str, WorkflowPattern] = {}
        self._lock = threading.Lock(); self._path = workspace / "logs" / "workflow_patterns.json"
        self._load()
    def _load(self) -> None:
        if self._path.exists():
            try:
                for k, v in json.loads(self._path.read_text(encoding="utf-8")).items():
                    self._patterns[k] = WorkflowPattern(**v)
            except Exception: pass
    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps({k: _dc.asdict(v) for k, v in self._patterns.items()}, indent=2), encoding="utf-8")
        except Exception: pass
    def record(self, steps: "list[WorkflowStep]", arch_id: str = "") -> str | None:
        """Detect repeated patterns and generate a skill template (v29 archetype-aware)."""
        if len(steps) < 2: return None
        seq = [f"{s.tool}:{(s.action.split()[0].lower() if s.action else 'do')}" for s in steps]
        # v29: Archetype-aware pattern hashing
        data = "|".join(seq) + (f"|{arch_id}" if arch_id else "")
        ph = hashlib.sha256(data.encode()).hexdigest()[:16]
        with self._lock:
            wp = self._patterns.get(ph)
            if wp: wp.occurrences += 1; wp.last_seen = time.time()
            else:
                wp = WorkflowPattern(pattern_hash=ph, tool_sequence=[s.tool for s in steps],
                    action_summary=" > ".join(s.action[:40] for s in steps), occurrences=1)
                self._patterns[ph] = wp
            self._save()
            if wp.occurrences >= _WF_COMPRESS_THRESHOLD and not wp.compressed:
                wp.compressed = True; self._save()
                tools = sorted(set(wp.tool_sequence))
                steps_md = "\n".join(f"{i+1}. {s.action}" for i, s in enumerate(steps))
                tools_str = ", ".join(tools)
                return f"---\nname: auto-{ph[:8]}\ndescription: Auto-pattern (seen {wp.occurrences}x)\ntools: [{tools_str}]\n---\n\n## Steps\n{steps_md}\n"
        return None

# ══════════════════════════════════════════════════════════════════════════════

# SHARED BLACKBOARD
# ══════════════════════════════════════════════════════════════════════════════

@_dc.dataclass
class BlackboardEntry:
    "An entry on the shared blackboard."
    key: str; value: Any; entry_type: str = "fact"; author: str = "system"
    ts: float = _dc.field(default_factory=time.time); confidence: float = 1.0

class SharedBlackboard:
    "Thread-safe shared workspace for multi-agent collaborative reasoning."
    def __init__(self) -> None:
        self._entries: dict[str, BlackboardEntry] = {}; self._lock = threading.RLock()
    def write(self, key: str, value: Any, entry_type: str = "fact", author: str = "system", confidence: float = 1.0) -> None:
        with self._lock: self._entries[key] = BlackboardEntry(key=key, value=value, entry_type=entry_type, author=author, confidence=confidence)
    def read(self, key: str, default: Any = None) -> Any:
        with self._lock:
            e = self._entries.get(key); return e.value if e else default
    def read_all(self, entry_type: str | None = None) -> list[BlackboardEntry]:
        with self._lock:
            return [e for e in self._entries.values() if not entry_type or e.entry_type == entry_type]
    def clear(self) -> None:
        with self._lock: self._entries.clear()

    def simulate(self, key: str, value: Any) -> dict:
        """v30: Return a hypothetical state of the blackboard after an update."""
        with self._lock:
            sim = {k: e.value for k, e in self._entries.items()}
            sim[key] = value
            return sim
    def to_context(self, max_entries: int = 20) -> str:
        with self._lock:
            if not self._entries: return ""
            lines = ["[Shared Blackboard]"]
            for e in list(self._entries.values())[-max_entries:]:
                lines.append(f"  [{e.entry_type}] {e.key}: {str(e.value)[:200]} (by {e.author})")
            return "\n".join(lines)

# ══════════════════════════════════════════════════════════════════════════════

# REASONING SCRATCHPAD
# ══════════════════════════════════════════════════════════════════════════════

class ReasoningScratchpad:
    "Dedicated buffer for chain-of-thought reasoning steps."
    def __init__(self, max_entries: int = 50):
        self._entries: list[dict] = []; self._max = max_entries
    def note(self, thought: str, kind: str = "reasoning", step_id: int = 0) -> None:
        self._entries.append({"thought": thought[:500], "kind": kind, "step_id": step_id, "ts": time.time()})
        if len(self._entries) > self._max * 2: self._entries = self._entries[-self._max:]
    def review(self, last_n: int = 10) -> list[dict]: return self._entries[-last_n:]
    def to_context(self, last_n: int = 5) -> str:
        entries = self.review(last_n)
        if not entries: return ""
        lines = ["[Reasoning Scratchpad]"]
        for e in entries: lines.append(f"  [{e['kind']}] {e['thought']}")
        return "\n".join(lines)
    def clear(self) -> None: self._entries.clear()


# ══════════════════════════════════════════════════════════════════════════════

# ──  Analytics Engine analytical reward delegation ────────────────────────────────────
# compute_reward() now delegates to compute_analytical_reward() when the
# Analytics Engine spine is available, returning a DecomposedReward whose .composite
# is backward-compatible with all v28.1 consumers.
try:
    from essence.analytics.analytical_reward import (   # noqa: F401
        compute_analytical_reward as _compute_analytical_reward,
        DecomposedReward,
        DelayedRewardQueue,
        route_decomposed_reward,
    )

    def compute_reward(observer, budget: int = 0,
                       user_feedback: float = 0.5,
                       spine=None,
                       genesis_feedback=None,
                       delayed_queue=None):
        """
        v29.0 wrapper: delegates to compute_analytical_reward().
        Returns DecomposedReward (has .composite for backward compat).
        Falls back to v28.1 scalar RewardSignal if Analytics Engine unavailable.
        """
        return _compute_analytical_reward(
            observer=observer,
            spine=spine,
            budget=budget,
            user_feedback=user_feedback,
            genesis_feedback=genesis_feedback,
            delayed_queue=delayed_queue,
        )

except ImportError:
    pass  # prism not yet available; original compute_reward preserved

# ──  AnalyticalPromptEvolver integration ───────────────────────────────────
try:
    from essence.analytics.analytical_prompt_evolver import (  # noqa: F401
        AnalyticalPromptEvolver,
        ANALYTICAL_ROLES,
    )
except ImportError:
    pass
