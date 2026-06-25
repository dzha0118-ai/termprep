"""TermPrep Web Server — FastAPI backend."""

import os
import sys
import json
import uvicorn
from fastapi import FastAPI, HTTPException, Query, UploadFile, File, Form, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Any, Optional

# Ensure termprep root is on path
_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _root not in sys.path:
    sys.path.insert(0, _root)

from termprep.analyzer import analyze
from termprep.extractor import extract
from termprep.searcher import search_term
from termprep.db import TermDB, list_termbases, init_termbase
from termprep.sources import get_available_sources
from termprep.pipeline import run_pipeline
from termprep.associator import find_related
from termprep.translator import translate_text, parse_file
from termprep.tm import store as tm_store, search as tm_search, stats as tm_stats
from termprep.termbase import lookup_term, validate_terms

PROJ_ROOT = _root
STATIC_DIR = os.path.join(PROJ_ROOT, "web", "static")


# ── Agent imports (new architecture) ──
from termprep.agents.orchestrator import AgentOrchestrator
from termprep.agents.schemas import TranslationRequest as AgentTranslationRequest
from termprep.services.translation_service import TranslationService
from termprep.config import settings as termprep_settings
import termprep.engines  # trigger engine registration (ai, google, youdao)


# ── Client-supplied AI config helper ──

def _ai_settings_from_request(request: Request) -> dict[str, Any]:
    """Override server AI settings with headers sent from the browser config panel.

    Headers:
        X-AI-Provider: kimi | openai | ollama | custom
        X-AI-Api-Key:  your key
        X-AI-Model:    e.g. moonshot-v1-128k
        X-AI-Base-Url: e.g. https://api.moonshot.cn/v1
    """
    headers = request.headers
    provider = headers.get("x-ai-provider") or headers.get("X-AI-Provider")
    api_key = headers.get("x-ai-api-key") or headers.get("X-AI-Api-Key")
    model = headers.get("x-ai-model") or headers.get("X-AI-Model")
    base_url = headers.get("x-ai-base-url") or headers.get("X-AI-Base-Url")

    settings_dict = termprep_settings.to_dict()
    if provider:
        settings_dict["ai_provider"] = provider
    if api_key:
        settings_dict["ai_api_key"] = api_key
    if model:
        settings_dict["ai_model"] = model
    if base_url:
        settings_dict["ai_base_url"] = base_url
    return settings_dict


# ── Pydantic models ──────────────────────────────────────────────

class AnalyzeIn(BaseModel):
    text: str

class ExtractIn(BaseModel):
    text: str
    top_n: int = 30

class SearchIn(BaseModel):
    term: str
    limit: int = 10

class TermAddIn(BaseModel):
    word: str
    translation: str = ""
    type_: str = ""
    domain: str = ""
    status: str = "draft"
    db_name: Optional[str] = None

class TermSearchIn(BaseModel):
    query: str
    db_name: Optional[str] = None

class PipelineIn(BaseModel):
    text: str
    project_name: str = "Untitled"
    top_n: int = 20
    search_limit: int = 5
    db_name: Optional[str] = None
    use_ai: bool = False
    ai_engine: Optional[str] = None
    translate_mode: str = "auto"  # auto | mixed | zh2en | en2zh
    use_rag: bool = False

class AssociateIn(BaseModel):
    term: str
    limit: int = 15
    wiki_text: Optional[str] = None  # Wikipedia summary text from client-side fetch

class TranslateIn(BaseModel):
    text: str = ""
    domain: str = "general"
    source_lang: str = "auto"
    target_lang: str = "auto"
    use_rag: bool = False


# ── Agent Pydantic models ──────────────────────────────────────

class AgentTermIn(BaseModel):
    text: str
    project_name: str = "Untitled"
    source_file: str = ""
    top_n: int = 30
    domain_hint: str = ""


class AgentTranslateIn(BaseModel):
    text: str
    glossary: dict | None = None
    domain: str = "general"
    source_lang: str = "auto"
    target_lang: str = "auto"
    engine: str | None = None
    style: str = "general"
    use_rag: bool = False


