"""Tests for the search engine."""

from termprep.searcher import search_term


def test_search_empty_result():
    """Search for a non-existent term should return empty list."""
    results = search_term("xyzzy_nonexistent_12345", source="local")
    assert isinstance(results, list)
    assert len(results) == 0


def test_search_returns_correct_structure():
    """Results should have required fields."""
    results = search_term("\u4eba\u5de5\u667a\u80fd", source="all")
    for r in results:
        assert "word" in r
        assert "type" in r
        assert "source" in r
        assert "score" in r
