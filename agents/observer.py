"""AgentObserver: token tracing, tool latency, drift detection.
v29.0: +15 Analytics Engine analytical traces; see wiring doc """
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403

# AGENT OBSERVER
# ══════════════════════════════════════════════════════════════════════════════
# AgentObserver provides lightweight OpenTelemetry-compatible spans without
# requiring an OTel SDK.  Traces export to JSONL and optionally to an OTel
# collector via HTTP if OTEL_EXPORTER_OTLP_ENDPOINT is set.

@_dc.dataclass
class TraceSpan:
    span_id:    str
    parent_id:  str
    name:       str
    start_ns:   int
    end_ns:     int   = 0
    attributes: dict  = _dc.field(default_factory=dict)
    events:     list  = _dc.field(default_factory=list)
    status:     str   = "OK"   # OK | ERROR

    def duration_ms(self) -> float:
        if self.end_ns == 0:
            return 0.0
        return (self.end_ns - self.start_ns) / 1_000_000

    def to_dict(self) -> dict:
        return {
            "span_id": self.span_id, "parent_id": self.parent_id,
            "name": self.name, "start_ns": self.start_ns,
            "end_ns": self.end_ns, "duration_ms": self.duration_ms(),
            "attributes": self.attributes, "events": self.events,
            "status": self.status,
        }


