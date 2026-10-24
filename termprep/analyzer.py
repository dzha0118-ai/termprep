"""Project analysis: language detection, word count, domain identification."""

import re
from dataclasses import dataclass, field
from pathlib import Path
DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "legal": [
        "contract", "agreement", "party", "clause", "liability",
        "warranty", "termination", "jurisdiction", "arbitration",
        "indemnify", "pursuant", "hereto", "furnish",
        "\u5408\u540c", "\u534f\u8bae", "\u6761\u6b3e", "\u8d23\u4efb", "\u4ef2\u88c1",
        "\u8d54\u507f", "\u6cd5\u5f8b", "\u6cd5\u9662", "\u8bc9\u8bbc",
    ],
    "medical": [
        "patient", "diagnosis", "treatment", "symptom", "surgery",
        "clinical", "pharmaceutical", "dosage", "therapy",
        "\u60a3\u8005", "\u8bca\u65ad", "\u6cbb\u7597", "\u624b\u672f", "\u836f\u7269",
        "\u4e34\u5e8a", "\u75c7\u72b6", "\u75be\u75c5",
    ],
    "finance": [
        "revenue", "asset", "liability", "equity", "dividend",
        "portfolio", "securities", "audit", "fiscal",
        "\u8d22\u52a1", "\u8d44\u4ea7", "\u8d1f\u503a", "\u80a1\u4e1c", "\u5ba1\u8ba1",
        "\u7a0e\u52a1", "\u6536\u5165", "\u6295\u8d44",
    ],
    "it": [
        "software", "hardware", "database", "API", "server",
        "algorithm", "deployment", "framework", "interface",
        "machine learning", "deep learning", "artificial intelligence",
        "neural network", "data", "cloud", "network", "programming",
        "code", "developer", "application", "system", "technology",
        "\u8f6f\u4ef6", "\u786c\u4ef6", "\u6570\u636e\u5e93", "\u670d\u52a1\u5668", "\u7b97\u6cd5",
        "\u63a5\u53e3", "\u7cfb\u7edf", "\u7f16\u7a0b", "\u4eba\u5de5\u667a\u80fd", "\u673a\u5668\u5b66\u4e60",
    ],
    "academic": [
        # §5.8 — expanded so academic prose is no longer misread as ``it``
        "hypothesis", "hypotheses", "methodology", "methodological", "analysis",
        "analyze", "conclusion", "conclusions", "abstract", "citation", "cite",
        "empirical", "theoretical", "theory", "literature review", "study",
        "studies", "research question", "qualitative", "quantitative",
        "significance", "significant", "corpus", "corpora", "framework",
        "paradigm", "survey", "questionnaire", "sample", "variable",
        "participant", "participants", "peer review", "journal", "scholarship",
        "dissertation", "thesis", "annotation", "rubric", "finding", "findings",
        "approach", "discussion", "implication", "limitation",
        "\u7814\u7a76", "\u65b9\u6cd5\u8bba", "\u65b9\u6cd5", "\u5206\u6790", "\u7ed3\u8bba", "\u6458\u8981",
        "\u5f15\u7528", "\u5b9e\u8bc1", "\u7406\u8bba", "\u5b9e\u9a8c", "\u5047\u8bbe", "\u663e\u8457\u6027",
        "\u8bed\u6599", "\u8303\u5f0f", "\u6846\u67b6", "\u53d8\u91cf", "\u6837\u672c", "\u95ee\u5377",
        "\u671f\u520a", "\u5b66\u4f4d\u8bba\u6587", "\u5b66\u672f", "\u8bba\u6587", "\u6587\u732e\u7efc\u8ff0",
        "\u4eca\u6587", "\u672c\u6587", "\u7814\u7a76\u8868\u660e", "\u7efc\u8ff0", "\u8ff0\u8bc4",
        "\u63a2\u8ba8", "\u8003\u5bdf", "\u5efa\u6784", "\u9610\u91ca", "\u8ba8\u8bba", "\u4e0d\u8db3",
    ],
    "translation": [
        # §5.8 — new category: translation & interpreting studies
        "translation", "translator", "translators", "translating", "translated",
        "interpreting", "interpreter", "post-editing", "post-editor",
        "pre-editing", "source text", "target text", "source language",
        "target language", "equivalence", "localisation", "localization",
        "terminology", "termbase", "glossary", "machine translation",
        "neural machine translation", "computer-assisted translation",
        "cat tool", "translation memory", "segmentation", "skopos",
        "faithfulness", "fidelity", "readability", "adequacy", "acceptability",
        "quality assurance", "revision", "multilingual",
        "\u7ffb\u8bd1", "\u8bd1\u8005", "\u8bd1\u5458", "\u53e3\u8bd1", "\u7b14\u8bd1",
        "\u8bd1\u540e\u7f16\u8f91", "\u8bd1\u524d\u7f16\u8f91", "\u6e90\u6587\u672c", "\u76ee\u6807\u6587\u672c",
        "\u6e90\u8bed", "\u76ee\u6807\u8bed", "\u5bf9\u7b49", "\u672c\u5730\u5316", "\u672f\u8bed",
        "\u672f\u8bed\u5e93", "\u672f\u8bed\u7ba1\u7406", "\u673a\u5668\u7ffb\u8bd1", "\u795e\u7ecf\u7f51\u7edc\u7ffb\u8bd1",
        "\u8ba1\u7b97\u673a\u8f85\u52a9\u7ffb\u8bd1", "\u7ffb\u8bd1\u8bb0\u5fc6", "\u5207\u5206", "\u5bf9\u9f50",
        "\u7ffb\u8bd1\u8d28\u91cf", "\u5fe0\u5b9e", "\u53ef\u8bfb\u6027", "\u5145\u5206\u6027", "\u53ef\u63a5\u53d7\u6027",
        "\u4fee\u8ba2", "\u5de5\u4f5c\u6d41",
    ],
    "humanities": [
        # §5.8 — new category: humanities / discourse studies
        "discourse", "narrative", "rhetoric", "semiotics", "hermeneutics",
        "ideology", "identity", "postcolonial", "ethnography", "phenomenology",
        "aesthetics", "cultural", "culture", "ethics", "gender",
        "\u8bdd\u8bed", "\u53d9\u4e8b", "\u4fee\u8f9e", "\u7b26\u53f7\u5b66", "\u9610\u91ca\u5b66",
        "\u610f\u8bc6\u5f62\u6001", "\u8eab\u4efd", "\u6587\u5316", "\u540e\u6b96\u6c11", "\u6027\u522b",
        "\u4f26\u7406", "\u7f8e\u5b66", "\u73b0\u8c61\u5b66", "\u6c11\u65cf\u5fd7",
    ],
}


