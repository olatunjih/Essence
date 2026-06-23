# essence.analytics.analytical_prompt_evolver
"""
  Analytical Prompt Evolution (Analytics Engine × Essence wiring doc )
=============================================================
Extends the v28.1 PromptEvolution (UCB1 bandit over prompt variants) with
four capabilities:

  1. Analytical prompt variant roles
     Manages prompts for Analytics Engine-specific roles alongside the standard set:
       planner_analytical  — "When analysing data, prioritise {strategy}"
       critic_analytical   — "Check for {failure_types_for_archetype}"
       narrator_analytical — "Explain findings using {domain_lens.templates}"
       scenario_analytical — "Generate what-if scenarios focusing on {hub_features}"

  2. Analytics Engine-informed variant generation
     Learning Engine feeds prompt generation: patterns observed in high-reward
     tasks for a specific archetype are injected as candidate phrases,
     and A/B tested against the current best variant.

  3. Decomposed reward routing
     Each role's bandit arm receives its OWN reward dimension
     (not the composite):
       planner prompts  → task_quality + efficiency
       critic prompts   → analytical_quality
       narrator prompts → user_feedback
       scenario prompts → novelty_score

  4. Archetype-specific variant pools
     Separate UCB1 populations per archetype prevent a prompt optimised
     for financial data from contaminating medical or sports data analysis.

Architecture
------------
AnalyticalPromptEvolver wraps the existing PromptEvolution instance and
adds an archetype dimension to every record() and select() call.
It is a thin overlay — the underlying UCB1 arithmetic lives in the
monolith's PromptEvolution and is reused via delegation.
"""
from __future__ import annotations
from essence._shared import *      # noqa: F401,F403

import dataclasses as _dc
import math
import uuid
import collections

from essence.analytics.layers import AnalyticalStateBus

_log = _setup_logging("essence.analytics.analytical_prompt_evolver")

# ─── analytical role definitions ─────────────────────────────────────────────

ANALYTICAL_ROLES: list[str] = [
    "planner_analytical",
    "critic_analytical",
    "narrator_analytical",
    "scenario_analytical",
]

# which DecomposedReward field scores each role
_ROLE_REWARD_DIM: dict[str, str] = {
    "planner_analytical":  "task_quality",
    "critic_analytical":   "analytical_quality",
    "narrator_analytical": "user_feedback",
    "scenario_analytical": "novelty_score",
    # standard v28.1 roles — composite as before
    "planner":   "task_quality",
    "critic":    "analytical_quality",
    "narrator":  "user_feedback",
    "executor":  "efficiency",
    "verifier":  "analytical_quality",
}

# default analytical prompt templates per role
_DEFAULT_ANALYTICAL_TEMPLATES: dict[str, str] = {
    "planner_analytical": (
        "You are an analytical planning agent. "
        "Dataset archetype: {archetype}. "
        "When planning this task, prioritise {strategy}. "
        "Trust score of available data: {trust_score:.2f}. "
        "Begin with reconnaissance, then proceed to depth."
    ),
    "critic_analytical": (
        "You are an analytical critic. "
        "Check step results for: {failure_categories}. "
        "Dataset archetype: {archetype}. "
        "Analytics Engine trust score: {trust_score:.2f}. "
        "Flag StatisticalOverclaim, ConfidenceInflation, CausalOverreach."
    ),
    "narrator_analytical": (
        "You are explaining analytical findings to the user. "
        "Domain: {domain_lens}. Archetype: {archetype}. "
        "Use concrete examples. Report calibrated confidence, not raw scores. "
        "Mention uncertainty explicitly."
    ),
    "scenario_analytical": (
        "Generate what-if scenarios for the user's dataset. "
        "Archetype: {archetype}. Focus on hub features: {hub_features}. "
        "For each scenario state: the perturbation, expected direction, "
        "confidence, and limiting assumptions."
    ),
}

# ─── UCB1 arm for a single prompt variant ─────────────────────────────────────

@_dc.dataclass
class PromptArm:
    """A single UCB1 arm representing one prompt variant."""
    arm_id:       str
    role:         str
    archetype:    str
    text:         str
    trials:       int   = 0
    total_reward: float = 0.0
    created_at:   float = _dc.field(default_factory=time.time)

    @property
    def avg_reward(self) -> float:
        return self.total_reward / max(1, self.trials)

    def ucb1(self, total_trials: int, c: float = 1.41) -> float:
        if self.trials == 0:
            return float("inf")
        return self.avg_reward + c * math.sqrt(
            math.log(max(1, total_trials)) / self.trials)


# ─── archetype-keyed pool ─────────────────────────────────────────────────────

