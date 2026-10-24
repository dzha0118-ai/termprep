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
from termprep.services.translation_analysis import analyze_translation_difficulty


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
    translation_qa: dict[str, Any] = field(default_factory=dict)  # NEW: AI QA review metadata
    # NEW: per-step outcome (name/label/status/detail/duration). The UI used to
    # show a decorative 1.2s-per-step animation, so a step that produced nothing
    # was indistinguishable from a step that succeeded.
    step_status: list[dict[str, Any]] = field(default_factory=list)


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
    steps: list[str] | None = None,  # NEW: run only these steps (None = all)
    # ── Human-in-the-loop support ──
    pipeline_id: str = "",
    on_event=None,  # Callable[[step_name, status, detail], None] — drives SSE
    preset_terms: list | None = None,     # 译员裁决后的术语（跳过提取）
    preset_glossary: list[dict] | None = None,  # 译员裁决后的 术语→译文（跳过 AI 术语翻译）
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

    # ── Step bookkeeping ──
    # ``steps`` (when given) restricts the run to the template's declared steps,
    # and every step records what actually happened so the UI can report the
    # truth instead of animating a fake progress bar.
    enabled = set(steps) if steps else None

    def _mark(name: str, label: str, status: str, detail: str = "", started: float | None = None) -> None:
        result.step_status.append({
            "name": name,
            "label": label,
            "status": status,  # ok | skipped | failed
            "detail": detail,
            "duration": round(time.time() - started, 2) if started else 0.0,
        })
        # Drive the SSE stream: without this the /api/pipeline/events endpoint
        # connected and then only sent keepalives (a dead feature).
        if on_event is not None:
            try:
                on_event(name, status, detail)
            except Exception:
                pass

    def _enabled(name: str) -> bool:
        return enabled is None or name in enabled

    # ---- Step 1: Analyze ----
    if not _enabled("analyze"):
        _mark("analyze", "分析文本", "skipped", "模板未包含该步骤")
    else:
        _t0 = time.time()
        try:
            if file_path:
                result.analysis = analyze_file(file_path)
            elif text:
                result.analysis = analyze(text)
            else:
                result.errors.append("No input provided (file or text required)")
                _mark("analyze", "分析文本", "failed", "无输入文本", _t0)
                return result
            _mark("analyze", "分析文本", "ok",
                  f"{result.analysis.lang} · {result.analysis.domain}", _t0)
        except Exception as e:
            result.errors.append(f"Analysis failed: {e}")
            _mark("analyze", "分析文本", "failed", str(e), _t0)
            return result

    source_text = text or (open(file_path, encoding="utf-8").read() if file_path else "")

    # ---- Step 2: Extract Terms ----
    if preset_terms is not None:
        # Human-in-the-loop: 译员已在检查点完成裁决，直接采用其结果，不再重新提取。
        result.terms = list(preset_terms)
        _mark("extract", "提取术语", "ok",
              f"采用译员裁决列表（{len(result.terms)} 项）")
    elif not _enabled("extract"):
        _mark("extract", "提取术语", "skipped", "模板未包含该步骤")
    else:
        _t0 = time.time()
        try:
            if file_path:
                result.terms = extract_file(file_path, top_n=top_n)
            else:
                result.terms = extract(text, top_n=top_n)
            _raw_count = len(result.terms)
            _detail = f"{_raw_count} 项候选"

            # ── P4 — AI 复核（可选）：一次批量调用，剔掉形态合法但非术语的长短语
            # 与冗长形式。未配置/失败时**完全不改动候选**（优雅降级）。
            if (active_settings.get("ai_api_key")
                    and active_settings.get("term_verify_ai", True) is not False):
                try:
                    from termprep.services.term_verifier import (
                        verify_terms,
                        normalise_term,
                    )
                    decisions, _vmeta = verify_terms(
                        [t.term for t in result.terms],
                        context=source_text,
                        settings=active_settings,
                    )
                    if decisions:
                        kept = []
                        dropped = 0
                        for t in result.terms:
                            d = decisions.get(normalise_term(t.term))
                            if d is not None and d.get("is_term") is False:
                                dropped += 1
                                continue
                            kept.append(t)
                        result.terms = kept
                        _detail = (
                            f"{_raw_count} 项候选 → AI 复核保留 {len(kept)} 项"
                            f"（剔除 {dropped} 项）"
                        )
                except Exception as e:
                    result.errors.append(f"AI term verification skipped: {e}")

            _mark("extract", "提取术语", "ok", _detail, _t0)
        except Exception as e:
            result.errors.append(f"Extraction failed: {e}")
            _mark("extract", "提取术语", "failed", str(e), _t0)

    # ---- Step 3: Build Glossary (Agent-enhanced or legacy) ----
    glossary_obj: Glossary | None = None
    if preset_glossary is not None:
        # Human-in-the-loop: 译文已由译员裁决确定（edited_translation / 词典检索采用 /
        # 引擎补齐），不再调用 AI 术语翻译，避免覆盖译员的决定。
        result.glossary = [dict(g) for g in preset_glossary]
        result.ai_used = bool(use_ai)
        _mark("glossary", "生成术语表", "ok",
              f"采用译员裁决译文（{len(result.glossary)} 条）")
    elif not _enabled("glossary"):
        _mark("glossary", "生成术语表", "skipped", "模板未包含该步骤")
    else:
        _t0 = time.time()
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

        if not use_ai and _enabled("glossary"):
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

        # Report how complete the glossary actually is — a partially translated
        # glossary used to look identical to a fully translated one.
        _untranslated = sum(
            1 for g in result.glossary if not g.get("translation") or g["translation"] == "[未找到翻译]"
        )
        if not result.glossary:
            _g_status, _g_detail = "failed", "未生成任何术语条目"
        elif _untranslated:
            _g_status, _g_detail = "ok", f"{len(result.glossary)} 条，其中 {_untranslated} 条未译出"
        else:
            _g_status, _g_detail = "ok", f"{len(result.glossary)} 条，全部已译"
        _mark("glossary", "生成术语表", _g_status, _g_detail, _t0)

    # Prepare glossary for injection (used by AI translation in Step 4)
    glossary_compact = glossary_obj.to_compact() if glossary_obj else []

    # ---- Step 4: Full-text Translation ----
    if not _enabled("full_translate"):
        _mark("full_translate", "全文翻译", "skipped", "模板未包含该步骤")
    else:
        _t0 = time.time()
        try:
            is_cn = bool(re.search(r'[\u4e00-\u9fff]', source_text))

            if translate_mode == "mixed":
                from termprep.translator import translate_text_mixed
                mixed_result = translate_text_mixed(
                    source_text,
                    domain=result.analysis.domain if result.analysis else "general",
                    engine=ai_engine if use_ai else None,
                    settings=active_settings,
                    use_rag=use_rag,
                    glossary=glossary_compact if use_ai and result.ai_used else None,
                )
                result.full_translation = mixed_result.translated_text
                result.rag_metadata = mixed_result.rag_metadata or {}
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
                    result.translation_qa = getattr(trans_result, 'metadata', {})
                    if trans_result.errors:
                        result.errors.extend(trans_result.errors)
                else:
                    result.full_translation = _translate_text(source_text, "zh-CN", "en")

            elif translate_mode == "en2zh":
                if use_ai and result.ai_used:
                    svc = TranslationService(active_settings)
                    svc.load_engines()
                    trans_result = svc.translate(text=source_text, source_lang="en", target_lang="zh", domain=result.analysis.domain if result.analysis else "general", engine=ai_engine, glossary=glossary_compact, use_rag=use_rag)
                    result.full_translation = trans_result.text
                    result.rag_metadata = getattr(trans_result, 'rag_metadata', {})
                    result.translation_qa = getattr(trans_result, 'metadata', {})
                    if trans_result.errors:
                        result.errors.extend(trans_result.errors)
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
                    result.translation_qa = getattr(trans_result, 'metadata', {})
                    if trans_result.errors:
                        result.errors.extend(trans_result.errors)
                else:
                    if is_cn:
                        result.full_translation = _translate_text(source_text, "zh-CN", "en")
                    else:
                        result.full_translation = _translate_text(source_text, "en", "zh-CN")

            # A silently empty translation is a FAILURE, not a success. Previously
            # an empty result was reported identically to a step that never ran —
            # exactly the "看不到第四步全文翻译" symptom.
            if result.full_translation and result.full_translation.strip():
                _mark("full_translate", "全文翻译", "ok",
                      f"{len(result.full_translation)} 字符", _t0)
            else:
                _why = result.errors[-1] if result.errors else "翻译引擎未返回内容"
                result.errors.append(
                    "全文翻译未产出内容：AI 与免费引擎均不可用、超时或被限流，请检查 AI 配置后重试。"
                )
                _mark("full_translate", "全文翻译", "failed", _why, _t0)
        except Exception as e:
            result.errors.append(f"Full translation failed: {e}")
            _mark("full_translate", "全文翻译", "failed", str(e), _t0)

    # ---- Step 5: Build Termbase ----
    tdb_name = _secure_filename(db_name or project_name)
    _t0 = time.time()
    if not _enabled("termbase"):
        _mark("termbase", "建立术语库", "skipped", "模板未包含该步骤")
    else:
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
            _mark("termbase", "建立术语库", "ok", f"{added} 条入库", _t0)
        except Exception as e:
            result.errors.append(f"Termbase failed: {e}")
            _mark("termbase", "建立术语库", "failed", str(e), _t0)

    # ---- Step 5.5: Translation Analysis ----
    translation_analysis_data = None
    _t0 = time.time()
    if not _enabled("translate_analysis"):
        _mark("translate_analysis", "翻译难度分析", "skipped", "模板未包含该步骤")
    else:
        try:
            translation_analysis_data = analyze_translation_difficulty(
                source_text=source_text,
                lang=result.analysis.lang if result.analysis else "auto",
                domain=result.analysis.domain if result.analysis else "general",
                terms=[{"term": t.term, "score": t.score} for t in result.terms],
                glossary=result.glossary,
            )
            _mark("translate_analysis", "翻译难度分析", "ok", "", _t0)
        except Exception as e:
            result.errors.append(f"Translation analysis failed: {e}")
            _mark("translate_analysis", "翻译难度分析", "failed", str(e), _t0)

    # ---- Step 6: Generate Report ----
    _t0 = time.time()
    if not _enabled("report"):
        _mark("report", "生成报告", "skipped", "模板未包含该步骤")
    else:
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
                translation_analysis=translation_analysis_data,
                segments=result.segments,
                source_text=source_text,
                translation_qa=result.translation_qa,
            )
            # Append glossary JSON to report if available
            if result.glossary_json:
                report_text += "\n\n---\n\n"
                report_text += "## 10. Standardized Glossary JSON (Agent Schema)\n\n"
                report_text += f"```json\n{result.glossary_json}\n```\n"
            save_report(report_text, report_output)
            result.report_path = report_output
            _mark("report", "生成报告", "ok", result.report_path, _t0)
        except Exception as e:
            result.errors.append(f"Report generation failed: {e}")
            _mark("report", "生成报告", "failed", str(e), _t0)

    # ---- Step 7: Export ----
    _t0 = time.time()
    if not _enabled("export"):
        _mark("export", "导出", "skipped", "模板未包含该步骤")
    elif not export_formats:
        # The template lists an export step, but nothing was requested. Say so
        # instead of leaving the step silently missing from the run.
        _mark("export", "导出", "skipped", "未指定导出格式")
    elif result.termbase_terms <= 0:
        _mark("export", "导出", "skipped", "术语库为空，无可导出内容")
    else:
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
        _mark("export", "导出", "ok", ", ".join(result.exports.keys()) or "无", _t0)

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
