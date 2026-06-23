"""SemanticStateStore: entity-relation-attribute triple store."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403

# SEMANTIC STATE STORE
# ══════════════════════════════════════════════════════════════════════════════
# Replaces the flat "growing pile of text chunks" memory model.
# Stores knowledge as typed triples: (entity, relation, attribute, value, confidence).
# Supports conflict detection: when two facts have the same (entity, relation, attribute)
# but different values, the store flags a conflict for the consolidation agent.
#
# This is the "semantic state" tier in the 3-tier memory architecture:
#   Working memory (deque)  →  SemanticStateStore (triples)  →  Episodic timeline (JSONL)
#
# Usage:
#   sss = SemanticStateStore(workspace / "memory" / "semantic_state.json")
#   sss.assert_fact("user", "preference", "editor", "neovim", confidence=0.9)
#   sss.assert_fact("project", "status", "essence", "active", confidence=1.0)
#   facts = sss.query(entity="user")
#   conflicts = sss.conflicts()

@_dc.dataclass
class SemanticFact:
    """A single entity-relation-attribute-value fact with provenance (v29 domain-aware)."""
    entity:      str
    relation:    str
    attribute:   str
    value:       str
    confidence:  float = 1.0
    source:      str   = "inferred"   # "explicit" | "inferred" | "agent"
    ts:          float = _dc.field(default_factory=time.time)
    fact_id:     str   = _dc.field(default_factory=lambda: secrets.token_hex(6))

    # v29 Analytics Engine fields
    domain_lens: str | None = None
    finding_id:  str | None = None
    superseded_by: str | None = None

    def to_dict(self) -> dict:
        return _dc.asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "SemanticFact":
        return cls(**{k: v for k, v in d.items() if k in {
            "entity","relation","attribute","value","confidence","source","ts","fact_id"}})

    def key(self) -> tuple:
        """Conflict key: same entity+relation+attribute = potential conflict."""
        return (self.entity, self.relation, self.attribute)


class SemanticStateStore:
    """
    Structured entity-relation-attribute knowledge store.

    Thread-safe. Persists to a single JSON file with atomic writes.
    Conflict detection: assert_fact() returns True if a conflicting belief
    already existed (different value for the same key).

    The consolidation agent should call resolve_conflict() after choosing
    which value to retain.
    """

    def __init__(self, path: Path) -> None:
        self._path  = path
        self._facts: list[SemanticFact] = []
        self._lock  = threading.RLock()
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                self._facts = [SemanticFact.from_dict(d) for d in raw]
            except Exception as _e:
                log.debug("semantic_state_load_error", extra={"error": str(_e)[:120]})

    _MAX_FACTS    = int(os.environ.get("Essence_SSS_MAX_FACTS", "5000"))
    _PRUNE_CONF   = float(os.environ.get("Essence_SSS_PRUNE_CONF", "0.25"))
    _PRUNE_DAYS   = int(os.environ.get("Essence_SSS_PRUNE_DAYS", "30"))

    def _save(self) -> None:
        # Prune low-confidence stale facts before persisting
        cutoff_ts = time.time() - self._PRUNE_DAYS * 86400
        self._facts = [
            f for f in self._facts
            if not (f.confidence < self._PRUNE_CONF and f.ts < cutoff_ts)
        ]
        # Hard cap: keep top-confidence facts when over limit
        if len(self._facts) > self._MAX_FACTS:
            self._facts = sorted(self._facts,
                                  key=lambda f: (-f.confidence, -f.ts))[:self._MAX_FACTS]
        tmp = self._path.with_suffix(".tmp")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(
            json.dumps([f.to_dict() for f in self._facts], indent=2, default=str),
            encoding="utf-8")
        tmp.replace(self._path)

    def assert_fact(self, entity: str, relation: str, attribute: str,
                    value: str, confidence: float = 1.0,
                    source: str = "inferred") -> bool:
        """
        Add or update a fact. Returns True if a conflicting value existed.
        Lower-confidence assertions never overwrite higher-confidence ones.
        """
        with self._lock:
            conflict = False
            for f in self._facts:
                if f.key() == (entity, relation, attribute):
                    if f.value != value:
                        conflict = True
                        if confidence >= f.confidence:
                            # Update: higher-confidence assertion wins
                            f.value      = value
                            f.confidence = confidence
                            f.source     = source
                            f.ts         = time.time()
                    else:
                        # Same value — just bump confidence and recency
                        f.confidence = max(f.confidence, confidence)
                        f.ts         = time.time()
                    self._save()
                    return conflict
            # New fact
            self._facts.append(SemanticFact(
                entity=entity, relation=relation, attribute=attribute,
                value=value, confidence=confidence, source=source))
            self._save()
            return False

    def query(self, entity: str | None = None,
              relation: str | None = None,
              attribute: str | None = None) -> list[SemanticFact]:
        """Return facts matching the given filter dimensions."""
        with self._lock:
            results = []
            for f in self._facts:
                if entity    and f.entity    != entity:    continue
                if relation  and f.relation  != relation:  continue
                if attribute and f.attribute != attribute: continue
                results.append(f)
            return sorted(results, key=lambda f: f.ts, reverse=True)

    def conflicts(self) -> list[tuple[str, list[SemanticFact]]]:
        """
        Return groups of facts that share a key but differ in value.
        Each group is (conflict_key_str, [fact_a, fact_b, ...]).
        """
        with self._lock:
            from collections import defaultdict
            groups: dict[tuple, list[SemanticFact]] = defaultdict(list)
            for f in self._facts:
                groups[f.key()].append(f)
            return [
                (f"{k[0]}.{k[1]}.{k[2]}", facts)
                for k, facts in groups.items()
                if len({f.value for f in facts}) > 1
            ]

    def resolve_conflict(self, entity: str, relation: str, attribute: str,
                         keep_value: str) -> bool:
        """Remove all facts for this key except the one with keep_value."""
        with self._lock:
            before = len(self._facts)
            self._facts = [
                f for f in self._facts
                if not (f.key() == (entity, relation, attribute) and f.value != keep_value)
            ]
            changed = len(self._facts) < before
            if changed:
                self._save()
            return changed

    def to_prompt_block(self, max_facts: int = 40) -> str:
        """Render the top-confidence facts as a compact system-prompt block."""
        with self._lock:
            top = sorted(self._facts, key=lambda f: (-f.confidence, -f.ts))[:max_facts]
        if not top:
            return ""
        lines = ["[Semantic state]"]
        for f in top:
            conf = f"({f.confidence:.1f})" if f.confidence < 1.0 else ""
            lines.append(f"  {f.entity}.{f.relation}.{f.attribute} = {f.value}{conf}")
        return "\n".join(lines)

    def __len__(self) -> int:
        with self._lock:
            return len(self._facts)

    def assert_prism_finding(self, finding: Any) -> bool:
        """
        Convenience: assert a Analytics Engine Finding object as a semantic triple.
        Accepts any object with .category, .title, .description,
        .calibrated_confidence, and .source_layer attributes.
        Returns True if a conflicting value existed (same contract as assert_fact).
        """
        category   = getattr(finding, "category", "unknown")
        title      = getattr(finding, "title", "")
        desc       = getattr(finding, "description", "")[:500]
        confidence = float(getattr(finding, "calibrated_confidence", 0.5))
        layer      = getattr(finding, "source_layer", "unknown")
        finding_id = getattr(finding, "finding_id", "")
        conflict = self.assert_fact(
            entity=category,
            relation="finding",
            attribute=title[:120],
            value=desc,
            confidence=confidence,
            source=f"prism_{layer}",
        )
        # Also index by finding_id for fast lookup
        if finding_id:
            self.assert_fact(
                entity="prism_finding",
                relation="id",
                attribute=finding_id[:32],
                value=f"{category}:{title[:60]}",
                confidence=confidence,
                source=f"prism_{layer}",
            )
        return conflict


# ══════════════════════════════════════════════════════════════════════════════
