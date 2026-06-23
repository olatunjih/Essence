"""LiteLLM,  A/B router, ,  cost tracker,  context budget."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403
from essence.infra.token_count import count_tokens  # noqa: F401  [real source bug: used below without import]

# LITELLM BACKEND
# ══════════════════════════════════════════════════════════════════════════════
# LiteLLM wraps 100+ LLM providers behind a single OpenAI-compatible interface
# with built-in retry, rate limiting, and cost tracking.
#
# Auto-selected when:
#   • Essence_BACKEND=litellm  is set, OR
#   • `litellm` is installed AND the provider is not a local-first backend
#     (Ollama / vLLM / MLX / llama-cpp remain the preferred T0–T2 path).
#
# ENV:  LITELLM_MODEL=claude-3-5-sonnet   override model within LiteLLM routing
#       LITELLM_API_KEY=...               forwarded as OPENAI_API_KEY if set
#       Essence_LITELLM_BUDGET_USD=5.0       per-session USD spend guard (0=off)

try:
    import litellm as _litellm_mod  # type: ignore
    _LITELLM = True
except ImportError:
    _litellm_mod = None  # type: ignore
    _LITELLM = False

_LITELLM_MODEL  = os.environ.get("LITELLM_MODEL", "")
_LITELLM_BUDGET = float(os.environ.get("Essence_LITELLM_BUDGET_USD", "0"))


class _LiteLLMBackend:
    """
    LiteLLM provider shim — exposes the same streaming interface as the
    built-in backend adapters so it can be swapped in via ProviderChain.

    Falls back gracefully to a no-op when litellm is not installed.
    """

    def __init__(self, model: str = "") -> None:
        self._model    = model or _LITELLM_MODEL or "gpt-4o-mini"
        self._ready    = _LITELLM
        self._spend    = 0.0   # cumulative USD tracked by litellm callbacks
        if self._ready and _LITELLM_BUDGET > 0:
            try:
                _litellm_mod.success_callback = []   # clear any existing
                _litellm_mod.set_verbose = False
            except Exception:
                pass

    @property
    def name(self) -> str:
        return f"litellm/{self._model}"

    def chat(self, messages: list[dict], stream: bool = True,
             tools: list | None = None) -> Iterator[str]:
        """Streaming chat via LiteLLM. Yields text chunks."""
        if not self._ready:
            yield "[litellm not installed — run: pip install litellm]"
            return
        kwargs: dict = {"model": self._model, "messages": messages, "stream": stream}
        if tools:
            kwargs["tools"]       = tools
            kwargs["tool_choice"] = "auto"
        if _LITELLM_BUDGET > 0 and self._spend >= _LITELLM_BUDGET:
            yield f"[litellm budget exceeded: ${self._spend:.4f} >= ${_LITELLM_BUDGET}]"
            return
        try:
            response = _litellm_mod.completion(**kwargs)
            if stream:
                for chunk in response:
                    delta = chunk.choices[0].delta
                    if hasattr(delta, "content") and delta.content:
                        yield delta.content
                    # Cost tracking via usage metadata when available
                    if hasattr(chunk, "_hidden_params"):
                        cost = chunk._hidden_params.get("response_cost", 0.0)
                        if cost:
                            self._spend += cost
            else:
                yield response.choices[0].message.content or ""
        except Exception as e:
            yield f"[litellm_error: {e}]"

    def spend(self) -> float:
        """Return cumulative USD spend tracked by LiteLLM callbacks."""
        return self._spend


# ══════════════════════════════════════════════════════════════════════════════
# CONTEXT BUDGET MANAGER
# ══════════════════════════════════════════════════════════════════════════════
# Coordinates how many tokens each context component receives:
#   system_prompt · skills · memory · conversation_history · tool_results
#
# Prevents quality degradation on T0/T1 devices (4K context) where injected
# context crowds out the actual conversation.
#
# Priority order (highest first): system_prompt > conversation > memory > skills
# When total exceeds budget, lower-priority components are truncated first.
#
# Usage:
#   budget = ContextBudgetManager(context_window=4096)
#   chunks = budget.allocate(
#       system_prompt=sp, skills=sk_text, memory=mem_text,
#       history=msg_list, tool_results=tr_text)

class ContextBudgetManager:
    """
    Token-budget-aware context assembler.

    Allocation ratios are configurable via env (Essence_CTX_*) and fall back
    to sensible defaults for each hardware tier.

    Approximate token counting: 1 token ≈ 4 chars (conservative estimate).
    Use `tiktoken` when available for precision; fall back to char-counting.
    """

    # Default allocation ratios (must sum ≤ 1.0; remainder goes to history)
    _DEFAULT_RATIOS = {
        "system_prompt": 0.15,
        "skills":        0.15,
        "memory":        0.20,
        "tool_results":  0.10,
        # conversation history gets the rest: 1 - sum(above) = 0.40
    }

    def __init__(self, context_window: int = 4096) -> None:
        self._window = context_window
        self._ratios = {
            "system_prompt": float(os.environ.get("Essence_CTX_SYSTEM",  "0.15")),
            "skills":        float(os.environ.get("Essence_CTX_SKILLS",  "0.15")),
            "memory":        float(os.environ.get("Essence_CTX_MEMORY",  "0.20")),
            "tool_results":  float(os.environ.get("Essence_CTX_TOOLS",   "0.10")),
        }
        history_ratio = max(0.05, 1.0 - sum(self._ratios.values()))
        self._ratios["history"] = history_ratio

    @staticmethod
    def _count_tokens(text: str) -> int:
        """Accurate token count via . Falls back to char/4."""
        return count_tokens(text)

    @staticmethod
    def _truncate(text: str, max_tokens: int) -> str:
        """Truncate text to approximately max_tokens, preserving whole words."""
        approx_chars = max_tokens * 4
        if len(text) <= approx_chars:
            return text
        return text[:approx_chars].rsplit(" ", 1)[0] + " …[truncated]"

    def allocate(self, *,
                 system_prompt: str = "",
                 skills:        str = "",
                 memory:        str = "",
                 history:       list[dict] | None = None,
                 tool_results:  str = "") -> dict[str, Any]:
        """
        Allocate the context window across all components.

        Returns a dict with truncated strings/lists ready for prompt assembly:
            {"system_prompt": str, "skills": str, "memory": str,
             "history": list[dict], "tool_results": str, "used_tokens": int}
        """
        budgets = {k: int(self._window * r) for k, r in self._ratios.items()}

        truncated_sp    = self._truncate(system_prompt, budgets["system_prompt"])
        truncated_sk    = self._truncate(skills,        budgets["skills"])
        truncated_mem   = self._truncate(memory,        budgets["memory"])
        truncated_tools = self._truncate(tool_results,  budgets["tool_results"])

        # History: keep most recent messages up to budget, preserving pairs
        hist_budget = budgets["history"]
        hist = list(history or [])
        hist_text = json.dumps(hist)
        while hist and self._count_tokens(hist_text) > hist_budget:
            # Drop oldest message (index 0) unless it's the system message
            if hist[0].get("role") == "system":
                hist.pop(1) if len(hist) > 1 else hist.pop(0)
            else:
                hist.pop(0)
            hist_text = json.dumps(hist)

        used = sum(self._count_tokens(t) for t in [
            truncated_sp, truncated_sk, truncated_mem, truncated_tools, hist_text
        ])

        return {
            "system_prompt": truncated_sp,
            "skills":        truncated_sk,
            "memory":        truncated_mem,
            "history":       hist,
            "tool_results":  truncated_tools,
            "used_tokens":   used,
            "budget":        self._window,
        }

    def summary(self) -> str:
        parts = [f"{k}={int(self._window*r)}" for k, r in self._ratios.items()]
        return f"ContextBudget(window={self._window}, {', '.join(parts)})"


# ══════════════════════════════════════════════════════════════════════════════
# A/B MODEL ROUTER
# ══════════════════════════════════════════════════════════════════════════════
# Routes requests between two (or more) models, tracks success rates per model,
# and auto-promotes the winner after a configurable trial window.
#
# Usage:
#   router = ABModelRouter(hw, provider_chain, workspace)
#   router.add_candidate("qwen3:14b", weight=0.2)   # 20% of requests
#   result = router.select()                          # returns model tag to use
#   router.record_outcome("qwen3:14b", success=True, score=0.85)
#   router.maybe_promote()                            # promotes if winner clear

class ModelTrialStats:
    """Running statistics for one model in an A/B trial."""
    def __init__(self, model: str, requests: int = 0, successes: int = 0,
                 total_score: float = 0.0, weight: float = 0.5) -> None:
        self.model       = model
        self.requests    = requests
        self.successes   = successes
        self.total_score = total_score
        self.weight      = weight
    def __repr__(self) -> str:
        return (f"ModelTrialStats(model={self.model!r}, requests={self.requests}, "
                f"avg_score={self.avg_score:.3f}, weight={self.weight})")

    @property
    def success_rate(self) -> float:
        return self.successes / max(self.requests, 1)

    @property
    def avg_score(self) -> float:
        return self.total_score / max(self.requests, 1)


class ABModelRouter:
    """
    A/B routing across two models with automatic winner promotion.
    Persists trial statistics to workspace so they survive restarts.
    Promotion threshold: winner needs ≥10% better avg_score AND ≥30 requests.
    """
    MIN_REQUESTS   = int(os.environ.get("Essence_AB_MIN_REQUESTS", "30"))
    PROMOTE_DELTA  = float(os.environ.get("Essence_AB_PROMOTE_DELTA", "0.10"))

    def __init__(self, workspace: Path) -> None:
        self._ws     = workspace
        self._stats: dict[str, ModelTrialStats] = {}
        self._lock   = threading.Lock()
        self._load()

    def _stats_path(self) -> Path:
        return self._ws / "logs" / "ab_trial_stats.json"

    def _load(self) -> None:
        p = self._stats_path()
        if p.exists():
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
                for model, d in raw.items():
                    self._stats[model] = ModelTrialStats(**d)
            except Exception as _e:
                log.debug("ab_router_stats_load_error", extra={"path": str(p), "error": str(_e)[:120]})

    def _save(self) -> None:
        p = self._stats_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            p.write_text(json.dumps(
                {m: {"model": s.model, "requests": s.requests, "successes": s.successes, "total_score": s.total_score, "weight": s.weight} for m, s in self._stats.items()},
                indent=2), encoding="utf-8")
        except Exception as _e:
            log.debug("ab_router_stats_save_error", extra={"path": str(p), "error": str(_e)[:120]})

    def add_candidate(self, model: str, weight: float = 0.5) -> None:
        with self._lock:
            if model not in self._stats:
                self._stats[model] = ModelTrialStats(model=model, weight=weight)
            else:
                self._stats[model].weight = weight

    def select(self) -> str:
        """Weighted random selection among registered candidates."""
        with self._lock:
            if not self._stats:
                return ""
            models  = list(self._stats.keys())
            weights = [self._stats[m].weight for m in models]
            total   = sum(weights)
            r = secrets.token_bytes(4)
            rnd = (int.from_bytes(r, "big") / (2**32)) * total
            cum = 0.0
            for m, w in zip(models, weights):
                cum += w
                if rnd < cum:
                    return m
            return models[-1]

    def record_outcome(self, model: str, success: bool,
                        score: float = 1.0) -> None:
        with self._lock:
            if model not in self._stats:
                self._stats[model] = ModelTrialStats(model=model)
            s = self._stats[model]
            s.requests   += 1
            s.successes  += 1 if success else 0
            s.total_score += score
            self._save()

    def maybe_promote(self) -> str | None:
        """
        Check if there's a clear winner; if so, promote it to weight=1.0.
        Returns the winning model tag, or None if no promotion yet.
        """
        with self._lock:
            if len(self._stats) < 2:
                return None
            candidates = [s for s in self._stats.values()
                          if s.requests >= self.MIN_REQUESTS]
            if len(candidates) < 2:
                return None
            candidates.sort(key=lambda s: s.avg_score, reverse=True)
            best, rest = candidates[0], candidates[1:]
            if all(best.avg_score - r.avg_score >= self.PROMOTE_DELTA
                   for r in rest):
                # Promote winner
                for m in self._stats:
                    self._stats[m].weight = 1.0 if m == best.model else 0.0
                self._save()
                log.info("ab_router_promoted",
                         extra={"model": best.model,
                                "score": round(best.avg_score, 3),
                                "requests": best.requests})
                return best.model
            return None

    def summary(self) -> str:
        with self._lock:
            parts = []
            for m, s in sorted(self._stats.items()):
                parts.append(
                    f"{m}: n={s.requests} "
                    f"score={s.avg_score:.2f} "
                    f"weight={s.weight:.1f}")
            return " | ".join(parts) if parts else "(no trials)"


# ══════════════════════════════════════════════════════════════════════════════
# CONTEXTUAL MULTI-ARMED BANDIT ROUTER
# ══════════════════════════════════════════════════════════════════════════════
# Upgrades the ABModelRouter's weighted-random selection to a LinUCB-inspired
# contextual bandit that incorporates task signals:
#   • complexity tier   (from ComplexityRouter: low/medium/high)
#   • latency history   (recent p50 per model)
#   • token cost        (tokens consumed per call)
#   • time-of-day       (hour bucket — heavier models tolerated off-peak)
#   • task success rate (per complexity tier, per model)
#
# The bandit maintains per-arm (model) per-context statistics and selects
# the arm with highest UCB score = mean_reward + exploration_bonus.
#
# Falls back to ABModelRouter.select() when no context is provided or when
# fewer than MIN_CONTEXT_REQUESTS observations exist for the context.
#
# ENV:
#   Essence_BANDIT_ALPHA=1.0   exploration coefficient (higher = more exploration)
#   Essence_BANDIT_MIN_N=5     min observations before bandit overrides A/B weight

_BANDIT_ALPHA   = float(os.environ.get("Essence_BANDIT_ALPHA", "1.0"))
_BANDIT_MIN_N   = int(os.environ.get("Essence_BANDIT_MIN_N",   "5"))


@_dc.dataclass
class _BanditArm:
    """Per-(model, context_bucket) statistics for the contextual bandit."""
    model:        str
    context_key:  str    # e.g. "complexity:high:hour:14"
    n:            int    = 0
    total_reward: float  = 0.0
    total_latency_ms: float = 0.0
    total_tokens: int   = 0

    @property
    def mean_reward(self) -> float:
        return self.total_reward / max(self.n, 1)

    @property
    def mean_latency_ms(self) -> float:
        return self.total_latency_ms / max(self.n, 1)

    @property
    def ucb(self) -> float:
        import math
        if self.n == 0:
            return float("inf")   # unexplored arm always wins UCB
        return self.mean_reward + _BANDIT_ALPHA * math.sqrt(math.log(self.n + 1) / self.n)


class ContextualBanditRouter:
    """
    LinUCB-lite contextual bandit model router.

    select(context) → model tag
    record(model, context, reward, latency_ms, tokens) → updates arm stats

    context dict keys (all optional):
      complexity: "low"|"medium"|"high"   (from ComplexityRouter)
      latency_sla: float                  (max acceptable latency in ms)
      hour: int                           (0–23, defaults to current hour)
    """

    def __init__(self, workspace: Path,
                 ab_router: ABModelRouter) -> None:
        self._ws       = workspace
        self._ab       = ab_router        # fallback for cold-start
        self._arms:    dict[str, _BanditArm] = {}
        self._lock     = threading.Lock()
        self._path     = workspace / "logs" / "bandit_state.json"
        self._load()

    def _context_key(self, context: dict) -> str:
        """archetype-enriched context bucket. v28.1 only read complexity+hour;
        v29.0 also folds in dataset_archetype / trust_score / domain_lens when present
        (agent.py already builds these into the context dict — previously discarded here)."""
        import datetime as _dt
        hour        = context.get("hour", _dt.datetime.now().hour)
        complexity  = context.get("complexity", "medium")
        hour_bucket = "peak" if 8 <= int(hour) <= 22 else "offpeak"
        key = f"complexity:{complexity}:time:{hour_bucket}"

        archetype = context.get("dataset_archetype")
        if archetype and archetype != "unknown":
            key += f":arch:{archetype}"

        trust = context.get("trust_score")
        if trust is not None:
            trust_bucket = "high" if trust >= 0.8 else "low" if trust < 0.5 else "med"
            key += f":trust:{trust_bucket}"

        domain_lens = context.get("domain_lens")
        if domain_lens and domain_lens != "none":
            key += f":lens:{domain_lens}"

        return key

    def _arm_key(self, model: str, ctx_key: str) -> str:
        return f"{model}::{ctx_key}"

    def _load(self) -> None:
        if self._path.exists():
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                for k, d in raw.items():
                    self._arms[k] = _BanditArm(**d)
            except Exception as _e:
                log.debug("bandit_load_error", extra={"error": str(_e)[:80]})

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._path.write_text(
                json.dumps({k: _dc.asdict(a) for k, a in self._arms.items()},
                           indent=2),
                encoding="utf-8")
        except Exception:
            pass

    def select(self, context: dict | None = None) -> str:
        """
        Select the best model for the given context using UCB.
        Falls back to ABModelRouter when insufficient data exists.
        """
        with self._lock:
            ctx_key  = self._context_key(context or {})
            models   = list(self._ab._stats.keys())
            if not models:
                return ""

            best_model = None
            best_ucb   = -1.0
            cold_start = False

            for model in models:
                ak   = self._arm_key(model, ctx_key)
                arm  = self._arms.get(ak)
                if arm is None or arm.n < _BANDIT_MIN_N:
                    cold_start = True
                    break
                if arm.ucb > best_ucb:
                    best_ucb   = arm.ucb
                    best_model = model

            if cold_start or best_model is None:
                # Not enough data — use A/B router's weighted random
                return self._ab.select()

            log.debug("bandit_select",
                      extra={"model": best_model, "ucb": round(best_ucb, 3),
                             "ctx": ctx_key})
            return best_model

    def record(self, model: str, context: dict,
               reward: float, latency_ms: float = 0.0, tokens: int = 0) -> None:
        """Record an outcome for (model, context) — updates UCB arm stats."""
        with self._lock:
            ctx_key = self._context_key(context)
            ak      = self._arm_key(model, ctx_key)
            if ak not in self._arms:
                self._arms[ak] = _BanditArm(model=model, context_key=ctx_key)
            arm                   = self._arms[ak]
            arm.n                += 1
            arm.total_reward     += reward
            arm.total_latency_ms += latency_ms
            arm.total_tokens     += tokens
            self._save()
        # Mirror into ABModelRouter for promotion logic
        self._ab.record_outcome(model, success=reward >= 0.5, score=reward)

    def summary(self) -> str:
        with self._lock:
            parts = []
            for ak, arm in sorted(self._arms.items()):
                parts.append(f"{ak}: n={arm.n} "
                              f"ucb={arm.ucb:.3f} "
                              f"reward={arm.mean_reward:.2f}")
            return " | ".join(parts) if parts else "(no bandit observations)"


# ══════════════════════════════════════════════════════════════════════════════
# COST TRACKER
# ══════════════════════════════════════════════════════════════════════════════
# Tracks input and output token counts per task. When a budget is set,
# raises BudgetExceededError when spend crosses the threshold so the
# WorkflowEngine can pause and queue a human decision rather than continuing
# to consume tokens unbounded.
#
# Offline-first: all accounting is local. No external billing API required.
# Costs use configurable per-model price tables (defaults match typical
# local inference where cost is compute-time, approximated as token count).
#
# ENV:  Essence_COST_BUDGET=50000   max tokens per task (0 = unlimited)
#       Essence_COST_LOG=1           write per-task spend to workspace/cost_log.jsonl

class BudgetExceededError(RuntimeError):
    """Raised by CostTracker when token spend exceeds the task budget."""
    def __init__(self, spent: int, budget: int) -> None:
        super().__init__(
            f"Task budget exceeded: {spent:,} tokens used of {budget:,} limit. "
            f"Raise Essence_COST_BUDGET or approve via DecisionQueue.")
        self.spent  = spent
        self.budget = budget


@_dc.dataclass
class TaskCost:
    """Accumulated cost record for a single task run."""
    task_id:      str
    model:        str
    prompt_tok:   int   = 0
    completion_tok: int = 0
    tool_calls:   int   = 0
    started_at:   float = _dc.field(default_factory=time.time)
    finished_at:  float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tok + self.completion_tok

    def to_dict(self) -> dict:
        return {
            "task_id":        self.task_id,
            "model":          self.model,
            "prompt_tok":     self.prompt_tok,
            "completion_tok": self.completion_tok,
            "tool_calls":     self.tool_calls,
            "total_tokens":   self.total_tokens,
            "started_at":     self.started_at,
            "finished_at":    self.finished_at,
            "duration_s":     round(self.finished_at - self.started_at, 2)
                              if self.finished_at else None,
        }


class CostTracker:
    """
    Thread-safe token cost accumulator with optional budget enforcement.

    Usage::
        tracker = CostTracker(workspace, budget=50_000)
        tracker.start_task("task-123", model="qwen3:8b")
        tracker.record(prompt_tokens=1024, completion_tokens=256)
        tracker.record_tool_call()
        tracker.finish_task("task-123")

    Budget enforcement::
        try:
            tracker.record(prompt_tokens=10_000, completion_tokens=0)
        except BudgetExceededError as e:
            # pause task and queue decision

    Cost log is written to workspace/cost_log.jsonl — one JSON record per task.
    """

    def __init__(self, workspace: Path,
                 budget: int = 0,
                 log_enabled: bool | None = None) -> None:
        self._ws          = workspace
        self._budget      = budget or _COST_BUDGET
        self._log_path    = workspace / "cost_log.jsonl"
        self._log_enabled = (
            log_enabled if log_enabled is not None
            else os.environ.get("Essence_COST_LOG", "1") == "1"
        )
        self._current:  dict[str, TaskCost] = {}
        self._lock      = threading.Lock()
        self._totals: dict[str, int] = {}   # task_id → running total

    # ── Lifecycle ─────────────────────────────────────────────────────────────
    def start_task(self, task_id: str, model: str = "") -> TaskCost:
        with self._lock:
            tc = TaskCost(task_id=task_id, model=model)
            self._current[task_id] = tc
            self._totals[task_id]  = 0
            log.debug("cost_task_started",
                      extra={"task_id": task_id, "model": model,
                             "budget": self._budget})
            return tc

    def finish_task(self, task_id: str) -> TaskCost | None:
        with self._lock:
            tc = self._current.pop(task_id, None)
            self._totals.pop(task_id, None)
            if tc:
                tc.finished_at = time.time()
                self._flush(tc)
                log.debug("cost_task_finished",
                          extra=tc.to_dict())
            return tc

    # ── Recording ─────────────────────────────────────────────────────────────
    def record(self, prompt_tokens: int = 0,
               completion_tokens: int = 0,
               task_id: str = "") -> None:
        """
        Record token usage for the active (or specified) task.
        Raises BudgetExceededError if cumulative spend exceeds budget.
        """
        with self._lock:
            # Find the target task
            tc: TaskCost | None = None
            if task_id and task_id in self._current:
                tc = self._current[task_id]
            elif len(self._current) == 1:
                tc = next(iter(self._current.values()))
            if tc is None:
                return  # no active task — silently skip

            tc.prompt_tok      += prompt_tokens
            tc.completion_tok  += completion_tokens
            self._totals[tc.task_id] = tc.total_tokens

            if self._budget > 0 and tc.total_tokens > self._budget:
                log.warning("cost_budget_exceeded",
                            extra={"task_id": tc.task_id,
                                   "spent": tc.total_tokens,
                                   "budget": self._budget})
                raise BudgetExceededError(tc.total_tokens, self._budget)

    def record_tool_call(self, task_id: str = "") -> None:
        """Increment tool call counter for the active task."""
        with self._lock:
            tc: TaskCost | None = None
            if task_id and task_id in self._current:
                tc = self._current[task_id]
            elif len(self._current) == 1:
                tc = next(iter(self._current.values()))
            if tc:
                tc.tool_calls += 1

    # ── Reporting ─────────────────────────────────────────────────────────────
    def current_spend(self, task_id: str = "") -> int:
        """Return current token count for the active (or specified) task."""
        with self._lock:
            if task_id:
                return self._totals.get(task_id, 0)
            if self._totals:
                return sum(self._totals.values())
            return 0

    def budget_remaining(self, task_id: str = "") -> int | None:
        """Return remaining token budget, or None if unlimited."""
        if not self._budget:
            return None
        return max(0, self._budget - self.current_spend(task_id))

    def history(self, n: int = 50) -> list[dict]:
        """Return last n completed task cost records from the log file."""
        if not self._log_path.exists():
            return []
        records = []
        try:
            for line in reversed(self._log_path.read_text(
                    encoding="utf-8").splitlines()):
                try:
                    records.append(json.loads(line))
                    if len(records) >= n:
                        break
                except Exception:
                    continue
        except Exception as _e:
            log.debug("cost_history_read_error", extra={"error": str(_e)[:80]})
        return list(reversed(records))

    def summary(self) -> dict:
        """Aggregate stats across all logged tasks."""
        records = self.history(n=1000)
        if not records:
            return {"tasks": 0, "total_tokens": 0, "avg_tokens": 0,
                    "total_tool_calls": 0}
        total_tok  = sum(r.get("total_tokens", 0) for r in records)
        total_calls = sum(r.get("tool_calls", 0) for r in records)
        return {
            "tasks":             len(records),
            "total_tokens":      total_tok,
            "avg_tokens":        total_tok // len(records) if records else 0,
            "total_tool_calls":  total_calls,
        }

    # ── Internal ──────────────────────────────────────────────────────────────
    def _flush(self, tc: TaskCost) -> None:
        if not self._log_enabled:
            return
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(tc.to_dict()) + "\n")
        except Exception as _e:
            log.debug("cost_flush_error", extra={"error": str(_e)[:80]})


# Module-level singleton — initialised lazily by first Agent instantiation
_cost_tracker: CostTracker | None = None

def get_cost_tracker(workspace: Path | None = None,
                     budget: int = 0) -> CostTracker:
    """Return the module-level CostTracker singleton, creating it if needed."""
    global _cost_tracker
    if _cost_tracker is None:
        ws = workspace or Path.home() / ".essence"
        _cost_tracker = CostTracker(ws, budget=budget)
    return _cost_tracker


# ══════════════════════════════════════════════════════════════════════════════
# CAPABILITY-BASED MODEL ROUTER
# ══════════════════════════════════════════════════════════════════════════════
# ModelRouter wraps ContextualBanditRouter with capability-based model
# selection. Different call classes require different model capabilities:
#   PLAN   → needs reasoning capability
#   EXEC   → needs tool-use capability
#   VERIFY → needs long-context capability
#   EMBED  → needs embedding capability

import enum as _enum
import dataclasses as _capability_dc


class Capability(_enum.Enum):
    """Model capability tags used for capability-based routing."""
    REASONING   = "reasoning"     # multi-step chain-of-thought
    TOOL_USE    = "tool_use"      # function/tool calling
    LONG_CONTEXT = "long_context" # 32K+ context window
    CODE        = "code"          # code generation/analysis
    VISION      = "vision"        # image understanding
    EMBEDDING   = "embedding"     # text embedding (not generation)
    FAST        = "fast"          # low-latency, lightweight
    CREATIVE    = "creative"      # creative text generation


@_capability_dc.dataclass
class ModelInfo:
    """Metadata about a model and its capability set."""
    model_id:     str
    capabilities: set  # set[Capability]
    context_k:    int   = 128   # context window in thousands of tokens
    cost_tier:    str   = "low" # "low" | "medium" | "high"
    provider:     str   = ""

    def has(self, cap: "Capability") -> bool:
        return cap in self.capabilities


# Default model capability registry — can be overridden via workspace config
_DEFAULT_MODEL_REGISTRY: list["ModelInfo"] = [
    ModelInfo(
        model_id="qwen3:4b",
        capabilities={Capability.REASONING, Capability.CODE, Capability.FAST},
        context_k=32, cost_tier="low", provider="ollama",
    ),
    ModelInfo(
        model_id="qwen3:8b",
        capabilities={Capability.REASONING, Capability.TOOL_USE, Capability.CODE},
        context_k=32, cost_tier="low", provider="ollama",
    ),
    ModelInfo(
        model_id="qwen3:14b",
        capabilities={
            Capability.REASONING, Capability.TOOL_USE,
            Capability.CODE, Capability.LONG_CONTEXT,
        },
        context_k=128, cost_tier="medium", provider="ollama",
    ),
    ModelInfo(
        model_id="gpt-4o",
        capabilities={
            Capability.REASONING, Capability.TOOL_USE, Capability.CODE,
            Capability.VISION, Capability.LONG_CONTEXT, Capability.CREATIVE,
        },
        context_k=128, cost_tier="high", provider="openai",
    ),
    ModelInfo(
        model_id="claude-3-5-sonnet",
        capabilities={
            Capability.REASONING, Capability.TOOL_USE, Capability.CODE,
            Capability.LONG_CONTEXT, Capability.CREATIVE,
        },
        context_k=200, cost_tier="high", provider="anthropic",
    ),
]

# call_class → required capability
_CALL_CLASS_CAPABILITY: dict[str, "Capability"] = {
    "PLAN":   Capability.REASONING,
    "EXEC":   Capability.TOOL_USE,
    "VERIFY": Capability.LONG_CONTEXT,
    "EMBED":  Capability.EMBEDDING,
}


class ModelRouter:
    """
    Capability-based model router wrapping ContextualBanditRouter.

    Selection priority:
    1. Filter models to those meeting the required capability for call_class.
    2. From filtered candidates, use ContextualBanditRouter UCB selection.
    3. Fall back to any available model when no capability-matched one exists.

    Usage:
        router = ModelRouter()
        model = router.select(call_class="PLAN", context={"domain": "finance"})
    """

    def __init__(self,
                 bandit: ContextualBanditRouter | None = None,
                 registry: list[ModelInfo] | None = None) -> None:
        self._bandit   = bandit or ContextualBanditRouter()
        self._registry = registry or list(_DEFAULT_MODEL_REGISTRY)

    def select(self, call_class: str = "EXEC",
               context: dict | None = None) -> str:
        """Select the best model for the call class using capability filtering + UCB."""
        required_cap = _CALL_CLASS_CAPABILITY.get(call_class.upper())

        # Filter to models meeting the required capability
        if required_cap is not None:
            candidates = [m for m in self._registry if m.has(required_cap)]
        else:
            candidates = list(self._registry)

        if not candidates:
            # No capability match — use bandit without filtering
            return self._bandit.select(context)

        # Restrict bandit's known models to our capability-filtered candidates
        candidate_ids = {m.model_id for m in candidates}
        ctx = dict(context or {})
        ctx["_capability_filter"] = ",".join(sorted(candidate_ids))

        # Use bandit for UCB selection among candidates
        selected = self._bandit.select(ctx)
        if selected and selected in candidate_ids:
            return selected

        # Bandit selected a non-candidate or hasn't warmed up — use cost-tier fallback
        low_tier = [m for m in candidates if m.cost_tier == "low"]
        if low_tier:
            return low_tier[0].model_id
        return candidates[0].model_id

    def record(self, model: str, call_class: str,
               context: dict, reward: float, latency_ms: float = 0.0) -> None:
        """Record an outcome to the underlying bandit."""
        self._bandit.record(model, context, reward, latency_ms)

    def list_models(self) -> list[dict]:
        return [
            {
                "model_id":     m.model_id,
                "capabilities": [c.value for c in m.capabilities],
                "context_k":    m.context_k,
                "cost_tier":    m.cost_tier,
                "provider":     m.provider,
            }
            for m in self._registry
        ]


