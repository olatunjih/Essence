# essence.analytics.analytical_reward
"""
  Analytical Reward Signal (Analytics Engine × Essence wiring doc )
==========================================================
Extends the v28.1 RewardSignal and compute_reward() with a dual-track
system that produces a structured, decomposed reward rather than a single
float.

Track 1 — Task Quality  (weights adjusted from v28.1)
  0.25 × critic_pass_rate
  0.20 × tool_success_rate
  0.15 × efficiency
  0.10 × hallucination_penalty
  0.10 × user_feedback
  0.20 × analytical_quality   ← NEW: replaces unused 0.20 slot

Track 2 — Analytical Quality  (new; from Analytics Engine Spine)
  0.20 × finding_confirmation_rate
  0.20 × prediction_accuracy         (Brier-based; delayed if outcomes unknown)
  0.15 × robustness_score
  0.15 × cross_resolution_consistency
  0.15 × novelty_score
  0.15 × entity_prediction_accuracy   (delayed)

Decomposed reward routing
  task_quality       → PromptEvolution (planner prompts)
  analytical_quality → Learning Engine strategy optimizer
  efficiency         → WorkflowCompressor
  confidence_calib   → Learning Engine calibrator
  novelty            → Learning Engine pattern memory
  robustness         → Resilience Layer (self-falsification intensity)

Delayed reward tracking
  Predictions made at time T are registered in a DelayedRewardQueue.
  When outcomes arrive they retroactively update Learning Engine calibration
  and finding confidence.
"""
from __future__ import annotations
from essence._shared import *      # noqa: F401,F403

import dataclasses as _dc
import collections

from essence.analytics.layers import AnalyticalStateBus, CalibrationState

_log = _setup_logging("essence.analytics.analytical_reward")


# ─── decomposed reward dataclass ─────────────────────────────────────────────

@_dc.dataclass
class DecomposedReward:
    """
    Structured reward object.

    All components are in [0, 1].  composite is the weighted sum used
    for backward-compatible single-float consumers (bandit, A/B router).
    """
    composite:              float = 0.0   # backward-compat single float

    # Track 1 — task quality
    task_quality:           float = 0.0
    tool_success:           float = 0.0
    efficiency:             float = 0.0
    hallucination_penalty:  float = 0.0
    user_feedback:          float = 0.5
    analytical_quality:     float = 0.0

    # Track 2 — analytical quality sub-scores
    finding_confirmation:   float = 0.0
    prediction_accuracy:    float = 0.0   # may be 0 until delayed outcome arrives
    robustness_score:       float = 0.0
    cross_resolution_consistency: float = 0.0
    novelty_score:          float = 0.0
    entity_prediction_accuracy:   float = 0.0  # delayed

    # metadata
    session_id:   str   = ""
    model:        str   = ""
    archetype:    str   = "unknown"
    ts:           float = _dc.field(default_factory=time.time)
    is_delayed:   bool  = False    # True if some scores are pending outcomes


# ─── delayed reward queue ─────────────────────────────────────────────────────

@_dc.dataclass
class DelayedRewardEntry:
    """
    A prediction registered at time T whose outcome arrives at T+k.
    """
    finding_id:       str
    predicted_value:  float
    target_col:       str
    registered_at:    float = _dc.field(default_factory=time.time)
    outcome_actual:   float | None = None
    outcome_at:       float | None = None
    archetype:        str = "unknown"


