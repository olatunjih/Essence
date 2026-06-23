"""MemoryLifecycleManager — archival, expiry, and distillation policies.

Policies:
  EPISODIC:  keep last N episodes; archive older ones to episodic_archive.db
  SEMANTIC:  expire facts not accessed in > TTL_DAYS
  KG:        prune nodes with no edges after ORPHAN_DAYS

Wire into HeartbeatScheduler as a daily job.
"""
from __future__ import annotations
import logging, time
from pathlib import Path
from typing import Any

log = logging.getLogger("essence.memory.lifecycle")

_EPISODIC_KEEP   = 1000
_SEMANTIC_TTL    = 90
_KG_ORPHAN_DAYS  = 30


class MemoryLifecycleManager:
    def __init__(self, workspace: Path,
                 episodic_store: Any = None,
                 semantic_store: Any = None,
                 kg: Any = None) -> None:
        self._ws       = workspace
        self._episodic = episodic_store
        self._semantic = semantic_store
        self._kg       = kg

    def run_cycle(self) -> dict:
        results: dict[str, int] = {
            "episodes_archived": 0,
            "facts_expired":     0,
            "kg_nodes_pruned":   0,
        }
        results["episodes_archived"] = self._archive_episodes()
        results["facts_expired"]     = self._expire_semantic_facts()
        results["kg_nodes_pruned"]   = self._prune_kg_orphans()
        log.info("lifecycle_cycle_complete", extra=results)
        return results

    def _archive_episodes(self) -> int:
        if self._episodic is None:
            return 0
        try:
            total = self._episodic.count()
            if total <= _EPISODIC_KEEP:
                return 0
            overflow = total - _EPISODIC_KEEP
            self._episodic.archive_oldest(overflow)
            return overflow
        except Exception as exc:
            log.warning("lifecycle_episodic_error: %s", exc)
            return 0

    def _expire_semantic_facts(self) -> int:
        if self._semantic is None:
            return 0
        try:
            cutoff = time.time() - _SEMANTIC_TTL * 86400
            return self._semantic.expire_before(cutoff)
        except Exception as exc:
            log.warning("lifecycle_semantic_error: %s", exc)
            return 0

    def _prune_kg_orphans(self) -> int:
        if self._kg is None:
            return 0
        try:
            cutoff = time.time() - _KG_ORPHAN_DAYS * 86400
            connected = {e.src for e in self._kg._edges} | \
                        {e.dst for e in self._kg._edges}
            orphans = [nid for nid, n in self._kg._nodes.items()
                       if nid not in connected
                       and n.created_at < cutoff]
            for nid in orphans:
                del self._kg._nodes[nid]
            if orphans:
                self._kg._save()
            return len(orphans)
        except Exception as exc:
            log.warning("lifecycle_kg_error: %s", exc)
            return 0
