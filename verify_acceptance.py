"""Local acceptance verification for the HARNESS task book (§8).

Runs entirely against an in-process TestClient — it never touches the live
HF Space (task book §1.1 red line).

Network-dependent criteria (real Youdao/Wikipedia lookups, a real Google/AI
translation) cannot be exercised offline; those are marked NETWORK and are
covered by unit-level checks of the same invariant.
"""

import os
import sys

# Admin token must be set *before* the app is created.
os.environ["TERMPREP_ADMIN_TOKEN"] = "local-test-token"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi.testclient import TestClient
from termprep.web.server import create_app

TOKEN = {"X-Admin-Token": "local-test-token"}
PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))


client = TestClient(create_app())

print("\n=== §8.1 安全 (Security) ===")

r = client.post("/api/db/init", params={"name": "hack", "domain": "x"})
check("匿名 POST /api/db/init 被拒 (401/503)", r.status_code in (401, 503), f"status={r.status_code}")

for path, body in [
    ("/api/term/add", {"word": "x", "translation": "y"}),
]:
    r = client.post(path, json=body)
    check(f"匿名 POST {path} 被拒 (401/503)", r.status_code in (401, 503), f"status={r.status_code}")

r = client.post("/api/rag/upload", files={"file": ("a.txt", b"hello")}, data={"domain": "general"})
check("匿名 POST /api/rag/upload 被拒 (401/503)", r.status_code in (401, 503), f"status={r.status_code}")

r = client.post("/api/translate/upload", files={"file": ("a.txt", b"hello")})
check("匿名 POST /api/translate/upload 被拒 (401/503)", r.status_code in (401, 503), f"status={r.status_code}")

r = client.get("/api/debug/env")
check("匿名 GET /api/debug/env 被拒 (401/503)", r.status_code in (401, 503), f"status={r.status_code}")

r = client.get("/api/debug/env", headers={"X-Admin-Token": "wrong"})
check("错误 token GET /api/debug/env 返回 401", r.status_code == 401, f"status={r.status_code}")

r = client.get("/api/debug/env", headers=TOKEN)
payload = r.json() if r.status_code == 200 else {}
check("正确 token GET /api/debug/env 返回 200", r.status_code == 200, f"status={r.status_code}")
check("debug/env 只含 ai_configured + provider",
      set(payload.keys()) == {"ai_configured", "provider"}, f"keys={sorted(payload.keys())}")
check("debug/env 不含 base_url",
      "base_url" not in r.text.lower() and "base_url" not in str(payload).lower(), "")
check("debug/env 不含任何 key 明文", "api_key" not in str(payload).lower(), "")

# --- §5.2 upload validation ---
r = client.post("/api/translate/upload", headers=TOKEN, files={"file": ("malware.exe", b"MZ")})
check("非白名单扩展名 .exe 返回 4xx", 400 <= r.status_code < 500, f"status={r.status_code}")

big = b"a" * (11 * 1024 * 1024)
r = client.post("/api/translate/upload", headers=TOKEN, files={"file": ("big.txt", big)})
check(">10MB 上传返回 4xx", 400 <= r.status_code < 500, f"status={r.status_code}")

from termprep.web.security import clean_upload_filename

check("文件名 ../ 被清洗",
      clean_upload_filename("../../etc/passwd") == "passwd",
      f"got={clean_upload_filename('../../etc/passwd')!r}")
check("Windows 路径穿越被清洗",
      clean_upload_filename("..\\..\\evil.doc") == "evil.doc",
      f"got={clean_upload_filename('..\\..\\evil.doc')!r}")

print("\n=== §8.2 功能 (Functional) ===")

# --- words_cn on pure English (§5.9b) ---
r = client.post("/api/analyze", json={"text": "Multi-agent orchestration changes the focus of translators work."})
a = r.json()
check("纯英文输入 words_cn == 0", a.get("words_cn") == 0, f"words_cn={a.get('words_cn')}")
check("analyze 返回 words_en > 0", (a.get("words_en") or 0) > 0, f"words_en={a.get('words_en')}")

# --- summary is not a dataclass repr (§5.9d) ---
check("summary 不再是 AnalysisResult repr",
      "AnalysisResult(" not in str(a.get("summary", "")),
      f"summary[:60]={str(a.get('summary'))[:60]!r}")

