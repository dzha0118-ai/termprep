"""Tests for dictionary source integrations."""

from termprep.sources.youdao import YoudaoSource
from termprep.sources.webster import WebsterSource
from termprep.sources.base import SearchResult


def test_youdao_unconfigured_returns_empty():
    """Without API key, Youdao source returns empty results."""
    source = YoudaoSource(api_key="", api_secret="")
    assert source.available is False
    results = source.search("test")
    assert results == []


def test_webster_unconfigured_returns_empty():
    """Without API key, Webster source returns empty results."""
    source = WebsterSource(api_key="")
    assert source.available is False
    results = source.search("test")
    assert results == []


def test_search_result_fields():
    """SearchResult should have all required fields."""
    r = SearchResult(
        query="hello",
        word="hi",
        word_type="synonym",
        source="webster",
        score=0.9,
    )
    assert r.query == "hello"
    assert r.word == "hi"
    assert r.word_type == "synonym"
    assert r.source == "webster"
    assert r.score == 0.9
