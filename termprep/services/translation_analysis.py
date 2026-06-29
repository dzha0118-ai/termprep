"""Translation difficulty analysis — rule-based text analysis for translation planning.

This module analyzes source text to identify:
- Per-segment translation difficulty (sentence length, complexity, technical terms)
- Specific challenges (idioms, cultural references, ambiguous terms, long sentences)
- Translation strategy recommendations
- Term consistency requirements

All analysis works without AI — purely rule-based, so it works offline / on HF Space.
"""

from __future__ import annotations

import re
from typing import Any


def analyze_translation_difficulty(
    source_text: str,
    lang: str = "auto",
    domain: str = "general",
    terms: list[dict] | None = None,
    glossary: list[dict] | None = None,
) -> dict[str, Any]:
    """Analyze text for translation difficulty.

    Returns a structured analysis dict that can be embedded in the MD report.
    """
    segments = _segment_text(source_text, lang)
    term_words = _build_term_set(terms, glossary)

    per_segment: list[dict[str, Any]] = []
    overall_flags: list[str] = []

    for seg in segments:
        analysis = _analyze_segment(seg, lang, domain, term_words)
        per_segment.append(analysis)
        for flag in analysis.get("flags", []):
            if flag not in overall_flags:
                overall_flags.append(flag)

    return {
        "total_segments": len(segments),
        "overall_difficulty": _assess_overall(per_segment, lang, domain),
        "overall_flags": overall_flags,
        "segment_analyses": per_segment,
        "recommendations": _generate_recommendations(overall_flags, domain, lang),
    }


def _build_term_set(
    terms: list[dict] | None,
    glossary: list[dict] | None,
) -> set[str]:
    words: set[str] = set()
    if terms:
        for t in terms:
            w = t.get("term", "") or t.get("word", "")
            if w and len(w) >= 2:
                words.add(w.lower())
    if glossary:
        for g in glossary:
            w = g.get("term", "") or g.get("word", "")
            if w and len(w) >= 2:
                words.add(w.lower())
    return words


def _segment_text(text: str, lang: str) -> list[str]:
    """Split text into analyzable segments (paragraphs or sentences)."""
    paragraphs = re.split(r"\n\s*\n", text.strip())
    segments: list[str] = []
    for p in paragraphs:
        p = p.strip()
        if not p:
            continue
        if len(p) > 500:
            segs = re.split(r"(?<=[.!?。！？])\s+", p)
            segments.extend(s.strip() for s in segs if s.strip())
        else:
            segments.append(p)
    return segments or [text]


DIFFICULT_KEYWORDS_EN = {
    "idiom": r"\b(?:piece of cake|spill the beans|hit the nail|once in a blue moon|bite the bullet|break the ice|cost an arm|ballpark figure|cutting edge|state of the art|paradigm shift|think outside the box)\b",
    "legal": r"\b(?:hereinafter|whereas|notwithstanding|pursuant|hereinafter|aforementioned|indemnify|warrant|thereof|thereto|therein)\b",
    "ambiguous": r"\b(?:run|set|get|take|make|catch|break|light|right|fair|just|clear|left|over|issue|interest|current|patient|party|mean)\b",
    "jargon": r"\b(?:synergy|leverage|optimize|streamline|holistic|actionable|scalable|robust|ecosystem|granular|touchpoint)\b",
}

DIFFICULT_PATTERNS_CN = {
    "idiom": r"[\u4e00-\u9fff]{4}(?:[\u4e00-\u9fff]{0,4})?(?:[\u4e00-\u9fff]\u800c[\u4e00-\u9fff]|\u4e4b[\u4e00-\u9fff]{2}|[\u4e00-\u9fff]{2}\u53c8[\u4e00-\u9fff]{2})",
    "four_char_idiom": r"(?:[\u4e00-\u9fff]{4})(?=[\u3002\uff0c\u3001\uff1b\uff1a\u201c\u201d]|$)",
    "legal": r"(?:\u4ee5\u4e0b\u79f0|href=\u4e4b|href=\u6b64|\u5f53\u4e8b\u4eba|\u5408\u540c\u53d8\u66f4|\u7ea0\u7eb7\u89e3\u51b3|\u4ef2\u88c1\u6761\u6b3e|\u8d54\u507f\u8d23\u4efb|\u4e0d\u53ef\u6297\u529b)",
    "long_attr": r"\u7684(?:\u4e00\u4e9b|\u5176\u4ed6|\u7279\u5b9a|\u6709\u5173|\u76f8\u5e94|\u4e0d\u540c|\u91cd\u8981|\u57fa\u672c|\u4e3b\u8981)",
}