@dataclass
class AnalysisResult:
    """Result of project analysis."""
    text: str = ""
    lang: str = ""  # zh, en, mixed
    chars_total: int = 0
    chars_no_space: int = 0
    words_cn: int = 0  # Chinese word estimate
    words_en: int = 0  # English word count
    paragraphs: int = 0
    sentences: int = 0
    domain: str = ""
    domain_scores: dict[str, float] = field(default_factory=dict)
    difficulty: str = "medium"  # easy, medium, hard


def analyze(text: str) -> AnalysisResult:
    """Run full project analysis on input text.

    Args:
        text: Source text to analyze.

    Returns:
        AnalysisResult with language, counts, domain, difficulty.
    """
    result = AnalysisResult(text=text)
    result.lang = _detect_language(text)
    result.chars_total = len(text)
    result.chars_no_space = len(text.replace(" ", "").replace("\n", ""))
    result.paragraphs = _count_paragraphs(text)
    result.sentences = _count_sentences(text, result.lang)
    result.words_cn = _count_chinese_words(text)
    result.words_en = _count_english_words(text)
    result.domain_scores = _identify_domain(text)
    result.domain = _best_domain(result.domain_scores)
    result.difficulty = _assess_difficulty(result)
    return result


def analyze_file(filepath: str) -> AnalysisResult:
    """Analyze a text file.

    Args:
        filepath: Path to the text file.

    Returns:
        AnalysisResult.
    """
    text = Path(filepath).read_text(encoding="utf-8")
    return analyze(text)


def _detect_language(text: str) -> str:
    """Detect language: zh, en, or mixed."""
    cn = len(re.findall(r"[\u4e00-\u9fff]", text))
    en = len(re.findall(r"[a-zA-Z]", text))
    total = cn + en
    if total == 0:
        return "unknown"
    cn_ratio = cn / total
    if cn_ratio > 0.7:
        return "zh"
    elif cn_ratio < 0.3:
        return "en"
    return "mixed"


def _count_paragraphs(text: str) -> int:
    """Count paragraphs separated by blank lines."""
    paras = re.split(r"\n\s*\n", text.strip())
    return len([p for p in paras if p.strip()])


