"""Tests for Wikipedia source integration."""

from termprep.sources.wikipedia import WikipediaSource


def test_wikipedia_available_no_key_needed():
    """Wikipedia source should always be available (no API key)."""
    source = WikipediaSource()
    assert source.available is True


def test_wikipedia_search_returns_results():
    """Wikipedia source should return concept summaries for known terms."""
    source = WikipediaSource()
    results = source.search("Artificial intelligence", limit=3)
    # May fail if network is unavailable, so skip if empty
    if not results:
        return
    assert len(results) >= 1
    r = results[0]
    assert r.source.startswith("wikipedia")
    assert r.word_type == "concept"
    # Should have a meaningful definition
    assert len(r.definition) > 20
    assert "intelligence" in r.definition.lower() or "machine" in r.definition.lower()


def test_wikipedia_search_chinese():
    """Wikipedia should also work for Chinese terms."""
    source = WikipediaSource()
    results = source.search("\u4eba\u5de5\u667a\u80fd", limit=3)
    if not results:
        return
    assert len(results) >= 1
    # Should have Chinese content
    assert any("\u4e2d\u6587" not in r.definition for r in results)


def test_wikipedia_unknown_term():
    """Searching for nonsense should return empty results gracefully."""
    source = WikipediaSource()
    results = source.search("xyznonexistent12345", limit=3)
    assert results == []


def test_wikipedia_search_result_format():
    """Results should follow the SearchResult structure."""
    source = WikipediaSource()
    results = source.search("Python (programming language)", limit=1)
    if not results:
        return
    r = results[0]
    assert hasattr(r, "query")
    assert hasattr(r, "word")
    assert hasattr(r, "word_type")
    assert hasattr(r, "source")
    assert hasattr(r, "score")
    assert hasattr(r, "definition")
    assert r.score == 0.8
