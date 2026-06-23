
"""Decision-Guide: rule library loader."""
from __future__ import annotations
import hashlib, json
from pathlib import Path


class RuleLibrary:
    """Loads and validates the 24-rule Decision-Guide library."""

    def __init__(self, rules: list[dict] | None = None) -> None:
        self._rules: list[dict] = rules or []

    @classmethod
    def from_json(cls, path: Path) -> "RuleLibrary":
        """Load a rule library from a JSON file."""
        data = json.loads(path.read_text(encoding="utf-8"))
        rules = data.get("rules", data) if isinstance(data, dict) else data
        lib = cls(rules)
        lib._validate()
        return lib

    @classmethod
    def from_embedded(cls) -> "RuleLibrary":
        """Load the embedded 24-rule library."""
        from essence.agents.decision_guide.loader import _EMBEDDED_RULES
        return cls(_EMBEDDED_RULES)

    @classmethod
    def load(cls, workspace: Path) -> "RuleLibrary":
        """
        Load rules from a workspace-local override if present, else the embedded defaults.

        Fix 11: operators can override the embedded rule library by placing a
        custom rule_library.json in <workspace>/rules/rule_library.json.  This
        file is created by `essence rules export` and can be edited directly.
        Changes to the override file take effect on the next SIGHUP reload (see
        infra/sighup.py) without a process restart.

        Workspace rules path: <workspace>/rules/rule_library.json

        Args:
            workspace: The Essence workspace directory (e.g. ~/.essence).

        Returns:
            A RuleLibrary loaded from the workspace override if it exists,
            otherwise the embedded 24-rule library.
        """
        override = workspace / "rules" / "rule_library.json"
        if override.exists():
            try:
                return cls.from_json(override)
            except Exception:
                # Fall back to embedded if the override is malformed
                pass
        return cls.from_embedded()

    def _validate(self) -> None:
        for i, rule in enumerate(self._rules):
            for req in ("id", "description", "risk", "tools", "writes_glob"):
                if req not in rule:
                    raise ValueError(
                        f"Decision-Guide rule[{i}] missing field '{req}'")

    def all_rules(self) -> list[dict]:
        return list(self._rules)

    def library_hash(self) -> str:
        canon = json.dumps(self._rules, sort_keys=True,
                           separators=(",", ":")).encode()
        return hashlib.sha256(canon).hexdigest()


