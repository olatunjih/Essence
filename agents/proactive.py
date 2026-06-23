"""WebhookEventBus +  ProactiveEngine.
v29.0: +10 analytical event types (drift, edge decay, etc.); see wiring doc """
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403

# WEBHOOK EVENT BUS
# ══════════════════════════════════════════════════════════════════════════════
# Replaces the pure polling ProactiveEngine with an event-driven bus.
# Supported event sources (all degrade gracefully when credentials absent):
#   • Google Calendar webhook (push notifications via HTTPS POST)
#   • GitHub / GitLab webhook (push, PR, issue events)
#   • Gmail watch (Google Pub/Sub push)
#   • Generic HTTP webhook (POST /api/webhooks/receive)
#   • Polling fallback (original behaviour — always active as safety net)
#
# The FastAPI server registers /api/webhooks/receive which fans events out
# to all registered WebhookSubscription handlers.
#
# Usage:
#   bus = WebhookEventBus(workspace, memory)
#   bus.subscribe("github.push", lambda e: agent.run_task(f"Summarise PR: {e}"))
#   bus.subscribe("gcal.event_created", lambda e: agent.run_task(f"Prep briefing: {e}"))
#   # Events arrive via POST /api/webhooks/receive?source=github&event=push

import dataclasses as _dc
import functools as _functools
from collections import defaultdict as _defaultdict

@_dc.dataclass
class WebhookEvent:
    """A single event received from an external source."""
    source:    str          # "github" | "gcal" | "gmail" | "generic"
    event_type: str         # "push" | "pr_merged" | "event_created" | ...
    payload:   dict         # raw parsed JSON from the webhook
    received_at: float = 0.0

    def __post_init__(self) -> None:
        if not self.received_at:
            self.received_at = time.time()

    @property
    def key(self) -> str:
        return f"{self.source}.{self.event_type}"

    def summary(self) -> str:
        """Human-readable one-liner for logging and ProactiveEvent body."""
        parts = []
        p = self.payload
        if self.source == "github":
            repo = p.get("repository", {}).get("full_name", "")
            if self.event_type == "push":
                branch = p.get("ref", "").replace("refs/heads/", "")
                n_commits = len(p.get("commits", []))
                parts.append(f"Push to {repo}/{branch}: {n_commits} commit(s)")
            elif "pull_request" in p:
                pr = p["pull_request"]
                parts.append(f"PR #{pr.get('number')} '{pr.get('title','')}' "
                              f"[{self.event_type}] on {repo}")
        elif self.source == "gcal":
            summary = p.get("summary", p.get("title", "event"))
            start   = p.get("start", {}).get("dateTime", p.get("start", {}).get("date", ""))
            parts.append(f"Calendar: '{summary}' at {start}")
        elif self.source == "gmail":
            subject = p.get("subject", "")
            sender  = p.get("from", "")
            parts.append(f"Email from {sender}: '{subject}'")
        else:
            parts.append(f"{self.source}/{self.event_type}: "
                         f"{json.dumps(p)[:120]}")
        return " | ".join(parts) if parts else f"{self.key}"


