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
        # P0 — jieba tags every English token as `eng`, so the POS chunker is
        # blind to English and the language fell back to YAKE/CValue, which emit
        # finite clauses ("study examines post-editing"). This rule-based
        # noun-phrase chunker supplies clean English candidates.
        terms.extend(_extract_en_noun_phrases(text, top_n))

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


# ════════════════════════════════════════════════════════════════════
#  P0 — 片段判别：谓词框架词与边界约束
# ════════════════════════════════════════════════════════════════════
# 「考察译员」「保证术语」「语料来自」「研究发现」这类**句子片段**无法用词性判别：
# 真术语里的动词性语素与其完全同构 —— 联合化疗(v+n)、术语提取(n+v)、翻译研究(v+vn)。
# 因此改用一份**封闭的谓词框架词表**：命中的是"做谓语的高频通用动词"，而不是"动词"本身。
_FRAME_VERBS = frozenset({
    # 认知 / 言说
    '考察', '探讨', '回顾', '分析', '发现', '表明', '说明', '指出', '认为', '提出',
    '显示', '意味', '意味着', '证实', '检验', '评估', '比较', '对比', '总结',
    # 行为 / 过程
    '采用', '使用', '利用', '借助', '依赖', '接受', '纳入', '保证', '确保', '进行',
    '开展', '实施', '完成', '实现', '获得', '提供', '支持', '要求', '允许', '需要',
    '建立', '构建', '形成', '产生', '导致', '引起', '促使', '推动', '影响',
    '提高', '提升', '增加', '降低', '减少', '改善', '改变', '转向', '修改', '规划',
    '包括', '包含', '涉及', '成为', '作为', '具有', '存在', '发生', '出现', '选择',
    '结合', '属于', '位于', '基于', '用于', '在于', '来自', '源自', '输出', '输入',
    '导致', '造成', '带来', '取得', '达到', '超过', '优于', '致力于', '着眼',
})

# 被 jieba 合成单一 token 的片段标记：token 级检查抓不到，做子串兜底。
# 每一条都已核对不会出现在真术语中（例如 '回顾' 只做 token 级，避免误杀「回顾性队列研究」）。
_FRAME_SUBSTRINGS = (
    '结果表明', '结果显示', '数据表明', '研究显示', '本研究', '综上所述', '由此可见',
)

# 不能作为候选**首字**的词性：代词/连词/介词/副词/助词/叹词/标点
_BAD_LEAD_POS = frozenset({'r', 'c', 'p', 'd', 'e', 'o', 'y', 'w', 'x', 'u', 'uj', 'ul', 'uv', 'ug', 'uz'})
# 不能作为候选**末字**的词性：连词/介词/助词/标点/方位词/时间词
# （注意：**不**排除 v/d —— 术语提取、人工判断、客观缓解率、甲状腺功能异常都会踩到。
#  时间词必须排除：'较低时' 会被切成 '低时' 这种跨词边界碎片。）
_BAD_TAIL_POS = frozenset({'c', 'p', 't', 'u', 'uj', 'ul', 'uv', 'ug', 'uz', 'w', 'x', 'e', 'o', 'y', 'f'})

# 通用中心词（术语度低）。出现在尾部且其前缀本身也是候选时，优先保留前缀。
_GENERIC_HEADS = (
    '过程', '环节', '方面', '情况', '问题', '内容', '因素', '部分', '阶段',
    '水平', '程度', '范围', '结果', '方式', '工具', '输出',
)


