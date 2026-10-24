"""Translation Service — orchestrates multiple engines with fallback and caching."""

import json
import re
from typing import Any, Iterable

from termprep.engines.base import build_available_engines, get_engine
from termprep.interfaces.translator import TranslationRequest, TranslationResponse, Translator


def _norm_term(s: str) -> str:
    """Normalise a term for key matching (case / surrounding noise insensitive)."""
    return re.sub(r"\s+", " ", (s or "").strip().strip("\"'`*")).lower()


def _chunks(seq: list, size: int) -> Iterable[list]:
    """Yield ``seq`` in fixed-size slices."""
    for i in range(0, len(seq), max(1, size)):
        yield seq[i:i + size]


def _resolve_terms(parsed: dict[str, str], wanted: dict[str, str]) -> dict[str, str]:
    """Keep only parsed entries that correspond to a requested term."""
    return {k: v for k, v in parsed.items() if k in wanted and v}


# Separators seen in real LLM replies, most explicit first.
_BATCH_SEPARATORS: tuple[str, ...] = (
    "||", "=>", "->", "\u2192", "::", "\t", " || ", " | ", " - ", " \u2014 ", ": ", "\uff1a", ":",
)


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

        # Translation Memory injection: find similar past translations
        try:
            from termprep.tm import search as tm_search
            tm_matches = tm_search(text, source_lang, target_lang, domain, limit=3)
            if tm_matches:
                tm_hints = []
                for m in tm_matches:
                    if m.get("score", 0) >= 0.7:
                        tm_hints.append(f'  "{m["source"]}" → "{m["target"]}"')
                if tm_hints:
                    current_glossary = request.glossary or []
                    request.glossary = current_glossary + [
                        {"term": m["source"], "translation": m["target"], "confidence": "high", "source": "tm"}
                        for m in tm_matches if m.get("score", 0) >= 0.7
                    ]
        except Exception:
            pass

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

    # Terms per single AI request. The old code sent the entire glossary in one
    # prompt; long replies get truncated by the model, which silently dropped
    # two thirds of the glossary.
    TERM_BATCH_SIZE = 15
    # Upper bound on the last-resort one-term-at-a-time AI retries (pass 4).
    TERM_SINGLE_RETRY_LIMIT = 15

    def translate_terms(
        self,
        terms: list[str],
        source_lang: str = "auto",
        target_lang: str = "auto",
        domain: str = "general",
        engine: str | None = None,
    ) -> list[dict]:
        """Batch-translate terms, guaranteeing completeness.

        Returns a list of dicts aligned 1:1 with ``terms``:
        ``[{"term": ..., "translation": ..., "confidence": ..., "engine": ...}]``

        Completeness is enforced in three passes, because one AI call is not a
        reliable way to translate a whole glossary:

        1. bounded AI batches (``TERM_BATCH_SIZE``) with format-tolerant parsing;
        2. a focused retry pass over the terms the first pass missed;
        3. a per-term fallback through the free engine chain (Google / Youdao).

        A term that survives all three passes is returned with
        ``translation=""`` so the caller can mark it unresolved instead of
        inventing a value.
        """
        if not terms:
            return []

        results: list[dict] = [
            {"term": t, "translation": "", "confidence": 0.0, "engine": ""}
            for t in terms
        ]
        pos = {t: i for i, t in enumerate(terms)}

        def _fill(term: str, translation: str, confidence: float, engine_name: str) -> None:
            i = pos.get(term)
            if i is None or not translation:
                return
            # never accept a "translation" that merely echoes the source term
            if translation.strip().lower() == term.strip().lower():
                return
            results[i]["translation"] = translation
            results[i]["confidence"] = confidence
            results[i]["engine"] = engine_name

        ai_engine = self._get_engine_instance("ai")
        if ai_engine is not None and ai_engine.available:
            # ── pass 1: bounded batches ──
            for batch in _chunks(terms, self.TERM_BATCH_SIZE):
                for term, trans in self._ai_translate_batch(
                    ai_engine, batch, source_lang, target_lang, domain
                ).items():
                    _fill(term, trans, 0.9, "ai")

            # ── pass 2: retry only the holes, asking for far fewer terms ──
            holes = [t for t in terms if not results[pos[t]]["translation"]]
            for batch in _chunks(holes[: self.TERM_BATCH_SIZE * 2], self.TERM_BATCH_SIZE):
                for term, trans in self._ai_translate_batch(
                    ai_engine, batch, source_lang, target_lang, domain
                ).items():
                    _fill(term, trans, 0.75, "ai-retry")

        # ── pass 3: per-term fallback through the free engines ──
        for term in [t for t in terms if not results[pos[t]]["translation"]]:
            try:
                r = self.translate(term, source_lang, target_lang, domain, engine=engine)
            except Exception:
                continue
            text = (r.text or "").strip()
            if text and not text.startswith("["):
                _fill(term, text, 0.6, r.engine or "fallback")

        # ── pass 4: last-resort single-term AI calls ──
        # A one-term prompt is answered far more reliably than a 30-term prompt,
        # so this closes the truncation/omission gap at a bounded extra cost.
        if ai_engine is not None and ai_engine.available:
            leftovers = [
                t for t in terms if not results[pos[t]]["translation"]
            ][: self.TERM_SINGLE_RETRY_LIMIT]
            for term in leftovers:
                for t, trans in self._ai_translate_batch(
                    ai_engine, [term], source_lang, target_lang, domain
                ).items():
                    _fill(t, trans, 0.65, "ai-single")

        return results

    def _ai_translate_batch(
        self,
        ai_engine,
        batch: list[str],
        source_lang: str,
        target_lang: str,
        domain: str,
    ) -> dict[str, str]:
        """Translate one bounded batch. Returns ``{requested_term: translation}``."""
        if not batch:
            return {}
        numbered = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(batch))
        system = (
            "You are a professional terminology translator. "
            f"Translate every item from {source_lang} into {target_lang}. "
            "Output exactly one line per item, in the SAME ORDER, as:\n"
            "term || translation\n"
            "No numbering, bullets, explanations or blank lines. "
            f"Domain: {domain}."
        )
        user = f"Translate ALL {len(batch)} terms below:\n\n{numbered}"
        try:
            raw = ai_engine._chat(user, system_prompt=system)  # type: ignore[attr-defined]
        except Exception:
            return {}
        if not raw:
            return {}
        parsed = self._parse_batch_output(batch, raw)
        exact = {_norm_term(t): t for t in batch}
        return {exact[k]: v for k, v in parsed.items() if k in exact and v}

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
    def _parse_batch_output(terms: list[str], raw: str) -> dict[str, str]:
        """Parse a batch reply into ``{normalised_term: translation}``.

        Tolerates format drift, which is what used to discard whole batches: the
        old parser accepted **only** ``term || translation``, so a reply written
        as ``1. term - translation``, as a JSON object/array, as a markdown table,
        or wrapped in a fenced code block yielded no translations at all.

        It also drops the old loose substring "fuzzy" fallback, which could attach
        a *different* term's translation to the wrong headword — a wrong pairing
        is worse than a missing one.
        """
        wanted = {_norm_term(t): t for t in terms}
        out: dict[str, str] = {}

        # ── 1) fenced / bare JSON ──
        text = (raw or "").strip()
        for fence in ("```json", "```"):
            if text.startswith(fence):
                text = text[len(fence):]
        text = text.strip().strip("`").strip()

        parsed_json: Any = None
        if text[:1] in ("{", "["):
            try:
                parsed_json = json.loads(text)
            except Exception:
                parsed_json = None

        if isinstance(parsed_json, dict):
            for key, val in parsed_json.items():
                k = _norm_term(str(key))
                if k in wanted and isinstance(val, str) and val.strip():
                    out[k] = val.strip()
            if out:
                return _resolve_terms(out, wanted)
        elif isinstance(parsed_json, list):
            for item in parsed_json:
                if isinstance(item, dict):
                    src = item.get("term") or item.get("source") or item.get("source_term")
                    tgt = item.get("translation") or item.get("target") or item.get("target_translation")
                elif isinstance(item, (list, tuple)) and len(item) >= 2:
                    src, tgt = item[0], item[1]
                else:
                    continue
                k = _norm_term(str(src))
                if k in wanted and isinstance(tgt, str) and tgt.strip():
                    out.setdefault(k, tgt.strip())
            if out:
                return _resolve_terms(out, wanted)

        # ── 2) line-based, tolerant of separators and list markers ──
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue

            # markdown table row: | term | translation |
            if line.startswith("|") and line.count("|") >= 3:
                if set(line) <= set("|-: "):
                    continue  # header separator row
                cells = [c.strip() for c in line.strip("|").split("|")]
                if len(cells) >= 2:
                    k = _norm_term(cells[0])
                    tgt = cells[-1].strip("\"'`")
                    if k in wanted and tgt:
                        out.setdefault(k, tgt)
                continue

            # strip leading list markers: "- ", "* ", "1. ", "2) ", "3、 "
            line = re.sub(r"^\s*(?:[-*\u2022\u00b7]|\d+\s*[.)\u3001])\s*", "", line)

            for sep in _BATCH_SEPARATORS:
                if sep in line:
                    src_part, _, tgt_part = line.partition(sep)
                    k = _norm_term(src_part)
                    tgt = tgt_part.strip().strip("\"'`")
                    if k in wanted and tgt:
                        out.setdefault(k, tgt)
                    break

        return _resolve_terms(out, wanted)
