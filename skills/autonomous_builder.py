"""
SkillAutonomousBuilder — detects gaps in the skill set and synthesises new
skills from first principles, observed patterns, or LLM-guided generation.

Gap detection:
  • Analyse recent failed tool calls → find missing capabilities.
  • Compare current skill index against a target capability matrix.
  • Surface the top-N gaps ordered by impact score.

Skill synthesis:
  • Use the LLM router to generate a full SKILL.md (frontmatter + body).
  • Parse, validate, and register the generated spec.
  • Optionally request user confirmation before promotion from DRAFT → ACTIVE.

All synthesised skills start as status=DRAFT so they are visible but not
injected into live tool-call payloads until promoted.
"""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403
from essence.skills.models import (
    SkillSpec, SkillSource, SkillStatus, SkillType,
    SkillGuardrails, spec_from_skill_md,
)
import dataclasses as _dc
import json        as _json
import time        as _time
from typing import Any

log = logging.getLogger("essence.skills.autonomous_builder")

# ══════════════════════════════════════════════════════════════════════════════
# Gap descriptor
# ══════════════════════════════════════════════════════════════════════════════

@_dc.dataclass
class CapabilityGap:
    """A missing skill identified by gap detection."""
    capability:  str   # short capability description
    impact:      float # 0.0–1.0 estimated value of having this skill
    evidence:    str   # why we think this gap exists
    suggested_name: str = ""

    def __post_init__(self) -> None:
        if not self.suggested_name:
            self.suggested_name = (
                self.capability.lower()
                .replace(" ", "_")
                .replace("-", "_")
                [:48]
            )


# ══════════════════════════════════════════════════════════════════════════════
# Autonomous Builder
# ══════════════════════════════════════════════════════════════════════════════

