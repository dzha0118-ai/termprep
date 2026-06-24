"""Full automated pre-translation pipeline (Agent-enhanced).

One command: analyze -> extract -> AI/translate terms -> full-text translate -> generate report -> export

NEW: Integrated with Agent architecture for standardized Glossary JSON output.
- use_ai=False (default): backward-compatible, uses Google/Youdao
- use_ai=True: uses TermAgent for standardized glossary, TranslationService with AI fallback

AI is optional enhancement. Without API key, auto-degrades to Google/Youdao.
"""

import json, time, re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from termprep.analyzer import AnalysisResult, analyze, analyze_file
from termprep.extractor import TermEntry, extract, extract_file
from termprep.searcher import search_term as do_search
from termprep.db import TermDB, init_termbase
from termprep.report import generate_report, save_report
from termprep.config import settings as _default_settings


# ── NEW: Agent imports for enhanced pipeline ──
from termprep.agents.term_agent import TermAgent
from termprep.agents.schemas import Glossary
from termprep.services.translation_service import TranslationService


def _pipeline_settings(overrides=None):
    """Return active settings dict, merging optional client overrides."""
    s = _default_settings.to_dict()
    if overrides:
        s.update({k: v for k, v in overrides.items() if v is not None and v != ""})
    return s


def _secure_filename(name: str) -> str:
    """Sanitize a project / file name to prevent path traversal."""
    name = name.strip()
    name = re.sub(r'[\\/:*?"<>|]', "", name)
    name = re.sub(r'\s+', " ", name)
    if name in ("", ".", "..") or name.startswith("."):
        name = "untitled"
    return name


