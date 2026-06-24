"""AI Large-Language-Model translation engine.

Supports any OpenAI-compatible API, including:
- OpenAI GPT-4 / GPT-3.5-turbo
- Kimi (Moonshot) — 国内稳定，长文本
- Azure OpenAI
- 阿里云百炼 (兼容模式)
- Ollama (本地) — 通过 openai-compatible 或原生 HTTP

Usage:
    from termprep.engines.ai_translator import AITranslator
    from termprep.interfaces.translator import TranslationRequest

    engine = AITranslator({
        "ai_provider": "kimi",
        "ai_api_key": "sk-...",
        "ai_model": "moonshot-v1-128k",
    })
    result = engine.translate(TranslationRequest(text="...", domain="medical"))
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
1. Output ONLY the translated text. Do NOT add explanations, summaries, markdown formatting, or quotation marks around the output.
2. Preserve all paragraphs, line breaks, and punctuation style from the source.
3. If a glossary is provided, you MUST use the specified translations for those terms consistently.
4. Do not add content that is not present in the source text. Do not omit content.
5. For domain-specific texts, use the appropriate professional terminology register described below."""


@register_engine
class AITranslator(Translator):
    """Universal AI translation engine."""

    name = "ai"
    priority = 5  # highest priority by default (AI beats Google)

    # Provider configs
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
            "base_url": "",  # must be provided, e.g. https://xxx.openai.azure.com/openai/deployments/xxx
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

        # Resolve defaults
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
        # 只做配置检查：key + base_url + model 齐全即认为可用
        # 实际翻译时如果网络失败会自动 fallback 到免费引擎
        return self.available

    # ── Core translate ──

    def translate(self, request: TranslationRequest) -> TranslationResponse:
        resp = TranslationResponse(engine=self.name)
        if not self.available:
            resp.errors.append(f"AI provider '{self.provider}' not configured (missing key/base_url/model)")
            return resp

        # Detect / normalize language pair
        src, tgt = self._resolve_lang_pair(request)

        # Build prompt
        system_prompt = self._build_system_prompt(request)
        user_prompt = self._build_user_prompt(request, src, tgt)

        # Chunking strategy: respect model context window
        chunk_size = self._chunk_size_for_model()
        text = request.text

        if len(text) <= chunk_size:
            translated = self._translate_single(text, system_prompt, user_prompt)
        else:
            translated = self._translate_long(request, system_prompt, user_prompt, chunk_size)

        if translated:
            resp.text = translated
            resp.source_lang = src
            resp.target_lang = tgt
            resp.confidence = 0.90  # AI generally high confidence
            resp.metadata = {"provider": self.provider, "model": self.model}
        else:
            resp.errors.append("AI translation returned empty")

        return resp

    # ── Prompt engineering ──

    def _build_system_prompt(self, request: TranslationRequest) -> str:
        parts = [_SYSTEM_PROMPT]
        domain = request.domain or "general"
        domain_hint = DOMAIN_PROMPTS.get(domain, DOMAIN_PROMPTS["general"])
        parts.append(f"\nDomain context: {domain_hint}")
        if request.style and request.style != domain:
            parts.append(f"Style: {request.style}")
        if request.glossary:
            parts.append("\nGlossary (you MUST use these translations consistently):")
            for g in request.glossary[:30]:  # limit to avoid token bloat
                term = g.get("term", "")
                trans = g.get("translation", "")
                if term and trans:
                    parts.append(f'  "{term}" → "{trans}"')
        # RAG context injection
        if getattr(request, "rag_context", ""):
            parts.append("\n【Retrieval-Augmented Context】")
            parts.append(request.rag_context)
        return "\n".join(parts)

    def _build_user_prompt(self, request: TranslationRequest, src: str, tgt: str) -> str:
        direction = f"{src} → {tgt}"
        return f"Translate the following text from {direction}. Output ONLY the translated text, no explanation.\n\n{request.text}"

    # ── API calls ──

    def _translate_single(self, text: str, system_prompt: str, user_prompt: str) -> str | None:
        return self._chat(user_prompt, system_prompt=system_prompt)

    def _translate_long(self, request: TranslationRequest, system_prompt: str, user_prompt: str, chunk_size: int) -> str:
        """Translate long text by splitting at sentence boundaries."""
        src, tgt = self._resolve_lang_pair(request)
        chunks = self._split_text(request.text, chunk_size)
        results = []
        for i, chunk in enumerate(chunks):
            chunk_request = TranslationRequest(
                text=chunk,
                source_lang=request.source_lang,
                target_lang=request.target_lang,
                domain=request.domain,
                style=request.style,
                glossary=request.glossary,
                use_rag=request.use_rag,
                rag_context=request.rag_context,
            )
            prompt = self._build_user_prompt(chunk_request, src, tgt)
            tr = self._chat(prompt, system_prompt=system_prompt)
            if tr:
                results.append(tr)
            else:
                results.append(f"[Translation missing for chunk {i+1}]")
        return "\n\n".join(results)

    def _chat(self, user_prompt: str, system_prompt: str = "", max_tokens: int | None = None) -> str | None:
        """Low-level chat completion. Works with any OpenAI-compatible server."""
        try:
            from openai import OpenAI
        except ImportError:
            # Fallback: raw requests
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
        """Fallback using raw requests (no openai SDK)."""
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
        """Rough heuristic for Chinese characters per chunk."""
        model = self.model.lower()
        if "128k" in model or "gpt-4o" in model or "claude-3" in model:
            return 30000  # very large context
        if "32k" in model:
            return 12000
        if "16k" in model or "gpt-4" in model:
            return 6000
        if "8k" in model or "gpt-3.5" in model:
            return 3000
        return 4000  # safe default

    @staticmethod
    def _split_text(text: str, chunk_size: int) -> list[str]:
        """Split at sentence boundaries while respecting chunk size."""
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
        return chunks

    # ── Streaming (optional, for future use) ──

    def translate_stream(self, request: TranslationRequest):
        """Yield translated text chunks as they arrive."""
        if not self.available:
            yield "[AI translator not configured]"
            return
        # TODO: implement SSE streaming using openai SDK stream=True
        yield self.translate(request).text
