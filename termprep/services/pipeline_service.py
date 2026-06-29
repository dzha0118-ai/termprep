"""Pipeline Service — declarative, plugin-based pipeline execution with templates."""

from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from typing import Any, Callable

from termprep.interfaces.pipeline_step import PipelineStep, StepContext


# ── Pipeline event types ──

EVENT_PIPELINE_START = "pipeline_start"
EVENT_STEP_START = "step_start"
EVENT_STEP_OK = "step_ok"
EVENT_STEP_SKIP = "step_skip"
EVENT_STEP_ERR = "step_err"
EVENT_PIPELINE_DONE = "pipeline_done"
EVENT_PIPELINE_ERR = "pipeline_err"


# ── Built-in pipeline templates ──

PIPELINE_TEMPLATES: dict[str, dict[str, Any]] = {
    "quick-analysis": {
        "name": "quick-analysis",
        "label": "快速分析",
        "description": "仅分析和提取术语，适合快速预览",
        "steps": [
            {"name": "analyze", "type": "builtin", "label": "分析文本"},
            {"name": "extract", "type": "builtin", "label": "提取术语", "config": {"top_n": 20}},
        ],
    },
    "full-pipeline": {
        "name": "full-pipeline",
        "label": "完整流水线",
        "description": "分析→提取→生成术语表→全文翻译→术语库→报告→导出",
        "steps": [
            {"name": "analyze", "type": "builtin", "label": "分析文本"},
            {"name": "extract", "type": "builtin", "label": "提取术语", "config": {"top_n": 30}},
            {"name": "ai_term_translate", "type": "builtin", "label": "翻译术语"},
            {"name": "ai_full_translate", "type": "builtin", "label": "全文翻译"},
            {"name": "translate_analysis", "type": "builtin", "label": "翻译难度分析"},
            {"name": "termbase", "type": "builtin", "label": "建立术语库"},
            {"name": "report", "type": "builtin", "label": "生成报告"},
            {"name": "export", "type": "builtin", "label": "导出"},
        ],
    },
    "agent-pipeline": {
        "name": "agent-pipeline",
        "label": "Agent 增强流水线",
        "description": "使用 AI Agent 进行术语提取和翻译，适合高质量翻译准备",
        "steps": [
            {"name": "analyze", "type": "builtin", "label": "分析文本"},
            {"name": "ai_extract", "type": "builtin", "label": "AI 术语提取"},
            {"name": "ai_glossary", "type": "builtin", "label": "AI 术语翻译"},
            {"name": "ai_translate", "type": "builtin", "label": "AI 全文翻译"},
            {"name": "translate_analysis", "type": "builtin", "label": "翻译难度分析"},
            {"name": "report", "type": "builtin", "label": "生成报告"},
        ],
    },
}


def list_templates() -> list[dict[str, Any]]:
    """Return all available pipeline templates."""
    return [
        {"name": t["name"], "label": t["label"], "description": t["description"], "steps": t["steps"]}
        for t in PIPELINE_TEMPLATES.values()
    ]


def get_template(name: str) -> dict[str, Any] | None:
    """Get a specific template by name."""
    return PIPELINE_TEMPLATES.get(name)


