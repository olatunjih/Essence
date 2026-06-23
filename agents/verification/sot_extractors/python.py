
"""Python source-of-truth extractor: produces a byte-stable manifest of public API."""
from __future__ import annotations
import ast, hashlib, json
from pathlib import Path

VERSION = "1.0.0"


class PythonSoTExtractor:
    """
    Extracts public API surface of Python packages as a byte-stable JSON manifest.
    Used by acceptance criterion 21 (sot_extractor byte-stability).
    """

    version = VERSION

    def extract(self, source_root: Path) -> dict:
        """Walk source_root, extract all public names, return a manifest dict."""
        modules: dict[str, list[str]] = {}
        for py_file in sorted(source_root.rglob("*.py")):
            if "__pycache__" in py_file.parts:
                continue
            rel = str(py_file.relative_to(source_root))
            try:
                src = py_file.read_text(encoding="utf-8", errors="replace")
                tree = ast.parse(src)
                names = self._public_names(tree)
                modules[rel] = names
            except SyntaxError:
                modules[rel] = []
        return {
            "extractor": "python",
            "version":   VERSION,
            "modules":   modules,
        }

    def _public_names(self, tree: ast.AST) -> list[str]:
        names: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if not node.name.startswith("_"):
                    names.append(node.name)
        return sorted(set(names))

    def manifest_hash(self, source_root: Path) -> str:
        """Return sha256 of the canonical manifest bytes."""
        manifest = self.extract(source_root)
        canon = json.dumps(manifest, sort_keys=True,
                           separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(canon).hexdigest()
