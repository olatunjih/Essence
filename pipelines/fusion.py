"""
FusionEngine — fuses HARD and SOFT signal predictions.

Applies a maximum ±5% probability adjustment from soft (sentiment) signals,
only when soft signal meets minimum confidence and historical correlation
thresholds.

This is the correct place to combine HARD and SOFT signals — NOT inside
individual skills. Skills produce single-signal outputs; FusionEngine
combines them downstream with explicit provenance tracking.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from .provenance import DataProvenance, SignalType

log = logging.getLogger("essence.pipelines.fusion")


class FusionEngine:
    """
    Fuses a HARD-signal prediction with an optional SOFT-signal sentiment overlay.

    Rules:
    - Soft signal is ignored if its confidence < MIN_SOFT_CONFIDENCE.
    - Soft signal is ignored if its historical correlation < MIN_SOFT_CORRELATION.
    - Maximum probability adjustment from soft signal: ±MAX_SOFT_ADJUSTMENT (5%).
    - Output is capped to [0.05, 0.95] to avoid probability extremes.
    """

    MAX_SOFT_ADJUSTMENT  = 0.05
    MIN_SOFT_CONFIDENCE  = 0.3
    MIN_SOFT_CORRELATION = 0.15

    def fuse(self,
             hard_prob:        float,
             soft_score:       float | None = None,
             soft_confidence:  float = 0.0,
             soft_correlation: float = 0.0,
             hard_provenance:  DataProvenance | None = None,
             soft_provenance:  DataProvenance | None = None,
             ) -> dict:
        """
        Fuse a hard-signal probability with an optional soft-signal overlay.

        Args:
            hard_prob:         Core probability from the HARD signal model [0, 1].
            soft_score:        Sentiment/opinion score [-1, 1] (None to skip).
            soft_confidence:   Confidence level of the soft signal [0, 1].
            soft_correlation:  Historical correlation of soft signal with outcomes.
            hard_provenance:   DataProvenance for the HARD signal.
            soft_provenance:   DataProvenance for the SOFT signal.

        Returns:
            dict with keys: probability, hard_contribution, soft_contribution,
                            soft_applied, provenance_chain, fused_at.
        """
        hard_prob = float(max(0.0, min(1.0, hard_prob)))

        soft_applied     = False
        soft_contribution = 0.0
        reject_reason    = ""

        if soft_score is not None:
            if soft_confidence < self.MIN_SOFT_CONFIDENCE:
                reject_reason = (
                    f"soft_confidence={soft_confidence:.3f} < "
                    f"MIN={self.MIN_SOFT_CONFIDENCE}"
                )
            elif soft_correlation < self.MIN_SOFT_CORRELATION:
                reject_reason = (
                    f"soft_correlation={soft_correlation:.3f} < "
                    f"MIN={self.MIN_SOFT_CORRELATION}"
                )
            else:
                # Weight the soft adjustment by confidence × correlation
                weight  = soft_confidence * soft_correlation
                raw_adj = float(soft_score) * weight * self.MAX_SOFT_ADJUSTMENT
                clamped = max(-self.MAX_SOFT_ADJUSTMENT,
                              min(self.MAX_SOFT_ADJUSTMENT, raw_adj))
                soft_contribution = clamped
                soft_applied      = True

        fused = hard_prob + soft_contribution
        fused = max(0.05, min(0.95, fused))

        provenance_chain: list[dict] = []
        if hard_provenance:
            provenance_chain.append(hard_provenance.to_dict())
        if soft_provenance and soft_applied:
            provenance_chain.append(soft_provenance.to_dict())

        result = {
            "probability":       round(fused, 6),
            "hard_contribution": round(hard_prob, 6),
            "soft_contribution": round(soft_contribution, 6),
            "soft_applied":      soft_applied,
            "reject_reason":     reject_reason if not soft_applied else "",
            "provenance_chain":  provenance_chain,
            "fused_at":          time.time(),
        }

        log.debug("fusion_result",
                  extra={
                      "hard":       hard_prob,
                      "soft_adj":   soft_contribution,
                      "fused":      fused,
                      "soft_applied": soft_applied,
                  })
        return result

    def fuse_batch(self, predictions: list[dict]) -> list[dict]:
        """
        Fuse a batch of predictions.

        Each dict must contain: hard_prob (float), optionally soft_score,
        soft_confidence, soft_correlation.
        """
        return [
            self.fuse(
                hard_prob=p["hard_prob"],
                soft_score=p.get("soft_score"),
                soft_confidence=float(p.get("soft_confidence", 0.0)),
                soft_correlation=float(p.get("soft_correlation", 0.0)),
            )
            for p in predictions
        ]
