"""Translation Memory — stores and retrieves translation pairs."""

import sqlite3, hashlib, time, os, re

from termprep.storage import connect_db, data_dir, db_path

# Resolved through termprep.storage: prefers <project>/data, falls back to a
# writable temp directory when the container's app dir is read-only.
DB_DIR = data_dir()
DB_PATH = db_path("translation_memory.db")


def _get_db() -> sqlite3.Connection:
    os.makedirs(DB_DIR, exist_ok=True)
    # storage.connect_db sets bus_timeout and avoids per-connection journal_mode
    # switches (which need an exclusive lock and cause "database is locked" when
    # several gunicorn workers open short-lived connections).
    conn = connect_db(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS tm_entries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_hash TEXT NOT NULL,
        source_text TEXT NOT NULL,
        target_text TEXT NOT NULL,
        source_lang TEXT DEFAULT 'auto',
        target_lang TEXT DEFAULT 'auto',
        domain TEXT DEFAULT 'general',
        use_count INTEGER DEFAULT 1,
        created_at REAL DEFAULT (strftime('%s','now'))
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_source_hash ON tm_entries(source_hash)")
    conn.commit()
    return conn


def store(text: str, translation: str, source_lang: str = "auto", target_lang: str = "auto", domain: str = "general"):
    """Store a translation pair. Updates use count if already exists."""
    source_hash = _hash(text)
    conn = _get_db()
    existing = conn.execute("SELECT id, use_count FROM tm_entries WHERE source_hash = ? AND target_text = ?",
                           (source_hash, translation)).fetchone()
    if existing:
        conn.execute("UPDATE tm_entries SET use_count = ?, created_at = ? WHERE id = ?",
                    (existing[1] + 1, time.time(), existing[0]))
    else:
        # §5.9c — write an explicit float timestamp. The old table default was
        # strftime('%s','now'), which has only second granularity, so a row stored
        # during a request could compare as "older" than that request's cut-off
        # and immediately match itself again.
        conn.execute("INSERT INTO tm_entries (source_hash, source_text, target_text, source_lang, target_lang, domain, created_at) VALUES (?,?,?,?,?,?,?)",
                    (source_hash, text, translation, source_lang, target_lang, domain, time.time()))
    conn.commit()
    conn.close()


def search(text: str, source_lang: str = "auto", target_lang: str = "auto", domain: str = "general", limit: int = 5, exclude_after: float | None = None) -> list[dict]:
    """Search for matching translations. Exact match first, then fuzzy.

    §5.9c — ``exclude_after`` (a UNIX timestamp) drops every entry written at or
    after that instant. The previous behaviour stored the freshly produced
    translation *before* querying, so the first translation of any new text
    always "matched itself" with ``match_type: "exact", score: 1.0`` and the TM
    hit-rate was a meaningless metric.
    """
    conn = _get_db()
    source_hash = _hash(text)
    results = []

    def _is_fresh(created_at: float | None) -> bool:
        """True when the row was written before this request started."""
        if exclude_after is None or created_at is None:
            return True
        try:
            return float(created_at) < exclude_after
        except (TypeError, ValueError):
            return True

    # Exact match
    rows = conn.execute("SELECT * FROM tm_entries WHERE source_hash = ? ORDER BY use_count DESC LIMIT ?",
                       (source_hash, limit)).fetchall()
    for r in rows:
        if not _is_fresh(r[8]):
            continue
        results.append({
            "source": r[2], "target": r[3], "domain": r[6],
            "use_count": r[7], "match_type": "exact", "score": 1.0
        })

    # Fuzzy match via substring
    if len(results) < limit:
        terms = _extract_terms(text)
        if terms:
            fuzzy_rows = conn.execute(
                "SELECT * FROM tm_entries WHERE " + " OR ".join(["source_text LIKE ?"] * len(terms)) + " ORDER BY use_count DESC LIMIT ?",
                tuple(f"%{t}%" for t in terms) + (limit - len(results),)
            ).fetchall()
            seen = {r["source"] for r in results}
            for r in fuzzy_rows:
                if not _is_fresh(r[8]):
                    continue
                if r[2] not in seen:
                    results.append({
                        "source": r[2], "target": r[3], "domain": r[6],
                        "use_count": r[7], "match_type": "fuzzy", "score": 0.7
                    })
                    seen.add(r[2])

    conn.close()
    return results[:limit]


def dump_all(limit: int = 5000) -> list[dict]:
    """Return every TM entry (newest first).

    Used by the case-bundle export so a case study can be reconstructed
    elsewhere: container-local SQLite is wiped on rebuild, the exported bundle
    is not.
    """
    conn = _get_db()
    rows = conn.execute(
        "SELECT source_text, target_text, source_lang, target_lang, domain,"
        " use_count, created_at FROM tm_entries ORDER BY id DESC LIMIT ?",
        (max(1, int(limit)),),
    ).fetchall()
    conn.close()
    return [
        {
            "source": r[0], "target": r[1], "source_lang": r[2],
            "target_lang": r[3], "domain": r[4], "use_count": r[5],
            "created_at": r[6],
        }
        for r in rows
    ]


def stats() -> dict:
    """Aggregate TM statistics.

    §5.9e — ``source``/``target`` are returned in full. They used to be sliced to
    60 characters, which is why the only live entry showed a truncated target
    (``"...administrative management is ineffici"``) and looked like data loss.
    """
    conn = _get_db()
    total = conn.execute("SELECT COUNT(*) FROM tm_entries").fetchone()[0]
    domains = conn.execute("SELECT domain, COUNT(*) FROM tm_entries GROUP BY domain ORDER BY COUNT(*) DESC LIMIT 5").fetchall()
    recent = conn.execute("SELECT source_text, target_text, use_count FROM tm_entries ORDER BY created_at DESC LIMIT 5").fetchall()
    conn.close()
    return {
        "total": total,
        "domains": {d: c for d, c in domains},
        "recent": [{"source": r[0], "target": r[1], "count": r[2]} for r in recent]
    }


def _hash(text: str) -> str:
    return hashlib.md5(text.strip().lower().encode()).hexdigest()


def _extract_terms(text: str) -> list[str]:
    """Extract key terms for fuzzy matching."""
    # Try jieba first
    try:
        import jieba.analyse
        terms = jieba.analyse.extract_tags(text, topK=5)
        return [t for t in terms if len(t) >= 3]
    except Exception:
        pass
    # Fallback: just take 3+ char words
    words = re.findall(r'[\u4e00-\u9fff]{3,}|[A-Za-z]{3,}', text)
    return list(set(words))[:5]
