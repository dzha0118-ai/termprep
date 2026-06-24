"""Term association — find related terms without relying on Wikipedia server-side.

Uses:
  1. Wikipedia summary (client-fetched text, extracted with jieba/YAKE)
  2. Youdao API (translations & collocations)
  3. Datamuse API (English related words, free, no key needed)
"""

import re
from typing import Any
from collections import Counter


def find_related(term: str, limit: int = 15, wiki_text: str | None = None) -> list[dict[str, Any]]:
    results: dict[str, dict] = {}

    # 1. Wikipedia summary (client-fetched) → term extraction
    if wiki_text and wiki_text.strip():
        _from_wiki_text(wiki_text, term, limit, results)

    # 2. Datamuse API for English terms (free, no key)
    if not re.search(r'[\u4e00-\u9fff]', term):
        _from_datamuse(term, limit, results)

    # 3. Youdao translations
    _from_youdao_full(term, limit, results)

    # Add translations
    _add_translations(term, results)

    sorted_results = sorted(results.values(), key=lambda x: x.get("score", 0), reverse=True)
    return sorted_results[:limit]


# ── Source: Wikipedia text extraction ──

def _from_wiki_text(wiki_text: str, query_term: str, limit: int, results: dict[str, dict]) -> None:
    extracted = _extract_terms(wiki_text, query_term, limit * 2)
    for i, entry in enumerate(extracted):
        word = entry["word"]
        if word == query_term: continue
        score = 0.60 - (i * 0.02)
        if score <= 0.1: break
        key = word.lower()
        if key not in results or score > results[key].get("score", 0):
            results[key] = {"word": word, "translation": "", "relation": "wiki-extract", "source": "wikipedia", "score": round(score, 3)}


def _extract_terms(text: str, query_term: str, limit: int) -> list[dict]:
    is_cn = bool(re.search(r'[\u4e00-\u9fff]', text))
    if is_cn:
        return _extract_cn(text, query_term, limit)
    else:
        return _extract_en(text, query_term, limit)


def _extract_cn(text: str, query_term: str, limit: int) -> list[dict]:
    try:
        import jieba, jieba.analyse
        tfidf = jieba.analyse.extract_tags(text, topK=limit, withWeight=True)
        textrank = jieba.analyse.textrank(text, topK=limit, withWeight=True)
        merged = {}
        for w, wt in tfidf:
            if len(w) >= 2 and w != query_term: merged[w] = merged.get(w, 0) + wt * 0.7
        for w, wt in textrank:
            if len(w) >= 2 and w != query_term: merged[w] = merged.get(w, 0) + wt * 0.9
        stops = {'的','了','在','和','是','不','这','那','其','之','而','且','及','等','被','把','将'}
        return [{"word": w, "score": round(min(1.0, s), 4)} for w, s in sorted(merged.items(), key=lambda x: -x[1]) if w not in stops][:limit]
    except Exception:
        return []


def _extract_en(text: str, query_term: str, limit: int) -> list[dict]:
    try:
        import yake
        kw = yake.KeywordExtractor(lan="en", n=2, dedupLim=0.85, top=limit * 2)
        raw = kw.extract_keywords(text)
        seen = set()
        result = []
        for phrase, score in raw:
            phrase = phrase.strip()
            if len(phrase) < 3 or phrase.lower() == query_term.lower(): continue
            key = phrase.lower()
            if key in seen: continue
            seen.add(key)
            result.append({"word": phrase, "score": round(max(0, 1.0 - score), 4)})
            if len(result) >= limit: break
        return result
    except Exception:
        return _extract_en_fallback(text, query_term, limit)


def _extract_en_fallback(text: str, query_term: str, limit: int) -> list[dict]:
    words = re.findall(r'[A-Za-z]{3,}', text.lower())
    stop = {'the','and','for','from','that','this','with','are','can','has','have','was','into','its','not','but','all','some','also','such','other','each','more','than','been','one','two','may','which'}
    filtered = [w for w in words if w not in stop and w != query_term.lower()]
    counter = Counter(filtered)
    total = max(1, sum(counter.values()))
    return [{"word": w, "score": round(c / total, 4)} for w, c in counter.most_common(limit)]


# ── Source: Datamuse API ──

def _from_datamuse(term: str, limit: int, results: dict[str, dict]) -> None:
    """Fetch related terms from Datamuse (free, no API key)."""
    try:
        import requests
        encoded = term.replace(" ", "+")
        url = f"https://api.datamuse.com/words?ml={encoded}&max={limit}"
        resp = requests.get(url, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            for i, item in enumerate(data):
                word = item.get("word", "")
                if not word or len(word) < 2: continue
                score = max(0.1, 1.0 - (i / max(1, len(data))) * 0.7)
                key = word.lower()
                if key not in results or score > results[key].get("score", 0):
                    results[key] = {"word": word, "translation": "", "relation": "related", "source": "datamuse", "score": round(score, 3)}
                if len(results) >= limit * 2: break
    except Exception:
        pass


# ── Source: Youdao ──

def _from_youdao_full(term: str, limit: int, results: dict[str, dict]) -> None:
    try:
        from termprep.sources.youdao import YoudaoSource
        y = YoudaoSource()
        if not y.available: return
        sr = y.search(term, limit=min(limit * 2, 20))
        for i, r in enumerate(sr):
            if not r.word or r.word == term: continue
            word = r.word.strip()
            if len(word) < 2 or len(word) > 120: continue
            type_score = {"translation": 0.85, "collocation": 0.70, "explanation": 0.55}.get(r.word_type, 0.40)
            score = type_score - (i * 0.02)
            if score <= 0.1: break
            key = word.lower()
            if key not in results or score > results[key].get("score", 0):
                results[key] = {"word": word, "translation": r.definition or "", "relation": r.word_type, "source": "youdao", "score": round(score, 3)}
    except Exception:
        pass


# ── Translation ──

def _add_translations(query_term: str, results: dict[str, dict]) -> None:
    for key, item in list(results.items()):
        if item.get("translation") and len(item["translation"]) > 1: continue
        try:
            trans = _translate_single(item["word"])
            if trans and trans != item["word"]: item["translation"] = trans
        except Exception: pass


def _translate_single(term: str) -> str:
    try:
        from termprep.sources.youdao import YoudaoSource
        y = YoudaoSource()
        if not y.available: return ""
        sr = y.search(term, limit=3)
        for r in sr:
            if r.word_type == "translation" and r.word:
                is_src_cn = bool(re.search(r'[\u4e00-\u9fff]', term))
                is_tgt_cn = bool(re.search(r'[\u4e00-\u9fff]', r.word))
                if is_src_cn != is_tgt_cn: return r.word
        if sr and sr[0].word: return sr[0].word
        return ""
    except Exception: return ""
