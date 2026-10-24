"""Acceptance test for the Human-in-the-loop pipeline (检查点模式).

Walks the full chain required by the task:

    /api/pipeline/start  →  剔除 2 项 · 修正 1 项 · 新增 1 项 · 合并 1 组
    /api/pipeline/{sid}/resume  →  检查**真实术语库**：
        被剔除项不存在 · 修正项为新值 · 合并为一条 · 新增项存在

Also verifies:
  * /api/decisions/export 的 CSV 能完整复算该链路
  * /openapi.json 正常生成
  * SSE 真的推送 step 进度（此前一步都没推，是死功能）
  * agent task 断头路已通（submit → status → execute）
  * 全自动模式 /api/pipeline/v2 未被破坏
"""

import csv
import glob
import io
import json
import os
import shutil
import sys

_TMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_hitl_tmp")
shutil.rmtree(_TMP, ignore_errors=True)
os.makedirs(_TMP, exist_ok=True)
os.environ["TERMPREP_DATA_DIR"] = _TMP
os.environ.setdefault("TERMPREP_ADMIN_TOKEN", "verify-token")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi.testclient import TestClient  # noqa: E402

from termprep.web import server as SRV  # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))


# ── 200 词英文摘要（翻译研究）──
ABSTRACT = (
    "This study investigates how terminology management affects post-editing effort in "
    "professional translation workflows. Drawing on a corpus of forty technical documents "
    "produced by twelve translators, we examine three related questions: whether machine "
    "translation quality assurance reduces revision time, whether translation memory "
    "increases lexical consistency, and how collaboration between translators and "
    "reviewers reshapes terminology decisions. Terminology constraints were injected into "
    "the machine translation engine to promote lexical consistency across segments. "
    "A qualitative methodology combining screen recording and retrospective interviews was "
    "adopted. The findings suggest that auditable terminology workflows substantially "
    "reduce post-editing effort, while automated quality assurance alone does not guarantee "
    "acceptable output. We argue that translation technology should support rather than "
    "replace translator judgement, and that multi-agent orchestration offers a practical "
    "framework for distributing terminology decisions across a workflow. The study "
    "contributes to research on translation quality assurance by documenting how "
    "terminology management interacts with revision practice in real production settings."
)
WORDS = len(ABSTRACT.split())

client = TestClient(SRV.create_app())

print(f"\n=== 0. 前置检查（摘要 {WORDS} 词 / 数据目录隔离）===")
check("摘要约 200 词", 150 <= WORDS <= 260, f"{WORDS} 词")
from termprep import storage as ST  # noqa: E402
check("数据目录隔离生效", os.path.abspath(ST.data_dir()) == os.path.abspath(_TMP), ST.data_dir())

print("\n=== 1. /api/pipeline/start（检查点 1：分析 + 提取）===")
r = client.post("/api/pipeline/start", json={
    "text": ABSTRACT, "project_name": "hitl-verify", "top_n": 20,
})
check("start 返回 200", r.status_code == 200, f"HTTP {r.status_code} {r.text[:120]}")
start = r.json()
sid = start.get("session_id", "")
terms = start.get("terms", [])
check("返回 session_id", bool(sid), sid)
check("返回候选术语 > 0", len(terms) > 0, f"n={len(terms)}")
check("候选含 score / score_breakdown / pos / freq",
      all({"score", "score_breakdown", "pos", "freq"} <= set(t) for t in terms),
      f"keys={sorted(terms[0]) if terms else []}")
check("返回 analysis（语言/领域）", bool(start.get("analysis", {}).get("lang")),
      f"{start.get('analysis', {}).get('lang')} / {start.get('analysis', {}).get('domain')}")
print(f"        候选: {[t['word'] for t in terms]}")

# ── 多 worker 回归：部署是 `gunicorn -w 2`，会话必须跨进程可见 ──
print("        [跨 worker] 用另一个 app 实例读取同一会话…")
client2 = TestClient(SRV.create_app())
r2 = client2.get(f"/api/pipeline/{sid}")
check("GET /api/pipeline/{sid} 可读取会话", r2.status_code == 200,
      f"HTTP {r2.status_code}")
check("另一个实例（模拟另一 worker）也能读到同一会话",
      r2.status_code == 200 and len(r2.json().get("terms", [])) == len(terms),
      f"n={len(r2.json().get('terms', [])) if r2.status_code == 200 else 'n/a'}")

if len(terms) < 6:
    print("  [SKIP] 候选不足 6 条，无法完成剔除2/修正1/新增1/合并1的编排")
    sys.exit(1)

