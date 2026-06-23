"""
Layer 1 — IntentRouter: 3-stage pipeline (Rules → ML → LLM fallback).

Produces a canonical Intent object consumed by the APDE Kernel's
ingest_capsule() and passed downstream to IntentCompressor.
"""
from __future__ import annotations

import dataclasses
import enum
import fnmatch
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("essence.routing.intent_router")


class IntentType(str, enum.Enum):
    ANALYSIS        = "analysis"
    PREDICTION      = "prediction"
    RESEARCH        = "research"
    SUMMARIZATION   = "summarization"
    CODE_GENERATION = "code_generation"
    DATA_RETRIEVAL  = "data_retrieval"
    COMPARISON      = "comparison"
    EXPLANATION     = "explanation"
    TASK_AUTOMATION = "task_automation"
    CREATIVE        = "creative"
    CLARIFICATION_NEEDED = "clarification_needed"
    CUSTOM          = "custom"


@dataclasses.dataclass
class NuanceContext:
    urgency:              str   = "normal"    # "low" | "normal" | "high" | "critical"
    detail_level:         str   = "standard"  # "brief" | "standard" | "detailed"
    confidence_preference: str  = "balanced"  # "fast" | "balanced" | "thorough"
    temporal_scope:       str   = "present"   # "historical" | "present" | "future"


@dataclasses.dataclass
class Intent:
    type:       IntentType
    params:     dict
    confidence: float
    raw_query:  str
    session_id: str
    nuance:     NuanceContext = dataclasses.field(default_factory=NuanceContext)
    resolved_at: float       = dataclasses.field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "type":       self.type.value,
            "params":     self.params,
            "confidence": self.confidence,
            "raw_query":  self.raw_query,
            "session_id": self.session_id,
            "nuance": dataclasses.asdict(self.nuance),
        }


# Default rule map: each IntentType → list of regex patterns
_DEFAULT_RULES: dict[IntentType, list[str]] = {
    IntentType.ANALYSIS:        [
        r"\banalyz[e|ing]\b", r"\banalysis\b", r"\bexamine\b", r"\binvestigat[e|ing]\b",
        r"\bbreak\s+down\b", r"\bdiagnos[e|ing]\b",
    ],
    IntentType.PREDICTION:      [
        r"\bpredict\b", r"\bforecast\b", r"\bprobabilit[y|ies]\b",
        r"\bwill\s+\w+\s+(?:go|rise|fall|move)\b", r"\bwin\s+probability\b",
        r"\bproject(?:ion|ed)?\b",
    ],
    IntentType.RESEARCH:        [
        r"\bresearch\b", r"\bfind\s+(?:info|information|papers?)\b",
        r"\bwhat\s+is\b", r"\bwho\s+is\b", r"\btell\s+me\s+about\b",
        r"\blook\s+up\b",
    ],
    IntentType.SUMMARIZATION:   [
        r"\bsummariz[e|ing]\b", r"\bsummary\b", r"\btl;?dr\b",
        r"\bkey\s+(?:points?|takeaways?)\b", r"\bbrief(?:ly)?\b",
    ],
    IntentType.CODE_GENERATION: [
        r"\bwrit[e|ing]\s+(?:a\s+)?(?:script|function|code|class)\b",
        r"\bgenerat[e|ing]\s+code\b", r"\bimplement\b",
        r"\bcode\s+(?:to|for|that)\b",
    ],
    IntentType.DATA_RETRIEVAL:  [
        r"\bfetch\b", r"\bget\s+(?:me\s+)?(?:the\s+)?data\b",
        r"\bretriev[e|ing]\b", r"\bquery\b", r"\bpull\s+data\b",
    ],
    IntentType.COMPARISON:      [
        r"\bcompar[e|ing]\b", r"\bvs\.?\b", r"\bversus\b",
        r"\bdifference\s+between\b", r"\bbetter\s+than\b",
    ],
    IntentType.EXPLANATION:     [
        r"\bexplain\b", r"\bhow\s+does\b", r"\bwhy\s+(?:does|is|did)\b",
        r"\bhelp\s+me\s+understand\b", r"\bwhat\s+does\s+\w+\s+mean\b",
    ],
    IntentType.TASK_AUTOMATION: [
        r"\bautomat[e|ing]\b", r"\bschedul[e|ing]\b",
        r"\brun\s+(?:every|daily|weekly)\b", r"\btrigger\b",
        r"\bwhen\s+\w+\s+happens?\b",
    ],
    IntentType.CREATIVE:        [
        r"\bwrit[e|ing]\s+(?:a\s+)?(?:poem|story|essay|blog)\b",
        r"\bcreate\s+(?:a\s+)?(?:poem|story|essay)\b", r"\bbrainstorm\b",
    ],
}