@dataclass
class PipelineResult:
    project_name: str = ""
    source_file: str = ""
    analysis: AnalysisResult | None = None
    terms: list[TermEntry] = field(default_factory=list)
    termbase_name: str = ""
    termbase_terms: int = 0
    report_path: str = ""
    exports: dict[str, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    duration: float = 0.0
    search_results_count: int = 0
    full_translation: str = ""  # full-text translation
    glossary: list[dict] = field(default_factory=list)  # backward-compatible flat glossary
    glossary_json: str = ""   # NEW: standardized Glossary JSON (Agent schema)
    ai_used: bool = False     # NEW: whether AI was used in this run
    translate_mode: str = "auto"  # NEW: "auto" | "mixed" | "zh2en" | "en2zh"
    segments: list[dict] = field(default_factory=list)  # NEW: paragraph segments for mixed mode
    rag_metadata: dict[str, Any] = field(default_factory=dict)  # NEW: RAG retrieval metadata


def run_pipeline(
    file_path: str | None = None,
    text: str | None = None,
    project_name: str = "Untitled",
    top_n: int = 20,
    search_limit: int = 5,
    db_name: str | None = None,
    report_output: str = "",
    export_formats: list[str] | None = None,
    notes: str = "",
    use_ai: bool = False,
    ai_engine: str | None = None,
    translate_mode: str = "auto",  # NEW: "auto" | "mixed" | "zh2en" | "en2zh"
    use_rag: bool = False,
    settings=None,
) -> PipelineResult:
    """Run the full pre-translation preparation pipeline.

    Args:
        file_path: Path to source file.
        text: Source text (alternative to file_path).
        project_name: Project name for reports/exports.
        top_n: Max terms to extract.
        search_limit: Max search results per term (legacy).
        db_name: Termbase name.
        report_output: Report output path.
        export_formats: List of export formats.
        notes: Additional notes for report.
        use_ai: If True, use TermAgent for glossary + TranslationService for full text.
                If False (default), use legacy Google/Youdao logic.
        ai_engine: Specific engine name when use_ai=True (e.g. "ai", "google", "youdao").
                   None means auto-priority chain.
        translate_mode: Translation direction strategy:
            - "auto": detect overall language, translate unidirectionally (default)
            - "mixed": paragraph-level bidirectional (CN→EN, EN→CN)
            - "zh2en": force Chinese → English
            - "en2zh": force English → Chinese
        use_rag: Enable RAG retrieval for medical domain context.

    Returns:
        PipelineResult with both legacy glossary (list[dict]) and new glossary_json.
    """
    result = PipelineResult(
        project_name=_secure_filename(project_name),
        source_file=file_path or "",
        translate_mode=translate_mode,
    )
    start = time.time()
    active_settings = _pipeline_settings(settings)

    # ---- Step 1: Analyze ----
    try:
        if file_path:
            result.analysis = analyze_file(file_path)
        elif text:
            result.analysis = analyze(text)
        else:
            result.errors.append("No input provided (file or text required)")
            return result
    except Exception as e:
        result.errors.append(f"Analysis failed: {e}")
        return result

    source_text = text or (open(file_path, encoding="utf-8").read() if file_path else "")

    # ---- Step 2: Extract Terms ----
    try:
        if file_path:
            result.terms = extract_file(file_path, top_n=top_n)
        else:
            result.terms = extract(text, top_n=top_n)
    except Exception as e:
        result.errors.append(f"Extraction failed: {e}")

    # ---- Step 3: Build Glossary (Agent-enhanced or legacy) ----
    glossary_obj: Glossary | None = None
    if use_ai:
        # NEW: Use TermAgent for standardized glossary
        try:
            term_agent = TermAgent(active_settings)
            glossary_obj = term_agent.run(
                text=source_text,
                project_name=project_name,
                source_file=file_path or "",
                top_n=top_n,
                domain_hint=result.analysis.domain if result.analysis else "",
            )
            result.glossary_json = glossary_obj.model_dump_json()
            result.ai_used = True
            # Convert to backward-compatible flat glossary
            for t in glossary_obj.terms:
                result.glossary.append({
                    "term": t.source_term,
                    "translation": t.target_translation or "[未找到翻译]",
                    "freq": t.frequency,
                    "type": t.term_type,
                    "context": t.source_context,
                    "confidence": t.confidence.value,
                    "status": t.status.value,
                    "term_id": t.term_id,
                })
        except Exception as e:
            result.errors.append(f"AI TermAgent failed: {e}; falling back to legacy.")
            use_ai = False
            result.ai_used = False

    if not use_ai:
        # LEGACY: old Google/Youdao term-by-term translation
        is_cn_source = result.analysis and result.analysis.lang in ("zh", "mixed")
        for term_entry in result.terms:
            t = term_entry.term
            if len(t) < 2:
                continue
            try:
                if is_cn_source:
                    trans = _translate_cn_to_en(t)
                else:
                    trans = _translate_en_to_cn(t)
                ctx = _find_context(source_text, t)
                result.glossary.append({
                    "term": t,
                    "translation": trans or "[未找到翻译]",
                    "freq": term_entry.frequency,
                    "type": term_entry.word_type,
                    "context": ctx,
                })
            except Exception:
                continue

    # Prepare glossary for injection (used by AI translation in Step 4)
    glossary_compact = glossary_obj.to_compact() if glossary_obj else []

    # ---- Step 4: Full-text Translation ----
    try:
        is_cn = bool(re.search(r'[\u4e00-\u9fff]', source_text))

        if translate_mode == "mixed":
            # NEW: mixed mode — paragraph-level bidirectional translation
            from termprep.translator import translate_text_mixed
            mixed_result = translate_text_mixed(source_text, domain=result.analysis.domain if result.analysis else "general", engine=ai_engine if use_ai else None, settings=active_settings, use_rag=use_rag)
            result.full_translation = mixed_result.translated_text
            result.rag_metadata = mixed_result.rag_metadata or {}
            # NEW: pass segments for frontend bilingual display
            result.segments = [
                {"index": seg.index, "source": seg.source, "target": seg.target, "highlights": seg.highlights}
                for seg in mixed_result.segments
            ]

        elif translate_mode == "zh2en":
            if use_ai and result.ai_used:
                svc = TranslationService(active_settings)
                svc.load_engines()
                trans_result = svc.translate(text=source_text, source_lang="zh", target_lang="en", domain=result.analysis.domain if result.analysis else "general", engine=ai_engine, glossary=glossary_compact, use_rag=use_rag)
                result.full_translation = trans_result.text
                result.rag_metadata = getattr(trans_result, 'rag_metadata', {})
            else:
                result.full_translation = _translate_text(source_text, "zh-CN", "en")

        elif translate_mode == "en2zh":
            if use_ai and result.ai_used:
                svc = TranslationService(active_settings)
                svc.load_engines()
                trans_result = svc.translate(text=source_text, source_lang="en", target_lang="zh", domain=result.analysis.domain if result.analysis else "general", engine=ai_engine, glossary=glossary_compact, use_rag=use_rag)
                result.full_translation = trans_result.text
                result.rag_metadata = getattr(trans_result, 'rag_metadata', {})
            else:
                result.full_translation = _translate_text(source_text, "en", "zh-CN")

        else:
            # auto mode (legacy behavior)
            if use_ai and result.ai_used:
                svc = TranslationService(active_settings)
                svc.load_engines()
                src_lang = "zh" if is_cn else "en"
                tgt_lang = "en" if is_cn else "zh"
                trans_result = svc.translate(
                    text=source_text,
                    source_lang=src_lang,
                    target_lang=tgt_lang,
                    domain=result.analysis.domain if result.analysis else "general",
                    engine=ai_engine,
                    glossary=glossary_compact,
                    use_rag=use_rag,
                )
                result.full_translation = trans_result.text
                result.rag_metadata = getattr(trans_result, 'rag_metadata', {})
                if trans_result.errors:
                    result.errors.extend(trans_result.errors)
            else:
                if is_cn:
                    result.full_translation = _translate_text(source_text, "zh-CN", "en")
                else:
                    result.full_translation = _translate_text(source_text, "en", "zh-CN")
    except Exception as e:
        result.errors.append(f"Full translation failed: {e}")

    # ---- Step 5: Build Termbase ----
    tdb_name = _secure_filename(db_name or project_name)
    try:
        try:
            tdb = init_termbase(tdb_name, domain=result.analysis.domain if result.analysis else "")
        except FileExistsError:
            tdb = TermDB(db_name=tdb_name)

        added = 0
        for g in result.glossary:
            try:
                tdb.add_term(
                    word=g["term"],
                    translation=g["translation"],
                    domain=result.analysis.domain if result.analysis else "",
                    type_=g.get("type", ""),
                    status="confirmed" if g.get("translation") and g["translation"] != "[未找到翻译]" else "draft",
                )
                added += 1
            except Exception:
                continue
        result.termbase_name = tdb_name
        result.termbase_terms = added
    except Exception as e:
        result.errors.append(f"Termbase failed: {e}")

    # ---- Step 6: Generate Report ----
    try:
        tdb = TermDB(db_name=tdb_name) if result.termbase_terms > 0 else None
        db_stats = tdb.get_stats() if tdb else {}
        if not report_output:
            safe_name = _secure_filename(project_name)
            report_output = f"{safe_name}-report.md"
        report_text = generate_report(
            analysis=result.analysis,
            terms=result.terms,
            glossary=result.glossary,
            full_translation=result.full_translation,
            db_stats=db_stats,
            project_name=project_name,
            source_file=file_path or "",
            notes=notes,
        )
        # Append glossary JSON to report if available
        if result.glossary_json:
            report_text += "\n\n---\n\n"
            report_text += "## 8. Standardized Glossary JSON (Agent Schema)\n\n"
            report_text += f"```json\n{result.glossary_json}\n```\n"
        save_report(report_text, report_output)
        result.report_path = report_output
    except Exception as e:
        result.errors.append(f"Report generation failed: {e}")

    # ---- Step 7: Export ----
    if export_formats and result.termbase_terms > 0:
        tdb = TermDB(db_name=tdb_name)
        for fmt in export_formats:
            try:
                fmt = fmt.lower().strip(".")
                safe_name = _secure_filename(project_name)
                out_path = f"{safe_name}-terms.{fmt}"
                if fmt == "csv": from termprep.exporter import export_csv; export_csv(tdb, out_path)
                elif fmt == "xlsx": from termprep.exporter import export_xlsx; export_xlsx(tdb, out_path)
                elif fmt == "tbx": from termprep.exporter import export_tbx; export_tbx(tdb, out_path)
                elif fmt == "json": from termprep.exporter import export_json; export_json(tdb, out_path)
                else: result.errors.append(f"Unknown format: {fmt}"); continue
                result.exports[fmt] = out_path
            except Exception as e:
                result.errors.append(f"Export {fmt} failed: {e}")

    # NEW: Also export glossary JSON as a standalone file
    if result.glossary_json:
        try:
            json_path = f"{_secure_filename(project_name)}-glossary.json"
            with open(json_path, "w", encoding="utf-8") as f:
                f.write(result.glossary_json)
            result.exports["glossary_json"] = json_path
        except Exception as e:
            result.errors.append(f"Glossary JSON export failed: {e}")

    result.duration = time.time() - start
    return result


# ── Legacy helpers (unchanged) ──

_google_available: bool | None = None


def _google_translate_available() -> bool:
    """Probe Google Translate once; cache the result."""
    global _google_available
    if _google_available is not None:
        return _google_available
    try:
        import urllib.parse, requests
        url = "https://translate.googleapis.com/translate_a/single?client=gtx&sl=en&tl=zh-CN&dt=t&q=test"
        r = requests.get(url, timeout=2, headers={"User-Agent": "Mozilla/5.0"})
        _google_available = r.status_code == 200
    except Exception:
        _google_available = False
    return _google_available


def _mark_google_unavailable() -> None:
    global _google_available
    _google_available = False


def _translate_cn_to_en(text: str) -> str:
    """Translate Chinese to English using Youdao API if configured."""
    try:
        from termprep.sources.youdao import YoudaoSource
        y = YoudaoSource()
        if not y.available: return ""
        text = text.replace('\uff0c', ',').replace('\u3001', ',')
        sr = y.search(text, limit=1)
        for r in sr:
            if r.word_type == "translation" and r.word: return r.word
    except Exception: pass
    return ""


def _translate_en_to_cn(text: str) -> str:
    """Translate English to Chinese using Google Translate with strict validation."""
    if not _google_translate_available():
        return ""
    try:
        import urllib.parse, requests
        encoded = urllib.parse.quote(text)
        url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl=en&tl=zh-CN&dt=t&q={encoded}"
        resp = requests.get(url, timeout=2, headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list) and data and isinstance(data[0], list):
                parts = [s[0] for s in data[0] if isinstance(s, (list, tuple)) and s and s[0]]
                result = " ".join(parts) if parts else ""
                if result and bool(re.search(r'[\u4e00-\u9fff]', result)):
                    return result
    except Exception:
        _mark_google_unavailable()
    return ""


def _translate_text(text: str, src: str, tgt: str) -> str:
    """Translate full text using Google Translate with short timeouts per chunk."""
    if not _google_translate_available():
        return ""
    try:
        import urllib.parse, requests
        chunks = []
        buf = ""
        for s in re.split(r'(?<=[.!?。！？])\s+', text):
            if len(buf) + len(s) < 1500: buf += s
            else:
                if buf.strip(): chunks.append(buf.strip())
                buf = s
        if buf.strip(): chunks.append(buf.strip())

        results = []
        for c in chunks:
            encoded = urllib.parse.quote(c)
            url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl={src}&tl={tgt}&dt=t&q={encoded}"
            try:
                resp = requests.get(url, timeout=2, headers={"User-Agent": "Mozilla/5.0"})
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, list) and data and isinstance(data[0], list):
                        parts = [s[0] for s in data[0] if isinstance(s, (list, tuple)) and s and s[0]]
                        if parts: results.append(" ".join(parts))
            except Exception:
                _mark_google_unavailable()
                break
            time.sleep(0.05)
        return "\n\n".join(results) if results else ""
    except Exception: pass
    return ""