# ════════════════════════════════════════════════════════════════════
#  P0 — 英文：jieba 把英文一律标成 eng，词性通道对英文完全失效，
#  因此单列一套基于封闭词表的英文名词短语分块规则。
# ════════════════════════════════════════════════════════════════════
_EN_BREAK_WORDS = frozenset({
    # 限定词 / 代词
    'a', 'an', 'the', 'this', 'that', 'these', 'those', 'its', 'their', 'his', 'her',
    'our', 'your', 'my', 'we', 'us', 'it', 'they', 'them', 'he', 'she', 'i', 'you',
    'there', 'such', 'some', 'any', 'all', 'both', 'each', 'every', 'no', 'not',
    'another', 'other', 'many', 'much', 'few', 'several', 'most',
    # 介词 / 连词 / 从句引导词
    'of', 'in', 'on', 'at', 'by', 'for', 'with', 'from', 'to', 'into', 'onto',
    'over', 'under', 'between', 'among', 'through', 'during', 'after', 'before',
    'about', 'against', 'within', 'without', 'across', 'via', 'per', 'as', 'than',
    'while', 'when', 'where', 'which', 'who', 'whom', 'whose', 'and', 'or', 'but',
    'if', 'because', 'so', 'although', 'though', 'since', 'unless', 'whether',
    # 疑问/关系词：'how collaboration' 这类子句片段就是从缺这几个词漏出来的
    'how', 'what', 'why', 'whose-ever',
    # 高频谓语动词（含形态变体）——正是它们把 YAKE/CValue 的产物污染成子句
    'examines', 'examine', 'examined', 'examining', 'adopts', 'adopt', 'adopted',
    'proposes', 'propose', 'proposed', 'shows', 'show', 'shown', 'ensures',
    'ensure', 'ensured', 'improves', 'improve', 'improved',
    'outperforms', 'outperform', 'outperformed',
    'evaluates', 'evaluate', 'evaluated', 'suggests', 'suggest', 'suggested',
    'uses', 'use', 'used', 'requires', 'require', 'required', 'shifts', 'shift',
    'discusses', 'discuss', 'reports', 'report', 'presents', 'present',
    'investigates', 'investigate', 'analyzes', 'analyse', 'explores', 'explore',
    'finds', 'find', 'found', 'provides', 'provide', 'indicates', 'indicate',
    'reveals', 'reveal', 'confirms', 'confirm', 'argues', 'argue', 'claims', 'claim',
    'notes', 'note', 'observes', 'observe', 'measures', 'measure', 'tests', 'test',
    'fine-tune', 'fine-tunes', 'achieves', 'achieve', 'obtains', 'obtain',
    'yields', 'yield', 'allows', 'allow', 'enables', 'enable', 'helps', 'help',
    'gives', 'give', 'makes', 'make', 'takes', 'take', 'keeps', 'keep', 'needs',
    'need', 'becomes', 'become', 'includes', 'include', 'contains', 'contain',
    'consists', 'consist', 'remains', 'remain', 'seems', 'seem', 'appears', 'appear',
    # 学术摘要里最常见的谓语（'promote lexical'、'assurance reduces revision time'
    # 这类片段就出自它们）
    'promote', 'promotes', 'promoted', 'reduce', 'reduces', 'reduced', 'reducing',
    'reshape', 'reshapes', 'reshaped', 'increase', 'increases', 'increased',
    'guarantee', 'guarantees', 'guaranteed', 'document', 'documents', 'documented',
    'documenting', 'offer', 'offers', 'offered', 'contribute', 'contributes',
    'contributed', 'inject', 'injected', 'injects', 'draw', 'draws', 'drawing',
    'combine', 'combines', 'combining', 'combining', 'support', 'supports',
    'replace', 'replaces', 'replacing', 'distribute', 'distributes',
    'distributing', 'affect', 'affects', 'affected', 'argue', 'argues',
    'suggest', 'suggests', 'indicate', 'indicates', 'examine', 'examines',
})


def _looks_english(s: str) -> bool:
    """True for candidates made only of Latin letters (pure English)."""
    return bool(re.search(r"[A-Za-z]", s)) and not re.search(r"[\u4e00-\u9fff]", s)


def _en_is_fragment(s: str) -> bool:
    """True when an English candidate is a clause/fragment rather than a noun phrase."""
    toks = re.findall(r"[A-Za-z][A-Za-z\-']*", s)
    if not toks:
        return True
    low = [t.lower() for t in toks]
    if len(low) > 4:
        return True
    if low[0] in _EN_BREAK_WORDS or low[-1] in _EN_BREAK_WORDS:
        return True
    return any(t in _EN_BREAK_WORDS for t in low)


