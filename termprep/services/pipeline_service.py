"""Pipeline Service — declarative, plugin-based pipeline execution."""

from __future__ import annotations

import time
from typing import Any

from termprep.interfaces.pipeline_step import PipelineStep, StepContext


class PipelineService:
    """Orchestrates pre-translation preparation steps.

    Steps are registered by name and executed in order. Each step receives
    a shared StepContext and may mutate it. Steps can be skipped conditionally.

    Usage:
        service = PipelineService()
        service.register(AnalyzeStep())
        service.register(ExtractStep())
        service.register(AITermTranslateStep())
        service.register(AIFullTranslateStep())
        service.register(TermbaseStep())
        service.register(ReportStep())
        result = service.run(StepContext(source_text="..."))
    """

    def __init__(self) -> None:
        self._steps: list[PipelineStep] = []
        self._listeners: list[Any] = []  # callback(obs: str, ctx: StepContext)

    def register(self, step: PipelineStep) -> PipelineService:
        """Add a step to the pipeline. Chainable."""
        self._steps.append(step)
        return self

    def register_before(self, target_name: str, step: PipelineStep) -> PipelineService:
        """Insert a step before the named step."""
        for i, s in enumerate(self._steps):
            if s.name == target_name:
                self._steps.insert(i, step)
                return self
        self._steps.append(step)
        return self

    def register_after(self, target_name: str, step: PipelineStep) -> PipelineService:
        """Insert a step after the named step."""
        for i, s in enumerate(self._steps):
            if s.name == target_name:
                self._steps.insert(i + 1, step)
                return self
        self._steps.append(step)
        return self

    def remove(self, name: str) -> PipelineService:
        """Remove a step by name."""
        self._steps = [s for s in self._steps if s.name != name]
        return self

    def run(self, ctx: StepContext) -> StepContext:
        """Execute all steps in order."""
        start = time.time()
        for step in self._steps:
            if not step.can_run(ctx):
                self._notify(f"SKIP: {step.name}", ctx)
                continue
            step_start = time.time()
            try:
                ctx = step.run(ctx)
                self._notify(f"OK: {step.name} ({time.time() - step_start:.2f}s)", ctx)
            except Exception as e:
                ctx.errors.append(f"Step '{step.name}' failed: {e}")
                self._notify(f"ERR: {step.name}: {e}", ctx)
        ctx.metadata["duration"] = time.time() - start
        return ctx

    def on_event(self, callback: Any) -> PipelineService:
        """Register an event listener for step notifications."""
        self._listeners.append(callback)
        return self

    def _notify(self, observation: str, ctx: StepContext) -> None:
        for cb in self._listeners:
            try:
                cb(observation, ctx)
            except Exception:
                pass

    @property
    def step_names(self) -> list[str]:
        return [s.name for s in self._steps]
