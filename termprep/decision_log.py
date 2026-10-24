"""Decision log — an auditable record of 系统建议 vs 译员裁决.

Implements task book §5.4.1 (data persistence) together with §5.11 item 3 (the
highest-value item for the paper).

Why this exists
---------------
The paper's case study reports a concrete chain — "system extracted 30 candidate
terms → the translator deleted 9 false positives → added 10 missed terms →
merged/recategorised 3 → 28 terms kept" — but nothing was persisted anywhere, so
the case could not be reproduced or audited. This module records every decision
(timestamp, input digest, term, system suggestion, translator verdict, and the
score breakdown that drove the suggestion) and exports it as CSV/JSON ready for
the paper appendix.

It also turns an anecdote into an instrument: the same export can be produced at
scale, which is what converts a single case study into an empirical one.
"""

from __future__ import annotations

import csv
import io
import json
import hashlib
import os
import sqlite3
import time
import uuid

from termprep.storage import connect_db, data_dir, db_path

# Resolved through termprep.storage: prefers <project>/data, falls back to a
# writable temp directory when the container's app dir is read-only.
DB_DIR = data_dir()
DB_PATH = db_path("decision_log.db")

# ── verdict vocabulary ──
VERDICT_PENDING = "pending"      # system suggestion, no human ruling yet
VERDICT_ACCEPTED = "accepted"    # 译员采纳
VERDICT_REJECTED = "rejected"    # 译员剔除（误检）
VERDICT_ADDED = "added"          # 译员补充（漏检）
VERDICT_MERGED = "merged"        # 译员合并 / 修正分类
VERDICT_EDITED = "edited"        # 译员修改译文

VALID_VERDICTS = frozenset({
    VERDICT_PENDING, VERDICT_ACCEPTED, VERDICT_REJECTED,
    VERDICT_ADDED, VERDICT_MERGED, VERDICT_EDITED,
})

MAX_ROWS = 20000  # bound the log so an anonymous append cannot fill the disk


def _get_db() -> sqlite3.Connection:
    os.makedirs(DB_DIR, exist_ok=True)
    # storage.connect_db handles busy_timeout; no per-connection journal_mode
    # switch (that needs an exclusive lock and breaks under multiple workers).
    conn = connect_db(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS decisions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        created_at REAL NOT NULL,
        step TEXT DEFAULT '',
        input_digest TEXT DEFAULT '',
        term TEXT NOT NULL,
        system_suggestion TEXT DEFAULT '',
        translator_verdict TEXT DEFAULT 'pending',
        score REAL,
        score_breakdown TEXT DEFAULT '',
        note TEXT DEFAULT ''
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_decisions_session ON decisions(session_id)")
    # Full session input, so an exported case bundle can be re-run elsewhere.
    # The ruling log alone only keeps an 80-char digest and therefore cannot make
    # a case reproducible (§5.4.1).
    conn.execute("""CREATE TABLE IF NOT EXISTS session_inputs (
        session_id TEXT PRIMARY KEY,
        created_at REAL NOT NULL,
        sha256 TEXT NOT NULL,
        chars INTEGER NOT NULL,
        text TEXT NOT NULL,
        meta TEXT DEFAULT ''
    )""")
    # Migration shim for DBs created before `meta` existed.
    cols = {r[1] for r in conn.execute("PRAGMA table_info(session_inputs)").fetchall()}
    if "meta" not in cols:
        conn.execute("ALTER TABLE session_inputs ADD COLUMN meta TEXT DEFAULT ''")
    conn.commit()
    return conn


MAX_INPUT_CHARS = 200_000


def remember_input(session_id: str, text: str, meta: dict | None = None) -> dict:
    """Store a session's full source text (bounded) plus optional run parameters.

    Recording the parameters is what makes the case *reproducible*: the same
    input with the same ``top_n`` / domain reproduces the same rule-based
    candidates, so a reader can verify the numbers.
    """
    sid = session_id or ""
    body = (text or "")[:MAX_INPUT_CHARS]
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    conn = _get_db()
    conn.execute(
        "INSERT INTO session_inputs (session_id, created_at, sha256, chars, text, meta)"
        " VALUES (?,?,?,?,?,?)"
        " ON CONFLICT(session_id) DO UPDATE SET"
        " created_at=excluded.created_at, sha256=excluded.sha256,"
        " chars=excluded.chars, text=excluded.text, meta=excluded.meta",
        (
            sid,
            time.time(),
            digest,
            len(body),
            body,
            json.dumps(meta, ensure_ascii=False) if meta else "",
        ),
    )
    conn.commit()
    conn.close()
    return {"session_id": sid, "sha256": digest, "chars": len(body)}


def get_input(session_id: str) -> dict | None:
    """Return the stored input (and parameters) for a session, or None."""
    conn = _get_db()
    row = conn.execute(
        "SELECT session_id, created_at, sha256, chars, text, meta"
        " FROM session_inputs WHERE session_id = ?",
        (session_id or "",),
    ).fetchone()
    conn.close()
    if not row:
        return None
    params: dict = {}
    if row[5]:
        try:
            params = json.loads(row[5])
        except Exception:
            params = {}
    return {
        "session_id": row[0],
        "created_at": row[1],
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(row[1])),
        "sha256": row[2],
        "chars": row[3],
        "text": row[4],
        "parameters": params,
    }


