"""Case bundle — a self-sufficient artefact that makes a case study reproducible.

Why this exists
---------------
The paper's methodology section reports a concrete chain — "system extracted 30
candidate terms → the translator deleted 9 false positives → added 10 missed
terms → merged/recategorised 3 → 28 kept" — but the data lived only in
container-local SQLite. Hugging Face containers are **ephemeral**, so every
rebuild wipes it and the case becomes impossible to reproduce or audit
(task book §5.4.1).

The durable answer cannot be "hope the container keeps the file"; it has to be
**an artefact the author keeps**. :func:`build_case_bundle` produces one
self-describing file containing everything a reader needs to re-run and verify
the case:

  manifest            format / generated_at / app_version / determinism notes
  input               exact source text + sha256 (the reproducibility anchor)
  parameters          top_n / domain / engine names / template actually used
  terms_recomputed    a fresh extraction from the stored input + parameters
  decisions           the 系统建议 / 译员裁决 log
  decision_summary    the counts the paper cites (suggested / rejected / added…)
  termbases           full termbase dumps
  tm                  translation-memory entries
  engines             engine availability snapshot

Reproducibility is *verifiable*: the rule-based extractor is deterministic given
``(input, top_n)``, so re-running from the bundle reproduces `terms_recomputed`.
AI-assisted stages are recorded as **observed values** and the manifest states
plainly that they are not deterministic.

Privacy: no API keys are ever included — only provider/model names.
"""

from __future__ import annotations

import csv
import io
import json
import os
import time
import zipfile
from typing import Any

from termprep import decision_log
from termprep.storage import data_dir

FORMAT = "termprep-case-bundle"
FORMAT_VERSION = 1
SNAPSHOT_DIRNAME = "exports"


# ── storage ──

def snapshot_dir() -> str:
    """Directory holding auto-saved case-bundle snapshots."""
    d = os.path.join(data_dir(), SNAPSHOT_DIRNAME)
    os.makedirs(d, exist_ok=True)
    return d


def _safe_name(s: str) -> str:
    out = "".join(c for c in (s or "") if c.isalnum() or c in "-_.")
    return (out[:64] or "case").strip("._") or "case"


def _stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S", time.localtime())


# ── collectors ──

def _collect_termbases(only: str | None = None) -> list[dict[str, Any]]:
    """Full dumps of every termbase (or just ``only``)."""
    try:
        from termprep.db import TermDB, list_termbases
        from termprep.exporter import _fetch_rows
    except Exception:
        return []

    out: list[dict[str, Any]] = []
    try:
        infos = list_termbases()
    except Exception:
        infos = []
    for info in infos:
        name = getattr(info, "name", "")
        if only and name != only:
            continue
        try:
            tdb = TermDB(db_name=name)
            rows = _fetch_rows(tdb)
        except Exception:
            continue
        out.append({
            "name": name,
            "domain": getattr(info, "domain", "") or "",
            "lang": getattr(info, "lang", "") or "",
            "total_terms": getattr(info, "total_terms", 0),
            "terms": rows,
        })
    if only and not out:
        try:
            tdb = TermDB(db_name=only)
            out.append({
                "name": only,
                "domain": getattr(tdb, "domain", "") or "",
                "lang": "",
                "total_terms": tdb.get_stats().get("total_terms", 0),
                "terms": _fetch_rows(tdb),
            })
        except Exception:
            pass
    return out


def _collect_tm(limit: int = 5000) -> list[dict[str, Any]]:
    try:
        from termprep import tm
        return tm.dump_all(limit=limit)
    except Exception:
        return []


def _collect_engines() -> list[dict[str, Any]]:
    try:
        from termprep.config import settings as termprep_settings
        from termprep.services.translation_service import TranslationService

        svc = TranslationService(termprep_settings.to_dict())
        svc.load_engines()
        return [
            {"name": e["name"], "available": e["available"], "health": e["health"]}
            for e in svc.list_available_engines()
        ]
    except Exception:
        return []


