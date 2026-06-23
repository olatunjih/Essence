"""AttentionManager — unified signal arbitration for the ESSENCE kernel.

Ranks competing signals from all autonomous engines and returns a prioritised
focus window for each kernel tick.

Signal sources: OpportunityEngine, CuriosityEngine, ResearchEngine,
                ProactiveEngine, IntentEvolutionEngine, GoalManager.

Focus policy:
  - At most MAX_FOCUS_SIGNALS signals per tick (default: 3)
  - Source "explicit" (user intent) always wins — PRIORITY_EXPLICIT
  - Temporal urgency: signals decay in priority as they age toward their TTL
  - Interrupt suppression: autonomous signals within SUPPRESS_WINDOW_S of a
    recent "explicit" signal are held back to avoid interrupting user flow
"""
from __future__ import annotations
import dataclasses
import logging
import threading
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("essence.attention.manager")

PRIORITY_EXPLICIT  = 1.0
SUPPRESS_WINDOW_S  = 30.0    # seconds after explicit signal to suppress autonomy
MAX_FOCUS_SIGNALS  = 3


@dataclasses.dataclass
class AttentionSignal:
    source:       str          # "opportunity"|"curiosity"|"research"|"proactive"|"explicit"|"autonomy"
    payload:      dict
    priority:     float        # 0.0 – 1.0 (before decay)
    submitted_at: float
    ttl_s:        float = 300.0
    suppressed:   bool  = False

    @property
    def effective_priority(self) -> float:
        """Priority adjusted for time-decay toward TTL."""
        if self.source == "explicit":
            return PRIORITY_EXPLICIT
        elapsed = time.time() - self.submitted_at
        if elapsed >= self.ttl_s:
            return 0.0
        decay = 1.0 - (elapsed / self.ttl_s)
        return self.priority * decay

    @property
    def is_expired(self) -> bool:
        return (time.time() - self.submitted_at) >= self.ttl_s


class AttentionManager:
    """
    Ranks competing signals and returns a prioritised focus window
    for each kernel tick.
    """

    def __init__(self, workspace: Path, max_signals: int = MAX_FOCUS_SIGNALS) -> None:
        self._workspace       = workspace
        self._max_signals     = max_signals
        self._pending:  list[AttentionSignal] = []
        self._lock            = threading.Lock()
        self._suppress_until  = 0.0     # suppress autonomy until this timestamp

    # ── public API ──────────────────────────────────────────────────────────

    def submit(self, signal: AttentionSignal) -> None:
        """Add a new signal to the pending queue."""
        with self._lock:
            # Explicit user intent: suppress autonomy and jump queue
            if signal.source == "explicit":
                self.suppress_until(time.time() + SUPPRESS_WINDOW_S)
                self._pending.insert(0, signal)
                log.debug("attention_signal_explicit_submitted",
                          extra={"priority": signal.priority})
            else:
                if time.time() < self._suppress_until:
                    signal.suppressed = True
                    log.debug("attention_signal_suppressed",
                              extra={"source": signal.source})
                self._pending.append(signal)

    def focus_window(self) -> list[AttentionSignal]:
        """
        Return up to max_signals non-expired, non-suppressed signals,
        sorted by effective_priority descending.
        Does NOT remove signals from the queue — call drain() to consume.
        """
        with self._lock:
            self._evict_expired()
            eligible = [
                s for s in self._pending
                if not s.suppressed and not s.is_expired
            ]
            eligible.sort(key=lambda s: s.effective_priority, reverse=True)
            return eligible[: self._max_signals]

    def suppress_until(self, ts: float) -> None:
        """Suppress all non-explicit signals until timestamp *ts*."""
        self._suppress_until = max(self._suppress_until, ts)
        log.debug("attention_suppress_until", extra={"until": ts})

    def drain(self) -> list[AttentionSignal]:
        """
        Remove and return the current focus window (consumed signals).
        Called once per tick after processing to clear the acted-upon signals.
        """
        with self._lock:
            self._evict_expired()
            eligible = [
                s for s in self._pending
                if not s.suppressed and not s.is_expired
            ]
            eligible.sort(key=lambda s: s.effective_priority, reverse=True)
            to_return = eligible[: self._max_signals]
            for s in to_return:
                try:
                    self._pending.remove(s)
                except ValueError:
                    pass
            return to_return

    def pending_count(self) -> int:
        with self._lock:
            self._evict_expired()
            return len(self._pending)

    # ── private ──────────────────────────────────────────────────────────────

    def _evict_expired(self) -> None:
        """Remove expired signals in-place (must be called with lock held)."""
        self._pending = [s for s in self._pending if not s.is_expired]
        # Re-evaluate suppressed flag in case suppress_until has elapsed
        if time.time() >= self._suppress_until:
            for s in self._pending:
                s.suppressed = False