def list_sessions(limit: int = 100) -> list[dict]:
    """List stored sessions with their record counts (for the export UI)."""
    conn = _get_db()
    rows = conn.execute(
        """SELECT i.session_id, i.created_at, i.sha256, i.chars,
                  (SELECT COUNT(*) FROM decisions d WHERE d.session_id = i.session_id)
           FROM session_inputs i ORDER BY i.created_at DESC LIMIT ?""",
        (max(1, int(limit)),),
    ).fetchall()
    conn.close()
    return [
        {
            "session_id": r[0],
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r[1])),
            "sha256": r[2],
            "chars": r[3],
            "records": r[4],
        }
        for r in rows
    ]


def _digest(text: str, width: int = 80) -> str:
    """Shorten an input text into a single-line digest for the audit trail."""
    flat = " ".join((text or "").split())
    return flat[:width]


def record(
    term: str,
    session_id: str = "",
    step: str = "",
    input_digest: str = "",
    system_suggestion: str = "",
    translator_verdict: str = VERDICT_PENDING,
    score: float | None = None,
    score_breakdown: dict | None = None,
    note: str = "",
    replace: bool = False,
) -> int:
    """Append one decision. Returns the new row id.

    ``replace=True`` gives the log *per-term* semantics: any earlier ruling on the
    same ``(session_id, step, term)`` is removed first, so a translator who
    changes their mind leaves exactly one final ruling per term (which is what a
    paper appendix table wants) instead of a doubled count.
    """
    verdict = translator_verdict if translator_verdict in VALID_VERDICTS else VERDICT_PENDING
    conn = _get_db()
    if replace:
        conn.execute(
            "DELETE FROM decisions WHERE session_id = ? AND step = ? AND term = ?",
            (session_id or "", step or "", (term or "").strip()),
        )
    total = conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
    if total >= MAX_ROWS:
        # drop the oldest rows to stay bounded
        conn.execute(
            "DELETE FROM decisions WHERE id IN (SELECT id FROM decisions ORDER BY id LIMIT ?)",
            (total - MAX_ROWS + 1,),
        )
    cur = conn.execute(
        """INSERT INTO decisions
           (session_id, created_at, step, input_digest, term, system_suggestion,
            translator_verdict, score, score_breakdown, note)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            session_id or uuid.uuid4().hex[:12],
            time.time(),
            step or "",
            _digest(input_digest),
            (term or "").strip(),
            system_suggestion or "",
            verdict,
            float(score) if score is not None else None,
            json.dumps(score_breakdown, ensure_ascii=False) if score_breakdown else "",
            (note or "")[:500],
        ),
    )
    conn.commit()
    row_id = int(cur.lastrowid or 0)
    conn.close()
    return row_id


def record_many(
    session_id: str,
    step: str,
    input_digest: str,
    items: list[dict],
    replace: bool = True,
    input_text: str = "",
    meta: dict | None = None,
) -> int:
    """Append (or replace) several decisions for one batch. Returns the count.

    When ``input_text`` is supplied the session's full source text (and optional
    run parameters) is remembered alongside the rulings, so the exported case
    bundle is self-sufficient instead of holding only an 80-char digest.
    """
    if input_text and session_id:
        try:
            remember_input(session_id, input_text, meta)
        except Exception:
            pass

    count = 0
    for item in items or []:
        term = (item.get("term") or item.get("word") or "").strip()
        if not term:
            continue
        record(
            term=term,
            session_id=session_id,
            step=item.get("step") or step,
            input_digest=item.get("input_digest") or input_digest,
            system_suggestion=item.get("system_suggestion", ""),
            translator_verdict=item.get("translator_verdict", VERDICT_PENDING),
            score=item.get("score"),
            score_breakdown=item.get("score_breakdown"),
            note=item.get("note", ""),
            replace=replace,
        )
        count += 1
    return count


def list_decisions(session_id: str | None = None, limit: int = 500) -> list[dict]:
    """Return recorded decisions, newest last."""
    conn = _get_db()
    limit = max(1, min(int(limit or 500), 5000))
    if session_id:
        rows = conn.execute(
            "SELECT * FROM decisions WHERE session_id = ? ORDER BY id LIMIT ?",
            (session_id, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM decisions ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    conn.close()
    return [_row_to_dict(r) for r in rows]


def _row_to_dict(r: tuple) -> dict:
    breakdown: dict | str = ""
    if r[9]:
        try:
            breakdown = json.loads(r[9])
        except Exception:
            breakdown = r[9]
    return {
        "id": r[0],
        "session_id": r[1],
        "created_at": r[2],
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r[2])),
        "step": r[3],
        "input_digest": r[4],
        "term": r[5],
        "system_suggestion": r[6],
        "translator_verdict": r[7],
        "score": r[8],
        "score_breakdown": breakdown,
        "note": r[10],
    }


def summary(session_id: str | None = None) -> dict:
    """Aggregate the log — the numbers the paper's methodology section needs.

    Counts the system's suggestions against the translator's rulings, i.e. the
    "系统自动提取 30 → 译员删 9 / 补 10 / 合并 3 → 保留 28" chain.
    """
    rows = list_decisions(session_id=session_id, limit=5000)
    by_verdict: dict[str, int] = {}
    by_step: dict[str, int] = {}
    for r in rows:
        by_verdict[r["translator_verdict"]] = by_verdict.get(r["translator_verdict"], 0) + 1
        by_step[r["step"] or "unknown"] = by_step.get(r["step"] or "unknown", 0) + 1

    suggested = len(rows)
    accepted = by_verdict.get(VERDICT_ACCEPTED, 0)
    added = by_verdict.get(VERDICT_ADDED, 0)
    rejected = by_verdict.get(VERDICT_REJECTED, 0)
    merged = by_verdict.get(VERDICT_MERGED, 0)
    pending = by_verdict.get(VERDICT_PENDING, 0)

    kept = accepted + added + merged
    return {
        "session_id": session_id or "",
        "total_records": len(rows),
        "by_verdict": by_verdict,
        "by_step": by_step,
        # the paper's chain, computed rather than hand-counted
        "system_suggested": suggested - added,   # additions were not suggested
        "translator_rejected": rejected,
        "translator_added": added,
        "translator_merged": merged,
        "translator_accepted": accepted,
        "pending": pending,
        "final_kept": kept,
    }


CSV_COLUMNS = [
    "timestamp", "session_id", "step", "input_digest", "term",
    "system_suggestion", "translator_verdict", "score", "note",
]


def export_csv(session_id: str | None = None) -> str:
    """Export the decision log as CSV (paper-appendix ready).

    Columns: 时间戳 / 会话 / 步骤 / 输入文本摘要 / 术语 / 系统建议 /
    译员裁决 / 系统评分 / 备注.
    """
    rows = list_decisions(session_id=session_id, limit=5000)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        writer.writerow({k: r.get(k, "") for k in CSV_COLUMNS})
    return buf.getvalue()


def export_json(session_id: str | None = None) -> dict:
    """Export the decision log plus its summary as JSON."""
    rows = list_decisions(session_id=session_id, limit=5000)
    return {
        "session_id": session_id or "",
        "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "summary": summary(session_id),
        "decisions": rows,
    }
