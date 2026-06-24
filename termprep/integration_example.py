"""Integration example: how to use the new architecture in CLI / Web / Pipeline.

This file demonstrates how to gradually migrate from the old tight-coupling style
to the new service-based, engine-agnostic architecture.
"""

from termprep.config import settings
from termprep.services.translation_service import TranslationService
from termprep.services.pipeline_service import PipelineService
from termprep.interfaces.pipeline_step import PipelineStep, StepContext

# ── Example 1: Basic AI translation ──


def example_ai_translate():
    """Use AI engine directly for full-text translation."""
    svc = TranslationService(settings.to_dict())
    svc.load_engines()

    result = svc.translate(
        text="Machine learning is a subset of artificial intelligence.",
        source_lang="en",
        target_lang="zh",
        domain="it",
        engine="ai",  # explicitly use AI; omit for auto-fallback
    )

    print("Engine:", result.engine)
    print("Translation:", result.text)
    print("Errors:", result.errors)


# ── Example 2: Batch term translation (glossary building) ──


def example_batch_term_translate():
    """Use AI to translate a list of terms efficiently."""
    svc = TranslationService(settings.to_dict())
    svc.load_engines()

    terms = ["machine learning", "neural network", "deep learning", "natural language processing"]
    results = svc.translate_terms(terms, source_lang="en", target_lang="zh", domain="it")

    for r in results:
        print(f"  {r['term']} → {r['translation']} (confidence: {r['confidence']})")


# ── Example 3: Declarative pipeline with AI steps ──


class AnalyzeStep(PipelineStep):
    name = "analyze"

    def run(self, ctx: StepContext) -> StepContext:
        from termprep.analyzer import analyze
        result = analyze(ctx.source_text)
        ctx.analysis = {
            "lang": result.lang,
            "domain": result.domain,
            "difficulty": result.difficulty,
        }
        return ctx


class ExtractStep(PipelineStep):
    name = "extract"

    def run(self, ctx: StepContext) -> StepContext:
        from termprep.extractor import extract
        terms = extract(ctx.source_text, top_n=20)
        ctx.terms = [{"term": t.term, "score": t.score, "type": t.word_type} for t in terms]
        return ctx


class AITermTranslateStep(PipelineStep):
    """NEW: Use AI to translate extracted terms into a bilingual glossary."""
    name = "ai_term_translate"

    def __init__(self) -> None:
        self.svc = TranslationService(settings.to_dict())
        self.svc.load_engines()

    def run(self, ctx: StepContext) -> StepContext:
        if not ctx.terms:
            return ctx
        term_words = [t["term"] for t in ctx.terms]
        glossary = self.svc.translate_terms(
            term_words,
            source_lang=ctx.analysis.get("lang", "auto"),
            domain=ctx.analysis.get("domain", "general"),
            engine="ai",
        )
        ctx.glossary = glossary
        return ctx


class AIFullTranslateStep(PipelineStep):
    """NEW: Use AI for full-text translation with glossary injection."""
    name = "ai_full_translate"

    def __init__(self) -> None:
        self.svc = TranslationService(settings.to_dict())
        self.svc.load_engines()

    def run(self, ctx: StepContext) -> StepContext:
        if not ctx.source_text:
            return ctx
        result = self.svc.translate(
            text=ctx.source_text,
            source_lang=ctx.analysis.get("lang", "auto"),
            domain=ctx.analysis.get("domain", "general"),
            engine="ai",
            glossary=ctx.glossary,
        )
        ctx.full_translation = result.text
        ctx.metadata["translation_engine"] = result.engine
        return ctx


class ReportStep(PipelineStep):
    name = "report"

    def run(self, ctx: StepContext) -> StepContext:
        from termprep.report import generate_report, save_report
        from termprep.analyzer import AnalysisResult
        from termprep.extractor import TermEntry

        # Reconstruct legacy objects for compatibility
        analysis = AnalysisResult(
            lang=ctx.analysis.get("lang", ""),
            domain=ctx.analysis.get("domain", ""),
            difficulty=ctx.analysis.get("difficulty", "medium"),
        )
        terms = [TermEntry(term=t["term"], score=t.get("score", 0.0), word_type=t.get("type", "")) for t in ctx.terms]

        report = generate_report(
            analysis=analysis,
            terms=terms,
            glossary=ctx.glossary,
            full_translation=ctx.full_translation,
            project_name=ctx.project_name,
            source_file=ctx.source_file,
        )
        path = f"{ctx.project_name}-report.md"
        save_report(report, path)
        ctx.report_path = path
        return ctx


def example_pipeline():
    """Build and run a fully declarative AI-enhanced pipeline."""
    service = PipelineService()
    service.register(AnalyzeStep())
    service.register(ExtractStep())
    service.register(AITermTranslateStep())
    service.register(AIFullTranslateStep())
    service.register(ReportStep())

    # Optional: add event listener for progress
    def on_event(obs: str, ctx: StepContext):
        print(f"[pipeline] {obs}")

    service.on_event(on_event)

    ctx = StepContext(
        project_name="AI-Demo",
        source_text="Deep learning has revolutionized natural language processing. "
                    "Neural networks now power machine translation systems.",
    )
    result = service.run(ctx)

    print("\n=== Pipeline Result ===")
    print(f"Duration: {result.metadata.get('duration', 0):.2f}s")
    print(f"Terms: {len(result.terms)}")
    print(f"Glossary: {len(result.glossary)}")
    print(f"Translation length: {len(result.full_translation)}")
    print(f"Report: {result.report_path}")
    if result.errors:
        print("Errors:", result.errors)


# ── Example 4: FastAPI route using new service (drop-in replacement) ──


def example_fastapi_route():
    """Pseudo-code for how web/server.py would use the new service."""
    from fastapi import FastAPI
    from pydantic import BaseModel

    app = FastAPI()
    svc = TranslationService(settings.to_dict())
    svc.load_engines()

    class TranslateIn(BaseModel):
        text: str
        domain: str = "general"
        engine: str | None = None  # "ai", "google", "youdao", or None for auto

    @app.post("/api/translate/v2")
    def api_translate_v2(data: TranslateIn):
        result = svc.translate(
            text=data.text,
            domain=data.domain,
            engine=data.engine,
        )
        return {
            "translated": result.text,
            "engine": result.engine,
            "confidence": result.confidence,
            "errors": result.errors,
        }

    # New route: batch term translation
    @app.post("/api/translate/terms")
    def api_translate_terms(terms: list[str], domain: str = "general"):
        results = svc.translate_terms(terms, domain=domain, engine="ai")
        return {"results": results}


if __name__ == "__main__":
    example_pipeline()
