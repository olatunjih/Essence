"""MetaReflectionEngine — extracts generalisable insight from successful episodes.

Questions: M1 why did it work, M2 generalisation score,
           M3 reusable capability flag, M4 cross-subsystem transfer.
Outputs: <workspace>/logs/meta_reflections.jsonl + PersonalTwin.decision_patterns
"""
from __future__ import annotations
import json, logging, time
from pathlib import Path
from typing import Any

log = logging.getLogger("essence.skills.evolution.meta_reflect")


class MetaReflectionEngine:
    def __init__(self, workspace: Path, twin: Any = None) -> None:
        self._log_path = workspace / "logs" / "meta_reflections.jsonl"
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        self._twin = twin

    def reflect(self, episode: dict) -> dict:
        skill  = episode.get("skill", "unknown")
        result = episode.get("result_summary", "")
        m1 = self._why_did_it_work(skill, result)
        m2 = self._generalisation_score(skill, result)
        m3 = m2 >= 0.6
        m4 = self._cross_subsystem_candidate(skill)
        reflection = {
            "episode_id":         episode.get("episode_id"),
            "skill":              skill,
            "M1_success_factor":  m1,
            "M2_generalisation":  m2,
            "M3_extract_skill":   m3,
            "M4_cross_subsystem": m4,
            "reflected_at":       time.time(),
        }
        with self._log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(reflection) + "\n")
        if self._twin and m3:
            self._twin.update("decision_patterns", f"auto_{skill}", m1)
        log.info("meta_reflection: %s | gen=%.2f | extract=%s | cross=%s",
                 skill, m2, m3, m4)
        return reflection

    def _why_did_it_work(self, skill: str, result: str) -> str:
        if "fast" in result.lower():
            return f"{skill}: succeeded due to low-latency execution"
        if "accurate" in result.lower() or "correct" in result.lower():
            return f"{skill}: succeeded due to high precision"
        return f"{skill}: succeeded — no dominant factor identified"

    def _generalisation_score(self, skill: str, result: str) -> float:
        generic = ("summarization", "code_review", "research", "planning")
        return 0.8 if any(g in skill for g in generic) else 0.4

    def _cross_subsystem_candidate(self, skill: str) -> str | None:
        transfer_map = {
            "data_analysis": "analytics.layers",
            "code_review":   "agents.critic",
            "planning":      "agents.planning",
            "research":      "autonomy.curiosity_engine",
        }
        for key, target in transfer_map.items():
            if key in skill:
                return target
        return None