# Embedded 24-rule library
_EMBEDDED_RULES: list[dict] = [
    {"id": "DG-001", "description": "No shell command writes to / without guardrail",
     "risk": "CRITICAL", "tools": ["shell"], "writes_glob": "/*",
     "guardrail_link": "G3", "action": "require_guardrail"},
    {"id": "DG-002", "description": "File writes to workspace only",
     "risk": "HIGH",     "tools": ["write_file"], "writes_glob": "/workspace/*",
     "guardrail_link": "G3", "action": "allow"},
    {"id": "DG-003", "description": "Web search requires research_only context",
     "risk": "LOW",      "tools": ["web_search"], "writes_glob": "",
     "guardrail_link": "", "action": "allow_research_only"},
    {"id": "DG-004", "description": "Python exec sandboxed",
     "risk": "HIGH",     "tools": ["python_exec"], "writes_glob": "",
     "guardrail_link": "G4", "action": "require_sandbox"},
    {"id": "DG-005", "description": "No network calls from EXEC context without G5",
     "risk": "HIGH",     "tools": ["shell"], "writes_glob": "",
     "guardrail_link": "G5", "action": "require_guardrail"},
    {"id": "DG-006", "description": "Heartbeat tasks require LOW risk classification",
     "risk": "LOW",      "tools": ["heartbeat_add"], "writes_glob": "",
     "guardrail_link": "", "action": "allow"},
    {"id": "DG-007", "description": "Image analysis requires VLM availability",
     "risk": "LOW",      "tools": ["analyze_image"], "writes_glob": "",
     "guardrail_link": "", "action": "check_hw_tier"},
    {"id": "DG-008", "description": "Config writes require G6 compliance check",
     "risk": "HIGH",     "tools": ["write_file"], "writes_glob": "*.config*",
     "guardrail_link": "G6", "action": "require_guardrail"},
    {"id": "DG-009", "description": "Credential files must never be written",
     "risk": "CRITICAL", "tools": ["write_file"], "writes_glob": "*.key|*.pem|*.env",
     "guardrail_link": "G7", "action": "deny"},
    {"id": "DG-010", "description": "Shell with sudo triggers G3 + G8",
     "risk": "CRITICAL", "tools": ["shell"], "writes_glob": "",
     "guardrail_link": "G8", "action": "require_guardrail"},
    {"id": "DG-011", "description": "Multi-file writes require coverage check",
     "risk": "MEDIUM",   "tools": ["write_file"], "writes_glob": "**/*",
     "guardrail_link": "", "action": "verify_coverage"},
    {"id": "DG-012", "description": "Database writes require explicit G9 auth",
     "risk": "HIGH",     "tools": ["shell"], "writes_glob": "*.db|*.sqlite",
     "guardrail_link": "G9", "action": "require_guardrail"},
    {"id": "DG-013", "description": "Read-only tools always allowed",
     "risk": "LOW",      "tools": ["read_file"], "writes_glob": "",
     "guardrail_link": "", "action": "allow"},
    {"id": "DG-014", "description": "PLAN call class cannot invoke exec tools",
     "risk": "HIGH",     "tools": ["shell","python_exec","write_file"], "writes_glob": "",
     "guardrail_link": "G1", "action": "deny_in_plan_class"},
    {"id": "DG-015", "description": "VERIFY call class may only read",
     "risk": "LOW",      "tools": ["read_file","web_search"], "writes_glob": "",
     "guardrail_link": "", "action": "allow_verify_only"},
    {"id": "DG-016", "description": "HIGH-risk tasks checkpoint every 25%",
     "risk": "HIGH",     "tools": [], "writes_glob": "",
     "guardrail_link": "G2", "action": "enforce_checkpoint"},
    {"id": "DG-017", "description": "Scratch namespace only during EXEC",
     "risk": "LOW",      "tools": [], "writes_glob": "scratch/*",
     "guardrail_link": "", "action": "allow_scratch"},
    {"id": "DG-018", "description": "PMP commit requires pre_pmp_commit hook",
     "risk": "MEDIUM",   "tools": [], "writes_glob": "",
     "guardrail_link": "G2", "action": "require_pmp_hook"},
    {"id": "DG-019", "description": "Token budget enforced by governor",
     "risk": "MEDIUM",   "tools": [], "writes_glob": "",
     "guardrail_link": "G2", "action": "check_token_budget"},
    {"id": "DG-020", "description": "Axiom 7: no order execution from research capsule",
     "risk": "CRITICAL", "tools": [], "writes_glob": "",
     "guardrail_link": "G1", "action": "enforce_axiom7"},
    {"id": "DG-021", "description": "Replanning requires delta ledger entry",
     "risk": "MEDIUM",   "tools": [], "writes_glob": "",
     "guardrail_link": "", "action": "require_delta"},
    {"id": "DG-022", "description": "Audit hook fires on every state transition",
     "risk": "LOW",      "tools": [], "writes_glob": "",
     "guardrail_link": "G10", "action": "audit_always"},
    {"id": "DG-023", "description": "Context reads must be in task.reads",
     "risk": "HIGH",     "tools": [], "writes_glob": "",
     "guardrail_link": "G5", "action": "enforce_context_scope"},
    {"id": "DG-024", "description": "User-facing output verified by judge before delivery",
     "risk": "MEDIUM",   "tools": [], "writes_glob": "",
     "guardrail_link": "", "action": "require_judge_verify"},
]
