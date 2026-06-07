"""Tests for the database module."""

import tempfile
import os
from pathlib import Path
from termprep.db import TermDB


def test_db_init_creates_tables():
    """Database initialization should create tables."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    try:
        db = TermDB(db_path=db_path)
        with db._get_conn() as conn:
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            table_names = [t[0] for t in tables]
        assert "terms" in table_names
        assert "associations" in table_names
    finally:
        del db
        try:
            os.unlink(db_path)
        except PermissionError:
            pass  # Windows SQLite handle may not be released yet
