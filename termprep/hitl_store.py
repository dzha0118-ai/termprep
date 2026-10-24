"""Persistent store for Human-in-the-loop pipeline sessions.

Why this is not just an in-memory dict
--------------------------------------
The deployment command is ``gunicorn -w 2`` — **two worker processes**. A session
created by worker A is invisible to worker B if it only lives in process memory,
so ``/api/pipeline/start`` could succeed and the very next
``/api/pipeline/{sid}/resume`` fail with "session not found or expired" even
though nothing expired.

SQLite in the runtime data directory fixes that: every worker sees the same rows.

Hard-won lessons (this cost a live-production bug)
--------------------------------------------------
* **Never write on the read path.** ``get()`` used to call ``clean()`` first,
  i.e. a ``DELETE`` on every lookup. Under concurrent workers that write could hit
  ``database is locked``, the caller swallowed the error, and a perfectly healthy
  session turned into a 404. Cleanup now only happens on ``save()``.
* **Don't set ``journal_mode`` per connection.** It needs a brief exclusive lock
  and is the classic source of ``database is locked`` when several processes open
  short-lived connections. We leave the journal mode alone and rely on
  ``busy_timeout`` instead (see ``storage.connect_db``).
* **Never swallow errors silently.** ``last_error()`` exposes the most recent
  failure so ``/api/health`` can report *why* a lookup failed.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from typing import Any

from termprep.storage import connect_db, data_dir, db_path

DB_DIR = data_dir()
DB_PATH = db_path("hitl_sessions.db")

DEFAULT_TTL = 3600          # 1 hour, same as the spec
MAX_ROWS = 500              # bound the table

_last_error: str = ""


def last_error() -> str:
    """Most recent storage error (empty when everything is fine)."""
    return _last_error


def _set_error(msg: str) -> None:
    global _last_error
    _last_error = msg


def _clear_error() -> None:
    global _last_error
    _last_error = ""


def _get_db() -> sqlite3.Connection:
    os.makedirs(DB_DIR, exist_ok=True)
    conn = connect_db(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS pipeline_sessions (
        session_id TEXT PRIMARY KEY,
        created_at REAL NOT NULL,
        expires_at REAL NOT NULL,
        payload TEXT NOT NULL
    )""")
    conn.commit()
    return conn


def save(session_id: str, payload: dict[str, Any], ttl: int = DEFAULT_TTL) -> None:
    """Insert or replace a session (also the only place expired rows are swept)."""
    sid = (session_id or "").strip()
    if not sid:
        return
    now = time.time()
    conn = _get_db()
    try:
        conn.execute(
            "INSERT INTO pipeline_sessions (session_id, created_at, expires_at, payload)"
            " VALUES (?,?,?,?)"
            " ON CONFLICT(session_id) DO UPDATE SET"
            " created_at=excluded.created_at, expires_at=excluded.expires_at,"
            " payload=excluded.payload",
            (sid, now, now + max(60, int(ttl)), json.dumps(payload, ensure_ascii=False)),
        )
        # opportunistic sweep — writes happen here, never on the read path
        conn.execute("DELETE FROM pipeline_sessions WHERE expires_at < ?", (now,))
        total = conn.execute("SELECT COUNT(*) FROM pipeline_sessions").fetchone()[0]
        if total > MAX_ROWS:
            conn.execute(
                "DELETE FROM pipeline_sessions WHERE session_id IN"
                " (SELECT session_id FROM pipeline_sessions ORDER BY created_at LIMIT ?)",
                (total - MAX_ROWS,),
            )
        conn.commit()
        _clear_error()
    finally:
        conn.close()


def get(session_id: str) -> dict[str, Any] | None:
    """Return a live session, or None when genuinely missing or expired.

    Pure read: no cleanup, no journal-mode changes, and any SQLite error is
    recorded in :func:`last_error` **and re-raised** so callers can tell
    "not found" apart from "storage failed".
    """
    sid = (session_id or "").strip()
    if not sid:
        return None
    try:
        conn = _get_db()
    except Exception as e:
        _set_error(f"open: {type(e).__name__}: {e}")
        raise
    try:
        row = conn.execute(
            "SELECT payload, created_at, expires_at FROM pipeline_sessions WHERE session_id = ?",
            (sid,),
        ).fetchone()
    except Exception as e:
        _set_error(f"select: {type(e).__name__}: {e}")
        raise
    finally:
        conn.close()

    if not row:
        return None
    if float(row[2] or 0) < time.time():
        return None          # expired — treated as missing, cleaned up by save()
    try:
        data = json.loads(row[0])
    except Exception as e:
        _set_error(f"json: {type(e).__name__}: {e}")
        return None
    if not isinstance(data, dict):
        return None
    data.setdefault("session_id", sid)
    data["created_at"] = row[1]
    data["expires_at"] = row[2]
    _clear_error()
    return data


def drop(session_id: str) -> None:
    sid = (session_id or "").strip()
    if not sid:
        return
    conn = _get_db()
    try:
        conn.execute("DELETE FROM pipeline_sessions WHERE session_id = ?", (sid,))
        conn.commit()
    finally:
        conn.close()


def clean() -> int:
    """Delete expired sessions. Returns how many were removed."""
    conn = _get_db()
    try:
        cur = conn.execute("DELETE FROM pipeline_sessions WHERE expires_at < ?", (time.time(),))
        removed = cur.rowcount or 0
        conn.commit()
        return removed
    finally:
        conn.close()


def count() -> int:
    conn = _get_db()
    try:
        return conn.execute("SELECT COUNT(*) FROM pipeline_sessions").fetchone()[0]
    finally:
        conn.close()


def backend() -> str:
    """Where sessions live — surfaced by /api/health for troubleshooting."""
    return DB_PATH


def self_test() -> dict[str, Any]:
    """Write→read→drop a probe row so /api/health can prove the store works.

    This is what turns "resume failed with 404" into an actionable diagnosis on a
    live deployment where we cannot read the server logs.
    """
    probe_id = f"__probe__{int(time.time()*1000)}"
    result: dict[str, Any] = {"ok": False}
    try:
        save(probe_id, {"session_id": probe_id, "probe": True}, ttl=60)
        back = get(probe_id)
        result["wrote"] = True
        result["read_back"] = bool(back)
        result["ok"] = bool(back)
        drop(probe_id)
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
        result["last_error"] = last_error()
    return result
