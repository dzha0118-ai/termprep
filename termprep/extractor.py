"""Terminology extraction: segmentation, frequency, POS chunking, keyword scoring."""

import re
from collections import Counter
from dataclasses import dataclass, field


@dataclass
class TermEntry:
    """A single extracted term."""
    term: str
    frequency: int = 1
    score: float = 0.0
    word_type: str = ""  # keyword, phrase, proper_noun, textrank, ngram
    positions: list[int] = field(default_factory=list)  # line numbers
    search_results: list[dict] = field(default_factory=list)  # pipeline search results


def extract(text: str, top_n: int = 30) -> list[TermEntry]:
    """Extract key terminology from text.

    Chinese text: C-Value + pke_zh + jieba TF-IDF + TextRank + POS chunking
    English text: C-Value + YAKE + regex proper nouns

    C-Value is a well-established algorithm that handles nested terms
    (e.g., scores "免疫检查点抑制剂" higher than "检查点抑制剂").

    Args:
        text: Source text.
        top_n: Maximum number of terms to return.

    Returns:
        List of TermEntry sorted by score descending.
    """
    terms: list[TermEntry] = []

    cn_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
    en_chars = len(re.findall(r"[a-zA-Z]", text))
    is_cn = cn_chars > en_chars * 0.5

    if is_cn:
        terms.extend(_extract_jieba(text, top_n))
        terms.extend(_extract_textrank(text, top_n))
        terms.extend(_extract_pke_zh(text, top_n))
        terms.extend(_extract_phrases(text, top_n))
    else:
        terms.extend(_extract_yake(text, top_n))
        terms.extend(_extract_proper_nouns(text))

    # C-Value: always applied for both languages (re-ranks with nesting awareness)
    terms.extend(_extract_cvalue(text, top_n, is_cn))

    return _merge_and_rank(terms, top_n)


def extract_file(filepath: str, top_n: int = 30) -> list[TermEntry]:
    """Extract terms from a text file."""
    with open(filepath, "r", encoding="utf-8") as f:
        text = f.read()
    return extract(text, top_n)


# ════════════════════════════════════════════════════════════════════
#  Method 1: jieba TF-IDF keyword extraction
# ════════════════════════════════════════════════════════════════════

def _extract_jieba(text: str, top_n: int) -> list[TermEntry]:
    """Extract keywords using jieba TF-IDF."""
    try:
        import jieba
        import jieba.analyse

        keywords = jieba.analyse.extract_tags(text, topK=top_n, withWeight=True)
        results: list[TermEntry] = []
        for word, weight in keywords:
            if len(word) < 2:
                continue
            results.append(TermEntry(term=word, score=weight * 0.85, word_type="keyword"))
        return results
    except ImportError:
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


# ════════════════════════════════════════════════════════════════════
#  Method 2: jieba TextRank keyword extraction
# ════════════════════════════════════════════════════════════════════

def _extract_textrank(text: str, top_n: int) -> list[TermEntry]:
    """Extract keywords using jieba TextRank algorithm.

    TextRank uses a graph-based approach similar to PageRank, building
    a co-occurrence graph of words and ranking them. It often produces
    different (and sometimes better) results than TF-IDF.
    """
    try:
        import jieba.analyse

        keywords = jieba.analyse.textrank(
            text, topK=top_n, withWeight=True,
            allowPOS=('ns', 'n', 'vn', 'v', 'nr', 'nt', 'nz', 'a', 'an'),
        )
        results: list[TermEntry] = []
        for word, weight in keywords:
            if len(word) >= 2:
                # Normalize score: TextRank weights are typically 0-1
                results.append(TermEntry(term=word, score=weight * 0.9, word_type="textrank"))
        return results
    except Exception:
        return []


# ════════════════════════════════════════════════════════════════════
#  Method 3: POS-based phrase chunking (multi-word term extraction)
# ════════════════════════════════════════════════════════════════════

# POS tags that can participate in noun phrases
_NOUN_POS = frozenset({
    'n', 'nr', 'ns', 'nt', 'nz', 'ng',   # nouns
    'vn',                                 # verbal noun (e.g. \u7814\u7a76, \u68c0\u6d4b)
    'an',                                 # noun-adjective
    'j', 'l', 'i',                        # abbreviation, idiom
})

