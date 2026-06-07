"""Terminology extraction: segmentation, frequency, N-gram, keyword scoring."""

import re
from collections import Counter
from dataclasses import dataclass, field
@dataclass
class TermEntry:
    """A single extracted term."""
    term: str
    frequency: int = 1
    score: float = 0.0
    word_type: str = ""  # keyword, ngram, proper_noun
    positions: list[int] = field(default_factory=list)  # line numbers


def extract(text: str, top_n: int = 30) -> list[TermEntry]:
    """Extract key terminology from text.

    Combines jieba segmentation, frequency analysis, and N-gram extraction.

    Args:
        text: Source text.
        top_n: Maximum number of terms to return.

    Returns:
        List of TermEntry sorted by score descending.
    """
    terms: list[TermEntry] = []
    terms.extend(_extract_jieba(text, top_n))
    terms.extend(_extract_ngrams(text, top_n))
    terms.extend(_extract_proper_nouns(text))
    return _merge_and_rank(terms, top_n)


def extract_file(filepath: str, top_n: int = 30) -> list[TermEntry]:
    """Extract terms from a text file."""
    with open(filepath, "r", encoding="utf-8") as f:
        text = f.read()
    return extract(text, top_n)


def _extract_jieba(text: str, top_n: int) -> list[TermEntry]:
    """Extract keywords using jieba segmentation and frequency."""
    try:
        import jieba
        import jieba.analyse

        # Use TF-IDF extraction
        keywords = jieba.analyse.extract_tags(text, topK=top_n, withWeight=True)
        results: list[TermEntry] = []
        for word, weight in keywords:
            if len(word) < 2:
                continue
            results.append(TermEntry(term=word, score=weight, word_type="keyword"))
        return results
    except ImportError:
        # Fallback: character-based frequency for Chinese
        return _fallback_chinese_freq(text, top_n)


def _fallback_chinese_freq(text: str, top_n: int) -> list[TermEntry]:
    """Fallback: count Chinese bigrams by frequency."""
    cn_chars = re.findall(r"[\u4e00-\u9fff]", text)
    bigrams = ["".join(cn_chars[i : i + 2]) for i in range(len(cn_chars) - 1)]
    counter = Counter(bigrams)
    total = max(1, sum(counter.values()))
    return [
        TermEntry(term=bg, frequency=count, score=count / total, word_type="bigram")
        for bg, count in counter.most_common(top_n)
        if len(bg) == 2
    ]


def _extract_ngrams(text: str, top_n: int) -> list[TermEntry]:
    """Extract meaningful N-grams from text."""
    results: list[TermEntry] = []

    # Chinese: extract 2-5 character chains
    cn_chars = "".join(re.findall(r"[\u4e00-\u9fff]", text))
    for n in range(3, 6):
        ngrams = [cn_chars[i : i + n] for i in range(len(cn_chars) - n + 1)]
        counter = Counter(ngrams)
        total = sum(counter.values())
        for ng, count in counter.most_common(top_n):
            if count < 2:  # Require at least 2 occurrences
                break
            results.append(
                TermEntry(
                    term=ng,
                    frequency=count,
                    score=(count / total) * (n * 0.3),  # Longer n-grams weighted higher
                    word_type="ngram",
                )
            )
    return results


def _extract_proper_nouns(text: str) -> list[TermEntry]:
    """Extract proper nouns: capitalized words, acronyms, numbers+units."""
    results: list[TermEntry] = []

    # Capitalized multi-word phrases
    capitalized = re.findall(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b", text)
    for phrase in set(capitalized):
        results.append(
            TermEntry(term=phrase, word_type="proper_noun")
        )

    # PascalCase / camelCase identifiers (e.g. WebApplicationFactory, IWebHostBuilder)
    pascal = re.findall(r"\b(?:[A-Z][a-z]+){2,}\b|\b[A-Z][a-zA-Z]*[a-z][A-Z][a-zA-Z]*\b", text)
    for p in set(pascal):
        if len(p) > 4:
            results.append(
                TermEntry(term=p, word_type="proper_noun")
            )

    # Acronyms (2-5 uppercase letters)
    acronyms = re.findall(r"\b([A-Z]{2,5})\b", text)
    for acr in set(acronyms):
        results.append(
            TermEntry(term=acr, word_type="proper_noun")
        )

    return results


def _merge_and_rank(terms: list[TermEntry], top_n: int) -> list[TermEntry]:
    """Merge duplicate terms, sum scores, and rank."""
    merged: dict[str, TermEntry] = {}
    for t in terms:
        key = t.term.lower()
        if key in merged:
            merged[key].score += t.score
            merged[key].frequency += t.frequency
        else:
            merged[key] = t
    sorted_terms = sorted(merged.values(), key=lambda x: x.score, reverse=True)
    return sorted_terms[:top_n]


def get_frequency_table(terms: list[TermEntry]) -> str:
    """Format terms as a frequency table string."""
    lines = [f"{'Term':<20} {'Freq':>6} {'Score':>8} {'Type':>12}"]
    lines.append("-" * 50)
    for t in terms:
        lines.append(
            f"{t.term:<20} {t.frequency:>6} {t.score:>8.4f} {t.word_type:>12}"
        )
    return "\n".join(lines)