class WebhookEventBus:
    """
    Lightweight in-process event bus for webhook-driven triggers.
    Thread-safe. Subscribers run in the bus worker thread to avoid
    blocking the FastAPI event loop.
    """

    def __init__(self, workspace: Path, memory: "Memory") -> None:
        self._ws         = workspace
        self._mem        = memory
        self._handlers:  dict[str, list] = _defaultdict(list)
        self._queue:     "asyncio.Queue[WebhookEvent | None]" = None  # type: ignore
        self._thread:    threading.Thread | None = None
        self._running    = False
        self._lock       = threading.Lock()
        self._event_log  = workspace / "logs" / "webhook_events.jsonl"
        self._event_log.parent.mkdir(parents=True, exist_ok=True)

    # ── Subscription ─────────────────────────────────────────────────────────
    def subscribe(self, event_key: str,
                  handler: "Callable[[WebhookEvent], None]") -> None:
        """
        Register a handler for a specific event key ("source.event_type").
        Use "*" to subscribe to all events.
        Handler is called synchronously in the bus worker thread.
        """
        with self._lock:
            self._handlers[event_key].append(handler)
            log.debug("webhook_subscribed", extra={"key": event_key})

    def unsubscribe_all(self, event_key: str) -> None:
        with self._lock:
            self._handlers.pop(event_key, None)

    # ── Publishing ────────────────────────────────────────────────────────────
    def publish(self, event: WebhookEvent) -> None:
        """
        Thread-safe event publication. Called by the FastAPI route handler
        and also internally by the polling fallback.
        """
        # Log raw event
        try:
            with open(self._event_log, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "ts": event.received_at, "key": event.key,
                    "summary": event.summary()}) + "\n")
        except Exception:
            pass

        # Fan out to handlers
        with self._lock:
            handlers = (list(self._handlers.get(event.key, [])) +
                        list(self._handlers.get("*", [])))

        for handler in handlers:
            try:
                handler(event)
            except Exception as e:
                log.error("webhook_handler_error",
                          extra={"key": event.key, "error": str(e)[:120]})

    # ── HTTP signature verification ───────────────────────────────────────────
    @staticmethod
    def verify_github_signature(payload_bytes: bytes,
                                 signature_header: str) -> bool:
        """Verify GitHub webhook HMAC-SHA256 signature."""
        secret = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
        if not secret:
            return True   # No secret configured — skip verification
        import hmac as _hmac
        expected = "sha256=" + _hmac.new(
            secret.encode(), payload_bytes, "sha256").hexdigest()
        return secrets.compare_digest(expected, signature_header)

    # ── Polling fallback (runs alongside webhook subscriptions) ───────────────
    def start_polling(self, interval_s: int = 300) -> None:
        """
        Background thread that polls slow-changing sources every interval_s.
        Runs even when webhooks are configured — acts as a safety-net fallback.
        """
        if self._running:
            return
        self._running = True
        self._thread  = threading.Thread(target=self._poll_loop,
                                         args=(interval_s,), daemon=True)
        self._thread.start()
        log.debug("webhook_bus_polling_started",
                  extra={"interval_s": interval_s})

    def stop(self) -> None:
        self._running = False

    def _poll_loop(self, interval_s: int) -> None:
        while self._running:
            try:
                self._poll_gcal()
                self._poll_github_repos()
            except Exception as e:
                log.debug("webhook_poll_error", extra={"error": str(e)[:80]})
            time.sleep(interval_s)

    def _poll_gcal(self) -> None:
        """Poll Google Calendar for upcoming events (requires GOOGLE_CALENDAR_ID)."""
        cal_id = os.environ.get("GOOGLE_CALENDAR_ID", "")
        token  = os.environ.get("GOOGLE_CALENDAR_TOKEN", "")
        if not cal_id or not token:
            return
        try:
            import urllib.parse
            now   = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            limit = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                   time.gmtime(time.time() + 3600))
            url   = (f"https://www.googleapis.com/calendar/v3/calendars/"
                     f"{urllib.parse.quote(cal_id)}/events"
                     f"?timeMin={now}&timeMax={limit}&singleEvents=true"
                     f"&orderBy=startTime&maxResults=5")
            req = urllib.request.Request(
                url, headers={"Authorization": f"Bearer {token}"})
            resp  = json.loads(urllib.request.urlopen(req, timeout=8).read())
            for item in resp.get("items", []):
                evt = WebhookEvent(
                    source="gcal", event_type="event_upcoming",
                    payload=item)
                # Only publish once per event (deduplicate by event id)
                eid = item.get("id", "")
                key = f"_gcal_seen_{eid}"
                if not self._mem.get(key):
                    self._mem.set(key, str(time.time()))
                    self.publish(evt)
        except Exception as e:
            log.debug("gcal_poll_error", extra={"error": str(e)[:60]})

    def _poll_github_repos(self) -> None:
        """Poll configured GitHub repos for recent pushes/PRs."""
        repos_str = os.environ.get("GITHUB_WATCH_REPOS", "")
        gh_token  = os.environ.get("GITHUB_TOKEN", "")
        if not repos_str:
            return
        for repo in repos_str.split(","):
            repo = repo.strip()
            if not repo:
                continue
            try:
                headers = {}
                if gh_token:
                    headers["Authorization"] = f"Bearer {gh_token}"
                req   = urllib.request.Request(
                    f"https://api.github.com/repos/{repo}/events?per_page=5",
                    headers={**headers, "Accept": "application/vnd.github+json"})
                resp  = json.loads(urllib.request.urlopen(req, timeout=8).read())
                for event in resp:
                    eid = event.get("id", "")
                    key = f"_gh_seen_{eid}"
                    if not self._mem.get(key):
                        self._mem.set(key, str(time.time()))
                        self.publish(WebhookEvent(
                            source="github",
                            event_type=event.get("type", "Event").lower(),
                            payload=event))
            except Exception as e:
                log.debug("github_poll_error",
                          extra={"repo": repo, "error": str(e)[:60]})


# ── Module-level singleton ────────────────────────────────────────────────────
_event_bus: WebhookEventBus | None = None