# POS tags allowed as modifiers before nouns
_MOD_POS = frozenset({
    'n', 'nr', 'ns', 'nt', 'nz', 'ng', 'vn', 'an',
    'a', 'ad', 'ag',                       # adjectives
    'b',                                   # distinguisher (\u533b\u7597, \u6c11\u4e3b)
    'v', 'vd',                             # verb (allowed only when followed by noun)
    'eng',                                 # English
})

# Words that should never be part of a terminology phrase
_PHRASE_STOP_WORDS = frozenset({
    '\u7684', '\u4e86', '\u5728', '\u548c', '\u4e0e', '\u662f', '\u4e0d',
    '\u8fd9', '\u90a3', '\u5176', '\u4e4b', '\u800c', '\u4e14', '\u53ca', '\u7b49',
    '\u88ab', '\u628a', '\u5c06', '\u4ece', '\u5bf9', '\u5411', '\u7740', '\u8fc7',
    '\u5f97', '\u5730', '\u8981', '\u4f1a', '\u80fd', '\u53ef', '\u4ee5',
    '\u4e0a', '\u4e0b', '\u4e2d', '\u524d', '\u540e', '\u6709', '\u65e0',
    '\u6765', '\u53bb', '\u5230', '\u51fa', '\u5165', '\u5927', '\u5c0f',
    '\u591a', '\u5c11', '\u65b0', '\u65e7', '\u5f88', '\u66f4', '\u6700',
    '\u5c31', '\u90fd', '\u4e5f', '\u8fd8', '\u53c8', '\u624d',
    # Common grammatical verbs
    '\u5305\u62ec', '\u6839\u636e', '\u901a\u8fc7', '\u9700\u8981',
    '\u663e\u793a', '\u6210\u4e3a', '\u4f5c\u4e3a', '\u8fdb\u884c',
    '\u4f7f\u7528', '\u91c7\u7528', '\u5177\u6709', '\u5b58\u5728',
    '\u53d1\u751f', '\u51fa\u73b0', '\u9009\u62e9', '\u7ed3\u5408',
    '\u5bf9\u6bd4', '\u8fd1\u5e74', '\u91cd\u8981', '\u5408\u9002',
    '\u4f18\u52bf', '\u5176\u9ad8', '\u7ed3\u679c', '\u7a81\u7834',
    '\u663e\u8457', '\u8f83\u4f4e', '\u5305\u62ec',
    '\u65b9\u6cd5', '\u9886\u57df', '\u5b9e\u8df5', '\u4f18\u52bf',
    # Common adjectives that rarely form terminology
    '\u5e38\u89c1', '\u4e3b\u8981', '\u57fa\u672c', '\u4e00\u822c', '\u67d0\u4e9b',
    '\u5404\u79cd', '\u4e0d\u540c', '\u76f8\u5173', '\u5176\u4ed6', '\u8fd9\u79cd',
})

# POS that are definitely NOT content (punctuation, numbers, etc.)
_SKIP_POS = frozenset({'w', 'x', 'm', 'q', 'r', 'e', 'o', 'y', 'z', 'k', 'h', 'f'})


