"""
Layer 2 — TaskRouter: converts an Intent into a workflow DAG.

Templates are loaded from <workspace>/config/workflow_templates.yaml (if
present) and fall back to inline defaults so the system works out of the box.
"""
from __future__ import annotations

import dataclasses
import logging
import re
from pathlib import Path
from typing import Any

log = logging.getLogger("essence.routing.task_router")


@dataclasses.dataclass
class WorkflowNode:
    """A single node in a workflow template DAG."""
    id:         str
    skill:      str
    params:     dict = dataclasses.field(default_factory=dict)
    depends_on: list[str] = dataclasses.field(default_factory=list)
    condition:  str | None = None   # Python expression evaluated against intent.params


# ── Default templates ─────────────────────────────────────────────────────────

_DEFAULT_TEMPLATES: dict[str, list[WorkflowNode]] = {
    "analysis": [
        WorkflowNode(id="fetch",    skill="data_retrieval"),
        WorkflowNode(id="analyze",  skill="analysis",    depends_on=["fetch"]),
        WorkflowNode(id="explain",  skill="explanation", depends_on=["analyze"]),
    ],
    "prediction": [
        WorkflowNode(id="fetch_hard", skill="data_retrieval",
                     params={"signal_type": "hard"}),
        WorkflowNode(id="fetch_soft", skill="sentiment_analysis",
                     params={"signal_type": "soft"}),
        WorkflowNode(id="predict",    skill="prediction",
                     depends_on=["fetch_hard", "fetch_soft"]),
        WorkflowNode(id="explain",    skill="explanation", depends_on=["predict"]),
    ],
    "research": [
        WorkflowNode(id="search",     skill="web_search"),
        WorkflowNode(id="summarize",  skill="summarization", depends_on=["search"]),
    ],
    "summarization": [
        WorkflowNode(id="summarize",  skill="summarization"),
    ],
    "code_generation": [
        WorkflowNode(id="plan_code",  skill="code_planner"),
        WorkflowNode(id="generate",   skill="code_generation", depends_on=["plan_code"]),
        WorkflowNode(id="validate",   skill="code_validator",  depends_on=["generate"]),
    ],
    "data_retrieval": [
        WorkflowNode(id="fetch",      skill="data_retrieval"),
        WorkflowNode(id="format",     skill="data_formatter",  depends_on=["fetch"]),
    ],
    "comparison": [
        WorkflowNode(id="fetch_a",    skill="data_retrieval",
                     params={"entity": "{{entity_a}}"}),
        WorkflowNode(id="fetch_b",    skill="data_retrieval",
                     params={"entity": "{{entity_b}}"}),
        WorkflowNode(id="compare",    skill="comparison",
                     depends_on=["fetch_a", "fetch_b"]),
    ],
    "explanation": [
        WorkflowNode(id="explain",    skill="explanation"),
    ],
    "task_automation": [
        WorkflowNode(id="plan",       skill="task_planner"),
        WorkflowNode(id="execute",    skill="task_automation", depends_on=["plan"]),
        WorkflowNode(id="verify",     skill="result_verifier",  depends_on=["execute"]),
    ],
    "creative": [
        WorkflowNode(id="generate",   skill="creative_writing"),
    ],
    "custom": [
        WorkflowNode(id="process",    skill="general_purpose"),
    ],
}


def _resolve_params(params: dict, ctx: dict) -> dict:
    """Template parameter substitution: {{key}} → ctx[key]."""
    out: dict = {}
    for k, v in params.items():
        if isinstance(v, str):
            for placeholder, value in ctx.items():
                v = v.replace(f"{{{{{placeholder}}}}}", str(value))
        out[k] = v
    return out


