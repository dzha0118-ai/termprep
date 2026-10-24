"""TermPrep Web Server — FastAPI backend."""

import asyncio
import hashlib
import json
import os
import queue
import re
import sys
import time
import uuid
import uvicorn
from fastapi import FastAPI, HTTPException, Query, UploadFile, File, Form, Request, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, HTMLResponse, StreamingResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from pydantic import BaseModel
from typing import Any, Optional

# Ensure termprep root is on path
_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _root not in sys.path:
    sys.path.insert(0, _root)

from termprep.analyzer import analyze, get_summary
from termprep.extractor import extract, explainable_score, guess_pos
from termprep.searcher import search_term, search_multi
from termprep.db import TermDB, list_termbases, init_termbase
from termprep.sources import get_available_sources
from termprep.pipeline import run_pipeline
from termprep.associator import find_related
from termprep.translator import translate_text, parse_file
from termprep.tm import store as tm_store, search as tm_search, stats as tm_stats
from termprep.termbase import lookup_term, validate_terms
from termprep import decision_log

PROJ_ROOT = _root
STATIC_DIR = os.path.join(PROJ_ROOT, "web", "static")

# Single source of truth for the reported version (§5.4 / §5.10). Exposed by
# ``/api/health`` so the paper's stated version can be verified against the
# running deployment.
APP_VERSION = "0.5"


from termprep.web.security import (
    ADMIN_TOKEN_ENV,
    MAX_REQUEST_BYTES,
    admin_token_configured,
    precheck_content_length,
    require_admin,
    validate_upload,
)


# ── Agent imports (new architecture) ──
from termprep.agents.orchestrator import AgentOrchestrator
from termprep.agents.schemas import TranslationRequest as AgentTranslationRequest
from termprep.services.translation_service import TranslationService
from termprep.services.pipeline_service import PipelineService, list_templates, get_template
from termprep.services.translation_analysis import analyze_translation_difficulty
from termprep.config import settings as termprep_settings
import termprep.engines  # trigger engine registration (ai, google, youdao)


# ── SSE event store (in-memory, per-worker) ──
# Keyed by pipeline_id, each entry is a queue.Queue of SSE event dicts.
_pipeline_events: dict[str, queue.Queue] = {}
_PIPELINE_EVENT_TTL = 3600  # 1 hour


def _push_pipeline_event(pipeline_id: str, event_type: str, data: dict[str, Any]) -> None:
    """Push an SSE event to the pipeline event queue."""
    q = _pipeline_events.get(pipeline_id)
    if q is not None:
        q.put_nowait({"event": event_type, "data": data})


def _clean_stale_event_queues() -> None:
    """Remove stale event queues (called periodically)."""
    now = time.time()
    stale = [pid for pid, q in _pipeline_events.items()
             if hasattr(q, '_created_at') and now - getattr(q, '_created_at', 0) > _PIPELINE_EVENT_TTL]
    for pid in stale:
        _pipeline_events.pop(pid, None)


# ── Human-in-the-loop pipeline sessions (in-memory, TTL 1h) ──
# Same shape as the SSE event store: a plain module-level dict with a TTL sweep.
# A HITL session holds the input text, the run parameters, the analysis and the
# candidate terms produced by /api/pipeline/start, so that /resume can continue
# from the translator's adjudication without re-extracting.
_HITL_TTL = 3600  # 1 hour

# 会话必须**跨 worker 共享**：部署命令是 `gunicorn -w 2`，`/start` 落在 worker A、
# `/resume` 落在 worker B 就必然「session not found or expired」。
# 因此落盘到 SQLite（termprep.hitl_store），多 worker 共享同一个文件。
from termprep import hitl_store as _hitl_store  # noqa: E402

# 进程内兜底缓存：即使 SQLite 读取瞬时失败（多进程锁竞争），同一个 worker 仍能
# 从自己的缓存里拿到刚创建/刚读取过的会话，避免把可用的会话误判成 404。
_HITL_LOCAL_CACHE: dict[str, dict[str, Any]] = {}
_HITL_LOCAL_MAX = 64


def _hitl_clean() -> None:
    try:
        _hitl_store.clean()
    except Exception:
        pass


def _hitl_get(session_id: str) -> dict[str, Any] | None:
    """Look up a checkpoint session (SQLite first, process cache as fallback).

    A storage error must NEVER be reported as "session expired": we fall back to
    the in-process cache, and if that misses too we re-raise so the endpoint can
    return 503 with the real reason instead of a misleading 404.
    """
    sid = (session_id or "").strip()
    if not sid:
        return None
    try:
        sess = _hitl_store.get(sid)
    except Exception:
        cached = _HITL_LOCAL_CACHE.get(sid)
        if cached is not None:
            return cached
        raise
    if sess is not None:
        _HITL_LOCAL_CACHE[sid] = sess
        if len(_HITL_LOCAL_CACHE) > _HITL_LOCAL_MAX:
            for k in list(_HITL_LOCAL_CACHE)[: len(_HITL_LOCAL_CACHE) - _HITL_LOCAL_MAX]:
                _HITL_LOCAL_CACHE.pop(k, None)
        return sess
    # DB says missing — but this worker may have created it moments ago
    return _HITL_LOCAL_CACHE.get(sid)


def _hitl_save(session_id: str, payload: dict[str, Any]) -> None:
    _hitl_store.save(session_id, payload, ttl=_HITL_TTL)
    _HITL_LOCAL_CACHE[session_id] = dict(payload, session_id=session_id)


def _pipeline_session_or_404(session_id: str) -> dict[str, Any]:
    """取检查点会话；区分「存储故障(503)」与「会话不存在(404)」。"""
    try:
        sess = _hitl_get(session_id)
    except Exception as e:
        raise HTTPException(
            503,
            f"检查点存储暂时不可用（{type(e).__name__}）。请重试；"
            "若持续失败请查看 /api/health 的 hitl 区块。",
        )
    if not sess:
        raise HTTPException(
            404,
            "该检查点会话已过期或不存在（TTL 1 小时）。请在流水线页重新点"
            "「① 开始：分析与提取」。",
        )
    return sess


def _download_name(sess: dict[str, Any], kind: str, ext: str) -> str:
    """生成安全的下载文件名（中文项目名转写失败则退回默认名）。"""
    raw = (sess.get("result", {}) or {}).get("termbase_name") \
        or sess.get("project_name") or ""
    base = re.sub(r"[^A-Za-z0-9._-]+", "-", str(raw)).strip("-._")
    base = base[:60] or "termprep"
    ext = (ext or "txt").lstrip(".")
    return f"{base}-{kind}.{ext}"


def _attachment_headers(filename: str) -> dict[str, str]:
    """Content-Disposition：同时给出 ASCII 回退与 RFC 5987 的 UTF-8 名。"""
    from urllib.parse import quote

    ascii_name = re.sub(r'[^A-Za-z0-9._-]+', "-", filename) or "download"
    return {
        "Content-Disposition": (
            f'attachment; filename="{ascii_name}"; '
            f"filename*=UTF-8''{quote(filename)}"
        ),
        "Cache-Control": "no-store",
    }


# ── Agent task store (module level, so submit/status/execute agree) ──
# The orchestrator keeps its own _tasks dict per instance, but the API creates a
# fresh orchestrator per request — which is exactly why submit returned a
# task_id while status/execute were hard-coded 501 (a dead end).
_AGENT_TASKS: dict[str, dict[str, Any]] = {}
_AGENT_TASK_TTL = 3600


def _agent_tasks_clean() -> None:
    now = time.time()
    for tid in [k for k, v in _AGENT_TASKS.items() if now - v.get("created_at", 0) > _AGENT_TASK_TTL]:
        _AGENT_TASKS.pop(tid, None)


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
    disable_ai: bool = False   # 调用方明确关闭 AI（否则服务端有 key 就用）
    ai_engine: Optional[str] = None
    translate_mode: str = "auto"  # auto | mixed | zh2en | en2zh
    use_rag: bool = False

class PipelineRunStep(BaseModel):
    name: str
    type: str = "builtin"
    label: str = ""
    config: dict[str, Any] = {}

class PipelineRunIn(BaseModel):
    text: str
    project_name: str = "Untitled"
    template: str = "full-pipeline"
    steps: list[PipelineRunStep] | None = None
    top_n: int = 30
    use_ai: bool = True
    ai_engine: Optional[str] = None
    translate_mode: str = "auto"
    use_rag: bool = False
    stream: bool = True

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


class DecisionItem(BaseModel):
    """One 系统建议 / 译员裁决 record (§5.4.1 + §5.11)."""
    term: str
    system_suggestion: str = ""
    translator_verdict: str = "pending"   # pending|accepted|rejected|added|merged|edited
    score: float | None = None
    score_breakdown: dict | None = None
    note: str = ""
    step: str = ""
    input_digest: str = ""