def _is_fragment(term: str) -> bool:
    """P0 — is this candidate a clause/fragment rather than a terminological unit?"""
    t = (term or "").strip()
    if not t:
        return True
    for marker in _FRAME_SUBSTRINGS:
        if marker in t:
            return True
    if _looks_english(t):
        return _en_is_fragment(t)
    # 中文（或中英混合）走 token 级框架动词检查；token 级能避免
    # 「回顾性队列研究」被「回顾」误杀。
    try:
        import jieba
        toks = [w.strip() for w in jieba.lcut(t) if w.strip()]
    except Exception:
        toks = [t]
    return any(tok in _FRAME_VERBS for tok in toks)


def _strip_generic_head(term: str) -> str | None:
    """P1 — 若候选以通用中心词结尾，返回剥离后的前缀（否则 None）。"""
    for head in _GENERIC_HEADS:
        if term.endswith(head) and len(term) - len(head) >= 2:
            return term[: -len(head)]
    return None


# 单字候选若属于这些词性，基本不可能是术语（代词/副词/时间/方位/数词/量词/虚词…）
_NON_TERM_POS = frozenset({
    'r', 'd', 't', 'f', 'm', 'q', 'c', 'p', 'u', 'uj', 'ul', 'uv', 'ug', 'uz',
    'w', 'x', 'e', 'o', 'y', 'z', 'k', 'h',
})
# 高频通用单字词：词性上是名词，但没有术语度（方法/课堂/常见…）
_GENERIC_SINGLE = frozenset({
    '方法', '方式', '情况', '问题', '方面', '内容', '过程', '环节', '工作', '结果',
    '水平', '程度', '范围', '部分', '阶段', '本文', '本', '研究', '分析', '课堂',
    '常见', '进行', '使用', '需要', '目的', '意义', '作用', '影响', '特点', '优势',
    '效果', '时间', '地方', '东西', '时候', '一般', '主要', '重要', '不同', '相关',
})


# 候选**内部**不允许出现的词性（虚词/代词/标点/连词/介词/方位/量词）。
# 'm' 只允许出现在首位（多智能体编排 → 多/m）；'t'/'b'/'a'/'d' 允许作定语。
_BAD_INNER_POS = frozenset({
    'u', 'uj', 'ul', 'uv', 'ug', 'uz', 'c', 'p', 'r', 'w', 'x', 'e', 'o', 'y',
    'z', 'k', 'h', 'f', 'm', 'q',
})


