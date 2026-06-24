"""Term Agent — specialized agent for terminology extraction and glossary building.

Responsibilities:
  1. Analyze source text (language, domain, difficulty)
  2. Extract candidate terms using multiple engines
  3. Deduplicate and rank terms
  4. Translate terms using AI (batch mode for efficiency)
  5. Output a standardized Glossary JSON

The output is completely decoupled from the Translation Agent — it only
needs to conform to the Glossary schema.
"""

from __future__ import annotations

import hashlib
import re
import time
import uuid
from typing import Any

from termprep.agents.schemas import (
    Glossary,
    GlossaryMeta,
    LangPair,
    TermConfidence,
    TermEntry,
    TermStatus,
)
from termprep.analyzer import analyze, AnalysisResult
from termprep.extractor import extract, TermEntry as ExtractorTermEntry


class TermAgent:
    """Standalone agent for pre-translation terminology preparation.

    Usage:
        agent = TermAgent()
        glossary = agent.run(
            text="Machine learning has revolutionized NLP...",
            project_name="AI-Paper-2024",
            top_n=30,
        )
        print(glossary.model_dump_json())
    """

    def __init__(self, settings: dict | None = None) -> None:
        self.settings = settings or {}
        self._ai_enabled = bool(self.settings.get("ai_api_key"))

    # ── Public API ──

    def run(
        self,
        text: str,
        project_name: str = "Untitled",
        source_file: str = "",
        top_n: int = 30,
        domain_hint: str = "",
    ) -> Glossary:
        """Run the full term extraction pipeline and return a Glossary."""
        start = time.time()

        # 1. Analyze
        analysis = self._analyze(text)
        detected_domain = domain_hint or analysis.domain

        # 2. Extract terms
        raw_terms = self._extract(text, top_n=top_n)

        # 3. Build entries (deduplicate, enrich)
        entries = self._build_entries(raw_terms, text, analysis)

        # 4. Translate terms (batch AI)
        if self._ai_enabled:
            entries = self._translate_terms_ai(entries, analysis)
        else:
            entries = self._translate_terms_fallback(entries, analysis)

        # 5. Assign confidence & status
        entries = self._assign_quality(entries, analysis)

        # 6. Build glossary
        glossary = Glossary(
            meta=GlossaryMeta(
                project_name=project_name,
                source_file=source_file,
                domain=detected_domain,
                difficulty=analysis.difficulty,
                lang_pair=LangPair(
                    source=analysis.lang,
                    target="en" if analysis.lang in ("zh", "mixed") else "zh",
                    detected=True,
                ),
                extractor_engines=["jieba", "yake", "cvalue", "textrank"],
                translation_engines=["ai"] if self._ai_enabled else ["google", "youdao"],
                stats={
                    "extraction_time": round(time.time() - start, 2),
                    "total_chars": analysis.chars_total,
                    "source_words": analysis.words_cn + analysis.words_en,
                },
            ),
            terms=entries,
        )
        glossary.update_meta()
        return glossary

    def run_from_file(self, filepath: str, **kwargs: Any) -> Glossary:
        """Convenience: read file and run extraction."""
        with open(filepath, "r", encoding="utf-8") as f:
            text = f.read()
        return self.run(text=text, source_file=filepath, **kwargs)

    # ── Internal steps ──

    def _analyze(self, text: str) -> AnalysisResult:
        return analyze(text)

    def _extract(self, text: str, top_n: int) -> list[ExtractorTermEntry]:
        # If AI is available, use AI for higher quality term extraction
        if self._ai_enabled:
            ai_terms = self._extract_with_ai(text, top_n)
            if ai_terms:
                return ai_terms
        return extract(text, top_n=top_n)

    def _extract_with_ai(self, text: str, top_n: int) -> list[ExtractorTermEntry]:
        """Use AI to extract domain-specific terms from the source text."""
        from termprep.engines.ai_translator import AITranslator
        from termprep.extractor import TermEntry as ExtractorTermEntry

        engine = AITranslator(self.settings)
        if not engine.available:
            return []

        prompt = (
            f"Extract up to {top_n} important domain-specific terms from the following text. "
            "Return ONLY a JSON array in this exact format:\n"
            '[{\"term\": \"machine learning\", \"type\": \"technical\"}, ...]\n\n'
            "Rules:\n"
            "1. Each term must be a meaningful, complete phrase (not fragments).\n"
            "2. Prefer noun phrases and technical concepts.\n"
            "3. Do NOT include common verbs like 'is', 'has', 'revolutionized'.\n"
            "4. Do NOT include overlapping or partial phrases.\n"
            "5. Output valid JSON only, no markdown, no explanation.\n\n"
            f"Text:\n{text[:4000]}"
        )
        raw = engine._chat(prompt, system_prompt="You are a professional terminology extraction assistant.")
        if not raw:
            return []

        import json as _json
        terms = []
        try:
            # Strip markdown fences if any
            cleaned = raw.strip()
            cleaned = re.sub(r"^```[a-z]*\n?", "", cleaned)
            cleaned = re.sub(r"\n?```$", "", cleaned)
            cleaned = cleaned.strip()
            arr = _json.loads(cleaned)
            if isinstance(arr, list):
                seen = set()
                for item in arr:
                    term = (item.get("term") or item.get("source_term") or "").strip()
                    if not term:
                        continue
                    key = term.lower()
                    if key in seen:
                        continue
                    seen.add(key)
                    terms.append(
                        ExtractorTermEntry(
                            term=term,
                            score=float(item.get("score", 0.9)),
                            frequency=int(item.get("frequency", 1)),
                            word_type=item.get("type") or "ai",
                            positions=[],
                        )
                    )
        except Exception:
            # Fallback: try line-based parsing
            seen = set()
            for line in raw.splitlines():
                w = line.strip().lstrip("-•0123456789.\"'").strip().strip('"').strip("'")
                if not w or len(w) < 2:
                    continue
                key = w.lower()
                if key in seen:
                    continue
                seen.add(key)
                terms.append(
                    ExtractorTermEntry(
                        term=w,
                        score=0.85,
                        frequency=1,
                        word_type="ai",
                        positions=[],
                    )
                )
        return terms[:top_n]

    def _build_entries(
        self,
        raw_terms: list[ExtractorTermEntry],
        source_text: str,
        analysis: AnalysisResult,
    ) -> list[TermEntry]:
        """Convert extractor output to standardized TermEntry objects."""
        entries: list[TermEntry] = []
        seen: set[str] = set()

        # Common stop words / non-term words
        stop_words = {
            "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
            "have", "has", "had", "do", "does", "did", "will", "would", "could",
            "should", "may", "might", "must", "shall", "can", "need", "dare",
            "ought", "used", "to", "of", "in", "on", "at", "by", "for", "with",
            "about", "against", "between", "into", "through", "during", "before",
            "after", "above", "below", "from", "up", "down", "out", "off", "over",
            "under", "again", "further", "then", "once", "and", "but", "if", "or",
            "because", "as", "until", "while", "although", "though", "since",
            "so", "than", "that", "this", "these", "those", "i", "you", "he", "she",
            "it", "we", "they", "me", "him", "her", "us", "them", "my", "your",
            "his", "its", "our", "their", "what", "which", "who", "when", "where",
            "why", "how", "all", "any", "both", "each", "few", "more", "most",
            "other", "some", "such", "no", "nor", "not", "only", "own", "same",
            "so", "than", "too", "very", "just", "now",
        }

        def _is_valid_term(term: str) -> bool:
            t = term.strip()
            if not t or len(t) < 2:
                return False
            # Reject pure numbers / symbols
            if not re.search(r"[A-Za-z\u4e00-\u9fff]", t):
                return False
            # English: should be 1-5 words and not start/end with stop words
            if re.search(r"[A-Za-z]", t) and not re.search(r"[\u4e00-\u9fff]", t):
                words = [w.lower().strip(",.!?;:\"'") for w in t.split()]
                words = [w for w in words if w]
                if len(words) < 1 or len(words) > 5:
                    return False
                if words[0] in stop_words or words[-1] in stop_words:
                    return False
                # Reject if mostly stop words
                if sum(1 for w in words if w in stop_words) > len(words) / 2:
                    return False
            # Chinese: 2-12 chars
            if re.search(r"[\u4e00-\u9fff]", t):
                cn_chars = re.findall(r"[\u4e00-\u9fff]", t)
                if len(cn_chars) < 2 or len(cn_chars) > 12:
                    return False
            return True

        for i, t in enumerate(raw_terms):
            if not _is_valid_term(t.term):
                continue
            key = t.term.lower().strip()
            if key in seen:
                continue
            seen.add(key)

            # Find context
            ctx = self._find_context(source_text, t.term)

            entries.append(
                TermEntry(
                    term_id=f"t-{i+1:03d}",
                    source_term=t.term,
                    target_translation="",
                    term_type=t.word_type or "unknown",
                    domain=analysis.domain,
                    score=round(t.score, 4),
                    source_context=ctx,
                    frequency=t.frequency,
                    positions=t.positions[:5],
                    source_engine=t.word_type,
                )
            )
        return entries

    def _translate_terms_ai(
        self,
        entries: list[TermEntry],
        analysis: AnalysisResult,
    ) -> list[TermEntry]:
        """Batch-translate terms using AI for maximum efficiency and consistency."""
        from termprep.services.translation_service import TranslationService

        if not entries:
            return entries

        svc = TranslationService(self.settings)
        svc.load_engines()

        source_lang = analysis.lang
        target_lang = "en" if source_lang in ("zh", "mixed") else "zh"
        domain = analysis.domain

        # Batch all terms at once
        term_words = [e.source_term for e in entries]
        results = svc.translate_terms(
            terms=term_words,
            source_lang=source_lang,
            target_lang=target_lang,
            domain=domain,
            engine="ai",
        )

        # Map results back to entries
        for entry, result in zip(entries, results):
            trans = result.get("translation", "")
            # Validate translation is not just echoing source
            if trans and trans.lower().strip() != entry.source_term.lower().strip():
                entry.target_translation = trans
                entry.translation_engine = result.get("engine", "")
                if entry.target_translation:
                    entry.status = TermStatus.CONFIRMED
            else:
                entry.target_translation = ""
                entry.confidence = TermConfidence.LOW
                entry.status = TermStatus.DRAFT

        return entries

    def _translate_terms_fallback(
        self,
        entries: list[TermEntry],
        analysis: AnalysisResult,
    ) -> list[TermEntry]:
        """Translate terms using available non-AI engines (Google / Youdao)."""
        from termprep.services.translation_service import TranslationService

        svc = TranslationService(self.settings)
        svc.load_engines()

        source_lang = analysis.lang
        target_lang = "en" if source_lang in ("zh", "mixed") else "zh"
        domain = analysis.domain

        for entry in entries:
            result = svc.translate(
                text=entry.source_term,
                source_lang=source_lang,
                target_lang=target_lang,
                domain=domain,
            )
            trans = result.text or ""
            if trans and trans.lower().strip() != entry.source_term.lower().strip():
                entry.target_translation = trans
                entry.translation_engine = result.engine
                entry.status = TermStatus.CONFIRMED
            else:
                entry.target_translation = ""
                entry.confidence = TermConfidence.LOW
                entry.status = TermStatus.DRAFT

        return entries

    def _assign_quality(
        self,
        entries: list[TermEntry],
        analysis: AnalysisResult,
    ) -> list[TermEntry]:
        """Assign confidence levels based on multiple signals."""
        for e in entries:
            if not e.target_translation:
                e.confidence = TermConfidence.LOW
                e.status = TermStatus.DRAFT
                continue

            signals = 0
            # Has translation
            signals += 1
            # High extraction score
            if e.score >= 0.7:
                signals += 1
            # High frequency
            if e.frequency >= 3:
                signals += 1
            # AI-translated
            if e.translation_engine == "ai":
                signals += 1
            # Domain match
            if e.domain == analysis.domain and analysis.domain != "general":
                signals += 1

            if signals >= 4:
                e.confidence = TermConfidence.HIGH
            elif signals >= 2:
                e.confidence = TermConfidence.MEDIUM
            else:
                e.confidence = TermConfidence.LOW

        return entries

    @staticmethod
    def _find_context(text: str, term: str, window: int = 50) -> str:
        """Extract a snippet containing the term."""
        idx = text.find(term)
        if idx < 0:
            return ""
        start = max(0, idx - window)
        end = min(len(text), idx + len(term) + window)
        ctx = text[start:end].strip()
        if start > 0:
            ctx = "..." + ctx
        if end < len(text):
            ctx += "..."
        return ctx

    # ── Utility ──

    @staticmethod
    def generate_task_id(text: str) -> str:
        """Generate a deterministic task ID from source text."""
        h = hashlib.md5(text[:500].encode()).hexdigest()[:8]
        return f"term-{h}-{uuid.uuid4().hex[:8]}"
