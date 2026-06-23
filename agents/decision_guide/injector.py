
"""Decision-Guide: guidance block injector — prepends rules to EXEC prompts."""
from __future__ import annotations
import json
from essence.apde_types import GuidanceBlock, Task


class RuleInjector:
    """
    Injects a GuidanceBlock into the system prompt for an EXEC call.
    Renders rules as a compact, numbered policy list.
    """

    def inject(self, system_prompt: str, guidance: GuidanceBlock,
               task: Task) -> str:
        """Return system_prompt prepended with active Decision-Guide rules."""
        if not guidance.rules:
            return system_prompt
        header = (
            f"[Decision-Guide: {len(guidance.rules)} active rules | "
            f"risk={guidance.risk.value} | "
            f"checkpoint_pct={guidance.checkpoint_every_pct:.0%}]\n"
        )
        lines = [header]
        for i, rule in enumerate(guidance.rules, 1):
            action = rule.get("action", "allow")
            gl     = rule.get("guardrail_link", "")
            gl_str = f" [requires {gl}]" if gl else ""
            lines.append(
                f"  {i}. [{rule['id']}] {rule['description']}"
                f" → {action}{gl_str}"
            )
        lines.append("")
        return "\n".join(lines) + system_prompt