words = [t["word"] for t in terms]
reject_a, reject_b = words[1], words[2]
edit_w = words[3]
merge_src, merge_tgt = words[4], words[5]
keep = words[0]
print(f"        编排: 剔除[{reject_a}, {reject_b}] 修正[{edit_w}] "
      f"合并[{merge_src}→{merge_tgt}] 新增[translator agency]")

print("\n=== 2. /api/pipeline/{sid}/resume（检查点 2：裁决后继续）===")
items = []
for t in terms:
    w = t["word"]
    if w in (reject_a, reject_b):
        items.append({"word": w, "verdict": "rejected"})
    elif w == edit_w:
        items.append({
            "word": w, "verdict": "edited",
            "edited_word": w + "-revised",
            "edited_translation": "REVISED-BY-TRANSLATOR",
        })
    elif w == merge_src:
        items.append({"word": w, "verdict": "accepted", "merge_into": merge_tgt})
    else:
        items.append({"word": w, "verdict": "accepted",
                      "edited_translation": "EN:" + w[:20]})
# 新增一条（译员补充）
items.append({
    "word": "translator agency", "verdict": "added",
    "edited_translation": "译者主体性", "type": "added",
})

# 先挂一个 SSE 队列，验证 step 事件真的被推送
import queue as _queue  # noqa: E402
sse_q: _queue.Queue = _queue.Queue()
sse_q._created_at = __import__("time").time()
SRV._pipeline_events[sid] = sse_q

r = client.post(f"/api/pipeline/{sid}/resume", json={
    "terms": items, "project_name": "hitl-verify", "top_n": 20,
    "use_ai": False, "translate_mode": "auto", "export_formats": ["csv"],
})
check("resume 返回 200", r.status_code == 200, f"HTTP {r.status_code} {r.text[:200]}")
res = r.json()

adj = res.get("adjudication", {})
print(f"        adjudication: rejected={adj.get('rejected')} merged={adj.get('merged')} "
      f"kept={adj.get('kept')} untranslated={adj.get('untranslated')}")
check("剔除 2 项被记录", sorted(adj.get("rejected") or []) == sorted([reject_a, reject_b]),
      f"{adj.get('rejected')}")
check("合并 1 组被记录",
      adj.get("merged") == [{"from": merge_src, "into": merge_tgt}], f"{adj.get('merged')}")

steps = {s["name"]: s["status"] for s in res.get("step_status", [])}
print(f"        step_status: {steps}")
check("后续步骤已执行（termbase ok）", steps.get("termbase") == "ok", f"{steps}")
check("glossary 标记为采用译员裁决", steps.get("glossary") == "ok", f"{steps.get('glossary')}")
check("report / export 已执行",
      steps.get("report") == "ok" and steps.get("export") == "ok", f"{steps}")

print("\n=== 3. 检查**真实术语库**（不是只看响应）===")
db_name = res.get("termbase_name") or "hitl-verify"
info = client.get("/api/db/info", params={"db": db_name}).json()
print(f"        termbase={db_name} total_terms={info.get('total_terms')}")


def in_termbase(word: str) -> bool:
    r = client.post("/api/term/search", json={"query": word, "db_name": db_name})
    rows = r.json().get("results", [])
    return any((row.get("word") or row.get("term") or "") == word for row in rows)


def lookup(word: str) -> dict:
    r = client.post("/api/term/search", json={"query": word, "db_name": db_name})
    for row in r.json().get("results", []):
        if (row.get("word") or row.get("term") or "") == word:
            return row
    return {}


check("被剔除项 A 不在术语库", not in_termbase(reject_a), reject_a)
check("被剔除项 B 不在术语库", not in_termbase(reject_b), reject_b)
edited_row = lookup(edit_w + "-revised")
check("修正项以**新值**入库", bool(edited_row), f"{edited_row}")
check("修正项译文为译员指定值",
      (edited_row.get("translation") or "") == "REVISED-BY-TRANSLATOR",
      f"{edited_row.get('translation')!r}")
check("修正前的旧值未以旧名入库", not in_termbase(edit_w), edit_w)
merged_row = lookup(merge_tgt)
check("合并目标存在", bool(merged_row), merge_tgt)
check("合并源不再单独存在", not in_termbase(merge_src), merge_src)
added_row = lookup("translator agency")
check("新增项已入库", bool(added_row), f"{added_row}")
check("新增项译文正确",
      (added_row.get("translation") or "") == "译者主体性", f"{added_row.get('translation')!r}")