class AgentObserver:
    """
    Lightweight observability layer for agent sessions.

    Records:
      • LLM calls: model, token counts (estimated), latency, thinking flag
      • Tool calls: name, latency, success/failure
      • CriticGate: pass/fail rates per category
      • Verification: hallucination rate
      • Session: total tokens, cost estimate, wall time

    Exports:
      • JSONL trace log to workspace/logs/traces/
      • OpenTelemetry HTTP export when OTEL_EXPORTER_OTLP_ENDPOINT is set
    """

    # Cost estimates USD per 1K tokens (local = near-zero, approximated)
    _COST_PER_1K: dict[str, float] = {
        "qwen3:0.6b": 0.00001, "qwen3:1.7b": 0.00002, "qwen3:4b": 0.00005,
        "qwen3:8b":   0.0001,  "qwen3:14b":  0.0002,  "qwen3:32b": 0.0005,
        "default":    0.001,
    }

    def __init__(self, workspace: Path, session_id: str) -> None:
        self._ws         = workspace / "logs" / "traces"
        self._ws.mkdir(parents=True, exist_ok=True)
        self._session_id = session_id
        self._spans:  list[TraceSpan] = []
        self._lock    = threading.Lock()
        self._otel_ep = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "")
        self._root_id = secrets.token_hex(8)
        # Counters
        self.total_tokens_in:  int   = 0
        self.total_tokens_out: int   = 0
        self.tool_calls:       int   = 0
        self.tool_failures:    int   = 0
        self.critic_passes:    int   = 0
        self.critic_failures:  int   = 0
        self.hallucinations:   int   = 0
        self.total_cost_usd:   float = 0.0
        # Reasoning trace: stores the agent's pre-tool-call LLM output per step.
        # Surfaced via /api/logs for post-hoc task debugging.
        self._reasoning_trace: list[dict] = []
        # Analytics Engine analytical counters
        self.prism_findings:       int = 0
        self.aegis_events:         int = 0
        self.genesis_learns:       int = 0
        self.domain_switches:      int = 0
        self.conflict_resolutions: int = 0

    def _new_span(self, name: str, parent: str = "") -> TraceSpan:
        return TraceSpan(
            span_id   = secrets.token_hex(8),
            parent_id = parent or self._root_id,
            name      = name,
            start_ns  = time.perf_counter_ns(),
        )

    def record_llm_call(self, model: str, prompt_chars: int,
                        response_chars: int, latency_ms: float,
                        thinking: bool = False, step_id: int = 0) -> None:
        # Approximate token counts (4 chars ≈ 1 token)
        tok_in  = max(1, prompt_chars   // 4)
        tok_out = max(1, response_chars // 4)
        cost_key = model.split(":")[0] + ":" + model.split(":")[-1] \
                   if ":" in model else "default"
        cost = ((tok_in + tok_out) / 1000) * \
               self._COST_PER_1K.get(model, self._COST_PER_1K["default"])
        with self._lock:
            self.total_tokens_in  += tok_in
            self.total_tokens_out += tok_out
            self.total_cost_usd   += cost
            span = self._new_span(f"llm.{model}.step{step_id}")
            span.end_ns = time.perf_counter_ns()
            span.attributes = {
                "model": model, "tokens_in": tok_in, "tokens_out": tok_out,
                "latency_ms": latency_ms, "thinking": thinking,
                "cost_usd": round(cost, 6),
            }
            self._spans.append(span)

    def record_tool_call(self, name: str, latency_ms: float,
                          success: bool, result_preview: str = "") -> None:
        with self._lock:
            self.tool_calls   += 1
            if not success:
                self.tool_failures += 1
            span = self._new_span(f"tool.{name}")
            span.end_ns    = time.perf_counter_ns()
            span.status    = "OK" if success else "ERROR"
            span.attributes = {"tool": name, "latency_ms": latency_ms,
                                "success": success,
                                "result_preview": result_preview[:80]}
            self._spans.append(span)

    def record_critic(self, step_id: int, passed: bool,
                       category: str | None = None) -> None:
        with self._lock:
            if passed:
                self.critic_passes   += 1
            else:
                self.critic_failures += 1
            span = self._new_span(f"critic.step{step_id}")
            span.end_ns    = time.perf_counter_ns()
            span.status    = "OK" if passed else "ERROR"
            span.attributes = {"step_id": step_id, "passed": passed,
                                "category": category or ""}
            self._spans.append(span)

    def record_hallucination(self, claim: str, verdict: str) -> None:
        with self._lock:
            if verdict in ("unverified", "contradicted"):
                self.hallucinations += 1
            span = self._new_span("verifier.claim")
            span.end_ns    = time.perf_counter_ns()
            span.status    = "OK" if verdict == "verified" else "ERROR"
            span.attributes = {"claim_preview": claim[:60], "verdict": verdict}
            self._spans.append(span)

    def record_reasoning(self, step_id: int, text: str,
                          phase: str = "execute") -> None:
        """Store the agent's pre-decision LLM output for a given step.
        phase: 'plan' | 'execute' | 'critique' | 'replan'"""
        with self._lock:
            self._reasoning_trace.append({
                "step_id": step_id,
                "phase":   phase,
                "text":    text[:800],
                "ts":      time.time(),
            })

    # ──  Analytics Engine analytical traces ───────────────────────────────────────────

    def record_prism_finding(self, finding_id: str, category: str,
                              confidence: float, layer: str,
                              novelty: float = 0.0) -> None:
        """Trace a Analytics Engine finding emitted by any L0-L7 analytical layer."""
        with self._lock:
            self.prism_findings += 1
            span = self._new_span(f"prism.finding.{category}")
            span.end_ns = time.perf_counter_ns()
            span.attributes = {
                "finding_id": finding_id, "category": category,
                "confidence": round(confidence, 4), "layer": layer,
                "novelty": round(novelty, 4),
            }
            self._spans.append(span)

    def record_aegis_event(self, event_type: str, component: str,
                            severity: str = "warn",
                            detail: str = "") -> None:
        """Trace an Resilience Layer resilience event (R1-R5 circuit breaker / fallback)."""
        with self._lock:
            self.aegis_events += 1
            span = self._new_span(f"aegis.{event_type}")
            span.end_ns = time.perf_counter_ns()
            span.status = "ERROR" if severity == "critical" else "OK"
            span.attributes = {
                "event_type": event_type, "component": component,
                "severity": severity, "detail": detail[:120],
            }
            self._spans.append(span)

    def record_genesis_learn(self, trajectory_id: str, reward: float,
                              outcome: str = "") -> None:
        """Trace a Learning Engine self-learning update after task completion."""
        with self._lock:
            self.genesis_learns += 1
            span = self._new_span("genesis.learn")
            span.end_ns = time.perf_counter_ns()
            span.attributes = {
                "trajectory_id": trajectory_id,
                "reward": round(reward, 4),
                "outcome": outcome[:120],
            }
            self._spans.append(span)

    def record_wave_analysis(self, file_path: str, wave: int,
                              findings_count: int, latency_ms: float = 0.0) -> None:
        """Trace a Analytics Engine WaveController analysis pass."""
        with self._lock:
            span = self._new_span(f"prism.wave.{wave}")
            span.end_ns = time.perf_counter_ns()
            span.attributes = {
                "file_path": file_path[:120], "wave": wave,
                "findings_count": findings_count,
                "latency_ms": round(latency_ms, 1),
            }
            self._spans.append(span)

    def record_domain_switch(self, from_lens: str, to_lens: str,
                              confidence: float = 1.0) -> None:
        """Trace a Domain Lens domain-lens switch."""
        with self._lock:
            self.domain_switches += 1
            span = self._new_span("nexus.domain_switch")
            span.end_ns = time.perf_counter_ns()
            span.attributes = {
                "from_lens": from_lens, "to_lens": to_lens,
                "confidence": round(confidence, 4),
            }
            self._spans.append(span)

    def record_conflict_resolution(self, method: str, winner: str,
                                    options_count: int = 2) -> None:
        """Trace a ConflictResolution arbitration outcome."""
        with self._lock:
            self.conflict_resolutions += 1
            span = self._new_span("protocol.conflict_resolution")
            span.end_ns = time.perf_counter_ns()
            span.attributes = {
                "method": method, "winner": winner[:80],
                "options_count": options_count,
            }
            self._spans.append(span)

    def record_protocol_wrap(self, sender: str, receiver: str,
                              msg_type: str) -> None:
        """Trace a MessageProtocol envelope wrap."""
        with self._lock:
            span = self._new_span(f"protocol.msg.{msg_type}")
            span.end_ns = time.perf_counter_ns()
            span.attributes = {
                "sender": sender, "receiver": receiver, "msg_type": msg_type,
            }
            self._spans.append(span)

    def record_task_handoff(self, task_id: str, from_agent: str,
                             to_agent: str, success: bool = True) -> None:
        """Trace a TaskHandoff claim/transfer event."""
        with self._lock:
            span = self._new_span("protocol.task_handoff")
            span.end_ns = time.perf_counter_ns()
            span.status = "OK" if success else "ERROR"
            span.attributes = {
                "task_id": task_id, "from_agent": from_agent,
                "to_agent": to_agent, "success": success,
            }
            self._spans.append(span)

    def summary(self) -> dict:
        total_calls = max(self.critic_passes + self.critic_failures, 1)
        return {
            "session_id":          self._session_id,
            "total_tokens_in":     self.total_tokens_in,
            "total_tokens_out":    self.total_tokens_out,
            "estimated_cost":      round(self.total_cost_usd, 6),
            "tool_calls":          self.tool_calls,
            "tool_failure_rate":   round(self.tool_failures / max(self.tool_calls,1), 3),
            "critic_pass_rate":    round(self.critic_passes / total_calls, 3),
            "hallucination_count": self.hallucinations,
            "span_count":          len(self._spans),
            "reasoning_steps":     len(self._reasoning_trace),
            "prism_findings":       self.prism_findings,
            "aegis_events":         self.aegis_events,
            "genesis_learns":       self.genesis_learns,
            "domain_switches":      self.domain_switches,
            "conflict_resolutions": self.conflict_resolutions,
        }

    def export_jsonl(self) -> Path:
        path = self._ws / f"{self._session_id}.jsonl"
        lines = [json.dumps(s.to_dict()) for s in self._spans]
        # Append reasoning trace entries so /api/logs shows full decision chain
        for r in self._reasoning_trace:
            lines.append(json.dumps({"type": "reasoning", **r}))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def export_otel(self) -> None:
        """Send spans to OTEL collector if endpoint is configured."""
        if not self._otel_ep:
            return
        try:
            spans_proto = [s.to_dict() for s in self._spans[-100:]]
            payload = json.dumps({
                "resourceSpans": [{
                    "scopeSpans": [{"spans": spans_proto}]
                }]
            }).encode()
            req = urllib.request.Request(
                f"{self._otel_ep.rstrip('/')}/v1/traces",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST")
            urllib.request.urlopen(req, timeout=3)
        except Exception as e:
            log.debug("otel_export_failed", extra={"error": str(e)})

    def behavioral_drift(self, baseline_path: Path) -> float:
        """
        Compare current session metrics against a baseline session.
        Returns drift score 0.0 (identical) → 1.0 (completely different).

        Drift components (equally weighted):
          1. critic pass rate delta
          2. tool failure rate delta
          3. hallucination count delta (normalised)
        Drift >= 0.20 triggers a logged warning and an alert ProactiveEvent.
        Drift >= 0.35 is logged as critical.
        """
        DRIFT_WARN  = 0.20
        DRIFT_CRIT  = 0.35

        if not baseline_path.exists():
            return 0.0
        try:
            lines = baseline_path.read_text(encoding="utf-8").splitlines()
            if not lines:
                return 0.0
            baseline_spans = [json.loads(l) for l in lines if l.strip()]

            # Critic pass rate
            b_critic_pass = sum(1 for s in baseline_spans
                                if s.get("name","").startswith("critic")
                                and s.get("status") == "OK")
            b_critic_total = sum(1 for s in baseline_spans
                                 if s.get("name","").startswith("critic"))
            b_critic_rate  = b_critic_pass / max(b_critic_total, 1)
            c_critic_rate  = self.critic_passes / max(
                self.critic_passes + self.critic_failures, 1)

            # Tool failure rate
            b_tool_calls   = sum(1 for s in baseline_spans
                                 if s.get("name","").startswith("tool."))
            b_tool_fail    = sum(1 for s in baseline_spans
                                 if s.get("name","").startswith("tool.")
                                 and s.get("status") == "ERROR")
            b_tool_fail_rate = b_tool_fail / max(b_tool_calls, 1)
            c_tool_fail_rate = self.tool_failures / max(self.tool_calls, 1)

            # Hallucinations (normalised by output tokens)
            b_halluc = sum(1 for s in baseline_spans
                           if s.get("name","") == "verifier.claim"
                           and s.get("status") == "ERROR")
            b_tokens_out = sum(s.get("attributes",{}).get("tokens_out",0)
                                for s in baseline_spans
                                if isinstance(s.get("attributes",{}), dict))
            c_halluc_rate = self.hallucinations / max(self.total_tokens_out // 100, 1)
            b_halluc_rate = b_halluc / max(b_tokens_out // 100, 1)

            drift = (abs(b_critic_rate - c_critic_rate) +
                     abs(b_tool_fail_rate - c_tool_fail_rate) +
                     abs(b_halluc_rate - c_halluc_rate)) / 3.0

            if drift >= DRIFT_CRIT:
                log.error("behavioral_drift_critical",
                          extra={"drift": round(drift, 3),
                                 "critic_delta": round(abs(b_critic_rate-c_critic_rate),3),
                                 "tool_fail_delta": round(abs(b_tool_fail_rate-c_tool_fail_rate),3)})
            elif drift >= DRIFT_WARN:
                log.warning("behavioral_drift_warning",
                             extra={"drift": round(drift, 3)})

            return drift
        except Exception as _e:
            log.debug("behavioral_drift_error", extra={"error": str(_e)[:60]})
            return 0.0

    def drift_alert(self, baseline_path: Path,
                    channel_registry: "ChannelRegistry | None" = None) -> None:
        """
        Check drift and broadcast an alert via ChannelRegistry if drift is high.
        Called at session end by Agent.run_task().
        """
        drift = self.behavioral_drift(baseline_path)
        if drift >= 0.20:
            msg = (f"⚠ Essence Behavioral Drift Alert\n"
                   f"Session: {self._session_id}\n"
                   f"Drift score: {drift:.2f} "
                   f"(threshold 0.20)\n"
                   f"Critic pass rate: "
                   f"{self.critic_passes}/{self.critic_passes+self.critic_failures}\n"
                   f"Hallucinations: {self.hallucinations}")
            if channel_registry:
                channel_registry.broadcast(msg)
            else:
                log.warning("behavioral_drift_alert",
                            extra={"drift": round(drift, 3),
                                   "session": self._session_id})


# ══════════════════════════════════════════════════════════════════════════════


# ══════════════════════════════════════════════════════════════════════════════
