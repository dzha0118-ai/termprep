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
    metadata: dict[str, Any] = field(default_factory=dict)  # engine-specific data


class PipelineStep(ABC):
    """A single step in the pre-translation pipeline."""

    name: str = "step"

    @abstractmethod
    def run(self, ctx: StepContext) -> StepContext:
        """Execute the step and return the (possibly mutated) context.

        Steps should be idempotent where possible: running twice with the
        same input should produce the same output.
        """
        ...

    def can_run(self, ctx: StepContext) -> bool:
        """Return True if this step should execute given the current context.

        Override to implement conditional steps (e.g. skip AI translation
        when source text is empty).
        """
        return True