def _analyze_segment(
    seg: str,
    lang: str,
    domain: str,
    term_words: set[str],
) -> dict[str, Any]:
    """Analyze a single text segment for translation difficulty."""
    char_count = len(seg)
    word_count = len(seg.split())
    cn_chars = len(re.findall(r"[\u4e00-\u9fff]", seg))

    flags: list[str] = []
    technical_terms_found: list[str] = []

    # Check for long sentences
    if lang in ("en", "mixed", "auto") and word_count > 40:
        flags.append("long_sentence")
    if lang in ("zh", "mixed", "auto") and cn_chars > 60:
        flags.append("long_sentence")

    # Check for difficult English patterns
    for flag_key, pattern in DIFFICULT_KEYWORDS_EN.items():
        matches = re.findall(pattern, seg, re.IGNORECASE)
        if matches:
            flags.append(flag_key)
            technical_terms_found.extend(m.lower() for m in matches[:3])

    # Check for difficult Chinese patterns
    for flag_key, pattern in DIFFICULT_PATTERNS_CN.items():
        matches = re.findall(pattern, seg)
        if matches:
            flags.append(f"cn_{flag_key}")
            technical_terms_found.extend(m for m in matches[:3])

    # Check for terms from glossary/extraction
    seg_lower = seg.lower()
    for tw in term_words:
        if tw in seg_lower:
            technical_terms_found.append(tw)
    technical_terms_found = list(dict.fromkeys(technical_terms_found))[:8]

    # Check for numbers with units (requires careful translation)
    if re.search(r"\d+[\u00b0]?[FC]|\d+\s*(?:mm|cm|m|km|ml|l|g|kg|lb|oz|mph|km/h)", seg, re.IGNORECASE):
        flags.append("measurement_unit")

    # Check for parenthetical expressions
    if "(" in seg and ")" in seg:
        flags.append("parenthetical")

    # Determine segment difficulty
    difficulty_score = 0
    if len(flags) > 0:
        difficulty_score += min(len(flags), 3)
    if len(technical_terms_found) > 3:
        difficulty_score += 1
    if char_count > 200:
        difficulty_score += 1

    if difficulty_score >= 4:
        difficulty = "hard"
    elif difficulty_score >= 2:
        difficulty = "medium"
    else:
        difficulty = "easy"

    return {
        "text": seg[:120],
        "char_count": char_count,
        "word_count": word_count,
        "difficulty": difficulty,
        "difficulty_score": difficulty_score,
        "flags": flags,
        "technical_terms_found": technical_terms_found,
        "has_unknown_terms": len(technical_terms_found) > 5,
    }


def _assess_overall(
    per_segment: list[dict[str, Any]],
    lang: str,
    domain: str,
) -> str:
    """Assess overall translation difficulty."""
    if not per_segment:
        return "easy"

    hard_count = sum(1 for s in per_segment if s["difficulty"] == "hard")
    medium_count = sum(1 for s in per_segment if s["difficulty"] == "medium")
    total = len(per_segment)

    hard_ratio = hard_count / total if total > 0 else 0
    medium_ratio = medium_count / total if total > 0 else 0

    if hard_ratio > 0.3 or (hard_ratio > 0.1 and medium_ratio > 0.4):
        return "hard"
    if medium_ratio > 0.3 or hard_ratio > 0.05:
        return "medium"
    return "easy"


def _generate_recommendations(
    flags: list[str],
    domain: str,
    lang: str,
) -> list[dict[str, str]]:
    """Generate translation strategy recommendations."""
    recs: list[dict[str, str]] = []

    if "long_sentence" in flags:
        recs.append({
            "type": "sentence_structure",
            "recommendation": "含长句/复杂句，建议拆分翻译后重组，注意中英文语序差异（英文重心在前，中文在后）"
        })
    if "legal" in flags:
        recs.append({
            "type": "legal_terminology",
            "recommendation": "包含法律术语，需确保术语一致性，注意法律文本的正式语气和固定句式"
        })
    if "idiom" in flags or "cn_four_char_idiom" in flags or "cn_idiom" in flags:
        recs.append({
            "type": "idiom_handling",
            "recommendation": "包含成语/习语，应根据上下文意译而非字面翻译，必要时添加注释说明"
        })
    if "ambiguous" in flags:
        recs.append({
            "type": "ambiguous_terms",
            "recommendation": "存在多义词，需根据上下文确定准确含义，建议查阅平行文本验证"
        })
    if "jargon" in flags:
        recs.append({
            "type": "buzzwords",
            "recommendation": "包含流行商业/行业术语，建议使用行业公认译法而非直译"
        })
    if "measurement_unit" in flags:
        recs.append({
            "type": "units",
            "recommendation": "包含度量单位，注意单位换算或保留原单位+括号注释中文单位"
        })
    if "parenthetical" in flags:
        recs.append({
            "type": "parenthetical",
            "recommendation": "含括号注释/插入语，中文适合转换为破折号或重新组织语序"
        })

    if domain and domain != "general":
        recs.append({
            "type": "domain_preparation",
            "recommendation": f"领域: {domain}，建议准备相应领域的术语词典和参考语料"
        })

    if lang == "mixed":
        recs.append({
            "type": "mixed_language",
            "recommendation": "中英混合文本，需分别处理各语言段落的翻译方向，注意代码切换的边界"
        })

    if not recs:
        recs.append({
            "type": "general",
            "recommendation": "文本难度适中，按标准流程准备即可。注意保持术语一致性和整体风格统一"
        })

    return recs
