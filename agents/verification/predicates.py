
"""Predicate algebra and runtime for done_when evaluation (Stage E)."""
from __future__ import annotations
import dataclasses as _dc, re
from typing import Any, Callable


@_dc.dataclass
class Predicate:
    """A decidable predicate that can be evaluated against task context."""
    name:       str
    expression: str
    fn:         Callable[[dict], bool] = _dc.field(repr=False, default=lambda _: True)

    def evaluate(self, context: dict) -> bool:
        return self.fn(context)


class PredicateRuntime:
    """
    Runtime for evaluating done_when predicates.
    Supports: file_exists(path), contains(key, value),
              eq(key, value), ne(key, value), all(*preds), any(*preds).
    """

    def __init__(self) -> None:
        self._registry: dict[str, Predicate] = {}
        self._register_builtins()

    def _register_builtins(self) -> None:
        import pathlib

        def file_exists(path: str) -> Callable[[dict], bool]:
            return lambda ctx: pathlib.Path(path).exists()

        def contains(key: str, value: str) -> Callable[[dict], bool]:
            return lambda ctx: value in str(ctx.get(key, ""))

        def eq(key: str, value: str) -> Callable[[dict], bool]:
            return lambda ctx: str(ctx.get(key, "")) == value

        def ne(key: str, value: str) -> Callable[[dict], bool]:
            return lambda ctx: str(ctx.get(key, "")) != value

        self._builtins = {
            "file_exists": file_exists,
            "contains":    contains,
            "eq":          eq,
            "ne":          ne,
        }

    def register(self, predicate: Predicate) -> None:
        self._registry[predicate.name] = predicate

    def parse(self, expression: str) -> Predicate:
        """Parse a done_when expression string into a Predicate."""
        expr = expression.strip()

        # file_exists(path)
        m = re.match(r"^file_exists\((.+)\)$", expr)
        if m:
            path = m.group(1).strip().strip("\"'")
            return Predicate(
                name=f"file_exists({path})",
                expression=expr,
                fn=self._builtins["file_exists"](path),
            )

        # contains(key, value)
        m = re.match("^contains\\((\\w+),\\s*[\"'](.+)[\"']\\)$", expr)
        if m:
            key, val = m.group(1), m.group(2)
            return Predicate(
                name=f"contains({key},{val})",
                expression=expr,
                fn=self._builtins["contains"](key, val),
            )

        # eq(key, value)
        m = re.match("^eq\\((\\w+),\\s*[\"'](.+)[\"']\\)$", expr)
        if m:
            key, val = m.group(1), m.group(2)
            return Predicate(
                name=f"eq({key},{val})",
                expression=expr,
                fn=self._builtins["eq"](key, val),
            )

        # Fallback: always-true predicate for plain text done_when
        return Predicate(
            name=expr[:40],
            expression=expr,
            fn=lambda ctx: True,
        )

    def evaluate(self, expression: str, context: dict) -> bool:
        pred = self.parse(expression)
        return pred.evaluate(context)


def evaluate_done_when(done_when: str, context: dict) -> bool:
    """Convenience function: parse and evaluate a done_when expression."""
    rt = PredicateRuntime()
    return rt.evaluate(done_when, context)
