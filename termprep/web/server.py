"""TermPrep Web Server — FastAPI backend."""

import os
import sys
import json
import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional

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

PROJ_ROOT = _root
STATIC_DIR = os.path.join(PROJ_ROOT, "web", "static")


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


# ── App factory ──────────────────────────────────────────────────

def create_app() -> FastAPI:
    app = FastAPI(title="TermPrep", version="0.4")

    # CORS — allow GitHub Pages and local dev
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "https://dzha0118-ai.github.io",
            "http://127.0.0.1:8672",
            "http://localhost:8672",
        ],
        allow_credentials=True,
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
    def api_extract(data: ExtractIn):
        if not data.text.strip():
            raise HTTPException(400, "No input text")
        terms = extract(data.text, top_n=data.top_n)
        return {"terms": [
            {"word": t.term, "freq": t.frequency, "type": t.word_type or "word",
             "score": round(t.score, 3), "pos": ""}
            for t in terms
        ]}

    @app.post("/api/search")
    def api_search(data: SearchIn):
        if not data.term.strip():
            raise HTTPException(400, "No search term")
        results = search_term(data.term, source="web", limit=data.limit)
        return {"results": results, "term": data.term}

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
    def api_pipeline(data: PipelineIn):
        if not data.text.strip():
            raise HTTPException(400, "No input text")
        result = run_pipeline(
            text=data.text,
            project_name=data.project_name,
            top_n=data.top_n,
            search_limit=data.search_limit,
            db_name=data.db_name,
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
            "termbase_terms": result.termbase_terms,
            "duration": round(result.duration, 1),
            "errors": result.errors,
            "report": report_text,
            "report_path": result.report_path or "",
        }

    # ── Serve frontend ──
    @app.get("/", response_class=HTMLResponse)
    def index_html():
        index_path = os.path.join(STATIC_DIR, "index.html")
        if os.path.isfile(index_path):
            with open(index_path, encoding="utf-8") as f:
                return f.read()
        return HTMLResponse(status_code=404, content="index.html not found")

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
