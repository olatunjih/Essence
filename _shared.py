"""
essence._shared — single source of truth for stdlib imports,
optional-dep guards, colour helpers, logging factory, SystemRole.
Every sub-module does:  from essence._shared import *
"""
from __future__ import annotations

import argparse, asyncio, dataclasses as _dc, enum as _enum
import hashlib, json, logging, math, os, platform, re
import secrets, shlex, shutil, subprocess, sys, textwrap
import threading, time, urllib.error, urllib.parse, urllib.request
from pathlib import Path
from typing import Any, Callable, Iterator, AsyncIterator, TYPE_CHECKING

ESSENCE_VERSION = "1.0.0"
ESSENCE_BUILD   = "20260621"
# Legacy aliases kept for any submodule still referencing old names
Essence_VERSION = ESSENCE_VERSION
Essence_BUILD   = ESSENCE_BUILD
MIN_PYTHON   = (3, 10)
GITHUB_REPO  = os.environ.get("Essence_GITHUB_REPO", "")

_OLLAMA_HOST      = os.environ.get("OLLAMA_HOST",           "http://127.0.0.1:11434")
_VLLM_HOST        = os.environ.get("VLLM_HOST",             "http://127.0.0.1:8000")
_MLX_HOST         = os.environ.get("MLX_HOST",              "http://127.0.0.1:8080")
_TEAM_ID          = os.environ.get("Essence_TEAM_ID",          "local")
_COST_BUDGET      = int(os.environ.get("Essence_COST_BUDGET",  "0"))
_SOP_DIR          = os.environ.get("Essence_SOP_DIR",          "")
_EVAL_ON_START    = os.environ.get("Essence_EVAL_ON_START",    "0") == "1"
_DRIFT_WEBHOOK    = os.environ.get("Essence_DRIFT_WEBHOOK",    "")
_VAULT_ALLOW_WEAK = os.environ.get("Essence_VAULT_ALLOW_WEAK", "0") == "1"
_MODEL_OVERRIDE   = os.environ.get("Essence_MODEL",            "")
_BACKEND_OVERRIDE = os.environ.get("Essence_BACKEND",          "")
_ALIGNMENT_FAILOPEN = os.environ.get("Essence_ALIGNMENT_FAILOPEN", "0") == "1"
_EXTRA_PROVIDERS_RAW = os.environ.get("Essence_EXTRA_PROVIDERS", "")

# pydantic
try:
    from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator, SecretStr
    _PYDANTIC = True
except ImportError:
    import warnings
    warnings.warn("Essence: 'pydantic' required. pip install pydantic>=2.0  "
                  "(running in degraded mode)", RuntimeWarning, stacklevel=2)
    class BaseModel:   # type: ignore[no-redef]
        model_config: dict = {}
        def __init__(self, **kw: Any) -> None:
            for k, v in kw.items(): setattr(self, k, v)
        @classmethod
        def model_rebuild(cls, **_: Any) -> None: ...
        def model_dump(self, **_: Any) -> dict:
            return {k: v for k, v in vars(self).items() if not k.startswith("_")}
    def ConfigDict(**_: Any) -> dict: return {}          # type: ignore[misc]
    def Field(default: Any = None, **_: Any) -> Any: return default  # type: ignore[misc]
    def field_validator(*_a: Any, **_kw: Any):           # type: ignore[misc]
        def _d(fn: Any) -> Any: return fn
        return _d
    def model_validator(**_kw: Any):                     # type: ignore[misc]
        def _d(fn: Any) -> Any: return fn
        return _d
    class SecretStr(str):  # type: ignore[no-redef]
        def __repr__(self): return "SecretStr('**********')"
        def get_secret_value(self) -> str: return str(self)
    _PYDANTIC = False

# httpx
try:
    import httpx as _httpx; _HTTPX = True
except ImportError:
    _httpx = None; _HTTPX = False  # type: ignore[assignment]

# cryptography
try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes as _crypto_hashes
    _CRYPTO = True
