"""Tests for the pre-translation report generator."""

from termprep.report import generate_report, save_report
from termprep.analyzer import AnalysisResult
from termprep.extractor import TermEntry
import tempfile
from pathlib import Path


def test_generate_report_minimal():
    """Report should generate even with minimal data."""
    report = generate_report(
        project_name="Test Project",
        notes="Quick test",
    )
    assert "# Pre-Translation Report: Test Project" in report
    assert "Quick test" in report
    assert "Recommendations" in report


def test_generate_report_with_analysis():
    """Report should include analysis results."""
    analysis = AnalysisResult(
        text="Test text here",
        lang="en",
        chars_total=14,
        chars_no_space=14,
        words_cn=0,
        words_en=3,
        paragraphs=1,
        sentences=1,
        domain="it",
        difficulty="easy",
    )
    report = generate_report(
        analysis=analysis,
        project_name="Code Project",
    )
    assert "Text Analysis" in report
    assert "it" in report
    assert "easy" in report


def test_generate_report_with_terms():
    """Report should include extracted terms."""
    terms = [
        TermEntry(term="API", frequency=5, score=0.9, word_type="abbreviation"),
        TermEntry(term="database", frequency=3, score=0.8, word_type="keyword"),
    ]
    report = generate_report(
        terms=terms,
        project_name="DB Project",
    )
    assert "Extracted Terms" in report
    assert "API" in report
    assert "database" in report


def test_save_report():
    """save_report should write to a file."""
    report = "# Test Report\n\nContent here."
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "report.md"
        save_report(report, str(path))
        content = path.read_text(encoding="utf-8")
        assert content == report


def test_generate_report_recommendations_hard():
    """Hard difficulty should generate time recommendations."""
    analysis = AnalysisResult(
        text="x" * 200,
        lang="en",
        chars_total=200,
        chars_no_space=200,
        words_cn=0,
        words_en=30,
        paragraphs=1,
        sentences=1,
        domain="legal",
        difficulty="hard",
    )
    report = generate_report(analysis=analysis)
    assert "Allow extra time" in report
    assert "hard" in report
    assert "legal" in report
