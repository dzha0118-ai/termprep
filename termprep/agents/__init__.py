"""TermPrep Agents — specialized agents for multi-agent translation workflows.

This package provides:
  - TermAgent: extracts terminology and produces standardized Glossary JSON
  - TranslationAgent: consumes Glossary and produces structured translations
  - AgentOrchestrator: coordinates agents in pipelines or async tasks

All agent communication happens through strict Pydantic schemas in `schemas.py`,
ensuring decoupling between upstream (Term) and downstream (Translation) agents.
"""

from termprep.agents.schemas import (
    AgentTask,
    Glossary,
    GlossaryMeta,
    LangPair,
    TermConfidence,
    TermEntry,
    TermStatus,
    TranslationRequest,
    TranslationResult,
    TranslationSegment,
)
from termprep.agents.term_agent import TermAgent
from termprep.agents.translation_agent import TranslationAgent
from termprep.agents.orchestrator import AgentOrchestrator

__all__ = [
    "AgentTask",
    "Glossary",
    "GlossaryMeta",
    "LangPair",
    "TermConfidence",
    "TermEntry",
    "TermStatus",
    "TranslationRequest",
    "TranslationResult",
    "TranslationSegment",
    "TermAgent",
    "TranslationAgent",
    "AgentOrchestrator",
]
