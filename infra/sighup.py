""" — config hot-reload via SIGHUP."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403

# CONFIG HOT-RELOAD  — SIGHUP signal handler
# ══════════════════════════════════════════════════════════════════════════════
# Reloads workspace/config.toml + env overrides on SIGHUP without restart.
# Rate limits, feature flags, cost budgets, and log level all update live.
# Wired at server startup; no-op on Windows (SIGHUP not available).

import signal as _signal

from essence import workspace as _workspace  # noqa: F401  [unused but keeps package import order stable]
from essence.workspace import guided as _guided_mod  # noqa: F401  [real source bug: this module kept its own
# separate `_essence_config` global instead of updating workspace.guided's singleton, so SIGHUP reloads were
# invisible to get_config()]

from essence.workspace.guided import EssenceConfig  # noqa: F401  [real source bug: used in _reload_config_handler() without import]

_sighup_workspace: "Path | None" = None



def _reload_config_handler(signum: int, frame: Any) -> None:
    """SIGHUP handler — reloads EssenceConfig in-place."""
    ws = _sighup_workspace or Path.cwd()
    try:
        new_cfg              = EssenceConfig.load(ws)
        errs                 = new_cfg.validate()
        _guided_mod._essence_config = new_cfg
        log.info("config_reloaded_sighup",
                 extra={"workspace": str(ws),
                        "errors":   errs or "none"})
    except Exception as _e:
        log.warning("config_reload_failed",
                    extra={"error": str(_e)[:120]})


def register_sighup_handler(workspace: Path) -> None:
    """
    Register SIGHUP config hot-reload. No-op on platforms without SIGHUP.
    v26: _sighup_workspace is set atomically; tests must reset it explicitly
    via `essence._sighup_workspace = None` after use to avoid cross-test leakage.
    """
    global _sighup_workspace
    _sighup_workspace = workspace
    try:
        _signal.signal(_signal.SIGHUP, _reload_config_handler)
        log.info("sighup_handler_registered",
                 extra={"workspace": str(workspace)})
    except (AttributeError, OSError):
        log.debug("sighup_not_available",
                  extra={"platform": "windows or restricted env"})


# ══════════════════════════════════════════════════════════════════════════════