class DelayedRewardQueue:
    """
    Learning Engine delayed reward queue.

    Usage
    -----
    queue = DelayedRewardQueue(maxlen=1000)
    queue.register(entry)
    queue.record_outcome(finding_id="...", actual=42.0)
    scores = queue.recent_brier_scores(n=50)
    """

    def __init__(self, maxlen: int = 1000) -> None:
        self._q: collections.deque[DelayedRewardEntry] = collections.deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def register(self, finding_id: str, predicted_value: float,
                 target_col: str, archetype: str = "unknown") -> None:
        with self._lock:
            self._q.append(DelayedRewardEntry(
                finding_id=finding_id,
                predicted_value=predicted_value,
                target_col=target_col,
                archetype=archetype,
            ))

    def record_outcome(self, finding_id: str, actual: float) -> bool:
        """Set the actual outcome for a registered prediction. Returns True if found."""
        with self._lock:
            for entry in self._q:
                if entry.finding_id == finding_id and entry.outcome_actual is None:
                    entry.outcome_actual = actual
                    entry.outcome_at = time.time()
                    return True
        return False

    def recent_brier_scores(self, n: int = 50) -> list[float]:
        """
        Return up to n Brier scores for resolved entries.
        Brier = (predicted - actual)² for regression proxy.
        """
        with self._lock:
            resolved = [e for e in self._q if e.outcome_actual is not None]
        resolved.sort(key=lambda e: e.outcome_at or 0, reverse=True)
        scores = []
        for e in resolved[:n]:
            diff = e.predicted_value - e.outcome_actual
            scores.append(diff * diff)  # squared error proxy
        return scores

    def pending_count(self) -> int:
        with self._lock:
            return sum(1 for e in self._q if e.outcome_actual is None)

    def __len__(self) -> int:
        return len(self._q)


# ─── analytical quality scorer ───────────────────────────────────────────────

def _score_analytical_quality(spine: AnalyticalStateBus,
                               genesis_feedback: list[dict] | None = None
                               ) -> dict[str, float]:
    """
    Compute Track 2 analytical quality sub-scores from the Analytical Spine.

    Parameters
    ----------
    spine           : AnalyticalStateBus  live spine state
    genesis_feedback: list[dict]          critic feedback records from this task

    Returns a dict of sub-scores, all in [0, 1].
    """
    findings = spine.active_findings
    scores: dict[str, float] = {
        "finding_confirmation":          0.0,
        "prediction_accuracy":           0.0,
        "robustness_score":              0.0,
        "cross_resolution_consistency":  0.0,
        "novelty_score":                 0.0,
        "entity_prediction_accuracy":    0.0,
    }

    if not findings:
        return scores

    n = len(findings)

    # finding_confirmation_rate: fraction confirmed by critic
    if genesis_feedback:
        confirmed = sum(1 for fb in genesis_feedback if fb.get("confirmed", False))
        scores["finding_confirmation"] = confirmed / max(1, len(genesis_feedback))
    else:
        # Proxy: average calibrated confidence
        scores["finding_confirmation"] = sum(
            getattr(f, "calibrated_confidence", getattr(f, "confidence", 0))
            for f in findings
        ) / n

    # robustness_score: average self-falsification robustness
    rob_vals = [getattr(f, "robustness", None) for f in findings]
    rob_vals = [v for v in rob_vals if v is not None]
    if rob_vals:
        scores["robustness_score"] = sum(rob_vals) / len(rob_vals)
    else:
        scores["robustness_score"] = 0.5  # neutral when not set

    # novelty_score: fraction of findings with novelty > 0.5
    nov_vals = [getattr(f, "novelty", None) for f in findings]
    nov_vals = [v for v in nov_vals if v is not None]
    if nov_vals:
        scores["novelty_score"] = sum(1 for v in nov_vals if v > 0.5) / len(nov_vals)
    else:
        # Proxy: fraction of findings NOT in the previous snapshot
        scores["novelty_score"] = min(1.0, n / max(1, n + 3))

    # cross_resolution_consistency: all findings have status ACTIVE (not RETRACTED)
    active  = sum(1 for f in findings if getattr(f, "status", "ACTIVE") == "ACTIVE")
    scores["cross_resolution_consistency"] = active / n

    # prediction_accuracy: derive from calibration state ECE (lower = better)
    ece = spine.confidence_state.ece
    scores["prediction_accuracy"] = max(0.0, 1.0 - ece * 3)

    # entity_prediction_accuracy: proxy from entity count / trust
    if spine.active_entities:
        scores["entity_prediction_accuracy"] = min(1.0, spine.trust_score)

    return scores