class SkillAutonomousBuilder:
    """
    Detects gaps in the Essence skill set and autonomously synthesises new
    skills, which are persisted as DRAFT SKILL.md files awaiting promotion.

    Parameters
    ----------
    repository : SkillRepository
    executor   : SkillExecutor  — used to validate synthesised skills.
    router     : LLM router     — used to generate SKILL.md content.
    workspace  : Path
    event_bus  : optional event bus for capability_gap_detected events.
    """

    # Minimum number of observed failures before we declare a gap.
    _FAILURE_THRESHOLD = 3
    # How many gaps to synthesise per run (avoids token budget overrun).
    _MAX_SYNTH_PER_RUN = 5

    def __init__(self,
                 repository: Any,
                 executor:   Any,
                 router:     Any | None,
                 workspace:  Path,
                 event_bus:  Any | None = None) -> None:
        self._repo      = repository
        self._executor  = executor
        self._router    = router
        self._workspace = workspace
        self._bus       = event_bus

        # Rolling logs: filled by callers via record_failure()
        self._failure_log: list[dict] = []   # {tool, reason, ts}
        self._gap_history: list[CapabilityGap] = []

    # ── Recording failures ────────────────────────────────────────────────────

    def record_failure(self, tool_name: str, reason: str) -> None:
        """Log a tool-call failure for gap analysis."""
        self._failure_log.append({
            "tool":   tool_name,
            "reason": reason[:200],
            "ts":     _time.time(),
        })
        # Keep last 500 entries
        if len(self._failure_log) > 500:
            self._failure_log = self._failure_log[-500:]

    # ── Gap detection ─────────────────────────────────────────────────────────

    def detect_gaps(self,
                    capability_matrix: list[str] | None = None) -> list[CapabilityGap]:
        """
        Return a ranked list of capability gaps.

        Sources:
          1. Failure frequency analysis (tools called but failing repeatedly).
          2. capability_matrix: list of desired capabilities not covered by any
             current skill (useful for product-defined skill requirements).
        """
        gaps: list[CapabilityGap] = []

        # ── Source 1: failure frequency ────────────────────────────────────
        tool_failures: dict[str, list[str]] = {}
        for entry in self._failure_log:
            tool = entry["tool"]
            tool_failures.setdefault(tool, []).append(entry["reason"])

        for tool, reasons in tool_failures.items():
            if len(reasons) < self._FAILURE_THRESHOLD:
                continue
            # Skip if we already have an active skill covering this tool
            existing = self._repo.search(tool, limit=1)
            if existing:
                continue
            impact = min(1.0, len(reasons) / 20)
            gaps.append(CapabilityGap(
                capability     = f"Handle {tool} reliably",
                impact         = impact,
                evidence       = (
                    f"{len(reasons)} failures. Latest: {reasons[-1][:80]}"
                ),
                suggested_name = f"skill_{tool.replace('-','_').lower()}",
            ))

        # ── Source 2: capability matrix ────────────────────────────────────
        for cap in (capability_matrix or []):
            existing = self._repo.search(cap, limit=1, min_score=0.3)
            if existing:
                continue
            gaps.append(CapabilityGap(
                capability = cap,
                impact     = 0.5,
                evidence   = "Required by capability matrix but not covered.",
            ))

        # Deduplicate by suggested_name, highest impact wins
        seen: dict[str, CapabilityGap] = {}
        for g in gaps:
            if g.suggested_name not in seen or g.impact > seen[g.suggested_name].impact:
                seen[g.suggested_name] = g
        gaps = sorted(seen.values(), key=lambda g: g.impact, reverse=True)

        self._gap_history = gaps

        # Emit event
        if self._bus is not None and gaps:
            try:
                self._bus.publish("capability_gap_detected", {
                    "count": len(gaps),
                    "top":   gaps[0].capability if gaps else "",
                })
            except Exception:
                pass

        log.info("skill_gaps_detected", extra={"count": len(gaps)})
        return gaps

    # ── Skill synthesis ────────────────────────────────────────────────────────

    def synthesise_skill(self, gap: CapabilityGap) -> SkillSpec | None:
        """
        Generate a SKILL.md for the given gap using the LLM router.
        Registers the result as DRAFT.  Returns None on failure.
        """
        if self._router is None:
            return self._synthesise_template(gap)

        prompt = self._build_synthesis_prompt(gap)
        try:
            raw_md = self._router.complete(
                prompt     = prompt,
                call_class = "SKILL_SYNTH",
                max_tokens = 1024,
            )
        except Exception as exc:
            log.warning("skill_synthesis_router_error",
                        extra={"gap": gap.capability, "error": str(exc)[:80]})
            return self._synthesise_template(gap)

        # Validate the generated SKILL.md is parseable
        try:
            spec = spec_from_skill_md(
                md_content = raw_md,
                skill_name = gap.suggested_name,
                skill_path = "",
                source     = SkillSource.LEARNED,
            )
        except Exception as exc:
            log.warning("skill_synthesis_parse_error",
                        extra={"error": str(exc)[:80]})
            return None

        # Force DRAFT status — requires explicit promotion before going live
        spec.status = SkillStatus.DRAFT

        ok, msg = self._repo.register(spec, force=True)
        if ok:
            self._repo.save_skill(spec)
            log.info("skill_synthesised",
                     extra={"name": spec.name, "gap": gap.capability})
        return spec if ok else None

    def synthesise_all_gaps(self,
                            capability_matrix: list[str] | None = None
                            ) -> list[SkillSpec]:
        """Detect gaps and synthesise up to _MAX_SYNTH_PER_RUN skills."""
        gaps    = self.detect_gaps(capability_matrix)[:self._MAX_SYNTH_PER_RUN]
        results = []
        for gap in gaps:
            spec = self.synthesise_skill(gap)
            if spec is not None:
                results.append(spec)
        return results

    # ── Promotion ─────────────────────────────────────────────────────────────

    def promote(self, skill_name: str) -> tuple[bool, str]:
        """Promote a DRAFT skill to ACTIVE status."""
        spec = self._repo.get(skill_name)
        if spec is None:
            return False, f"Skill '{skill_name}' not found."
        if spec.status == SkillStatus.ACTIVE:
            return True, f"Skill '{skill_name}' is already ACTIVE."
        spec.status = SkillStatus.ACTIVE
        ok, _ = self._repo.register(spec, force=True)
        if ok:
            self._repo.save_skill(spec)
        return ok, f"Skill '{skill_name}' promoted to ACTIVE."

    def demote(self, skill_name: str) -> tuple[bool, str]:
        """Demote an ACTIVE skill back to DRAFT for review."""
        spec = self._repo.get(skill_name)
        if spec is None:
            return False, f"Skill '{skill_name}' not found."
        spec.status = SkillStatus.DRAFT
        ok, _ = self._repo.register(spec, force=True)
        if ok:
            self._repo.save_skill(spec)
        return ok, f"Skill '{skill_name}' demoted to DRAFT."

    def list_drafts(self) -> list[SkillSpec]:
        """Return all skills in DRAFT status."""
        return [s for s in self._repo.all()
                if s.status == SkillStatus.DRAFT]

    # ── Private helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _build_synthesis_prompt(gap: CapabilityGap) -> str:
        return textwrap.dedent(f"""
            You are an expert AI engineer.  Generate a complete SKILL.md file
            for the following capability gap.

            Capability needed : {gap.capability}
            Impact score      : {gap.impact:.2f}
            Evidence          : {gap.evidence}
            Suggested name    : {gap.suggested_name}

            The SKILL.md must have:
            1. A YAML frontmatter block (between --- delimiters) containing:
               name, version, description, skill_type, category, tags,
               input_schema (JSON Schema), output_schema (JSON Schema),
               guardrails (max_execution_time_seconds, max_tokens).
            2. A markdown body with: Purpose, Instructions, Examples.

            Output ONLY the raw SKILL.md content — no commentary, no code fences.
        """).strip()

    @staticmethod
    def _synthesise_template(gap: CapabilityGap) -> SkillSpec:
        """
        Fallback synthesis used when no router is available.
        Produces a minimal but structurally valid SkillSpec.
        """
        body = textwrap.dedent(f"""
            # {gap.suggested_name}

            ## Purpose
            {gap.capability}

            ## Evidence of Need
            {gap.evidence}

            ## Instructions
            Implement the capability described above step-by-step.
            Verify the output meets the expected output schema before returning.

            ## Notes
            This skill was auto-generated as a template.  Edit the body and
            promote it from DRAFT → ACTIVE when ready.
        """).strip()

        return SkillSpec(
            name        = gap.suggested_name,
            description = gap.capability[:200],
            version     = "1.0.0",
            skill_type  = SkillType.GENERAL,
            source      = SkillSource.LEARNED,
            status      = SkillStatus.DRAFT,
            category    = "learned",
            tags        = ["draft", "auto-generated"],
            body        = body,
            guardrails  = SkillGuardrails(
                max_execution_time_seconds = 60,
                max_tokens                 = 1024,
                max_retries                = 1,
            ),
        )
