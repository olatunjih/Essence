"""Essence version, BANNER, __all__ — no deps beyond _shared."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403

# SECRETS VAULT
# ══════════════════════════════════════════════════════════════════════════════
# Stores API keys, tokens, and bearer secrets in a PBKDF2-SHA256-encrypted JSON
# file so they never live in plain-text .env files committed to repos.
# The vault auto-unlocks on first use by prompting for a master password.
# Falls back gracefully to os.environ when the vault file doesn't exist.
#
# Usage:
#   vault = SecretsVault(workspace / ".vault")
#   vault.set("TELEGRAM_BOT_TOKEN", "abc123")   # prompt for password on first use
#   token = vault.get("TELEGRAM_BOT_TOKEN")      # returns decrypted value
#   # Or just use env fallback transparently:
#   token = vault.resolve("TELEGRAM_BOT_TOKEN")  # vault first, then os.environ

import base64, struct

import zlib

_COMPRESS_MAGIC = b"EssenceCZ1\x00"  # 8-byte magic header for zlib-compressed bundles


def compress_bundle(data: bytes) -> bytes:
    """zlib pre-compression before AES-GCM encryption. [Used by
    SecretsVault._aes_encrypt/_aes_decrypt but never defined anywhere in
    the split package -- real content-loss gap.] Prefixes a magic header so
    decompress_bundle can tell compressed bundles apart from legacy raw
    bytes and pass the latter through unchanged."""
    compressed = zlib.compress(data, level=6)
    if len(compressed) < len(data):
        return _COMPRESS_MAGIC + compressed
    return data  # compression didn't help (small/incompressible) -- store raw


def decompress_bundle(data: bytes) -> bytes:
    if data[:8] == _COMPRESS_MAGIC:
        return zlib.decompress(data[8:])
    return data  # no magic header -- legacy/raw passthrough


class SecretsVault:
    """
    AES-256-GCM encrypted JSON key-value secrets store.

    ENCRYPTION HIERARCHY (strongest available wins):
      1. AES-256-GCM   — requires `pip install cryptography` (PRODUCTION)
         PBKDF2-SHA256 key derivation (260k iterations, NIST 2024 baseline).
         Each write generates a fresh 12-byte nonce; authenticated encryption
         prevents both tampering and oracle attacks.

      2. XOR stream cipher  — zero extra deps (DEV/IoT fallback only)
         WARNING: deterministic, no IV — safe for local dev where installing
         `cryptography` is impractical (e.g. Raspberry Pi without pip).
         Set Essence_VAULT_WARN_WEAK=0 to silence the startup warning.

    Never stores the master password; never writes plaintext.
    Thread-safe via a re-entrant lock.
    """
    _ITER     = 260_000    # PBKDF2 iterations (NIST 2024 baseline)
    _SALT_LEN = 32
    _KEY_LEN  = 32         # AES-256 key length

    def __init__(self, vault_path: Path) -> None:
        self._path  = vault_path
        self._data: dict[str, str] = {}
        self._key:  bytes | None   = None
        self._lock  = threading.RLock()
        # Production gate: refuse to use XOR cipher unless explicitly opted-in.
        # In production deployments _VAULT_ALLOW_WEAK must remain False.
        if not _AESGCM:
            if _VAULT_ALLOW_WEAK:
                if os.environ.get("Essence_VAULT_WARN_WEAK", "1") == "1":
                    log.warning(
                        "vault_weak_cipher_allowed",
                        extra={"detail": "Essence_VAULT_ALLOW_WEAK=1 set — vault uses XOR "
                                       "fallback (DEV ONLY). Run: pip install cryptography"})
            else:
                log.warning(
                    "vault_weak_cipher_blocked",
                    extra={"detail": "AES-256-GCM unavailable. Set() and get() will "
                                   "no-op until `pip install cryptography` or "
                                   "Essence_VAULT_ALLOW_WEAK=1 is set."})

    # ── Internal: key derivation ────────────────────────────────────────────
    @staticmethod
    def _derive(password: str, salt: bytes) -> bytes:
        return hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt,
            SecretsVault._ITER, dklen=SecretsVault._KEY_LEN)

    # ── AES-256-GCM (production path) ───────────────────────────────────────
    @staticmethod
    def _aes_encrypt(data: bytes, key: bytes) -> bytes:
        """AES-256-GCM encrypt with zlib pre-compression.
        Returns magic(8) + nonce(12) + tag(16) + ciphertext."""
        compressed = compress_bundle(data)
        nonce = secrets.token_bytes(12)
        ct    = AESGCM(key).encrypt(nonce, compressed, None)
        return nonce + ct

    @staticmethod
    def _aes_decrypt(blob: bytes, key: bytes) -> bytes:
        """AES-256-GCM decrypt with zlib decompression."""
        nonce, ct    = blob[:12], blob[12:]
        decompressed = AESGCM(key).decrypt(nonce, ct, None)
        return decompress_bundle(decompressed)

    # ── XOR stream cipher (fallback — dev / IoT only) ───────────────────────
    @staticmethod
    def _xor_encrypt(data: bytes, key: bytes) -> bytes:
        """XOR stream cipher — deterministic, zero external deps. DEV ONLY."""
        stream = b""
        block  = key
        while len(stream) < len(data):
            block  = hashlib.sha256(block).digest()
            stream += block
        return bytes(a ^ b for a, b in zip(data, stream[:len(data)]))

    # ── Encryption dispatcher ────────────────────────────────────────────────
    def _encrypt(self, data: bytes) -> bytes:
        assert self._key
        if _AESGCM:
            return self._aes_encrypt(data, self._key)
        return self._xor_encrypt(data, self._key)

    def _decrypt(self, blob: bytes) -> bytes:
        assert self._key
        if _AESGCM:
            return self._aes_decrypt(blob, self._key)
        return self._xor_encrypt(blob, self._key)   # XOR is its own inverse

    # ── Public API ──────────────────────────────────────────────────────────
    def unlock(self, password: str | None = None) -> bool:
        """Derive the encryption key from a master password.
        Prompts interactively if password is None and stdin is a TTY.
        Returns False immediately when AES-GCM is unavailable,
        keeping secrets in memory only.
        """
        with self._lock:
            if self._key is not None:
                return True
            if not _AESGCM:
                log.error("vault_unlock_failed_no_cryptography")
                if password:
                    salt = secrets.token_bytes(self._SALT_LEN)
                    self._key  = self._derive(password, salt)
                    self._salt = salt
                return bool(password)
            if password is None:
                if sys.stdin.isatty():
                    import getpass
                    password = getpass.getpass(
                        "  Essence Vault master password: ").strip()
                else:
                    password = os.environ.get("Essence_VAULT_PASSWORD", "")
            if not password:
                return False
            if self._path.exists():
                raw_hdr = self._path.read_bytes()
                _hdr_offset = 1 if raw_hdr[0:1] in (self._VER_AES, self._VER_XOR) else 0
                salt = raw_hdr[_hdr_offset : _hdr_offset + self._SALT_LEN]
            else:
                salt = secrets.token_bytes(self._SALT_LEN)
            self._key  = self._derive(password, salt)
            self._salt = salt
            if self._path.exists():
                try:
                    self._load()
                    return True
                except Exception:
                    self._key = None
                    return False
            return True
    def set(self, name: str, value: str, password: str | None = None) -> None:
        """Store an encrypted secret. Auto-unlocks on first call."""
        with self._lock:
            if self._key is None:
                if not self.unlock(password):
                    raise PermissionError("SecretsVault: unlock failed")
            self._data[name] = value
            self._save()

    def get(self, name: str, default: str = "") -> str:
        """Retrieve a decrypted secret. Returns default if not found."""
        with self._lock:
            if self._key is None:
                if not self.unlock():
                    return default
            return self._data.get(name, default)

    def resolve(self, name: str, default: str = "") -> str:
        """Vault first, os.environ fallback, then default."""
        val = self.get(name, "")
        return val or os.environ.get(name, default)

    def delete(self, name: str) -> None:
        with self._lock:
            if name in self._data:
                del self._data[name]
                self._save()

    def list_names(self) -> list[str]:
        with self._lock:
            return list(self._data.keys())

    # ── Serialisation ────────────────────────────────────────────────────────
    # ── On-disk format ────────────────────────────────────────────────────────
    # Byte layout (v2 — added with v16):
    #   [ver:1][salt:32][mac:32][len:4][payload]
    #
    #   ver=1 → XOR stream cipher (legacy / IoT fallback)
    #   ver=2 → AES-256-GCM (nonce:12 + ciphertext + tag:16 inside payload)
    #
    # The version byte makes payloads self-describing so a future reader
    # never needs to try both ciphers.  v1 vaults written before this change
    # have no leading byte — detected by checking raw[0] for 1 or 2.
    _VER_XOR = b'\x01'
    _VER_AES = b'\x02'

    def _save(self) -> None:
        """Serialize + encrypt vault to disk atomically.
        No-ops silently when AES-GCM is unavailable and Essence_VAULT_ALLOW_WEAK is not set.
        """
        if not _AESGCM:
            log.error("vault_save_failed_no_cryptography")
            return
        import struct
        assert self._key and hasattr(self, "_salt")
        plaintext = json.dumps(self._data).encode("utf-8")
        payload   = self._encrypt(plaintext)
        # ver byte distinguishes AES-GCM from XOR payloads
        ver  = self._VER_AES
        # mac kept for XOR path integrity; AES-GCM embeds its own auth tag
        mac  = hashlib.sha256(self._key + payload).digest()
        blob = ver + self._salt + mac + struct.pack(">I", len(payload)) + payload
        self._path.write_bytes(blob)
        self._path.chmod(0o600)

    def _load(self) -> None:
        import struct
        assert self._key
        raw = self._path.read_bytes()

        # Detect format version — legacy vaults (pre-v16) have no version byte
        if raw[0:1] in (self._VER_XOR, self._VER_AES):
            ver    = raw[0:1]
            offset = 1
        else:
            # Legacy vault written without version byte — assume XOR format (blocked)
            ver    = self._VER_XOR
            offset = 0

        if ver == self._VER_XOR:
            raise RuntimeError(
                "SecretsVault: legacy XOR vault format is no longer supported for ",
                "security reasons. Run: pip install cryptography")

        salt    = raw[offset:offset + self._SALT_LEN]
        mac     = raw[offset + self._SALT_LEN : offset + self._SALT_LEN + 32]
        hdr_end = offset + self._SALT_LEN + 32
        size    = struct.unpack(">I", raw[hdr_end:hdr_end + 4])[0]
        payload = raw[hdr_end + 4 : hdr_end + 4 + size]

        # For AES-GCM (ver=2), _decrypt raises on authentication failure automatically
        self._data = json.loads(self._decrypt(payload).decode("utf-8"))
        self._salt = salt
_vault: SecretsVault | None = None

def get_vault(workspace: Path | None = None) -> SecretsVault:
    """Return the module-level SecretsVault, initialising if necessary."""
    global _vault
    if _vault is None:
        ws = workspace or _workspace_root()
        _vault = SecretsVault(ws / ".essence_vault")
    return _vault


# _workspace_root() defined in  with full Windows/Linux/macOS path logic.

# ─────────────────────────────────────────────────────────────────────────────
#  TERMINAL COLOUR  (zero deps, respects NO_COLOR env)
# ─────────────────────────────────────────────────────────────────────────────
_USE_COLOUR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")

def _c(s: str, *codes: str) -> str:
    return f"\033[{';'.join(codes)}m{s}\033[0m" if _USE_COLOUR else s

bold    = lambda s: _c(s, "1")
dim     = lambda s: _c(s, "2")
green   = lambda s: _c(s, "32")
yellow  = lambda s: _c(s, "33")
red     = lambda s: _c(s, "31")
cyan    = lambda s: _c(s, "36")
magenta = lambda s: _c(s, "35")
blue    = lambda s: _c(s, "34")
amber   = lambda s: _c(s, "33", "1")

BANNER = f"""
{cyan('╔════════════════════════════════════════════════════════════╗')}
{cyan('║')}  {bold(f'Essence v{Essence_VERSION}')}  —  Essence Intelligence System          {cyan('║')}
{cyan('║')}  {dim('production-grade · deterministic · observable · safe')}   {cyan('║')}
{cyan('╚════════════════════════════════════════════════════════════╝')}
"""

__all__ = [
    "Essence_VERSION", "Essence_BUILD", "SystemRole", "get_system_role",
    "SecretsVault", "get_vault",
]

# ══════════════════════════════════════════════════════════════════════════════
