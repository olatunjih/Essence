""" OpenTelemetry +  W3C trace headers."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403

# OPENTELEMETRY SPANS  — proper parent-child trace instrumentation
# ══════════════════════════════════════════════════════════════════════════════
# Replaces AgentObserver's manual span dicts with real OpenTelemetry spans.
# Falls back to a no-op tracer when opentelemetry-sdk is not installed.
# Auto-propagation through contextvars handles parent-child relationships.
#
# ENV:  OTEL_EXPORTER_OTLP_ENDPOINT=http://jaeger:4317  (standard OTLP env)

try:
    from opentelemetry import trace as _otel_trace
    from opentelemetry.sdk.trace import TracerProvider as _OTelTP
    from opentelemetry.sdk.trace.export import (
        BatchSpanProcessor as _OTelBSP)
    try:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter as _OTLPExporter)
        _OTLP_EXPORTER = True
    except ImportError:
        _OTLP_EXPORTER = False
    _OTEL = True
except ImportError:
    _otel_trace    = None   # type: ignore
    _OTelTP        = None   # type: ignore
    _OTEL          = False
    _OTLP_EXPORTER = False

_otel_tracer: Any = None


def _setup_otel(service_name: str = "essence") -> None:
    """Configure OpenTelemetry with OTLP export when endpoint is set."""
    global _otel_tracer
    if not _OTEL:
        return
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "")
    provider = _OTelTP()
    if endpoint and _OTLP_EXPORTER:
        exporter  = _OTLPExporter(endpoint=endpoint)
        processor = _OTelBSP(exporter)
        provider.add_span_processor(processor)
    _otel_trace.set_tracer_provider(provider)
    _otel_tracer = _otel_trace.get_tracer(service_name)
    log.info("otel_configured",
             extra={"endpoint": endpoint or "none (no export)",
                    "service":  service_name})


def get_tracer() -> Any:
    """Return the OpenTelemetry tracer (or no-op context manager if unavailable)."""
    if _otel_tracer is not None:
        return _otel_tracer
    if _OTEL:
        return _otel_trace.get_tracer("essence")
    # No-op context manager fallback
    import contextlib
    class _NoOpSpan:
        def set_attribute(self, *a, **k): pass
        def record_exception(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
    @contextlib.contextmanager
    def _noop_span(*a, **k):
        yield _NoOpSpan()
    class _NoOpTracer:
        def start_as_current_span(self, *a, **k): return _noop_span()
    return _NoOpTracer()


# ── Span helpers wired into hot paths ──────────────────────────────────────────

def span_llm(model: str, **attrs: Any):
    """Context manager: OpenTelemetry span for an LLM call."""
    tracer = get_tracer()
    span   = tracer.start_as_current_span(
        f"llm.complete",
        attributes={"essence.model": model, **attrs})
    return span


def span_tool(name: str, **attrs: Any):
    """Context manager: OpenTelemetry span for a tool dispatch."""
    tracer = get_tracer()
    return tracer.start_as_current_span(
        f"tool.{name}",
        attributes={"essence.tool": name, **attrs})


# ══════════════════════════════════════════════════════════════════════════════

# W3C TRACE CONTEXT PROPAGATION  — distributed A2A trace headers
# ══════════════════════════════════════════════════════════════════════════════
# Injects W3C traceparent/tracestate headers into A2A outbound calls.
# Extracts and activates context from inbound A2A requests.
# Connects multi-agent Essence workflows into a single Jaeger/Tempo trace tree.
#
# Standard: https://www.w3.org/TR/trace-context/

_TRACEPARENT_HEADER = "traceparent"
_TRACESTATE_HEADER  = "tracestate"


def get_traceparent() -> str:
    """
    Return the current W3C traceparent header value.
    Format: 00-{trace_id}-{span_id}-{flags}
    Returns empty string when OpenTelemetry is not active.
    """
    if not _OTEL:
        return ""
    try:
        span    = _otel_trace.get_current_span()
        ctx     = span.get_span_context()
        if not ctx.is_valid:
            return ""
        trace_id = format(ctx.trace_id, "032x")
        span_id  = format(ctx.span_id,  "016x")
        flags    = "01" if ctx.trace_flags else "00"
        return f"00-{trace_id}-{span_id}-{flags}"
    except Exception:
        return ""


def inject_trace_headers(headers: dict) -> dict:
    """
    Add W3C traceparent header to an outbound HTTP headers dict.
    Returns the headers dict unchanged if tracing is not active.
    """
    tp = get_traceparent()
    if tp:
        headers = dict(headers)
        headers[_TRACEPARENT_HEADER] = tp
    return headers


def extract_trace_context(headers: dict) -> None:
    """
    Extract and activate the incoming W3C trace context from request headers.
    Call this at the top of every inbound A2A/webhook handler.
    No-op when OpenTelemetry is not installed.
    """
    if not _OTEL:
        return
    try:
        from opentelemetry.propagate import extract as _otel_extract
        from opentelemetry.context   import attach  as _otel_attach
        ctx = _otel_extract(headers)
        _otel_attach(ctx)
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════════
