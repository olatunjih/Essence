"""Health monitor — background poller for backend endpoints and health API endpoint."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403
from essence.infra.circuit import CIRCUIT_BREAKERS  # noqa: F401
from essence.infra.retry_queue import get_retry_queue  # noqa: F401
from essence.infra.ratelimit import _valkey_rate_limiter  # noqa: F401
from essence.infra.otel import _otel_tracer  # noqa: F401
from essence.infra.structured_log import _STRUCTLOG_ENABLED  # noqa: F401
from essence.memory.episodic import EpisodicStore  # noqa: F401
from essence.infra.cost_sqlite import CostSQLite  # noqa: F401

_nats_bus  = None   # set by server bootstrap if NATS is configured
_STRUCTLOG = _STRUCTLOG_ENABLED
_OTEL      = _otel_tracer is not None

_SERVER_START_TS: float = 0.0   # set at server startup


@_dc.dataclass
class _BackendHealth:
    name:    str
    url:     str
    healthy: bool = True
    last_checked: float = 0.0
    last_error:   str = ""


class _NullHealthMonitor:
    """No-op sentinel used before the real HealthMonitor is wired at boot."""
    def status(self) -> list:
        return []
    def all_healthy(self) -> bool:
        return True
    def probe_all(self) -> None:
        pass
    def register(self, name: str, url: str) -> None:
        pass
    def start(self, interval_s: float = 30.0) -> None:
        pass
    def stop(self) -> None:
        pass


class HealthMonitor:
    """Lightweight background health poller for backend endpoints."""

    def __init__(self):
        self._backends: dict[str, _BackendHealth] = {}
        self._lock = threading.Lock()

    def register(self, name: str, url: str) -> None:
        with self._lock:
            self._backends[name] = _BackendHealth(name=name, url=url)

    def _probe(self, bh: "_BackendHealth") -> None:
        ok = _ping_url(bh.url)
        bh.last_checked = time.time()
        bh.healthy = ok
        cb = CIRCUIT_BREAKERS.get(bh.name)
        if ok:
            cb.record_success()
        else:
            bh.last_error = "probe failed"
            cb.record_failure()

    def probe_all(self) -> None:
        with self._lock:
            backends = list(self._backends.values())
        for bh in backends:
            self._probe(bh)

    def status(self) -> list[dict]:
        with self._lock:
            return [_dc.asdict(bh) for bh in self._backends.values()]

    def all_healthy(self) -> bool:
        with self._lock:
            return all(bh.healthy for bh in self._backends.values())

    def start(self, interval_s: float = 30.0) -> None:
        """Run an immediate probe then start a daemon thread that re-probes on schedule."""
        self.probe_all()
        if getattr(self, "_thread", None) is not None:
            return
        self._stop_event = threading.Event()

        def _loop() -> None:
            while not self._stop_event.wait(interval_s):
                self.probe_all()

        self._thread = threading.Thread(target=_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        ev = getattr(self, "_stop_event", None)
        if ev is not None:
            ev.set()
        thr = getattr(self, "_thread", None)
        if thr is not None:
            thr.join(timeout=1.0)
        self._thread = None


def _ping_url(url: str, timeout: float = 2.0) -> bool:
    try:
        import urllib.request
        urllib.request.urlopen(url, timeout=timeout)
        return True
    except Exception:
        return False


HEALTH_MONITOR: HealthMonitor | _NullHealthMonitor = _NullHealthMonitor()


@_dc.dataclass
class HealthDetail:
    uptime_s:          float
    version:           str
    backends:          list[dict]
    circuit_breakers:  list[dict]
    retry_queue_size:  int
    active_sessions:   int
    memory_episodes:   int
    total_tokens_used: int
    rate_limiter:      str
    nats_connected:    bool
    structlog_active:  bool
    otel_active:       bool

    def to_dict(self) -> dict:
        return _dc.asdict(self)


def build_health_detail(workspace: Path,
                         session_count: int = 0) -> HealthDetail:
    """Build a HealthDetail snapshot from current system state."""
    backends = HEALTH_MONITOR.status()
    cbs      = CIRCUIT_BREAKERS.all_status()
    rq       = get_retry_queue(workspace)
    rq_size  = rq.size() if rq else 0
    ep_count = 0
    try:
        es = EpisodicStore(workspace)
        ep_count = es.count()
    except Exception:
        pass
    tok_total = 0
    try:
        cs = CostSQLite(workspace)
        tok_total = cs.total_tokens()
    except Exception:
        pass
    nats_ok = bool(_nats_bus and getattr(_nats_bus, "_ready", False))
    return HealthDetail(
        uptime_s          = time.time() - _SERVER_START_TS,
        version           = Essence_VERSION,
        backends          = backends,
        circuit_breakers  = cbs,
        retry_queue_size  = rq_size,
        active_sessions   = session_count,
        memory_episodes   = ep_count,
        total_tokens_used = tok_total,
        rate_limiter      = ("valkey" if _valkey_rate_limiter else "in-process"),
        nats_connected    = nats_ok,
        structlog_active  = _STRUCTLOG_ENABLED and _STRUCTLOG,
        otel_active       = _OTEL and _otel_tracer is not None,
    )
