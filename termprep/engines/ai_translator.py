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
    "general":  "翻译自然流畅，将习语转换为地道的目标语言等价表达。",
}

_SYSTEM_PROMPT = """You are a professional translation engine. Your task is to translate the given text accurately while preserving the original meaning, tone, and structure.

Rules:
1. Output ONLY the translated text. Do NOT add explanations, summaries, markdown formatting, or quotation marks.
2. Preserve all paragraphs, line breaks, and punctuation style from the source.
3. If a glossary is provided, you MUST use the specified translations for those terms consistently throughout.
4. Do not add content not present in the source. Do not omit content.
5. For domain-specific texts, use the appropriate professional terminology."""

_QA_SYSTEM_PROMPT = """You are a professional translation quality reviewer. Your task is to review a translation and fix any issues.

You MUST check for:
1. Missing or omitted content — compare source with translation sentence by sentence
2. Glossary term consistency — ensure all glossary-specified terms use their correct translations
3. Awkward phrasing — unnatural target-language expressions
4. Inconsistent terminology — same source term translated differently across the text
5. Structural issues — paragraph breaks, formatting, lists

Output format: Return the CORRECTED full translation text ONLY. If the translation is already perfect, return it unchanged. Do not add explanations."""


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

        if len(text) <= chunk_size:
            raw_translation = self._translate_single(text, system_prompt, self._build_chunk_prompt(text, src, tgt, None))
        else:
            raw_translation = self._translate_long_with_context(request, system_prompt, chunk_size)

        if not raw_translation:
            resp.errors.append("AI translation returned empty")
            return resp

        # ── Post-translation QA: AI self-review ──
        if request.glossary:
            qa_result = self._run_qa(request, raw_translation)
            if qa_result and qa_result != raw_translation:
                resp.metadata["qa_refined"] = True
                resp.metadata["qa_summary"] = self._qa_diff_summary(raw_translation, qa_result)
                raw_translation = qa_result

        resp.text = raw_translation
        resp.source_lang = src
        resp.target_lang = tgt
        resp.confidence = 0.90
        resp.metadata = {**resp.metadata, "provider": self.provider, "model": self.model}
        return resp

    # ── Context-aware long text translation ──

    def _translate_long_with_context(self, request: TranslationRequest, system_prompt: str, chunk_size: int) -> str:
        """Translate long text with context passing between chunks.

        Each chunk receives the previous chunk's source and translation as context,
        ensuring terminology consistency and coherent flow across chunk boundaries.
        """
        src, tgt = self._resolve_lang_pair(request)
        chunks = self._split_text(request.text, chunk_size)
        results = []
        prev_source = ""
        prev_translation = ""

        for i, chunk in enumerate(chunks):
            context_hint = None
            if i > 0 and prev_translation:
                bridge = self._find_context_bridge(chunk, prev_source, prev_translation)
                if bridge:
                    context_hint = bridge

            chunk_prompt = self._build_chunk_prompt(chunk, src, tgt, context_hint)
            tr = self._translate_single(chunk, system_prompt, chunk_prompt)
            if tr:
                results.append(tr)
                prev_source = chunk
                prev_translation = tr
            else:
                results.append(f"[Translation missing for segment {i+1}]")

        return "\n\n".join(results)

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
        """Build user prompt for a single chunk, optionally with context."""
        direction = f"{src} → {tgt}"
        parts = [f"Translate the following text from {direction}. Output ONLY the translated text."]
        if context_hint:
            parts.append(f"\n{context_hint}\n\nContinue from the context above. The following text is the next segment to translate:")
        parts.append(f"\n{chunk_text}")
        return "\n".join(parts)

    # ── Post-translation QA ──

    def _run_qa(self, request: TranslationRequest, translation: str) -> str | None:
        """Run AI self-review: check glossary compliance and fix issues."""
        qa_terms = request.glossary[:60] if request.glossary else []
        if not qa_terms:
            return None
        qa_prompt = self._build_qa_prompt(request, translation, qa_terms)
        # Lower temperature for QA to be more conservative
        original_temp = self.temperature
        self.temperature = 0.1
        result = self._translate_single(translation, _QA_SYSTEM_PROMPT, qa_prompt)
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
        direction = f"{src} → {tgt}"
        return f"Translate the following text from {direction}. Output ONLY the translated text, no explanation.\n\n{request.text}"

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
    def _split_text(text: str, chunk_size: int) -> list[str]:
        chunks = []
        buf = ""
        for s in re.split(r"(?<=[.!?。！？])\s+", text):
            if not s.strip():
                continue
            if len(buf) + len(s) < chunk_size:
                buf += s
            else:
                if buf.strip():
                    chunks.append(buf.strip())
                buf = s
        if buf.strip():
            chunks.append(buf.strip())
        return chunks if chunks else [text]

    def translate_stream(self, request: TranslationRequest):
        if not self.available:
            yield "[AI translator not configured]"
            return
        yield self.translate(request).text
