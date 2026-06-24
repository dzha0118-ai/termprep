"""Agent schemas — standardized data contracts for multi-agent workflows.

These Pydantic models enforce a strict JSON contract between agents,
ensuring that the Term Agent's output can be consumed by the Translation Agent
(and any downstream QA / Review Agent) without coupling.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class TermStatus(str, Enum):
    """Lifecycle status of a glossary term."""
    DRAFT = "draft"         # extracted but not verified
    CONFIRMED = "confirmed" # approved by human or AI consensus
    DEPRECATED = "deprecated" # obsolete, do not use
    PENDING = "pending"     # under review


class TermConfidence(str, Enum):
    """Qualitative confidence of a term translation."""
    HIGH = "high"      # AI + domain dictionary + high frequency
    MEDIUM = "medium"  # AI only or single source
    LOW = "low"        # fallback / heuristic


class TermEntry(BaseModel):
    """A single bilingual term entry in the glossary."""

    # ── Identity ──
    term_id: str = Field(..., description="Unique identifier (e.g. 't-001')")
    source_term: str = Field(..., description="Original term in source language")
    target_translation: str = Field(default="", description="Translation in target language")

    # ── Classification ──
    term_type: str = Field(default="", description="POS or category: noun, verb, phrase, proper_noun, acronym")
    domain: str = Field(default="general", description="Detected or assigned domain")
    subdomain: str = Field(default="", description="Fine-grained domain tag")

    # ── Quality ──
    confidence: TermConfidence = Field(default=TermConfidence.MEDIUM)
    status: TermStatus = Field(default=TermStatus.DRAFT)
    score: float = Field(default=0.0, ge=0.0, le=1.0, description="Extraction score")

    # ── Context ──
    source_context: str = Field(default="", description="Sentence or snippet containing the term")
    frequency: int = Field(default=1, ge=0, description="Occurrence count in source text")
    positions: list[int] = Field(default_factory=list, description="Line or paragraph indices")

    # ── Metadata ──
    source_engine: str = Field(default="", description="Engine that produced this entry: jieba, yake, cvalue, ai")
    translation_engine: str = Field(default="", description="Engine that produced the translation: ai, youdao, google")
    alternatives: list[str] = Field(default_factory=list, description="Alternative translations")
    notes: str = Field(default="", description="Human or AI notes")
    tags: list[str] = Field(default_factory=list, description="Custom tags")

    # ── Extensibility ──
    meta: dict[str, Any] = Field(default_factory=dict, description="Agent-specific extensibility")


class LangPair(BaseModel):
    """Source / target language pair."""
    source: str = Field(..., description="Source language code: en, zh, ja, ...")
    target: str = Field(..., description="Target language code: en, zh, ja, ...")
    detected: bool = Field(default=True, description="Whether languages were auto-detected")


class GlossaryMeta(BaseModel):
    """Metadata block for a glossary."""
    version: str = Field(default="1.0.0", description="Schema version")
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    project_name: str = Field(default="", description="Project or document name")
    source_file: str = Field(default="", description="Original file path or URL")
    domain: str = Field(default="general")
    difficulty: str = Field(default="medium", description="Translation difficulty: easy, medium, hard")
    lang_pair: LangPair = Field(default_factory=lambda: LangPair(source="auto", target="auto"))
    total_terms: int = Field(default=0, description="Total number of terms in glossary")
    confirmed_terms: int = Field(default=0, description="Number of confirmed terms")
    extractor_engines: list[str] = Field(default_factory=list, description="Engines used for extraction")
    translation_engines: list[str] = Field(default_factory=list, description="Engines used for translation")
    stats: dict[str, Any] = Field(default_factory=dict, description="Arbitrary statistics")


class Glossary(BaseModel):
    """Standardized glossary output from the Term Agent.

    This is the canonical contract consumed by the Translation Agent.
    """
    meta: GlossaryMeta = Field(..., description="Glossary metadata")
    terms: list[TermEntry] = Field(default_factory=list, description="Bilingual term entries")

    # ── Convenience ──

    def to_compact(self) -> list[dict[str, str]]:
        """Return a compact list for prompt injection."""
        return [
            {"term": t.source_term, "translation": t.target_translation}
            for t in self.terms
            if t.target_translation
        ]

    def by_confidence(self, level: TermConfidence) -> list[TermEntry]:
        return [t for t in self.terms if t.confidence == level]

    def by_status(self, status: TermStatus) -> list[TermEntry]:
        return [t for t in self.terms if t.status == status]

    def by_domain(self, domain: str) -> list[TermEntry]:
        return [t for t in self.terms if t.domain == domain]

    def update_meta(self) -> None:
        """Recalculate derived metadata."""
        self.meta.total_terms = len(self.terms)
        self.meta.confirmed_terms = len([t for t in self.terms if t.status == TermStatus.CONFIRMED])

    def model_dump_json(self, **kwargs: Any) -> str:
        """Override to ensure nice formatting."""
        return super().model_dump_json(indent=2, ensure_ascii=False, **kwargs)


class TranslationRequest(BaseModel):
    """Input for the Translation Agent."""
    source_text: str = Field(..., description="Full text to translate")
    glossary: Glossary | None = Field(default=None, description="Optional glossary for term consistency")
    source_lang: str = Field(default="auto")
    target_lang: str = Field(default="auto")
    domain: str = Field(default="general")
    style: str = Field(default="general", description="Translation style: formal, fluent, literal")
    engine: str | None = Field(default=None, description="Specific engine: ai, google, youdao, auto")
    preserve_formatting: bool = Field(default=True, description="Preserve paragraphs, lists, tables")
    instructions: str = Field(default="", description="Additional translator instructions")
    use_rag: bool = Field(default=False, description="Enable RAG retrieval for medical domain context")


class TranslationSegment(BaseModel):
    """A translated segment with alignment info."""
    index: int = Field(..., description="Segment index")
    source: str = Field(..., description="Source segment text")
    target: str = Field(..., description="Translated segment text")
    highlights: list[dict[str, Any]] = Field(default_factory=list, description="Term highlight positions")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class TranslationResult(BaseModel):
    """Output from the Translation Agent."""
    translated_text: str = Field(..., description="Complete translated text")
    source_text: str = Field(default="")
    source_lang: str = Field(default="")
    target_lang: str = Field(default="")
    domain: str = Field(default="general")
    engine: str = Field(default="", description="Engine that produced the translation")
    model: str = Field(default="", description="Model name (for AI engines)")
    segments: list[TranslationSegment] = Field(default_factory=list)
    glossary_used: bool = Field(default=False, description="Whether glossary was injected")
    term_consistency_score: float = Field(default=0.0, description="How well terms were preserved")
    errors: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    rag_metadata: dict[str, Any] = Field(default_factory=dict, description="RAG retrieval metadata")

    def model_dump_json(self, **kwargs: Any) -> str:
        return super().model_dump_json(indent=2, ensure_ascii=False, **kwargs)


class AgentTask(BaseModel):
    """Generic task envelope for inter-agent communication."""
    task_id: str = Field(..., description="UUID for tracing")
    task_type: str = Field(..., description="term_extraction | full_translation | qa_review | ...")
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
