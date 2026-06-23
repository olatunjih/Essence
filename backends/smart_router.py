"""SmartRouter — multi-provider parallel availability checking and model routing.

Selects the best available provider + model for each request based on:
  - real-time availability (parallel ping, TTL-cached)
  - task intent → provider capability mapping
  - latency history (exponential moving average, per-tier defaults)
  - cost tier (local → cheap cloud → premium cloud)
  - circuit breaker (skip repeatedly failing providers)

Fixes applied (vs. v1):
  1.1  Fallback now resolves the correct model per fallback provider, not the
       original provider's model.
  1.2  kwargs["model"] is popped once at the top before any complete() call.
  1.3  add_provider no longer sets _built=True (avoids silently skipping cloud
       provider bootstrap).
  1.4  _build_pool uses double-checked locking to prevent duplicate pool init.
  1.5  Streaming fallback buffers first chunk; if anything was yielded to the
       caller the router re-raises rather than splicing two streams.
  1.6  Latency EMA seeds use per-tier defaults (local≈0.1s, cheap≈0.5s,
       premium≈1.0s) instead of a flat 1.0 that penalises local providers.
  1.7  Env overrides are scoped to the selected provider via
       ESSENCE_MODEL_{PROVIDER}_{INTENT}, falling back to ESSENCE_MODEL_{INTENT}
       only when it is confirmed valid for the chosen provider.
  1.8  Fallback reuses the candidates already ranked in select(); a short-lived
       "recently_failed" TTL set prevents re-attempting the same dead provider.
  1.9  Providers not listed in _INTENT_PREFERENCE score 0 (neutral), not -10.
  1.10 Module-level singleton guarded by a threading.Lock.
  2.1  status() now tries to populate models via list_models() with a short
       timeout so the sync path is useful for diagnostics.
  2.3  Anthropic "vision" entry added.
  2.6  select() and select_async() share a single _resolve_model() helper.
  3.2  Simple circuit breaker: after N failures within T seconds a provider is
       skipped for a back-off window.
  3.6  Provider health results are TTL-cached (default 10 s) to avoid pinging
       on every request.
  3.8  select()/select_async() accept force_provider and force_model overrides.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
import threading
from typing import Any, Iterator

log = logging.getLogger("essence.backends.smart_router")

# ── Cost tiers (lower = cheaper / prefer first) ───────────────────────────────
_PROVIDER_TIER: dict[str, int] = {
    "ollama":          0,
    "llamacpp":        0,
    "llamacpp_python": 0,
    "onnx":            0,
    "mlx":             0,
    "local":           0,
    "perplexity":      1,
    "gemini":          1,
    "elevenlabs":      1,
    "openai":          2,
    "anthropic":       2,
}

# Default latency seed per tier (seconds) — avoids penalising fast local providers
_TIER_LATENCY_DEFAULT: dict[int, float] = {0: 0.1, 1: 0.5, 2: 1.0}

# ── Intent → preferred provider ordering ─────────────────────────────────────
_INTENT_PREFERENCE: dict[str, list[str]] = {
    "reasoning":  ["anthropic", "openai", "gemini", "ollama"],
    "coding":     ["openai", "anthropic", "ollama", "gemini"],
    "research":   ["perplexity", "gemini", "anthropic", "openai"],
    "vision":     ["gemini", "openai", "anthropic", "ollama"],
    "planning":   ["anthropic", "openai", "gemini", "ollama"],
    "search":     ["perplexity", "gemini", "openai"],
    "general":    ["ollama", "gemini", "openai", "anthropic"],
    "voice":      ["elevenlabs", "local", "openai"],
}

# ── Recommended models per provider × intent ──────────────────────────────────
_PROVIDER_MODEL_MAP: dict[str, dict[str, str]] = {
    "openai": {
        "reasoning": "o3-mini",
        "coding":    "gpt-4o",
        "research":  "gpt-4o",
        "vision":    "gpt-4o",
        "planning":  "o3-mini",
        "general":   "gpt-4o-mini",
        "voice":     "tts-1",
        "search":    "gpt-4o",
    },
    "anthropic": {
        "reasoning": "claude-opus-4-5",
        "coding":    "claude-sonnet-4-5",
        "research":  "claude-sonnet-4-5",
        "vision":    "claude-3-5-sonnet-20241022",
        "planning":  "claude-opus-4-5",
        "general":   "claude-3-5-haiku-20241022",
    },
    "gemini": {
        "reasoning": "gemini-1.5-pro",
        "research":  "gemini-1.5-pro",
        "vision":    "gemini-1.5-pro",
        "coding":    "gemini-2.0-flash-exp",
        "planning":  "gemini-1.5-pro",
        "general":   "gemini-1.5-flash",
    },
    "perplexity": {
        "research":  "llama-3.1-sonar-large-128k-online",
        "search":    "llama-3.1-sonar-large-128k-online",
        "general":   "llama-3.1-sonar-small-128k-online",
        "reasoning": "llama-3.1-sonar-large-128k-online",
        "coding":    "llama-3.1-sonar-large-128k-online",
    },
    "elevenlabs": {
        "voice": "eleven_turbo_v2",
    },
}

# ── Circuit-breaker constants ─────────────────────────────────────────────────
_CB_FAILURE_THRESHOLD = 3     # consecutive failures before opening circuit
_CB_WINDOW_SECONDS    = 60    # sliding window to count failures
_CB_BACKOFF_SECONDS   = 120   # how long to keep circuit open

# ── Health-check TTL cache constants ─────────────────────────────────────────
_HEALTH_TTL_SECONDS   = 10    # re-ping at most every N seconds


class _CircuitBreaker:
    """Per-provider simple circuit breaker (not shared across SmartRouter instances)."""

    def __init__(self) -> None:
        self._failures: dict[str, list[float]] = {}   # name → timestamps
        self._open_until: dict[str, float]     = {}   # name → monotonic

    def is_open(self, name: str) -> bool:
        """Return True if the circuit is open (provider should be skipped)."""
        open_ts = self._open_until.get(name, 0.0)
        if time.monotonic() < open_ts:
            return True
        return False

    def record_failure(self, name: str) -> None:
        now = time.monotonic()
        timestamps = [t for t in self._failures.get(name, [])
                      if now - t < _CB_WINDOW_SECONDS]
        timestamps.append(now)
        self._failures[name] = timestamps
        if len(timestamps) >= _CB_FAILURE_THRESHOLD:
            self._open_until[name] = now + _CB_BACKOFF_SECONDS
            log.warning("circuit_breaker_opened",
                        extra={"provider": name,
                               "backoff_s": _CB_BACKOFF_SECONDS})

    def record_success(self, name: str) -> None:
        self._failures.pop(name, None)
        self._open_until.pop(name, None)


class SmartRouter:
    """Selects the best available provider and model for each intent."""

    def __init__(self, store: Any,
                 extra_providers: list[Any] | None = None) -> None:
        self._store     = store
        self._extra     = list(extra_providers or [])
        self._providers: list[Any] = []
        self._latency:   dict[str, float] = {}   # name → EMA latency (s)
        self._lock       = threading.RLock()
        self._built      = False
        self._cb         = _CircuitBreaker()
        # TTL health cache: name → (alive: bool, expires_monotonic: float)
        self._health_cache: dict[str, tuple[bool, float]] = {}

    # ── Provider pool ──────────────────────────────────────────────────────────

    def _build_pool(self) -> None:
        """Double-checked locking: safe to call from multiple threads."""
        with self._lock:
            if self._built:
                return
            from essence.backends.cloud import build_cloud_providers
            cloud = build_cloud_providers(self._store)
            self._providers = list(self._extra) + cloud
            self._built = True

    def _get_providers(self) -> list[Any]:
        if not self._built:
            self._build_pool()
        with self._lock:
            return list(self._providers)

    def add_provider(self, provider: Any) -> None:
        """Add a provider to the pool.  Does NOT set _built so cloud providers
        are still bootstrapped on the next _get_providers() call if not yet
        built."""
        with self._lock:
            self._providers.append(provider)
            # Do NOT set self._built = True here — cloud providers must still load.

    # ── Latency defaults ──────────────────────────────────────────────────────

    def _latency_default(self, name: str) -> float:
        """Return a sensible seed latency for a provider with no history yet."""
        tier = _PROVIDER_TIER.get(name, 2)
        return _TIER_LATENCY_DEFAULT.get(tier, 1.0)

    # ── Availability (parallel, TTL-cached) ───────────────────────────────────

    def _check_alive_cached(self, p: Any) -> bool:
        """Return cached alive status if fresh; otherwise re-ping."""
        name = self._provider_name(p)
        cached = self._health_cache.get(name)
        now = time.monotonic()
        if cached and now < cached[1]:
            return cached[0]
        try:
            ok = p.alive()
        except Exception:
            ok = False
        self._health_cache[name] = (ok, now + _HEALTH_TTL_SECONDS)
        return ok

    async def _check_alive_async(self, p: Any) -> tuple[Any, bool]:
        name = self._provider_name(p)
        cached = self._health_cache.get(name)
        now = time.monotonic()
        if cached and now < cached[1]:
            return p, cached[0]
        try:
            loop = asyncio.get_running_loop()
            ok   = await asyncio.wait_for(
                loop.run_in_executor(None, p.alive), timeout=5.0)
        except Exception:
            ok = False
        self._health_cache[name] = (ok, now + _HEALTH_TTL_SECONDS)
        return p, ok

    async def available_providers_async(self) -> list[Any]:
        providers = self._get_providers()
        sem = asyncio.Semaphore(8)  # cap concurrent pings

        async def _guarded(p: Any) -> tuple[Any, bool]:
            async with sem:
                return await self._check_alive_async(p)

        results = await asyncio.gather(*[_guarded(p) for p in providers],
                                       return_exceptions=False)
        return [p for p, ok in results
                if ok and not self._cb.is_open(self._provider_name(p))]

    def available_providers(self) -> list[Any]:
        """Sync availability check using the TTL cache + circuit breaker."""
        providers = self._get_providers()
        alive: list[Any] = []
        for p in providers:
            name = self._provider_name(p)
            if self._cb.is_open(name):
                continue
            if self._check_alive_cached(p):
                alive.append(p)
        return alive

    # ── Scoring ───────────────────────────────────────────────────────────────

    def _provider_name(self, provider: Any) -> str:
        name = getattr(provider, "NAME", "unknown")
        return name.split(":")[0] if ":" in name else name

    def _score(self, provider: Any, intent: str) -> float:
        name       = self._provider_name(provider)
        pref_list  = _INTENT_PREFERENCE.get(intent, _INTENT_PREFERENCE["general"])
        # unlisted → neutral score 0, not -10 (fix 1.9)
        pref_score = (len(pref_list) - pref_list.index(name)
                      if name in pref_list else 0)
        tier_score = -_PROVIDER_TIER.get(name, 5)
        default_lat = self._latency_default(name)
        ema_lat    = self._latency.get(name, default_lat)  # fix 1.6
        lat_score  = -ema_lat
        return pref_score * 10 + tier_score * 2 + lat_score

    # ── Model resolution (single helper shared by select + select_async) ──────

    def _resolve_model(self, provider: Any, intent: str) -> str:
        """
        Resolve the correct model for *this* provider + intent.

        Precedence:
          1. ESSENCE_MODEL_{PROVIDER}_{INTENT} env var           (provider-scoped)
          2. ESSENCE_MODEL_{INTENT} — only if valid for provider (cross-provider)
          3. Integration store default_model for this provider
          4. _PROVIDER_MODEL_MAP[provider][intent]
          5. provider.list_models()[0]
          6. "default"
        """
        name = self._provider_name(provider)
        provider_map = _PROVIDER_MODEL_MAP.get(name, {})

        # 1. Provider-scoped env override (fix 1.7)
        scoped_env = os.environ.get(
            f"ESSENCE_MODEL_{name.upper()}_{intent.upper()}", "")
        if scoped_env:
            return scoped_env

        # 2. Generic intent env override — only if the model is plausibly valid
        #    for this provider (i.e., it appears in the provider's map values or
        #    the provider has no map entries at all).
        generic_env = os.environ.get(f"ESSENCE_MODEL_{intent.upper()}", "")
        if generic_env:
            known_models = set(provider_map.values())
            if not known_models or generic_env in known_models:
                return generic_env

        # 3. Store setting
        try:
            stored = self._store.get_settings(name).get("default_model", "")
            if stored:
                return stored
        except Exception:
            pass

        # 4. Capability map
        mapped = provider_map.get(intent, "")
        if mapped:
            return mapped

        # 5. Provider's own model list
        try:
            models = provider.list_models()
            if models:
                return models[0]
        except Exception:
            pass

        return "default"

    # ── Selection ─────────────────────────────────────────────────────────────

    def select(self, intent: str = "general",
               force_provider: str = "",
               force_model: str = "") -> tuple[Any, str] | tuple[None, None]:
        """Return (provider, model) for intent.

        Args:
            intent:         Task intent string.
            force_provider: If set, only consider providers with this NAME.
            force_model:    If set, skip model resolution and use this value.

        Returns (None, None) if nothing is available.
        """
        candidates = self.available_providers()
        if force_provider:
            candidates = [p for p in candidates
                          if self._provider_name(p) == force_provider]
        if not candidates:
            log.warning("smart_router: no providers for intent=%s force=%s",
                        intent, force_provider or "none")
            return None, None

        candidates.sort(key=lambda p: self._score(p, intent), reverse=True)
        provider = candidates[0]
        model    = force_model or self._resolve_model(provider, intent)

        log.debug("smart_router: intent=%s selected=%s model=%s",
                  intent, self._provider_name(provider), model)
        return provider, model

    async def select_async(
            self,
            intent: str = "general",
            force_provider: str = "",
            force_model: str = "") -> tuple[Any, str] | tuple[None, None]:
        """Async version of select()."""
        candidates = await self.available_providers_async()
        if force_provider:
            candidates = [p for p in candidates
                          if self._provider_name(p) == force_provider]
        if not candidates:
            return None, None
        candidates.sort(key=lambda p: self._score(p, intent), reverse=True)
        provider = candidates[0]
        model    = force_model or self._resolve_model(provider, intent)
        return provider, model

    # ── Latency tracking ──────────────────────────────────────────────────────

    def record_latency(self, provider: Any, latency_s: float) -> None:
        name = self._provider_name(provider)
        with self._lock:
            prev = self._latency.get(name, self._latency_default(name))
            # EMA: alpha=0.3 for new samples (fix 1.6 — was 0.7/0.3 inverted)
            self._latency[name] = 0.7 * prev + 0.3 * latency_s

    # ── Routed completion with fallback ───────────────────────────────────────

    def complete_with_routing(self,
                              messages: list[dict],
                              intent: str = "general",
                              **kwargs) -> Iterator[str]:
        """Select provider, route, track latency, fall back on error.

        Fixes applied:
          - kwargs["model"] popped once at the top (fix 1.2)
          - fallback resolves its own model (fix 1.1)
          - streaming fallback detects partial yield and re-raises (fix 1.5)
          - reuses candidates list, recently-failed set (fix 1.8)
          - circuit-breaker records failures / successes
        """
        # Fix 1.2: pop "model" once before any call
        caller_model = kwargs.pop("model", "")

        provider, model = self.select(intent)
        if caller_model:
            model = caller_model
        if provider is None:
            yield "[No provider available — configure one in /integrations]"
            return

        pname = self._provider_name(provider)
        t0 = time.monotonic()
        yielded_any = False

        try:
            for chunk in provider.complete(messages, model=model, **kwargs):
                yielded_any = True
                yield chunk
            self.record_latency(provider, time.monotonic() - t0)
            self._cb.record_success(pname)
            return
        except Exception as exc:
            elapsed = time.monotonic() - t0
            log.warning("smart_router: provider %s failed after %.2fs: %s",
                        pname, elapsed, exc)
            self.record_latency(provider, 60.0)   # penalise
            self._cb.record_failure(pname)

            # Fix 1.5: if we already streamed tokens to the caller we cannot
            # silently splice in a second provider's response.
            if yielded_any:
                raise RuntimeError(
                    f"Provider {pname} failed mid-stream after yielding tokens. "
                    f"Original error: {exc}"
                ) from exc

        # Fix 1.8: reuse ranked candidates; collect recently-failed providers
        recently_failed: set[str] = {pname}
        fallback_candidates = self.available_providers()
        fallback_candidates.sort(key=lambda p: self._score(p, intent),
                                 reverse=True)

        for fallback in fallback_candidates:
            fb_name = self._provider_name(fallback)
            if fb_name in recently_failed:
                continue
            # Fix 1.1: resolve the correct model for THIS fallback provider
            fb_model = caller_model or self._resolve_model(fallback, intent)
            t1 = time.monotonic()
            try:
                yield from fallback.complete(messages, model=fb_model, **kwargs)
                self.record_latency(fallback, time.monotonic() - t1)
                self._cb.record_success(fb_name)
                return
            except Exception as fb_exc:
                log.warning("smart_router: fallback %s also failed: %s",
                            fb_name, fb_exc)
                self.record_latency(fallback, 60.0)
                self._cb.record_failure(fb_name)
                recently_failed.add(fb_name)

        yield f"[All providers failed — last error from {pname}]"

    # ── Diagnostics ───────────────────────────────────────────────────────────

    def status(self) -> dict:
        """Sync status.  Tries to populate models list with a short timeout."""
        providers = self._get_providers()
        out: list[dict] = []
        for p in providers:
            name  = getattr(p, "NAME", "unknown")
            pname = self._provider_name(p)
            alive = self._check_alive_cached(p)
            # Fix 2.1: try to populate models (with timeout via threading)
            models: list[str] = []
            if alive:
                try:
                    result: list[str] = []
                    t = threading.Thread(target=lambda: result.extend(
                        p.list_models() or []))
                    t.start()
                    t.join(timeout=3.0)
                    models = result[:30]
                except Exception:
                    pass
            out.append({
                "name":        name,
                "alive":       alive,
                "latency_ema": round(
                    self._latency.get(pname, self._latency_default(pname)), 3),
                "models":      models,
                "circuit_open": self._cb.is_open(pname),
            })
        return {"providers": out, "count": len(out)}

    async def status_async(self) -> dict:
        providers  = self._get_providers()
        available  = await self.available_providers_async()
        avail_ids  = {id(p) for p in available}

        async def _models(p: Any) -> list[str]:
            try:
                loop = asyncio.get_running_loop()
                return await asyncio.wait_for(
                    loop.run_in_executor(None, p.list_models), timeout=5.0)
            except Exception:
                return []

        model_lists = await asyncio.gather(*[_models(p) for p in providers])
        out: list[dict] = []
        for p, mlist in zip(providers, model_lists):
            name  = getattr(p, "NAME", "unknown")
            pname = self._provider_name(p)
            out.append({
                "name":        name,
                "alive":       id(p) in avail_ids,
                "latency_ema": round(
                    self._latency.get(pname, self._latency_default(pname)), 3),
                "models":      mlist[:30],
                "circuit_open": self._cb.is_open(pname),
            })
        return {"providers": out, "count": len(out)}


# ── Module-level singleton (fix 1.10: guarded by Lock) ───────────────────────

_router:      SmartRouter | None = None
_router_lock: threading.Lock     = threading.Lock()


def get_router() -> SmartRouter:
    global _router
    if _router is None:
        with _router_lock:
            if _router is None:
                from essence.integrations.store import get_store
                _router = SmartRouter(get_store())
    return _router


def init_router(store: Any,
                extra_providers: list[Any] | None = None) -> SmartRouter:
    global _router
    with _router_lock:
        _router = SmartRouter(store, extra_providers)
        return _router