# --- academic domain (§5.8) ---
academic_text = (
    "本文以翻译研究的学术文本为语料，采用质性研究方法，考察译后编辑过程中译员的术语决策。"
    "研究假设是：术语管理的自动化程度会影响译后编辑工作量。"
    "文献综述部分回顾了机器翻译与计算机辅助翻译的相关理论框架。"
    "This study adopts a qualitative methodology and a corpus-based approach; "
    "the literature review discusses translation theory, post-editing and terminology."
)
r = client.post("/api/analyze", json={"text": academic_text})
a = r.json()
check("翻译研究学术文本判为 academic/translation/humanities",
      a.get("domain") in ("academic", "translation", "humanities"), f"domain={a.get('domain')}")
check("academic 得分 > 0", (a.get("domain_scores", {}).get("academic") or 0) > 0,
      f"academic={a.get('domain_scores', {}).get('academic')}")

# --- extract: explainable score + pos (§5.7 / §5.9a) ---
extract_text = (
    "Multi-agent orchestration changes the focus of translators' work. "
    "Post-editing effort decreases when terminology management is automated. "
    "The translation memory and the termbase support terminology consistency. "
    "Machine translation quality assurance requires systematic revision. "
    "This corpus-based study of post-editing effort uses a mixed methodology. "
)
r = client.post("/api/extract", json={"text": extract_text, "top_n": 8})
terms = r.json().get("terms", [])
scores = [t.get("score") for t in terms]
distinct = len(set(scores))
check("extract score 至少 3 个不同取值", distinct >= 3, f"distinct={distinct} scores={scores}")
check("extract pos 不再恒为空", all((t.get("pos") or "").strip() for t in terms) and len(terms) > 0,
      f"pos={[t.get('pos') for t in terms][:5]}")
check("extract 返回 score_breakdown", all(isinstance(t.get("score_breakdown"), dict) for t in terms) and len(terms) > 0, "")
if terms:
    print(f"        sample: {terms[0]}")

# --- §5.6 highlight alignment invariant ---
from termprep.translator import _generate_highlights

src = "多智能体编排改变了译员的工作重心。"
tgt = "Multi-agent orchestration changes the focus of translators' work."
hls = _generate_highlights(src, tgt, "translation")
bad = [h for h in hls if h.get("target_span") and h["target_span"] not in tgt]
check("每个 highlight 的 target_span 都能在译文中原样找到 (真实译文)", not bad,
      f"{len(hls)} highlights, {len(bad)} misaligned")
check("高亮不再给错译映射（编排→Arrange 必须被拦截）",
      all(h["target_span"].lower() != "arrange" for h in hls),
      f"translations={[h['target_span'] for h in hls]}")

# synthetic checker: a wrong guess must be dropped, not displayed
fake = _generate_highlights("编排改变了重心。", "Orchestration changes the focus.", "it")
check("合成用例: target_span 均在译文中", all(h["target_span"] in "Orchestration changes the focus." for h in fake),
      f"{[(h['term'], h['target_span']) for h in fake]}")

# --- §5.9c TM self-pollution ---
import time as _time
from termprep import tm

_sig = f"tm-selftest-{_time.time()}"
# Mirror the real flow in /api/translate: the cut-off is captured BEFORE storing.
_cutoff = _time.time()
tm.store(_sig, "self pollution probe", "zh", "en", "general")
fresh = tm.search(_sig, "zh", "en", "general", limit=3, exclude_after=_cutoff)
# The criterion is "does not match ITSELF": a genuinely similar *older* entry is a
# legitimate fuzzy hit, but the row written by this very request must never appear.
self_hits = [m for m in fresh if m["source"] == _sig]
check("首次翻译不自命中（本次写入的条目未被返回）", not self_hits, f"self_hits={self_hits}")

# --- §5.9e TM truncation ---
long_target = "Southeast University's administrative management is inefficient " + "x" * 40
tm.store(f"tm-trunc-{_time.time()}", long_target, "zh", "en", "general")
st = tm.stats()
longest = max((len(r["target"]) for r in st["recent"]), default=0)
check("tm/stats 不再截断译文", longest > 60, f"longest_target_len={longest}")

print("\n=== §5.5 双源聚合（离线单元测试，真实源需联网） ===")
from termprep import searcher


