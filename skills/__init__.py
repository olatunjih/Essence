"""
essence.skills — Autonomous skill system for the Essence kernel.

Public API (import from here):

    from essence.skills import (
        SkillRepository,
        SkillDiscovery,
        SkillExecutor,
        SkillComposer,
        SkillAutonomousBuilder,
        # models
        SkillSpec, SkillResult, SkillType, SkillSource, SkillStatus,
        SkillInputSpec, SkillOutputSpec, SkillGuardrails,
        CapabilityGap,
        PipelineStep, ConditionalBranch,
        # helpers
        boot_skill_system,
    )

Quick-start:
    sys = boot_skill_system(workspace=Path("~/essence-ws"))
    results = sys.executor.execute("web_search", {"query": "Python 3.13 release"})
"""
# ruff: noqa
# fmt: off
from __future__ import annotations

from essence.skills.models import (
    SkillSpec,
    SkillResult,
    SkillType,
    SkillSource,
    SkillStatus,
    SkillInputSpec,
    SkillOutputSpec,
    SkillGuardrails,
    parse_skill_frontmatter,
    spec_from_skill_md,
)
from essence.skills.repository import SkillRepository
from essence.skills.discovery  import SkillDiscovery
from essence.skills.executor   import SkillExecutor
from essence.skills.composer   import SkillComposer, PipelineStep, ConditionalBranch
from essence.skills.autonomous_builder import SkillAutonomousBuilder, CapabilityGap

import dataclasses as _dc
import logging     as _logging
from pathlib import Path
from typing  import Any

log = _logging.getLogger("essence.skills")


# ══════════════════════════════════════════════════════════════════════════════
# Bundled subsystem
# ══════════════════════════════════════════════════════════════════════════════

@_dc.dataclass
class SkillSystem:
    """
    All skill-system components wired together.
    Returned by boot_skill_system().
    """
    repository:  SkillRepository
    discovery:   SkillDiscovery
    executor:    SkillExecutor
    composer:    SkillComposer
    builder:     SkillAutonomousBuilder

    def search(self, query: str, limit: int = 10) -> list[SkillSpec]:
        return self.repository.search(query, limit=limit)

    def execute(self, skill_name: str, input_data: dict | None = None) -> SkillResult:
        return self.executor.execute(skill_name, input_data)

    def summary(self) -> str:
        return self.repository.summary_index()

    def flush(self) -> int:
        return self.repository.flush()

    def scan(self) -> dict[str, int]:
        return self.discovery.scan_all()

    def drafts(self) -> list[SkillSpec]:
        return self.builder.list_drafts()

    def promote(self, name: str) -> tuple[bool, str]:
        return self.builder.promote(name)


# ══════════════════════════════════════════════════════════════════════════════
# Boot helper  (called from essence/boot.py)
# ══════════════════════════════════════════════════════════════════════════════

def boot_skill_system(
    workspace:     Path,
    router:        Any | None = None,
    tool_registry: Any | None = None,
    event_bus:     Any | None = None,
    mcp_clients:   list[Any] | None = None,
    extra_urls:    list[str] | None = None,
) -> SkillSystem:
    """
    Instantiate and wire all skill-system components, then run discovery.

    Parameters
    ----------
    workspace     : Essence workspace root (workspace/skills/ lives inside it).
    router        : LLM router for skill execution and autonomous synthesis.
    tool_registry : ToolRegistry for skills that invoke built-in tools.
    event_bus     : Optional pub/sub bus for gap-detected events.
    mcp_clients   : Optional list of MCP client objects to harvest tools from.
    extra_urls    : Optional extra HTTPS skill registry index URLs.

    Returns a fully initialised SkillSystem.
    """
    repo      = SkillRepository(workspace)
    disc      = SkillDiscovery(workspace, repo)
    executor  = SkillExecutor(repo, router=router, tool_registry=tool_registry)
    composer  = SkillComposer(executor, repo)
    builder   = SkillAutonomousBuilder(
        repository = repo,
        executor   = executor,
        router     = router,
        workspace  = workspace,
        event_bus  = event_bus,
    )

    # Load persisted skills first, then discover any new ones
    repo.load_from_disk()
    counts = disc.scan_all(mcp_clients=mcp_clients, extra_urls=extra_urls)

    log.info("skill_system_booted", extra={
        "total":      repo.count(),
        "local_new":  counts["local"],
        "mcp_new":    counts["mcp"],
        "remote_new": counts["remote"],
    })

    return SkillSystem(
        repository = repo,
        discovery  = disc,
        executor   = executor,
        composer   = composer,
        builder    = builder,
    )


__all__ = [
    "SkillSystem",
    "boot_skill_system",
    "SkillRepository",
    "SkillDiscovery",
    "SkillExecutor",
    "SkillComposer",
    "SkillAutonomousBuilder",
    "SkillSpec",
    "SkillResult",
    "SkillType",
    "SkillSource",
    "SkillStatus",
    "SkillInputSpec",
    "SkillOutputSpec",
    "SkillGuardrails",
    "CapabilityGap",
    "PipelineStep",
    "ConditionalBranch",
    "parse_skill_frontmatter",
    "spec_from_skill_md",
]