class PipelineStartIn(BaseModel):
    """Human-in-the-loop 检查点：只跑 analyze + extract。"""
    text: str
    project_name: str = "Untitled"
    top_n: int = 30
    source_file: str = ""
    domain_hint: str = ""
    # 检查点阶段就把候选译文填好，否则裁决表那一列永远是「—」，译员只能盲裁
    prefill_translations: bool = True


class DecisionRecordIn(BaseModel):
    session_id: str = ""
    step: str = ""
    input_digest: str = ""
    items: list[DecisionItem] = []
    # §5.4.1 — 让个案「可复现」：把完整原文与运行参数一并记住，
    # 只存 80 字摘要的话导出包无法被复跑（见 export_bundle 的说明）。
    input_text: str = ""
    parameters: dict | None = None


class AdjudicationItem(BaseModel):
    """译员对单个候选术语的裁决。"""
    word: str
    verdict: str = "accepted"          # accepted | rejected | edited | added
    edited_word: str = ""              # verdict=edited 时的修正后术语
    edited_translation: str = ""       # verdict=edited/added 时的译文
    merge_into: str = ""               # 合并目标（另一条候选的 word）
    type: str = ""
    freq: int | None = None
    score: float | None = None
    score_breakdown: dict | None = None
    note: str = ""


class PipelineResumeIn(BaseModel):
    """裁决后继续：glossary → full_translate → termbase → report → export。"""
    terms: list[AdjudicationItem] = []
    project_name: str = "Untitled"
    top_n: int = 30
    translate_mode: str = "auto"
    use_rag: bool = False
    engine: str | None = None
    use_ai: bool = False
    disable_ai: bool = False   # 调用方明确关闭 AI（否则服务端有 key 就用）
    # 词典检索「采用」的译文（前端可直接带上；服务端还会回查裁决日志）
    adopted_translations: dict[str, str] | None = None
    export_formats: list[str] | None = None


# ── Template step names → run_pipeline() internal step keys ──
# The chosen template previously did nothing at all: /api/pipeline/v2 echoed the
# name back but always executed the same fixed workflow, so 「快速分析」和
# 「完整流水线」ran byte-identical work and no step could ever be reported missing.
_TEMPLATE_STEP_TO_KEY: dict[str, str] = {
    "analyze": "analyze",
    "extract": "extract",
    "ai_extract": "extract",
    "glossary": "glossary",
    "ai_term_translate": "glossary",
    "ai_glossary": "glossary",
    "full_translate": "full_translate",
    "ai_full_translate": "full_translate",
    "ai_translate": "full_translate",
    "translate_analysis": "translate_analysis",
    "termbase": "termbase",
    "report": "report",
    "export": "export",
}


def _template_step_keys(names: list[str]) -> list[str] | None:
    """Translate template step names into run_pipeline step keys (None = all)."""
    keys = [_TEMPLATE_STEP_TO_KEY[n] for n in names if n in _TEMPLATE_STEP_TO_KEY]
    return keys or None


def _adopted_translations_from_log(session_id: str = "") -> dict[str, str]:
    """词典检索中被译员「采用」的译文（step=search, verdict=accepted）。

    Human-in-the-loop：译员在搜索页点「采用」后，resume 时该译文优先入库，
    避免又被 AI 翻译覆盖掉。
    """
    out: dict[str, str] = {}
    try:
        for r in decision_log.list_decisions(session_id or None, limit=5000):
            if (r.get("step") or "") != "search":
                continue
            if (r.get("translator_verdict") or "") != decision_log.VERDICT_ACCEPTED:
                continue
            term = (r.get("term") or "").strip()
            trans = (r.get("system_suggestion") or "").strip()
            if term and trans:
                out[term] = trans
    except Exception:
        pass
    return out


def _apply_term_verification(
    raw_terms: list, decisions: dict, normalise
) -> tuple[list, list[dict], str]:
    """把 AI 复核结论应用到候选上，并带一道「不许清空」的兜底。

    为什么需要兜底：AI 语义复核有时会**把所有候选都判成非术语**（短文本、领域
    边缘文本、模型抽样波动）。此时用户看到的就是「提取不出来术语」——但输入
    明明有内容，只是被复核环节清空了。宁可保留候选让译员裁决（HITL 里人本来
    就要逐条过），也不要给出一个空列表。

    Returns ``(kept, dropped, fallback_note)``。
    """
    kept: list = []
    dropped: list[dict] = []
    for t in raw_terms:
        d = decisions.get(normalise(t.term))
        if d is not None and d.get("is_term") is False:
            dropped.append({
                "word": t.term,
                "type": d.get("type", ""),
                "reason": d.get("reason", ""),
                "freq": getattr(t, "frequency", None),
            })
        else:
            kept.append(t)

    if raw_terms and not kept and dropped:
        # AI 想把整批都删掉 —— 不采信，原样保留
        return list(raw_terms), [], "would-drop-all"
    return kept, dropped, ""


def _extraction_diagnostics(
    text: str, raw_count: int, kept_count: int, dropped_count: int
) -> dict:
    """解释「为什么只有这么几个（或没有）候选」。

    统计类提取器（jieba TF-IDF / TextRank / YAKE / C-Value）依赖词频与共现，
    文本太短时天然出不了几个词。用户看到空表时最需要的就是这个原因说明。
    """
    chars = len(text or "")
    cn_chars = len(re.findall(r"[\u4e00-\u9fff]", text or ""))
    en_letters = len(re.findall(r"[a-zA-Z]", text or ""))
    punctuation = len(re.findall(r"[^\w\s]", text or ""))
    wordy = cn_chars + en_letters

    hint = ""
    code = "ok"
    if kept_count == 0:
        if wordy < 10:
            code = "no-wordy-content"
            hint = ("文本里几乎没有可用的词语（数字/符号为主）。请粘贴正文段落。")
        elif raw_count == 0:
            code = "nothing-extracted"
            hint = (f"文本仅 {chars} 字，{wordy} 个实词字母/汉字，"
                    "统计类提取器无法产出候选。建议提供 ≥300 字的正文。")
        else:
            code = "all-dropped"
            hint = (f"提取到 {raw_count} 项但全部被过滤。可手动在下方补充术语。")
    elif kept_count < 5 and chars < 300:
        code = "short-text"
        hint = (f"文本较短（{chars} 字）：统计类提取器需要一定篇幅才能产出更多术语，"
                "建议提供 ≥300 字；也可在下方手动补充术语。")
    if dropped_count >= 3 and not hint:
        code = "ai-dropped-many"
        hint = (f"AI 语义复核剔除了 {dropped_count} 项（多为通用词或描述性短语）。"
                "如果其中包含你要的术语，点「加回候选」即可恢复。")

    return {
        "code": code,
        "hint": hint,
        "chars": chars,
        "cn_chars": cn_chars,
        "en_letters": en_letters,
        "non_word_chars": punctuation,
        "extracted": raw_count,
        "kept": kept_count,
        "dropped_by_ai": dropped_count,
    }


def _fill_candidate_translations(
    candidates: list[dict], client_settings: dict
) -> dict:
    """在检查点阶段就把候选术语的译文填好。

    没有译文的裁决表是没法裁决的：译员看不到「译后编辑工作量」该译成什么，
    就无从判断该采纳还是剔除。原先检查点返回 ``translation: ""``，前端那一列
    永远是「—」，等于让译员盲裁。这里复用 translate_terms 的四通道补齐
    （AI 批量 → 小批量重试 → 免费引擎 → 单条 AI），任何失败都只是让该行留空，
    绝不阻断检查点自身。
    """
    words = [c.get("word") or "" for c in candidates]
    words = [w for w in words if w.strip()]
    if not words:
        return {"used": False, "reason": "no candidates", "filled": 0, "total": 0}

    try:
        from termprep.services.translation_service import TranslationService

        svc = TranslationService(client_settings)
        svc.load_engines()
        joined = " ".join(words)
        src_lang = "zh" if re.search(r"[\u4e00-\u9fff]", joined) else "en"
        tgt_lang = "en" if src_lang == "zh" else "zh"
        results = svc.translate_terms(words, source_lang=src_lang, target_lang=tgt_lang)
    except Exception as e:
        return {"used": False, "reason": f"{type(e).__name__}: {e}",
                "filled": 0, "total": len(words)}

    by_term = {r.get("term"): r for r in results or []}
    filled = 0
    for c in candidates:
        r = by_term.get(c.get("word"))
        if not r:
            continue
        trans = (r.get("translation") or "").strip()
        if not trans:
            continue
        c["translation"] = trans
        c["translation_engine"] = r.get("engine") or ""
        c["translation_confidence"] = r.get("confidence") or 0.0
        filled += 1

    return {
        "used": True,
        "filled": filled,
        "total": len(words),
        "missing": len(words) - filled,
    }