def _pos_ok(term: str) -> bool:
    """P0 兜底 — 把词性/通用度约束应用到**所有通道**的候选上，而不只是分块通道。

    关键词与 C-Value 通道不过边界检查，会漏出 `本文`、`常见`、`低时`（跨词边界碎片）
    以及 `联合化疗组的无进展`（内部含"的"的跨边界长串）这类非术语。
    """
    t = (term or "").strip()
    if not t:
        return False
    if t in _GENERIC_SINGLE:
        return False
    if _looks_english(t):
        return not _en_is_fragment(t)
    try:
        import jieba.posseg as pseg
        pairs = [(w, f) for w, f in pseg.cut(t) if w.strip()]
    except Exception:
        return True
    if not pairs:
        return True

    if len(pairs) == 1:
        return pairs[0][1] not in _NON_TERM_POS

    head_word, head_flag = pairs[0]
    last_word, last_flag = pairs[-1]
    # 首字为单字名词性语素（ng）时是"半截词"：免疫相关性肺炎 被切出的 '性肺炎'
    if head_flag == 'ng' and len(head_word) == 1:
        return False
    # 末位为单字 ng 时是跨词边界碎片：'较低时' 被切成 '低时'
    if last_flag == 'ng' and len(last_word) == 1:
        return False
    if head_flag in _BAD_LEAD_POS or last_flag in _BAD_TAIL_POS:
        return False
    # 内部出现虚词即判为跨边界长串（联合化疗组的无进展 / 化疗组的无进展生存期）
    return all(flag not in _BAD_INNER_POS for _, flag in pairs[1:-1])


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
                # 单字词默认不是内容，但**构词前缀**除外：
                # 多智能体编排(多/m)、非小细胞肺癌(非/b)、单药治疗(单/b)
                (len(word) == 1 and flag not in ('eng', 'n', 'a', 'v', 'm', 'b'))
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
            # 允许以「构词前缀」开头：多智能体编排(多/m)、非小细胞肺癌(非/b)。
            # 单字数词/区别词本身不是内容词，但它们在术语里常作前缀，不能当边界。
            prefix = (
                len(tokens[i]["word"]) == 1
                and tokens[i]["flag"] in ('m', 'b')
                and (i + 1) < len(tokens) and tokens[i + 1]["is_content"]
            )
            if not tokens[i]["is_content"] and not prefix:
                i += 1
                continue

            parts = []
            j = i
            if prefix:
                parts.append(tokens[i]["word"])
                j = i + 1
            while j < len(tokens) and (j - i) < 4:
                t = tokens[j]
                if t["is_content"]:
                    parts.append(t["word"])
                    j += 1
                elif (t["flag"] == 'm' and 0 < len(parts)
                      and (j + 1) < len(tokens) and tokens[j + 1]["is_content"]):
                    # 嵌入的数字属于术语本体：程序性死亡受体1抑制剂
                    parts.append(t["word"])
                    j += 1
                else:
                    # 其余一律作为硬边界。旧代码允许“的”作连接，于是造出了
                    # 「联合化疗组的无进展」这种跨句法边界的长串。
                    break

            # 允许一个尾随的形容词/副词性语素作术语中心：甲状腺功能异常（异常/d）
            if j < len(tokens) and 0 < len(parts) < 4:
                nxt = tokens[j]
                is_last_of_run = (j + 1) >= len(tokens) or not tokens[j + 1]["is_content"]
                if (nxt["flag"] in ('a', 'ad', 'an', 'd')
                        and len(nxt["word"]) >= 2 and is_last_of_run):
                    parts.append(nxt["word"])
                    j += 1

            if len(parts) >= 2:
                phrase = "".join(parts)
                span = tokens[i:j]
                # P0 — 术语形状判定：必须含名词，且两端不得是功能词。
                # 旧代码只检查"含名词"，于是 考察译员(v+n)、语料来自(n+v) 全部放行。
                if (3 <= len(phrase) <= 24
                        and span
                        and span[0]["flag"] not in _BAD_LEAD_POS
                        and span[-1]["flag"] not in _BAD_TAIL_POS
                        and any(t["flag"] in _NOUN_POS for t in span)):
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
            # Score by relative frequency only. The old formula multiplied by a
            # length bonus (up to 2.0x), which actively rewarded the long clause
            # fragments that dominated the output.
            score = count / total
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
#  P0 — English noun-phrase chunking (rule based)
# ════════════════════════════════════════════════════════════════════

