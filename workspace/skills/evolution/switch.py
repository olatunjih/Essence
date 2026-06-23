"""
SkillEvolutionSwitch — control panel for the skill evolution safety gate.

Manages three modes for handling proposed/patched skills:

  HITL  — queue every proposal to the DecisionQueue for human review.
           The skill sits in scratch/skills_pending/ until a human
           approves it via the web UI.  Zero auto-commits.

  MODEL — run the SkillVerifier (autonomous LLM review) and auto-commit
          only when the verifier returns passed=True && score >= threshold.
          Rejected proposals are written to scratch/skills_rejected/ with
          the verifier's verdict attached.

  BOTH  — run the model verifier first; only proposals that pass the
          model review are forwarded to the HITL queue.  This filters
          obviously bad proposals before they reach a human reviewer.

The switch also manages verifier model selection — the model used by the
autonomous SkillVerifier can be changed at runtime without restart.

Usage in a proactive session::

    switch = SkillEvolutionSwitch(
        mode           = EvolutionMode.BOTH,
        verifier_model = "gpt-4o-mini",
        verifier       = SkillVerifier(router=router, model="gpt-4o-mini"),
        decision_queue = decision_queue,
        workspace      = workspace,
    )
    result = switch.gate_proposal(skill_content, pattern_key, episode)
    if result.committed:
        log.info("skill_committed", extra={"path": result.path})
"""
from __future__ import annotations

import json
import logging
import shutil
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from .verify import SkillVerificationResult, SkillVerifier

log = logging.getLogger("essence.skills.evolution.switch")


class EvolutionMode(str, Enum):
    """Operating mode for the SkillEvolutionSwitch."""
    HITL  = "hitl"    # human-in-the-loop only
    MODEL = "model"   # autonomous model verifier only
    BOTH  = "both"    # model verifier → HITL (model filters first)


@dataclass
class GateResult:
    """
    Outcome of passing a proposed skill through the evolution gate.

    Attributes:
        committed:         True when the skill was written to the active store.
        pending_hitl:      True when the skill is in the HITL queue.
        rejected:          True when the skill was denied (model or static).
        path:              Path to the written file (proposed/active/rejected).
        decision_id:       DecisionQueue ID when pending HITL review.
        verification:      SkillVerificationResult from the model verifier
                           (None when model stage was skipped).
        mode:              EvolutionMode that produced this result.
        notes:             Human-readable explanation.
    """
    committed:    bool                              = False
    pending_hitl: bool                              = False
    rejected:     bool                              = False
    path:         str                               = ""
    decision_id:  str                               = ""
    verification: SkillVerificationResult | None    = None
    mode:         EvolutionMode                     = EvolutionMode.HITL
    notes:        str                               = ""

    def to_dict(self) -> dict:
        return {
            "committed":    self.committed,
            "pending_hitl": self.pending_hitl,
            "rejected":     self.rejected,
            "path":         self.path,
            "decision_id":  self.decision_id,
            "verification": self.verification.to_dict() if self.verification else None,
            "mode":         self.mode.value,
            "notes":        self.notes,
        }


