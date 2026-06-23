"""Skill system (SOP-compatible skill format)."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403

# SKILL SYSTEM  (skill-compatible)
# ══════════════════════════════════════════════════════════════════════════════
# Essence skills: directories containing SKILL.md under workspace/skills/.
# Compatible with skill hub's 700+ community skills.
# Each SKILL.md is injected as additional system context when relevant.

def load_skills(workspace: Path) -> dict[str, str]:
    """Return {name: skill_instructions} — full content, used for eager injection.
    Prefer skills_summary() + read_skill_content() for lazy loading in production
    (saves context tokens on T0/T1 where context windows are tight)."""
    skills: dict[str, str] = {}
    skills_dir = workspace / "skills"
    if not skills_dir.exists(): return skills
    for skill_path in skills_dir.glob("*/SKILL.md"):
        name = skill_path.parent.name
        try: skills[name] = skill_path.read_text(encoding="utf-8")
        except Exception: pass
    return skills


def load_skills_index(workspace: Path) -> dict[str, str]:
    """Return {name: description} — one-line index only, for lazy skill loading.
    Inject this into the system prompt via skills_summary().  The model calls
    the read_skill built-in tool when it needs the full SKILL.md content."""
    index: dict[str, str] = {}
    skills_dir = workspace / "skills"
    if not skills_dir.exists(): return index
    for skill_path in skills_dir.glob("*/SKILL.md"):
        name = skill_path.parent.name
        try:
            md = skill_path.read_text(encoding="utf-8")
            desc = next(
                (l.strip() for l in md.splitlines()
                 if l.strip() and not l.startswith(("#", "---"))),
                name)
            index[name] = desc[:120]
        except Exception:
            index[name] = name
    return index


def read_skill_content(workspace: Path, skill_name: str) -> str:
    """Return full SKILL.md content for one skill by name.
    Called by the read_skill tool when the agent decides a skill is relevant."""
    skill_path = workspace / "skills" / skill_name / "SKILL.md"
    if not skill_path.exists():
        return f"[skill '{skill_name}' not found in workspace/skills/]"
    try:
        return skill_path.read_text(encoding="utf-8")
    except Exception as e:
        return f"[error reading skill '{skill_name}': {e}]"


def skills_summary(skills: dict[str, str]) -> str:
    """Compact one-line-per-skill index for system prompt injection.
    Accepts both {name: full_md} and {name: description} dicts."""
    if not skills: return ""
    lines = ["[Available Skills — use read_skill tool to load full instructions]"]
    for name, content in skills.items():
        # If full SKILL.md content was passed, extract first non-header line
        if "\n" in content:
            desc = next((l.strip() for l in content.splitlines()
                         if l.strip() and not l.startswith(("#", "---"))), name)
        else:
            desc = content
        lines.append(f"  • {name}: {desc[:100]}")
    return "\n".join(lines)


# ── Skill CRUD ────────────────────────────────────────────────────────────────

def create_skill(workspace: Path, name: str, content: str) -> tuple[bool, str]:
    """Create a new skill directory with a SKILL.md file.

    Args:
        workspace: Path to the Essence workspace root.
        name:      Skill name (must be a valid directory name, alphanumeric + hyphens).
        content:   Markdown content for the SKILL.md file.

    Returns:
        (True, skill_path) on success, (False, error_message) on failure.
    """
    # Sanitise name: allow letters, digits, hyphens, underscores only
    import re as _re
    safe_name = _re.sub(r"[^a-zA-Z0-9_\-]", "_", name.strip()).strip("_")
    if not safe_name:
        return False, "Skill name is empty after sanitisation."

    skill_dir = workspace / "skills" / safe_name
    if skill_dir.exists():
        return False, f"Skill '{safe_name}' already exists."

    try:
        skill_dir.mkdir(parents=True, exist_ok=False)
        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text(content, encoding="utf-8")
        return True, str(skill_file)
    except Exception as exc:
        return False, str(exc)


def update_skill(workspace: Path, name: str, content: str) -> tuple[bool, str]:
    """Overwrite the SKILL.md of an existing skill.

    Returns (True, path) on success, (False, error_message) on failure.
    """
    import re as _re
    safe_name = _re.sub(r"[^a-zA-Z0-9_\-]", "_", name.strip()).strip("_")
    if not safe_name:
        return False, "Skill name is empty after sanitisation."
    skill_file = workspace / "skills" / safe_name / "SKILL.md"
    if not skill_file.exists():
        return False, f"Skill '{safe_name}' not found."
    try:
        skill_file.write_text(content, encoding="utf-8")
        return True, str(skill_file)
    except Exception as exc:
        return False, str(exc)


def delete_skill(workspace: Path, name: str) -> tuple[bool, str]:
    """Remove a skill directory and all its files.

    Returns (True, name) on success, (False, error_message) on failure.
    """
    import shutil as _shutil
    import re as _re
    # Extra safety: never traverse out of the skills directory
    safe_name = _re.sub(r"[^a-zA-Z0-9_\-]", "_", name.strip())
    skill_dir = workspace / "skills" / safe_name
    if not skill_dir.exists():
        return False, f"Skill '{safe_name}' not found."
    try:
        _shutil.rmtree(skill_dir)
        return True, safe_name
    except Exception as exc:
        return False, str(exc)


def import_skill_from_url(workspace: Path, url: str,
                          name: str | None = None) -> tuple[bool, str]:
    """Fetch a raw SKILL.md from a URL and install it as a local skill.

    Security:
    - Only HTTPS URLs are accepted (blocks http://, file://, ftp://, etc.).
    - RFC-1918 / loopback / link-local destinations are blocked (SSRF guard).
    - The skill name is sanitised identically to create_skill (path traversal
      prevention).

    Returns (True, name) on success, (False, error) on failure.
    """
    import urllib.request as _req
    import urllib.parse as _urlparse
    import socket as _socket
    import ipaddress as _ipaddress
    import re as _re

    # ── URL scheme validation (https only) ───────────────────────────────────
    parsed = _urlparse.urlparse(url)
    if parsed.scheme.lower() != "https":
        return False, "Only HTTPS URLs are accepted for skill import."

    # ── SSRF guard: resolve hostname and reject private/reserved ranges ───────
    hostname = parsed.hostname or ""
    if not hostname:
        return False, "URL has no resolvable hostname."
    try:
        addr_infos = _socket.getaddrinfo(hostname, None)
        for _, _, _, _, sockaddr in addr_infos:
            ip = _ipaddress.ip_address(sockaddr[0])
            if (ip.is_private or ip.is_loopback
                    or ip.is_link_local or ip.is_reserved):
                return False, (
                    f"Blocked: '{hostname}' resolves to a private or "
                    f"reserved address ({ip})."
                )
    except Exception as exc:
        return False, f"Hostname resolution failed: {exc}"

    # ── Name sanitisation (mirror create_skill) ───────────────────────────────
    if name:
        name = _re.sub(r"[^a-zA-Z0-9_\-]", "_", name.strip()).strip("_")
        if not name:
            return False, "Provided skill name is empty after sanitisation."
    else:
        url_path = url.rstrip("/").split("/")[-1]
        name = _re.sub(r"\.[a-zA-Z]+$", "", url_path)
        name = _re.sub(r"[^a-zA-Z0-9_\-]", "_", name).strip("_") or "imported_skill"

    # ── Fetch ─────────────────────────────────────────────────────────────────
    try:
        with _req.urlopen(url, timeout=15) as resp:  # noqa: S310
            raw = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        return False, f"HTTP fetch failed: {exc}"

    # If skill already exists, overwrite its content
    skill_dir = workspace / "skills" / name
    existed = skill_dir.exists()
    skill_dir.mkdir(parents=True, exist_ok=True)
    try:
        (skill_dir / "SKILL.md").write_text(raw, encoding="utf-8")
        action = "updated" if existed else "created"
        return True, f"Skill '{name}' {action} from URL."
    except Exception as exc:
        return False, str(exc)


def list_skills_meta(workspace: Path) -> list[dict]:
    """Return a list of dicts with metadata for every installed skill.

    Each dict contains: name, description (first non-header line), size_bytes,
    path (relative to workspace).
    """
    skills_dir = workspace / "skills"
    result: list[dict] = []
    if not skills_dir.exists():
        return result
    for skill_path in sorted(skills_dir.glob("*/SKILL.md")):
        name = skill_path.parent.name
        try:
            md = skill_path.read_text(encoding="utf-8")
            desc = next(
                (ln.strip() for ln in md.splitlines()
                 if ln.strip() and not ln.startswith(("#", "---"))),
                name,
            )
            result.append({
                "name": name,
                "description": desc[:120],
                "size_bytes": skill_path.stat().st_size,
                "path": str(skill_path.relative_to(workspace)),
            })
        except Exception:
            result.append({"name": name, "description": "", "size_bytes": 0,
                           "path": str(skill_path.relative_to(workspace))})
    return result


# ══════════════════════════════════════════════════════════════════════════════
# SKILL RECORD
# ══════════════════════════════════════════════════════════════════════════════
# Each SKILL.md may contain a YAML frontmatter block delimited by --- lines.
# SkillRecord parses and validates this block, exposing typed fields for use
# by the skill executor and observability layer.
#
# Example frontmatter:
#   ---
#   name: my_skill
#   version: "1.2.0"
#   skill_type: analysis
#   input_schema:
#     type: object
#     properties:
#       query: {type: string}
#   output_schema:
#     type: object
#     properties:
#       result: {type: string}
#   guardrails:
#     max_execution_time_seconds: 60
#   a2a:
#     expose: true
#   ---

import dataclasses as _skill_dc
import json as _skill_json


@_skill_dc.dataclass
class SkillRecord:
    """Typed representation of a skill's YAML frontmatter."""
    name:           str
    version:        str   = "1.0.0"
    category:       str   = "general"
    description:    str   = ""
    skill_type:     str   = "general"
    input_schema:   dict  = _skill_dc.field(default_factory=dict)
    output_schema:  dict  = _skill_dc.field(default_factory=dict)
    guardrails:     dict  = _skill_dc.field(default_factory=dict)
    observability:  dict  = _skill_dc.field(default_factory=dict)
    a2a:            dict  = _skill_dc.field(default_factory=dict)
    raw_md:         str   = ""
    skill_path:     str   = ""

    def to_dict(self) -> dict:
        return _skill_dc.asdict(self)

    @property
    def expose_via_a2a(self) -> bool:
        return bool(self.a2a.get("expose", False))

    @property
    def max_execution_time_s(self) -> float:
        return float(self.guardrails.get("max_execution_time_seconds", 120))

    @property
    def log_inputs(self) -> bool:
        return bool(self.observability.get("log_inputs", True))

    @property
    def log_outputs(self) -> bool:
        return bool(self.observability.get("log_outputs", True))


def _parse_frontmatter(md_content: str) -> tuple[dict, str]:
    """
    Parse YAML frontmatter from a Markdown string.

    Returns (frontmatter_dict, body_without_frontmatter).
    Returns ({}, original_content) when no valid frontmatter is found.
    """
    stripped = md_content.lstrip()
    if not stripped.startswith("---"):
        return {}, md_content

    lines = stripped.splitlines()
    end_idx = None
    for i, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            end_idx = i
            break

    if end_idx is None:
        return {}, md_content

    yaml_block = "\n".join(lines[1:end_idx])
    body       = "\n".join(lines[end_idx + 1:])

    try:
        import yaml  # type: ignore
        data = yaml.safe_load(yaml_block) or {}
        return data, body
    except Exception:
        data: dict = {}
        for ln in yaml_block.splitlines():
            if ":" in ln:
                k, _, v = ln.partition(":")
                data[k.strip()] = v.strip()
        return data, body


def load_skill_record(workspace: Path, skill_name: str) -> "SkillRecord | None":
    """
    Load and parse the YAML frontmatter of a skill's SKILL.md.

    Returns None when the skill directory or SKILL.md does not exist.
    """
    skill_path = workspace / "skills" / skill_name / "SKILL.md"
    if not skill_path.exists():
        return None
    try:
        md = skill_path.read_text(encoding="utf-8")
    except Exception:
        return None

    front, body = _parse_frontmatter(md)

    return SkillRecord(
        name=str(front.get("name", skill_name)),
        version=str(front.get("version", "1.0.0")),
        category=str(front.get("category", "general")),
        description=str(front.get("description", "")).strip(),
        skill_type=str(front.get("skill_type", "general")),
        input_schema=front.get("input_schema", {}) or {},
        output_schema=front.get("output_schema", {}) or {},
        guardrails=front.get("guardrails", {}) or {},
        observability=front.get("observability", {}) or {},
        a2a=front.get("a2a", {}) or {},
        raw_md=md,
        skill_path=str(skill_path),
    )


def validate_skill_input(record: "SkillRecord", input_data: dict) -> tuple[bool, str]:
    """
    Validate input_data against the skill's input_schema.

    Uses jsonschema when installed; falls back to required-property check.
    Returns (True, "") on success, (False, error_message) on validation failure.
    """
    schema = record.input_schema
    if not schema:
        return True, ""

    try:
        import jsonschema  # type: ignore
        try:
            jsonschema.validate(instance=input_data, schema=schema)
            return True, ""
        except jsonschema.ValidationError as ve:
            return False, str(ve.message)[:200]
    except ImportError:
        required = schema.get("required", [])
        missing = [k for k in required if k not in input_data]
        if missing:
            return False, f"Missing required input fields: {missing}"
        return True, ""
    except Exception as exc:
        return False, str(exc)[:200]


def execute_skill(workspace: Path,
                  skill_name: str,
                  input_data: dict,
                  router: "Any | None" = None) -> dict:
    """
    Execute a skill by name.

    Pipeline:
    1. Load SkillRecord from SKILL.md frontmatter.
    2. Validate input against input_schema.
    3. Dispatch to the LLM router using the skill body as system prompt.
    4. Return {result, skill_name, elapsed_ms, validation_passed}.

    Emits skill-level Prometheus metrics on completion.
    """
    import time as _time
    t_start = _time.monotonic()

    record = load_skill_record(workspace, skill_name)
    if record is None:
        return {
            "error": f"Skill '{skill_name}' not found",
            "skill_name": skill_name,
            "elapsed_ms": 0,
            "validation_passed": False,
        }

    valid, val_err = validate_skill_input(record, input_data)
    if not valid:
        return {
            "error": f"Input validation failed: {val_err}",
            "skill_name": skill_name,
            "elapsed_ms": 0,
            "validation_passed": False,
        }

    result_text = ""
    status      = "success"
    try:
        if router is not None:
            _, skill_body = _parse_frontmatter(record.raw_md)
            prompt = (
                f"[SKILL: {record.name}]\n\n"
                f"{skill_body.strip()}\n\n"
                f"[INPUT]\n{_skill_json.dumps(input_data, indent=2)}"
            )
            result_text = router.complete(
                prompt=prompt,
                call_class="EXEC",
                max_tokens=int(record.guardrails.get("max_tokens", 2048)),
            )
        else:
            result_text = (
                f"[Skill '{skill_name}' loaded but no router available to execute]"
            )
    except Exception as exc:
        result_text = str(exc)[:200]
        status      = "error"

    elapsed_s  = _time.monotonic() - t_start
    elapsed_ms = int(elapsed_s * 1000)

    try:
        from essence.infra.metrics import record_metric_skill
        record_metric_skill(
            skill=skill_name,
            status=status,
            skill_type=record.skill_type,
            duration_s=elapsed_s,
        )
    except Exception:
        pass

    return {
        "result":            result_text,
        "skill_name":        record.name,
        "skill_type":        record.skill_type,
        "elapsed_ms":        elapsed_ms,
        "validation_passed": True,
        "status":            status,
    }