class IntentRouter:
    """
    3-stage intent routing pipeline: Rules → ML → LLM fallback.

    Stage 1 (Rules):  regex pattern matching, confidence=0.95, O(n) fast path.
    Stage 2 (ML):     zero-shot classification via sentence-transformers (optional).
    Stage 3 (LLM):    APDERouter compact classification prompt as last resort.

    Short-circuits when confidence ≥ 0.9.
    Falls back gracefully when each stage is unavailable.
    """

    def __init__(self,
                 llm_router: Any = None,
                 workspace: Path | None = None) -> None:
        self._router    = llm_router
        self._workspace = workspace
        self._rules     = self._load_rules(workspace)
        self._compiled  = self._compile_rules(self._rules)

    # ── Rule loading ──────────────────────────────────────────────────────────

    def _load_rules(self, workspace: Path | None) -> dict[IntentType, list[str]]:
        """Load rules from workspace config/intent_rules.yaml if present."""
        rules = dict(_DEFAULT_RULES)
        if workspace is None:
            return rules
        yaml_path = workspace / "config" / "intent_rules.yaml"
        if not yaml_path.exists():
            return rules
        try:
            import yaml  # type: ignore
            raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
            for key, patterns in raw.items():
                try:
                    it = IntentType(key)
                    rules[it] = list(patterns)
                except ValueError:
                    log.debug("intent_rules_unknown_type", extra={"key": key})
        except Exception as exc:
            log.warning("intent_rules_load_error", extra={"error": str(exc)[:120]})
        return rules

    def _compile_rules(self, rules: dict[IntentType, list[str]]) -> dict[IntentType, list[re.Pattern]]:
        compiled: dict[IntentType, list[re.Pattern]] = {}
        for intent_type, patterns in rules.items():
            compiled[intent_type] = [
                re.compile(p, re.IGNORECASE) for p in patterns
            ]
        return compiled

    # ── Stage 1: Rule-based matching ─────────────────────────────────────────

    def _rule_match(self, query: str) -> Intent | None:
        for intent_type, patterns in self._compiled.items():
            for pat in patterns:
                if pat.search(query):
                    return Intent(
                        type=intent_type,
                        params={},
                        confidence=0.95,
                        raw_query=query,
                        session_id="",
                        nuance=self._extract_nuance(query),
                    )
        return None

    # ── Stage 2: ML zero-shot classification (optional) ──────────────────────

    def _ml_classify(self, query: str) -> Intent | None:
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
            from sentence_transformers.util import cos_sim  # type: ignore
        except ImportError:
            return None

        try:
            labels = [it.value for it in IntentType if it not in (
                IntentType.CUSTOM, IntentType.CLARIFICATION_NEEDED)]
            model = SentenceTransformer("all-MiniLM-L6-v2")
            q_emb  = model.encode(query, convert_to_tensor=True)
            l_embs = model.encode(labels, convert_to_tensor=True)
            scores = cos_sim(q_emb, l_embs)[0].tolist()
            best_idx   = max(range(len(scores)), key=lambda i: scores[i])
            confidence = float(scores[best_idx])
            if confidence < 0.4:
                return None
            return Intent(
                type=IntentType(labels[best_idx]),
                params={},
                confidence=confidence,
                raw_query=query,
                session_id="",
                nuance=self._extract_nuance(query),
            )
        except Exception as exc:
            log.debug("ml_classify_error", extra={"error": str(exc)[:120]})
            return None

    # ── Stage 3: LLM fallback ────────────────────────────────────────────────

    def _llm_classify(self, query: str) -> Intent:
        if self._router is None:
            return Intent(
                type=IntentType.CUSTOM,
                params={},
                confidence=0.4,
                raw_query=query,
                session_id="",
                nuance=self._extract_nuance(query),
            )
        labels = [it.value for it in IntentType if it not in (
            IntentType.CUSTOM, IntentType.CLARIFICATION_NEEDED)]
        prompt = (
            f'Classify this user request into exactly one category.\n'
            f'Request: "{query}"\n'
            f'Categories: {json.dumps(labels)}\n'
            f'Respond with JSON only: {{"category": "<one of the categories>", '
            f'"confidence": <0.0-1.0>, "params": {{}}}}'
        )
        try:
            raw = self._router.complete(
                prompt=prompt,
                call_class="PLAN",
                max_tokens=128,
            )
            data = json.loads(raw.strip())
            return Intent(
                type=IntentType(data.get("category", "custom")),
                params=data.get("params", {}),
                confidence=float(data.get("confidence", 0.5)),
                raw_query=query,
                session_id="",
                nuance=self._extract_nuance(query),
            )
        except Exception as exc:
            log.warning("llm_classify_error", extra={"error": str(exc)[:120]})
            return Intent(
                type=IntentType.CUSTOM,
                params={},
                confidence=0.4,
                raw_query=query,
                session_id="",
                nuance=self._extract_nuance(query),
            )

    # ── Nuance extraction ─────────────────────────────────────────────────────

    def _extract_nuance(self, query: str) -> NuanceContext:
        q = query.lower()
        urgency = "normal"
        if any(w in q for w in ("urgent", "immediately", "asap", "now", "critical")):
            urgency = "high"
        elif any(w in q for w in ("when you can", "no rush", "eventually")):
            urgency = "low"

        detail = "standard"
        if any(w in q for w in ("detailed", "comprehensive", "thorough", "in-depth", "full")):
            detail = "detailed"
        elif any(w in q for w in ("brief", "quick", "short", "summary", "tldr")):
            detail = "brief"

        confidence_pref = "balanced"
        if any(w in q for w in ("accurate", "certain", "precise", "thorough")):
            confidence_pref = "thorough"
        elif any(w in q for w in ("fast", "quick", "rough", "approximate")):
            confidence_pref = "fast"

        temporal = "present"
        if any(w in q for w in ("historical", "past", "previously", "used to", "last year")):
            temporal = "historical"
        elif any(w in q for w in ("future", "will", "forecast", "predict", "tomorrow", "next year")):
            temporal = "future"

        return NuanceContext(
            urgency=urgency,
            detail_level=detail,
            confidence_preference=confidence_pref,
            temporal_scope=temporal,
        )

    # ── Public interface ──────────────────────────────────────────────────────

    async def route(self, query: str, session_id: str = "") -> Intent:
        """
        Orchestrate the 3-stage classification pipeline.
        Short-circuits on confidence ≥ 0.9.
        Returns CLARIFICATION_NEEDED intent when confidence < 0.5.
        """
        # Stage 1: Rules
        intent = self._rule_match(query)
        if intent is not None and intent.confidence >= 0.9:
            intent.session_id = session_id
            log.debug("intent_routed_rules",
                      extra={"type": intent.type.value, "conf": intent.confidence})
            return intent

        # Stage 2: ML
        ml_intent = self._ml_classify(query)
        if ml_intent is not None and ml_intent.confidence >= 0.9:
            ml_intent.session_id = session_id
            log.debug("intent_routed_ml",
                      extra={"type": ml_intent.type.value, "conf": ml_intent.confidence})
            return ml_intent

        # Pick best from stages 1 & 2 so far
        candidates = [i for i in [intent, ml_intent] if i is not None]
        if candidates:
            best = max(candidates, key=lambda i: i.confidence)
            if best.confidence >= 0.9:
                best.session_id = session_id
                return best
        else:
            best = None

        # Stage 3: LLM fallback
        llm_intent = self._llm_classify(query)
        llm_intent.session_id = session_id

        # Merge: pick highest confidence overall
        all_intents = [i for i in [best, llm_intent] if i is not None]
        final = max(all_intents, key=lambda i: i.confidence)
        final.session_id = session_id

        # Clarification needed when confidence is too low
        if final.confidence < 0.5:
            suggestions: list[str] = []
            if self._router is not None:
                try:
                    raw_sugg = self._router.complete(
                        prompt=(
                            f'User said: "{query}"\n'
                            'Suggest 3 short, specific action phrases. '
                            'Respond with a JSON array only, e.g. '
                            '["phrase 1", "phrase 2", "phrase 3"]'
                        ),
                        call_class="PLAN",
                        max_tokens=128,
                    )
                    suggestions = json.loads(raw_sugg.strip())
                except Exception:
                    suggestions = []
            return Intent(
                type=IntentType.CLARIFICATION_NEEDED,
                params={"options": suggestions[:3], "original_query": query},
                confidence=0.0,
                raw_query=query,
                session_id=session_id,
                nuance=self._extract_nuance(query),
            )

        log.debug("intent_routed_llm",
                  extra={"type": final.type.value, "conf": final.confidence})
        return final