def get_event_bus(workspace: Path | None = None,
                  memory: "Memory | None" = None) -> "WebhookEventBus | None":
    """Return (or create) the module-level WebhookEventBus."""
    global _event_bus
    if _event_bus is None and workspace is not None and memory is not None:
        _event_bus = WebhookEventBus(workspace, memory)
    return _event_bus



# PROACTIVE ENGINE
# ══════════════════════════════════════════════════════════════════════════════
# Runs as a background heartbeat job.  Scans memory, workspace, and system
# state to surface actionable events the user hasn't asked about yet.

@_dc.dataclass
class ProactiveEvent:
    kind:       str     # "stale_project" | "disk_alert" | "memory_gap" | "deadline"
    title:      str
    body:       str
    priority:   int     # 0 (info) → 4 (critical)
    action_hint: str    = ""
    created_at: float   = 0.0

    def format(self) -> str:
        icons = {0: "ℹ", 1: "·", 2: "⚠", 3: "⚡", 4: "🚨"}
        icon = icons.get(self.priority, "·")
        lines = [f"{icon} **{self.title}**", f"  {self.body}"]
        if self.action_hint:
            lines.append(f"  → {self.action_hint}")
        return "\n".join(lines)


class ProactiveEngine:
    """
    Scans environment and memory for events worth surfacing proactively.

    Checks performed on each scan():
      1. Stale projects — memory items not updated in N days
      2. Disk usage    — alert if workspace disk > 80%
      3. Memory gaps   — semantic memory has items with low confidence
      4. Heartbeat tasks — jobs that have never run or are overdue
    """

    STALE_DAYS      = 7
    DISK_WARN_PCT   = 80
    DISK_CRIT_PCT   = 90

    def __init__(self, workspace: Path, memory: "Memory",
                 event_bus: "WebhookEventBus | None" = None) -> None:
        self._ws   = workspace
        self._mem  = memory
        self._bus  = event_bus
        # Subscribe to webhook events for event-driven proactivity
        if event_bus:
            # EventBus.subscribe() is async; use subscribe_sync
            # so the coroutine is not created-and-discarded silently
            _sub = getattr(event_bus, "subscribe_sync", None) or getattr(event_bus, "subscribe", None)
            if callable(_sub):
                _sub("github.push",        self._on_github_push)
                _sub("gcal.event_upcoming", self._on_gcal_event)
                _sub("gmail.message",       self._on_gmail_message)

    def _on_event(self, event: Any) -> None:
        """
        Handle a NeuromorphicEventBus or WebhookEventBus event for proactive
        awareness.  Wired by Agent.__init__ via event_bus.subscribe("proactive", …).

        Supports both WebhookEvent objects and plain dict payloads.
        Writes a pending-alert entry to memory so the next scan() surfaces it.
        """
        try:
            if hasattr(event, "key"):
                event_type = event.key
                body       = event.summary() if hasattr(event, "summary") else str(event)[:200]
            elif isinstance(event, dict):
                event_type = event.get("event_type", event.get("type", "unknown"))
                body       = event.get("body", str(event)[:200])
            else:
                event_type = type(event).__name__
                body       = str(event)[:200]

            # Assign priority by analytical event class
            _high = {"analytical_drift", "aegis_circuit_open", "confidence_collapse",
                     "domain_shift", "hallucination_spike"}
            priority = 3 if event_type in _high else 1

            key = f"_proactive_pending_{int(time.time() * 1000)}"
            self._mem.set(key, json.dumps({
                "type":   event_type,
                "title":  f"Analytics Engine: {event_type.replace('_', ' ').title()}",
                "body":   body[:300],
                "priority": priority,
                "action": "Investigate: essence agent 'Explain Analytics Engine alert'",
            }))
        except Exception as _e:
            log.debug("proactive._on_event error", extra={"error": str(_e)[:80]})

    def _on_github_push(self, event: "WebhookEvent") -> None:
        """Handle a GitHub push event: surface as a proactive ProactiveEvent."""
        self._mem.set(
            f"_proactive_pending_{int(time.time())}",
            json.dumps({"kind": "github_push",
                        "title": "New code push",
                        "body": event.summary(),
                        "priority": 1,
                        "action_hint": "Review and summarise the changes?"}))

    def _on_gcal_event(self, event: "WebhookEvent") -> None:
        """Handle an upcoming calendar event."""
        self._mem.set(
            f"_proactive_pending_{int(time.time())}",
            json.dumps({"kind": "calendar_reminder",
                        "title": "Upcoming calendar event",
                        "body": event.summary(),
                        "priority": 2,
                        "action_hint": "Prepare a briefing?"}))

    def _on_gmail_message(self, event: "WebhookEvent") -> None:
        """Handle a new email message."""
        self._mem.set(
            f"_proactive_pending_{int(time.time())}",
            json.dumps({"kind": "new_email",
                        "title": "New email",
                        "body": event.summary(),
                        "priority": 1,
                        "action_hint": "Summarise or reply?"}))

    def scan(self) -> list[ProactiveEvent]:
        events: list[ProactiveEvent] = []
        now = time.time()
        events.extend(self._check_disk(now))
        events.extend(self._check_stale_projects(now))
        events.extend(self._check_overdue_heartbeats(now))
        # v29 Analytics Engine analytical checks
        events.extend(self._check_analytical_drift(now))
        return sorted(events, key=lambda e: e.priority, reverse=True)

    def _check_disk(self, now: float) -> list[ProactiveEvent]:
        events: list[ProactiveEvent] = []
        try:
            usage = shutil.disk_usage(str(self._ws))
            pct   = 100 * usage.used / max(usage.total, 1)
            free_gb = usage.free / 1e9
            if pct >= self.DISK_CRIT_PCT:
                events.append(ProactiveEvent(
                    kind="disk_alert", title="Disk nearly full",
                    body=f"{pct:.0f}% used — only {free_gb:.1f} GB free",
                    priority=4, created_at=now,
                    action_hint="Run: find ~/.essence -name '*.log' | xargs rm"))
            elif pct >= self.DISK_WARN_PCT:
                events.append(ProactiveEvent(
                    kind="disk_alert", title="Disk usage high",
                    body=f"{pct:.0f}% used — {free_gb:.1f} GB remaining",
                    priority=2, created_at=now))
        except Exception:
            pass
        return events

    def _check_analytical_drift(self, now: float) -> list[ProactiveEvent]:
        """
        v29: 10 Analytics Engine Analytical Event Types surfaced proactively.

        Sources checked:
          1. _prism_pending_alerts  — alerts written by Resilience Layer / wave controller
          2. _proactive_pending_*   — alerts written by _on_event() hook
          3. Confidence collapse    — last task had avg finding confidence < 0.4
          4. Domain shift           — domain lens changed from baseline
        """
        events: list[ProactiveEvent] = []

        # ── Source 1: Batch alert list (legacy Analytics Engine path) ────────────────
        pending_alerts = self._mem.get("_prism_pending_alerts", [])
        if isinstance(pending_alerts, list):
            for alert in pending_alerts[:10]:
                events.append(ProactiveEvent(
                    kind=alert.get("type", "analytical_alert"),
                    title=alert.get("title", "Analytical Drift Detected"),
                    body=alert.get("body", ""),
                    priority=alert.get("priority", 2),
                    created_at=now,
                    action_hint=alert.get("action", "Run: essence agent 'Investigate drift'")
                ))

        # ── Source 2: Per-event keys written by _on_event() ──────────────
        cutoff = now - 3600  # surface events from last hour only
        for key in list(self._mem.keys() if hasattr(self._mem, "keys") else []):
            if not key.startswith("_proactive_pending_"):
                continue
            try:
                ts = float(key.split("_")[-1]) / 1000
                if ts < cutoff:
                    continue
                raw = self._mem.get(key)
                if not raw:
                    continue
                alert = json.loads(raw) if isinstance(raw, str) else raw
                events.append(ProactiveEvent(
                    kind=alert.get("type", "analytical_event"),
                    title=alert.get("title", "Analytical Event"),
                    body=alert.get("body", ""),
                    priority=alert.get("priority", 1),
                    created_at=now,
                    action_hint=alert.get("action", ""),
                ))
            except Exception:
                continue

        # ── Source 3: Confidence collapse detection ───────────────────────
        last_conf = self._mem.get("_prism_last_avg_confidence")
        if last_conf is not None:
            try:
                avg_conf = float(last_conf)
                if avg_conf < 0.4:
                    events.append(ProactiveEvent(
                        kind="confidence_collapse",
                        title="Low analytical confidence in last task",
                        body=f"Average finding confidence: {avg_conf:.2f} (threshold 0.40)",
                        priority=3, created_at=now,
                        action_hint="Re-run analysis with higher wave depth or more data",
                    ))
            except (ValueError, TypeError):
                pass

        # ── Source 4: Domain lens shift ───────────────────────────────────
        baseline_domain = self._mem.get("_prism_baseline_domain", "")
        current_domain  = self._mem.get("_prism_current_domain", "")
        if baseline_domain and current_domain and baseline_domain != current_domain:
            events.append(ProactiveEvent(
                kind="domain_shift",
                title=f"Domain lens changed: {baseline_domain} → {current_domain}",
                body="Task domain shifted from baseline. Review domain-specific SOPs.",
                priority=1, created_at=now,
                action_hint=f"Run: essence agent 'Analyse {current_domain} task'",
            ))

        # ── Source 5: Resilience Layer circuit breaker open ─────────────────────────
        aegis_open = self._mem.get("_aegis_circuit_open", "")
        if aegis_open:
            events.append(ProactiveEvent(
                kind="aegis_circuit_open",
                title=f"Resilience Layer circuit breaker open: {aegis_open}",
                body="A subsystem circuit breaker tripped. Service degraded.",
                priority=4, created_at=now,
                action_hint="Check logs: essence logs --filter aegis",
            ))

        return events

    def _check_stale_projects(self, now: float) -> list[ProactiveEvent]:
        events: list[ProactiveEvent] = []
        try:
            projects = self._mem.get("ongoing_projects", {})
            if isinstance(projects, dict):
                for name, info in list(projects.items())[:5]:
                    last = info.get("last_updated", 0) if isinstance(info, dict) else 0
                    days_ago = (now - last) / 86400
                    if days_ago >= self.STALE_DAYS:
                        events.append(ProactiveEvent(
                            kind="stale_project",
                            title=f"Project '{name}' not touched in {days_ago:.0f} days",
                            body=str(info)[:120],
                            priority=1, created_at=now,
                            action_hint=f"Resume: essence agent 'Continue work on {name}'"))
        except Exception:
            pass
        return events

    def _check_overdue_heartbeats(self, now: float) -> list[ProactiveEvent]:
        events: list[ProactiveEvent] = []
        try:
            sched_path = self._ws / "logs" / "heartbeat_jobs.json"
            if sched_path.exists():
                jobs_raw = json.loads(sched_path.read_text(encoding="utf-8"))
                for job in jobs_raw:
                    last_run = job.get("last_run", 0)
                    interval = job.get("interval_s", 3600)
                    overdue_s = now - last_run - interval
                    if overdue_s > interval:
                        events.append(ProactiveEvent(
                            kind="deadline",
                            title=f"Heartbeat job '{job.get('name','')}' overdue",
                            body=f"Expected every {interval//60}m, "
                                 f"last ran {(now-last_run)//3600:.0f}h ago",
                            priority=2, created_at=now))
        except Exception:
            pass
        return events

    def format_briefing(self, events: list[ProactiveEvent]) -> str:
        if not events:
            return ""
        lines = ["## Proactive briefing"]
        for e in events:
            lines.append(e.format())
        return "\n".join(lines)

    def deliver(self, events: list[ProactiveEvent],
                guardrail: "Any | None" = None) -> str:
        """
        Format ``events`` into a briefing string and apply guardrail
        ``post_exec()`` before returning the content for delivery.

        This is the canonical emit path for all proactive output.
        Callers that previously called ``format_briefing()`` directly and sent
        the result to a channel should switch to ``deliver()`` so that proactive
        output passes through the same G5/G9/G10 content gates as
        capsule-originated execution (GuardrailLayer.post_exec).

        Parameters
        ----------
        events   : list[ProactiveEvent]  events returned by scan()
        guardrail: GuardrailLayer | None  pass the kernel's guardrail instance
                   to enable content gating.  When None, no guardrail is applied
                   (safe for standalone / personal deployments).

        Returns
        -------
        str  — the screened briefing text, or "" when all events are cleared
               or guardrail denies the content.
        """
        content = self.format_briefing(events)
        if not content:
            return ""

        if guardrail is None:
            return content

        # Apply post_exec guardrail gate (same path as PipelineExecutor)
        try:
            result = guardrail.post_exec(
                output=content,
                tool_name="proactive_engine",
                user_id="__system__",
            )
            if hasattr(result, "allowed") and not result.allowed:
                log.warning(
                    "proactive_deliver_blocked",
                    extra={"guardrail": getattr(result, "guardrail_id", "?"),
                           "reason": getattr(result, "reason", "")[:120]},
                )
                return ""
        except Exception as _ge:
            log.debug("proactive_deliver_guardrail_error",
                      extra={"error": str(_ge)[:120]})

        return content

    def as_heartbeat_job(self, scheduler: "HeartbeatScheduler",
                          interval: str = "30m") -> None:
        """Register a recurring scan as a HeartbeatJob."""
        scheduler.add("proactive_scan",
                      "Run proactive scan and surface any pending events.",
                      interval)


# ══════════════════════════════════════════════════════════════════════════════