def _apply_adjudication(
    items: list[dict],
    session_terms: list[dict],
    adopted_from_log: dict[str, str],
    explicit_adopted: dict[str, str] | None,
    client_settings: dict,
) -> tuple[list, list[dict], dict]:
    """把译员裁决转换为 (术语列表, 术语→译文, 统计)。

    规则：
      * rejected  → 丢弃，绝不入库
      * edited    → 用 ``edited_word`` / ``edited_translation`` 作为最终值
      * added     → 追加（不要求 admin token，走裁决日志而非 /api/term/add）
      * merge_into→ 源条目并入目标条目（只保留一条，频次相加）

    译文优先级：edited_translation → 前端显式采用 → 裁决日志里「采用」的检索结果
    → 会话原始候选译文 → 引擎补齐。
    """
    from termprep.extractor import TermEntry

    by_word = {t.get("word", ""): t for t in (session_terms or [])}
    explicit_adopted = explicit_adopted or {}

    rejected: list[str] = []
    merge_map: dict[str, str] = {}
    rows: dict[str, dict] = {}
    order: list[str] = []

    for it in items or []:
        word = (it.get("word") or "").strip()
        if not word:
            continue
        verdict = (it.get("verdict") or "accepted").lower()
        merge_into = (it.get("merge_into") or "").strip()

        # 合并优先于其它裁决：只要指向了别的条目就并过去
        if merge_into and merge_into != word:
            merge_map[word] = merge_into
            continue
        if verdict == "rejected":
            rejected.append(word)
            continue
        if verdict not in ("accepted", "edited", "added"):
            verdict = "accepted"

        base = by_word.get(word, {})
        new_word = (it.get("edited_word") or "").strip() if verdict == "edited" else word
        new_word = new_word or word

        trans = (it.get("edited_translation") or "").strip()
        if not trans:
            trans = (explicit_adopted.get(word) or "").strip()
        if not trans:
            trans = (adopted_from_log.get(word) or "").strip()
        if not trans:
            trans = (base.get("translation") or "").strip()

        rows[new_word] = {
            "term": new_word,
            "source_word": word,
            "translation": trans,
            "freq": it.get("freq") if it.get("freq") is not None else base.get("freq", 1),
            "type": (it.get("type") or base.get("type")
                     or ("added" if verdict == "added" else "")),
            "score": it.get("score") if it.get("score") is not None else base.get("score"),
            "verdict": verdict,
            "note": it.get("note") or "",
        }
        if new_word not in order:
            order.append(new_word)

    # ── 合并 ──
    for source, target in merge_map.items():
        src_row = rows.pop(source, None)
        tgt = rows.get(target)
        if tgt is None:
            # 目标被剔除或不存在 → 保留源条目，避免静默丢术语
            base = by_word.get(source, {})
            rows[source] = {
                "term": source,
                "source_word": source,
                "translation": (src_row or {}).get("translation") or (base.get("translation") or ""),
                "freq": base.get("freq", 1),
                "type": base.get("type", ""),
                "score": base.get("score"),
                "verdict": "accepted",
                "note": f"合并目标 {target} 不可用，保留本条",
            }
            if source not in order:
                order.append(source)
            continue
        tgt["freq"] = int(tgt.get("freq") or 1) + int(
            (src_row or {}).get("freq") or by_word.get(source, {}).get("freq") or 1
        )
        tgt["merged_from"] = list(tgt.get("merged_from") or []) + [source]

    # ── 缺译文的用引擎补齐（4 通道补齐已实现于 translate_terms）──
    missing = [w for w in order if w in rows and not rows[w]["translation"]]
    if missing:
        try:
            from termprep.services.translation_service import TranslationService

            svc = TranslationService(client_settings)
            svc.load_engines()
            joined = " ".join(rows[w]["term"] for w in missing)
            src_lang = "zh" if re.search(r"[\u4e00-\u9fff]", joined) else "en"
            tgt_lang = "en" if src_lang == "zh" else "zh"
            for r in svc.translate_terms(missing, source_lang=src_lang, target_lang=tgt_lang):
                w = r.get("term")
                if w in rows and r.get("translation"):
                    rows[w]["translation"] = r["translation"]
                    rows[w]["translation_engine"] = r.get("engine", "")
        except Exception:
            pass

    terms: list = []
    glossary: list[dict] = []
    for w in order:
        row = rows.get(w)
        if row is None:
            continue
        terms.append(TermEntry(
            term=row["term"],
            frequency=int(row.get("freq") or 1),
            score=float(row.get("score") or 0.0),
            word_type=row.get("type") or "verdict",
        ))
        glossary.append({
            "term": row["term"],
            "translation": row["translation"] or "[未找到翻译]",
            "freq": int(row.get("freq") or 1),
            "type": row.get("type") or "",
            "verdict": row["verdict"],
            "merged_from": row.get("merged_from") or [],
        })

    stats = {
        "input_items": len(items or []),
        "rejected": rejected,
        "merged": [{"from": s, "into": t} for s, t in merge_map.items()],
        "kept": len(terms),
        "untranslated": sum(1 for g in glossary if g["translation"] == "[未找到翻译]"),
    }
    return terms, glossary, stats


def _hitl_status() -> dict:
    """Where checkpoint sessions are kept + a live write→read self-test.

    The self-test is deliberate: on a live Space we cannot read the server logs,
    so a failing session store must be visible through the API rather than
    surfacing as a mysterious "session not found" on resume.
    """
    try:
        return {
            "backend": f"sqlite:{_hitl_store.backend()}",
            "sessions": _hitl_store.count(),
            "ttl_seconds": _HITL_TTL,
            "shared_across_workers": True,
            "local_cache_entries": len(_HITL_LOCAL_CACHE),
            "self_test": _hitl_store.self_test(),
            "last_error": _hitl_store.last_error(),
        }
    except Exception as e:
        return {"backend": "unavailable", "error": f"{type(e).__name__}: {e}",
                "shared_across_workers": False,
                "last_error": _hitl_store.last_error() if hasattr(_hitl_store, "last_error") else ""}


def _storage_status() -> dict:
    """Report whether runtime data will survive a container rebuild (§5.4.1).
    Container-local SQLite is wiped on every rebuild, so an operator who *thinks*
    they configured persistence (but whose path is not writable) would silently
    lose the case data. Surfacing this in /api/health makes that visible.
    """
    try:
        from termprep import storage as _storage

        return {
            "data_dir": _storage.data_dir(),
            "persistent": _storage.is_persistent(),
            "note": _storage.resolution_note(),
            "warning": (
                ""
                if _storage.is_persistent()
                else "数据存在容器内，重建后会丢失；请设置 TERMPREP_DATA_DIR 指向持久目录，"
                     "并定期用 /api/cases/bundle 导出个案数据包"
            ),
        }
    except Exception as e:
        return {"persistent": False, "error": f"{type(e).__name__}: {e}"}


# ── App factory ──────────────────────────────────────────────────