class SkillEvolutionSwitch:
    """
    Central control for the skill evolution safety gate.

    Wire into ReflectionSkill at construction time so every proposed or
    patched skill passes through this gate before touching workspace/skills/.
    """

    # Minimum verifier score to auto-commit in MODEL/BOTH mode.
    DEFAULT_SCORE_THRESHOLD = 0.70

    def __init__(self,
                 mode:              EvolutionMode = EvolutionMode.HITL,
                 verifier:          SkillVerifier | None = None,
                 verifier_model:    str = "",
                 decision_queue:    Any = None,
                 workspace:         Path | None = None,
                 score_threshold:   float = DEFAULT_SCORE_THRESHOLD) -> None:
        """
        Args:
            mode:             Starting mode (HITL / MODEL / BOTH).
            verifier:         SkillVerifier instance.  Created lazily when
                              None and a router is provided later via
                              set_router().
            verifier_model:   Model tag for the verifier LLM.  Overrides
                              the model on the verifier instance.
            decision_queue:   DecisionQueue instance for HITL routing.
                              When None, HITL mode falls back to writing
                              to scratch/skills_pending/ only.
            workspace:        Path to the Essence workspace root.
            score_threshold:  Minimum score for MODEL-mode auto-commit.
        """
        self._mode            = EvolutionMode(mode)
        self._verifier        = verifier
        self._verifier_model  = verifier_model
        self._decision_queue  = decision_queue
        self._workspace       = workspace
        self._score_threshold = score_threshold

        if verifier_model and verifier:
            verifier.set_model(verifier_model)

        log.info("skill_evolution_switch_init",
                 extra={"mode": self._mode.value,
                        "model": verifier_model or "(default)"})

    # ── Runtime controls ──────────────────────────────────────────────────────

    def set_mode(self, mode: str | EvolutionMode) -> None:
        """Change mode at runtime (e.g. from the UI toggle)."""
        self._mode = EvolutionMode(mode)
        log.info("skill_evolution_mode_changed", extra={"mode": self._mode.value})

    def set_verifier_model(self, model: str) -> None:
        """Change the verifier model at runtime."""
        self._verifier_model = model
        if self._verifier:
            self._verifier.set_model(model)
        log.info("skill_verifier_model_changed", extra={"model": model})

    def set_router(self, router: Any) -> None:
        """Inject a router into the verifier (useful for late wiring)."""
        if self._verifier is None:
            self._verifier = SkillVerifier(
                router = router,
                model  = self._verifier_model,
            )
        else:
            self._verifier._router = router
            if self._verifier_model:
                self._verifier.set_model(self._verifier_model)

    @property
    def mode(self) -> EvolutionMode:
        return self._mode

    @property
    def verifier_model(self) -> str:
        return self._verifier_model

    # ── Gate entry points ─────────────────────────────────────────────────────

    def gate_proposal(self,
                      skill_content: str,
                      pattern_key:   str = "",
                      episode:       Any = None,
                      skill_name:    str = "") -> GateResult:
        """
        Gate a new skill proposal through the configured pipeline.

        Returns a GateResult describing what happened.  The caller
        (ReflectionSkill) should only call SkillProposer.commit() when
        result.committed is True.
        """
        return self._gate(
            content     = skill_content,
            item_type   = "proposal",
            pattern_key = pattern_key,
            episode     = episode,
            name        = skill_name,
        )

    def gate_patch(self,
                   patch_content: str,
                   episode:       Any = None,
                   skill_name:    str = "") -> GateResult:
        """
        Gate a skill patch through the configured pipeline.

        Patches go through the same mode logic as proposals — a malicious
        patch is just as dangerous as a malicious new skill.
        """
        return self._gate(
            content     = patch_content,
            item_type   = "patch",
            pattern_key = "",
            episode     = episode,
            name        = skill_name,
        )

    # ── Internal pipeline ─────────────────────────────────────────────────────

    def _gate(self,
              content:     str,
              item_type:   str,
              pattern_key: str,
              episode:     Any,
              name:        str) -> GateResult:
        """Unified gate logic shared by proposals and patches."""
        ws = self._workspace

        mode = self._mode
        verification: SkillVerificationResult | None = None

        # ── Step 1: Model verification (MODEL or BOTH) ────────────────────────
        if mode in (EvolutionMode.MODEL, EvolutionMode.BOTH):
            if self._verifier is not None:
                verification = self._verifier.verify_skill(
                    skill_content = content,
                    pattern_key   = pattern_key,
                    episode       = episode,
                )
                log.info("skill_gate_model_verdict",
                         extra={"type":  item_type,
                                "passed": verification.passed,
                                "score":  verification.score,
                                "model":  verification.model})
            else:
                # No verifier configured — treat as passed with low confidence
                log.warning("skill_gate_no_verifier",
                            extra={"type": item_type,
                                   "mode": mode.value})

        # MODEL mode: reject if verifier says no, or score below threshold
        if mode == EvolutionMode.MODEL:
            if verification is not None and (
                not verification.passed
                or verification.score < self._score_threshold
            ):
                path = self._write_rejected(content, item_type, name,
                                            verification)
                return GateResult(
                    rejected     = True,
                    path         = str(path),
                    verification = verification,
                    mode         = mode,
                    notes        = (f"Rejected by model verifier: score="
                                    f"{verification.score:.2f} "
                                    f"({verification.notes})"),
                )
            # Model approved — commit directly to active store
            if ws:
                path = self._write_pending(content, item_type, name)
                self._commit_from_pending(path, item_type, name)
                return GateResult(
                    committed    = True,
                    path         = str(path),
                    verification = verification,
                    mode         = mode,
                    notes        = (f"Auto-committed after model review: "
                                    f"score={getattr(verification, 'score', 'n/a')}"),
                )
            return GateResult(
                committed    = True,
                verification = verification,
                mode         = mode,
                notes        = "Approved by model verifier (no workspace to write).",
            )

        # BOTH mode: if model rejected, skip HITL entirely
        if mode == EvolutionMode.BOTH:
            if verification is not None and (
                not verification.passed
                or verification.score < self._score_threshold
            ):
                path = self._write_rejected(content, item_type, name,
                                            verification)
                return GateResult(
                    rejected     = True,
                    path         = str(path),
                    verification = verification,
                    mode         = mode,
                    notes        = (f"Rejected by model verifier before HITL: "
                                    f"score={verification.score:.2f}"),
                )
            # Model approved → forward to HITL queue

        # ── Step 2: HITL queue (HITL or BOTH post-model-approval) ────────────
        pending_path = self._write_pending(content, item_type, name) if ws else None
        decision_id  = ""

        if self._decision_queue is not None:
            try:
                decision = self._decision_queue.enqueue(
                    tool_name  = f"skill_{item_type}_commit",
                    args       = {
                        "type":        item_type,
                        "name":        name,
                        "pattern_key": pattern_key,
                        "path":        str(pending_path) if pending_path else "",
                        "score":       getattr(verification, "score", None),
                    },
                    reason = (
                        f"Skill {item_type} pending human review. "
                        f"Pattern: {pattern_key[:80] if pattern_key else 'N/A'}. "
                        + (f"Model score: {verification.score:.2f}."
                           if verification else "No model review.")
                    ),
                )
                decision_id = decision.decision_id
                log.info("skill_gate_hitl_queued",
                         extra={"type": item_type,
                                "decision_id": decision_id})
            except Exception as exc:
                log.warning("skill_gate_hitl_error",
                            extra={"error": str(exc)[:120]})

        return GateResult(
            pending_hitl = True,
            path         = str(pending_path) if pending_path else "",
            decision_id  = decision_id,
            verification = verification,
            mode         = mode,
            notes        = (
                f"Queued for human review (decision_id={decision_id!r}). "
                + (f"Model pre-approved: score={verification.score:.2f}."
                   if verification and verification.passed
                   else "Awaiting human decision.")
            ),
        )

    # ── Filesystem helpers ────────────────────────────────────────────────────

    def _pending_dir(self) -> Path | None:
        if self._workspace is None:
            return None
        d = self._workspace / "scratch" / "skills_pending"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _rejected_dir(self) -> Path | None:
        if self._workspace is None:
            return None
        d = self._workspace / "scratch" / "skills_rejected"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _write_pending(self, content: str,
                       item_type: str, name: str) -> Path:
        """Write skill content to scratch/skills_pending/."""
        d = self._pending_dir()
        if d is None:
            return Path(f"/dev/null/{item_type}_{int(time.time())}.md")
        ts   = int(time.time())
        safe = _safe_name(name or item_type)
        path = d / f"{safe}_{ts}.md"
        path.write_text(content, encoding="utf-8")
        return path

    def _write_rejected(self, content: str, item_type: str, name: str,
                        verification: SkillVerificationResult) -> Path:
        """Write rejected skill to scratch/skills_rejected/ with verdict."""
        d = self._rejected_dir()
        if d is None:
            return Path(f"/dev/null/{item_type}_{int(time.time())}.md")
        ts   = int(time.time())
        safe = _safe_name(name or item_type)
        path = d / f"{safe}_{ts}.md"
        rejection_block = (
            f"\n\n<!-- REJECTED by SkillVerifier -->\n"
            f"<!-- score={verification.score:.3f} "
            f"passed={verification.passed} "
            f"notes={json.dumps(verification.notes)} -->\n"
        )
        path.write_text(content + rejection_block, encoding="utf-8")
        log.info("skill_rejected_written",
                 extra={"path": str(path),
                        "score": verification.score,
                        "notes": verification.notes[:100]})
        return path

    def _commit_from_pending(self, pending_path: Path,
                             item_type: str, name: str) -> Path | None:
        """Move a pending skill to the active workspace/skills/ directory."""
        if self._workspace is None:
            return None
        safe = _safe_name(name or item_type)
        target_dir  = self._workspace / "skills" / safe
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / "SKILL.md"
        try:
            shutil.copy2(str(pending_path), str(target_path))
            pending_path.unlink(missing_ok=True)
            log.info("skill_committed_from_pending",
                     extra={"target": str(target_path)})
            return target_path
        except Exception as exc:
            log.warning("skill_commit_error",
                        extra={"error": str(exc)[:120]})
            return None

    def approve_pending(self, pending_path: str,
                        skill_name: str = "") -> GateResult:
        """
        Called by the HITL approval handler (DecisionQueue callback / web UI).
        Commits the pending skill at pending_path to the active skill store.
        """
        path_obj = Path(pending_path)
        if not path_obj.exists():
            return GateResult(
                rejected = True,
                notes    = f"Pending skill file not found: {pending_path}",
            )
        try:
            _content = path_obj.read_text(encoding="utf-8")
        except Exception as exc:
            return GateResult(rejected=True,
                              notes=f"Cannot read pending file: {exc}")

        committed_path = self._commit_from_pending(
            path_obj, "approval", skill_name)
        if committed_path:
            return GateResult(
                committed = True,
                path      = str(committed_path),
                mode      = self._mode,
                notes     = "Approved by human and committed.",
            )
        return GateResult(
            rejected = True,
            notes    = "Commit failed after human approval (see logs).",
        )


def _safe_name(name: str) -> str:
    """Sanitise a string into a valid directory/file name."""
    import re as _re
    n = _re.sub(r"[^a-z0-9]+", "-", name.lower()[:40]).strip("-")
    return n or "skill"
