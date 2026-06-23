"""ProviderRegistry: plug-and-play backend extension point."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403
from essence.backends.adapters import (  # noqa: F401
    LiteLLMBackend, BackendError, ProviderChain,
    OllamaBackend, MLXBackend, OpenAICompatBackend,
)
from essence.core.hardware import HardwareProfile  # noqa: F401

# PROVIDER REGISTRY  (plug-and-play backend extension point)
# ══════════════════════════════════════════════════════════════════════════════
# Register new inference backends at runtime — no source edits required.
#
#   from essence import PROVIDER_REGISTRY, OpenAICompatBackend
#   PROVIDER_REGISTRY.register("my_llm",
#       lambda: OpenAICompatBackend("http://my-server:8080", api_key="secret"))
#
#   # Or via env var:  Essence_EXTRA_PROVIDERS=myapi=http://host:port?key=KEY

class ProviderRegistry:
    """
    Plug-and-play backend registry.

    Supports three registration patterns:

    1. **Runtime factory** (code):
         PROVIDER_REGISTRY.register("my_llm", lambda: MyBackend(...))

    2. **Env-var string** (Essence_EXTRA_PROVIDERS):
         Parsed by build_provider_chain(); creates OpenAICompatBackend instances.

    3. **Role-aware delegation** (master/slave topology):
         build_provider_chain() automatically adds RemoteProviders when
         Essence_ROLE=master and peer slave URLs are discovered.
    """
    def __init__(self) -> None:
        self._factories: dict[str, Callable[[], Any]] = {}
        self._runtime_providers: list[Any] = []

    def register(self, name: str, factory: Callable[[], Any]) -> None:
        """Register a named provider factory.  Called at import time or lazily."""
        self._factories[name] = factory
        instance = factory()
        self._runtime_providers.append(instance)
        log.debug("provider_registered", extra={"name": name})

    def clear(self) -> None:
        """Remove all runtime-registered providers (useful in tests)."""
        self._factories.clear()
        self._runtime_providers.clear()

    def names(self) -> list[str]:
        return list(self._factories.keys())


PROVIDER_REGISTRY = ProviderRegistry()


def build_provider_chain(hw: HardwareProfile) -> ProviderChain:
    providers = []
    # T3: check SGLang first (faster speculative decode for MoE), then vLLM
    if hw.backend == "vllm" or hw.tier >= 3:
        sg = SGLangBackend()
        if sg.alive(): providers.append(sg)
        vb = OpenAICompatBackend(_VLLM_HOST)
        if vb.alive(): providers.append(vb)
    if hw.backend == "mlx" and hw.has_metal:
        mb = MLXBackend()
        if mb.alive(): providers.append(mb)
    if shutil.which("ollama"):
        ob = OllamaBackend()
        providers.append(ob)   # Ollama added even if not yet alive
    if shutil.which("llama-server"):
        providers.append(LlamaCppBackend())
    # In-process llama-cpp-python (T0 — no external binary required)
    _lcpp_model = os.environ.get("Essence_GGUF_PATH", "")
    if _lcpp_model and Path(_lcpp_model).exists():
        providers.append(LlamaCppPythonBackend(
            _lcpp_model,
            n_gpu_layers=-1 if hw.has_cuda or hw.has_metal else 0))
    # LiteLLM cloud gateway when installed (100+ providers, auto-retry)
    ll = LiteLLMBackend()
    if ll.alive():
        providers.append(ll)
    # Bare OpenAI-compat cloud fallback
    elif cloud_url := os.environ.get("Essence_CLOUD_URL", ""):
        providers.append(OpenAICompatBackend(
            cloud_url, os.environ.get("Essence_CLOUD_KEY", "sk-")))
    # Extra providers registered via Essence_EXTRA_PROVIDERS env var:
    #   name1=http://host:port[?key=MYKEY],name2=http://host2:port2
    if _EXTRA_PROVIDERS_RAW:
        for entry in _EXTRA_PROVIDERS_RAW.split(","):
            entry = entry.strip()
            if "=" not in entry:
                continue
            _ename, _eurl = entry.split("=", 1)
            _ekey = "sk-"
            if "?key=" in _eurl:
                _eurl, _ekey = _eurl.split("?key=", 1)
            providers.append(OpenAICompatBackend(_eurl.strip(), _ekey.strip()))
    # Providers registered at runtime via PROVIDER_REGISTRY
    for p in PROVIDER_REGISTRY._runtime_providers:
        providers.append(p)
    if not providers:
        raise BackendError("No backend binary found.\n  Run: essence install")
    return ProviderChain(providers)


# ══════════════════════════════════════════════════════════════════════════════
