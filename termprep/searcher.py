"""Search engine for terminology association."""

from typing import Any

# Future: integrate with online dictionary APIs
# from termprep.sources import webster, youdao, eudict


def search_term(term: str, source: str = "all", limit: int = 20) -> list[dict[str, Any]]:
    """Search a term and return associated vocabulary.

    Args:
        term: The search term in Chinese or English.
        source: Search source - "local", "web", or "all".
        limit: Maximum number of results.

    Returns:
        List of result dicts with keys: query, word, type, source, score.
    """
    results: list[dict[str, Any]] = []

    # Local database search
    if source in ("local", "all"):
        results.extend(_search_local(term, limit))

    # Web/API search (stub for future)
    if source in ("web", "all"):
        results.extend(_search_web(term, limit))

    # Sort by relevance score descending
    results.sort(key=lambda x: x.get("score", 0), reverse=True)
    return results[:limit]


def _search_local(term: str, limit: int) -> list[dict[str, Any]]:
    """Search in local SQLite database."""
    try:
        from termprep.db import TermDB
        db_conn = TermDB()
        return db_conn.search(term, limit)
    except Exception:
        return []


def _search_web(term: str, limit: int) -> list[dict[str, Any]]:
    """Search via online dictionary APIs (stub)."""
    # TODO: integrate with dictionary APIs
    return []
