"""ModelFabric — routes IntentType to optimal model specialisation.

Roles: reasoning | coding | vision | research | planning | general
Config override: <workspace>/config/model_fabric.yaml
"""
from __future__ import annotations
import logging, os
from pathlib import Path

log = logging.getLogger("essence.backends.model_fabric")

_DEFAULT_ROLES: dict[str, str] = {
    "reasoning": os.environ.get("ESSENCE_MODEL_REASONING", "qwen3:32b"),
    "coding":    os.environ.get("ESSENCE_MODEL_CODING",    "qwen2.5-coder:14b"),
    "vision":    os.environ.get("ESSENCE_MODEL_VISION",    "llava:13b"),
    "research":  os.environ.get("ESSENCE_MODEL_RESEARCH",  "gemini/gemini-1.5-pro"),
    "planning":  os.environ.get("ESSENCE_MODEL_PLANNING",  "claude-sonnet-4-6"),
    "general":   os.environ.get("ESSENCE_MODEL_GENERAL",   "qwen3:8b"),
}
_INTENT_TO_ROLE: dict[str, str] = {
    "analysis":        "reasoning",
    "prediction":      "reasoning",
    "code_generation": "coding",
    "research":        "research",
    "summarization":   "research",
    "task_automation": "planning",
    "comparison":      "reasoning",
    "explanation":     "general",
    "creative":        "general",
}


class ModelFabric:
    def __init__(self, workspace: Path | None = None) -> None:
        self._roles = dict(_DEFAULT_ROLES)
        if workspace:
            self._load_config(workspace)

    def _load_config(self, workspace: Path) -> None:
        cfg = workspace / "config" / "model_fabric.yaml"
        if not cfg.exists():
            return
        try:
            import yaml
            raw = yaml.safe_load(cfg.read_text()) or {}
            self._roles.update(raw.get("roles", {}))
        except Exception as exc:
            log.warning("model_fabric.yaml load failed: %s", exc)

    def select_model(self, intent_type: str,
                     has_images: bool = False) -> str:
        if has_images:
            return self._roles["vision"]
        role = _INTENT_TO_ROLE.get(intent_type, "general")
        return self._roles.get(role, self._roles["general"])