class AgentPipelineIn(BaseModel):
    text: str
    project_name: str = "Untitled"
    source_file: str = ""
    top_n: int = 30
    domain_hint: str = ""
    translation_engine: str | None = None
    translation_style: str = "general"
    use_rag: bool = False


class AgentTaskSubmitIn(BaseModel):
    task_type: str  # term_extraction | full_translation | full_pipeline
    payload: dict


class AgentTaskStatusIn(BaseModel):
    task_id: str


# ── App factory ──────────────────────────────────────────────────

def create_app() -> FastAPI:
    app = FastAPI(title="TermPrep", version="0.5")

    # RAG seed data initialization (lazy, no-op if ChromaDB unavailable)
    @app.on_event("startup")
    async def _init_rag_seed():
        try:
            from termprep.rag.seed_initializer import init_rag_seed_data
            result = init_rag_seed_data()
            if result.get("status") == "seeded":
                import logging
                logging.getLogger("termprep").info("RAG seeded: %s", result)
        except Exception:
            pass  # RAG dependencies may not be installed

    # CORS — allow HF Space, GitHub Pages and local dev
    # In production (HF Space), allow all origins since the Space URL is dynamic
    _origins = [
        "https://dzha0118-ai.github.io",
        "https://dzha0118-termprep.hf.space",
        "http://127.0.0.1:8672",
        "http://localhost:8672",
    ]
    # If running on HF Space, allow all origins for iframe embedding
    if os.environ.get("SPACE_ID") or os.environ.get("HF_SPACE"):
        _origins = ["*"]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_origins,
        allow_credentials=True if _origins != ["*"] else False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── API Routes ──

    @app.get("/api/sources")
    def api_sources():
        srcs = get_available_sources()
        return {"sources": [
            {"name": s.name, "available": s.available}
            for s in srcs
        ]}

    @app.post("/api/analyze")
    def api_analyze(data: AnalyzeIn):
        if not data.text.strip():
            raise HTTPException(400, "No input text")
        result = analyze(data.text)
        return {
            "lang": result.lang,
            "char_count": result.chars_total,
            "word_count": result.words_en + result.words_cn,
            "domain": result.domain,
            "difficulty": result.difficulty,
            "summary": str(result),
        }

    @app.post("/api/extract")
    def api_extract(data: ExtractIn, request: Request):
        if not data.text.strip():
            raise HTTPException(400, "No input text")
        # Try AI-enhanced extraction if client provided AI config
        client_settings = _ai_settings_from_request(request)
        if client_settings.get("ai_api_key"):
            try:
                orch = _get_orchestrator(request)
                glossary = orch.run_term_agent(text=data.text, project_name="browser", top_n=data.top_n)
                return {"terms": [
                    {"word": t.source_term, "freq": t.frequency, "type": t.term_type or "ai",
                     "score": round(t.score, 3), "pos": "", "translation": t.target_translation or ""}
                    for t in glossary.terms
                ], "ai_used": True}
            except Exception:
                pass
        terms = extract(data.text, top_n=data.top_n)
        return {"terms": [
            {"word": t.term, "freq": t.frequency, "type": t.word_type or "word",
             "score": round(t.score, 3), "pos": ""}
            for t in terms
        ]}

    @app.post("/api/search")
    def api_search(data: SearchIn, request: Request):
        if not data.term.strip():
            raise HTTPException(400, "No search term")
        # Try AI term lookup first if client config provided
        client_settings = _ai_settings_from_request(request)
        if client_settings.get("ai_api_key"):
            try:
                from termprep.services.translation_service import TranslationService
                svc = TranslationService(client_settings)
                svc.load_engines()
                ai_result = svc.translate(data.term, source_lang="auto", target_lang="auto", engine="ai")
                if ai_result.text and not ai_result.text.startswith("["):
                    return {"results": [{
                        "query": data.term,
                        "word": ai_result.text,
                        "type": "translation",
                        "source": f"ai-{client_settings.get('ai_provider','deepseek')}",
                        "score": 0.95,
                        "definition": ai_result.text,
                    }], "term": data.term, "ai_used": True}
            except Exception:
                pass
        results = search_term(data.term, source="web", limit=data.limit)
        return {"results": results, "term": data.term}

    @app.post("/api/associate")
    def api_associate(data: AssociateIn, request: Request):
        if not data.term.strip():
            raise HTTPException(400, "No term")
        # Try AI-powered related terms if client config provided
        client_settings = _ai_settings_from_request(request)
        if client_settings.get("ai_api_key"):
            try:
                from termprep.engines.ai_translator import AITranslator
                engine = AITranslator(client_settings)
                prompt = f"List {data.limit} related terms for '{data.term}' in the same domain, with their English or Chinese translations. Return JSON: [{{'word': '...', 'translation': '...'}}]. Only return valid JSON."
                raw = engine._chat(prompt, system_prompt="You are a terminology assistant. Always return valid JSON arrays.")
                if raw:
                    related = []
                    # Try parse JSON response
                    try:
                        import json as _json
                        arr = _json.loads(raw.strip().lstrip('```json').lstrip('```').strip().strip('`'))
                        if isinstance(arr, list):
                            for item in arr:
                                w = (item.get('word') or item.get('term') or '').strip()
                                t = (item.get('translation') or item.get('trans') or '').strip()
                                if w and len(w) < 80:
                                    related.append({"word": w, "translation": t, "relation": "related", "source": "ai", "score": 0.9})
                    except Exception:
                        # Fallback to line parsing
                        for line in raw.splitlines():
                            w = line.strip().lstrip("-•0123456789.").strip()
                            if w and len(w) < 80:
                                related.append({"word": w, "translation": "", "relation": "related", "source": "ai", "score": 0.9})
                    if related:
                        return {"term": data.term, "related": related[:data.limit], "ai_used": True}
            except Exception:
                pass
        related = find_related(data.term, limit=data.limit, wiki_text=data.wiki_text)
        return {"term": data.term, "related": related}

    @app.post("/api/translate")
    def api_translate(data: TranslateIn, request: Request):
        if not data.text.strip():
            raise HTTPException(400, "No input text")
        try:
            client_settings = _ai_settings_from_request(request)
            if client_settings.get("ai_api_key"):
                # AI-enhanced translation with glossary
                try:
                    orch = _get_orchestrator(request)
                    glossary = orch.run_term_agent(text=data.text, project_name="browser", top_n=20)
                    trans = orch.run_translation_agent(
                        text=data.text,
                        glossary=glossary,
                        domain=data.domain,
                        source_lang=data.source_lang,
                        target_lang=data.target_lang,
                        engine="ai",
                        use_rag=data.use_rag,
                    )
                    return {
                        "translated": trans.translated_text,
                        "source_lang": trans.source_lang,
                        "target_lang": trans.target_lang,
                        "domain": data.domain,
                        "style_used": "AI 风格化翻译",
                        "tm_matches": [],
                        "segments": [
                            {"index": s.index, "source": s.source, "target": s.target, "highlights": s.highlights}
                            for s in trans.segments
                        ],
                        "errors": trans.errors,
                    }
                except Exception:
                    pass
            result = translate_text(
                text=data.text,
                source_lang=data.source_lang,
                target_lang=data.target_lang,
                domain=data.domain,
            )
            # Auto-store to translation memory
            if result.translated_text and not result.translated_text.startswith("["):
                tm_store(data.text, result.translated_text,
                         result.source_lang, result.target_lang, result.domain)

            return {
                "translated": result.translated_text,
                "source_lang": result.source_lang,
                "target_lang": result.target_lang,
                "domain": result.domain,
                "style_used": result.style_used,
                "tm_matches": tm_search(data.text, result.source_lang, result.target_lang, result.domain, limit=3),
                "segments": [
                    {
                        "index": s.index,
                        "source": s.source,
                        "target": s.target,
                        "highlights": s.highlights,
                    }
                    for s in result.segments
                ],
                "errors": result.errors,
            }
        except Exception as e:
            raise HTTPException(500, str(e))

    @app.post("/api/translate/upload")
    async def api_translate_upload(request: Request):
        import tempfile
        import traceback as _tb
        try:
            # 手动解析 multipart 表单，绕过 FastAPI UploadFile 参数解析问题
            form = await request.form()
            file = form.get("file")
            if not file:
                return JSONResponse(status_code=400, content={"error": "缺少 file 字段"})

            domain = str(form.get("domain", "general"))
            target_lang = str(form.get("target_lang", "auto"))
            use_rag_str = str(form.get("use_rag", "false")).lower()
            use_rag = use_rag_str in ("true", "1", "yes", "on")

            suffix = os.path.splitext(getattr(file, 'filename', 'file.txt') or ".txt")[1]
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                content = await file.read()
                tmp.write(content)
                tmp_path = tmp.name

            text = parse_file(tmp_path)
            os.unlink(tmp_path)

            if not text.strip():
                return JSONResponse(status_code=400, content={"error": "文件内容为空"})

            # Use AI if client config is provided
            client_settings = _ai_settings_from_request(request)
            ai_api_key = client_settings.get("ai_api_key")
            
            if ai_api_key:
                try:
                    from termprep.agents.orchestrator import AgentOrchestrator
                    from termprep.agents.schemas import Glossary
                    orch = AgentOrchestrator(client_settings)
                    glossary = orch.run_term_agent(text=text, project_name="upload", top_n=20)
                    trans = orch.run_translation_agent(
                        text=text,
                        glossary=glossary,
                        domain=domain,
                        engine="ai",
                        use_rag=use_rag,
                    )
                    return {
                        "filename": getattr(file, 'filename', 'unknown'),
                        "translated": trans.translated_text,
                        "source_lang": trans.source_lang,
                        "target_lang": trans.target_lang,
                        "domain": domain,
                        "style_used": "AI 风格化翻译",
                        "glossary": glossary.model_dump(),
                        "segments": [
                            {"index": s.index, "source": s.source, "target": s.target, "highlights": s.highlights}
                            for s in trans.segments
                        ],
                        "errors": trans.errors,
                    }
                except Exception as ai_err:
                    error_detail = f"AI 翻译失败: {type(ai_err).__name__}: {ai_err}"
                    result = translate_text(
                        text=text,
                        domain=domain,
                        target_lang=target_lang,
                    )
                    return {
                        "filename": getattr(file, 'filename', 'unknown'),
                        "translated": result.translated_text,
                        "source_lang": result.source_lang,
                        "target_lang": result.target_lang,
                        "domain": result.domain,
                        "style_used": result.style_used + " (AI 降级)",
                        "segments": [
                            {"index": s.index, "source": s.source, "target": s.target, "highlights": s.highlights}
                            for s in result.segments
                        ],
                        "errors": result.errors + [error_detail],
                    }

            # 免费引擎路径
            result = translate_text(
                text=text,
                domain=domain,
                target_lang=target_lang,
            )
            return {
                "filename": getattr(file, 'filename', 'unknown'),
                "translated": result.translated_text,
                "source_lang": result.source_lang,
                "target_lang": result.target_lang,
                "domain": result.domain,
                "style_used": result.style_used,
                "segments": [
                    {"index": s.index, "source": s.source, "target": s.target, "highlights": s.highlights}
                    for s in result.segments
                ],
                "errors": result.errors,
            }
        except Exception as e:
            tb_str = _tb.format_exc()
            return JSONResponse(status_code=200, content={
                "error": f"服务器内部错误: {type(e).__name__}: {e}",
                "traceback": tb_str,
                "error_type": type(e).__name__,
                "status": 500,
            })

    @app.post("/api/termbase/lookup")
    def api_termbase_lookup(data: SearchIn):
        if not data.term.strip():
            raise HTTPException(400, "No term")
        result = lookup_term(data.term)
        return result

    @app.post("/api/termbase/validate")
    def api_termbase_validate(terms: list[str] = Query(...), domain: str = Query("general")):
        results = validate_terms(terms, domain)
        return {"results": results}

    @app.get("/api/tm/stats")
    def api_tm_stats():
        return tm_stats()

    @app.post("/api/tm/search")
    def api_tm_search(data: SearchIn):
        if not data.term.strip():
            raise HTTPException(400, "No search term")
        results = tm_search(data.term, limit=data.limit)
        return {"matches": results}

    @app.post("/api/term/add")
    def api_term_add(data: TermAddIn):
        tdb = TermDB(db_name=data.db_name) if data.db_name else TermDB()
        tid = tdb.add_term(
            data.word, translation=data.translation,
            type_=data.type_, domain=data.domain, status=data.status,
        )
        return {"id": tid}

    @app.post("/api/term/search")
    def api_term_search(data: TermSearchIn):
        tdb = TermDB(db_name=data.db_name) if data.db_name else TermDB()
        results = tdb.search_terms(data.query)
        return {"results": results}

    @app.get("/api/db/list")
    def api_db_list():
        dbs = list_termbases()
        return {"databases": [
            {"name": d.name, "domain": d.domain or "",
             "lang": d.lang or "", "total_terms": d.total_terms}
            for d in dbs
        ]}

    @app.get("/api/db/info")
    def api_db_info(db: str = Query("terms")):
        tdb = TermDB(db_name=db)
        stats = tdb.get_stats()
        return stats

    @app.post("/api/db/init")
    def api_db_init(name: str = Query(...), domain: str = Query("")):
        try:
            tdb = init_termbase(name, domain=domain)
            return {"path": tdb.db_path}
        except FileExistsError as e:
            raise HTTPException(409, str(e))

    @app.post("/api/pipeline")
    def api_pipeline(data: PipelineIn, request: Request):
        if not data.text.strip():
            raise HTTPException(400, "No input text")
        try:
            # Merge client AI settings if provided
            client_settings = _ai_settings_from_request(request)
            result = run_pipeline(
                text=data.text,
                project_name=data.project_name,
                top_n=data.top_n,
                search_limit=data.search_limit,
                db_name=data.db_name,
                use_ai=data.use_ai,
                ai_engine=data.ai_engine,
                translate_mode=data.translate_mode,
                use_rag=data.use_rag,
                settings=client_settings,
            )
            # Read report content
            report_text = ""
            if result.report_path and os.path.isfile(result.report_path):
                try:
                    with open(result.report_path, encoding="utf-8") as f:
                        report_text = f.read()
                except Exception:
                    pass

            return {
                "lang": result.analysis.lang if result.analysis else "",
                "domain": result.analysis.domain if result.analysis else "",
                "terms": [{"word": t.term, "freq": t.frequency, "type": t.word_type or "word", "score": round(t.score, 3)} for t in result.terms],
                "terms_count": len(result.terms),
                "glossary": result.glossary,
                "full_translation": result.full_translation,
                "termbase_terms": result.termbase_terms,
                "duration": round(result.duration, 1),
                "errors": result.errors,
                "report": report_text,
                "report_path": result.report_path or "",
                "glossary_json": result.glossary_json,
                "ai_used": result.ai_used,
                "translate_mode": result.translate_mode,
                "segments": result.segments,
            }
        except Exception as e:
            raise HTTPException(500, str(e))

    # ── Serve frontend ──
    @app.get("/", response_class=HTMLResponse)
    def index_html():
        index_path = os.path.join(STATIC_DIR, "index.html")
        if os.path.isfile(index_path):
            with open(index_path, encoding="utf-8") as f:
                return f.read()
        return HTMLResponse(status_code=404, content="index.html not found")

    # ════════════════════════════════════════════════════════════════
    #  Agent Routes (Multi-Agent Architecture)
    # ════════════════════════════════════════════════════════════════

    # Lazy-init orchestrator per request (thread-safe, stateless)
    def _get_orchestrator(request: Request | None = None) -> AgentOrchestrator:
        settings_dict = _ai_settings_from_request(request) if request else termprep_settings.to_dict()
        return AgentOrchestrator(settings_dict)

    # ── Engine health ──
    @app.get("/api/agents/engines")
    def api_agents_engines():
        """List all available translation engines with health status."""
        svc = TranslationService(termprep_settings.to_dict())
        svc.load_engines()
        return {
            "engines": svc.list_available_engines(),
            "settings": {
                "ai_provider": termprep_settings.ai_provider,
                "ai_model": termprep_settings.ai_model,
            },
        }

    # ── Term Agent ──
    @app.post("/api/agents/term")
    def api_agents_term(data: AgentTermIn, request: Request):
        """Term Agent: extract terminology and return standardized Glossary JSON."""
        if not data.text.strip():
            raise HTTPException(400, "No input text")
        try:
            orch = _get_orchestrator(request)
            glossary = orch.run_term_agent(
                text=data.text,
                project_name=data.project_name,
                source_file=data.source_file,
                top_n=data.top_n,
                domain_hint=data.domain_hint,
            )
            return {
                "glossary": glossary.model_dump(),
                "meta": glossary.meta.model_dump(),
                "term_count": len(glossary.terms),
                "high_confidence": len(glossary.by_confidence("high")),
                "confirmed": len(glossary.by_status("confirmed")),
            }
        except Exception as e:
            raise HTTPException(500, str(e))

    # ── Translation Agent ──
    @app.post("/api/agents/translate")
    def api_agents_translate(data: AgentTranslateIn, request: Request):
        """Translation Agent: translate text, optionally consuming a Glossary JSON."""
        if not data.text.strip():
            raise HTTPException(400, "No input text")
        try:
            from termprep.agents.schemas import Glossary
            glossary = None
            if data.glossary:
                glossary = Glossary.model_validate(data.glossary)

            orch = _get_orchestrator(request)
            result = orch.run_translation_agent(
                text=data.text,
                glossary=glossary,
                domain=data.domain,
                engine=data.engine,
                style=data.style,
                use_rag=data.use_rag,
            )
            return {
                "translated": result.translated_text,
                "source_lang": result.source_lang,
                "target_lang": result.target_lang,
                "domain": result.domain,
                "engine": result.engine,
                "model": result.model,
                "glossary_used": result.glossary_used,
                "term_consistency": result.term_consistency_score,
                "segments": [
                    {
                        "index": s.index,
                        "source": s.source,
                        "target": s.target,
                        "highlights": s.highlights,
                        "confidence": s.confidence,
                    }
                    for s in result.segments
                ],
                "errors": result.errors,
                "metadata": result.metadata,
            }
        except Exception as e:
            raise HTTPException(500, str(e))

    # ── Full Pipeline (Term + Translation) ──
    @app.post("/api/agents/pipeline")
    def api_agents_pipeline(data: AgentPipelineIn, request: Request):
        """Run the full multi-agent pipeline: Term Agent → Translation Agent."""
        if not data.text.strip():
            raise HTTPException(400, "No input text")
        try:
            orch = _get_orchestrator(request)
            result = orch.run_full_pipeline(
                text=data.text,
                project_name=data.project_name,
                source_file=data.source_file,
                top_n=data.top_n,
                domain_hint=data.domain_hint,
                translation_engine=data.translation_engine,
                translation_style=data.translation_style,
                use_rag=data.use_rag,
            )
            return result
        except Exception as e:
            raise HTTPException(500, str(e))

    # ── Async Task API ──
    @app.post("/api/agents/task/submit")
    def api_agents_task_submit(data: AgentTaskSubmitIn, request: Request):
        """Submit an async task and receive a task_id for polling."""
        orch = _get_orchestrator(request)
        task_id = orch.submit_task(data.task_type, data.payload)
        return {"task_id": task_id, "status": "pending"}

    @app.get("/api/agents/task/{task_id}")
    def api_agents_task_status(task_id: str):
        """Poll task status and retrieve result."""
        # Note: tasks are in-memory per orchestrator instance; this only works
        # if the same worker handles submit and status. Disabled on HF Spaces.
        raise HTTPException(501, "Async task polling is not supported in this deployment")

    @app.post("/api/agents/task/{task_id}/execute")
    def api_agents_task_execute(task_id: str):
        """Execute a pending task synchronously."""
        raise HTTPException(501, "Async task execution is not supported in this deployment")

    @app.get("/api/debug/env")
    def debug_env():
        import os
        return {
            "ai_provider_env": os.environ.get("TERMPREP_AI_PROVIDER", ""),
            "ai_model_env": os.environ.get("TERMPREP_AI_MODEL", ""),
            "ai_base_url_env": os.environ.get("TERMPREP_AI_BASE_URL", ""),
            "ai_api_key_env_set": bool(os.environ.get("TERMPREP_AI_API_KEY")),
            "settings_provider": termprep_settings.ai_provider,
            "settings_model": termprep_settings.ai_model,
            "settings_base_url": termprep_settings.ai_base_url,
            "settings_key_set": bool(termprep_settings.ai_api_key),
        }

    @app.get("/api/rag/stats")
    def api_rag_stats():
        """Get RAG vector store statistics."""
        try:
            from termprep.rag.vector_store import get_vector_store
            store = get_vector_store()
            if not store.is_available:
                return {
                    "available": False,
                    "reason": "chromadb not installed",
                    "counts": {"terms": 0, "corpus": 0, "documents": 0},
                }
            counts = store.count()
            return {"available": True, "counts": counts}
        except Exception as e:
            return {
                "available": False,
                "reason": str(e),
                "counts": {"terms": 0, "corpus": 0, "documents": 0},
            }

    @app.post("/api/rag/upload")
    async def api_rag_upload(
        request: Request,
        file: UploadFile = File(...),
        domain: str = Form("general"),
    ):
        """Upload a medical document and add it to the RAG vector store."""
        import tempfile
        import uuid

        try:
            suffix = os.path.splitext(file.filename or ".txt")[1]
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                content = await file.read()
                tmp.write(content)
                tmp_path = tmp.name

            text = parse_file(tmp_path)
            os.unlink(tmp_path)

            if not text.strip():
                raise HTTPException(400, "文件内容为空")

            from termprep.rag.chunker import MedicalChunker
            from termprep.rag.vector_store import get_vector_store
            from termprep.rag.medical_schema import MedicalDomain, Document, CorpusChunk

            try:
                med_domain = MedicalDomain(domain.lower())
            except ValueError:
                med_domain = MedicalDomain.GENERAL

            chunker = MedicalChunker()
            chunks = list(chunker.chunk_document(text, domain=med_domain))

            store = get_vector_store()
            if not store.is_available:
                raise HTTPException(503, "RAG vector store not available")

            doc_id = str(uuid.uuid4())
            doc = Document(
                id=doc_id,
                title=file.filename or "untitled",
                domain=med_domain,
                language="mixed",
                chunk_count=len(chunks),
            )
            store.add_document(doc)

            for chunk in chunks:
                chunk.source_document = doc_id
            store.add_corpus_chunks(chunks)

            counts = store.count()
            return {
                "document_id": doc_id,
                "filename": file.filename,
                "domain": med_domain.value,
                "chunks_added": len(chunks),
                "total_corpus": counts.get("corpus", 0),
                "total_terms": counts.get("terms", 0),
                "total_documents": counts.get("documents", 0),
            }
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(500, f"RAG upload failed: {e}")

    return app


def start_server(host: str = "127.0.0.1", port: int = 8672):
    """Start the TermPrep web server."""
    app = create_app()
    url = f"http://{host}:{port}"
    print(f"\n  TermPrep Web UI  →  {url}")
    print(f"  Press Ctrl+C to stop\n")
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    start_server()
