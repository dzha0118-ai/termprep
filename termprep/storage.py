"""Resolve a writable directory for runtime data (translation memory, decision log).

Why this exists
---------------
``tm.py`` originally resolved its database to ``<root>/../data`` — one level too
far, i.e. ``/data`` inside the container, outside the application directory.
Fixing that to ``<root>/data`` introduced a deployment risk: Hugging Face Docker
Spaces can run with a non-writable application directory, in which case SQLite
would fail to open the file and ``/api/translate`` would start returning 500s.

:func:`data_dir` therefore resolves the first *actually writable* candidate:

1. ``$TERMPREP_DATA_DIR`` — explicit operator override (e.g. a mounted volume);
2. ``<project>/data`` — keeps the data beside the app;
3. a temp directory — last resort, so the service degrades instead of breaking.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import tempfile

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_cached_dir: str | None = None
# Human-readable explanation of how data_dir() was resolved. Exposed so a silent
# fallback (e.g. TERMPREP_DATA_DIR points somewhere unwritable) is discoverable
# instead of the operator wrongly believing persistence is configured.
_resolution_note: str = ""

_log = logging.getLogger("termprep.storage")


def _is_writable(path: str) -> bool:
    """True when ``path`` can be created and written to."""
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".termprep-write-probe")
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("ok")
        os.unlink(probe)
        return True
    except Exception:
        return False


def data_dir() -> str:
    """Return (and cache) the first writable runtime-data directory."""
    global _cached_dir, _resolution_note
    if _cached_dir is not None:
        return _cached_dir

    override = (os.environ.get("TERMPREP_DATA_DIR") or "").strip()
    candidates = []
    if override:
        candidates.append(override)
    candidates.append(os.path.join(_PROJECT_ROOT, "data"))
    candidates.append(os.path.join(tempfile.gettempdir(), "termprep-data"))

    for candidate in candidates:
        if _is_writable(candidate):
            _cached_dir = candidate
            if override and os.path.abspath(candidate) != os.path.abspath(override):
                _resolution_note = (
                    f"TERMPREP_DATA_DIR={override!r} is not writable; fell back to {candidate!r}. "
                    "Persistence is NOT configured as intended."
                )
                _log.warning(_resolution_note)
            elif override:
                _resolution_note = f"using TERMPREP_DATA_DIR={candidate!r}"
            else:
                _resolution_note = f"using default data dir {candidate!r}"
            return candidate

    # Nothing writable — return the temp path anyway so the error surfaces
    # at the call site rather than here.
    _cached_dir = candidates[-1]
    _resolution_note = f"no writable data dir found; using {_cached_dir!r} (will likely fail)"
    _log.error(_resolution_note)
    return _cached_dir


def resolution_note() -> str:
    """How :func:`data_dir` was resolved (empty until it is first called)."""
    data_dir()
    return _resolution_note


def is_persistent() -> bool:
    """True when an explicit TERMPREP_DATA_DIR override is actually in effect."""
    return bool((os.environ.get("TERMPREP_DATA_DIR") or "").strip()) and (
        os.path.abspath(data_dir()) == os.path.abspath(os.environ["TERMPREP_DATA_DIR"].strip())
    )


def connect_db(db_file: str) -> sqlite3.Connection:
    """Open a SQLite connection configured for **multi-process** use.

    Why this exists: the deployment runs gunicorn with more than one worker (and
    the container FS can be an overlay). Under concurrency, a per-connection
    ``PRAGMA journal_mode=WAL`` needs a brief exclusive lock, so short-lived
    connections from several processes intermittently raise
    ``sqlite3.OperationalError: database is locked``.

    Instead of fighting journal modes we:
      * leave the journal mode alone (whatever the DB already uses),
      * give the connection a generous ``busy_timeout`` so concurrent writers
        queue instead of failing,
      * add a driver-level timeout as a second line of defence.
    """
    conn = sqlite3.connect(db_file, timeout=15.0)
    try:
        conn.execute("PRAGMA busy_timeout=15000")
    except Exception:
        pass
    return conn


def db_path(filename: str) -> str:
    """Absolute path of a runtime SQLite file inside :func:`data_dir`."""
    return os.path.join(data_dir(), filename)


def project_root() -> str:
    """Absolute path of the project root (the app directory)."""
    return _PROJECT_ROOT