def _count_sentences(text: str, lang: str) -> int:
    """Count sentences using punctuation."""
    if lang == "zh":
        return len(re.findall(r"[\u3002\uff1f\uff01\uff1b]", text))
    return len(re.findall(r"[.!?]+", text))


def _count_chinese_words(text: str) -> int:
    """Estimate Chinese word count (approx 1.5 chars per word).

    §5.9b — returns **0** for text containing no CJK characters. The previous
    implementation clamped with ``max(1, ...)``, which reported ``words_cn: 1``
    for purely English input.
    """
    cn_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
    if cn_chars == 0:
        return 0
    return int(cn_chars / 1.5)


def _count_english_words(text: str) -> int:
    """Count English words."""
    en_text = " ".join(re.findall(r"[a-zA-Z]+", text))
    return len(en_text.split())


_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _keyword_pattern(kw: str) -> "re.Pattern[str]":
    """Build a matcher for a single domain keyword.

    CJK keywords match as substrings (Chinese has no word boundaries); ASCII
    keywords require word boundaries, so ``api`` no longer matches ``rapid`` and
    ``data`` no longer matches ``database``. Those loose substring matches were
    what let generic IT vocabulary swamp the academic category (§5.8).
    """
    if _CJK_RE.search(kw):
        return re.compile(re.escape(kw), re.IGNORECASE)
    return re.compile(
        r"(?<![A-Za-z0-9])" + re.escape(kw) + r"(?![A-Za-z0-9])", re.IGNORECASE
    )


_KEYWORD_PATTERNS = None


def _patterns() -> dict:
    """Compile (once) and cache the keyword matchers per domain."""
    global _KEYWORD_PATTERNS
    if _KEYWORD_PATTERNS is None:
        _KEYWORD_PATTERNS = {
            domain: [_keyword_pattern(kw) for kw in kws]
            for domain, kws in DOMAIN_KEYWORDS.items()
        }
    return _KEYWORD_PATTERNS


def _identify_domain(text: str) -> dict[str, float]:
    """Score text against known domains using a saturating distinct-hit count.

    Every distinct keyword contributes at most one hit, so repeating one word
    cannot inflate a domain. The score is ``hits / (hits + 3)``: a single hit
    (0.25) already clears the 0.1 floor in :func:`_best_domain`, while a text
    saturated with domain vocabulary approaches 1.0.

    This replaces the previous length-derived threshold, which could not fire on
    short academic texts and therefore reported ``academic: 0.0`` even for
    translation-studies papers (§5.8).

    Returns:
        Dict of domain -> score (0-1).
    """
    scores: dict[str, float] = {}
    for domain, patterns in _patterns().items():
        hits = sum(1 for p in patterns if p.search(text))
        scores[domain] = hits / (hits + 3.0) if hits else 0.0
    return scores


def _best_domain(scores: dict[str, float]) -> str:
    """Return the highest-scoring domain, or 'general' if none."""
    if not scores:
        return "general"
    best = max(scores, key=scores.get)
    if scores[best] < 0.1:
        return "general"
    return best


def _assess_difficulty(result: AnalysisResult) -> str:
    """Assess translation difficulty based on text characteristics."""
    score = 0
    # Long sentences increase difficulty
    if result.sentences > 0:
        avg_sentence_len = result.chars_no_space / result.sentences
        if avg_sentence_len > 80:
            score += 2
        elif avg_sentence_len > 50:
            score += 1
    # Mixed language is harder
    if result.lang == "mixed":
        score += 1
    # Technical domain is harder
    if result.domain not in ("general", ""):
        score += 1
    # Word count contributes
    total_words = result.words_cn + result.words_en
    if total_words > 5000:
        score += 2
    elif total_words > 1000:
        score += 1
    if score >= 4:
        return "hard"
    elif score >= 2:
        return "medium"
    return "easy"


def get_summary(result: AnalysisResult) -> str:
    """Generate a human-readable summary."""
    total_words = result.words_cn + result.words_en
    lines = [
        f"Language: {result.lang}",
        f"Characters: {result.chars_total} (no spaces: {result.chars_no_space})",
        f"Words (estimated): {total_words} (CN: {result.words_cn}, EN: {result.words_en})",
        f"Paragraphs: {result.paragraphs}",
        f"Sentences: {result.sentences}",
        f"Domain: {result.domain}",
        f"Difficulty: {result.difficulty}",
    ]
    return "\n".join(lines)
