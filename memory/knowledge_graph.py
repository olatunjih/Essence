"""PersonalKnowledgeGraph — typed entity-relationship graph.

Entity types: Person | Project | Tool | Concept | Place | Event
Edge types: WORKS_ON | USES | KNOWS | PART_OF | RELATED_TO | OWNS | ATTENDED
Persistence: <workspace>/memory/kg.json
"""
from __future__ import annotations
import json, time
from pathlib import Path


class KGNode:
    __slots__ = ("id", "type", "label", "attrs", "created_at")

    def __init__(self, id: str, type: str, label: str,
                 attrs: dict | None = None) -> None:
        self.id = id
        self.type = type
        self.label = label
        self.attrs = attrs or {}
        self.created_at = time.time()


class KGEdge:
    __slots__ = ("src", "dst", "rel", "weight", "created_at")

    def __init__(self, src: str, dst: str,
                 rel: str, weight: float = 1.0) -> None:
        self.src = src
        self.dst = dst
        self.rel = rel
        self.weight = weight
        self.created_at = time.time()


class PersonalKnowledgeGraph:
    def __init__(self, workspace: Path) -> None:
        self._path  = workspace / "memory" / "kg.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._nodes: dict[str, KGNode] = {}
        self._edges: list[KGEdge]      = []
        self._load()

    def add_node(self, id: str, type: str, label: str, **attrs) -> KGNode:
        if id not in self._nodes:
            self._nodes[id] = KGNode(id, type, label, attrs)
            self._save()
        return self._nodes[id]

    def add_edge(self, src: str, dst: str,
                 rel: str, weight: float = 1.0) -> None:
        self._edges.append(KGEdge(src, dst, rel, weight))
        self._save()

    def neighbors(self, node_id: str,
                  rel: str | None = None) -> list[KGNode]:
        ids = {e.dst for e in self._edges
               if e.src == node_id and (rel is None or e.rel == rel)}
        return [self._nodes[i] for i in ids if i in self._nodes]

    def context_for(self, node_id: str) -> str:
        node = self._nodes.get(node_id)
        if not node:
            return ""
        lines = [f"{node.type}:{node.label}"]
        for e in self._edges:
            if e.src == node_id:
                dst = self._nodes.get(e.dst)
                if dst:
                    lines.append(f"  \u2013[{e.rel}]\u2192 {dst.type}:{dst.label}")
        return "\n".join(lines)

    def _save(self) -> None:
        payload = {
            "nodes": {k: {"type": n.type, "label": n.label, "attrs": n.attrs}
                      for k, n in self._nodes.items()},
            "edges": [{"src": e.src, "dst": e.dst, "rel": e.rel, "w": e.weight}
                      for e in self._edges],
        }
        self._path.write_text(json.dumps(payload, indent=2))

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            d = json.loads(self._path.read_text())
            for k, v in d.get("nodes", {}).items():
                self._nodes[k] = KGNode(k, v["type"], v["label"],
                                        v.get("attrs", {}))
            for e in d.get("edges", []):
                self._edges.append(KGEdge(e["src"], e["dst"],
                                          e["rel"], e.get("w", 1.0)))
        except Exception:
            pass
