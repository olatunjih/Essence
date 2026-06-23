""" — JSON schema registry (versioned)."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403

# JSON SCHEMA REGISTRY  — versioned tool + output schemas
# ══════════════════════════════════════════════════════════════════════════════
# Stores named schemas with versions. Validates tool inputs at dispatch time.
# Catches schema drift between upgrades before the LLM call.
#
# Usage:
#   SCHEMA_REGISTRY.register("plan_output", {"type":"object",...}, version="1.0")
#   SCHEMA_REGISTRY.validate("plan_output", {"steps": [...]})  # raises on error

class SchemaRegistry:
    """
    Central registry of named JSON schemas with versioning.
    Validates dicts at tool dispatch time to catch schema drift.
    Uses jsonschema when available; falls back to key-presence check.
    """

    def __init__(self) -> None:
        self._schemas: dict[str, dict] = {}   # name → {"schema": ..., "version": ...}
        self._lock    = threading.Lock()

    def register(self, name: str, schema: dict, version: str = "1.0") -> None:
        with self._lock:
            self._schemas[name] = {"schema": schema, "version": version}

    def validate(self, name: str, data: dict) -> tuple[bool, str]:
        """
        Validate data against the named schema.
        Returns (valid: bool, error_message: str).
        """
        with self._lock:
            entry = self._schemas.get(name)
        if entry is None:
            return True, "schema not registered (passthrough)"
        schema = entry["schema"]
        try:
            import jsonschema  # type: ignore
            jsonschema.validate(data, schema)
            return True, "ok"
        except ImportError:
            # Fallback: check required keys
            required = schema.get("required", [])
            missing  = [k for k in required if k not in data]
            if missing:
                return False, f"missing required keys: {missing}"
            return True, "ok (shallow)"
        except Exception as _e:
            return False, str(_e)[:200]

    def get(self, name: str) -> dict | None:
        with self._lock:
            entry = self._schemas.get(name)
            return entry["schema"] if entry else None

    def names(self) -> list[str]:
        with self._lock:
            return list(self._schemas.keys())

    def _register_builtin_schemas(self) -> None:
        """Register schemas for core Essence outputs."""
        self.register("plan_output", {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["step", "action", "tool"],
                "properties": {
                    "step":   {"type": "integer"},
                    "action": {"type": "string"},
                    "tool":   {"type": "string"},
                    "args":   {"type": "object"},
                    "depends_on": {"type": "array", "items": {"type": "integer"}},
                }
            }
        }, version="1.0")

        self.register("consolidation_output", {
            "type": "object",
            "properties": {
                "facts":   {"type": "array", "items": {"type": "string"}},
                "profile": {"type": "object"},
                "triples": {"type": "array"},
                "retain":  {"type": "array", "items": {"type": "string"}},
            }
        }, version="1.0")

        self.register("critic_output", {
            "type": "object",
            "required": ["passed"],
            "properties": {
                "passed":   {"type": "boolean"},
                "category": {"type": "string"},
                "evidence": {"type": "string"},
                "fix_hint": {"type": "string"},
            }
        }, version="1.0")

        self.register("semantic_fact", {
            "type": "object",
            "required": ["entity", "relation", "attribute", "value"],
            "properties": {
                "entity":     {"type": "string"},
                "relation":   {"type": "string"},
                "attribute":  {"type": "string"},
                "value":      {"type": "string"},
                "confidence": {"type": "number"},
                "source":     {"type": "string"},
            }
        }, version="1.0")


SCHEMA_REGISTRY = SchemaRegistry()
SCHEMA_REGISTRY._register_builtin_schemas()


# ══════════════════════════════════════════════════════════════════════════════
