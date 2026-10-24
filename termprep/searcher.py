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

# ── 单源结果缓存 ──
# 手机上一次搜索要 2–3 秒，重复查同一个词又完整重跑一遍，完全没命中缓存。
# 这里按 (源, 词, limit) 缓存**单源原始结果**：既能给「先出词典、后补 AI」的
# 两段式渲染复用（快路径跑过的源，慢路径不再重复请求），也能让重复查询秒回。
# 单进程 gunicorn，加锁足够。
import threading  # noqa: E402
import time as _time  # noqa: E402

_SOURCE_CACHE: dict[tuple[str, str, int], tuple[float, list[Any]]] = {}
_SOURCE_CACHE_LOCK = threading.Lock()
_SOURCE_TTL = 180.0      # 秒（有结果）
_EMPTY_TTL = 30.0        # 秒（空结果：短，避免把瞬时故障固化）
_SOURCE_MAX = 512        # 条


def _cached_source(name: str, term: str, limit: int, fn: Callable[[], list[Any]]) -> list[Any]:
    """带 TTL 的单源缓存。

    **有**结果缓存 180s；**空**结果只缓存 30s（并且 AI 的空结果不缓存）——
    空结果可能只是瞬时故障，缓存太久会让用户在一段时间里查不到本来有的词，
    那是拿质量换速度，不做。
    """
    key = (name, (term or "").strip().lower(), int(limit))
    now = _time.time()
    with _SOURCE_CACHE_LOCK:
        hit = _SOURCE_CACHE.get(key)
        if hit is not None:
            ts, cached = hit
            ttl = _SOURCE_TTL if cached else _EMPTY_TTL
            if now - ts < ttl:
                return cached
    out = fn()
    if out or name != "ai":          # AI 的空结果不缓存（模型抖动/限流不该粘住）
        with _SOURCE_CACHE_LOCK:
            if len(_SOURCE_CACHE) > _SOURCE_MAX:
                for k in sorted(_SOURCE_CACHE, key=lambda k: _SOURCE_CACHE[k][0])[: _SOURCE_MAX // 2]:
                    _SOURCE_CACHE.pop(k, None)
            _SOURCE_CACHE[key] = (now, out)
    return out


def clear_source_cache() -> None:
    """清缓存（测试用）。"""
    with _SOURCE_CACHE_LOCK:
        _SOURCE_CACHE.clear()

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
    skip_ai: bool = False,
    deadline_ms: int | None = None,
) -> dict[str, Any]:
    """Aggregate every available source concurrently, then merge and rank (§5.5).

    Args:
        term: Term to look up.
        limit: Maximum number of merged results to return.
        ai_callable: Optional zero-argument callable returning AI-sourced results.
            It is executed inside the same thread pool, so the AI lookup runs in
            parallel with the dictionary sources instead of pre-empting them.
        skip_ai: Skip the AI source entirely — used for the *fast* first paint.
            Dictionary sources answer in a few hundred ms; the LLM takes seconds,
            so the UI shows dictionary results first and merges AI ones after.
        deadline_ms: Stop waiting after this many milliseconds and return what is
            ready. A slow source that yields nothing (Wikipedia answering in 2.5s
            with zero hits) must not hold the first paint hostage.

    Returns:
        ``{"results": [...], "sources_used": [...], "sources_failed": [...],
        "sources_pending": [...]}``.
        Merged results carry ``also_from`` when more than one source agrees.
    """
    jobs: dict[str, Callable[[], list[Any]]] = {}
    timing: dict[str, int] = {}

    def _timed(name: str, fn: Callable[[], list[Any]]) -> list[Any]:
        """记录单源耗时（毫秒）——排查「为什么慢」必须有数据，不能靠猜。"""
        t0 = _time.perf_counter()
        try:
            return fn()
        finally:
            timing[name] = int((_time.perf_counter() - t0) * 1000)

    _t_start = _time.perf_counter()
    for src in get_available_sources():
        if getattr(src, "available", False):
            jobs[src.name] = (lambda s=src: _timed(
                s.name, lambda: _cached_source(s.name, term, limit,
                                               lambda: s.search(term, limit=limit))))
    if ai_callable is not None and not skip_ai:
        jobs["ai"] = (lambda: _timed("ai", lambda: _cached_source("ai", term, limit, ai_callable)))

    raw: dict[str, list[Any]] = {}
    failed: list[str] = []
    pending: list[str] = []
    if jobs:
        from concurrent.futures import wait as _wait

        pool = ThreadPoolExecutor(max_workers=min(6, len(jobs)))
        try:
            futures = {pool.submit(fn): name for name, fn in jobs.items()}
            if deadline_ms and deadline_ms > 0:
                done, not_done = _wait(list(futures), timeout=deadline_ms / 1000.0)
            else:
                done, not_done = set(futures), set()
            for future in done:
                name = futures[future]
                try:
                    raw[name] = future.result() or []
                except Exception:
                    # one broken source must never break the whole lookup
                    raw[name] = []
                    failed.append(name)
            for future in not_done:
                pending.append(futures[future])
        finally:
            pool.shutdown(wait=False)   # 不等迟到的线程，避免拖住首屏

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
        # 超时未返回的源（慢源）：前端可据此提示「还有源在加载」
        "sources_pending": sorted(pending),
        # which of the queried sources actually produced at least one entry
        "sources_with_results": sorted(n for n in raw if raw.get(n)),
        # 服务端真实耗时（毫秒）：区分「应用慢」还是「网络慢」
        "timings": {
            "per_source_ms": dict(sorted(timing.items(), key=lambda kv: -kv[1])),
            "elapsed_ms": int((_time.perf_counter() - _t_start) * 1000),
            "deadline_ms": deadline_ms,
        },
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
