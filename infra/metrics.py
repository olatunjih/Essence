""" Prometheus +  push gateway."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403

# PROMETHEUS METRICS  — /metrics endpoint
# ══════════════════════════════════════════════════════════════════════════════
# Exposes: llm_calls, tool_calls, sessions_active, tokens, request_duration.
# Uses prometheus_client when available; falls back to a simple text renderer.
# ENV:  Essence_METRICS=1   Enable /metrics endpoint (default: off)

_METRICS_ENABLED = os.environ.get("Essence_METRICS", "0") == "1"

try:
    from prometheus_client import (  # type: ignore
        Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST)
    _PROM = True
    _m_llm_calls     = Counter("essence_llm_calls_total",
                                "LLM completions", ["model", "backend"])
    _m_tool_calls    = Counter("essence_tool_calls_total",
                                "Tool dispatches", ["tool", "status"])
    _m_tokens_in     = Counter("essence_tokens_in_total",
                                "Input tokens consumed", ["model"])
    _m_tokens_out    = Counter("essence_tokens_out_total",
                                "Output tokens generated", ["model"])
    _m_sessions      = Gauge("essence_sessions_active",
                              "Active agent sessions")
    _m_req_duration  = Histogram("essence_request_duration_seconds",
                                  "HTTP request latency", ["route"])
    _m_sss_facts     = Gauge("essence_sss_facts_total",
                              "SemanticStateStore fact count")
    _m_bandit_arms   = Gauge("essence_bandit_arms_total",
                              "Contextual bandit arm observations")
except ImportError:
    _PROM = False
    # Lightweight fallback: shared counters dict
    _metrics_counters: dict[str, float] = {}

    class _FakeMetric:
        def __init__(self, *a, **kw): pass
        def labels(self, **kw): return self
        def inc(self, v=1): pass
        def set(self, v): pass
        def observe(self, v): pass

    _m_llm_calls = _m_tool_calls = _m_tokens_in = _m_tokens_out = _FakeMetric()
    _m_sessions  = _m_sss_facts  = _m_bandit_arms = _FakeMetric()
    _m_req_duration = _FakeMetric()
    # Skill-level metrics (also no-op in fallback)
    _m_skill_duration = _m_skill_total = _m_autonomous_actions = _FakeMetric()
    _m_adapter_calls = _m_cb_state = _m_model_confidence = _FakeMetric()
    _m_cache_hit_ratio = _m_memory_episodes = _FakeMetric()

    # Boot-time warning so operators know metrics are silently no-op (#15)
    import logging as _logging
    _logging.getLogger("essence.metrics").warning(
        "metrics_noop: prometheus_client not installed — "
        "all metrics recording is a no-op. "
        "Install with: pip install prometheus-client"
    )

else:
    # Spec-required skill/agent metrics (Part 7)
    _m_skill_duration = Histogram(
        "essence_skill_duration_seconds",
        "Skill execution time", ["skill", "status"])
    _m_skill_total = Counter(
        "essence_skill_total",
        "Skill executions", ["skill", "status", "type"])
    _m_autonomous_actions = Counter(
        "essence_autonomous_actions_total",
        "Autonomous actions taken", ["action", "level"])
    _m_adapter_calls = Counter(
        "essence_adapter_calls_total",
        "External adapter calls", ["adapter", "status"])
    _m_cb_state = Gauge(
        "essence_circuit_breaker_state",
        "Circuit breaker state 0=closed 1=open", ["adapter"])
    _m_model_confidence = Histogram(
        "essence_model_confidence",
        "Prediction confidence distribution", ["model"])
    _m_cache_hit_ratio = Gauge(
        "essence_cache_hit_ratio",
        "Cache hit ratio", ["cache_type"])
    _m_memory_episodes = Counter(
        "essence_memory_episodes_total",
        "Episodes stored in episodic memory")


def record_metric_skill(skill: str, status: str, skill_type: str,
                        duration_s: float) -> None:
    """Record one skill execution in Prometheus counters."""
    if not _METRICS_ENABLED:
        return
    _m_skill_duration.labels(skill=skill[:40], status=status).observe(duration_s)
    _m_skill_total.labels(skill=skill[:40], status=status, type=skill_type).inc()


def record_metric_autonomous_action(action: str, level: str) -> None:
    """Record one autonomous goal execution."""
    if not _METRICS_ENABLED:
        return
    _m_autonomous_actions.labels(action=action[:40], level=level).inc()


def record_metric_adapter_call(adapter: str, success: bool) -> None:
    """Record one adapter call."""
    if not _METRICS_ENABLED:
        return
    _m_adapter_calls.labels(adapter=adapter, status="ok" if success else "error").inc()


def record_circuit_breaker_state(adapter: str, is_open: bool) -> None:
    """Record circuit breaker state change."""
    if not _METRICS_ENABLED:
        return
    _m_cb_state.labels(adapter=adapter).set(1 if is_open else 0)


def record_metric_model_confidence(model: str, confidence: float) -> None:
    """Record a model confidence score."""
    if not _METRICS_ENABLED:
        return
    _m_model_confidence.labels(model=model).observe(confidence)


def record_memory_episode() -> None:
    """Increment the episodic memory episode counter."""
    if not _METRICS_ENABLED:
        return
    _m_memory_episodes.inc()


def record_metric_llm(model: str, backend: str,
                       tokens_in: int, tokens_out: int) -> None:
    """Record one LLM call in Prometheus counters."""
    if not _METRICS_ENABLED: return
    _m_llm_calls.labels(model=model, backend=backend).inc()
    _m_tokens_in.labels(model=model).inc(tokens_in)
    _m_tokens_out.labels(model=model).inc(tokens_out)


def record_metric_tool(tool: str, success: bool) -> None:
    if not _METRICS_ENABLED: return
    _m_tool_calls.labels(tool=tool, status="ok" if success else "error").inc()


def metrics_text() -> str:
    """Render /metrics payload — uses prometheus_client or fallback."""
    if _PROM:
        return generate_latest().decode("utf-8")
    # Minimal fallback text format
    lines = [f"# Essence metrics fallback (pip install prometheus-client for full support)"]
    for k, v in sorted(_metrics_counters.items()):
        lines.append(f"essence_{k} {v}")
    return "\n".join(lines) + "\n"


_PROMETHEUS_GATEWAY_URL = os.environ.get("Essence_PROMETHEUS_GATEWAY", "")


def push_metrics_to_gateway(job: str = "essence") -> bool:
    """ — push current metrics to a Prometheus Pushgateway.
    [Referenced by infra/sentinel.py's "_prometheus_push" heartbeat sentinel
    but never implemented anywhere — content-loss gap from build_pkg.py.]
    Returns True on a successful push, False otherwise (never raises —
    this runs from a heartbeat sentinel and must not crash the scheduler)."""
    if not _PROMETHEUS_GATEWAY_URL:
        return False
    try:
        import urllib.request
        payload = metrics_text().encode("utf-8")
        url = f"{_PROMETHEUS_GATEWAY_URL.rstrip('/')}/metrics/job/{job}"
        req = urllib.request.Request(url, data=payload, method="POST")
        urllib.request.urlopen(req, timeout=5).read()
        return True
    except Exception as e:
        log.debug("prometheus_push_failed", extra={"error": str(e)})
        return False


# ══════════════════════════════════════════════════════════════════════════════
