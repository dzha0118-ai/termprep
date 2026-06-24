"""Agent orchestrator — coordinates TermAgent → TranslationAgent in a multi-agent workflow.

Provides both:
  1. Synchronous pipeline: run all agents in sequence
  2. Asynchronous task-based: enqueue and poll for results
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from termprep.agents.schemas import AgentTask, Glossary, TranslationResult
from termprep.agents.term_agent import TermAgent
from termprep.agents.translation_agent import TranslationAgent
from termprep.config import settings


class AgentOrchestrator:
    """High-level orchestrator for the TermPrep multi-agent workflow.

    Usage — single-step pipeline:
        orch = AgentOrchestrator()
        result = orch.run_full_pipeline(
            text="Machine learning has revolutionized NLP...",
            project_name="AI-Demo",
        )
        # result contains: glossary (JSON) + translation (JSON)

    Usage — two-step (decoupled):
        # Step 1: Term Agent
        glossary = orch.run_term_agent(text=..., project_name=...)
        # (glossary is saved / sent to another service)

        # Step 2: Translation Agent (later, possibly on another machine)
        translation = orch.run_translation_agent(
            text=...,
            glossary=glossary,
        )
    """

    def __init__(self, settings_dict: dict | None = None) -> None:
        self.settings = settings_dict or settings.to_dict()
        self._term_agent = TermAgent(self.settings)
        self._translation_agent = TranslationAgent(self.settings)
        # Simple in-memory task store (replace with Redis / DB in production)
        self._tasks: dict[str, dict[str, Any]] = {}

    # ── High-level pipeline ──

    def run_full_pipeline(
        self,
        text: str,
        project_name: str = "Untitled",
        source_file: str = "",
        top_n: int = 30,
        domain_hint: str = "",
        translation_engine: str | None = None,
        translation_style: str = "general",
        use_rag: bool = False,
    ) -> dict[str, Any]:
        """Run TermAgent → TranslationAgent in sequence and return combined result."""
        task_id = str(uuid.uuid4())
        start = time.time()

        # Step 1: Term Agent
        glossary = self._term_agent.run(
            text=text,
            project_name=project_name,
            source_file=source_file,
            top_n=top_n,
            domain_hint=domain_hint,
        )

        # Step 2: Translation Agent
        translation = self._translation_agent.translate(
            source_text=text,
            glossary=glossary,
            domain=glossary.meta.domain,
            engine=translation_engine,
            style=translation_style,
            use_rag=use_rag,
        )

        return {
            "task_id": task_id,
            "duration": round(time.time() - start, 2),
            "glossary": glossary.model_dump(),
            "translation": translation.model_dump(),
            "term_consistency": translation.term_consistency_score,
        }

    def run_term_agent(
        self,
        text: str,
        project_name: str = "Untitled",
        source_file: str = "",
        top_n: int = 30,
        domain_hint: str = "",
    ) -> Glossary:
        """Run only the Term Agent. Returns standardized Glossary."""
        return self._term_agent.run(
            text=text,
            project_name=project_name,
            source_file=source_file,
            top_n=top_n,
            domain_hint=domain_hint,
        )

    def run_translation_agent(
        self,
        text: str,
        glossary: Glossary | None = None,
        domain: str = "general",
        engine: str | None = None,
        style: str = "general",
        use_rag: bool = False,
    ) -> TranslationResult:
        """Run only the Translation Agent. Consumes Glossary."""
        return self._translation_agent.translate(
            source_text=text,
            glossary=glossary,
            domain=domain,
            engine=engine,
            style=style,
            use_rag=use_rag,
        )

    # ── Async task API (for production / distributed) ──

    def submit_task(self, task_type: str, payload: dict[str, Any]) -> str:
        """Submit a task and return a task_id for polling."""
        task_id = str(uuid.uuid4())
        self._tasks[task_id] = {
            "task_id": task_id,
            "task_type": task_type,
            "status": "pending",
            "payload": payload,
            "result": None,
            "errors": [],
            "created_at": time.time(),
        }
        return task_id

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        """Get current task status and result."""
        return self._tasks.get(task_id)

    def execute_task(self, task_id: str) -> dict[str, Any]:
        """Execute a pending task synchronously."""
        task = self._tasks.get(task_id)
        if not task:
            return {"error": "Task not found"}

        task["status"] = "running"
        try:
            if task["task_type"] == "term_extraction":
                result = self._run_term_task(task["payload"])
            elif task["task_type"] == "full_translation":
                result = self._run_translation_task(task["payload"])
            elif task["task_type"] == "full_pipeline":
                result = self._run_full_pipeline_task(task["payload"])
            else:
                raise ValueError(f"Unknown task type: {task['task_type']}")

            task["result"] = result
            task["status"] = "completed"
        except Exception as e:
            task["status"] = "failed"
            task["errors"].append(str(e))

        return task

    # ── Internal task handlers ──

    def _run_term_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        glossary = self._term_agent.run(
            text=payload["text"],
            project_name=payload.get("project_name", "Untitled"),
            source_file=payload.get("source_file", ""),
            top_n=payload.get("top_n", 30),
            domain_hint=payload.get("domain", ""),
        )
        return {"glossary": glossary.model_dump()}

    def _run_translation_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        # Reconstruct glossary from payload if provided
        glossary = None
        if "glossary" in payload:
            glossary = Glossary.model_validate(payload["glossary"])

        translation = self._translation_agent.translate(
            source_text=payload["text"],
            glossary=glossary,
            domain=payload.get("domain", "general"),
            engine=payload.get("engine"),
            style=payload.get("style", "general"),
            use_rag=payload.get("use_rag", False),
        )
        return {"translation": translation.model_dump()}

    def _run_full_pipeline_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.run_full_pipeline(
            text=payload["text"],
            project_name=payload.get("project_name", "Untitled"),
            source_file=payload.get("source_file", ""),
            top_n=payload.get("top_n", 30),
            domain_hint=payload.get("domain", ""),
            translation_engine=payload.get("engine"),
            translation_style=payload.get("style", "general"),
            use_rag=payload.get("use_rag", False),
        )