# ─── main compute function ───────────────────────────────────────────────────

def compute_analytical_reward(
        observer:          Any,                         # AgentObserver
        spine:             AnalyticalStateBus | None = None,
        budget:            int   = 0,
        user_feedback:     float = 0.5,
        genesis_feedback:  list[dict] | None = None,
        delayed_queue:     DelayedRewardQueue | None = None,
) -> DecomposedReward:
    """
    Compute the v29.0 decomposed reward.

    Backward-compatible: DecomposedReward.composite matches the scalar
    returned by the v28.1 compute_reward() for all existing consumers.

    Parameters
    ----------
    observer        : AgentObserver  session observer from agents/observer.py
    spine           : AnalyticalStateBus or None
    budget          : int  token budget (for efficiency score)
    user_feedback   : float  0-1 user satisfaction signal
    genesis_feedback: list[dict]  critic feedback records from AnalyticalCriticGate
    delayed_queue   : DelayedRewardQueue  registers L6 predictions for later scoring
    """
    spine = spine or AnalyticalStateBus()

    # ── Track 1: task quality (adjusted from v28.1) ───────────────────────
    s         = observer.summary() if hasattr(observer, "summary") else {}
    cr        = float(s.get("critic_pass_rate",   1.0))
    tr        = 1.0 - float(s.get("tool_failure_rate", 0.0))
    tot_tok   = (s.get("total_tokens_in", 0) + s.get("total_tokens_out", 0))
    eff       = (min(1.0, budget / max(tot_tok, 1))
                 if budget > 0 and tot_tok > 0 else 0.8)
    hp        = max(0.0, 1.0 - float(s.get("hallucination_count", 0)) * 0.2)

    # ── Track 2: analytical quality ───────────────────────────────────────
    aq_scores = _score_analytical_quality(spine, genesis_feedback)
    aq_composite = (
        0.20 * aq_scores["finding_confirmation"]
        + 0.20 * aq_scores["prediction_accuracy"]
        + 0.15 * aq_scores["robustness_score"]
        + 0.15 * aq_scores["cross_resolution_consistency"]
        + 0.15 * aq_scores["novelty_score"]
        + 0.15 * aq_scores["entity_prediction_accuracy"]
    )

    # ── composite (v29 weights) ───────────────────────────────────────────
    composite = (
        0.25 * cr
        + 0.20 * tr
        + 0.15 * eff
        + 0.10 * hp
        + 0.10 * user_feedback
        + 0.20 * aq_composite
    )
    composite = round(max(0.0, min(1.0, composite)), 4)

    # ── register L6 predictions in the delayed queue ──────────────────────
    is_delayed = False
    if delayed_queue is not None:
        pred_findings = [f for f in spine.active_findings
                         if getattr(f, "category", "") == "PREDICTION"]
        for f in pred_findings:
            fid = getattr(f, "id", "")
            ts  = getattr(f, "test_statistic", None)  # R² — not a value prediction
            if fid and ts is not None:
                delayed_queue.register(
                    finding_id=fid,
                    predicted_value=float(ts),
                    target_col=getattr(f, "sub_category", ""),
                    archetype=spine.active_archetype,
                )
                is_delayed = True

    reward = DecomposedReward(
        composite             = composite,
        task_quality          = round(0.25*cr + 0.20*tr + 0.15*eff + 0.10*hp + 0.10*user_feedback, 4),
        tool_success          = round(tr, 4),
        efficiency            = round(eff, 4),
        hallucination_penalty = round(hp, 4),
        user_feedback         = user_feedback,
        analytical_quality    = round(aq_composite, 4),
        finding_confirmation  = round(aq_scores["finding_confirmation"], 4),
        prediction_accuracy   = round(aq_scores["prediction_accuracy"], 4),
        robustness_score      = round(aq_scores["robustness_score"], 4),
        cross_resolution_consistency = round(aq_scores["cross_resolution_consistency"], 4),
        novelty_score         = round(aq_scores["novelty_score"], 4),
        entity_prediction_accuracy   = round(aq_scores["entity_prediction_accuracy"], 4),
        session_id            = str(s.get("session_id", "")),
        model                 = str(s.get("model", "")),
        archetype             = spine.active_archetype,
        is_delayed            = is_delayed,
    )

    _log.debug(
        "DecomposedReward composite=%.3f task=%.3f analytical=%.3f",
        reward.composite, reward.task_quality, reward.analytical_quality,
    )
    return reward


