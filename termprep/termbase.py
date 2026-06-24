"""Term base — query free knowledge bases for term validation and translations.

Sources:
  1. Wikidata (free, multilingual, no API key, excellent CN/EN coverage)
  2. IATE (EU terminology database, free REST API)
  3. Local domain term dictionary for instant lookups
"""

import re, json, os, time
from typing import Any


# ── Local domain dictionary ──

DOMAIN_TERMS: dict[str, list[str]] = {
    "legal": [
        "contract", "agreement", "party", "clause", "liability", "warranty", "termination",
        "jurisdiction", "arbitration", "indemnify", "pursuant", "hereto", "breach",
        "合同", "协议", "条款", "责任", "赔偿", "违约", "仲裁", "管辖", "担保", "终止",
        "不可抗力", "知识产权", "保密", "签署", "生效", "义务", "权利",
    ],
    "medical": [
        "diagnosis", "treatment", "symptom", "surgery", "clinical", "pharmaceutical",
        "therapy", "patient", "prognosis", "etiology", "pathology", "oncology",
        "诊断", "治疗", "症状", "手术", "临床", "药物", "患者", "预后", "病理", "肿瘤",
        "免疫", "抑制剂", "靶向", "化疗", "放疗", "基因", "细胞", "疫苗",
    ],
    "finance": [
        "revenue", "asset", "liability", "equity", "dividend", "portfolio", "securities",
        "audit", "fiscal", "inflation", "liquidity", "capital", "investment",
        "收入", "资产", "负债", "股权", "分红", "审计", "通胀", "流动性", "资本", "投资",
        "货币政策", "利率", "汇率", "股票", "债券", "基金",
    ],
    "it": [
        "software", "hardware", "database", "API", "algorithm", "deployment",
        "framework", "interface", "runtime", "container", "microservice",
        "算法", "数据库", "接口", "框架", "部署", "容器", "微服务", "云计算", "人工智能",
    ],
    "academic": [
        "hypothesis", "methodology", "analysis", "conclusion", "abstract",
        "empirical", "theoretical", "variable", "correlation", "significant",
        "假设", "方法论", "分析", "结论", "摘要", "实证", "理论", "变量", "相关性",
    ],
}


def lookup_term(term: str, domain: str = "general") -> dict:
    """Look up a term in local dictionary and Wikidata.

    Returns: {found, term, translation, definitions, domain, source}
    """
    result = {
        "found": False, "term": term, "translation": "",
        "definitions": [], "domain": domain, "source": ""
    }

    # 1. Check local dictionary
    domain_lower = term.lower()
    for d, terms in DOMAIN_TERMS.items():
        if d == "general": continue
        for t in terms:
            if t.lower() == domain_lower:
                result["found"] = True
                result["domain"] = d
                result["source"] = "local"
                return result

    # 2. Wikidata lookup (free, no key)
    wd = _wikidata_lookup(term)
    if wd:
        result.update(wd)
        result["source"] = "wikidata"
        return result

    return result


def _wikidata_lookup(term: str) -> dict | None:
    """Query Wikidata for term definition and label."""
    try:
        import urllib.parse, requests

        encoded = urllib.parse.quote(term)
        is_cn = bool(re.search(r'[\u4e00-\u9fff]', term))
        lang = "zh" if is_cn else "en"
        target_lang = "en" if is_cn else "zh"

        # Wikidata search API
        url = f"https://www.wikidata.org/w/api.php?action=wbsearchentities&search={encoded}&language={lang}&format=json&limit=3"
        resp = requests.get(url, timeout=8, headers={"User-Agent": "TermPrep/0.5"})
        if resp.status_code != 200:
            return None

        data = resp.json()
        results = data.get("search", [])
        if not results:
            return None

        best = results[0]
        label = best.get("label", term)
        desc = best.get("description", "")
        entity_id = best.get("id", "")

        definitions = [desc] if desc else []
        translation = ""

        # Get label in target language
        if entity_id:
            trans_url = f"https://www.wikidata.org/wiki/Special:EntityData/{entity_id}.json"
            try:
                tr = requests.get(trans_url, timeout=5, headers={"User-Agent": "TermPrep/0.5"})
                if tr.status_code == 200:
                    edata = tr.json()
                    entities = edata.get("entities", {})
                    if entity_id in entities:
                        labels = entities[entity_id].get("labels", {})
                        if target_lang in labels:
                            translation = labels[target_lang].get("value", "")
                        # Get description in source language
                        descs = entities[entity_id].get("descriptions", {})
                        if lang in descs:
                            def_val = descs[lang].get("value", "")
                            if def_val and def_val != desc:
                                definitions.insert(0, def_val)
            except Exception:
                pass

        return {
            "found": True,
            "term": label,
            "translation": translation,
            "definitions": definitions,
            "domain": _infer_domain(definitions + [desc], label),
        }
    except Exception:
        return None


def _infer_domain(texts: list[str], label: str) -> str:
    """Infer domain from description text."""
    all_text = " ".join(texts).lower() + " " + label.lower()
    scores = {}
    for d in ("medical", "legal", "finance", "it", "academic"):
        terms = DOMAIN_TERMS.get(d, [])
        score = sum(1 for t in terms if t.lower() in all_text)
        if score > 0:
            scores[d] = score
    if scores:
        return max(scores, key=scores.get)
    return "general"


# ── API for bulk term validation ──

def validate_terms(terms: list[str], domain: str = "general") -> list[dict]:
    """Validate a list of extracted terms against knowledge bases.

    Returns list of dicts with found, term, translation, domain, is_valid.
    """
    results = []
    for term in terms:
        if len(term) < 2:
            continue
        info = lookup_term(term, domain)
        # A term is valid if found in Wikidata or local dict, or has specific domain indicators
        info["is_valid"] = info["found"] or _is_likely_term(term, domain)
        results.append(info)
        time.sleep(0.1)  # rate limit for Wikidata API
    return results


def _is_likely_term(word: str, domain: str) -> bool:
    """Heuristic: is this likely a domain term?"""
    # Filter common noise
    stops = {"的", "了", "在", "和", "是", "不", "这", "那", "其", "之",
             "而", "且", "及", "等", "被", "把", "将", "从", "对", "向",
             "上", "下", "中", "前", "后", "有", "无", "来", "去", "到"}
    if word in stops:
        return False
    # Chinese terms should be 3+ chars
    if re.search(r'[\u4e00-\u9fff]', word) and len(word) >= 3:
        return True
    # English: usually 2+ words
    if re.search(r'[a-zA-Z]', word) and len(word.split()) >= 2:
        return True
    return False
