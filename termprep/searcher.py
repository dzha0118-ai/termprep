"""Search engine for terminology association.

Implements the task book §5.5 fix: the previous implementation was a *priority
short-circuit* — the highest-priority engine (AI, priority 5) answered first and
returned immediately, so Youdao and Wikipedia produced literally zero results
even while ``/api/sources`` advertised both as ``available: true``.

:func:`search_multi` replaces that with **concurrent aggregation**: every
available source is queried in parallel, results are merged, de-duplicated and
cross-corroborated, then ranked with the authoritative dictionary sources ahead
of the AI fallback. A failing source can no longer take the whole lookup down.
"""

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from termprep.sources import get_available_sources

# Authoritative dictionary sources rank ahead of the AI fallback (§5.5).
_SOURCE_RANK: dict[str, int] = {
    "youdao": 0,
    "webster": 1,
    "wikipedia": 1,
    "local": 2,
    "ai": 3,
}


def _source_rank(name: str) -> int:
    """Rank a source name; unknown sources sort last."""
    low = (name or "").lower()
    for key, rank in _SOURCE_RANK.items():
        if low.startswith(key):
            return rank
    return 9


def _to_dict(r: Any) -> dict[str, Any]:
    """Normalise a ``SearchResult`` (or dict) into a plain result dict."""
    if isinstance(r, dict):
        return {
            "query": r.get("query", ""),
            "word": r.get("word", ""),
            "type": r.get("type", r.get("word_type", "")),
            "source": r.get("source", ""),
            "score": float(r.get("score", 0) or 0),
            "definition": r.get("definition", "") or "",
        }
    return {
        "query": getattr(r, "query", ""),
        "word": getattr(r, "word", ""),
        "type": getattr(r, "word_type", ""),
        "source": getattr(r, "source", ""),
        "score": float(getattr(r, "score", 0) or 0),
        "definition": getattr(r, "definition", "") or "",
    }


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

    # Web/API search
    if source in ("web", "all"):
        results.extend(_search_web(term, limit))

    # Sort by relevance score descending
    results.sort(key=lambda x: x.get("score", 0), reverse=True)
    return results[:limit]


def search_multi(
    term: str,
    limit: int = 10,
    ai_callable: Callable[[], list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Aggregate every available source concurrently, then merge and rank (§5.5).

    Args:
        term: Term to look up.
        limit: Maximum number of merged results to return.
        ai_callable: Optional zero-argument callable returning AI-sourced results.
            It is executed inside the same thread pool, so the AI lookup runs in
            parallel with the dictionary sources instead of pre-empting them.

    Returns:
        ``{"results": [...], "sources_used": [...], "sources_failed": [...]}``.
        Merged results carry ``also_from`` when more than one source agrees.
    """
    jobs: dict[str, Callable[[], list[Any]]] = {}
    for src in get_available_sources():
        if getattr(src, "available", False):
            jobs[src.name] = (lambda s=src: s.search(term, limit=limit))
    if ai_callable is not None:
        jobs["ai"] = ai_callable

    raw: dict[str, list[Any]] = {}
    failed: list[str] = []
    if jobs:
        with ThreadPoolExecutor(max_workers=min(6, len(jobs))) as pool:
            futures = {pool.submit(fn): name for name, fn in jobs.items()}
            for future, name in futures.items():
                try:
                    raw[name] = future.result() or []
                except Exception:
                    # one broken source must never break the whole lookup
                    raw[name] = []
                    failed.append(name)

    out: list[dict[str, Any]] = []
    index: dict[tuple[str, str], dict[str, Any]] = {}
    # headword -> source names that returned it (used for cross-corroboration)
    word_jobs: dict[str, list[str]] = {}

    for name in jobs:  # deterministic order
        for item in raw.get(name, []):
            entry = _to_dict(item)
            word = entry["word"].strip()
            if not word:
                continue
            # §5.5 — a definition identical to the headword carries no
            # information; blank it here and enrich it from a sibling below.
            if entry["definition"].strip().lower() == word.lower():
                entry["definition"] = ""

            jobs_for_word = word_jobs.setdefault(word.lower(), [])
            if name not in jobs_for_word:
                jobs_for_word.append(name)

            key = (word.lower(), entry["definition"][:40].strip().lower())
            existing = index.get(key)
            if existing is not None:
                existing["score"] = max(existing.get("score", 0.0), entry["score"])
                continue

            if not entry["source"]:
                entry["source"] = name
            entry["_job"] = name
            index[key] = entry
            out.append(entry)

    # §5.5 — cross-corroboration: an entry confirmed by more than one source is
    # stronger evidence than a single-source hit. This is the micro-level
    # demonstration of the paper's 「协同校验」argument.
    for entry in out:
        job = entry.pop("_job", "")
        others = [
            n for n in word_jobs.get(entry["word"].strip().lower(), []) if n != job
        ]
        if others:
            entry["also_from"] = sorted(others)

    # Enrich blank definitions from any sibling entry sharing the same headword
    by_word: dict[str, str] = {}
    for entry in out:
        if entry["definition"]:
            by_word.setdefault(entry["word"].strip().lower(), entry["definition"])
    for entry in out:
        if not entry["definition"]:
            enriched = by_word.get(entry["word"].strip().lower(), "")
            if enriched:
                entry["definition"] = enriched
            else:
                # No vocabulary-level content available for this entry: flag it
                # rather than echo the headword back (the reported defect).
                entry["definition_missing"] = True

    # Dictionary sources first, AI last; then by descending score (§5.5)
    out.sort(key=lambda r: (_source_rank(r.get("source", "")), -float(r.get("score", 0))))

    return {
        "results": out[:limit],
        "sources_used": sorted(raw.keys()),
        "sources_failed": sorted(failed),
        # which of the queried sources actually produced at least one entry
        "sources_with_results": sorted(n for n in raw if raw.get(n)),
    }


def _search_local(term: str, limit: int) -> list[dict[str, Any]]:
    """Search in local SQLite database."""
    try:
        from termprep.db import TermDB

        db_conn = TermDB()
        return db_conn.search(term, limit)
    except Exception:
        return []


def _search_web(term: str, limit: int) -> list[dict[str, Any]]:
    """Search via online dictionary APIs (Youdao, Webster, etc.)."""
    results: list[dict[str, Any]] = []
    sources = get_available_sources()
    per_source = max(1, limit // max(1, len(sources)))

    for src in sources:
        if not src.available:
            continue
        try:
            src_results = src.search(term, limit=per_source)
            for r in src_results:
                results.append({
                    "query": r.query,
                    "word": r.word,
                    "type": r.word_type,
                    "source": r.source,
                    "score": r.score,
                    "definition": r.definition,
                })
        except Exception:
            continue

    return results