def _extract_en_noun_phrases(text: str, top_n: int) -> list[TermEntry]:
    """Extract English noun phrases with a closed-class lexicon.

    jieba tags every English token as ``eng``/``x``, so ``_extract_phrases``
    returns nothing for English (verified) and the language fell back to
    YAKE/CValue, which emit finite clauses such as
    ``study examines post-editing``. This chunker instead splits the text into
    runs of content tokens (breaking at determiners, prepositions, conjunctions
    and a verb lexicon) and keeps noun-phrase-shaped runs of 2–4 tokens, plus
    acronyms.
    """
    counter: Counter[str] = Counter()

    for chunk in re.split(r"[.;:!?()\[\]{}\n\u2014\u2013]+", text):
        tokens = re.findall(r"[A-Za-z][A-Za-z\-']*", chunk)
        run: list[str] = []

        def flush(run_tokens: list[str]) -> None:
            n = len(run_tokens)
            if n >= 2:
                for size in range(2, min(4, n) + 1):
                    for start in range(0, n - size + 1):
                        phrase = " ".join(run_tokens[start:start + size])
                        if not _en_is_fragment(phrase):
                            counter[phrase] += 1
            elif n == 1:
                tok = run_tokens[0]
                # keep acronyms only (BERT, BLEU) — bare lowercase words are
                # already covered by the keyword channels
                if re.fullmatch(r"[A-Z][A-Z0-9\-]{1,7}", tok):
                    counter[tok] += 1

        for tok in tokens:
            if tok.lower() in _EN_BREAK_WORDS:
                if run:
                    flush(run)
                run = []
            else:
                run.append(tok)
        if run:
            flush(run)

    if not counter:
        return []

    total = max(1, sum(counter.values()))
    results: list[TermEntry] = []
    for phrase, count in counter.most_common(top_n * 3):
        results.append(TermEntry(
            term=phrase,
            frequency=count,
            score=(count / total) * 0.9,
            word_type="en-np",
        ))
    return results


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
        # P0 — 片段过滤放在所有通道的汇合处，让每个通道都受益
        # （考察译员 / 语料来自 / study examines post-editing …）
        if _is_fragment(t.term):
            continue
        # P0 兜底 — 词性与通用度检查，抓关键词通道漏出的 本文 / 常见 / 低时
        if not _pos_ok(t.term):
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

    # Step 2c — P1 通用中心词剥离：若「术语管理过程」与「术语管理」同时是候选，
    # 保留更精确的前者（旧逻辑反过来，长的那条会吃掉短的真术语）。
    for key in list(merged.keys()):
        if key not in merged:
            continue
        prefix = _strip_generic_head(merged[key].term)
        if prefix and prefix.lower().strip() in merged:
            del merged[key]

    # Step 3: Normalize scores to 0-1 range
    if merged:
        all_scores = [t.score for t in merged.values()]
        max_sc = max(all_scores)
        if max_sc > 0:
            for t in merged.values():
                t.score = round(t.score / max_sc, 4)

    # Tie-break on length ASCENDING: with equal evidence the shorter candidate is
    # the more precise term. The old `-len(x.term)` tie-break systematically
    # promoted the longer, clause-like variants.
    sorted_terms = sorted(merged.values(), key=lambda x: (-x.score, len(x.term)))
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


# ════════════════════════════════════════════════════════════════════
#  Explainable scoring & part-of-speech (task book §5.7 / §5.9a)
# ════════════════════════════════════════════════════════════════════

_CJK_CHAR = re.compile(r"[\u4e00-\u9fff]")

# Weights exactly as specified in the task book §5.7.
SCORE_WEIGHTS: dict[str, float] = {
    "frequency": 0.40,
    "domain_hit": 0.30,
    "length": 0.20,
    "form": 0.10,
}


def count_occurrences(term: str, text: str) -> int:
    """Count occurrences of ``term`` in ``text`` (CJK substring, ASCII word-bound)."""
    if not term or not text:
        return 0
    if _CJK_CHAR.search(term):
        return text.count(term)
    return len(
        re.findall(
            r"(?<![A-Za-z0-9])" + re.escape(term) + r"(?![A-Za-z0-9])",
            text,
            re.IGNORECASE,
        )
    )


