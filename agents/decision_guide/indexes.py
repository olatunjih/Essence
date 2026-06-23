
"""Decision-Guide: indexes by writes_glob, tools, risk, and composite."""
from __future__ import annotations
import fnmatch
from typing import Any


class RuleIndex:
    """
    Multi-dimensional index over a rule library.
    Indexes: writes_glob, tools, risk, composite (tools+risk).
    """

    def __init__(self, rules: list[dict]) -> None:
        self._rules = rules
        self._by_tool:  dict[str, list[dict]] = {}
        self._by_risk:  dict[str, list[dict]] = {}
        self._build()

    def _build(self) -> None:
        for rule in self._rules:
            risk = rule.get("risk", "").upper()
            if risk not in self._by_risk:
                self._by_risk[risk] = []
            self._by_risk[risk].append(rule)
            for tool in rule.get("tools", []):
                if tool not in self._by_tool:
                    self._by_tool[tool] = []
                self._by_tool[tool].append(rule)

    def by_tool(self, tool_name: str) -> list[dict]:
        return list(self._by_tool.get(tool_name, []))

    def by_risk(self, risk: str) -> list[dict]:
        return list(self._by_risk.get(risk.upper(), []))

    def by_writes_glob(self, path: str) -> list[dict]:
        """Return rules whose writes_glob matches path."""
        matched: list[dict] = []
        for rule in self._rules:
            glob = rule.get("writes_glob", "")
            if not glob:
                continue
            for pat in glob.split("|"):
                pat = pat.strip()
                if pat and (fnmatch.fnmatch(path, pat) or
                            fnmatch.fnmatch(path.lstrip("/"), pat.lstrip("/"))):
                    matched.append(rule)
                    break
        return matched

    def composite(self, tools: list[str], risk: str,
                  write_path: str = "") -> list[dict]:
        """Combined query: union of tool matches + risk filter."""
        seen: set[str] = set()
        results: list[dict] = []
        candidate_sets: list[list[dict]] = []
        for tool in tools:
            candidate_sets.append(self.by_tool(tool))
        if not tools:
            candidate_sets.append(self._rules)
        for rule in (r for cs in candidate_sets for r in cs):
            rid = rule["id"]
            if rid not in seen:
                seen.add(rid)
                results.append(rule)
        if write_path:
            for rule in self.by_writes_glob(write_path):
                if rule["id"] not in seen:
                    seen.add(rule["id"])
                    results.append(rule)
        return results
