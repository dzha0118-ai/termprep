"""AI Large-Language-Model translation engine with multi-pass quality enhancement.

Features:
- Context-aware chunking for long text (passes context between chunks)
- Glossary injection with per-chunk reinforcement
- Post-translation QA: AI self-review for terminology consistency
- Supports any OpenAI-compatible API (OpenAI, Kimi, DeepSeek, Ollama, etc.)
"""

import re
from typing import Any

from termprep.engines.base import register_engine
from termprep.interfaces.translator import TranslationRequest, TranslationResponse, Translator


DOMAIN_PROMPTS = {
    "legal":    "使用正式的法律文书风格，术语准确，句式严谨。保留条款结构和义务表述的精确性。",
    "medical":  "使用专业的医学文献风格，术语准确。药物名称、解剖学术语和临床操作遵循国际命名规范。",
    "finance":  "使用正式的财经报告风格，术语准确，数据表达规范。会计术语和金融工具名称保持精确。",
    "it":       "使用技术文档风格，术语保持一致性。API 名称、技术参数和代码相关术语适当保留英文。",
    "academic": "使用学术论文风格，表达严谨。保留引用格式和方法论描述，维持正式语体。",
    "news":     "使用新闻体风格，语言简洁有力。",
    "translation": "使用翻译研究领域的规范表述（如 terminology management 译作「术语管理」、"
                   "post-editing 译作「译后编辑」）。",
    "humanities": "使用人文社科的论述语体，避免生硬的西式长句。",
    "speech":   "这是讲稿/致辞：要像人在台上说话，句子短、有节奏、可直接朗读，不要写成公文。",
    "general":  "翻译自然流畅，将习语转换为地道的目标语言等价表达。",
}

# ⚠️ 这段提示词是全文翻译质量的第一道闸门，之前的版本只有
# "translate naturally" 一句，模型于是逐字直译、还常漏句。约束必须写死：
#   · 句数/段落数守恒（漏句是这个项目里最严重的译文缺陷）
#   · 段落结构保留（旧代码把段落边界弄丢了）
#   · 禁止在译文里留下原文语序与空转搭配
_SYSTEM_PROMPT = """你是一名资深专业译者，母语读者导向，擅长中英双向翻译。请把用户给出的内容译成目标语言。

硬性要求（逐条遵守，违反任何一条都算失败）：
1. 只输出译文本身：不要解释、不要加标题、不要用引号包裹、不要输出 Markdown 或任何标注。
2. 逐句完整翻译：不得漏译、不得增译、不得合并或拆分句子。译文的句数必须与原文一致。
3. 严格保留段落结构：原文分段的地方译文也分段（用空行分隔），不要自行并段或另起段。
4. 数字、专名、缩写、货币、百分比原样保留（例如 US$1.57 trillion、74%、2024）。
5. 若给出了术语表，必须严格使用术语表里的译法，且全文一致；术语表优先于你的个人习惯。
6. 译文要像目标语母语者直接写出来的，不能留有原文语序的痕迹（不要"逐词对译"）。

目标语言为中文时，额外要求（非常重要）：
- 以读者为中心，按汉语的意合习惯造句：多用短句和动词短语，少用长定语、少堆"的"。
- 不要保留英文的形合骨架（which/that/while/so 不要一律硬译成"这/那/而/因此"再往后接）。
- 使用全角标点；不要在中文字词之间加空格；句末必须有标点。
- 避免"进行设计""加以管理""得以实现"这类空转搭配，直接用动词。
- 句子要有主谓，不要写成提纲式残句：列举项也必须是完整句子。
  例：原文 "First, it raises yield." 应译「第一，它提高了收益。」，
  而不是「第一，提高收益。」（后者是被校对改坏的典型）。
- 讲稿、致辞、访谈等口头语体：要像人在台上讲话，短句、有节奏、可直接朗读。
- 已被广泛接受的译名用通行译法；无通行译法的人名/机构名保留英文，必要时括注。

目标语言为英文时，额外要求：
- 使用英文标点与正常空格；句式自然，不要逐字对应中文的四字格。
- 中文的流水句要合并成有主从关系的英文句子，不要拆成一串短句。"""

