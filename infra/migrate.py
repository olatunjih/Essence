""" — version-aware workspace upgrader."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403

# WORKSPACE MIGRATOR  — version-aware upgrade path
# ══════════════════════════════════════════════════════════════════════════════
# Detects workspace version from .essence_version file and applies incremental
# migrations to bring it up to the current Essence_VERSION.
# Safe to run on every startup — idempotent, never overwrites user data.

_WORKSPACE_VERSION_FILE = ".essence_version"


@_dc.dataclass
class MigrationResult:
    from_version: str
    to_version:   str
    applied:      list[str] = _dc.field(default_factory=list)
    skipped:      list[str] = _dc.field(default_factory=list)
    errors:       list[str] = _dc.field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


class WorkspaceMigrator:
    """
    Applies incremental workspace migrations on startup.
    Each migration is a (from_version_prefix, description, fn) tuple.
    Migrations are applied in order; already-applied ones are skipped.
    """

    _MIGRATIONS: list[tuple[str, str, "Callable[[Path], None]"]] = []

    @classmethod
    def register(cls, min_version: str, description: str):
        """Decorator: register a migration function."""
        def _deco(fn: "Callable[[Path], None]"):
            cls._MIGRATIONS.append((min_version, description, fn))
            return fn
        return _deco

    @classmethod
    def detect_version(cls, workspace: Path) -> str:
        """Read the workspace version. Returns '0.0.0' if unversioned."""
        vf = workspace / _WORKSPACE_VERSION_FILE
        if vf.exists():
            try:
                return vf.read_text(encoding="utf-8").strip()
            except Exception:
                pass
        return "0.0.0"

    @classmethod
    def write_version(cls, workspace: Path, version: str) -> None:
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / _WORKSPACE_VERSION_FILE).write_text(
            version + "\n", encoding="utf-8")

    @classmethod
    def run(cls, workspace: Path) -> MigrationResult:
        """Run all pending migrations. Returns a MigrationResult."""
        from_ver = cls.detect_version(workspace)
        result   = MigrationResult(from_version=from_ver,
                                    to_version=Essence_VERSION)
        for min_ver, desc, fn in cls._MIGRATIONS:
            try:
                # Semantic version comparison (fixes string-sort bug for 2-digit versions)
                def _sv(v: str) -> tuple:
                    try: return tuple(int(x) for x in v.split("."))
                    except Exception: return (0, 0, 0)
                if _sv(from_ver) >= _sv(min_ver) and from_ver != "0.0.0":
                    result.skipped.append(desc)
                    continue
                fn(workspace)
                result.applied.append(desc)
                log.info("workspace_migration_applied", extra={"desc": desc})
            except Exception as _e:
                result.errors.append(f"{desc}: {_e}")
                log.warning("workspace_migration_error",
                            extra={"desc": desc, "error": str(_e)[:120]})
        if result.applied or not result.errors:
            cls.write_version(workspace, Essence_VERSION)
        return result


# ── Built-in migrations ────────────────────────────────────────────────────────
@WorkspaceMigrator.register("0.0.0", "create memory/semantic_state.json")
def _mig_semantic_state(ws: Path) -> None:
    p = ws / "memory" / "semantic_state.json"
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("[]", encoding="utf-8")


@WorkspaceMigrator.register("0.0.0", "create logs directory")
def _mig_logs_dir(ws: Path) -> None:
    (ws / "logs").mkdir(parents=True, exist_ok=True)


@WorkspaceMigrator.register("20.0.0", "create bandit_state.json")
def _mig_bandit_state(ws: Path) -> None:
    p = ws / "logs" / "bandit_state.json"
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{}", encoding="utf-8")


@WorkspaceMigrator.register("21.0.0", "create .api_keys.json skeleton")
def _mig_api_keys(ws: Path) -> None:
    p = ws / ".api_keys.json"
    if not p.exists():
        p.write_text("[]", encoding="utf-8")
        p.chmod(0o600)


@WorkspaceMigrator.register("22.0.0", "scaffold config.toml from defaults")
def _mig_config_toml(ws: Path) -> None:
    p = ws / "config.toml"
    if not p.exists():
        cfg = EssenceConfig()
        p.write_text(cfg.to_toml(), encoding="utf-8")


@WorkspaceMigrator.register("23.0.0", "create plugins directory")
def _mig_plugins_dir(ws: Path) -> None:
    (ws / "plugins").mkdir(parents=True, exist_ok=True)


# ══════════════════════════════════════════════════════════════════════════════