def _recompute_terms(text: str, top_n: int, domain: str = "") -> list[dict[str, Any]]:
    """Re-run the deterministic rule-based extractor on the stored input."""
    try:
        from termprep.extractor import explainable_score, extract, guess_pos

        out = []
        for t in extract(text, top_n=top_n):
            score, breakdown = explainable_score(
                t.term, text, frequency=t.frequency, word_type=t.word_type or ""
            )
            out.append({
                "term": t.term,
                "freq": t.frequency,
                "type": t.word_type or "",
                "pos": guess_pos(t.term),
                "score": score,
                "score_breakdown": breakdown,
            })
        return out
    except Exception:
        return []


# ── bundle ──

def build_case_bundle(
    session_id: str | None = None,
    db_name: str | None = None,
    include_text: bool = True,
    app_version: str = "",
    extra: dict[str, Any] | None = None,
    recompute: bool = True,
) -> dict[str, Any]:
    """Assemble a self-sufficient, reproducible case bundle.

    Args:
        session_id: Session whose input / rulings should be included.
        db_name: Restrict the termbase dump to one termbase.
        include_text: Include the full source text (set False to ship only its
            sha256, e.g. when the text is confidential).
        app_version: Reported application version.
        extra: Caller-supplied section (pipeline result, analysis, …).
        recompute: Re-run the rule-based extractor from the stored input so the
            bundle carries a verifiable reproduction, not just a claim.
    """
    record = decision_log.get_input(session_id) if session_id else None
    params = (record or {}).get("parameters") or {}
    source_text = (record or {}).get("text", "") or ""

    terms_recomputed: list[dict[str, Any]] = []
    if recompute and source_text:
        top_n = int(params.get("top_n") or 30)
        terms_recomputed = _recompute_terms(source_text, top_n, params.get("domain", ""))

    bundle: dict[str, Any] = {
        "manifest": {
            "format": FORMAT,
            "format_version": FORMAT_VERSION,
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "app_version": app_version,
            "session_id": session_id or "",
            "determinism": {
                "rule_based_extraction": (
                    "deterministic: identical for the same (input, top_n) "
                    "and the same app version"
                ),
                "ai_assisted_stages": (
                    "NOT deterministic (sampling temperature > 0); their outputs "
                    "are recorded here as observed values"
                ),
            },
            "privacy": "no API keys are included; only provider/model names",
            "sections": [
                "manifest", "input", "parameters", "analysis", "terms_recomputed",
                "decisions", "decision_summary", "termbases", "tm", "engines",
                "pipeline", "notes",
            ],
        },
        "input": {
            "sha256": (record or {}).get("sha256", ""),
            "chars": (record or {}).get("chars", 0),
            "timestamp": (record or {}).get("timestamp", ""),
            "text": source_text if include_text else "",
            "text_included": bool(include_text),
        },
        "parameters": params,
        "analysis": (extra or {}).get("analysis", {}),
        "terms_recomputed": terms_recomputed,
        "decisions": decision_log.list_decisions(session_id or None, limit=5000),
        "decision_summary": decision_log.summary(session_id or None),
        "termbases": _collect_termbases(db_name),
        "tm": _collect_tm(),
        "engines": _collect_engines(),
        "pipeline": (extra or {}).get("pipeline", {}),
        "notes": (extra or {}).get("notes", ""),
    }
    return bundle


# ── serialisation ──

def _decisions_csv(bundle: dict[str, Any]) -> str:
    buf = io.StringIO()
    cols = ["timestamp", "session_id", "step", "input_digest", "term",
            "system_suggestion", "translator_verdict", "score", "note"]
    writer = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    writer.writeheader()
    for r in bundle.get("decisions", []):
        writer.writerow({k: r.get(k, "") for k in cols})
    return buf.getvalue()


