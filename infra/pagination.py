""" — page/page_size helpers."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403

# PAGINATED LIST RESPONSES  — page/page_size for all collections
# ══════════════════════════════════════════════════════════════════════════════
# All list endpoints (workflows, decisions, analytics, cost, audit) support
# ?page=N&page_size=50. Returns total + items slice.
# Default page_size=50, max=500. Zero-indexed pages.

_DEFAULT_PAGE_SIZE = 50
_MAX_PAGE_SIZE     = 500


def paginate(items: list, page: int = 0, page_size: int = _DEFAULT_PAGE_SIZE) -> dict:
    """
    Return a pagination envelope for a list of items.
    {total, page, page_size, pages, items}
    """
    page_size = max(1, min(page_size, _MAX_PAGE_SIZE))
    page      = max(0, page)
    total     = len(items)
    pages     = max(1, (total + page_size - 1) // page_size)
    start     = page * page_size
    end       = start + page_size
    return {
        "total":     total,
        "page":      page,
        "page_size": page_size,
        "pages":     pages,
        "items":     items[start:end],
    }


# ══════════════════════════════════════════════════════════════════════════════