except ImportError:
    _CRYPTO = False; AESGCM = None  # type: ignore[assignment,misc]
    PBKDF2HMAC = None; _crypto_hashes = None  # type: ignore[assignment]

# Naming drift fix: vault.py/memory.py/migrator.py/installer.py all check a
# flag literally named `_AESGCM` (not `_CRYPTO`) to decide whether strong
# encryption is available. _shared.py only ever defined `_CRYPTO` for this,
# so `_AESGCM` was a NameError everywhere it was used — real source bug.
_AESGCM = _CRYPTO

import zlib

def compress_bundle(data: bytes) -> bytes:
    """zlib pre-compression for vault payloads before AES-GCM sealing.
    [Used by core/vault.py's _aes_encrypt/_aes_decrypt but never defined
    anywhere — content-loss gap from the build_pkg.py split.]"""
    return zlib.compress(data, level=6)


def decompress_bundle(data: bytes) -> bytes:
    """Inverse of compress_bundle(). Vault payloads written before
    compression support was added (or any other raw bytes lacking the zlib
    stream header) are passed through unchanged rather than raising."""
    try:
        return zlib.decompress(data)
    except zlib.error:
        return data


# colour
_USE_COLOUR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
def _c(s: str, *codes: str) -> str:
    return f"\033[{';'.join(codes)}m{s}\033[0m" if _USE_COLOUR else s
bold   = lambda s: _c(s, "1")
dim    = lambda s: _c(s, "2")
green  = lambda s: _c(s, "32")
yellow = lambda s: _c(s, "33")
red    = lambda s: _c(s, "31")
cyan   = lambda s: _c(s, "36")
blue   = lambda s: _c(s, "34")

# logging
def _setup_logging(name: str = "essence") -> logging.Logger:
    _log = logging.getLogger(name)
    if _log.handlers: return _log
    _log.setLevel(logging.DEBUG if os.environ.get("Essence_DEBUG") else logging.INFO)
    h = logging.StreamHandler(sys.stderr)
    h.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S"))
    _log.addHandler(h)
    return _log

log: logging.Logger = _setup_logging()

class SystemRole(_enum.Enum):
    STANDALONE   = "standalone"
    ORCHESTRATOR = "orchestrator"
    WORKER       = "worker"

def get_system_role() -> SystemRole:
    if os.environ.get("Essence_ORCH_URL"):    return SystemRole.WORKER
    if os.environ.get("Essence_WORKER_URLS"): return SystemRole.ORCHESTRATOR
    return SystemRole.STANDALONE


async def _async_iter(items):
    """Tiny helper used by the test suite to build an async generator out of
    a plain iterable, for mocking `Provider.acomplete()`-style streaming
    return values."""
    for item in items:
        yield item

__all__ = [
    "argparse","asyncio","_dc","_enum","hashlib","json","logging","math",
    "os","platform","re","secrets","shlex","shutil","subprocess","sys",
    "textwrap","threading","time","Path","urllib",
    "Any","Callable","Iterator","AsyncIterator","TYPE_CHECKING",
    "BaseModel","ConfigDict","Field","field_validator","model_validator","SecretStr","_PYDANTIC",
    "_httpx","_HTTPX","_CRYPTO","_AESGCM","AESGCM","PBKDF2HMAC","_crypto_hashes",
    "_c","bold","dim","green","yellow","red","cyan","blue","_USE_COLOUR",
    "_setup_logging","log","compress_bundle","decompress_bundle",
    "Essence_VERSION","Essence_BUILD","MIN_PYTHON","GITHUB_REPO",
    "_OLLAMA_HOST","_VLLM_HOST","_MLX_HOST","_TEAM_ID","_COST_BUDGET",
    "_SOP_DIR","_EVAL_ON_START","_DRIFT_WEBHOOK","_VAULT_ALLOW_WEAK",
    "_MODEL_OVERRIDE","_BACKEND_OVERRIDE","_ALIGNMENT_FAILOPEN","_EXTRA_PROVIDERS_RAW",
    "SystemRole","get_system_role","_async_iter",
]
