"""Tests for terminology extractor."""

from termprep.extractor import extract, _extract_proper_nouns, _extract_ngrams


SAMPLE_CN = (
    "\u4eba\u5de5\u667a\u80fd\u6280\u672f\u5728\u533b\u7597\u8bca\u65ad\u9886\u57df\u7684\u5e94\u7528"
    "\u65e5\u76ca\u5e7f\u6cdb\u3002\u4e34\u5e8a\u6570\u636e\u8868\u660e\uff0cAI\u8f85\u52a9\u8bca\u65ad"
    "\u53ef\u4ee5\u663e\u8457\u63d0\u9ad8\u75be\u75c5\u68c0\u51fa\u7387\u3002"
    "\u4eba\u5de5\u667a\u80fd\u5728\u4e34\u5e8a\u4e2d\u7684\u5e94\u7528\u4e0d\u65ad\u6269\u5c55\u3002"
)

SAMPLE_EN = (
    "The WebApplicationFactory provides in-memory TestServer for "
    "integration testing. ConfigureWebHost uses IWebHostBuilder "
    "internally."
)


def test_extract_returns_terms():
    results = extract(SAMPLE_CN, top_n=10)
    assert len(results) > 0
    assert len(results) <= 10


def test_extract_terms_have_fields():
    results = extract(SAMPLE_CN)
    for t in results:
        assert t.term
        assert t.frequency >= 1
        assert t.score >= 0
        assert t.word_type in ("keyword", "ngram", "proper_noun", "bigram")


def test_extract_scored_descending():
    results = extract(SAMPLE_CN, top_n=5)
    scores = [t.score for t in results]
    assert scores == sorted(scores, reverse=True)


def test_extract_ngrams():
    results = _extract_ngrams(SAMPLE_CN, top_n=10)
    assert isinstance(results, list)


def test_extract_proper_nouns():
    results = _extract_proper_nouns(SAMPLE_EN)
    # Should find WebApplicationFactory, TestServer, ConfigureWebHost, IWebHostBuilder
    terms = [t.term for t in results]
    assert any("TestServer" in t for t in terms) or any("IWebHostBuilder" in t for t in terms)


def test_extract_empty_text():
    results = extract("")
    assert results == []
