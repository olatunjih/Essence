"""Capability Discovery: progressive, hierarchical capability graph exploration.

CapabilityNode represents a node in the capability graph (domain, cluster, tool, skill, or
parameter). It is distinct from EntityProfile in the Analytics Engine: EntityProfile describes
a data entity discovered during statistical analysis, while CapabilityNode describes an agent
capability available for task planning. The two share no schema and must not be conflated.
"""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403

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
        """Return neighbour node IDs reachable via edges of the given type.
        Uses the graph reference when available; returns [] only when the
        scorer was constructed without a graph (backwards-compatible)."""
        if self.graph is None:
            return []
        return self.graph.get_neighbors(node_id, edge_type)

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
        except Exception:
            pass

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
    loop.evolve({"hit_rate": 0.5})
    assert s.weights["semantic"] > orig_weight
