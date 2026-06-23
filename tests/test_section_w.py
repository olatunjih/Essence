"""Essence unit tests."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403
from essence import *  # noqa: F401,F403  [auto-fix: tests never imported the assembled package]

import pytest  # type: ignore
from essence._shared import *  # noqa

# ──   v28.1 OPENCANVAS TESTS ─────────────────────────────────────────────

def test_oc_type_from_lang():
    "from_lang maps known languages to correct types."
    assert _OCType.from_lang("mermaid") == _OCType.DIAGRAM
    assert _OCType.from_lang("python") == _OCType.CODE
    assert _OCType.from_lang("json") == _OCType.JSON
    assert _OCType.from_lang("html") == _OCType.HTML
    assert _OCType.from_lang("csv") == _OCType.TABLE
    assert _OCType.from_lang("unknown_lang") == _OCType.CODE

def test_oc_detector_extracts_fenced_code():
    "OCArtifactDetector extracts fenced code blocks."
    text = "Here is code:\n```python\nprint('hello')\n```\nDone."
    cleaned, arts = OCArtifactDetector.detect(text, "s1")
    assert len(arts) == 1
    assert arts[0].artifact_type == _OCType.CODE
    assert arts[0].language == "python"
    assert "print" in arts[0].content
    assert "[canvas:" in cleaned

def test_oc_detector_extracts_mermaid():
    "OCArtifactDetector detects mermaid diagrams."
    text = "```mermaid\ngraph TD\n  A-->B\n```"
    _, arts = OCArtifactDetector.detect(text)
    assert len(arts) == 1
    assert arts[0].artifact_type == _OCType.DIAGRAM

def test_oc_detector_no_artifacts_in_plain_text():
    "Plain text with no code blocks returns empty artifacts."
    text = "This is just a plain text response with no special content."
    cleaned, arts = OCArtifactDetector.detect(text)
    assert len(arts) == 0
    assert cleaned == text

def test_oc_store_add_and_list(tmp_path):
    "OCArtifactStore round-trips add + list_session."
    store = OCArtifactStore(tmp_path)
    art = OCArtifact(session_id="s1", artifact_type=_OCType.CODE,
                     title="test", content="print(1)")
    store.add(art)
    listed = store.list_session("s1")
    assert len(listed) == 1
    assert listed[0].content == "print(1)"

def test_oc_store_delete(tmp_path):
    "OCArtifactStore.delete() removes artifact."
    store = OCArtifactStore(tmp_path)
    art = OCArtifact(session_id="s1", content="x")
    store.add(art)
    assert store.delete(art.artifact_id)
    assert len(store.list_session("s1")) == 0

def test_oc_store_clear_session(tmp_path):
    "OCArtifactStore.clear_session() removes all session artifacts."
    store = OCArtifactStore(tmp_path)
    for i in range(5):
        store.add(OCArtifact(session_id="s2", content=f"item{i}"))
    n = store.clear_session("s2")
    assert n == 5
    assert len(store.list_session("s2")) == 0

def test_oc_store_persists(tmp_path):
    "OCArtifactStore persists to disk and reloads."
    store1 = OCArtifactStore(tmp_path)
    store1.add(OCArtifact(session_id="s1", content="persistent"))
    store2 = OCArtifactStore(tmp_path)
    assert len(store2.list_session("s1")) == 1
    assert store2.list_session("s1")[0].content == "persistent"

def test_oc_store_caps_at_100(tmp_path):
    "OCArtifactStore caps at 100 artifacts per session."
    store = OCArtifactStore(tmp_path)
    for i in range(110):
        store.add(OCArtifact(session_id="s1", content=f"art{i}"))
    assert len(store.list_session("s1")) <= 100

def test_oc_detector_multiple_blocks():
    "OCArtifactDetector handles multiple code blocks in one response."
    text = "```python\nprint(1)\n```\ntext\n```json\n{\"a\": 1}\n```"
    _, arts = OCArtifactDetector.detect(text)
    assert len(arts) == 2
    types = {a.artifact_type for a in arts}
    assert _OCType.CODE in types
    assert _OCType.JSON in types

if __name__ == "__main__":
    main()

# ══════════════════════════════════════════════════════════════════════════════
# Capability Discovery — Recursive Hierarchical Progressive Discovery
# ══════════════════════════════════════════════════════════════════════════════

class CapabilityNodeType(_enum.Enum):
    DOMAIN    = "D"
    CLUSTER   = "C"
    TOOL      = "T"
    SKILL     = "S"
    PARAMETER = "P"
    PHANTOM   = "Φ"

class CapabilityEdgeType(_enum.Enum):
    CONTAINS     = "contains"
    REQUIRES     = "requires"
    COMPOSES     = "composes"
    CONFLICTS    = "conflicts"
    ENHANCES     = "enhances"
    PHANTOM_LINK = "phantom_link"

class ResolutionLevel(_enum.IntEnum):
    L1_SILHOUETTE   = 1
    L2_CONTOUR      = 2
    L3_BLUEPRINT    = 3
    L4_SCHEMATIC    = 4
    L5_EXPERIENTIAL = 5

@_dc.dataclass
class CapabilityNode:
    node_id: str
    node_type: CapabilityNodeType
    metadata: dict[int, dict[str, Any]] = _dc.field(default_factory=dict)
    usage_freq: int = 0
    recency: float = 0.0
    success_rate: float = 0.0
    confidence: float = 1.0
    safety_clearance: int = 0

@_dc.dataclass
class CapabilityEdge:
    source_id: str
    target_id: str
    edge_type: CapabilityEdgeType
    weight: float = 1.0

class CapabilityGraph:
    def __init__(self):
        self.nodes: dict[str, CapabilityNode] = {}
        self.edges: list[CapabilityEdge] = []
        self._lock = threading.RLock()

    def graft(self, node: CapabilityNode):
        with self._lock: self.nodes[node.node_id] = node

    def link(self, source_id: str, target_id: str, edge_type: CapabilityEdgeType, weight: float = 1.0):
        with self._lock: self.edges.append(CapabilityEdge(source_id, target_id, edge_type, weight))

    def get_neighbors(self, node_id: str, edge_type: CapabilityEdgeType = None) -> list[str]:
        with self._lock:
            return [e.target_id for e in self.edges if e.source_id == node_id and (edge_type is None or e.edge_type == edge_type)]

class RelevanceScorer:
    def __init__(self, workspace: Path, graph: CapabilityGraph = None):
        self.workspace = workspace
        self.graph = graph
        self.workspace = workspace
        self.weights = {
            "semantic": 0.3, "plan": 0.2, "co_occurrence": 0.1,
            "recency": 0.1, "success": 0.1, "dependency": 0.1,
            "novelty": 0.05, "safety": 1.0
        }

    def score(self, node: CapabilityNode, context: dict) -> float:
        s1 = context.get("semantic_affinity", {}).get(node.node_id, 0.5)
        s2 = 1.0 if node.node_id in context.get("plan_refs", []) else 0.0
        s3 = context.get("co_occurrence", {}).get(node.node_id, 0.0)
        s4 = 1.0 / (1.0 + (time.time() - node.recency) / 3600) if node.recency > 0 else 0.0
        s5 = node.success_rate
        s6 = 1.0 if any(nid in context.get("active_nodes", []) for nid in self.get_neighbors_of_type(node.node_id, CapabilityEdgeType.REQUIRES)) else 0.0
        s7 = 1.0 / (1.0 + node.usage_freq)
        s8 = 1.0 if node.safety_clearance >= 0 else 0.0

        raw_score = (self.weights["semantic"] * s1 + self.weights["plan"] * s2 +
                     self.weights["co_occurrence"] * s3 + self.weights["recency"] * s4 +
                     self.weights["success"] * s5 + self.weights["dependency"] * s6 +
                     self.weights["novelty"] * s7)
        return raw_score * s8

    def get_neighbors_of_type(self, node_id: str, edge_type: CapabilityEdgeType) -> list[str]:
        # Helper if graph ref available, otherwise empty
        return []

class ProjectionFunction:
    def __init__(self, graph: CapabilityGraph, scorer: RelevanceScorer):
        self.graph = graph; self.scorer = scorer

    def project(self, context: dict, budget: int) -> dict[str, ResolutionLevel]:
        scored = []
        for nid, node in self.graph.nodes.items():
            scored.append((nid, self.scorer.score(node, context)))
        scored.sort(key=lambda x: x[1], reverse=True)

        res = {}; used = 0
        for nid, score in scored:
            if score > 0.8: target_lvl = ResolutionLevel.L4_SCHEMATIC
            elif score > 0.6: target_lvl = ResolutionLevel.L3_BLUEPRINT
            elif score > 0.4: target_lvl = ResolutionLevel.L2_CONTOUR
            else: target_lvl = ResolutionLevel.L1_SILHOUETTE

            cost = {1:10, 2:50, 3:150, 4:500, 5:1000}.get(target_lvl.value, 10)
            if used + cost <= budget:
                res[nid] = target_lvl
                used += cost
            else:
                res[nid] = ResolutionLevel.L1_SILHOUETTE
        return res


class AnticipationEngine:
    def __init__(self, graph: CapabilityGraph): self.graph = graph
    def predict(self, context: dict) -> list[tuple[str, ResolutionLevel, float]]:
        preds = []
        if "plan_steps" in context:
            for s in context["plan_steps"]:
                if hasattr(s, "tool") and s.tool in self.graph.nodes: preds.append((s.tool, ResolutionLevel.L3_BLUEPRINT, 0.9))
        return preds

class CompositionSynthesiser:
    def __init__(self, graph: CapabilityGraph): self.graph = graph
    def scan_traces(self, traces: list):
        if not traces: return
        self.graph.graft(CapabilityNode("skill_synthesised", CapabilityNodeType.PHANTOM))

class ExplorationExploitationController:
    def __init__(self, graph: CapabilityGraph): self.graph = graph
    def ucb_d(self, node_id: str, total_loads: int) -> float:
        node = self.graph.nodes.get(node_id)
        if not node or node.usage_freq == 0: return float("inf")
        import math
        return node.success_rate + 2.0 * math.sqrt(math.log(total_loads + 1) / (node.usage_freq + 1))

class SelfEvolutionLoop:
    def __init__(self, scorer: RelevanceScorer, anticipation: AnticipationEngine, workspace: Path):
        self.scorer = scorer; self.anticipation = anticipation; self.workspace = workspace
        self.log_path = workspace / "logs" / "discovery_evolution.jsonl"
    def evolve(self, metrics: dict):
        hit_rate = metrics.get("hit_rate", 0.0)
        if hit_rate < 0.8: self.scorer.weights["semantic"] *= 1.1
        try:
            self.log_path.parent.mkdir(exist_ok=True)
            with open(self.log_path, "a") as f: f.write(json.dumps({"ts":time.time(), "metrics":metrics, "weights":self.scorer.weights}) + "\n")
        except: pass

# ──  Capability Discovery TESTS ──────────────────────────────────────────────────────────

def test_rhpd_projection():
    g = CapabilityGraph(); s = RelevanceScorer(Path("/tmp"), graph=g)
    s.weights["plan"] = 0.8
    g.graft(CapabilityNode("t1", CapabilityNodeType.TOOL, safety_clearance=1))
    p = ProjectionFunction(g, s)
    res = p.project({"plan_refs": ["t1"]}, 1000)
    assert res["t1"] == ResolutionLevel.L4_SCHEMATIC

def test_rhpd_self_evolution_loop(tmp_path):
    s = RelevanceScorer(tmp_path)
    ae = AnticipationEngine(CapabilityGraph())
    loop = SelfEvolutionLoop(s, ae, tmp_path)
    orig_weight = s.weights["semantic"]
