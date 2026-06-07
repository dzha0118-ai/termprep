"""Tests for multi-format exporter."""

import json
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from termprep.db import TermDB
from termprep.exporter import export_csv, export_xlsx, export_tbx, export_json


def _make_test_db() -> tuple[TermDB, Path]:
    """Create a temp database with sample terms."""
    tmp = Path(tempfile.mkdtemp())
    db_path = tmp / "test.db"
    db = TermDB(db_path=db_path)
    db.add_term("artificial intelligence", translation="\u4eba\u5de5\u667a\u80fd",
                domain="it", type_="noun", status="confirmed")
    db.add_term("machine learning", translation="\u673a\u5668\u5b66\u4e60",
                domain="it", status="draft")
    db.add_term("contract", translation="\u5408\u540c",
                domain="legal", status="confirmed")
    return db, tmp


def test_export_csv():
    """CSV export should produce a valid CSV file."""
    db, tmp = _make_test_db()
    out = str(tmp / "export.csv")
    count = export_csv(db, out)
    assert count == 3

    content = Path(out).read_bytes()
    assert content.startswith(b"\xef\xbb\xbf")  # BOM for Excel
    text = Path(out).read_text(encoding="utf-8")
    assert "artificial intelligence" in text
    assert "\u4eba\u5de5\u667a\u80fd" in text


def test_export_csv_with_status_filter():
    """CSV export with status filter should only include matching terms."""
    db, tmp = _make_test_db()
    out = str(tmp / "export-confirmed.csv")
    count = export_csv(db, out, status_filter="confirmed")
    assert count == 2  # AI + contract


def test_export_xlsx():
    """XLSX export should produce a valid Excel file."""
    db, tmp = _make_test_db()
    out = str(tmp / "export.xlsx")
    count = export_xlsx(db, out)
    assert count == 3
    assert Path(out).exists()
    assert Path(out).stat().st_size > 100  # non-trivial file


def test_export_tbx():
    """TBX export should produce valid XML (ISO 30042)."""
    db, tmp = _make_test_db()
    out = str(tmp / "export.tbx")
    count = export_tbx(db, out)
    assert count == 3
    assert Path(out).exists()

    # Parse and validate XML structure
    tree = ET.parse(out)
    root = tree.getroot()
    assert "martif" in root.tag or root.tag.endswith("martif")
    assert root.attrib.get("type") == "TBX"

    # Should have termEntry elements
    entries = root.findall(".//{urn:iso:std:iso:30042:ed-1}termEntry")
    if not entries:
        entries = root.findall(".//termEntry")
    assert len(entries) >= 3


def test_export_json():
    """JSON export should produce valid structured JSON."""
    db, tmp = _make_test_db()
    out = str(tmp / "export.json")
    count = export_json(db, out)
    assert count == 3

    data = json.loads(Path(out).read_text(encoding="utf-8"))
    assert data["termbase"] == "test"
    assert data["total"] == 3
    assert len(data["terms"]) == 3
    assert data["stats"]["confirmed"] == 2
    assert data["terms"][0]["word"] == "artificial intelligence"


def test_export_empty_db():
    """Exporting an empty database should produce valid files."""
    tmp = Path(tempfile.mkdtemp())
    db = TermDB(db_path=tmp / "empty.db")

    count_csv = export_csv(db, str(tmp / "empty.csv"))
    assert count_csv == 0

    count_json = export_json(db, str(tmp / "empty.json"))
    assert count_json == 0

    data = json.loads(Path(tmp / "empty.json").read_text(encoding="utf-8"))
    assert data["total"] == 0
    assert data["terms"] == []
