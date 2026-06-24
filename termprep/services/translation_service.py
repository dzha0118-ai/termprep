"""Translation Service — orchestrates multiple engines with fallback and caching."""

from typing import Any

from termprep.engines.base import build_available_engines, get_engine
from termprep.interfaces.translator import TranslationRequest, TranslationResponse, Translator


class TranslationService:
    """High-level translation service used by CLI and Web.

    Responsibilities:
    - Engine selection (by name or auto)
    - Fallback chain execution
    - Simple in-memory caching (optional)
    - Metrics / logging (optional)
    """

    def __init__(self, settings: dict | None = None) -> None:
        self.settings = settings or {}
        self._engines: list[Translator] = []
        self._cache: dict[str, TranslationResponse] = {}
        self._cache_enabled = self.settings.get("translation_cache", False)
        self._max_fallbacks = self.settings.get("max_fallbacks", 3)

    # ── Lifecycle ──

    def load_engines(self) -> None:
        """Discover and instantiate all available engines."""
        self._engines = build_available_engines(self.settings)

    # ── Public API ──

    def translate(
        self,
        text: str,
        source_lang: str = "auto",
        target_lang: str = "auto",
        domain: str = "general",
        engine: str | None = None,
        glossary: list[dict] | None = None,
        use_rag: bool = False,
    ) -> TranslationResponse:
        """Translate with optional engine selection and automatic fallback.

        Args:
            text: Source text.
            source_lang: Source language code.
            target_lang: Target language code.
            domain: Domain hint for style control.
            engine: Preferred engine name. If None, use priority chain.
            glossary: Optional bilingual glossary for term consistency.
            use_rag: Enable RAG retrieval for medical domain context.

        Returns:
            TranslationResponse (always non-None; check errors list).
        """
        request = TranslationRequest(
            text=text,
            source_lang=source_lang,
            target_lang=target_lang,
            domain=domain,
            glossary=glossary or [],
            use_rag=use_rag,
        )

        # RAG context injection
        rag_metadata = {"exact_terms": 0, "fuzzy_terms": 0, "corpus_chunks": 0}
        if use_rag:
            rag_context, rag_metadata = self._build_rag_context(text, domain)
            if rag_context:
                request.rag_context = rag_context
        # Cache check
        cache_key = self._make_key(request)
        if self._cache_enabled and cache_key in self._cache:
            return self._cache[cache_key]

        # Single engine mode
        if engine:
            result = self._translate_with_engine(request, engine)
        else:
            result = self._translate_with_fallback(request)

        # Inject RAG metadata into response
        result.rag_metadata = rag_metadata

        if self._cache_enabled:
            self._cache[cache_key] = result

        return result

    def translate_terms(
        self,
        terms: list[str],
        source_lang: str = "auto",
        target_lang: str = "auto",
        domain: str = "general",
        engine: str | None = None,
    ) -> list[dict]:
        """Batch-translate a list of terms using AI (optimal for glossary building).

        Returns a list of dicts: [{"term": "...", "translation": "...", "confidence": 0.9}]
        """
        if not terms:
            return []

        # Build a batch prompt for efficiency
        prompt_terms = "\n".join(f"- {t}" for t in terms)
        system = (
            "You are a professional terminology translator. "
            "Translate each term into the target language. "
            "Output exactly one line per term in the format: term || translation\n"
            f"Domain: {domain}."
        )
        user = f"Translate the following terms:\n\n{prompt_terms}"

        # Try AI first for batch terminology
        ai_engine = self._get_engine_instance("ai")
        if ai_engine and ai_engine.available:
            from termprep.interfaces.translator import TranslationRequest
            req = TranslationRequest(text=user, source_lang=source_lang, target_lang=target_lang, domain=domain)
            # Reuse AI engine's internal prompt builder, but override with batch system
            raw = ai_engine._chat(user, system_prompt=system)  # type: ignore[attr-defined]
            if raw:
                return self._parse_batch_output(terms, raw)

        # Fallback: one-by-one with any available engine
        results = []
        for t in terms:
            r = self.translate(t, source_lang, target_lang, domain, engine=engine)
            results.append({
                "term": t,
                "translation": r.text or "",
                "confidence": r.confidence,
                "engine": r.engine,
            })
        return results

    def list_available_engines(self) -> list[dict[str, Any]]:
        """Return metadata about available engines."""
        if not self._engines:
            self.load_engines()
        return [
            {
                "name": e.name,
                "available": e.available,
                "priority": e.priority,
                "health": e.health_check(),
            }
            for e in self._engines
        ]

    # ── Internal ──

    def _translate_with_engine(self, request: TranslationRequest, engine_name: str) -> TranslationResponse:
        instance = self._get_engine_instance(engine_name)
        if not instance:
            return TranslationResponse(
                errors=[f"Engine '{engine_name}' not found"],
            )
        if not instance.available:
            return TranslationResponse(
                errors=[f"Engine '{engine_name}' not available"],
            )
        try:
            return instance.translate(request)
        except Exception as e:
            return TranslationResponse(errors=[f"Engine '{engine_name}' failed: {e}"])

    def _translate_with_fallback(self, request: TranslationRequest) -> TranslationResponse:
        if not self._engines:
            self.load_engines()

        if not self._engines:
            return TranslationResponse(errors=["No translation engines available"])

        for engine in self._engines[: self._max_fallbacks]:
            try:
                result = engine.translate(request)
                if not result.errors and result.text:
                    return result
            except Exception as e:
                continue

        return TranslationResponse(errors=["All translation engines failed"])

    def _get_engine_instance(self, name: str) -> Translator | None:
        cls = get_engine(name)
        if not cls:
            return None
        try:
            return cls(settings=self.settings)
        except Exception:
            return None

    @staticmethod
    def _make_key(request: TranslationRequest) -> str:
        import hashlib
        text = f"{request.text}:{request.source_lang}:{request.target_lang}:{request.domain}:{request.use_rag}"
        return hashlib.md5(text.encode()).hexdigest()

    def _build_rag_context(self, text: str, domain: str) -> tuple[str, dict[str, Any]]:
        """Build RAG context string and metadata for medical translation."""
        try:
            from termprep.rag.retriever import get_retriever
            from termprep.rag.medical_schema import MedicalDomain

            # Map common domain names to MedicalDomain values
            domain_map = {"medical": "clinical"}
            domain_key = domain_map.get(domain.lower(), domain.lower())
            try:
                med_domain = MedicalDomain(domain_key)
            except ValueError:
                med_domain = MedicalDomain.GENERAL

            retriever = get_retriever()
            rag_ctx = retriever.retrieve(text, domain=med_domain)
            if not rag_ctx.is_empty():
                metadata = {
                    "exact_terms": len(rag_ctx.exact_terms),
                    "fuzzy_terms": len(rag_ctx.fuzzy_terms),
                    "corpus_chunks": len(rag_ctx.corpus_chunks),
                }
                return rag_ctx.to_prompt(max_terms=20, max_chunks=5), metadata
        except Exception as e:
            import logging
            logging.getLogger("termprep.rag").warning(f"RAG retrieval failed: {e}")
        return "", {"exact_terms": 0, "fuzzy_terms": 0, "corpus_chunks": 0}

    @staticmethod
    def _parse_batch_output(terms: list[str], raw: str) -> list[dict]:
        """Parse 'term || translation' lines from AI batch response."""
        results = []
        mapping = {}
        for line in raw.splitlines():
            if "||" in line:
                parts = line.split("||", 1)
                src = parts[0].strip().lstrip("- ").strip('"')
                tgt = parts[1].strip().strip('"')
                mapping[src.lower()] = tgt

        for t in terms:
            translation = mapping.get(t.lower(), "")
            if not translation:
                # Fuzzy match
                for k, v in mapping.items():
                    if k in t.lower() or t.lower() in k:
                        translation = v
                        break
            results.append({
                "term": t,
                "translation": translation,
                "confidence": 0.90 if translation else 0.0,
                "engine": "ai",
            })
        return results