def _find_context(text: str, term: str) -> str:
    """Find the sentence containing the term for context."""
    idx = text.find(term)
    if idx < 0: return ""
    start = max(0, idx - 30)
    end = min(len(text), idx + len(term) + 40)
    ctx = text[start:end].strip()
    if start > 0: ctx = "..." + ctx
    if end < len(text): ctx += "..."
    return ctx


def format_pipeline_result(result: PipelineResult) -> str:
    """Format a PipelineResult into a human-readable summary for the CLI."""
    lines = [f"[bold]Pipeline result: {result.project_name}[/bold]"]
    lines.append(f"Duration: {result.duration:.2f}s")
    if result.analysis:
        lines.append(f"Language: {result.analysis.lang} | Domain: {result.analysis.domain} | Difficulty: {result.analysis.difficulty}")
    lines.append(f"Terms extracted: {len(result.terms)}")
    lines.append(f"Glossary entries: {len(result.glossary)}")
    if result.glossary_json:
        lines.append(f"[green]AI-enhanced:[/green] Glossary JSON exported")
    lines.append(f"Termbase terms added: {result.termbase_terms}")
    if result.report_path:
        lines.append(f"Report: {result.report_path}")
    if result.exports:
        lines.append("Exports:")
        for fmt, path in result.exports.items():
            lines.append(f"  {fmt}: {path}")
    if result.errors:
        lines.append("[red]Errors:[/red]")
        for err in result.errors:
            lines.append(f"  - {err}")
    return "\n".join(lines)


# ── NEW: Convenience function for AI-enhanced pipeline ──

def run_ai_pipeline(
    text: str | None = None,
    file_path: str | None = None,
    project_name: str = "Untitled",
    top_n: int = 20,
    **kwargs: Any,
) -> PipelineResult:
    """Convenience wrapper that always uses AI-enhanced mode.

    This is the recommended entry point for new users who want the
    standardized Glossary JSON output.

    Usage:
        result = run_ai_pipeline(text="Machine learning...", top_n=30)
        print(result.glossary_json)  # standardized JSON
        print(result.translated_text)  # AI translation with glossary injection
    """
    return run_pipeline(
        text=text,
        file_path=file_path,
        project_name=project_name,
        top_n=top_n,
        use_ai=True,
        **kwargs,
    )