class _StubSource:
    name = "youdao"

    def __init__(self, results):
        self.available = True
        self._r = results

    def search(self, term, limit=10):
        return self._r[:limit]


class _StubWiki:
    name = "wikipedia"

    def __init__(self, results):
        self.available = True
        self._r = results

    def search(self, term, limit=10):
        return self._r[:limit]


from termprep.sources.base import SearchResult

fake_sources = [
    _StubSource([SearchResult(query="q", word="译后编辑工作量", word_type="translation",
                              source="youdao", score=0.95, definition="/ˈedɪtɪŋ/ n. 编辑工作")]),
    _StubWiki([SearchResult(query="q", word="Post-editing", word_type="concept",
                            source="wikipedia-en", score=0.8,
                            definition="Post-editing is the process of improving machine output.")]),
]
_orig = searcher.get_available_sources
searcher.get_available_sources = lambda: fake_sources
try:
    out = searcher.search_multi("post-editing effort", limit=3,
                                ai_callable=lambda: [{"query": "q", "word": "译后编辑工作量",
                                                      "type": "translation", "score": 0.9,
                                                      "definition": ""}])
finally:
    searcher.get_available_sources = _orig

check("多源聚合返回 > 1 条", len(out["results"]) > 1, f"n={len(out['results'])}")
check("出现非 ai 来源", any(r["source"] != "ai" for r in out["results"]),
      f"sources={[r['source'] for r in out['results']]}")
check("definition 与 word 不同", all(r["definition"].strip().lower() != r["word"].strip().lower()
                                    for r in out["results"]),
      f"{[(r['word'], r['definition'][:24]) for r in out['results']]}")
check("词典源排在 AI 之前", out["results"][0]["source"].startswith(("youdao", "wikipedia")),
      f"first={out['results'][0]['source']}")
check("跨源印证 also_from 生效",
      any(r.get("also_from") for r in out["results"]),
      f"{[r.get('also_from') for r in out['results']]}")

print("\n=== §8.3 论文支撑 ===")

r = client.get("/api/health")
h = r.json()
check("/api/health 返回版本号", bool(h.get("version")), f"version={h.get('version')}")
check("/api/health 含 engines + db", "engines" in h and "db" in h, f"keys={sorted(h.keys())}")

session = f"selftest-{int(_time.time())}"
r = client.post("/api/decisions/record", json={
    "session_id": session,
    "step": "extract",
    "input_digest": academic_text[:80],
    "items": [
        {"term": "post-editing effort", "system_suggestion": "译后编辑工作量",
         "translator_verdict": "accepted", "score": 0.87, "score_breakdown": {"frequency": 1.0}},
        {"term": "machine", "system_suggestion": "机器", "translator_verdict": "rejected",
         "score": 0.42, "note": "误检：常用词"},
        {"term": "术语管理", "system_suggestion": "", "translator_verdict": "added",
         "note": "漏检：译员补充"},
    ],
})
check("裁决记录写入成功", r.status_code == 200 and r.json().get("recorded") == 3, f"{r.text[:120]}")

r = client.get("/api/decisions/summary", params={"session_id": session})
s = r.json()
check("summary 统计正确",
      s.get("translator_rejected") == 1 and s.get("translator_added") == 1 and s.get("translator_accepted") == 1,
      f"{s}")

r = client.get("/api/decisions/export", params={"session_id": session, "format": "csv"})
csv_text = r.text
check("CSV 导出含全部必需列",
      all(col in csv_text for col in ["timestamp", "input_digest", "term", "system_suggestion", "translator_verdict"]),
      "")
check("CSV 含译员裁决标签", "rejected" in csv_text and "added" in csv_text, "")
rows = [ln for ln in csv_text.strip().splitlines() if ln.strip()]
check("CSV 行数 = 表头 + 3", len(rows) == 4, f"rows={len(rows)}")

r = client.get("/api/decisions/export", params={"session_id": session, "format": "json"})
check("JSON 导出可用", r.status_code == 200 and len(r.json().get("decisions", [])) == 3, "")

print("\n" + "=" * 60)
print(f"PASS: {len(PASS)}   FAIL: {len(FAIL)}")
if FAIL:
    print("FAILED:")
    for f in FAIL:
        print("  -", f)
print("=" * 60)
sys.exit(1 if FAIL else 0)