# 漏译/句数不足时追加的严厉提醒（只用于重试那一次）
_COMPLETENESS_NUDGE = (
    "\n\n【重要】上一版译文存在漏译或句数不足。这一次请务必逐句翻译，"
    "原文有多少句就译出多少句，一句都不能省略，段落也要一一对应。"
)

_QA_SYSTEM_PROMPT = """你是一名译文审校。请对照原文逐句核对译文，只修正发现的问题。

必须检查（按顺序）：
1. **漏译**——把原文和译文逐句对照，缺少的句子必须补译；这是最高优先级的问题。
2. **术语一致性**——术语表里指定的译法必须全文统一。
3. **生硬表达**——不符合目标语习惯、留有原文语序痕迹的句子要改写。
4. **标点与格式**——中文用全角标点、句末有标点、中文字词之间不留空格；段落结构与原文一致。
5. 数字、专名、百分比是否原样保留。
6. **不要为了简洁而删主语**——不能把完整句改成提纲式残句
   （「第一，它提高了收益。」不能改成「第一，提高收益。」）。

输出要求：只输出修正后的完整译文。若译文已经合格，则原样返回。
绝不允许因为修改而删减内容——修正后的译文不得比原译文更短、不得减少句子。"""


# ════════════════════════════════════════════════════════════════════
#  译后规范化与漏译审计（模块级，便于单测）
#
#  这三件事以前一件都没做：分段丢段落、译完不校验、标点空格不收拾。
#  它们叠加起来才是"译文生硬"的真正原因，而不是模型水平不行。
# ════════════════════════════════════════════════════════════════════

# 中日韩字符与全角标点
_CJKISH = r"\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uff00-\uffef"
_CJK_RE = re.compile(f"[{_CJKISH}]")
# AI 失败时旧代码会把这种占位符塞进译文里，必须清掉
_PLACEHOLDER_RE = re.compile(r"\[Translation missing for [^\]]*\]\s*", re.I)
# 句末终止符：中文句号后不需要空格（"甲。乙。"），英文的 "." 必须后跟空白
# 或结尾才算句末，否则 `US$1.57`、`1.5` 会被误判成句子边界。
_SENT_END_RE = re.compile(
    r"(?:[。！？]+[\u201d\"'\uff09)\]]*)"
    r"|(?:[.!?]+[\u201d\"'\uff09)\]]*(?=\s|$))"
)
# 中文行末已有标点（含右引号、右括号）就不再补句号
_ZH_TERMINAL = set("。！？；：，、…—～.!?;:,）)】」』”’\"]")


def sentence_count(text: str) -> int:
    """估算句子数：只认"后面跟空白或结尾"的句末标点。

    这样 `US$1.57 trillion` 里的点不会被误当成句号。
    """
    if not text or not text.strip():
        return 0
    n = len(_SENT_END_RE.findall(text))
    # 没有任何终止符但确实有内容 → 算 1 句（一段未结尾的文字）
    return n if n else 1