def _extract_phrases(text: str, top_n: int) -> list[TermEntry]:
    """Extract multi-word phrases by POS chunking with stop-word filtering.

    Approach:
    1. Segment with jieba POS tagging
    2. Mark each token as content, stop, or connector
    3. Build noun-centered phrases of 2-5 consecutive content tokens
    4. Filter: must contain at least one noun, no edge stop words
    5. Score by frequency * length bonus

    This is based on the approach used by JioNLP and textrank4zh.
    """
    try:
        import jieba.posseg as pseg

        words = list(pseg.cut(text))

        # Classify each token
        tokens: list[dict] = []
        for w in words:
            word = w.word.strip()
            flag = w.flag

            if not word:
                continue

            is_skip = (
                flag in _SKIP_POS or
                word in _PHRASE_STOP_WORDS or
                len(word) == 1 and flag not in ('eng', 'n', 'a', 'v')  # single char, non-content
            )

            is_content = not is_skip and (
                flag in _MOD_POS or
                (len(word) >= 2 and flag not in ('t', 'c', 'p', 'd', 'u', 'uj', 'ul', 'uv', 'ug'))
            )

            tokens.append({
                "word": word,
                "flag": flag,
                "is_content": is_content,
                "is_skip": is_skip,
                "len": len(word),
            })

        # Build candidate phrases: 2-5 consecutive content tokens
        candidates: list[tuple[str, int]] = []  # (phrase, start position)

        i = 0
        while i < len(tokens):
            if not tokens[i]["is_content"]:
                i += 1
                continue

            parts = []
            j = i
            while j < len(tokens) and (j - i) < 5:
                t = tokens[j]
                if t["is_content"]:
                    parts.append(t["word"])
                    j += 1
                elif t["is_skip"]:
                    break  # hard boundary
                elif t["flag"] in ('uj', 'ul', 'uv'):  # \u7684, \u5730, \u5f97
                    # Allow \u7684 as connector if surrounded by content
                    if len(parts) > 0 and (j + 1) < len(tokens) and tokens[j + 1]["is_content"]:
                        parts.append(t["word"])
                        j += 1
                    else:
                        break
                else:
                    break  # soft boundary — stop growing this phrase

            if len(parts) >= 2:
                phrase = "".join(parts)
                # Length check: 3-20 chars (shorter min to catch "靶向治疗")
                if 3 <= len(phrase) <= 24:
                    # Must contain at least one noun-tagged word
                    has_noun = any(
                        tokens[i + k]["flag"] in _NOUN_POS
                        for k in range(len(parts))
                    )
                    if has_noun:
                        candidates.append((phrase, i))

            # Advance: skip processed content tokens
            i += 1  # step by 1 to allow overlapping phrases

        # Count occurrences
        counter = Counter(p for p, _ in candidates)
        total = max(1, sum(counter.values()))
        results: list[TermEntry] = []

        for phrase, count in counter.most_common(top_n * 3):
            if count < 1:
                break
            # Frequency score * length bonus (longer = more specific)
            length_bonus = min(2.0, 1.0 + max(0, len(phrase) - 4) * 0.1)
            score = (count / total) * length_bonus
            results.append(TermEntry(
                term=phrase, frequency=count,
                score=score, word_type="phrase",
            ))

        return results
    except Exception:
        return []


# ════════════════════════════════════════════════════════════════════
#  Method 4: Regex-based proper noun extraction
# ════════════════════════════════════════════════════════════════════