def create_app() -> FastAPI:
    app = FastAPI(title="TermPrep", version=APP_VERSION)

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

    # GZip — compress HTML/JSON responses (big win for the ~120KB single-file frontend)
    app.add_middleware(GZipMiddleware, minimum_size=500)

    # §5.2 — hard request-body cap: refuse oversized uploads before buffering them
    @app.middleware("http")
    async def _limit_request_body(request: Request, call_next):
        declared = request.headers.get("content-length")
        if declared:
            try:
                too_big = int(declared) > MAX_REQUEST_BYTES
            except ValueError:
                too_big = False
            if too_big:
                limit_mb = MAX_REQUEST_BYTES // (1024 * 1024)
                return JSONResponse(
                    status_code=413,
                    content={"detail": f"请求体过大，上限 {limit_mb} MB"},
                )
        return await call_next(request)

    # ── API Routes ──

    @app.get("/api/sources")
    def api_sources():
        srcs = get_available_sources()
        return {"sources": [
            {"name": s.name, "available": s.available}
            for s in srcs
        ]}

    @app.get("/api/health")
    def api_health():
        """§5.10 — service self-check.

        Lets readers / reviewers confirm the deployment is alive, and gives the
        demo a pre-flight check. Reports the version, engine health and term-base
        size. Read-only and safe to call anonymously.
        """
        engines: list[dict] = []
        try:
            svc = TranslationService(termprep_settings.to_dict())
            svc.load_engines()
            engines = [
                {"name": e["name"], "available": e["available"], "health": e["health"]}
                for e in svc.list_available_engines()
            ]
        except Exception:
            engines = []

        db_info: dict = {"name": "terms", "total_terms": 0}
        try:
            stats = TermDB().get_stats()
            db_info = {
                "name": stats.get("name", "terms"),
                "total_terms": stats.get("total_terms", 0),
            }
        except Exception:
            pass

        return {
            "ok": True,
            "version": APP_VERSION,
            "engines": engines,
            "db": db_info,
            # Boolean only — never the token itself (§1.2).
            "admin_token_configured": admin_token_configured(),
            # §5.4.1 — 数据留存是否真的配置生效（容器重建会不会丢数据）
            "storage": _storage_status(),
            # 检查点会话的存储后端（多 worker 共享；排查 resume 失败时看这里）
            "hitl": _hitl_status(),
        }

    # ════════════════════════════════════════════════════════════════
    #  Decision log (§5.4.1 data persistence + §5.11 intervention export)
    # ════════════════════════════════════════════════════════════════
    # These routes are intentionally NOT behind require_admin: the whole point
    # of §5.11 is that a reviewer can mark 「系统建议 / 译员裁决」intervention
    # points during a demo. The log is append-only, session-scoped and bounded
    # (decision_log.MAX_ROWS), so it cannot be used to destroy anything.

    @app.post("/api/decisions/record")
    def api_decisions_record(data: DecisionRecordIn):
        """Persist 系统建议 / 译员裁决 pairs for the current session.

        Turns the paper's hand-counted chain (30 suggested → 9 deleted → 10 added
        → 3 merged → 28 kept) into a recorded, reproducible artefact.
        """
        items = [it.model_dump() for it in data.items]
        if not items:
            raise HTTPException(400, "no decision items")
        recorded = decision_log.record_many(
            session_id=data.session_id,
            step=data.step,
            input_digest=data.input_digest,
            items=items,
            input_text=data.input_text,
            meta=data.parameters,
        )
        return {
            "recorded": recorded,
            "session_id": data.session_id,
            "summary": decision_log.summary(data.session_id or None),
        }

    @app.get("/api/decisions/list")
    def api_decisions_list(session_id: str = Query(""), limit: int = Query(500)):
        return {"decisions": decision_log.list_decisions(session_id or None, limit)}

    @app.get("/api/decisions/summary")
    def api_decisions_summary(session_id: str = Query("")):
        """Aggregate counts the methodology section can cite directly."""
        return decision_log.summary(session_id or None)

    @app.get("/api/decisions/export")
    def api_decisions_export(session_id: str = Query(""), format: str = Query("csv")):
        """Download the decision log — ready to become a paper appendix.

        CSV columns: 时间戳 / 会话 / 步骤 / 输入文本摘要 / 术语 / 系统建议 /
        译员裁决 / 系统评分 / 备注 (§5.4.1 + §5.11 item 3).
        """
        session = session_id or None
        if (format or "csv").lower() == "json":
            return JSONResponse(
                content=decision_log.export_json(session),
                headers={
                    "Content-Disposition": 'attachment; filename="termprep-decisions.json"'
                },
            )
        # Leading BOM so Excel opens the UTF-8 Chinese headers correctly
        return Response(
            content="\ufeff" + decision_log.export_csv(session),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": 'attachment; filename="termprep-decisions.csv"'
            },
        )

    # ════════════════════════════════════════════════════════════════
    #  Case bundle (§5.4.1 — 可复现的个案数据包)
    # ════════════════════════════════════════════════════════════════
    # 容器本地 SQLite 在重建时会清空，所以「可复现」不能依赖容器留存，
    # 必须产出**作者自己保存的**自描述数据包。这些端点只读 + 落盘导出文件，
    # 不修改术语库/裁决数据。

    @app.get("/api/cases/sessions")
    def api_cases_sessions(limit: int = Query(100)):
        """已记录输入文本（可导出数据包）的会话列表。"""
        return {"sessions": decision_log.list_sessions(limit)}

    @app.get("/api/cases/bundle")
    def api_cases_bundle(
        session_id: str = Query(""),
        format: str = Query("json"),
        include_text: bool = Query(True),
        db_name: str = Query(""),
        recompute: bool = Query(True),
    ):
        """下载完整个案数据包（含输入原文、参数、重算术语、裁决、术语库、TM）。

        ``terms_recomputed`` 是用**存储的输入+参数重新跑**规则层得到的，
        用来让审稿人验证确定性复现；AI 阶段在 manifest 中明确标注为不确定。
        """
        from termprep import export_bundle

        bundle = export_bundle.build_case_bundle(
            session_id=session_id or None,
            db_name=db_name or None,
            include_text=include_text,
            app_version=APP_VERSION,
            recompute=recompute,
        )
        safe = export_bundle._safe_name(session_id or "all")
        if (format or "json").lower() == "zip":
            return Response(
                content=export_bundle.to_zip_bytes(bundle),
                media_type="application/zip",
                headers={
                    "Content-Disposition": f'attachment; filename="termprep-case-{safe}.zip"'
                },
            )
        return Response(
            content=export_bundle.to_json_bytes(bundle),
            media_type="application/json; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="termprep-case-{safe}.json"'
            },
        )

    @app.post("/api/cases/snapshot")
    def api_cases_snapshot(session_id: str = Query(""), label: str = Query("")):
        """把当前会话落成一个快照文件（仍会随容器重建消失——请及时下载）。"""
        from termprep import export_bundle

        bundle = export_bundle.build_case_bundle(
            session_id=session_id or None, app_version=APP_VERSION
        )
        snap = export_bundle.save_snapshot(bundle, label=label)
        return {
            "snapshot": snap["name"],
            "bytes": snap["bytes"],
            "warning": "容器重建后快照会丢失，请立即通过 /api/cases/snapshots/{name} 下载",
        }

    @app.get("/api/cases/snapshots")
    def api_cases_snapshots(limit: int = Query(50)):
        from termprep import export_bundle

        return {"snapshots": export_bundle.list_snapshots(limit), "dir": export_bundle.snapshot_dir()}

    @app.get("/api/cases/snapshots/{name}")
    def api_cases_snapshot_download(name: str):
        from termprep import export_bundle

        data = export_bundle.read_snapshot(name)
        if data is None:
            raise HTTPException(404, "snapshot not found")
        return Response(
            content=data,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{os.path.basename(name)}"'},
        )

    @app.post("/api/analyze")
    def api_analyze(data: AnalyzeIn):
        if not data.text.strip():
            raise HTTPException(400, "No input text")
        result = analyze(data.text)
        return {
            # ── existing fields: semantics unchanged ──
            "lang": result.lang,
            "char_count": result.chars_total,
            "word_count": result.words_en + result.words_cn,
            "domain": result.domain,
            "difficulty": result.difficulty,
            # §5.9d — natural-language summary. Previously this was
            # `str(AnalysisResult(...))`, i.e. a Python dataclass repr that the
            # front-end had to parse by hand.
            "summary": get_summary(result),
            # §5.9d — struct fields flattened onto the top level (additive).
            "words_cn": result.words_cn,
            "words_en": result.words_en,
            "chars_no_space": result.chars_no_space,
            "paragraphs": result.paragraphs,
            "sentences": result.sentences,
            # §5.8 — per-domain score breakdown so classification is auditable.
            "domain_scores": {k: round(v, 4) for k, v in result.domain_scores.items()},
        }

    @app.post("/api/extract")
    def api_extract(data: ExtractIn, request: Request):
        if not data.text.strip():
            raise HTTPException(400, "No input text")

        def _term_payload(word: str, freq, type_: str, translation: str | None = None) -> dict:
            """§5.7 / §5.9a — every term now carries a real score + a POS tag."""
            score, breakdown = explainable_score(
                word, data.text, frequency=freq or 0, word_type=type_ or ""
            )
            item = {
                "word": word,
                "freq": freq,
                "type": type_,
                # §5.7 — multi-factor score (used to be a constant 0.9 on the AI path)
                "score": score,
                # §5.9a — used to be hard-coded ""
                "pos": guess_pos(word),
                # §5.7 — auditable decomposition: which factor drove the decision
                "score_breakdown": breakdown,
            }
            if translation is not None:
                item["translation"] = translation
            return item

        # Try AI-enhanced extraction if client provided AI config
        client_settings = _ai_settings_from_request(request)
        if client_settings.get("ai_api_key"):
            try:
                orch = _get_orchestrator(request)
                glossary = orch.run_term_agent(text=data.text, project_name="browser", top_n=data.top_n)
                return {
                    "terms": [
                        _term_payload(
                            t.source_term,
                            t.frequency,
                            t.term_type or "ai",
                            t.target_translation or "",
                        )
                        for t in glossary.terms
                    ],
                    "ai_used": True,
                    # keep the response shape identical to the rule-based path
                    "rejected": [],
                    "verification": {
                        "used": False,
                        "reason": "AI extraction path (candidates are already AI-produced)",
                        "checked": 0,
                        "dropped": 0,
                    },
                }
            except Exception:
                pass
        terms = extract(data.text, top_n=data.top_n)
        # ── P4 — AI 复核：一次批量调用，剔掉"形态合法但非术语"的描述性短语 ──
        # 规则层抓不住「高校翻译硕士课程」这类短语，也分辨不出「术语管理过程」
        # 只是「术语管理」的冗长形式；这一步靠语义判断。
        rejected: list[dict] = []
        verification: dict = {"used": False, "reason": "AI not configured"}
        if client_settings.get("ai_api_key"):
            try:
                from termprep.services.term_verifier import verify_terms, normalise_term

                decisions, verification = verify_terms(
                    [t.term for t in terms],
                    context=data.text,
                    settings=client_settings,
                )
                if decisions:
                    kept: list = []
                    for t in terms:
                        d = decisions.get(normalise_term(t.term))
                        # 仅在"明确判为非术语 + 原样匹配"时丢弃（宁可漏删，不可错删）
                        if d is not None and d.get("is_term") is False:
                            rejected.append({
                                "word": t.term,
                                "type": d.get("type", ""),
                                "reason": d.get("reason", ""),
                            })
                        else:
                            kept.append(t)
                    terms = kept
            except Exception as e:  # 复核绝不影响主流程
                verification = {"used": False, "reason": f"verifier error: {type(e).__name__}"}

        return {
            "terms": [
                _term_payload(t.term, t.frequency, t.word_type or "word")
                for t in terms
            ],
            # P5 — 可观测性：让译员看得见系统丢弃了什么、为什么
            "rejected": rejected,
            "verification": verification,
        }

    @app.post("/api/search")
    def api_search(data: SearchIn, request: Request):
        if not data.term.strip():
            raise HTTPException(400, "No search term")

        client_settings = _ai_settings_from_request(request)
        ai_available = bool(client_settings.get("ai_api_key"))

        def _ai_lookup() -> list[dict]:
            """§5.5 — the AI engine is one source among several, never a shortcut.

            Asks for a dictionary-style entry (translation + POS + gloss) so the
            result carries real 词汇级信息 instead of echoing the headword the way
            the previous implementation did (``definition = word``).
            """
            from termprep.engines.ai_translator import AITranslator

            engine = AITranslator(client_settings)
            prompt = (
                f"Look up the term: {data.term}\n"
                "Return JSON only:\n"
                '{"translation": "...", "pos": "...", "definition": "...", "example": "..."}\n'
                "`definition` must be a short explanatory gloss, never a copy of the term."
            )
            raw = engine._chat(
                prompt,
                system_prompt="You are a bilingual terminology dictionary. Return valid JSON only.",
            )
            if not raw:
                return []

            import json as _json

            text = raw.strip()
            for fence in ("```json", "```"):
                if text.startswith(fence):
                    text = text[len(fence):]
            text = text.strip().strip("`").strip()
            try:
                obj = _json.loads(text)
            except Exception:
                return []
            if not isinstance(obj, dict):
                return []

            word = str(obj.get("translation") or "").strip()
            if not word:
                return []

            parts: list[str] = []
            if obj.get("pos"):
                parts.append(f"[{str(obj['pos']).strip()}]")
            if obj.get("definition"):
                parts.append(str(obj["definition"]).strip())
            if obj.get("example"):
                parts.append(f"例：{str(obj['example']).strip()}")
            definition = " ".join(parts).strip()
            # never echo the headword back as its own definition
            if definition.lower() == word.lower():
                definition = ""

            return [{
                "query": data.term,
                "word": word,
                "type": "translation",
                "score": 0.90,
                "definition": definition,
            }]

        # §5.5 — concurrent multi-source aggregation (dictionary + Wikipedia + AI)
        payload = search_multi(
            data.term,
            limit=data.limit,
            ai_callable=_ai_lookup if ai_available else None,
        )
        contributed = payload.get("sources_with_results", [])
        return {
            "results": payload["results"],
            "term": data.term,
            # existing field, now truthful: AI ran as one of several sources
            "ai_used": "ai" in contributed,
            # new, additive: which sources actually contributed
            "sources_used": payload["sources_used"],
            "sources_failed": payload["sources_failed"],
        }

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
        # §5.9c — cut-off for TM lookups: entries written during this request
        # (including the auto-store below) must never be reported as matches.
        _tm_cutoff = time.time()
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
                "tm_matches": tm_search(data.text, result.source_lang, result.target_lang, result.domain, limit=3, exclude_after=_tm_cutoff),
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

    @app.post("/api/translate/upload-test", dependencies=[Depends(require_admin)])
    async def api_translate_upload_test(request: Request):
        """Minimal test endpoint for multipart upload diagnosis."""
        try:
            form = await request.form()
            file = form.get("file")
            if not file:
                return {"error": "no file field", "form_keys": list(form.keys())}
            content = await file.read()
            return {
                "filename": getattr(file, 'filename', 'unknown'),
                "size": len(content),
                "content_type": getattr(file, 'content_type', 'unknown'),
                "ok": True,
            }
        except Exception as e:
            import traceback
            return {"error": str(e), "traceback": traceback.format_exc()}

    @app.post("/api/translate/upload", dependencies=[Depends(require_admin)])
    async def api_translate_upload(request: Request):
        import sys, traceback as _tb, tempfile
        _step = "start"
        try:
            _step = "parsing_form"
            form = await request.form()
            _step = "got_form"
            file = form.get("file")
            if not file:
                return {"error": "no file field", "form_keys": list(form.keys())}

            _step = "reading_params"
            domain = str(form.get("domain", "general"))
            target_lang = str(form.get("target_lang", "auto"))
            use_rag_str = str(form.get("use_rag", "false")).lower()
            use_rag = use_rag_str in ("true", "1", "yes", "on")

            _step = "validating_upload"
            # §5.2 — reject oversized bodies before buffering, then whitelist +
            # size-check the file and sanitise its name (path-traversal safe).
            precheck_content_length(request.headers.get("content-length"))
            content = await file.read()
            safe_name = validate_upload(getattr(file, 'filename', ''), len(content))

            _step = "reading_file"
            suffix = os.path.splitext(safe_name)[1].lower()
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(content)
                tmp_path = tmp.name

            _step = "parsing_file"
            text = parse_file(tmp_path)
            os.unlink(tmp_path)

            if not text.strip():
                return {"error": "文件内容为空"}

            # 安全截断：防止长文件导致 AI 翻译超时（HF Space 代理超时约 30-60s）
            MAX_CHARS = 3000
            if len(text) > MAX_CHARS:
                truncated = text[:MAX_CHARS]
                warning = f"文件过长 ({len(text)} 字符)，已截断到前 {MAX_CHARS} 字符进行翻译。"
            else:
                truncated = text
                warning = ""

            _step = "getting_settings"
            client_settings = _ai_settings_from_request(request)
            ai_api_key = client_settings.get("ai_api_key")
            
            if ai_api_key:
                _step = "ai_path"
                try:
                    from termprep.agents.orchestrator import AgentOrchestrator
                    from termprep.agents.schemas import Glossary
                    orch = AgentOrchestrator(client_settings)
                    glossary = orch.run_term_agent(text=truncated, project_name="upload", top_n=20)
                    trans = orch.run_translation_agent(
                        text=truncated,
                        glossary=glossary,
                        domain=domain,
                        engine="ai",
                        use_rag=use_rag,
                    )
                    return {
                        "filename": safe_name,
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
                        "errors": trans.errors + ([warning] if warning else []),
                    }
                except Exception as ai_err:
                    error_detail = f"AI 翻译失败: {type(ai_err).__name__}: {ai_err}"
                    result = translate_text(text=text, domain=domain, target_lang=target_lang)
                    return {
                        "filename": safe_name,
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

            _step = "free_path"
            result = translate_text(text=text, domain=domain, target_lang=target_lang)
            return {
                "filename": safe_name,
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
        except HTTPException:
            # §5.2 — let validation failures keep their real 4xx status code
            # instead of being flattened into the 200-with-error envelope.
            raise
        except Exception as e:
            tb_str = _tb.format_exc()
            return {
                "error": f"服务器内部错误 [{_step}]: {type(e).__name__}: {e}",
                "traceback": tb_str,
                "error_type": type(e).__name__,
                "status": 500,
            }

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

    @app.post("/api/term/add", dependencies=[Depends(require_admin)])
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

    @app.post("/api/db/init", dependencies=[Depends(require_admin)])
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
                use_ai=data.use_ai, disable_ai=getattr(data, "disable_ai", False),
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

    # ════════════════════════════════════════════════════════════════
    #  Pipeline v2: Templates + SSE Progress
    # ════════════════════════════════════════════════════════════════

    @app.get("/api/pipeline/templates")
    def api_pipeline_templates():
        """List all available pipeline templates."""
        return {"templates": list_templates()}

    @app.post("/api/pipeline/v2")
    def api_pipeline_v2(data: PipelineRunIn, request: Request):
        """Run pipeline with template support and optional SSE progress streaming."""
        if not data.text.strip():
            raise HTTPException(400, "No input text")
        try:
            pipeline_id = str(uuid.uuid4())
            client_settings = _ai_settings_from_request(request)

            # Run translation analysis
            from termprep.analyzer import analyze
            analysis_result = analyze(data.text)
            ta = analyze_translation_difficulty(
                source_text=data.text,
                lang=analysis_result.lang,
                domain=analysis_result.domain,
            )

            # Make the selected template drive execution (it used to be cosmetic).
            template = get_template(data.template) or get_template("full-pipeline") or {}
            selected = [s.get("name", "") for s in (template.get("steps") or [])]
            if data.steps:
                selected = [
                    (s.name if hasattr(s, "name") else str(s)) for s in data.steps
                ]
            step_keys = _template_step_keys(selected)
            # The full template declares an export step — actually produce files
            # for it instead of silently skipping it.
            export_formats = ["csv", "json"] if "export" in selected else None

            # 真正驱动 SSE：此前一步都没推事件，/api/pipeline/events 只会发 keepalive
            # （这就是它作为「死功能」的根因）。
            def _emit(name: str, status: str, detail: str) -> None:
                _push_pipeline_event(pipeline_id, "step", {
                    "name": name, "status": status, "detail": detail,
                })

            # Build result
            result = run_pipeline(
                text=data.text,
                project_name=data.project_name,
                top_n=data.top_n,
                use_ai=data.use_ai, disable_ai=getattr(data, "disable_ai", False),
                ai_engine=data.ai_engine,
                translate_mode=data.translate_mode,
                use_rag=data.use_rag,
                settings=client_settings,
                steps=step_keys,
                export_formats=export_formats,
                pipeline_id=pipeline_id,
                on_event=_emit,
            )
            _push_pipeline_event(pipeline_id, "pipeline_done", {"pipeline_id": pipeline_id})

            # Read report
            report_text = ""
            if result.report_path and os.path.isfile(result.report_path):
                try:
                    with open(result.report_path, encoding="utf-8") as f:
                        report_text = f.read()
                except Exception:
                    pass

            # ══════════════════════════════════════════════════════════
            # §5.4.1 — 把本次运行落成「可复现的个案数据包」
            # ══════════════════════════════════════════════════════════
            # 容器本地库会随重建清空，所以这里主动把（输入原文 + 参数 + 结果）
            # 记进 session 并保存一份快照；同时返回一个可直接下载的 URL。
            # 注意：只记录 provider/model 名称，绝不记录任何 key。
            case_bundle_info: dict = {
                "session_id": pipeline_id,
                "download_url": "",
                "snapshot": "",
                "auto_saved": False,
            }
            try:
                from termprep import export_bundle

                decision_log.remember_input(
                    pipeline_id,
                    data.text,
                    meta={
                        "top_n": data.top_n,
                        "template": data.template,
                        "translate_mode": data.translate_mode,
                        "use_ai": bool(data.use_ai),
                        "use_rag": bool(data.use_rag),
                        "steps": step_keys,
                        "domain": analysis_result.domain,
                        "lang": analysis_result.lang,
                        "ai_provider": client_settings.get("ai_provider") or "",
                        "ai_model": client_settings.get("ai_model") or "",
                    },
                )
                bundle = export_bundle.build_case_bundle(
                    session_id=pipeline_id,
                    include_text=True,
                    app_version=APP_VERSION,
                    extra={
                        "analysis": {
                            "lang": analysis_result.lang,
                            "domain": analysis_result.domain,
                            "difficulty": analysis_result.difficulty,
                            "chars": analysis_result.chars_total,
                            "words_cn": analysis_result.words_cn,
                            "words_en": analysis_result.words_en,
                            "domain_scores": {
                                k: round(v, 4) for k, v in analysis_result.domain_scores.items()
                            },
                        },
                        "pipeline": {
                            "template": data.template,
                            "steps": selected,
                            "step_status": result.step_status,
                            "terms_count": len(result.terms),
                            "glossary_total": len(result.glossary),
                            "termbase_terms": result.termbase_terms,
                            "full_translation_chars": len(result.full_translation or ""),
                            "ai_used": result.ai_used,
                            "duration": round(result.duration, 2),
                            "errors": result.errors,
                            "exports": result.exports,
                        },
                    },
                )
                snap = export_bundle.save_snapshot(bundle, label="pipeline")
                case_bundle_info["snapshot"] = snap["name"]
                case_bundle_info["auto_saved"] = True
                case_bundle_info["download_url"] = (
                    f"/api/cases/bundle?session_id={pipeline_id}&format=zip"
                )
            except Exception as e:
                case_bundle_info["error"] = f"{type(e).__name__}: {e}"

            return {
                "pipeline_id": pipeline_id,
                "template": data.template,
                # what the UI should render, resolved from the template
                "steps": selected,
                # what actually happened, per step (ok / skipped / failed + why)
                "step_status": result.step_status,
                "lang": result.analysis.lang if result.analysis else "",
                "domain": result.analysis.domain if result.analysis else "",
                "terms": [{"word": t.term, "freq": t.frequency, "type": t.word_type or "word", "score": round(t.score, 3)} for t in result.terms],
                "terms_count": len(result.terms),
                "glossary": result.glossary,
                "glossary_total": len(result.glossary),
                "glossary_untranslated": sum(
                    1 for g in result.glossary
                    if not g.get("translation") or g.get("translation") == "[未找到翻译]"
                ),
                "full_translation": result.full_translation,
                "termbase_terms": result.termbase_terms,
                "duration": round(result.duration, 1),
                "errors": result.errors,
                "report": report_text,
                "report_path": result.report_path or "",
                "exports": result.exports,
                "ai_used": result.ai_used,
                "translate_mode": result.translate_mode,
                "segments": result.segments,
                "translation_analysis": ta,
                # §5.4.1 — 本次运行已落成一个可下载的个案数据包
                "case": case_bundle_info,
            }
        except Exception as e:
            raise HTTPException(500, str(e))

    # ════════════════════════════════════════════════════════════════
    #  Human-in-the-loop 流水线（检查点模式）
    # ════════════════════════════════════════════════════════════════
    # 与 /api/pipeline、/api/pipeline/v2 的「全自动模式」并存：
    #   start  → 只跑 analyze + extract，返回候选术语与 session_id
    #   （译员在页面上裁决：采纳/剔除/修正/合并/新增）
    #   resume → 按裁决过滤/修正/合并后，继续 glossary → full_translate
    #            → termbase → report → export
    # 会话仅存内存（TTL 1h），结构与 _pipeline_events 一致。

    @app.post("/api/pipeline/start")
    def api_pipeline_start(data: PipelineStartIn, request: Request):
        """HITL 检查点 1/2：分析 + 提取，返回候选术语供译员裁决。"""
        if not data.text.strip():
            raise HTTPException(400, "No input text")

        client_settings = _ai_settings_from_request(request)
        session_id = f"hitl-{uuid.uuid4().hex[:12]}"

        analysis_result = analyze(data.text)
        raw_terms = extract(data.text, top_n=data.top_n)

        # P4 AI 复核（可选）：与 /api/extract 一致，未配置 AI 时完全不改动候选
        verification: dict = {"used": False, "reason": "AI not configured"}
        rejected_by_ai: list[dict] = []
        kept_terms = raw_terms
        if client_settings.get("ai_api_key"):
            try:
                from termprep.services.term_verifier import normalise_term, verify_terms

                decisions, verification = verify_terms(
                    [t.term for t in raw_terms],
                    context=data.text,
                    settings=client_settings,
                )
                if decisions:
                    kept_terms, rejected_by_ai, fallback = _apply_term_verification(
                        raw_terms, decisions, normalise_term
                    )
                    if fallback:
                        # AI 想把整批都删掉 → 不采信，如实告诉用户
                        verification = dict(verification, fallback=fallback,
                                            dropped=0, note="AI 判定会清空全部候选，已保留原候选")
            except Exception as e:
                verification = {"used": False, "reason": f"verifier error: {type(e).__name__}"}

        candidates = []
        for t in kept_terms:
            score, breakdown = explainable_score(
                t.term, data.text, frequency=t.frequency, word_type=t.word_type or ""
            )
            candidates.append({
                "word": t.term,
                "freq": t.frequency,
                "type": t.word_type or "word",
                "score": score,
                "score_breakdown": breakdown,
                "pos": guess_pos(t.term),
                "translation": "",   # 供译员填写/修正
            })

        # 被 AI 剔除的项也补上分数与词性：前端要能一键「加回候选」，
        # 加回来以后排序/展示都得跟正常候选一致。
        for r in rejected_by_ai:
            try:
                score, breakdown = explainable_score(
                    r["word"], data.text, frequency=r.get("freq"),
                    word_type=r.get("type") or "",
                )
                r["score"] = score
                r["score_breakdown"] = breakdown
            except Exception:
                r["score"] = None
            r["pos"] = guess_pos(r["word"])

        diagnostics = _extraction_diagnostics(
            data.text, len(raw_terms), len(candidates), len(rejected_by_ai)
        )

        params = {
            "top_n": data.top_n,
            "project_name": data.project_name,
            "source_file": data.source_file,
            "domain_hint": data.domain_hint or analysis_result.domain,
            "domain": analysis_result.domain,
            "lang": analysis_result.lang,
            "mode": "hitl",
            "ai_provider": client_settings.get("ai_provider") or "",
            "ai_model": client_settings.get("ai_model") or "",
        }

        # 检查点阶段补齐候选译文（失败不阻断：该行留空，前端提示「补译」）
        prefill = ({"used": False, "reason": "disabled", "filled": 0,
                    "total": len(candidates)} if data.prefill_translations is False
                   else _fill_candidate_translations(candidates, client_settings))

        analysis_payload = {
            "lang": analysis_result.lang,
            "domain": analysis_result.domain,
            "difficulty": analysis_result.difficulty,
            "chars": analysis_result.chars_total,
            "words_cn": analysis_result.words_cn,
            "words_en": analysis_result.words_en,
            "domain_scores": {k: round(v, 4) for k, v in analysis_result.domain_scores.items()},
        }
        _hitl_clean()
        _hitl_save(session_id, {
            "session_id": session_id,
            "text": data.text,
            "project_name": data.project_name,
            "params": params,
            "analysis": analysis_payload,
            "terms": candidates,
            # 会话里也存诊断与 AI 剔除项：刷新页面后仍能解释"为什么这么少"、
            # 仍能一键把 AI 剔掉的项加回候选
            "diagnostics": diagnostics,
            "rejected": rejected_by_ai,
            "verification": verification,
        })
        # 存原文 + 参数：resume / 个案数据包都依赖它
        try:
            decision_log.remember_input(session_id, data.text, meta=params)
        except Exception:
            pass

        return {
            "session_id": session_id,
            "expires_in": _HITL_TTL,
            "project_name": data.project_name,
            "lang": analysis_result.lang,
            "domain": analysis_result.domain,
            "difficulty": analysis_result.difficulty,
            "analysis": analysis_payload,
            "terms": candidates,
            "terms_count": len(candidates),
            "verification": verification,
            "translation_prefill": prefill,
            "rejected": rejected_by_ai,
            "diagnostics": diagnostics,
            "next": f"POST /api/pipeline/{session_id}/resume 提交裁决后继续",
        }

    @app.get("/api/pipeline/{session_id}")
    def api_pipeline_session(session_id: str):
        """读取一个检查点会话（供页面刷新后恢复，不必重新提取）。"""
        try:
            sess = _hitl_get(session_id)
        except Exception as e:
            raise HTTPException(
                503,
                f"检查点存储暂时不可用（{type(e).__name__}）。请重试；"
                "若持续失败请查看 /api/health 的 hitl 区块。",
            )
        if not sess:
            raise HTTPException(
                404,
                "该检查点会话已过期或不存在（TTL 1 小时）。请在流水线页重新点"
                "「① 开始：分析与提取」，裁决后立即继续。",
            )
        return {
            "session_id": session_id,
            "expires_at": sess.get("expires_at"),
            "project_name": sess.get("project_name", ""),
            "params": sess.get("params", {}),
            "analysis": sess.get("analysis", {}),
            "terms": sess.get("terms", []),
            "terms_count": len(sess.get("terms") or []),
            "diagnostics": sess.get("diagnostics") or {},
            "rejected": sess.get("rejected") or [],
            "verification": sess.get("verification") or {},
            # 裁决后已跑完 ④→⑧ 的会话，刷新页面照样能看报告、能下载
            "result": sess.get("result") or None,
            "has_result": bool(sess.get("result")),
        }

    @app.post("/api/pipeline/{session_id}/resume")
    def api_pipeline_resume(session_id: str, data: PipelineResumeIn, request: Request):
        """HITL 检查点 2/2：按译员裁决继续执行后续步骤。"""
        try:
            sess = _hitl_get(session_id)
        except Exception as e:
            # 存储故障 ≠ 会话过期：必须区分开，否则会误导用户重新提取
            raise HTTPException(
                503,
                f"检查点存储暂时不可用（{type(e).__name__}: {e}）。请重试；"
                "若持续失败请查看 /api/health 的 hitl 区块。",
            )
        if not sess:
            raise HTTPException(
                404,
                "该检查点会话已过期或不存在（TTL 1 小时）。请在流水线页重新点「开始：分析与提取」，"
                "裁决后立即继续。",
            )

        client_settings = _ai_settings_from_request(request)
        items = [it.model_dump() for it in data.terms]

        adopted = _adopted_translations_from_log(session_id)
        terms, glossary, stats = _apply_adjudication(
            items, sess.get("terms") or [], adopted, data.adopted_translations, client_settings
        )

        # ── 整批裁决写入日志（不改变 /api/decisions/* 既有格式）──
        try:
            decision_log.record_many(
                session_id=session_id,
                step="adjudicate",
                input_digest=(sess.get("text") or "")[:80],
                items=[{
                    "term": it.get("word") or "",
                    "system_suggestion": it.get("edited_translation")
                    or (it.get("merge_into") and f"merge_into:{it['merge_into']}") or "",
                    "translator_verdict": ("merged" if (it.get("merge_into") or "").strip()
                                           and it["merge_into"] != it.get("word")
                                           else it.get("verdict") or "accepted"),
                    "score": it.get("score"),
                    "score_breakdown": it.get("score_breakdown"),
                    "note": it.get("note") or "",
                    "step": "adjudicate",
                } for it in items if (it.get("word") or "").strip()],
                input_text=sess.get("text") or "",
                meta=sess.get("params") or {},
                replace=True,
            )
        except Exception as e:
            pass  # 日志失败不应阻断流水线

        project = data.project_name or sess.get("project_name") or "Untitled"

        def _emit(name: str, status: str, detail: str) -> None:
            _push_pipeline_event(session_id, "step", {
                "name": name, "status": status, "detail": detail,
            })

        result = run_pipeline(
            text=sess.get("text") or "",
            project_name=project,
            top_n=data.top_n,
            use_ai=data.use_ai, disable_ai=getattr(data, "disable_ai", False),
            ai_engine=data.engine,
            translate_mode=data.translate_mode,
            use_rag=data.use_rag,
            settings=client_settings,
            # 只跑裁决之后的步骤；extract/glossary 由 preset_* 直接接管
            steps=["glossary", "full_translate", "termbase", "translate_analysis", "report", "export"],
            preset_terms=terms,
            preset_glossary=glossary,
            export_formats=data.export_formats or ["csv", "json"],
            pipeline_id=session_id,
            on_event=_emit,
        )
        _push_pipeline_event(session_id, "pipeline_done", {"session_id": session_id})

        # ── 落一个个案数据包快照（可复现）──
        case_bundle_info: dict = {"session_id": session_id, "download_url": "", "snapshot": "",
                                  "auto_saved": False}
        try:
            from termprep import export_bundle

            bundle = export_bundle.build_case_bundle(
                session_id=session_id,
                include_text=True,
                app_version=APP_VERSION,
                extra={
                    "analysis": sess.get("analysis") or {},
                    "pipeline": {
                        "mode": "hitl",
                        "step_status": result.step_status,
                        "adjudication": stats,
                        "glossary_total": len(result.glossary),
                        "termbase_terms": result.termbase_terms,
                        "full_translation_chars": len(result.full_translation or ""),
                        "errors": result.errors,
                    },
                },
            )
            snap = export_bundle.save_snapshot(bundle, label="hitl")
            case_bundle_info.update({
                "snapshot": snap["name"],
                "auto_saved": True,
                "download_url": f"/api/cases/bundle?session_id={session_id}&format=zip",
            })
        except Exception as e:
            case_bundle_info["error"] = f"{type(e).__name__}: {e}"

        payload = {
            "session_id": session_id,
            "mode": "hitl",
            "project_name": project,
            "adjudication": stats,
            "steps": result.step_status,
            "step_status": result.step_status,
            "glossary": result.glossary,
            "glossary_total": len(result.glossary),
            "terms": [{"word": t.term, "freq": t.frequency, "type": t.word_type or ""}
                      for t in result.terms],
            "termbase_name": result.termbase_name,
            "termbase_terms": result.termbase_terms,
            "full_translation": result.full_translation,
            "translation_qa": getattr(result, "translation_qa", {}) or {},
            "report": (open(result.report_path, encoding="utf-8").read()
                       if result.report_path and os.path.isfile(result.report_path) else ""),
            "report_path": result.report_path or "",
            "exports": result.exports,
            "errors": result.errors,
            "adopted_translations": adopted,
            "case": case_bundle_info,
        }

        # 结果必须留在会话里：否则一刷新页面，报告/导出就"消失"了——
        # 用户看到的不是"没算出来"，而是"算出来了却拿不到"。
        try:
            sess["result"] = payload
            _hitl_save(session_id, sess)
        except Exception:
            pass

        return payload

    @app.get("/api/pipeline/{session_id}/report")
    def api_pipeline_report(session_id: str):
        """下载检查点会话的报告（Markdown）。"""
        sess = _pipeline_session_or_404(session_id)
        res = sess.get("result") or {}
        text = res.get("report") or ""
        if not text:
            path = res.get("report_path") or ""
            if path and os.path.isfile(path):
                with open(path, encoding="utf-8") as f:
                    text = f.read()
        if not text:
            raise HTTPException(404, "本次会话尚未生成报告，请先完成裁决并继续流水线。")
        name = _download_name(sess, "report", "md")
        return Response(
            content=text.encode("utf-8"),
            media_type="text/markdown; charset=utf-8",
            headers=_attachment_headers(name),
        )

    @app.get("/api/pipeline/{session_id}/export/{fmt}")
    def api_pipeline_export(session_id: str, fmt: str):
        """下载检查点会话的导出文件（csv / json / glossary_json）。"""
        sess = _pipeline_session_or_404(session_id)
        res = sess.get("result") or {}
        exports = res.get("exports") or {}
        key = (fmt or "").strip().lower()
        allowed = {"csv", "json", "glossary_json"}
        if key not in allowed:
            raise HTTPException(400, f"不支持的导出格式：{fmt}（可选 {sorted(allowed)}）")
        path = exports.get(key) or ""
        if not path or not os.path.isfile(path):
            raise HTTPException(404, f"本次会话没有 {key} 导出文件，请先完成裁决并继续流水线。")
        ext = os.path.splitext(path)[1].lstrip(".").lower() or "txt"
        media = {"csv": "text/csv; charset=utf-8",
                 "json": "application/json; charset=utf-8"}.get(ext,
                                                                "application/octet-stream")
        if key == "glossary_json":
            ext = "glossary.json"
        with open(path, "rb") as f:
            body = f.read()
        return Response(
            content=body,
            media_type=media,
            headers=_attachment_headers(_download_name(sess, key, ext)),
        )

    @app.get("/api/pipeline/events/{pipeline_id}")
    async def api_pipeline_events(pipeline_id: str):
        """SSE endpoint: stream pipeline progress events."""
        event_queue: queue.Queue = queue.Queue()
        event_queue._created_at = time.time()
        _pipeline_events[pipeline_id] = event_queue

        async def event_generator():
            try:
                yield f"event: connected\ndata: {json.dumps({'pipeline_id': pipeline_id})}\n\n"
                while True:
                    try:
                        event = event_queue.get(timeout=30)
                        yield f"event: {event['event']}\ndata: {json.dumps(event['data'], ensure_ascii=False)}\n\n"
                        if event['event'] == 'pipeline_done':
                            break
                    except queue.Empty:
                        yield f": keepalive\n\n"
            except asyncio.CancelledError:
                pass
            finally:
                _pipeline_events.pop(pipeline_id, None)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # ── Serve frontend ──
    @app.get("/", response_class=HTMLResponse)
    def index_html(request: Request):
        index_path = os.path.join(STATIC_DIR, "index.html")
        if os.path.isfile(index_path):
            with open(index_path, encoding="utf-8") as f:
                content = f.read()
            # Content-based ETag: browser revalidates (304) but updates instantly on rebuild
            etag = '"' + hashlib.md5(content.encode("utf-8")).hexdigest() + '"'
            if request.headers.get("if-none-match") == etag:
                return Response(status_code=304)
            return HTMLResponse(
                content=content,
                headers={"ETag": etag, "Cache-Control": "no-cache"},
            )
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

    # ── Task API（同步实现；此前 submit 返回 task_id 而 status/execute 恒 501，是断头路）──
    # 任务存到模块级 _AGENT_TASKS，而不是 orchestrator 实例上：API 每个请求都会新建
    # orchestrator，实例内的 _tasks 拿不到上一次提交的任务，这正是原本只能 501 的原因。
    @app.post("/api/agents/task/submit")
    def api_agents_task_submit(data: AgentTaskSubmitIn, request: Request):
        """提交任务，返回可轮询的 task_id。"""
        _agent_tasks_clean()
        task_id = str(uuid.uuid4())
        _AGENT_TASKS[task_id] = {
            "task_id": task_id,
            "task_type": data.task_type,
            "payload": data.payload or {},
            "status": "pending",
            "result": None,
            "error": "",
            "created_at": time.time(),
            "finished_at": None,
        }
        return {"task_id": task_id, "status": "pending",
                "next": f"POST /api/agents/task/{task_id}/execute 同步执行"}

    @app.get("/api/agents/task/{task_id}")
    def api_agents_task_status(task_id: str):
        """轮询任务状态与结果。"""
        _agent_tasks_clean()
        task = _AGENT_TASKS.get(task_id)
        if not task:
            raise HTTPException(404, "task not found or expired (TTL 1h)")
        return task

    @app.post("/api/agents/task/{task_id}/execute")
    def api_agents_task_execute(task_id: str, request: Request):
        """同步执行任务并把结果写回（不再返回 501）。"""
        _agent_tasks_clean()
        task = _AGENT_TASKS.get(task_id)
        if not task:
            raise HTTPException(404, "task not found or expired (TTL 1h)")
        if task["status"] == "done":
            return task

        task["status"] = "running"
        try:
            # A fresh orchestrator per request, so replay the task into THIS
            # instance before executing it (its _tasks dict starts empty).
            orch = _get_orchestrator(request)
            local_id = orch.submit_task(task["task_type"], task["payload"])
            outcome = orch.execute_task(local_id)
            if outcome.get("status") == "completed":
                task["status"] = "done"
                task["result"] = outcome.get("result")
            else:
                task["status"] = "failed"
                task["error"] = "; ".join(outcome.get("errors") or []) or "execution failed"
        except Exception as e:
            task["status"] = "failed"
            task["error"] = f"{type(e).__name__}: {e}"
        task["finished_at"] = time.time()
        return task

    @app.get("/api/debug/env", dependencies=[Depends(require_admin)])
    def debug_env():
        """§5.3 — converged diagnostics: provider name + a single boolean.

        Deliberately does **not** expose the api key, the base URL or the model
        identifier (task book §1.2 / §5.3). The previous payload leaked the
        upstream provider endpoint via ``*_base_url``; that field is gone.
        Requires the admin token (§5.1).
        """
        env_provider = os.environ.get("TERMPREP_AI_PROVIDER", "")
        provider = termprep_settings.ai_provider or env_provider
        configured = bool(
            termprep_settings.ai_api_key or os.environ.get("TERMPREP_AI_API_KEY")
        )
        return {
            "ai_configured": configured,
            "provider": provider,
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

    @app.post("/api/rag/upload", dependencies=[Depends(require_admin)])
    async def api_rag_upload(
        request: Request,
        file: UploadFile = File(...),
        domain: str = Form("general"),
    ):
        """Upload a medical document and add it to the RAG vector store."""
        import tempfile
        import uuid

        try:
            # §5.2 — validate extension + size and sanitise the file name before
            # anything touches the filesystem (temp dir is outside the code dir).
            precheck_content_length(request.headers.get("content-length"))
            content = await file.read()
            safe_name = validate_upload(file.filename, len(content))

            suffix = os.path.splitext(safe_name)[1].lower()
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
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
                "filename": safe_name,
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