def _terms_csv(bundle: dict[str, Any]) -> str:
    buf = io.StringIO()
    cols = ["termbase", "id", "word", "translation", "type", "domain", "status", "lang"]
    writer = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    writer.writeheader()
    for tb in bundle.get("termbases", []):
        for row in tb.get("terms", []):
            writer.writerow({"termbase": tb.get("name", ""), **row})
    return buf.getvalue()


_README = """TermPrep 个案数据包（case bundle）
==================================

目的：让论文的个案数据「可复现、可审计」。容器本地数据库在重建时会清空，
本文件是你**下载留存**的证据产物。

文件说明
--------
case-bundle.json   完整数据包（本 README 之外的其余文件都可由它重建）
manifest.json      元信息：生成时间、版本、确定性说明、隐私说明
decisions.csv      系统建议 / 译员裁决逐条记录（论文附录可直接用）
terms.csv          术语库全量导出（含状态、领域、译文）
README.txt         本说明

如何复现
--------
1. 取 case-bundle.json 中的 input.text 与 parameters（top_n / domain）。
2. 用相同版本的 TermPrep 对同一文本执行：
       POST /api/extract  {"text": <input.text>, "top_n": <parameters.top_n>}
3. 得到的术语列表应与 case-bundle.json 中的 terms_recomputed 完全一致
   （规则层是确定性的）。

注意
----
* AI 参与的阶段（AI 术语翻译、全量翻译、AI 复核）**不确定**：
  相同输入可能得到不同输出，包内记录的是**当次观测值**。
* 本文件不含任何 API key，只记录 provider / model 名称。
"""


def to_json_bytes(bundle: dict[str, Any]) -> bytes:
    return json.dumps(bundle, ensure_ascii=False, indent=2).encode("utf-8")


def to_zip_bytes(bundle: dict[str, Any]) -> bytes:
    """Bundle + human-readable CSV/README companions in one archive."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("case-bundle.json", to_json_bytes(bundle).decode("utf-8"))
        zf.writestr("manifest.json",
                    json.dumps(bundle.get("manifest", {}), ensure_ascii=False, indent=2))
        zf.writestr("decisions.csv", _decisions_csv(bundle))
        zf.writestr("terms.csv", _terms_csv(bundle))
        zf.writestr("README.txt", _README)
    return buf.getvalue()


# ── snapshots (auto-saved, still ephemeral — the point is that you download them) ──

def save_snapshot(bundle: dict[str, Any], label: str = "") -> dict[str, Any]:
    """Persist a bundle into the snapshot directory and return its descriptor."""
    sid = _safe_name(bundle.get("manifest", {}).get("session_id") or "case")
    name = f"case-{sid}-{_stamp()}{('-' + _safe_name(label)) if label else ''}.zip"
    path = os.path.join(snapshot_dir(), name)
    with open(path, "wb") as fh:
        fh.write(to_zip_bytes(bundle))
    return {"name": name, "path": path, "bytes": os.path.getsize(path),
            "created_at": time.time()}


def list_snapshots(limit: int = 50) -> list[dict[str, Any]]:
    """List saved snapshots, newest first."""
    d = snapshot_dir()
    try:
        entries = [
            f for f in os.listdir(d) if f.startswith("case-") and f.endswith(".zip")
        ]
    except Exception:
        return []
    entries.sort(key=lambda f: os.path.getmtime(os.path.join(d, f)), reverse=True)
    out = []
    for name in entries[: max(1, int(limit))]:
        path = os.path.join(d, name)
        try:
            st = os.stat(path)
        except Exception:
            continue
        out.append({
            "name": name,
            "bytes": st.st_size,
            "created_at": st.st_mtime,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime)),
        })
    return out


def read_snapshot(name: str) -> bytes | None:
    """Read a saved snapshot by file name (path-traversal safe)."""
    base = os.path.basename(name or "")
    if not base.startswith("case-") or not base.endswith(".zip"):
        return None
    path = os.path.join(snapshot_dir(), base)
    if not os.path.isfile(path):
        return None
    with open(path, "rb") as fh:
        return fh.read()
