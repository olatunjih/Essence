"""
SkillProposer — generates new composite SKILL.md files from execution patterns.

Generated skills are written to <workspace>/skills/proposed/ as drafts.
They require human approval before being moved to <workspace>/skills/.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("essence.skills.evolution.propose")


_SKILL_TEMPLATE = """\
---
name: {name}
version: "1.0.0"
category: composite
description: |
  {description}
skill_type: composite
input_schema:
  type: object
  properties: {{}}
output_schema:
  type: object
  properties:
    result:
      type: string
guardrails:
  max_execution_time_seconds: 120
observability:
  log_inputs: true
  log_outputs: true
a2a:
  expose: false
---

# {name}

> Auto-proposed composite skill from observed execution pattern.
> Pattern: `{pattern}`
> Proposed at: {proposed_at}
> **Review required before activating.**

## Description

{description}

## Workflow

{workflow_steps}

## Notes

This skill was automatically proposed by the ReflectionSkill after observing
this execution pattern {frequency} times. Review and edit before activating.
"""


class SkillProposer:
    """
    Generates new composite SKILL.md drafts from observed execution patterns.

    Content generation (generate_content) is now separated from disk I/O
    (propose_composite) so the SkillEvolutionSwitch can inspect the content
    before deciding where — or whether — to write it.
    """

    def __init__(self,
                 workspace: Path | None = None,
                 router: Any = None) -> None:
        self._workspace = workspace
        self._router    = router

    def generate_content(self, pattern_key: str,
                         example_episode: Any = None,
                         frequency: int = 5) -> str:
        """
        Generate the SKILL.md content for a pattern WITHOUT writing to disk.

        Used by ReflectionSkill + SkillEvolutionSwitch so the content can be
        reviewed (by model verifier or human) before being committed.

        Returns the raw Markdown string.
        """
        skill_name     = self._make_skill_name(pattern_key)
        description    = self._generate_description(pattern_key)
        workflow_steps = self._extract_workflow(example_episode)

        return _SKILL_TEMPLATE.format(
            name           = skill_name,
            description    = description,
            pattern        = pattern_key,
            proposed_at    = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
            frequency      = frequency,
            workflow_steps = workflow_steps,
        )

    def propose_composite(self, pattern_key: str,
                          example_episode: Any = None,
                          frequency: int = 5) -> Path | None:
        """
        Generate a composite skill draft and write it to skills/proposed/.

        This path is used when no SkillEvolutionSwitch is configured — the
        draft lands in skills/proposed/ (not the active skills/ directory)
        so it still requires manual promotion before activation.

        Returns the path to the generated SKILL.md file, or None on failure.
        """
        if self._workspace is None:
            log.warning("skill_proposer_no_workspace")
            return None

        proposed_dir = self._workspace / "skills" / "proposed"
        proposed_dir.mkdir(parents=True, exist_ok=True)

        skill_name = self._make_skill_name(pattern_key)
        skill_dir  = proposed_dir / skill_name
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_path = skill_dir / "SKILL.md"

        if skill_path.exists():
            log.debug("skill_already_proposed", extra={"name": skill_name})
            return skill_path

        content = self.generate_content(pattern_key, example_episode, frequency)
        skill_path.write_text(content, encoding="utf-8")
        log.info("skill_proposed_written",
                 extra={"path": str(skill_path), "pattern": pattern_key})
        return skill_path

    def _make_skill_name(self, pattern_key: str) -> str:
        """Convert a pattern key to a valid skill directory name."""
        import re
        name = pattern_key.split("::")[0][:40]
        name = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        return f"auto-{name}-{int(time.time()) % 10000}"

    def _generate_description(self, pattern_key: str) -> str:
        """Generate a human-readable description for the skill."""
        goal, _, tools = pattern_key.partition("::")
        tool_list = tools.split(",") if tools else []
        return (
            f"Composite skill for: {goal}. "
            f"Uses tools: {', '.join(tool_list) if tool_list else 'general purpose'}."
        )

    def _extract_workflow(self, episode: Any) -> str:
        """Extract workflow steps from an episode's tool calls."""
        if episode is None:
            return "- Step 1: process input\n- Step 2: generate output"
        result = getattr(episode, "result", None)
        tool_calls = getattr(result, "tool_calls", []) or []
        if not tool_calls:
            return "- Step 1: process input\n- Step 2: generate output"
        lines = []
        for i, tc in enumerate(tool_calls[:10], 1):
            name = tc.get("name", "unknown")
            lines.append(f"- Step {i}: {name}")
        return "\n".join(lines)