class ArchetypePromptPool:
    """
    UCB1 variant pool for one (role, archetype) pair.
    Maintains up to max_arms variants and selects via UCB1 + ε-greedy.
    """

    def __init__(self, role: str, archetype: str,
                 max_arms: int = 10, epsilon: float = 0.10) -> None:
        self.role      = role
        self.archetype = archetype
        self.max_arms  = max_arms
        self.epsilon   = epsilon
        self._arms:  list[PromptArm] = []
        self._total: int = 0
        self._lock   = threading.Lock()

    def add(self, text: str) -> PromptArm:
        with self._lock:
            # Evict the worst arm if at capacity
            if len(self._arms) >= self.max_arms:
                self._arms.sort(key=lambda a: a.avg_reward)
                self._arms.pop(0)
            arm = PromptArm(
                arm_id=str(uuid.uuid4())[:8],
                role=self.role,
                archetype=self.archetype,
                text=text,
            )
            self._arms.append(arm)
            return arm

    def select(self) -> PromptArm | None:
        with self._lock:
            if not self._arms:
                return None
            import random
            if random.random() < self.epsilon:
                return random.choice(self._arms)
            return max(self._arms, key=lambda a: a.ucb1(self._total))

    def record(self, arm_id: str, reward: float) -> None:
        with self._lock:
            for arm in self._arms:
                if arm.arm_id == arm_id:
                    arm.trials       += 1
                    arm.total_reward += reward
                    self._total      += 1
                    return

    def best(self) -> PromptArm | None:
        with self._lock:
            if not self._arms:
                return None
            return max((a for a in self._arms if a.trials > 0),
                       key=lambda a: a.avg_reward, default=self._arms[0])

    def __len__(self) -> int:
        return len(self._arms)


# ─── main class ──────────────────────────────────────────────────────────────

