"""Tests for project analyzer."""

from termprep.analyzer import analyze, analyze_file, _detect_language


SAMPLE_CN = (
    "\u4eba\u5de5\u667a\u80fd\u6280\u672f\u5728\u533b\u7597\u8bca\u65ad\u9886\u57df\u7684\u5e94\u7528"
    "\u65e5\u76ca\u5e7f\u6cdb\u3002\u4e34\u5e8a\u6570\u636e\u8868\u660e\uff0cAI\u8f85\u52a9\u8bca\u65ad"
    "\u53ef\u4ee5\u663e\u8457\u63d0\u9ad8\u75be\u75c5\u68c0\u51fa\u7387\u3002"
    "\u672c\u5408\u540c\u7531\u7532\u4e59\u53cc\u65b9\u7b7e\u8ba2\uff0c\u53cc\u65b9\u5e94\u4e25\u683c"
    "\u9075\u5b88\u5408\u540c\u6761\u6b3e\u3002"
)

SAMPLE_EN = (
    "The software deployment framework provides a robust interface for "
    "database integration. Clinical studies show that AI-assisted diagnosis "
    "can significantly improve patient outcomes. The contract between "
    "the parties shall be governed by applicable law."
)


def test_detect_language_cn():
    assert _detect_language(SAMPLE_CN) == "zh"


def test_detect_language_en():
    assert _detect_language(SAMPLE_EN) == "en"


def test_detect_language_mixed():
    text = SAMPLE_CN + " " + SAMPLE_EN
    result = _detect_language(text)
    assert result in ("zh", "en", "mixed")


def test_analyze_cn_returns_counts():
    result = analyze(SAMPLE_CN)
    assert result.lang == "zh"
    assert result.chars_total > 0
    assert result.chars_no_space > 0
    assert result.words_cn > 0
    assert result.paragraphs > 0
    assert result.sentences > 0


def test_analyze_en_returns_counts():
    result = analyze(SAMPLE_EN)
    assert result.lang == "en"
    assert result.words_en > 0
    assert result.sentences > 0


def test_analyze_identifies_domain():
    result = analyze(SAMPLE_CN)
    assert result.domain in ("medical", "legal", "general", "it", "")


def test_analyze_difficulty_level():
    result = analyze(SAMPLE_CN)
    assert result.difficulty in ("easy", "medium", "hard")


def test_analyze_empty_text():
    result = analyze("")
    assert result.lang == "unknown"
    assert result.chars_total == 0
