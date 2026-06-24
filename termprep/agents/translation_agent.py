"""Translation Agent — specialized agent for full-text translation with glossary injection.

Consumes a standardized Glossary JSON from the Term Agent and produces
a TranslationResult with term-level consistency tracking.

Key design principles:
  1. Stateless: no side effects, pure function from (text + glossary) → translation
  2. Glossary-aware: injects glossary into system prompt for term consistency
  3. Observable: returns per-segment alignment + term consistency score
  4. Fallback: if AI fails, degrades to Google / Youdao automatically
"""

from __future__ import annotations

import re
from typing import Any

from termprep.agents.schemas import (
    Glossary,
    TermEntry,
    TranslationRequest,
    TranslationResult,
    TranslationSegment,
)
from termprep.interfaces.translator import TranslationRequest as TranslatorRequest
from termprep.services.translation_service import TranslationService


class TranslationAgent:
    """Standalone agent for full-text translation with glossary support.

    Usage:
        agent = TranslationAgent()
        result = agent.translate(
            source_text="Machine learning...",
            glossary=glossary,  # from TermAgent
            domain="it",
        )
        print(result.translated_text)
    """

    def __init__(self, settings: dict | None = None) -> None:
        self.settings = settings or {}
        self._svc = TranslationService(self.settings)
        self._svc.load_engines()

    # ── Public API ──

    def translate(
        self,
        source_text: str,
        glossary: Glossary | None = None,
        source_lang: str = "auto",
        target_lang: str = "auto",
        domain: str = "general",
        style: str = "general",
        engine: str | None = None,
        preserve_formatting: bool = True,
        instructions: str = "",
        use_rag: bool = False,
    ) -> TranslationResult:
        """Translate full text, optionally using glossary for consistency."""
        return self._translate(
            TranslationRequest(
                source_text=source_text,
                glossary=glossary,
                source_lang=source_lang,
                target_lang=target_lang,
                domain=domain,
                style=style,
                engine=engine,
                preserve_formatting=preserve_formatting,
                instructions=instructions,
                use_rag=use_rag,
            )
        )

    def translate_with_request(self, request: TranslationRequest) -> TranslationResult:
        """Translate using a pre-built TranslationRequest."""
        return self._translate(request)

    # ── Core implementation ──

    def _translate(self, req: TranslationRequest) -> TranslationResult:
        # Detect language if auto
        src_lang, tgt_lang = self._resolve_lang(req.source_text, req.source_lang, req.target_lang)

        # Build glossary injection
        glossary_compact: list[dict[str, str]] = []
        if req.glossary:
            glossary_compact = req.glossary.to_compact()

        # Create engine request
        engine_req = TranslatorRequest(
            text=req.source_text,
            source_lang=src_lang,
            target_lang=tgt_lang,
            domain=req.domain,
            style=req.style,
            glossary=glossary_compact,
        )

        # Call translation service (with fallback chain)
        result = self._svc.translate(
            text=req.source_text,
            source_lang=src_lang,
            target_lang=tgt_lang,
            domain=req.domain,
            engine=req.engine,
            glossary=glossary_compact,
            use_rag=req.use_rag,
        )

        # Build structured result
        translation_result = TranslationResult(
            translated_text=result.text,
            source_text=req.source_text,
            source_lang=src_lang,
            target_lang=tgt_lang,
            domain=req.domain,
            engine=result.engine,
            model=result.metadata.get("model", ""),
            glossary_used=bool(glossary_compact),
            errors=result.errors,
            metadata={
                "provider": result.metadata.get("provider", ""),
                "confidence": result.confidence,
                "instructions": req.instructions,
            },
            rag_metadata=result.rag_metadata,
        )

        # Segment the translation (simple sentence-level)
        translation_result.segments = self._segment_translation(
            req.source_text, result.text, glossary_compact
        )

        # Compute term consistency score
        translation_result.term_consistency_score = self._compute_consistency(
            result.text, glossary_compact
        )

        return translation_result

    # ── Segmentation & Alignment ──

    def _segment_translation(
        self,
        source_text: str,
        translated_text: str,
        glossary: list[dict[str, str]],
    ) -> list[TranslationSegment]:
        """Split into sentence-level segments and highlight terms."""
        # Split sentences using common delimiters
        source_sentences = self._split_sentences(source_text)
        target_sentences = self._split_sentences(translated_text)

        segments: list[TranslationSegment] = []
        for i, src in enumerate(source_sentences):
            tgt = target_sentences[i] if i < len(target_sentences) else ""
            highlights = self._find_term_highlights(tgt, glossary)
            segments.append(
                TranslationSegment(
                    index=i,
                    source=src,
                    target=tgt,
                    highlights=highlights,
                    confidence=0.9 if highlights else 0.7,
                )
            )
        return segments

    def _find_term_highlights(
        self,
        text: str,
        glossary: list[dict[str, str]],
    ) -> list[dict[str, Any]]:
        """Find glossary terms in translated text and record positions."""
        highlights = []
        for g in glossary:
            term = g.get("translation", "")
            if not term:
                continue
            # Simple substring search (case-insensitive for English)
            pos = text.lower().find(term.lower())
            if pos >= 0:
                highlights.append({
                    "start": pos,
                    "end": pos + len(term),
                    "term": term,
                    "source_term": g.get("term", ""),
                })
        return highlights

    def _compute_consistency(
        self,
        translated_text: str,
        glossary: list[dict[str, str]],
    ) -> float:
        """Score how many glossary translations actually appear in the output."""
        if not glossary:
            return 0.0

        text_lower = translated_text.lower()
        found = 0
        for g in glossary:
            trans = g.get("translation", "").lower()
            if trans and trans in text_lower:
                found += 1

        return round(found / len(glossary), 4) if glossary else 0.0

    # ── Helpers ──

    def _resolve_lang(self, text: str, src: str, tgt: str) -> tuple[str, str]:
        if src == "auto":
            src = "zh" if bool(re.search(r"[\u4e00-\u9fff]", text)) else "en"
        if tgt == "auto":
            tgt = "en" if src.startswith("zh") else "zh"
        return src, tgt

    @staticmethod
    def _split_sentences(text: str) -> list[str]:
        """Split text into sentences preserving delimiters."""
        return [s.strip() for s in re.split(r"(?<=[.!?。！？])\s+", text) if s.strip()]
