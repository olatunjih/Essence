"""Ollama, LlamaCpp, OpenAI-compat, MLX, ONNX, SGLang, LiteLLM, ProviderChain."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403

# BACKEND ADAPTERS  (uniform streaming interface)
# ══════════════════════════════════════════════════════════════════════════════
# All backends expose:
#   complete(messages, *, model, stream, thinking, budget, tools) → Iterator[str]
#
# Provider chain: primary → fallback_1 → fallback_2 → cloud
# "Thinking tax" mitigation: thinking=True only for planning step
# (design principle: don't use full reasoning for every subtask)
#
# Qwen3 thinking:   Ollama ≥0.7 → payload["think"] = True
# Qwen3.5 thinking: vLLM/SGLang → extra_body chat_template_kwargs enable_thinking
# Nemotron3-Super:  extra_body reasoning_parser="super_v3"

from typing import Protocol, runtime_checkable
from essence.infra.conn import _HTTPX_POOL  # noqa: F401  [real source bug: used in _ping()/_ping_async() without import]
from essence.infra.circuit import CIRCUIT_BREAKERS  # noqa: F401  [real source bug: used in ProviderChain.complete() without import]
from essence.infra.otel import span_llm  # noqa: F401  [real source bug: used in ProviderChain.complete() without import]
@runtime_checkable
class InferenceProvider(Protocol):
    NAME: str
    def alive(self) -> bool: ...
    def complete(self, messages: list[dict], **kwargs) -> Iterator[str]: ...
    async def acomplete(self, messages: list[dict], **kwargs) -> AsyncIterator[str]: ...

class BackendError(RuntimeError): ...


def _json_post(url: str, payload: dict,
               timeout: int = 180) -> Iterator[bytes]:
    """v23: Uses httpx streaming pool when available; falls back to urllib."""
    if _HTTPX_POOL:
        client = get_sync_client()
        if client:
            try:
                with client.stream("POST", url, json=payload,
                                    timeout=timeout) as resp:
                    resp.raise_for_status()
                    for line in resp.iter_lines():
                        if line:
                            yield (line + "\n").encode()
                return
            except Exception as _he:
                # httpx failed — fall through to urllib
                log.debug("json_post_httpx_fallback", extra={"error": str(_he)[:80]})
    data = json.dumps(payload).encode()
    req  = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            while chunk := r.readline():
                yield chunk
    except urllib.error.URLError as e:
        raise BackendError(f"POST {url}: {e}") from e


async def _ajson_post(url: str, payload: dict,
                     timeout: int = 180) -> AsyncIterator[bytes]:
    """Async version of _json_post using httpx.AsyncClient."""
    if _HTTPX_POOL:
        client = get_async_client()
        if client:
            try:
                async with client.stream("POST", url, json=payload,
                                         timeout=timeout) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if line:
                            yield (line + "\n").encode()
                return
            except Exception as _he:
                log.debug("ajson_post_error", extra={"error": str(_he)[:80]})

    # Fallback: run sync version in thread
    loop = asyncio.get_running_loop()
    gen = _json_post(url, payload, timeout)
    while True:
        try:
            chunk = await loop.run_in_executor(None, next, gen)
            yield chunk
        except StopIteration:
            break


def _ping(url: str, t: int = 2) -> bool:
    """v23: Uses httpx pool when available for connection reuse."""
    if _HTTPX_POOL:
        try:
            client = get_sync_client()
            if client:
                resp = client.get(url, timeout=t)
                return resp.status_code < 500
        except Exception:
            pass
    try:
        urllib.request.urlopen(url, timeout=t)
        return True
    except Exception:
        return False


# ── Alive-check TTL cache (FIX: was per-token HTTP ping) ─────────────────────
_ALIVE_TTL   = float(os.environ.get("Essence_ALIVE_TTL", "10"))
_alive_cache: dict[str, tuple[bool, float]] = {}
_alive_lock  = threading.Lock()

def _ping_cached(url: str, t: int = 2,
                  circuit_name: str = "") -> bool:
    """Cached HTTP liveness probe — re-checks at most every Essence_ALIVE_TTL s.
    v23: Checks circuit breaker state before making the network call.
    If the circuit is OPEN the backend is assumed down (no ping needed)."""
    # Fast path: circuit is open — skip the ping entirely
    if circuit_name:
        try:
            cb = CIRCUIT_BREAKERS.get(circuit_name)
            if not cb.allow():
                return False   # circuit open; treat as down
        except Exception:
            pass
    now = time.monotonic()
    with _alive_lock:
        cached = _alive_cache.get(url)
        if cached and now < cached[1]:
            return cached[0]
    result = _ping(url, t)
    with _alive_lock:
        _alive_cache[url] = (result, now + _ALIVE_TTL)
    return result


# ── Ollama ────────────────────────────────────────────────────────────────────
class OllamaBackend:
    """
    Ollama v0.7+ — primary cross-platform backend.
    /api/chat · SSE streaming · think param · native tool calls.
    Docs: https://github.com/ollama/ollama/blob/main/docs/api.md
    """
    NAME = "ollama"

    def __init__(self, host: str = _OLLAMA_HOST):
        self.host  = host.rstrip("/")
        self._chat = f"{self.host}/api/chat"
        self._tags = f"{self.host}/api/tags"

    def alive(self) -> bool:
        return _ping_cached(self._tags, circuit_name="ollama")

    def list_models(self) -> list[str]:
        try:
            return [m["name"] for m in
                    http_get_json(self._tags, timeout=5).get("models", [])]
        except Exception:
            return []

    def pull(self, tag: str) -> None:
        if not any(tag in t for t in self.list_models()):
            print(f"  {yellow('Pulling')} {tag} …")
            subprocess.run(["ollama", "pull", tag], check=True, timeout=600)

    def complete(self, messages: list[dict], *, model: str,
                 stream: bool = True, thinking: bool = False,
                 budget: int = 1024,
                 tools: list[dict] | None = None) -> Iterator[str]:
        payload: dict[str, Any] = {
            "model": model, "messages": messages, "stream": stream,
            "options": {"num_ctx": 32768,
                        "temperature": 0.6 if thinking else 0.7},
        }
        if thinking:
            payload["think"] = True   # Ollama ≥0.7 native think param
        if tools:
            payload["tools"] = tools
        for raw in _json_post(self._chat, payload):
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                chunk = json.loads(line)
                # Skip internal <think> tokens surfaced by Ollama 0.7+
                if chunk.get("message", {}).get("role") == "thinking":
                    continue
                if tok := chunk.get("message", {}).get("content", ""):
                    yield tok
                if chunk.get("done"):
                    break
            except json.JSONDecodeError:
                continue

    async def acomplete(self, messages: list[dict], *, model: str,
                        stream: bool = True, thinking: bool = False,
                        budget: int = 1024,
                        tools: list[dict] | None = None) -> AsyncIterator[str]:
        payload: dict[str, Any] = {
            "model": model, "messages": messages, "stream": stream,
            "options": {"num_ctx": 32768,
                        "temperature": 0.6 if thinking else 0.7},
        }
        if thinking:
            payload["think"] = True
        if tools:
            payload["tools"] = tools
        async for raw in _ajson_post(self._chat, payload):
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                chunk = json.loads(line)
                if chunk.get("message", {}).get("role") == "thinking":
                    continue
                if tok := chunk.get("message", {}).get("content", ""):
                    yield tok
                if chunk.get("done"):
                    break
            except json.JSONDecodeError:
                continue


# ── llama-server (T0 / offline fallback) ──────────────────────────────────────
class LlamaCppBackend:
    """
    Wraps llama-server subprocess. T0 IoT / zero-install fallback.
    Karpathy principle: expose every launch parameter; no hidden defaults.
    """
    NAME = "llamacpp"

    def __init__(self, host: str = "127.0.0.1", port: int = 8081):
        self.host = host; self.port = port
        self.base = f"http://{host}:{port}"
        self._proc: subprocess.Popen | None = None

    def alive(self) -> bool:
        return _ping_cached(f"{self.base}/health")

    def start(self, model_path: str, *, n_gpu: int = 0,
              ctx: int = 4096, threads: int = 4) -> None:
        if self._proc and self._proc.poll() is None:
            return
        exe = shutil.which("llama-server") or shutil.which("llama.cpp")
        if not exe:
            raise BackendError("llama-server not found. "
                               "Build: https://github.com/ggerganov/llama.cpp")
        self._proc = subprocess.Popen(
            [exe, "-m", model_path,
             "--host", self.host, "--port", str(self.port),
             "--ctx-size", str(ctx), "--threads", str(threads),
             "--n-gpu-layers", str(n_gpu), "--mlock"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(40):
            if self.alive(): return
            time.sleep(0.5)
        # Kill the orphaned process before raising so it does not linger
        try:
            self._proc.kill()
        except Exception as _exc:
            log.debug('llamacpp_kill_failed', extra={'error': str(_exc)})
        self._proc = None
        raise BackendError("llama-server did not start in 20s")

    def complete(self, messages: list[dict], *, model: str = "",
                 stream: bool = True, thinking: bool = False,
                 budget: int = 1024,
                 tools: list[dict] | None = None) -> Iterator[str]:
        prompt = "\n".join(f"<|{m['role']}|>\n{m.get('content', '')}" for m in messages)
        for raw in _json_post(f"{self.base}/completion",
                              {"prompt": prompt, "n_predict": 2048,
                               "temperature": 0.6 if thinking else 0.7,
                               "stream": stream}):
            line = raw.decode("utf-8", errors="replace").strip()
            if line.startswith("data: "):
                try:
                    chunk = json.loads(line[6:])
                    if tok := chunk.get("content", ""):
                        yield tok
                    if chunk.get("stop"):
                        break
                except json.JSONDecodeError:
                    continue


# ── OpenAI-compat (vLLM, SGLang, TGI, OpenRouter, cloud) ──────────────────────
class OpenAICompatBackend:
    """
    Uniform adapter for any OpenAI /v1/chat/completions endpoint.
    vLLM ≥0.7 + PagedAttention recommended for T3.
    SGLang ≥0.4 recommended for multi-agent parallel tool calls.
    Nemotron 3 Super: reasoning_parser="super_v3" via extra_body.
    """
    NAME = "openai_compat"

    def __init__(self, base: str = _VLLM_HOST, api_key: str = "EMPTY"):
        self.base    = base.rstrip("/")
        self.api_key = SecretStr(api_key) if _PYDANTIC else api_key  # type: ignore

    def alive(self) -> bool:
        # vLLM exposes /health; SGLang/TGI/OpenRouter may only expose /v1/models
        return _ping(f"{self.base}/health") or _ping(f"{self.base}/v1/models")

    def complete(self, messages: list[dict], *, model: str,
                 stream: bool = True, thinking: bool = False,
                 budget: int = 1024,
                 tools: list[dict] | None = None) -> Iterator[str]:
        payload: dict[str, Any] = {
            "model": model, "messages": messages,
            "stream": stream, "max_tokens": 4096,
            "temperature": 0.6 if thinking else 0.7,
        }
        if thinking:
            # Qwen3.5 via vLLM/SGLang — does NOT support /think soft-switch
            payload["extra_body"] = {
                "chat_template_kwargs": {"enable_thinking": True},
                "thinking_budget": budget,
            }
        if "nemotron" in model.lower():
            payload.setdefault("extra_body", {})
            payload["extra_body"]["reasoning_parser"] = "super_v3"
        if tools:
            payload["tools"] = tools

        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key.get_secret_value() if hasattr(self.api_key, 'get_secret_value') else self.api_key}",
        }

        # Anthropic prompt caching: inject cache_control on the system message.
        # Reduces cost by 60–90% on long sessions (cache hit = 0.1× input price).
        # Only applied when talking directly to api.anthropic.com.
        if "anthropic.com" in self.base:
            headers["anthropic-beta"] = "prompt-caching-2024-07-31"
            # Mark the first system message as cacheable
            for msg in payload["messages"]:
                if msg.get("role") == "system":
                    content = msg.get("content", "")
                    if isinstance(content, str):
                        msg["content"] = [
                            {"type": "text", "text": content,
                             "cache_control": {"type": "ephemeral"}}
                        ]
                    elif isinstance(content, list) and content:
                        content[-1]["cache_control"] = {"type": "ephemeral"}
                    break

        data = json.dumps(payload).encode()
        req  = urllib.request.Request(
            f"{self.base}/v1/chat/completions", data=data,
            headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                for raw in resp:
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line or line == "data: [DONE]":
                        continue
                    if line.startswith("data: "):
                        try:
                            tok = (json.loads(line[6:])["choices"][0]
                                   .get("delta", {}).get("content", ""))
                            if tok:
                                yield tok
                        except (json.JSONDecodeError, KeyError, IndexError):
                            continue
        except urllib.error.URLError as e:
            raise BackendError(str(e)) from e

    async def acomplete(self, messages: list[dict], *, model: str,
                        stream: bool = True, thinking: bool = False,
                        budget: int = 1024,
                        tools: list[dict] | None = None) -> AsyncIterator[str]:
        payload: dict[str, Any] = {
            "model": model, "messages": messages,
            "stream": stream, "max_tokens": 4096,
            "temperature": 0.6 if thinking else 0.7,
        }
        if thinking:
            payload["extra_body"] = {
                "chat_template_kwargs": {"enable_thinking": True},
                "thinking_budget": budget,
            }
        if "nemotron" in model.lower():
            payload.setdefault("extra_body", {})
            payload["extra_body"]["reasoning_parser"] = "super_v3"
        if tools:
            payload["tools"] = tools

        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key.get_secret_value() if hasattr(self.api_key, 'get_secret_value') else self.api_key}",
        }

        if "anthropic.com" in self.base:
            headers["anthropic-beta"] = "prompt-caching-2024-07-31"
            for msg in payload["messages"]:
                if msg.get("role") == "system":
                    content = msg.get("content", "")
                    if isinstance(content, str):
                        msg["content"] = [
                            {"type": "text", "text": content,
                             "cache_control": {"type": "ephemeral"}}
                        ]
                    elif isinstance(content, list) and content:
                        content[-1]["cache_control"] = {"type": "ephemeral"}
                    break

        url = f"{self.base}/v1/chat/completions"
        async for raw in _ajson_post(url, payload):
            line = raw.decode("utf-8", errors="replace").strip()
            if not line or line == "data: [DONE]":
                continue
            if line.startswith("data: "):
                try:
                    tok = (json.loads(line[6:])["choices"][0]
                            .get("delta", {}).get("content", ""))
                    if tok:
                        yield tok
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue


# ── MLX (Apple Silicon) ────────────────────────────────────────────────────────
class MLXBackend(OpenAICompatBackend):
    """
    mlx_lm.server subprocess wrapper.
    4–10× faster than Ollama on M-series due to unified memory architecture.
    """
    NAME = "mlx"

    def __init__(self, host: str = "127.0.0.1", port: int = 8082):
        super().__init__(base=f"http://{host}:{port}", api_key="mlx")
        self._port_n = port
        self._proc: subprocess.Popen | None = None

    def start(self, model_hf: str) -> None:
        if self._proc and self._proc.poll() is None:
            return
        self._proc = subprocess.Popen(
            [sys.executable, "-m", "mlx_lm.server",
             "--model", model_hf,
             "--host", "127.0.0.1", "--port", str(self._port_n)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(40):
            if self.alive(): return
            time.sleep(0.5)
        raise BackendError("mlx_lm.server did not start in 20s")


# ── llama-cpp-python in-process backend (T0 — no external binary required) ───────
class LlamaCppPythonBackend:
    """
    Runs llama_cpp.Llama directly inside the Essence process.
    T0 path: Raspberry Pi, Pi-class SBC 2W, any CPU-only device.
    n_gpu_layers=-1 offloads everything when a GPU is present.
    """
    NAME = "llamacpp_python"

    def __init__(self, model_path: str, n_gpu_layers: int = 0,
                 n_ctx: int = 4096):
        self.model_path   = model_path
        self.n_gpu_layers = n_gpu_layers
        self.n_ctx        = n_ctx
        self._llama       = None

    def _load(self):
        if self._llama is not None:
            return
        try:
            from llama_cpp import Llama  # type: ignore
            self._llama = Llama(
                model_path=self.model_path,
                n_gpu_layers=self.n_gpu_layers,
                n_ctx=self.n_ctx,
                verbose=False,
            )
        except ImportError:
            raise BackendError(
                "llama-cpp-python not installed. "
                "Run: pip install llama-cpp-python")

    def alive(self) -> bool:
        return bool(self.model_path and Path(self.model_path).exists())

    def complete(self, messages: list[dict], *, model: str = "",
                 stream: bool = True, thinking: bool = False,
                 budget: int = 1024,
                 tools: list[dict] | None = None) -> Iterator[str]:
        self._load()
        prompt = "\n".join(f"<|{m['role']}|>\n{m.get('content','')}" for m in messages)
        output = self._llama(prompt, max_tokens=2048,
                             temperature=0.6 if thinking else 0.7,
                             stream=True)
        for chunk in output:
            tok = chunk["choices"][0].get("text", "")
            if tok:
                yield tok

class OnnxBackend:
    """
    ONNX Runtime inference — ultra-light, no native CUDA deps.
    Suitable for Alpine Linux, Pi-class SBC 2W with Python 3.9+.
    Model must be exported to ONNX format (Optimum library).
    """
    NAME = "onnx"

    def __init__(self, model_path: str):
        self.model_path = model_path
        self._pipeline  = None

    def alive(self) -> bool:
        return bool(self.model_path and Path(self.model_path).exists())

    def _load(self):
        if self._pipeline is not None:
            return
        try:
            from optimum.onnxruntime import ORTModelForCausalLM  # type: ignore
            from transformers import AutoTokenizer, pipeline      # type: ignore
            model = ORTModelForCausalLM.from_pretrained(
                self.model_path, provider="CPUExecutionProvider")
            tok = AutoTokenizer.from_pretrained(self.model_path)
            self._pipeline = pipeline("text-generation", model=model,
                                      tokenizer=tok)
        except ImportError:
            raise BackendError(
                "optimum[onnxruntime] not installed. "
                "Run: pip install optimum[onnxruntime]")

    def complete(self, messages: list[dict], *, model: str = "",
                 stream: bool = True, thinking: bool = False,
                 budget: int = 1024,
                 tools: list[dict] | None = None) -> Iterator[str]:
        self._load()
        prompt = "\n".join(f"<|{m['role']}|>\n{m.get('content', '')}" for m in messages)
        result = self._pipeline(prompt, max_new_tokens=512,
                                do_sample=True, temperature=0.7)
        text = result[0]["generated_text"][len(prompt):]
        yield text


# ── SGLang backend (T3 — speculative decode for Qwen3-MoE) ───────────────────────
class SGLangBackend(OpenAICompatBackend):
    """
    SGLang ≥0.4 speculative decode is meaningfully faster for Qwen3-MoE
    workloads vs vLLM at T3.  Uses the same OpenAI-compat /v1/chat/completions.
    """
    NAME = "sglang"

    def __init__(self, host: str = "127.0.0.1", port: int = 30000):
        super().__init__(base=f"http://{host}:{port}", api_key="EMPTY")

    def alive(self) -> bool:
        return _ping_cached(f"{self.base}/health")


# ── LiteLLM cloud gateway (100+ providers through one interface) ─────────────────
class LiteLLMBackend:
    """
    When litellm is installed, replaces bare OpenAICompatBackend(cloud_url).
    Gives automatic retry, cost tracking, model fallback across:
    Anthropic, Gemini, Bedrock, Azure, Groq, Together, and 90+ more.
    """
    NAME = "litellm"

    def __init__(self, model: str = "", api_key: str = ""):
        self._model   = model or os.environ.get("Essence_LITELLM_MODEL", "gpt-4o")
        self._api_key = SecretStr(api_key) if (api_key and _PYDANTIC) else api_key  # type: ignore

    def alive(self) -> bool:
        try:
            import litellm  # type: ignore  # noqa: F401
            # Only consider alive if a cloud model or API key is configured
            has_key = bool(
                os.environ.get("OPENAI_API_KEY") or
                os.environ.get("ANTHROPIC_API_KEY") or
                os.environ.get("GEMINI_API_KEY") or
                os.environ.get("GROQ_API_KEY") or
                os.environ.get("Essence_LITELLM_MODEL")
            )
            return has_key
        except ImportError:
            return False

    def complete(self, messages: list[dict], *, model: str = "",
                 stream: bool = True, thinking: bool = False,
                 budget: int = 1024,
                 tools: list[dict] | None = None) -> Iterator[str]:
        try:
            import litellm  # type: ignore
        except ImportError:
            raise BackendError("litellm not installed. Run: pip install litellm")
        chosen = model or self._model
        kwargs: dict[str, Any] = dict(
            model=chosen, messages=messages, stream=True,
            max_tokens=4096, temperature=0.6 if thinking else 0.7,
        )
        if tools:
            kwargs["tools"] = tools
        response = litellm.completion(**kwargs)
        for chunk in response:
            tok = (chunk.choices[0].delta.content or "")
            if tok:
                yield tok


# ── Provider chain ──────────────────────────────────────────────────────────────
class ProviderChain:
    """
    primary → fallback_1 → fallback_2 → cloud.
    Per-agent override via AGENTS.md [provider] directive.
    First alive backend wins — transparent to the caller.
    """
    def __init__(self, providers: list):
        self.providers = providers

    @property
    def active(self) -> InferenceProvider:
        """Return the first alive provider.
        Return first provider as fallback if none are alive — callers get a
        descriptive error from the provider itself rather than a crash here.
        """
        for p in self.providers:
            try:
                if p.alive():
                    return p
            except Exception:
                pass
        # Return first provider as fallback (its complete() will error descriptively)
        if self.providers:
            log.warning("provider_chain_all_dead",
                        extra={"providers": [p.NAME for p in self.providers]})
            return self.providers[0]
        raise BackendError(
            "ProviderChain: empty — run: essence install && essence up")

    def alive(self) -> bool:
        return any(p.alive() for p in self.providers)

    def complete(self, *a, **kw) -> Iterator[str]:
        """True streaming with jittered backoff fallback across providers.
        Yields tokens as they arrive — no full materialisation."""
        import random as _rnd
        errors: list[str] = []
        for provider in self.providers:
            if not provider.alive():
                continue
            # v22: Circuit breaker per provider
            _pname = getattr(provider, "NAME", type(provider).__name__)
            _cb    = CIRCUIT_BREAKERS.get(_pname)
            if not _cb.allow():
                log.debug("circuit_open_skip", extra={"provider": _pname})
                continue
            for attempt in range(2):
                try:
                    # v27: Wrap in OTEL span for distributed tracing
                    _model = kw.get("model", "") or (a[1] if len(a) > 1 else "")
                    with span_llm(_model or _pname):
                        def _safe(_g=provider.complete(*a, **kw)):
                            try:
                                yield from _g
                            except BackendError:
                                raise
                            except Exception as _e:
                                raise BackendError(str(_e)) from _e
                        yield from _safe()
                    _cb.record_success()
                    return
                except BackendError as exc:
                    _cb.record_failure()
                    errors.append(f"{_pname}[{attempt}]: {exc}")
                    log.warning("backend_retry", extra={
                        "provider": _pname,
                        "attempt": attempt, "error": str(exc)})
                    if attempt == 0:
                        time.sleep(2.0 + _rnd.uniform(0, 1.0))
        raise BackendError("All providers failed.\n" + "\n".join(f"  {e}" for e in errors))

    async def acomplete(self, *a, **kw) -> AsyncIterator[str]:
        """Async version of complete(). Yields tokens asynchronously."""
        import random as _rnd
        errors: list[str] = []
        for provider in self.providers:
            # alive() is still sync for now as it's cached/fast
            if not provider.alive():
                continue
            _pname = getattr(provider, "NAME", type(provider).__name__)
            _cb    = CIRCUIT_BREAKERS.get(_pname)
            if not _cb.allow():
                continue

            # Check if provider has acomplete, else fall back to thread
            _acomplete = getattr(provider, "acomplete", None)

            for attempt in range(2):
                try:
                    _model = kw.get("model", "") or (a[1] if len(a) > 1 else "")
                    with span_llm(_model or _pname):
                        if _acomplete:
                            async for tok in _acomplete(*a, **kw):
                                yield tok
                        else:
                            # Fallback: run sync generator in thread
                            loop = asyncio.get_running_loop()
                            gen = provider.complete(*a, **kw)
                            while True:
                                try:
                                    tok = await loop.run_in_executor(None, next, gen)
                                    yield tok
                                except StopIteration:
                                    break
                                except Exception as _e:
                                    raise BackendError(str(_e)) from _e
                    _cb.record_success()
                    return
                except BackendError as exc:
                    _cb.record_failure()
                    errors.append(f"{_pname}[{attempt}]: {exc}")
                    if attempt == 0:
                        await asyncio.sleep(2.0 + _rnd.uniform(0, 1.0))
        raise BackendError("All providers failed.\n" + "\n".join(f"  {e}" for e in errors))




# ══════════════════════════════════════════════════════════════════════════════