def normalize_target_text(text: str, target_lang: str = "zh") -> str:
    """译后规范化：标点、空格、段落、占位符。

    用户看到的那几处痕迹（"混合型旅行 现在，"、"明确指导 总而言之"、
    句末缺句号）都是因为这里什么都没做。
    """
    if not text:
        return ""
    t = text.replace("\r\n", "\n").replace("\r", "\n")
    t = _PLACEHOLDER_RE.sub("", t).strip()
    if not t:
        return ""

    is_zh = (str(target_lang or "").lower().startswith(("zh", "chinese"))
             or _CJK_RE.search(t) is not None)

    if is_zh:
        # 半角标点 → 全角，但只在中文字符后转换，避免毁掉 1,000 / 3.5% / US$1.57
        for half, full in ((",", "，"), (";", "；"), (":", "："), ("?", "？"), ("!", "！")):
            t = re.sub(rf"(?<=[{_CJKISH}])\s*{re.escape(half)}\s*", full, t)
        # 中文字词/标点之间的多余空格（同一行内）—— 保留数字与英文之间的空格
        t = re.sub(rf"(?<=[{_CJKISH}])[ \t]+(?=[{_CJKISH}])", "", t)
        # 行末缺标点则补句号
        fixed_lines = []
        for ln in t.split("\n"):
            s = ln.rstrip()
            if s and _CJK_RE.search(s) and s[-1] not in _ZH_TERMINAL:
                s += "。"
            fixed_lines.append(s)
        t = "\n".join(fixed_lines)
    else:
        # 英文目标：中文标点回转，压缩多余空白
        for zh_p, en_p in (("，", ", "), ("；", "; "), ("：", ": "),
                           ("？", "?"), ("！", "!"), ("。", ". ")):
            t = t.replace(zh_p, en_p)
        t = re.sub(r"[ \t]{2,}", " ", t)
        t = re.sub(r"[ \t]+([,.;:!?])", r"\1", t)

    # 段落空行归一：最多一个空行
    t = re.sub(r"[ \t]+\n", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def audit_translation(source: str, target: str, target_lang: str = "zh") -> dict:
    """漏译审计：句数守恒 + 长度区间。

    这是"整句消失"唯一的自动探测手段——用户的译文里
    "smoothing the seasonality that has always troubled the visitor economy"
    整句丢失，而当时没有任何机制发现它。
    """
    src_s = sentence_count(source)
    tgt_s = sentence_count(target)
    coverage = (tgt_s / src_s) if src_s else 1.0
    ratio = (len(target) / len(source)) if source else 0.0
    is_zh = str(target_lang or "").lower().startswith(("zh", "chinese"))
    # 中文译文通常比英文原文短（字符数），但不应短到离谱
    lo, hi = (0.26, 1.30) if is_zh else (0.80, 3.40)

    issues = []
    if not (source and target):
        issues.append("译文为空")
    else:
        if src_s and tgt_s and coverage < 0.75:
            issues.append(f"句数偏少（原文 {src_s} 句 → 译文 {tgt_s} 句）")
        if ratio < lo:
            issues.append(f"译文过短（长度比 {ratio:.2f} < {lo}）")
        elif ratio > hi:
            issues.append(f"译文过长（长度比 {ratio:.2f} > {hi}），可能混入说明文字")

    return {
        "src_sentences": src_s,
        "tgt_sentences": tgt_s,
        "coverage": round(coverage, 3),
        "length_ratio": round(ratio, 3),
        "ok": not issues,
        "issues": issues,
    }


@register_engine
class AITranslator(Translator):
    """Universal AI translation engine with multi-pass quality enhancement."""

    name = "ai"
    priority = 5

    _PROVIDERS = {
        "openai": {
            "base_url": "https://api.openai.com/v1",
            "default_model": "gpt-4o-mini",
        },
        "kimi": {
            "base_url": "https://api.moonshot.cn/v1",
            "default_model": "moonshot-v1-128k",
        },
        "deepseek": {
            "base_url": "https://api.deepseek.com/v1",
            "default_model": "deepseek-chat",
        },
        "azure": {
            "base_url": "",
            "default_model": "",
        },
        "ollama": {
            "base_url": "http://localhost:11434/v1",
            "default_model": "llama3.1",
        },
        "custom": {
            "base_url": "",
            "default_model": "",
        },
    }

    def __init__(self, settings: dict | None = None) -> None:
        self.settings = settings or {}
        self.provider = self.settings.get("ai_provider", "openai")
        self.api_key = self.settings.get("ai_api_key", "")
        self.base_url = self.settings.get("ai_base_url", "")
        self.model = self.settings.get("ai_model", "")
        self.timeout = self.settings.get("ai_timeout", 60)
        self.max_tokens = self.settings.get("ai_max_tokens", 4096)
        self.temperature = self.settings.get("ai_temperature", 0.3)

        if not self.base_url:
            self.base_url = self._PROVIDERS.get(self.provider, {}).get("base_url", "")
        if not self.model:
            self.model = self._PROVIDERS.get(self.provider, {}).get("default_model", "")

        self._client = None

    @property
    def available(self) -> bool:
        return bool(self.api_key and self.base_url and self.model)

    @property
    def priority(self) -> int:
        return self.settings.get("ai_priority", 5)

    def health_check(self) -> bool:
        return self.available

    # ── Core translate ──

    def translate(self, request: TranslationRequest) -> TranslationResponse:
        resp = TranslationResponse(engine=self.name)
        if not self.available:
            resp.errors.append(f"AI provider '{self.provider}' not configured (missing key/base_url/model)")
            return resp

        src, tgt = self._resolve_lang_pair(request)
        system_prompt = self._build_system_prompt(request)
        chunk_size = self._chunk_size_for_model()
        text = request.text
        self._chunk_errors = []
        self._chunk_retried = 0

        if len(text) <= chunk_size:
            raw_translation = self._translate_single(
                text, system_prompt, self._build_chunk_prompt(text, src, tgt, None))
            single_shot = True
        else:
            raw_translation = self._translate_long_with_context(request, system_prompt, chunk_size)
            single_shot = False

        if not raw_translation:
            resp.errors.append("AI translation returned empty")
            resp.errors.extend(self._chunk_errors[:5])
            return resp

        # ── ① 译后规范化：标点、空格、段落、占位符 ──
        raw_translation = normalize_target_text(raw_translation, tgt)

        # ── ② 漏译审计：整句消失是这里最严重的缺陷，必须自动发现 ──
        audit = audit_translation(text, raw_translation, tgt)
        if not audit["ok"] and single_shot:
            # 单块全文才值得整篇重译；分块路径已逐块重试过了
            retry = self._translate_single(
                text, system_prompt,
                self._build_chunk_prompt(text, src, tgt, None) + _COMPLETENESS_NUDGE)
            if retry:
                retry = normalize_target_text(retry, tgt)
                audit_retry = audit_translation(text, retry, tgt)
                if audit_retry["ok"] or len(retry) > len(raw_translation):
                    raw_translation, audit = retry, audit_retry
                    resp.metadata["audit_retried"] = True
        resp.metadata["audit"] = audit
        if self._chunk_retried:
            resp.metadata["chunk_retried"] = self._chunk_retried
        if not audit["ok"]:
            resp.errors.append("译文可能不完整：" + "；".join(audit["issues"][:2]))
        resp.errors.extend(self._chunk_errors[:5])

        # ── ③ 校对：只在不把译文改坏的前提下采纳 ──
        # 旧实现无条件采纳 AI 校对结果，校对一旦删内容，用户拿到的是更差的译文。
        # 旧实现还只在有术语表时才校对 —— 而"生硬"恰恰出现在普通正文里，
        # 所以现在默认对成篇文档也做一次校对（可用 ai_full_text_qa=false 关掉）。
        want_qa = bool(request.glossary) or (
            self.settings.get("ai_full_text_qa", True) is not False
            and 200 <= len(text) <= 12000
        )
        if want_qa:
            qa_result = self._run_qa(request, raw_translation)
            if qa_result and qa_result != raw_translation:
                qa_norm = normalize_target_text(qa_result, tgt)
                qa_audit = audit_translation(text, qa_norm, tgt)
                too_short = len(qa_norm) < len(raw_translation) * 0.90
                if too_short:
                    resp.metadata["qa_rejected"] = "shorter"
                elif qa_audit["ok"] or not audit["ok"]:
                    resp.metadata["qa_refined"] = True
                    resp.metadata["qa_summary"] = self._qa_diff_summary(raw_translation, qa_norm)
                    raw_translation = qa_norm
                    resp.metadata["audit"] = qa_audit
                else:
                    resp.metadata["qa_rejected"] = "missing-content"

        resp.text = raw_translation
        resp.source_lang = src
        resp.target_lang = tgt
        resp.confidence = 0.90
        resp.metadata = {**resp.metadata, "provider": self.provider, "model": self.model}
        return resp

    # ── Context-aware long text translation ──

    def _translate_long_with_context(self, request: TranslationRequest, system_prompt: str, chunk_size: int) -> str:
        """分块翻译长文本，逐块做漏译审计并重试，最后按段落拼回。

        每块把上一块的原文/译文作为上下文桥接，保证术语与语气连贯。
        """
        src, tgt = self._resolve_lang_pair(request)
        chunks = self._split_text(request.text, chunk_size)
        self._chunk_errors = []
        translated: list[tuple[int, str]] = []
        prev_source = ""
        prev_translation = ""

        for i, (para_no, chunk) in enumerate(chunks):
            context_hint = None
            if prev_translation:
                context_hint = self._find_context_bridge(chunk, prev_source, prev_translation) or None

            prompt = self._build_chunk_prompt(chunk, src, tgt, context_hint)
            tr = self._translate_single(chunk, system_prompt, prompt)
            audit = audit_translation(chunk, tr or "", tgt)

            # 漏译/空返回 → 带着"逐句完整"的硬提醒重试一次
            if not tr or not audit["ok"]:
                tr2 = self._translate_single(chunk, system_prompt, prompt + _COMPLETENESS_NUDGE)
                audit2 = audit_translation(chunk, tr2 or "", tgt)
                better = bool(tr2) and (audit2["ok"] or (not audit["ok"])
                                        and len(tr2) > len(tr or ""))
                if better:
                    tr, audit = tr2, audit2
                    self._chunk_retried = getattr(self, "_chunk_retried", 0) + 1

            if tr and tr.strip():
                translated.append((para_no, tr.strip()))
                prev_source, prev_translation = chunk, tr
            else:
                # 绝不把 "[Translation missing...]" 之类占位符写进译文
                self._chunk_errors.append(
                    f"第 {i + 1} 段翻译失败，已跳过（原文 {len(chunk)} 字符）："
                    f"{chunk[:40]}…"
                )

        if not translated:
            return ""

        # 按段落号拼回真实段落（同段落内的块用空格连接，交给 normalize 收拾）
        order: list[int] = []
        by_para: dict[int, list[str]] = {}
        for para_no, txt in translated:
            if para_no not in by_para:
                by_para[para_no] = []
                order.append(para_no)
            by_para[para_no].append(txt)
        return "\n\n".join(" ".join(by_para[p]).strip() for p in order)

    def _find_context_bridge(self, current_chunk: str, prev_source: str, prev_translation: str) -> str:
        """Extract the last substantive sentence(s) from previous chunk as context bridge."""
        src_sentences = re.split(r"(?<=[.!?。！？])\s+", prev_source.strip())
        tgt_sentences = re.split(r"(?<=[.!?。！？])\s+", prev_translation.strip())
        # Use up to last 2 sentences (source + translation pair)
        bridge_parts = []
        for j in range(max(1, len(src_sentences) - 2), len(src_sentences)):
            if j < len(src_sentences) and j < len(tgt_sentences):
                s = src_sentences[j].strip()
                t = tgt_sentences[j].strip()
                if s and t and len(s) < 300:
                    bridge_parts.append(f"  Source: {s}\n  Translation: {t}")
        if bridge_parts:
            return "Previous segment context:\n" + "\n".join(bridge_parts)
        return ""

    def _build_chunk_prompt(self, chunk_text: str, src: str, tgt: str, context_hint: str | None) -> str:
        """单块翻译的 user prompt（用目标语文风引导，中文指令更贴合中译）"""
        target_name = {"zh": "简体中文", "en": "英文"}.get(str(tgt)[:2].lower(), tgt)
        paras = [p for p in re.split(r"\n\s*\n", chunk_text) if p.strip()]
        if len(paras) <= 1 and "\n" in chunk_text:
            paras = [p for p in chunk_text.split("\n") if p.strip()]
        parts = [
            f"请把下面的内容译成{target_name}（{src} → {tgt}）。"
            "只输出译文，不要任何解释或标注；逐句完整翻译，不得漏句；保持原有段落结构。"
        ]
        if len(paras) >= 2:
            parts.append(f"下面共 {len(paras)} 段，译文必须同样是 {len(paras)} 段，"
                         "段与段之间保持换行，不要合并成一段。")
        if context_hint:
            parts.append(f"\n{context_hint}\n\n以下是紧接着的下一段，请保持衔接一致：")
        parts.append(f"\n{chunk_text}")
        return "\n".join(parts)

    # ── Post-translation QA ──

    def _run_qa(self, request: TranslationRequest, translation: str) -> str | None:
        """跑一次 AI 校对。

        以前只要没有术语表就直接返回 None（不校对），可"生硬"恰恰出现在
        没有术语表的普通正文里。现在即使没有术语表也校对，术语表只作为附加约束。
        """
        qa_terms = (request.glossary or [])[:60]
        qa_prompt = self._build_qa_prompt(request, translation, qa_terms)
        # Lower temperature for QA to be more conservative
        original_temp = self.temperature
        self.temperature = 0.1
        try:
            result = self._translate_single(translation, _QA_SYSTEM_PROMPT, qa_prompt)
        finally:
            self.temperature = original_temp
        if result and result != translation:
            return result
        return None

    def _build_qa_prompt(self, request: TranslationRequest, translation: str, qa_terms: list[dict]) -> str:
        """Build the QA review prompt with glossary terms highlighted."""
        parts = [
            "Review the following translation for accuracy and term consistency.",
            f"\nSource language: {request.source_lang or 'auto'}",
            f"Target language: {request.target_lang or 'auto'}",
            f"Domain: {request.domain or 'general'}",
        ]
        if qa_terms:
            parts.append("\n\nGlossary terms that MUST be used consistently in the translation:")
            for g in qa_terms:
                term = g.get("term", "")
                trans = g.get("translation", "")
                if term and trans:
                    parts.append(f'  "{term}" → "{trans}"')
        parts.append("\n\nSOURCE TEXT:")
        parts.append(request.text)
        parts.append("\n\nTRANSLATION TO REVIEW:")
        parts.append(translation)
        parts.append("\n\nFixed translation:")
        return "\n".join(parts)

    @staticmethod
    def _qa_diff_summary(original: str, refined: str) -> dict[str, Any]:
        """Produce a simple summary of what changed during QA."""
        if original == refined:
            return {"changed": False}
        orig_words = set(original.lower().split())
        ref_words = set(refined.lower().split())
        return {
            "changed": True,
            "original_length": len(original),
            "refined_length": len(refined),
            "words_replaced": len(orig_words - ref_words),
        }

    # ── Prompt building ──

    def _build_system_prompt(self, request: TranslationRequest) -> str:
        parts = [_SYSTEM_PROMPT]
        domain = request.domain or "general"
        domain_hint = DOMAIN_PROMPTS.get(domain, DOMAIN_PROMPTS["general"])
        parts.append(f"\nDomain context: {domain_hint}")
        if request.style and request.style != domain:
            parts.append(f"Style: {request.style}")
        glossary = request.glossary or []
        if glossary:
            parts.append("\nGlossary (you MUST use these translations consistently throughout):")
            for g in glossary[:50]:
                term = g.get("term", "")
                trans = g.get("translation", "")
                if term and trans:
                    parts.append(f'  "{term}" → "{trans}"')
            if len(glossary) > 50:
                parts.append(f"  ... and {len(glossary) - 50} more terms")
        if getattr(request, "rag_context", ""):
            parts.append("\n【Retrieval-Augmented Context】")
            parts.append(request.rag_context)
        return "\n".join(parts)

    def _build_user_prompt(self, request: TranslationRequest, src: str, tgt: str) -> str:
        target_name = {"zh": "简体中文", "en": "英文"}.get(str(tgt)[:2].lower(), tgt)
        return (f"请把下面的内容译成{target_name}（{src} → {tgt}）。"
                "只输出译文，逐句完整，不得漏句、保持段落结构。\n\n"
                f"{request.text}")

    # ── Single chunk translation ──

    def _translate_single(self, text: str, system_prompt: str, user_prompt: str) -> str | None:
        return self._chat(user_prompt, system_prompt=system_prompt)

    # ── API calls ──

    def _chat(self, user_prompt: str, system_prompt: str = "", max_tokens: int | None = None) -> str | None:
        try:
            from openai import OpenAI
        except ImportError:
            return self._chat_raw(user_prompt, system_prompt, max_tokens)

        client = self._get_client()
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_prompt})

        try:
            completion = client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=self.temperature,
                max_tokens=max_tokens or self.max_tokens,
                timeout=self.timeout,
            )
            if completion.choices:
                content = completion.choices[0].message.content
                if content:
                    content = content.strip()
                    content = re.sub(r"^```[a-z]*\n?", "", content)
                    content = re.sub(r"\n?```$", "", content)
                    content = content.strip('"').strip("'")
                    return content.strip()
        except Exception as e:
            import logging
            logging.getLogger("termprep.ai_translator").warning(f"AI chat failed: {e}")
        return None

    def _chat_raw(self, user_prompt: str, system_prompt: str = "", max_tokens: int | None = None) -> str | None:
        import requests
        url = f"{self.base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_prompt})
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": max_tokens or self.max_tokens,
        }
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=self.timeout)
            if r.status_code == 200:
                data = r.json()
                choices = data.get("choices", [])
                if choices:
                    content = choices[0].get("message", {}).get("content", "")
                    if content:
                        return content.strip()
        except Exception:
            pass
        return None

    def _get_client(self) -> Any:
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        return self._client

    # ── Helpers ──

    def _resolve_lang_pair(self, request: TranslationRequest) -> tuple[str, str]:
        src = request.source_lang
        tgt = request.target_lang
        if src == "auto":
            src = "zh" if bool(re.search(r"[\u4e00-\u9fff]", request.text)) else "en"
        if tgt == "auto":
            tgt = "en" if src.startswith("zh") else "zh"
        return src, tgt

    def _chunk_size_for_model(self) -> int:
        model = self.model.lower()
        if "128k" in model or "gpt-4o" in model or "claude-3" in model:
            return 30000
        if "32k" in model:
            return 12000
        if "16k" in model or "gpt-4" in model:
            return 6000
        if "8k" in model or "gpt-3.5" in model:
            return 3000
        return 4000

    @staticmethod
    def _split_text(text: str, chunk_size: int) -> list[tuple[int, str]]:
        """按段落切块，保留段落结构；超长段落再按句子切。

        返回 ``[(段落号, 块文本), ...]``，同段落的块共用一个段落号。

        与旧实现的区别（这正是"译文生硬、句子粘连"的根因）：
          · 旧实现用 ``buf += s`` 直接拼句子 —— 句子之间没有任何分隔符，
            产生 ``journey.Now`` 这种英文粘连；段落换行更是完全丢失。
          · 旧实现还把所有块用 ``\\n\\n`` 硬拼回去，段落数是假的。
          · 新实现切分时**取原文切片**，空白与段落边界原样保留，
            最后由调用方按段落号拼回真实段落。
        """
        norm = (text or "").replace("\r\n", "\n").replace("\r", "\n")
        if not norm.strip():
            return []
        chunk_size = max(200, int(chunk_size))
        out: list[tuple[int, str]] = []
        # 优先按空行分段；若全文没有任何空行（讲稿、PDF 抽取文本常见），
        # 单个换行就是唯一的段落信息，必须用上，否则段落结构会全丢。
        if re.search(r"\n\s*\n", norm):
            paragraphs = re.split(r"\n\s*\n", norm)
        else:
            paragraphs = norm.split("\n")

        for pi, para in enumerate(paragraphs):
            para = para.strip()
            if not para:
                continue
            if len(para) <= chunk_size:
                out.append((pi, para))
                continue
            # 超长段落：在句末切开，但拼接用的是原文切片（含原有空格）
            start = 0
            for m in re.finditer(r"[.!?。！？]+[\u201d\"'\uff09)\]]*\s+", para):
                if m.end() - start >= chunk_size:
                    piece = para[start:m.end()].strip()
                    if piece:
                        out.append((pi, piece))
                    start = m.end()
            tail = para[start:].strip()
            if tail:
                out.append((pi, tail))
        return out or [(0, norm.strip())]

    def translate_stream(self, request: TranslationRequest):
        if not self.available:
            yield "[AI translator not configured]"
            return
        yield self.translate(request).text