class AnalyticalPromptEvolver:
    """
     — Archetype-aware prompt evolution overlay.

    Usage
    -----
    evolver = AnalyticalPromptEvolver(spine=spine)

    # Get a rendered prompt for the current task
    prompt = evolver.select_prompt("planner_analytical")

    # After task completion, record decomposed reward
    evolver.record_reward(arm_id, "planner_analytical", decomposed_reward)

    # Inject a Learning Engine-suggested phrase into a new candidate
    evolver.inject_candidate("planner_analytical", "include distribution-aware sampling")

    Integration
    -----------
    In Agent.run_task() Step 6 (REWARD → MEMORY):
      evolver.record_reward(
          arm_id        = selected_arm_id,
          role          = "planner_analytical",
          reward        = decomposed_reward,
      )
      evolver.inject_from_genesis(archetype, genesis_insight_phrase)
    """

    def __init__(self,
                 spine:       AnalyticalStateBus | None = None,
                 base_evol:   Any | None = None,
                 max_arms:    int   = 10,
                 epsilon:     float = 0.10) -> None:
        self.spine     = spine or AnalyticalStateBus()
        self.base_evol = base_evol   # existing v28.1 PromptEvolution instance
        self.max_arms  = max_arms
        self.epsilon   = epsilon
        # pools[(role, archetype)] → ArchetypePromptPool
        self._pools: dict[tuple[str, str], ArchetypePromptPool] = {}
        self._lock = threading.Lock()
        self._initialise_defaults()

    # ── public API ────────────────────────────────────────────────────────

    def select_prompt(self, role: str, archetype: str | None = None,
                      context: dict | None = None) -> tuple[str, str]:
        """
        Select the best prompt variant for (role, archetype) via UCB1.

        Returns
        -------
        (rendered_prompt_text, arm_id)
        arm_id is passed back to record_reward() after task completion.
        """
        arch = archetype or self.spine.active_archetype or "generic"
        pool = self._get_or_create_pool(role, arch)
        arm  = pool.select()
        if arm is None:
            tmpl = _DEFAULT_ANALYTICAL_TEMPLATES.get(role, "{role} prompt")
            text = self._render(tmpl, arch, context)
            arm  = pool.add(text)
        rendered = self._render(arm.text, arch, context)
        return rendered, arm.arm_id

    def record_reward(self, arm_id: str, role: str,
                      reward: Any,              # DecomposedReward or float
                      archetype: str | None = None) -> None:
        """
        Record reward for an arm.  Extracts the role-specific dimension
        from a DecomposedReward; accepts a plain float for backward compat.
        """
        arch = archetype or self.spine.active_archetype or "generic"
        dim  = _ROLE_REWARD_DIM.get(role, "composite")
        score = (getattr(reward, dim, None)
                 if not isinstance(reward, float)
                 else reward)
        if score is None:
            score = getattr(reward, "composite", float(reward))

        pool = self._get_or_create_pool(role, arch)
        pool.record(arm_id, float(score))

        # Also forward to base PromptEvolution for backward compat
        if self.base_evol is not None and hasattr(self.base_evol, "record"):
            try:
                self.base_evol.record(role, score)
            except Exception:
                pass

        _log.debug("AnalyticalPromptEvolver: role=%s arch=%s arm=%s score=%.3f",
                   role, arch, arm_id, score)

    def inject_candidate(self, role: str, candidate_phrase: str,
                         archetype: str | None = None) -> str:
        """
        Inject a Learning Engine-suggested phrase into a new prompt variant.
        Returns the new arm_id.
        """
        arch = archetype or self.spine.active_archetype or "generic"
        tmpl = _DEFAULT_ANALYTICAL_TEMPLATES.get(role, "")
        # Append the phrase to the existing best variant's text
        pool = self._get_or_create_pool(role, arch)
        base = pool.best()
        base_text = base.text if base else tmpl
        new_text  = base_text.rstrip() + f" {candidate_phrase}"
        arm = pool.add(new_text)
        _log.debug("inject_candidate: role=%s arch=%s arm=%s phrase=%r",
                   role, arch, arm.arm_id, candidate_phrase[:60])
        return arm.arm_id

    def inject_from_genesis(self, archetype: str, insight: str) -> None:
        """
        Convenience: inject a Learning Engine insight into all analytical roles
        for the given archetype simultaneously.
        """
        for role in ANALYTICAL_ROLES:
            self.inject_candidate(role, insight, archetype=archetype)

    def inject_finding_context(self, role: str,
                                archetype: str | None = None) -> tuple[str, str]:
        """
        Build a prompt that dynamically injects active Analytics Engine findings.
        Returns (rendered_prompt, arm_id).

        This is the  "prompt finding injection" from the wiring doc:
        active findings are injected at call-time, not baked into the
        stored variant.
        """
        arch    = archetype or self.spine.active_archetype or "generic"
        base_p, arm_id = self.select_prompt(role, arch)

        findings = sorted(self.spine.active_findings,
                          key=lambda f: getattr(f, "impact", 0), reverse=True)
        if not findings:
            return base_p, arm_id

        finding_block = "Analytics Engine context for this task:\n"
        for f in findings[:5]:
            conf  = getattr(f, "calibrated_confidence", getattr(f, "confidence", 0))
            title = getattr(f, "title", str(f))
            finding_block += f"  • [{conf:.0%}] {title}\n"

        injected = f"{finding_block}\n{base_p}"
        return injected, arm_id

    def pool_stats(self) -> list[dict]:
        """Return statistics for all active pools (for Observer telemetry)."""
        with self._lock:
            return [
                {
                    "role":       pool.role,
                    "archetype":  pool.archetype,
                    "arms":       len(pool),
                    "total_trials": pool._total,
                    "best_reward": pool.best().avg_reward if pool.best() else 0.0,
                }
                for pool in self._pools.values()
            ]

    # ── private helpers ───────────────────────────────────────────────────

    def _get_or_create_pool(self, role: str,
                             archetype: str) -> ArchetypePromptPool:
        key = (role, archetype)
        with self._lock:
            if key not in self._pools:
                self._pools[key] = ArchetypePromptPool(
                    role=role, archetype=archetype,
                    max_arms=self.max_arms, epsilon=self.epsilon)
                # Seed with the default template
                tmpl = _DEFAULT_ANALYTICAL_TEMPLATES.get(role, "")
                if tmpl:
                    self._pools[key].add(tmpl)
            return self._pools[key]

    def _render(self, template: str, archetype: str,
                context: dict | None = None) -> str:
        """
        Fill template placeholders from the Analytical Spine + context.
        Safe: unknown placeholders are left as-is.
        """
        ctx = context or {}
        spine = self.spine

        # Gather sub-values
        domain_lens  = getattr(spine.active_lens, "name", "general") \
                       if spine.active_lens else "general"
        strategy     = (spine.genesis_strategy.layer_ranking[:3]
                        if spine.genesis_strategy.layer_ranking
                        else ["L0", "L1", "L3"])
        trust_score  = spine.trust_score

        findings = sorted(spine.active_findings,
                          key=lambda f: getattr(f, "impact", 0), reverse=True)
        hub_features = ", ".join(
            getattr(f, "title", "")[:40]
            for f in findings[:3]
        ) or "top correlated features"

        failure_cats = ", ".join([
            "StatisticalOverclaim", "ConfidenceInflation",
            "CausalOverreach", "StalePatternApplication",
        ])

        replacements = {
            "{archetype}":          archetype,
            "{strategy}":           str(strategy),
            "{trust_score:.2f}":    f"{trust_score:.2f}",
            "{trust_score}":        f"{trust_score:.2f}",
            "{domain_lens}":        domain_lens,
            "{hub_features}":       hub_features,
            "{failure_categories}": failure_cats,
            **{f"{{{k}}}": str(v) for k, v in ctx.items()},
        }
        out = template
        for placeholder, value in replacements.items():
            out = out.replace(placeholder, value)
        return out

    def _initialise_defaults(self) -> None:
        """Pre-seed default pools for the generic archetype."""
        for role in ANALYTICAL_ROLES:
            self._get_or_create_pool(role, "generic")
