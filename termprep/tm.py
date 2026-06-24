"""Translation Memory — stores and retrieves translation pairs."""

import sqlite3, hashlib, time, os, re

DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data")
DB_PATH = os.path.join(DB_DIR, "translation_memory.db")


def _get_db() -> sqlite3.Connection:
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
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
        conn.execute("INSERT INTO tm_entries (source_hash, source_text, target_text, source_lang, target_lang, domain) VALUES (?,?,?,?,?,?)",
                    (source_hash, text, translation, source_lang, target_lang, domain))
    conn.commit()
    conn.close()


def search(text: str, source_lang: str = "auto", target_lang: str = "auto", domain: str = "general", limit: int = 5) -> list[dict]:
    """Search for matching translations. Exact match first, then fuzzy."""
    conn = _get_db()
    source_hash = _hash(text)
    results = []

    # Exact match
    rows = conn.execute("SELECT * FROM tm_entries WHERE source_hash = ? ORDER BY use_count DESC LIMIT ?",
                       (source_hash, limit)).fetchall()
    for r in rows:
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
                if r[2] not in seen:
                    results.append({
                        "source": r[2], "target": r[3], "domain": r[6],
                        "use_count": r[7], "match_type": "fuzzy", "score": 0.7
                    })
                    seen.add(r[2])

    conn.close()
    return results[:limit]


def stats() -> dict:
    conn = _get_db()
    total = conn.execute("SELECT COUNT(*) FROM tm_entries").fetchone()[0]
    domains = conn.execute("SELECT domain, COUNT(*) FROM tm_entries GROUP BY domain ORDER BY COUNT(*) DESC LIMIT 5").fetchall()
    recent = conn.execute("SELECT source_text, target_text, use_count FROM tm_entries ORDER BY created_at DESC LIMIT 5").fetchall()
    conn.close()
    return {
        "total": total,
        "domains": {d: c for d, c in domains},
        "recent": [{"source": r[0][:60], "target": r[1][:60], "count": r[2]} for r in recent]
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
