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
        "\u8f6f\u4ef6", "\u786c\u4ef6", "\u6570\u636e\u5e93", "\u670d\u52a1\u5668", "\u7b97\u6cd5",
        "\u63a5\u53e3", "\u7cfb\u7edf", "\u7f16\u7a0b",
    ],
    "academic": [
        "hypothesis", "methodology", "analysis", "conclusion",
        "abstract", "citation", "empirical", "theoretical",
        "\u7814\u7a76", "\u65b9\u6cd5", "\u5206\u6790", "\u7ed3\u8bba", "\u6458\u8981",
        "\u5b9e\u8bc1", "\u7406\u8bba", "\u5b9e\u9a8c",
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
    """Estimate Chinese word count (approx 1.5 chars per word)."""
    cn_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
    return max(1, int(cn_chars / 1.5))


def _count_english_words(text: str) -> int:
    """Count English words."""
    en_text = " ".join(re.findall(r"[a-zA-Z]+", text))
    return len(en_text.split())


def _identify_domain(text: str) -> dict[str, float]:
    """Score text against known domains.

    Returns:
        Dict of domain -> score (0-1).
    """
    text_lower = text.lower()
    scores: dict[str, float] = {}
    for domain, keywords in DOMAIN_KEYWORDS.items():
        hits = sum(1 for kw in keywords if kw in text_lower)
        scores[domain] = min(1.0, hits / max(1, len(keywords) * 0.1))
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