print("\n=== 4. /api/decisions/export 能否复算整条链路 ===")
r = client.get("/api/decisions/export", params={"session_id": sid, "format": "csv"})
check("导出 CSV 200", r.status_code == 200, f"HTTP {r.status_code}")
rows = list(csv.DictReader(io.StringIO(r.text.lstrip("\ufeff"))))
verdicts = {}
for row in rows:
    if (row.get("step") or "") == "adjudicate":
        verdicts.setdefault(row["term"], []).append(row["translator_verdict"])
print(f"        adjudicate 记录数={sum(len(v) for v in verdicts.values())}")
check("CSV 记录了 2 条 rejected",
      sum(1 for v in verdicts.values() if "rejected" in v) == 2, f"{verdicts}")
check("CSV 记录了 edited 项", "edited" in (verdicts.get(edit_w) or []), f"{verdicts.get(edit_w)}")
check("CSV 记录了 merged 项", "merged" in (verdicts.get(merge_src) or []),
      f"{verdicts.get(merge_src)}")
check("CSV 记录了 added 项", "accepted" in (verdicts.get("translator agency") or [])
      or "added" in (verdicts.get("translator agency") or []),
      f"{verdicts.get('translator agency')}")
check("CSV 含输入摘要 / 时间戳列",
      all(k in (rows[0] if rows else {}) for k in ("timestamp", "input_digest", "translator_verdict")),
      "")

print("\n=== 5. SSE 是否真的推送了 step 进度（原为死功能）===")
events = []
while True:
    try:
        events.append(sse_q.get_nowait())
    except _queue.Empty:
        break
names = [e["data"].get("name") for e in events if e.get("event") == "step"]
kinds = [e.get("event") for e in events]
print(f"        收到事件 {len(events)} 个: {kinds}")
check("SSE 收到 step 事件", len(names) > 0, f"{names}")
check("SSE 收到 pipeline_done", "pipeline_done" in kinds, f"{kinds}")
check("step 事件含 name/status",
      all("name" in e["data"] and "status" in e["data"] for e in events if e.get("event") == "step"), "")

print("\n=== 6. agent task 断头路（原 submit→501）===")
r = client.post("/api/agents/task/submit", json={
    "task_type": "term_extraction", "payload": {"text": ABSTRACT, "top_n": 5},
})
check("submit 返回 task_id", r.status_code == 200 and r.json().get("task_id"),
      f"HTTP {r.status_code}")
tid = r.json().get("task_id", "")
st = client.get(f"/api/agents/task/{tid}")
check("status 不再是 501", st.status_code == 200, f"HTTP {st.status_code}")
check("status=pending", st.json().get("status") == "pending", st.json().get("status"))
ex = client.post(f"/api/agents/task/{tid}/execute")
check("execute 不再是 501", ex.status_code == 200, f"HTTP {ex.status_code}")
check("execute 后状态为 done 或 failed（不再恒 501）",
      ex.json().get("status") in ("done", "failed"), f"{ex.json().get('status')}")

print("\n=== 7. 兼容性：全自动模式仍可用 + openapi ===")
r = client.post("/api/pipeline/v2", json={
    "text": ABSTRACT, "project_name": "auto-mode", "top_n": 5,
    "template": "quick-analysis", "use_ai": False,
})
check("/api/pipeline/v2（全自动模式）仍 200", r.status_code == 200, f"HTTP {r.status_code}")
check("全自动模式仍返回 step_status", bool(r.json().get("step_status")), "")
r = client.post("/api/pipeline", json={"text": ABSTRACT, "project_name": "auto-v1", "top_n": 5})
check("/api/pipeline（v1 全自动模式）仍存在", r.status_code in (200, 500),
      f"HTTP {r.status_code}")

oa = client.get("/openapi.json")
check("/openapi.json 正常生成", oa.status_code == 200, f"HTTP {oa.status_code}")
paths = oa.json().get("paths", {})
check("openapi 含 HITL 端点",
      "/api/pipeline/start" in paths and "/api/pipeline/{session_id}/resume" in paths,
      f"paths={len(paths)}")

print("\n=== 8. 会话过期/非法 ===")
r = client.post("/api/pipeline/nonexistent-session/resume", json={"terms": []})
check("未知 session 返回 404", r.status_code == 404, f"HTTP {r.status_code}")

shutil.rmtree(_TMP, ignore_errors=True)

print("\n" + "=" * 66)
print(f"PASS: {len(PASS)}   FAIL: {len(FAIL)}")
if FAIL:
    print("FAILED:")
    for f in FAIL:
        print("  -", f)
print("=" * 66)
sys.exit(1 if FAIL else 0)
