"""
Signal provenance tracking.

Every ExecResult artifact carries a DataProvenance so the audit trail records
not just what was produced but where it came from and what type of signal it is.

SignalType:
  HARD — validated, structured event data (primary model input)
  SOFT — noisy, unstructured opinion data (optional overlay only)
"""
from __future__ import annotations

import dataclasses
import enum
import time
from typing import Any


class SignalType(str, enum.Enum):
    HARD = "hard"   # Validated, structured event data — primary model input
    SOFT = "soft"   # Noisy, unstructured opinion data — optional overlay only


@dataclasses.dataclass
class DataProvenance:
    """
    Tracks the origin, source type, and transformation history of data.

    Attached to every ExecResult artifact so the audit trail records not just
    what was produced but where it came from and through what transformations.
    """
    signal_type:        SignalType
    source:             str
    fetched_at:         float = dataclasses.field(default_factory=time.time)
    validation_status:  str  = "passed"   # "passed" | "partial" | "failed"
    record_count:       int  = 0
    confidence_score:   float | None = None   # SOFT signals only
    lineage:            list[str] = dataclasses.field(default_factory=list)
    adapter_version:    str | None = None

    def to_dict(self) -> dict:
        return {
            "signal_type":       self.signal_type.value,
            "source":            self.source,
            "fetched_at":        self.fetched_at,
            "validation_status": self.validation_status,
            "record_count":      self.record_count,
            "confidence_score":  self.confidence_score,
            "lineage":           self.lineage,
            "adapter_version":   self.adapter_version,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "DataProvenance":
        return cls(
            signal_type=SignalType(d.get("signal_type", "hard")),
            source=d.get("source", "unknown"),
            fetched_at=d.get("fetched_at", time.time()),
            validation_status=d.get("validation_status", "passed"),
            record_count=int(d.get("record_count", 0)),
            confidence_score=d.get("confidence_score"),
            lineage=list(d.get("lineage", [])),
            adapter_version=d.get("adapter_version"),
        )

    def add_lineage(self, step: str) -> "DataProvenance":
        """Return a new DataProvenance with an additional lineage entry."""
        return dataclasses.replace(self, lineage=[*self.lineage, step])
