"""
ReflectionSkill — post-execution outcome reflection.

For each completed episode:
 1. Retrieve from EpisodicStore.
 2. Classify success/failure from stored ExecResult.
 3. On success (frequency >= 5): pass proposed skill through SkillEvolutionSwitch,
    which gates it via model verifier and/or HITL queue before any file is written
    to the active skill store.  Direct auto-commit to workspace/skills/ is removed.
 4. On failure: classify root cause and dispatch to SkillPatcher via the same gate.
 5. Call memory.update_beliefs(outcome).
 6. Append a reflection summary to EpisodicStore.
"""
from __future__ import annotations

import logging
import time
from typing import Any

log = logging.getLogger("essence.skills.evolution.reflect")


class ReflectionSkill:
    """
    Reflects on completed episodes to drive skill evolution.

    Wire into HeartbeatScheduler as a periodic scheduled job.

    The skill_switch parameter is the SkillEvolutionSwitch that gates every
    proposal and patch before any file reaches workspace/skills/.  When None,
    proposals/patches are written to scratch/skills_pending/ only (safe
    default — nothing auto-commits to the active store).
    """

    SUCCESS_FREQUENCY_THRESHOLD = 5   # pattern must occur >=N times to propose

    def __init__(self,
                 episodic_store: Any = None,
                 memory:         Any = None,
                 skill_proposer: Any = None,
                 skill_patcher:  Any = None,
                 router:         Any = None,
                 skill_switch:   Any = None) -> None:
        self._episodes      = episodic_store
        self._memory        = memory
        self._proposer      = skill_proposer
        self._patcher       = skill_patcher
        self._router        = router
        self._switch        = skill_switch      # SkillEvolutionSwitch | None
        self._pattern_freq: dict[str, int] = {}

    def reflect_on_episode(self, episode_id: str) -> dict:
        """
        Reflect on a single episode.

        Returns a summary dict with keys: episode_id, outcome, root_cause,
        action_taken, gate_result, reflected_at.
        """
        if self._episodes is None:
            return {"error": "EpisodicStore not configured"}

        try:
            episode = self._episodes.get(episode_id)
        except Exception as exc:
            return {"error": f"Episode not found: {exc}"}

        result = getattr(episode, "result", None)
        if result is None:
            return {"error": "Episode has no result"}

        state      = str(getattr(result, "state", "unknown"))
        is_success = state in ("done", "done_insufficient")

        summary: dict = {
            "episode_id":   episode_id,
            "outcome":      "success" if is_success else "failure",
            "root_cause":   None,
            "action_taken": None,
            "gate_result":  None,
            "reflected_at": time.time(),
        }

        if is_success:
            pattern_key = self._extract_pattern(episode)
            self._pattern_freq[pattern_key] = (
                self._pattern_freq.get(pattern_key, 0) + 1
            )
            freq = self._pattern_freq[pattern_key]
            if freq >= self.SUCCESS_FREQUENCY_THRESHOLD and self._proposer:
                summary["action_taken"] = self._gate_proposal(
                    pattern_key, episode, freq, summary)
        else:
            root_cause = self._classify_root_cause(result)
            summary["root_cause"] = root_cause

            if root_cause == "skill_error" and self._patcher:
                summary["action_taken"] = self._gate_patch(episode, summary)
            elif root_cause == "knowledge_gap" and self._memory:
                try:
                    self._memory.update_beliefs({"episode": episode_id,
                                                 "outcome": "failure",
                                                 "cause": root_cause})
                    summary["action_taken"] = "semantic_memory_updated"
                except Exception:
                    pass
            elif root_cause == "model_drift":
                log.warning("model_drift_detected",
                            extra={"episode_id": episode_id})
                summary["action_taken"] = "model_health_check_triggered"

        # Update semantic memory with outcome
        if self._memory:
            try:
                self._memory.update_beliefs({
                    "episode_id": episode_id,
                    "outcome":    summary["outcome"],
                    "root_cause": summary.get("root_cause"),
                })
            except Exception:
                pass

        # Append reflection to episodic store
        if self._episodes:
            try:
                self._episodes.append_reflection(episode_id, summary)
            except Exception:
                pass

        return summary

    # ── Gate helpers ──────────────────────────────────────────────────────────

    def _gate_proposal(self, pattern_key: str, episode: Any,
                       freq: int, summary: dict) -> str:
        """
        Pass a new composite skill proposal through the SkillEvolutionSwitch.

        If no switch is configured, generate the skill draft but write it to
        scratch/skills_pending/ only — never auto-commit to workspace/skills/.
        """
        try:
            # Ask the proposer to generate the SKILL.md content
            skill_content = self._proposer.generate_content(pattern_key, episode,
                                                             frequency=freq)
        except Exception as exc:
            log.warning("skill_propose_generate_error",
                        extra={"error": str(exc)[:120]})
            return f"skill_propose_error:{exc!s:.60}"

        if self._switch is not None:
            try:
                gate = self._switch.gate_proposal(
                    skill_content = skill_content,
                    pattern_key   = pattern_key,
                    episode       = episode,
                    skill_name    = _pattern_to_name(pattern_key),
                )
                summary["gate_result"] = gate.to_dict()
                log.info("skill_proposal_gated",
                         extra={"pattern": pattern_key,
                                "committed":    gate.committed,
                                "pending_hitl": gate.pending_hitl,
                                "rejected":     gate.rejected,
                                "mode":         gate.mode.value})
                if gate.committed:
                    return f"skill_committed:{pattern_key}"
                if gate.pending_hitl:
                    return f"skill_pending_hitl:{gate.decision_id}"
                if gate.rejected:
                    return f"skill_rejected:{gate.notes[:80]}"
                return "skill_gate_unknown_disposition"
            except Exception as exc:
                log.warning("skill_gate_error",
                            extra={"error": str(exc)[:120]})
                return f"skill_gate_error:{exc!s:.60}"
        else:
            # No switch configured — fall back to safe pending write only.
            # The proposer's propose_composite() writes to skills/proposed/,
            # NOT the active skills/ directory, so nothing auto-activates.
            try:
                self._proposer.propose_composite(pattern_key, episode,
                                                 frequency=freq)
                log.info("skill_proposed_pending_only",
                         extra={"pattern": pattern_key, "freq": freq})
                return f"proposed_to_pending:{pattern_key}"
            except Exception as exc:
                log.warning("skill_propose_error",
                            extra={"error": str(exc)[:120]})
                return f"skill_propose_error:{exc!s:.60}"

    def _gate_patch(self, episode: Any, summary: dict) -> str:
        """
        Pass a skill patch through the SkillEvolutionSwitch.

        Same gate logic as proposals — malicious patches are equally dangerous.
        """
        try:
            patch_content = self._patcher.generate_patch_content(episode)
        except Exception:
            # Fallback: use the legacy patcher which writes to skills/patches/
            try:
                self._patcher.patch(episode)
                return "skill_patch_queued_legacy"
            except Exception as exc:
                return f"skill_patch_error:{exc!s:.60}"

        episode_id = str(getattr(episode, "id", "unknown"))

        if self._switch is not None:
            try:
                gate = self._switch.gate_patch(
                    patch_content = patch_content,
                    episode       = episode,
                    skill_name    = f"patch-{episode_id[:8]}",
                )
                summary["gate_result"] = gate.to_dict()
                log.info("skill_patch_gated",
                         extra={"episode": episode_id,
                                "committed":    gate.committed,
                                "pending_hitl": gate.pending_hitl,
                                "rejected":     gate.rejected})
                if gate.committed:
                    return "skill_patch_committed"
                if gate.pending_hitl:
                    return f"skill_patch_pending_hitl:{gate.decision_id}"
                if gate.rejected:
                    return f"skill_patch_rejected:{gate.notes[:80]}"
            except Exception as exc:
                log.warning("skill_patch_gate_error",
                            extra={"error": str(exc)[:120]})
        else:
            # No switch — use legacy patcher (writes to patches/, not active store)
            try:
                self._patcher.patch(episode)
                return "skill_patch_queued"
            except Exception as exc:
                return f"skill_patch_error:{exc!s:.60}"

        return "skill_patch_gate_unknown"

    # ── Pattern helpers ───────────────────────────────────────────────────────

    def _extract_pattern(self, episode: Any) -> str:
        """Extract a pattern key from a successful episode."""
        tools      = getattr(getattr(episode, "result", None), "tool_calls", []) or []
        tool_names = [t.get("name", "?") for t in tools[:5]]
        goal       = str(getattr(episode, "goal", ""))[:40]
        return f"{goal}::{','.join(tool_names)}"

    def _classify_root_cause(self, result: Any) -> str:
        """Classify failure root cause."""
        notes = str(getattr(result, "notes", "")).lower()
        if any(w in notes for w in ("permission", "tool", "exec", "runtime")):
            return "skill_error"
        if any(w in notes for w in ("not found", "unknown", "no data", "404")):
            return "knowledge_gap"
        if any(w in notes for w in ("drift", "distribution", "model")):
            return "model_drift"
        return "skill_error"

    def run_batch(self, episode_ids: list[str]) -> list[dict]:
        """Reflect on a batch of episodes."""
        return [self.reflect_on_episode(eid) for eid in episode_ids]


def _pattern_to_name(pattern_key: str) -> str:
    import re as _re
    name = pattern_key.split("::")[0][:40]
    return _re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "skill"
