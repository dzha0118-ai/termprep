"""Abstract pipeline step interface."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class StepContext:
    """Shared context passed through pipeline steps."""
    project_name: str = ""
    source_text: str = ""
    source_file: str = ""
    analysis: dict[str, Any] = field(default_factory=dict)
    terms: list[dict] = field(default_factory=list)
    glossary: list[dict] = field(default_factory=list)
    full_translation: str = ""
    termbase_name: str = ""
    termbase_terms: int = 0
    report_path: str = ""
    exports: dict[str, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    # Translation difficulty analysis
    translation_analysis: dict[str, Any] = field(default_factory=dict)

    # Pipeline orchestration
    pipeline_id: str = ""
    pipeline_template: str = ""
    pipeline_steps: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_name": self.project_name,
            "source_text": self.source_text[:200] if self.source_text else "",
            "source_file": self.source_file,
            "analysis": self.analysis,
            "terms_count": len(self.terms),
            "glossary_count": len(self.glossary),
            "has_translation": bool(self.full_translation),
            "report_path": self.report_path,
            "exports": self.exports,
            "errors": self.errors,
            "translation_analysis": self.translation_analysis,
            "pipeline_id": self.pipeline_id,
            "pipeline_template": self.pipeline_template,
        }


class PipelineStep(ABC):
    """A single step in the pre-translation pipeline."""

    name: str = "step"

    @abstractmethod
    def run(self, ctx: StepContext) -> StepContext:
        ...

    def can_run(self, ctx: StepContext) -> bool:
        return True

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name}
