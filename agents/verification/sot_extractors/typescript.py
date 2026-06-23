
"""TypeScript source-of-truth extractor: produces a byte-stable manifest."""
from __future__ import annotations
import hashlib, json, re
from pathlib import Path

VERSION = "1.0.0"


class TypeScriptSoTExtractor:
    """
    Extracts exported symbol names from TypeScript files.
    Uses regex-based parsing (no tsc dependency).
    """

    version = VERSION

    _EXPORT_RE = re.compile(
        r"^export\s+(?:(?:default|abstract|declare)\s+)*"
        r"(?:class|interface|function|const|let|var|type|enum)\s+(\w+)",
        re.MULTILINE,
    )

    def extract(self, source_root: Path) -> dict:
        modules: dict[str, list[str]] = {}
        for ts_file in sorted(source_root.rglob("*.ts")):
            if "node_modules" in ts_file.parts or ".d.ts" in ts_file.name:
                continue
            rel = str(ts_file.relative_to(source_root))
            try:
                src = ts_file.read_text(encoding="utf-8", errors="replace")
                names = sorted(set(self._EXPORT_RE.findall(src)))
                modules[rel] = names
            except Exception:
                modules[rel] = []
        return {
            "extractor": "typescript",
            "version":   VERSION,
            "modules":   modules,
        }

    def manifest_hash(self, source_root: Path) -> str:
        manifest = self.extract(source_root)
        canon = json.dumps(manifest, sort_keys=True,
                           separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(canon).hexdigest()