def explainable_score(
    term: str, text: str, frequency: int = 0, word_type: str = ""
) -> tuple[float, dict]:
    """Multi-factor, explainable term score (§5.7).

    ``score = 0.40*frequency + 0.30*domain_hit + 0.20*length + 0.10*form``

    The previous ``score`` was a constant 0.9 for AI-extracted terms, so it could
    not be used as a quality threshold. Every factor is returned in the
    breakdown, so the translator can see *why* a candidate was recommended and a
    mis-detection can be traced to the factor responsible for it.

    Returns:
        ``(score, breakdown)`` where score is in [0, 1].
    """
    term = (term or "").strip()
    occurrences = frequency or count_occurrences(term, text)

    # 1) frequency — saturating normalisation against a soft cap of 6 hits
    freq_factor = min(1.0, occurrences / 6.0) if occurrences > 0 else 0.0

    # 2) local domain-glossary hit (validated terminology)
    domain_factor = 1.0 if _get_term_boost(term) > 1.0 else 0.0

    # 3) length plausibility
    is_cjk = bool(_CJK_CHAR.search(term))
    if is_cjk:
        n_chars = len(term)
        length_factor = 1.0 if 2 <= n_chars <= 8 else (0.5 if n_chars <= 12 else 0.2)
    else:
        n_words = len(term.split())
        length_factor = 1.0 if 2 <= n_words <= 4 else (0.6 if n_words == 1 else 0.4)

    # 4) morphological shape (acronym / hyphen / capitalisation)
    if not term:
        form_factor = 0.0
    elif term.isupper() and 2 <= len(term) <= 6:
        form_factor = 1.0          # acronym: BERT, RAG, LLM
    elif is_cjk:
        form_factor = 0.6          # Chinese terms carry no casing signal
    elif "-" in term:
        form_factor = 0.8          # hyphenated compound
    elif len(term.split()) >= 2 and term[0].isupper():
        form_factor = 0.7          # Capitalised Phrase
    else:
        form_factor = 0.4

    breakdown = {
        "frequency": round(freq_factor, 4),
        "domain_hit": round(domain_factor, 4),
        "length": round(length_factor, 4),
        "form": round(form_factor, 4),
        "occurrences": occurrences,
        "weights": dict(SCORE_WEIGHTS),
    }
    score = (
        SCORE_WEIGHTS["frequency"] * freq_factor
        + SCORE_WEIGHTS["domain_hit"] * domain_factor
        + SCORE_WEIGHTS["length"] * length_factor
        + SCORE_WEIGHTS["form"] * form_factor
    )
    return round(min(1.0, score), 4), breakdown


# jieba POS flag → readable label
_POS_LABELS: dict[str, str] = {
    "n": "noun", "nr": "proper noun", "ns": "place name", "nt": "organization",
    "nz": "proper noun", "nw": "noun", "vn": "verbal noun", "v": "verb",
    "a": "adjective", "an": "adjective", "ad": "adverb", "d": "adverb",
    "m": "numeral", "q": "quantifier", "j": "abbreviation", "l": "idiom",
    "i": "idiom", "eng": "English token", "t": "time word", "f": "locative",
}

_EN_SUFFIX_POS: tuple[tuple[str, str], ...] = (
    ("tion", "noun"), ("sion", "noun"), ("ment", "noun"), ("ness", "noun"),
    ("ity", "noun"), ("ism", "noun"), ("ance", "noun"), ("ence", "noun"),
    ("ology", "noun"), ("graphy", "noun"), ("ship", "noun"), ("hood", "noun"),
    ("ing", "gerund"), ("ize", "verb"), ("ise", "verb"), ("ify", "verb"),
    ("ous", "adjective"), ("ive", "adjective"), ("able", "adjective"),
    ("ible", "adjective"), ("al", "adjective"), ("ic", "adjective"),
)


def guess_pos(term: str) -> str:
    """Best-effort part of speech for a candidate term (§5.9a).

    Chinese terms go through jieba's POS tagger; English terms fall back to
    morphological heuristics. The field used to be hard-coded to ``""`` for every
    term, which made the whole schema look unfinished.
    """
    t = (term or "").strip()
    if not t:
        return ""

    if _CJK_CHAR.search(t):
        try:
            import jieba.posseg as pseg

            flags = [flag for _, flag in pseg.cut(t) if flag and flag != "x"]
            # Walk from the head of the compound backwards to the first known tag
            for flag in reversed(flags):
                label = _POS_LABELS.get(flag) or _POS_LABELS.get(flag[:1])
                if label:
                    return label
        except Exception:
            pass
        # Chinese technical terms are overwhelmingly nominal
        return "noun"

    if t.isupper() and 2 <= len(t) <= 6:
        return "abbreviation"
    if len(t.split()) >= 2:
        return "noun phrase"
    low = t.lower()
    for suffix, label in _EN_SUFFIX_POS:
        if low.endswith(suffix) and len(low) > len(suffix) + 2:
            return label
    return "noun"