class PipelineService:
    """Orchestrates pre-translation preparation steps with template support.

    Usage:
        service = PipelineService()
        service.register(AnalyzeStep())
        service.register(ExtractStep())
        result = service.run(StepContext(source_text="..."))
    """

    def __init__(self) -> None:
        self._steps: list[PipelineStep] = []
        self._listeners: list[Callable[[str, str, StepContext], None]] = []
        self._pipeline_id: str = ""
        self._checkpoint_dir: str | None = None

    # ── Template-based setup ──

    def load_template(self, template_name: str, **overrides) -> PipelineService:
        """Load steps from a named template, with optional step config overrides."""
        template = get_template(template_name)
        if not template:
            raise ValueError(f"Unknown template: {template_name}. Available: {list(PIPELINE_TEMPLATES.keys())}")
        return self

    # ── Registry ──

    def register(self, step: PipelineStep) -> PipelineService:
        self._steps.append(step)
        return self

    def register_before(self, target_name: str, step: PipelineStep) -> PipelineService:
        for i, s in enumerate(self._steps):
            if s.name == target_name:
                self._steps.insert(i, step)
                return self
        self._steps.append(step)
        return self

    def register_after(self, target_name: str, step: PipelineStep) -> PipelineService:
        for i, s in enumerate(self._steps):
            if s.name == target_name:
                self._steps.insert(i + 1, step)
                return self
        self._steps.append(step)
        return self

    def remove(self, name: str) -> PipelineService:
        self._steps = [s for s in self._steps if s.name != name]
        return self

    @property
    def step_names(self) -> list[str]:
        return [s.name for s in self._steps]

    def steps_snapshot(self) -> list[dict[str, Any]]:
        return [s.to_dict() for s in self._steps]

    def to_dict(self) -> dict[str, Any]:
        return {
            "pipeline_id": self._pipeline_id,
            "steps": self.steps_snapshot(),
            "total_steps": len(self._steps),
        }

    # ── Events ──

    def on_event(self, callback: Callable[[str, str, StepContext], None]) -> PipelineService:
        self._listeners.append(callback)
        return self

    def _notify(self, event_type: str, observation: str, ctx: StepContext) -> None:
        for cb in self._listeners:
            try:
                cb(event_type, observation, ctx)
            except Exception:
                pass

    # ── Checkpoints ──

    def enable_checkpoints(self, directory: str | None = None) -> PipelineService:
        """Enable file-based checkpointing for long pipelines.

        Checkpoints are saved as JSON files. Use a temp dir for HF Space compat.
        """
        if directory:
            os.makedirs(directory, exist_ok=True)
            self._checkpoint_dir = directory
        else:
            self._checkpoint_dir = tempfile.mkdtemp(prefix="termprep_ckpt_")
        return self

    def _save_checkpoint(self, step_index: int, ctx: StepContext) -> None:
        if not self._checkpoint_dir or not self._pipeline_id:
            return
        try:
            path = os.path.join(self._checkpoint_dir, f"{self._pipeline_id}_{step_index}.json")
            data = {
                "pipeline_id": self._pipeline_id,
                "step_index": step_index,
                "step_name": self._steps[step_index].name if step_index < len(self._steps) else "done",
                "timestamp": time.time(),
                "context": {
                    "project_name": ctx.project_name,
                    "analysis": ctx.analysis,
                    "terms_count": len(ctx.terms),
                    "glossary_count": len(ctx.glossary),
                    "has_translation": bool(ctx.full_translation),
                    "report_path": ctx.report_path,
                    "exports": ctx.exports,
                    "errors": ctx.errors,
                },
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            # Clean old checkpoints (keep only the last 3 + current)
            self._clean_old_checkpoints(step_index)
        except Exception:
            pass

    def _clean_old_checkpoints(self, current_index: int) -> None:
        if not self._checkpoint_dir or not self._pipeline_id:
            return
        try:
            prefix = f"{self._pipeline_id}_"
            files = sorted(
                f for f in os.listdir(self._checkpoint_dir) if f.startswith(prefix)
            )
            keep_indices = {current_index, current_index - 1, current_index - 2}
            keep_indices.add(-1)
            for f in files[:-3]:
                try:
                    suffix = f.replace(prefix, "").replace(".json", "")
                    idx = int(suffix) if suffix.lstrip("-").isdigit() else -99
                    if idx not in keep_indices:
                        os.remove(os.path.join(self._checkpoint_dir, f))
                except Exception:
                    pass
        except Exception:
            pass

    def get_checkpoints(self) -> list[dict[str, Any]]:
        """List all checkpoints for the current pipeline."""
        if not self._checkpoint_dir or not self._pipeline_id:
            return []
        ckpts = []
        try:
            prefix = f"{self._pipeline_id}_"
            for f in sorted(os.listdir(self._checkpoint_dir)):
                if f.startswith(prefix) and f.endswith(".json"):
                    path = os.path.join(self._checkpoint_dir, f)
                    with open(path, encoding="utf-8") as fh:
                        ckpts.append(json.load(fh))
        except Exception:
            pass
        return ckpts

    # ── Execution ──

    def run(self, ctx: StepContext) -> StepContext:
        self._pipeline_id = ctx.pipeline_id or str(uuid.uuid4())
        ctx.pipeline_id = self._pipeline_id

        self._notify(EVENT_PIPELINE_START, f"Pipeline {self._pipeline_id[:8]} starting ({len(self._steps)} steps)", ctx)

        start = time.time()
        for i, step in enumerate(self._steps):
            if not step.can_run(ctx):
                obs = f"SKIP: {step.name}{' (' + step.name + ')' if step.name != step.name else ''}"
                self._notify(EVENT_STEP_SKIP, obs, ctx)
                continue

            step_start = time.time()
            step_label = step.name

            self._notify(EVENT_STEP_START, step_label, ctx)

            try:
                ctx = step.run(ctx)
                duration = time.time() - step_start
                obs = f"{step_label} ({duration:.2f}s)"
                self._notify(EVENT_STEP_OK, obs, ctx)
                self._save_checkpoint(i, ctx)
            except Exception as e:
                ctx.errors.append(f"Step '{step.name}' failed: {e}")
                obs = f"ERR: {step_label}: {e}"
                self._notify(EVENT_STEP_ERR, obs, ctx)

        total_duration = time.time() - start
        ctx.metadata["duration"] = total_duration
        ctx.metadata["pipeline_id"] = self._pipeline_id
        ctx.metadata["steps_completed"] = len(self._steps)

        self._save_checkpoint(len(self._steps), ctx)

        if ctx.errors:
            self._notify(EVENT_PIPELINE_DONE, f"Pipeline done with {len(ctx.errors)} error(s) ({total_duration:.2f}s)", ctx)
        else:
            self._notify(EVENT_PIPELINE_DONE, f"Pipeline completed ({total_duration:.2f}s)", ctx)

        return ctx
