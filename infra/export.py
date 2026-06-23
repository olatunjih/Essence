""" — portable ZIP workspace bundle."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403
from essence.infra.orjson_shim import _fast_dumps  # noqa: F401  [real source bug]

# WORKSPACE EXPORT / IMPORT  — portable ZIP bundle
# ══════════════════════════════════════════════════════════════════════════════
# essence export  → workspace.zip containing all persistent state
# essence import FILE  → reconstructs workspace from ZIP
# The ZIP is unencrypted (for portability). Use  vault for secure transfer.
#
# Included: memory.jsonl, semantic_state.json, audit.jsonl, cost_log.jsonl,
#           config.toml, skills/, .api_keys.json (without tokens)

import zipfile  as _zipfile
import io       as _io


class WorkspaceExporter:
    """Export and import workspace state as a ZIP archive."""

    # Files/dirs to include in the export (relative to workspace root)
    _INCLUDE_FILES = [
        "memory/episodic.jsonl",   # legacy — kept for old workspaces
        "memory/episodic.db",      # v27: SQLite WAL store
        "memory/semantic_state.json",
        "memory/kv_store.json",
        "logs/audit.jsonl",
        "cost_log.jsonl",
        "config.toml",
        ".essence_version",
        "IDENTITY.md",
        "SOUL.md",
        "TOOLS.md",
    ]
    _INCLUDE_DIRS  = ["skills", "sops"]

    @classmethod
    def export(cls, workspace: Path, dest: Path | None = None) -> Path:
        """
        Export workspace to a ZIP file.
        Returns the path of the created ZIP.
        """
        ts      = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
        # v26: Write to parent of workspace to avoid including the ZIP in itself
        out     = dest or (workspace.parent / f"essence_export_{ts}.zip")
        written = 0
        with _zipfile.ZipFile(str(out), "w",
                               compression=_zipfile.ZIP_DEFLATED) as zf:
            # Individual files
            for rel in cls._INCLUDE_FILES:
                p = workspace / rel
                if p.exists():
                    zf.write(str(p), rel)
                    written += 1
            # Directory trees
            for d in cls._INCLUDE_DIRS:
                dpath = workspace / d
                if dpath.is_dir():
                    for child in sorted(dpath.rglob("*")):
                        if child.is_file() and child.stat().st_size < 5_000_000:
                            zf.write(str(child),
                                     str(child.relative_to(workspace)))
                            written += 1
            # Manifest
            manifest = {
                "essence_version": Essence_VERSION,
                "exported_at":  time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "workspace":    str(workspace),
                "files":        written,
            }
            zf.writestr("MANIFEST.json",
                        _fast_dumps(manifest, default=str))

        log.info("workspace_exported",
                 extra={"path": str(out), "files": written})
        return out

    @classmethod
    def import_zip(cls, src: Path, workspace: Path,
                    overwrite: bool = False) -> dict:
        """
        Import workspace state from a ZIP file.
        Returns {imported, skipped, errors}.
        """
        result = {"imported": [], "skipped": [], "errors": []}
        if not src.exists():
            result["errors"].append(f"file not found: {src}")
            return result

        _ws_resolved = workspace.resolve()
        with _zipfile.ZipFile(str(src), "r") as zf:
            for name in zf.namelist():
                if name == "MANIFEST.json":
                    continue
                # v27: Guard against ZIP path traversal (../../../etc/passwd)
                dest = (workspace / name).resolve()
                if not str(dest).startswith(str(_ws_resolved)):
                    log.warning("import_zip_path_traversal_blocked",
                                extra={"entry": name[:120]})
                    result["errors"].append(f"blocked path traversal: {name}")
                    continue
                if dest.exists() and not overwrite:
                    result["skipped"].append(name)
                    continue
                try:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(zf.read(name))
                    result["imported"].append(name)
                except Exception as _e:
                    result["errors"].append(f"{name}: {_e}")

        log.info("workspace_imported",
                 extra={"src": str(src),
                        "imported": len(result["imported"]),
                        "skipped":  len(result["skipped"])})
        return result


# ══════════════════════════════════════════════════════════════════════════════