def _eval_condition(condition: str, ctx: dict) -> bool:
    """Safely evaluate a condition expression using an AST walker (no eval/exec)."""
    import ast
    import operator as _op

    _OPS = {
        ast.And:    lambda a, b: a and b,
        ast.Or:     lambda a, b: a or b,
        ast.Eq:     _op.eq,
        ast.NotEq:  _op.ne,
        ast.Lt:     _op.lt,
        ast.LtE:    _op.le,
        ast.Gt:     _op.gt,
        ast.GtE:    _op.ge,
        ast.In:     lambda a, b: a in b,
        ast.NotIn:  lambda a, b: a not in b,
    }

    def _ev(node: ast.expr):  # type: ignore[return]
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            return ctx.get(node.id)
        if isinstance(node, ast.BoolOp):
            values = [_ev(v) for v in node.values]
            fn = _OPS[type(node.op)]
            result = values[0]
            for val in values[1:]:
                result = fn(result, val)
            return result
        if isinstance(node, ast.Compare):
            left = _ev(node.left)
            for op_node, right_node in zip(node.ops, node.comparators):
                right = _ev(right_node)
                if not _OPS[type(op_node)](left, right):
                    return False
                left = right
            return True
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return not _ev(node.operand)
        raise ValueError(f"unsupported node: {ast.dump(node)}")

    try:
        tree = ast.parse(condition, mode="eval")
        return bool(_ev(tree.body))
    except Exception:
        return True  # fail open — skip condition, include node


class TaskRouter:
    """
    Converts an Intent into a dependency DAG (networkx.DiGraph).

    Templates are YAML-configurable at <workspace>/config/workflow_templates.yaml.
    Falls back to inline defaults when the file is absent.
    """

    def __init__(self, workspace: Path | None = None) -> None:
        self._workspace  = workspace
        self._templates  = self._load_templates(workspace)

    def _load_templates(self, workspace: Path | None) -> dict[str, list[WorkflowNode]]:
        templates = {k: list(v) for k, v in _DEFAULT_TEMPLATES.items()}
        if workspace is None:
            return templates
        yaml_path = workspace / "config" / "workflow_templates.yaml"
        if not yaml_path.exists():
            return templates
        try:
            import yaml  # type: ignore
            raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
            for intent_type, nodes in raw.items():
                wf_nodes = [
                    WorkflowNode(
                        id=n["id"],
                        skill=n["skill"],
                        params=n.get("params", {}),
                        depends_on=n.get("depends_on", []),
                        condition=n.get("condition"),
                    )
                    for n in (nodes or [])
                ]
                templates[intent_type] = wf_nodes
            log.info("task_router_templates_loaded",
                     extra={"path": str(yaml_path), "count": len(templates)})
        except Exception as exc:
            log.warning("task_router_template_load_error",
                        extra={"error": str(exc)[:120]})
        return templates

    def build_dag(self, intent: Any) -> Any:
        """
        Build a networkx DiGraph from the intent's template.

        Nodes carry the WorkflowNode as the 'node' attribute.
        Edges represent dependency relationships.
        Raises ValueError on cycles.
        """
        try:
            import networkx as nx  # type: ignore
        except ImportError:
            log.warning("networkx_unavailable — returning node list as fallback")
            return self._build_list_fallback(intent)

        intent_type = str(getattr(intent, "type", "custom"))
        if hasattr(intent_type, "value"):
            intent_type = intent_type.value

        nodes = self._templates.get(intent_type, self._templates["custom"])
        ctx   = dict(getattr(intent, "params", {}))

        G = nx.DiGraph()

        for node in nodes:
            if node.condition and not _eval_condition(node.condition, ctx):
                continue
            resolved_params = _resolve_params(node.params, ctx)
            G.add_node(node.id, node=dataclasses.replace(node, params=resolved_params))

        for node in nodes:
            if node.id not in G.nodes:
                continue
            for dep in node.depends_on:
                if dep in G.nodes:
                    G.add_edge(dep, node.id)

        if not nx.is_directed_acyclic_graph(G):
            raise ValueError(
                f"Workflow template for '{intent_type}' contains a cycle — "
                "cannot build execution DAG."
            )

        log.debug("task_router_dag_built",
                  extra={"intent": intent_type, "nodes": list(G.nodes)})
        return G

    def _build_list_fallback(self, intent: Any) -> list[WorkflowNode]:
        """Return ordered node list when networkx is unavailable."""
        intent_type = str(getattr(getattr(intent, "type", "custom"), "value", "custom"))
        return self._templates.get(intent_type, self._templates["custom"])
