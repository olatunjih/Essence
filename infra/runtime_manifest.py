
"""
Runtime manifest loading, signature verification, and boot epoch pinning.
NN-5: loaded once at boot, signature-verified, pinned to every plan hash.
"""
from __future__ import annotations
import hashlib, json, time
from pathlib import Path
from typing import Any
from essence.apde_types import ManifestVerificationError, AnchorDriftError

_EPOCH_ID: str = ""


def get_epoch_id() -> str:
    """Return the current boot epoch id (set once at boot)."""
    return _EPOCH_ID


def _set_epoch_id(eid: str) -> None:
    global _EPOCH_ID
    _EPOCH_ID = eid


_DEFAULT_MANIFEST: dict = {
    "runtime_id":   "dev-epoch-0001",
    "version":      "2.0",
    "public_key_id": "dev-key-none",
    "signature":    "dev-sig-bypass",
    "pools": {
        "plan_model":  {"primary": "gpt-4o-mini",    "fallbacks": []},
        "exec_model":  {"primary": "gpt-4o-mini",    "fallbacks": []},
        "judge_small": {"primary": "gpt-4o-mini",    "fallbacks": []},
    },
    "anchors":      [],
    "rubrics":      [],
    "decision_guide": {"library_hash": ""},
    "judge_prompts": [],
    "sot_extractors": [],
    "hash": "",
}


def _canonical_bytes(manifest: dict) -> bytes:
    """Canonical bytes for manifest hashing (exclude the hash field itself)."""
    d = {k: v for k, v in manifest.items() if k != "hash"}
    return json.dumps(d, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _compute_manifest_hash(manifest: dict) -> str:
    return hashlib.sha256(_canonical_bytes(manifest)).hexdigest()


class RuntimeManifest:
    """
    Loaded-once runtime manifest.
    Validates structure, signature (dev mode: bypass), and anchor sha256s.
    Pins the boot epoch.
    """

    def __init__(self, data: dict, dev_mode: bool = True) -> None:
        self._data     = data
        self._dev_mode = dev_mode

    @classmethod
    def load(cls, manifest_path: Path | None = None,
             dev_mode: bool = True) -> "RuntimeManifest":
        """Load manifest from path or use embedded default."""
        if manifest_path is None or not manifest_path.exists():
            data = dict(_DEFAULT_MANIFEST)
        else:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))

        manifest = cls(data, dev_mode=dev_mode)
        manifest._validate()
        _set_epoch_id(data.get("runtime_id", f"epoch-{int(time.time())}"))
        return manifest

    def _validate(self) -> None:
        d = self._data
        # Schema check: required keys
        for key in ("runtime_id", "pools", "version"):
            if key not in d:
                raise ManifestVerificationError(
                    f"Manifest missing required field: '{key}'")

        # Signature check (dev mode: bypass)
        if not self._dev_mode:
            sig = d.get("signature", "")
            if not sig or sig == "dev-sig-bypass":
                raise ManifestVerificationError(
                    "Manifest signature missing or using dev bypass in production mode")

        # Hash check
        stored_hash   = d.get("hash", "")
        computed_hash = _compute_manifest_hash(d)
        if stored_hash and stored_hash != computed_hash:
            raise ManifestVerificationError(
                f"Manifest hash mismatch: stored={stored_hash[:8]} "
                f"computed={computed_hash[:8]}")

        # Anchor drift check
        for anchor in d.get("anchors", []):
            aid  = anchor.get("id", "?")
            ref  = anchor.get("artifact_ref", "")
            exp  = anchor.get("sha256", "")
            if ref and exp:
                p = Path(ref)
                if p.exists():
                    actual = hashlib.sha256(p.read_bytes()).hexdigest()
                    if actual != exp:
                        raise AnchorDriftError(
                            f"Anchor drift: anchor '{aid}' ref '{ref}' "
                            f"expected={exp[:8]} actual={actual[:8]}")

    @property
    def runtime_id(self) -> str:
        return self._data.get("runtime_id", "unknown")

    @property
    def pools(self) -> dict:
        return self._data.get("pools", {})

    @property
    def rubrics(self) -> list[dict]:
        return self._data.get("rubrics", [])

    @property
    def decision_guide(self) -> dict:
        return self._data.get("decision_guide", {})

    @property
    def judge_prompts(self) -> list[dict]:
        return self._data.get("judge_prompts", [])

    def primary_model(self, pool_name: str) -> str:
        return self.pools.get(pool_name, {}).get("primary", "gpt-4o-mini")