def _extract_proper_nouns(text: str) -> list[TermEntry]:
    """Extract proper nouns: capitalized words, acronyms, numbers+units."""
    results: list[TermEntry] = []

    # Capitalized multi-word phrases (e.g. "Machine Learning", "United Nations")
    capitalized = re.findall(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b", text)
    for phrase in set(capitalized):
        results.append(TermEntry(term=phrase, score=0.5, word_type="proper_noun"))

    # PascalCase / camelCase identifiers (e.g. WebApplicationFactory)
    pascal = re.findall(r"\b(?:[A-Z][a-z]+){2,}\b|\b[A-Z][a-zA-Z]*[a-z][A-Z][a-zA-Z]*\b", text)
    for p in set(pascal):
        if len(p) > 4:
            results.append(TermEntry(term=p, score=0.5, word_type="proper_noun"))

    # Acronyms (2-5 uppercase letters)
    acronyms = re.findall(r"\b([A-Z]{2,5})\b", text)
    for acr in set(acronyms):
        results.append(TermEntry(term=acr, score=0.6, word_type="proper_noun"))

    # English multi-word terms in Chinese text (e.g. "deep learning", "machine translation")
    # Only use when text contains Chinese — for pure English, this regex produces garbage
    cn_count = len(re.findall(r"[\u4e00-\u9fff]", text))
    if cn_count > 0:
        en_phrases = re.findall(r"\b([A-Za-z][a-z]+(?:\s+[a-z]+){1,3})\b", text)
        for phrase in set(en_phrases):
            # Only keep reasonably short, meaningful phrases (not sentence fragments)
            if 6 < len(phrase) < 40 and phrase.count(' ') <= 3:
                # Filter common stop phrases
                low = phrase.lower()
                if not any(w in low for w in (' the ', ' a ', ' an ', ' is ', ' are ', ' was ', ' were ', ' has ', ' have ', ' had ', ' and ', ' for ', ' with ', ' that ', ' this ', ' their ', ' they ', ' shows ', ' plays ', ' role ')):
                    results.append(TermEntry(term=phrase, word_type="proper_noun"))
    else:
        # Pure English text: only extract true multi-word proper nouns
        # (already handled by capitalized pattern above)
        pass

    return results


# ════════════════════════════════════════════════════════════════════
#  Method 5: pke_zh PositionRank (Chinese keyphrase extraction)
# ════════════════════════════════════════════════════════════════════

def _extract_pke_zh(text: str, top_n: int) -> list[TermEntry]:
    """Extract Chinese keyphrases using pke_zh PositionRank.

    PositionRank is a graph-based algorithm that considers both word
    frequency and position in the document. It produces better
    multi-word phrases than pure TF-IDF or TextRank.
    """
    try:
        from pke_zh import PositionRank

        m = PositionRank()
        results_raw = m.extract(text)
        results: list[TermEntry] = []
        for word, score in results_raw[:top_n]:
            if len(word) >= 2:
                # Normalize score (PositionRank scores are typically 0-1)
                results.append(TermEntry(
                    term=word,
                    score=min(1.0, score) * 0.95,
                    word_type="positionrank",
                ))
        return results
    except Exception:
        return []


# ════════════════════════════════════════════════════════════════════
#  Method 6: YAKE (English keyphrase extraction)
# ════════════════════════════════════════════════════════════════════

_EN_STOP_WORDS = frozenset({
    'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
    'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'shall',
    'should', 'may', 'might', 'must', 'can', 'could', 'i', 'me', 'my',
    'we', 'our', 'you', 'your', 'he', 'she', 'it', 'they', 'them',
    'this', 'that', 'these', 'those', 'in', 'on', 'at', 'to', 'for',
    'of', 'from', 'by', 'with', 'about', 'as', 'into', 'through',
    'during', 'before', 'after', 'above', 'below', 'between', 'under',
    'and', 'but', 'or', 'nor', 'not', 'so', 'yet', 'if', 'than',
    'too', 'very', 'just', 'also', 'now', 'then', 'here', 'there',
    'all', 'each', 'every', 'both', 'few', 'more', 'most', 'other',
    'some', 'such', 'only', 'own', 'same', 'its', 'his', 'her',
    'their', 'our', 'no', 'up', 'out', 'over', 'down', 'off',
})


def _extract_yake(text: str, top_n: int) -> list[TermEntry]:
    """Extract English keyphrases using YAKE algorithm.

    YAKE is an unsupervised, language-independent keyphrase extraction
    method that uses statistical features (casing, position, frequency,
    relatedness, dispersion) to score candidate phrases.

    Falls back to a simple n-gram approach if YAKE is unavailable.
    """
    try:
        import yake

        # YAKE parameters
        kw_extractor = yake.KeywordExtractor(
            lan="en",
            n=3,              # max 3-gram for better phrase coverage
            dedupLim=0.65,    # more permissive dedup
            top=top_n * 2,    # get more candidates for filtering
            features=None,
        )
        raw = kw_extractor.extract_keywords(text)
        results: list[TermEntry] = []

        # YAKE returns (phrase, score) where lower = better
        # Normalize to 0-1 range (higher = better)
        if not raw:
            return results
        scores = [s for _, s in raw]
        min_s, max_s = min(scores), max(scores)
        score_range = max_s - min_s if max_s > min_s else 1.0

        for phrase, score in raw[:top_n]:
            phrase = phrase.strip()
            if len(phrase) < 3:
                continue
            # Require at least 2 content words for English phrases
            if len(phrase.split()) < 2:
                continue
            # Filter grammatical fragments
            if _is_grammar_fragment(phrase):
                continue
            normalized = 1.0 - (score - min_s) / score_range
            # Boost terms found in local domain dictionary
            boost = _get_term_boost(phrase)
            results.append(TermEntry(
                term=phrase,
                score=round(normalized * 0.85 * boost, 4),
                word_type="yake",
            ))
        return results
    except Exception:
        return _fallback_en_phrases(text, top_n)


def _fallback_en_phrases(text: str, top_n: int) -> list[TermEntry]:
    """Fallback English phrase extraction without YAKE."""
    import re
    from collections import Counter

    # Tokenize into words
    words = re.findall(r"[a-zA-Z]{2,}", text.lower())
    if len(words) < 3:
        return []

    # Build 2-4 word n-grams skipping stop words at edges
    candidates: list[tuple[str, float]] = []
    for n in (2, 3, 4):
        for i in range(len(words) - n + 1):
            ngram = words[i:i + n]
            # Must not start or end with stop word
            if ngram[0] in _EN_STOP_WORDS or ngram[-1] in _EN_STOP_WORDS:
                continue
            # Must not have too many stop words
            stop_count = sum(1 for w in ngram if w in _EN_STOP_WORDS)
            if stop_count > n // 3:
                continue
            phrase = " ".join(ngram)
            candidates.append((phrase, n * 0.1))  # length bonus

    if not candidates:
        return []

    counter = Counter(p for p, _ in candidates)
    total = max(1, sum(counter.values()))
    results: list[TermEntry] = []
    for phrase, count in counter.most_common(top_n):
        if count < 2:
            continue
        score = (count / total) * (1 + min(0.3, len(phrase.split()) * 0.1))
        results.append(TermEntry(
            term=phrase,
            frequency=count,
            score=score,
            word_type="en-ngram",
        ))
    return results


# ════════════════════════════════════════════════════════════════════
#  Method 7: C-Value (terminology extraction with nesting awareness)
# ════════════════════════════════════════════════════════════════════

def _extract_cvalue(text: str, top_n: int, is_cn: bool) -> list[TermEntry]:
    """Extract terms using the C-Value algorithm.

    C-Value is an established algorithm that:
    1. Extracts candidate noun phrases
    2. Scores by frequency × log₂(length)
    3. Penalizes nested sub-terms (e.g., "检查点" nested in "免疫检查点抑制剂")
    4. Rewards context-independent terms

    This produces significantly cleaner results than pure frequency-based methods.
    """
    import math
    from collections import defaultdict

    # Get candidate phrases (noun phrases or POS-chunked phrases)
    candidates_raw: list[str] = []
    if is_cn:
        from termprep.extractor import _extract_phrases as _phrases
        for t in _phrases(text, top_n * 3):
            candidates_raw.append(t.term)
    else:
        # English: use YAKE's phrase extraction
        try:
            import yake
            kw = yake.KeywordExtractor(lan="en", n=2, dedupLim=0.8, top=top_n * 2)
            for phrase, _ in kw.extract_keywords(text):
                phrase = phrase.strip()
                if len(phrase) > 3 and len(phrase.split()) >= 2:
                    candidates_raw.append(phrase)
        except Exception:
            pass

    if not candidates_raw:
        return []

    # Count frequencies of all candidate phrases
    freq: dict[str, int] = defaultdict(int)
    for phrase in candidates_raw:
        freq[phrase] += 1

    # Build nesting relationships
    # For CN: character-level containment. For EN: word-level containment.
    def is_nested(shorter: str, longer: str) -> bool:
        if len(shorter) >= len(longer):
            return False
        if is_cn:
            return shorter in longer
        else:
            sw = set(shorter.lower().split())
            lw = set(longer.lower().split())
            return sw.issubset(lw) and shorter.lower() != longer.lower()

    # C-Value scoring
    all_phrases = list(freq.keys())
    scores: dict[str, float] = {}

    for phrase in all_phrases:
        word_count = len(phrase) if is_cn else len(phrase.split())
        f_a = freq[phrase]

        # Find longer phrases that contain this one
        containing = [p for p in all_phrases if is_nested(phrase, p)]
        containing_freq_sum = sum(freq[p] for p in containing)

        if not containing:
            # Not nested: score = log2(word_count) * frequency
            scores[phrase] = math.log2(max(2, word_count)) * f_a / max(1, len(phrase) / 4)
        else:
            # Nested: penalize
            c = len(containing)
            scores[phrase] = math.log2(max(2, word_count)) * (f_a - containing_freq_sum / c)

    # Build results, filter very low scores
    min_score = max(0.01, max(scores.values()) * 0.05) if scores else 0
    results: list[TermEntry] = []
    for phrase, score in sorted(scores.items(), key=lambda x: -x[1]):
        if score < min_score:
            continue
        if len(phrase) < 3:
            continue
        results.append(TermEntry(
            term=phrase,
            frequency=freq[phrase],
            score=min(1.0, score / max(1, max(scores.values()))),
            word_type="cvalue",
        ))
        if len(results) >= top_n:
            break

    return results


def _is_grammar_fragment(phrase: str) -> bool:
    """Filter out grammatical fragments that aren't real terms."""
    words = phrase.lower().split()
    if not words:
        return True
    # Phrases that start/end with prepositions or auxiliary verbs
    start_stop = {'the', 'a', 'an', 'in', 'on', 'at', 'to', 'for', 'of', 'by', 'with', 'and', 'or', 'is', 'are', 'was', 'were', 'be', 'been', 'versus', 'vs', 'per'}
    end_stop = {'the', 'a', 'an', 'in', 'on', 'at', 'to', 'for', 'of', 'by', 'with', 'and', 'or', 'is', 'are', 'was', 'were', 'be', 'been'}
    if words[0] in start_stop:
        return True
    if words[-1] in end_stop:
        return True
    # Internal prepositions/conjunctions usually indicate a fragment
    internal_stop = {' of ', ' and ', ' or ', ' but ', ' with ', ' for ', ' in ', ' on ', ' at ', ' by ', ' to ', ' from ', ' as ', ' that ', ' which ', ' who ', ' when ', ' where ', ' like '}
    lowered = ' ' + phrase.lower() + ' '
    if any(token in lowered for token in internal_stop):
        return True
    # Verb + preposition fragments like "trial examined", "patients receiving"
    verb_patterns = {'examined', 'receiving', 'using', 'showed', 'compared', 'treated', 'demonstrated', 'shows', 'plays', 'includes', 'including', 'making', 'taken', 'given', 'found', 'generate', 'revolutionized', 'enabling', 'achieved', 'require', 'requires', 'required', 'requiring', 'learn', 'learns', 'learned', 'learning', 'achieve', 'achieves'}
    if len(words) >= 2 and words[-1] in verb_patterns:
        return True
    # Phrases starting with common verbs are usually sentence fragments
    start_verbs = {'is', 'are', 'was', 'were', 'has', 'have', 'had', 'can', 'could', 'will', 'would', 'should', 'may', 'might', 'must', 'shall', 'does', 'did', 'do', 'generates', 'revolutionized', 'enables', 'allows', 'makes', 'takes', 'gives', 'shows', 'achieved', 'understand', 'generate', 'enabled', 'learning', 'requires', 'required', 'achieve', 'requiring', 'learned', 'learns', 'require'}
    if words[0] in start_verbs:
        return True
    return False


def _get_term_boost(phrase: str) -> float:
    """Boost score for terms validated by the local domain dictionary.

    Avoids network calls during the extraction hot path; Wikidata validation
    is available separately via termbase.lookup_term().
    """
    try:
        from termprep.termbase import DOMAIN_TERMS
        low = phrase.lower()
        for domain, terms in DOMAIN_TERMS.items():
            if any(t.lower() == low for t in terms):
                return 1.3  # 30% boost for locally verified terms
    except Exception:
        pass
    return 1.0


# ════════════════════════════════════════════════════════════════════
#  Merge & Rank
# ════════════════════════════════════════════════════════════════════

def _merge_and_rank(terms: list[TermEntry], top_n: int) -> list[TermEntry]:
    """Merge duplicate terms across extraction methods, remove overlaps, and rank.

    Strategy:
    - Normalize keys by lowercasing and strip whitespace
    - Filter out very short single English words (uninformative)
    - Remove subsumed terms: if a longer phrase fully contains a shorter one,
      keep only the longer, more complete phrase
    - Sum frequencies and scores for merged terms
    - Prefer more descriptive word_type labels
    """
    TYPE_PRIORITY = {"phrase": 0, "proper_noun": 1, "positionrank": 2, "cvalue": 3, "textrank": 4, "keyword": 5, "yake": 6, "bigram": 7, "en-ngram": 8}

    # Step 1: Merge duplicates by normalized key
    merged: dict[str, TermEntry] = {}
    for t in terms:
        key = t.term.lower().strip()
        if not key or len(key) < 2:
            continue
        # Skip single English words that are not acronyms (uninformative as terms)
        if len(key.split()) == 1 and not re.search(r'[\u4e00-\u9fff]', key):
            # Keep acronyms like BERT, GPT, API (2-5 uppercase chars in original)
            original = t.term.strip()
            if len(original) >= 2 and len(original) <= 5 and original.isupper():
                pass  # keep acronym
            else:
                continue  # filter common single words like "machine", "learning"
        if key in merged:
            existing = merged[key]
            existing.score += t.score
            existing.frequency += t.frequency
            if TYPE_PRIORITY.get(t.word_type, 99) < TYPE_PRIORITY.get(existing.word_type, 99):
                existing.word_type = t.word_type
        else:
            merged[key] = t

    # Step 2: Remove subsumed terms (keep the longer, more complete phrase)
    sorted_keys = sorted(merged.keys(), key=lambda k: len(k), reverse=True)
    to_remove: set[str] = set()

    for long_key in sorted_keys:
        if long_key in to_remove:
            continue
        long_words = long_key.split()
        for short_key in sorted_keys:
            if short_key == long_key or short_key in to_remove:
                continue
            if len(short_key) >= len(long_key):
                continue

            # Check containment
            if ' ' in long_key or ' ' in short_key:
                # English / multi-word: word-level containment (in order)
                short_words = short_key.split()
                if len(short_words) < 2 and len(long_words) <= 2:
                    # Don't remove single-word terms from 2-word phrases
                    # e.g., keep "machine" even if "machine learning" exists
                    continue
                # Check if all short words appear in long words in order
                try:
                    idx = 0
                    for sw in short_words:
                        idx = long_words.index(sw, idx) + 1
                    # All short words found in order in long words
                    to_remove.add(short_key)
                except ValueError:
                    pass
            else:
                # Chinese / no spaces: character-level containment
                if short_key in long_key and len(long_key) - len(short_key) >= 2:
                    to_remove.add(short_key)

    # Remove subsumed terms
    for k in to_remove:
        if k in merged:
            del merged[k]

    # Step 2b: Filter grammar fragments from all extractors (not just YAKE)
    merged = {k: v for k, v in merged.items() if not _is_grammar_fragment(v.term)}

    # Step 3: Normalize scores to 0-1 range
    if merged:
        all_scores = [t.score for t in merged.values()]
        max_sc = max(all_scores)
        if max_sc > 0:
            for t in merged.values():
                t.score = round(t.score / max_sc, 4)

    sorted_terms = sorted(merged.values(), key=lambda x: (x.score, -len(x.term)), reverse=True)
    return sorted_terms[:top_n]


# ════════════════════════════════════════════════════════════════════
#  Diagnostics
# ════════════════════════════════════════════════════════════════════

def get_frequency_table(terms: list[TermEntry]) -> str:
    """Format terms as a frequency table string."""
    lines = [f"{'Term':<24} {'Freq':>6} {'Score':>8} {'Type':>14}"]
    lines.append("-" * 56)
    for t in terms:
        lines.append(
            f"{t.term:<24} {t.frequency:>6} {t.score:>8.4f} {t.word_type:>14}"
        )
    return "\n".join(lines)


def debug_extraction(text: str, top_n: int = 10) -> str:
    """Return a diagnostic string showing what each method extracted."""
    parts: list[str] = []
    parts.append("═══ TF-IDF ═══")
    for t in _extract_jieba(text, top_n):
        parts.append(f"  {t.term:20s}  score={t.score:.4f}")
    parts.append("═══ TextRank ═══")
    for t in _extract_textrank(text, top_n):
        parts.append(f"  {t.term:20s}  score={t.score:.4f}")
    parts.append("═══ POS Phrases ═══")
    for t in _extract_phrases(text, top_n):
        parts.append(f"  {t.term:20s}  freq={t.frequency}  score={t.score:.4f}")
    parts.append("═══ Proper Nouns ═══")
    for t in _extract_proper_nouns(text):
        parts.append(f"  {t.term:20s}  type={t.word_type}")
    parts.append("═══ Merged Top-{top_n} ═══")
    for t in extract(text, top_n):
        parts.append(f"  {t.term:20s}  score={t.score:.4f}  type={t.word_type}")
    return "\n".join(parts)