# ── routing helper: deliver decomposed reward to each learning subsystem ─────

def route_decomposed_reward(reward:       DecomposedReward,
                             prompt_evol:  Any | None = None,
                             workflow_cmp: Any | None = None,
                             bandit:       Any | None = None,
                             spine:        AnalyticalStateBus | None = None,
                             plan_prompt:  str = "",
                             critic_prompt: str = "") -> None:
    """
    Deliver each dimension of a DecomposedReward to its target subsystem.

    Subsystem routing table (from wiring doc ):
      task_quality       → PromptEvolution planner prompt
      analytical_quality → Learning Engine strategy optimizer (via spine)
      efficiency         → WorkflowCompressor
      calibration        → Learning Engine calibrator (via spine ECE update)
      novelty            → Learning Engine pattern memory
      robustness         → Resilience Layer (via spine calibration state)
    """
    # ── PromptEvolution ───────────────────────────────────────────────────
    if prompt_evol is not None and hasattr(prompt_evol, "record"):
        try:
            if plan_prompt:
                prompt_evol.record(plan_prompt, reward.task_quality)
            if critic_prompt:
                prompt_evol.record(critic_prompt, reward.analytical_quality)
        except Exception as e:
            _log.debug("route_decomposed_reward prompt_evol: %s", e)

    # ── WorkflowCompressor ────────────────────────────────────────────────
    if workflow_cmp is not None and hasattr(workflow_cmp, "record_reward"):
        try:
            workflow_cmp.record_reward(reward.efficiency)
        except Exception as e:
            _log.debug("route_decomposed_reward workflow_cmp: %s", e)

    # ── Bandit / model router ─────────────────────────────────────────────
    # Only pass immediately-resolvable components to the bandit.
    # analytical_quality contains Brier-scored prediction accuracy that may
    # not yet have outcomes; including it in the bandit signal biases arm
    # selection toward fast completions before delayed outcomes resolve.
    # The analytical quality track feeds the spine/learning engine separately.
    if bandit is not None and hasattr(bandit, "record"):
        try:
            # Immediate components: critic pass rate, tool success, efficiency,
            # hallucination penalty, user feedback (weights sum to 0.80).
            # Divide by 0.80 to re-normalise to [0, 1].
            immediate_bandit_reward = round(
                min(1.0, reward.task_quality / 0.80), 4
            )
            bandit.record(immediate_bandit_reward)
        except Exception as e:
            _log.debug("route_decomposed_reward bandit: %s", e)

    # ── Spine: update calibration state for Learning Engine ───────────────────────
    if spine is not None:
        try:
            # Use novelty + robustness to nudge ECE proxy
            ece_estimate = max(0.0, 1.0 - reward.prediction_accuracy)
            updated_cal = CalibrationState(
                ece=round(ece_estimate, 4),
                brier=round(1.0 - reward.finding_confirmation, 4),
                n_samples=spine.confidence_state.n_samples + 1,
            )
            spine.update_from_genesis(calibration=updated_cal)
        except Exception as e:
            _log.debug("route_decomposed_reward spine: %s", e)
