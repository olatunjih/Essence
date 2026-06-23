"""SOPLoader: load Standard Operating Procedures into planner context."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403

# SOP LOADER
# ══════════════════════════════════════════════════════════════════════════════
# Reads Markdown files from a `procedures/` directory (configurable via
# Essence_SOP_DIR or AgentConfig.sop_dir) and injects the most relevant ones
# into the planner system prompt so the agent follows organisation-specific
# processes instead of inventing its own.
#
# File convention:
#   procedures/deploy.md      → triggered when task mentions "deploy"
#   procedures/code_review.md → triggered when task mentions "review"
#   procedures/incident.md    → triggered when task mentions "incident"
#
# Each file should start with a YAML frontmatter block:
#   ---
#   triggers: [deploy, release, rollout]
#   priority: high
#   ---
#   # Deploy procedure
#   1. Run tests ...

import fnmatch as _fnmatch

@_dc.dataclass
class SOPDoc:
    """A single Standard Operating Procedure document."""
    path:     Path
    name:     str
    triggers: list[str]    # keywords that activate this SOP
    priority: str          # high | medium | low
    content:  str

    def matches(self, task: str) -> bool:
        task_lower = task.lower()
        return any(t.lower() in task_lower for t in self.triggers) or                self.name.lower().replace("_", " ") in task_lower


class SOPLoader:
    """
    Loads and indexes SOP markdown files. Injected into the planner prompt
    when a task description matches any SOP's trigger keywords.

    Usage::
        loader = SOPLoader(workspace / "procedures")
        sop_context = loader.relevant(task_description, max_docs=2)
        # sop_context is a string to inject into the planner system prompt
    """

    def __init__(self, sop_dir: str | Path | None = None) -> None:
        dirs = []
        if sop_dir:
            dirs.append(Path(sop_dir))
        if _SOP_DIR:
            dirs.append(Path(_SOP_DIR))
        self._dirs  = dirs
        self._docs:  list[SOPDoc] = []
        self._loaded = False

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        for d in self._dirs:
            if not d.exists():
                continue
            for p in sorted(d.glob("*.md")):
                try:
                    self._docs.append(self._parse(p))
                except Exception as _e:
                    log.debug("sop_load_error",
                              extra={"path": str(p), "error": str(_e)[:80]})

    @staticmethod
    def _parse(path: Path) -> SOPDoc:
        text    = path.read_text(encoding="utf-8", errors="replace")
        triggers: list[str] = []
        priority = "medium"
        content  = text
        # Parse optional YAML frontmatter
        if text.startswith("---"):
            end = text.find("---", 3)
            if end > 0:
                fm_raw = text[3:end].strip()
                content = text[end + 3:].strip()
                for line in fm_raw.splitlines():
                    if line.startswith("triggers:"):
                        raw = line.split(":", 1)[1].strip()
                        raw = raw.strip("[]")
                        triggers = [t.strip().strip('"').strip("'")
                                    for t in raw.split(",") if t.strip()]
                    elif line.startswith("priority:"):
                        priority = line.split(":", 1)[1].strip()
        # Fallback: derive triggers from filename
        if not triggers:
            triggers = [path.stem.replace("_", " ").replace("-", " ")]
        return SOPDoc(
            path=path,
            name=path.stem,
            triggers=triggers,
            priority=priority,
            content=content,
        )

    def relevant(self, task: str, max_docs: int = 2) -> str:
        """
        Return a formatted string of SOP content relevant to the task.
        Returns empty string when no SOPs match or directory is empty.
        """
        self._load()
        if not self._docs:
            return ""
        # Priority order: high > medium > low; then by trigger match quality
        priority_order = {"high": 0, "medium": 1, "low": 2}
        matched = sorted(
            [d for d in self._docs if d.matches(task)],
            key=lambda d: priority_order.get(d.priority, 1)
        )[:max_docs]
        if not matched:
            return ""
        parts = ["\n# Relevant Standard Operating Procedures\n"]
        for doc in matched:
            parts.append(f"## {doc.name.replace('_', ' ').title()}\n")
            parts.append(doc.content[:1500])  # cap per-SOP context
            parts.append("\n")
        return "\n".join(parts)

    def list_all(self) -> list[dict]:
        """Return metadata for all loaded SOPs (for CLI listing)."""
        self._load()
        return [{"name": d.name, "triggers": d.triggers,
                 "priority": d.priority, "path": str(d.path)}
                for d in self._docs]


# Module-level singleton
_sop_loader: SOPLoader | None = None

def get_sop_loader(sop_dir: str | Path | None = None) -> SOPLoader:
    global _sop_loader
    if _sop_loader is None:
        _sop_loader = SOPLoader(sop_dir)
    return _sop_loader


