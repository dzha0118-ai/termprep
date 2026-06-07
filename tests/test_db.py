"""Tests for the enhanced terminology database."""

import gc
import tempfile
import os
from pathlib import Path

from termprep.db import TermDB, list_termbases, init_termbase, delete_termbase


def test_db_init_creates_tables():
    """Database init should create terms and associations tables."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    try:
        db = TermDB(db_path=db_path)
        with db._get_conn() as conn:
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            table_names = [r["name"] if hasattr(r, "name") else r[0] for r in tables]
            assert "terms" in table_names
            assert "associations" in table_names
            assert "meta" in table_names
    finally:
        del db
        try:
            os.unlink(db_path)
        except PermissionError:
            pass


def test_db_add_and_get_term():
    """Adding a term should return its ID, getting should retrieve it."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    try:
        db = TermDB(db_path=db_path)
        tid = db.add_term("artificial intelligence",
                          translation="\u4eba\u5de5\u667a\u80fd",
                          domain="it", status="confirmed")
        assert tid > 0
        term = db.get_term(tid)
        assert term is not None
        assert term["word"] == "artificial intelligence"
        assert term["domain"] == "it"
        assert term["status"] == "confirmed"
    finally:
        del db
        try:
            os.unlink(db_path)
        except PermissionError:
            pass


def test_db_update_term():
    """Updating a term should persist changes."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    try:
        db = TermDB(db_path=db_path)
        tid = db.add_term("test", translation="\u6d4b\u8bd5")
        db.update_term(tid, status="confirmed", domain="general")
        term = db.get_term(tid)
        assert term["status"] == "confirmed"
        assert term["domain"] == "general"
    finally:
        del db
        try:
            os.unlink(db_path)
        except PermissionError:
            pass


def test_db_delete_term():
    """Deleting a term should remove it and its associations."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    try:
        db = TermDB(db_path=db_path)
        tid = db.add_term("delete-me")
        db.add_association(tid, "related-word")
        db.delete_term(tid)
        assert db.get_term(tid) is None
        assert db.get_associations(tid) == []
    finally:
        del db
        try:
            os.unlink(db_path)
        except PermissionError:
            pass


def test_db_search_terms():
    """search_terms should find by word, translation, or domain."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    try:
        db = TermDB(db_path=db_path)
        db.add_term("machine learning", domain="it")
        db.add_term("deep learning", domain="it")
        results = db.search_terms("machine")
        assert len(results) >= 1
        assert results[0]["word"] == "machine learning"
    finally:
        del db
        try:
            os.unlink(db_path)
        except PermissionError:
            pass


def test_db_filter_by_domain():
    """Filtering by domain should return matching terms."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    try:
        db = TermDB(db_path=db_path)
        db.add_term("contract", domain="legal")
        db.add_term("diagnosis", domain="medical")
        db.add_term("algorithm", domain="it")
        legal = db.filter_by_domain("legal")
        assert len(legal) == 1
        assert legal[0]["word"] == "contract"
    finally:
        del db
        try:
            os.unlink(db_path)
        except PermissionError:
            pass


def test_db_filter_by_status():
    """Filtering by status should return matching terms."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    try:
        db = TermDB(db_path=db_path)
        db.add_term("confirmed-term", status="confirmed")
        db.add_term("draft-term", status="draft")
        confirmed = db.filter_by_status("confirmed")
        assert len(confirmed) == 1
        assert confirmed[0]["word"] == "confirmed-term"
    finally:
        del db
        try:
            os.unlink(db_path)
        except PermissionError:
            pass


def test_db_stats():
    """get_stats should return correct counts."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    try:
        db = TermDB(db_path=db_path)
        for i in range(5):
            db.add_term(f"term-{i}", domain="it")
        for i in range(3):
            db.add_term(f"legal-{i}", domain="legal")
        stats = db.get_stats()
        assert stats["total_terms"] == 8
        assert stats["by_domain"]["it"] == 5
        assert stats["by_domain"]["legal"] == 3
    finally:
        del db
        try:
            os.unlink(db_path)
        except PermissionError:
            pass


def test_db_merge():
    """Merge should import terms from another database."""
    tmpdir = tempfile.mkdtemp()
    try:
        src_path = Path(tmpdir) / "source.db"
        tgt_path = Path(tmpdir) / "target.db"
        src = TermDB(db_path=src_path)
        src.add_term("source-term-1", domain="it")
        src.add_term("source-term-2", domain="legal")
        tgt = TermDB(db_path=tgt_path)
        tgt.add_term("existing-term", domain="general")

        stats = tgt.merge(str(src_path))
        assert stats["added"] == 2
        assert stats["total_other"] == 2
        assert tgt.get_stats()["total_terms"] == 3
    finally:
        del src, tgt
        gc.collect()
        try:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass


def test_db_merge_dedup():
    """Merge with dedup should skip existing terms."""
    tmpdir = tempfile.mkdtemp()
    try:
        src_path = Path(tmpdir) / "source.db"
        tgt_path = Path(tmpdir) / "target.db"
        src = TermDB(db_path=src_path)
        src.add_term("common-term", translation="\u516c\u5171")
        tgt = TermDB(db_path=tgt_path)
        tgt.add_term("common-term", translation="\u516c\u5171")
        stats = tgt.merge(str(src_path))
        assert stats["added"] == 0
        assert stats["skipped"] == 1
    finally:
        del src, tgt
        gc.collect()
        try:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass


def test_db_import_csv_dedup():
    """Import CSV with dedup should skip duplicates."""
    tmpdir = tempfile.mkdtemp()
    try:
        db_path = Path(tmpdir) / "test.db"
        db = TermDB(db_path=db_path)
        db.add_term("existing", translation="\u5b58\u5728")
        csv_path = Path(tmpdir) / "test.csv"
        csv_path.write_text(
            "word,translation\nexisting,\u5b58\u5728\nnew-term,\u65b0\u8bcd\n",
            encoding="utf-8"
        )
        count = db.import_csv(str(csv_path), dedup=True)
        assert count == 1  # only the new term
    finally:
        del db
        gc.collect()
        try:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass


def test_db_meta():
    """Metadata should persist."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    try:
        db = TermDB(db_path=db_path)
        db.set_meta("domain", "legal")
        assert db.get_meta("domain") == "legal"
    finally:
        del db
        try:
            os.unlink(db_path)
        except PermissionError:
            pass


def test_list_termbases():
    """list_termbases should return a list."""
    dbs = list_termbases()
    assert isinstance(dbs, list)


def test_init_and_delete_termbase():
    """init_termbase and delete_termbase should work."""
    import time
    name = f"test-{int(time.time())}"
    db = init_termbase(name)
    db_path = db.db_path
    assert db_path.exists()
    del db
    gc.collect()
    delete_termbase(name)
    assert not db_path.exists()
