"""Live read-only health check for https://dzha0118-termprep.hf.space

STRICT SAFETY (task book §1.1): this script only touches read-only endpoints plus
three side-effect-free computation endpoints (/api/analyze, /api/extract,
/api/search). It NEVER calls:

    POST /api/db/init          (would wipe the termbase)
    POST /api/term/add         (would pollute the termbase)
    POST /api/rag/upload       (file upload)
    POST /api/translate/upload (file upload)
    POST /api/translate        (writes to the translation memory)

It also spends at most 3 AI-triggering calls (the task book warns that the
platform calls a paid API).

Output: one line per check, PASS/FAIL, and a summary.
"""

import json
import re
import ssl
import sys
import time
import urllib.error
import urllib.request

BASE = "https://dzha0118-termprep.hf.space"
TIMEOUT = 90

PASS, FAIL, SKIP = [], [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {name}" + (f" -- {detail}" if detail else ""))


def note(msg):
    print(f"  [info] {msg}")


_ctx = ssl.create_default_context()

# Always advertise gzip: server-side GZipMiddleware only compresses when the
# client asks for it, so a bare urllib request would falsely look "uncompressed".
_BASE_HEADERS = {"Accept-Encoding": "gzip, deflate", "User-Agent": "termprep-livecheck/1.0"}


def _lower(hdrs):
    """HTTP header names are case-insensitive — normalise before lookup."""
    return {k.lower(): v for k, v in (hdrs or {}).items()}


def _decode(raw: bytes, hdrs: dict) -> str:
    """urllib does NOT auto-decompress when WE set Accept-Encoding, so do it here."""
    if "gzip" in (hdrs.get("content-encoding", "") or "").lower():
        import gzip as _gzip
        try:
            raw = _gzip.decompress(raw)
        except Exception:
            pass
    return raw.decode("utf-8", "replace")


def get(path, headers=None):
    merged = dict(_BASE_HEADERS)
    merged.update(headers or {})
    req = urllib.request.Request(BASE + path, headers=merged)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=_ctx) as r:
            hdrs = _lower(dict(r.headers))
            return r.status, hdrs, _decode(r.read(), hdrs)
    except urllib.error.HTTPError as e:
        hdrs = _lower(dict(e.headers or {}))
        return e.code, hdrs, _decode(e.read(), hdrs)
    except Exception as e:
        return 0, {}, f"{type(e).__name__}: {e}"


def post(path, payload, headers=None):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    hdrs = dict(_BASE_HEADERS)
    hdrs["Content-Type"] = "application/json"
    hdrs.update(headers or {})
    req = urllib.request.Request(BASE + path, data=body, headers=hdrs, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=_ctx) as r:
            rh = _lower(dict(r.headers))
            return r.status, _decode(r.read(), rh)
    except urllib.error.HTTPError as e:
        rh = _lower(dict(e.headers or {}))
        return e.code, _decode(e.read(), rh)
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}"


print("=" * 78)
print("TermPrep 线上体检（只读）—", BASE)
print("=" * 78)

# ── 1. 版本 / 部署确认 ──
print("\n[1] 部署版本确认")
st, _, txt = get("/api/health")
health = {}
try:
    health = json.loads(txt)
except Exception:
    pass
check("GET /api/health 可用（新端点存在）", st == 200, f"HTTP {st}")
check("health 含 version / engines / db / admin_token_configured",
      {"version", "engines", "db", "admin_token_configured"} <= set(health),
      f"keys={sorted(health)}")
if health:
    note(f"version={health.get('version')}  admin_token_configured={health.get('admin_token_configured')}")
    note(f"engines={[(e.get('name'), e.get('available'), e.get('health')) for e in health.get('engines', [])]}")
    note(f"db={health.get('db')}")
check("AI 引擎可用（用于术语复核与翻译）",
      any(e.get("name") == "ai" and e.get("available") for e in health.get("engines", [])),
      str([e.get("name") for e in health.get("engines", []) if e.get("available")]))

# ── 2. 前端资源 ──
print("\n[2] 前端是否为新版")
st, hdrs, html = get("/")
check("GET / 返回 200", st == 200, f"HTTP {st}")
check("响应带 gzip 压缩", "gzip" in (hdrs.get("content-encoding", "") or "").lower(),
      f"content-encoding={hdrs.get('content-encoding')} 压缩后={hdrs.get('content-length')} 字节")
check("有 ETag / 缓存头", bool(hdrs.get("etag")),
      f"etag={hdrs.get('etag')} cache-control={hdrs.get('cache-control')}")
markers = {
    "全文翻译渲染区块": 'id="pipeline-translation"',
    "真实步骤状态": "step_status",
    "裁决列": 'id="verdict-',
    "管理员令牌输入": 'id="admin-token"',
    "P4 复核提示": "rejected",
    "score_breakdown": "score_breakdown",
    "also_from 双源印证": "also_from",
    "新领域标签(翻译研究)": "翻译研究",
}
for label, needle in markers.items():
    check(f"前端含 {label}", needle in html, f"'{needle}'")

# ── 3. 鉴权门禁（只探测 GET /api/debug/env，无副作用）──
print("\n[3] 鉴权门禁（anon GET /api/debug/env，无副作用）")
st, _, txt = get("/api/debug/env")
check("匿名访问被拒（401 或 503）", st in (401, 503), f"HTTP {st} body={txt[:120]}")
if st == 503:
    note("503 = 未配置 TERMPREP_ADMIN_TOKEN（fail-closed，符合设计）")
elif st == 401:
    note("401 = 令牌不匹配（gate 生效）")
else:
    note("⚠️ 鉴权未生效，写端点仍暴露！")

# ── 4. 只读端点 ──
print("\n[4] 只读端点")
for path in ("/api/sources", "/api/agents/engines", "/api/pipeline/templates",
             "/api/db/info", "/api/db/list", "/api/tm/stats", "/api/rag/stats"):
    st, _, txt = get(path)
    check(f"GET {path}", st == 200, f"HTTP {st} {txt[:90]}")

for path in ("/api/decisions/summary", "/api/decisions/list",
             "/api/decisions/export?format=csv"):
    st, _, txt = get(path)
    check(f"GET {path}（新增裁决日志）", st == 200, f"HTTP {st} {txt[:80]}")

# ── 5. 功能端点（3 次 POST，均为无副作用计算）──
print("\n[5] 功能验证（3 次只读计算调用）")

ZH_ACADEMIC = (
    "本文以翻译研究的学术文本为语料，采用质性研究方法，考察译后编辑过程中译员的术语决策。"
    "研究假设是：术语管理的自动化程度会影响译后编辑工作量。"
    "文献综述部分回顾了机器翻译与计算机辅助翻译的相关理论框架。"
)

st, txt = post("/api/analyze", {"text": ZH_ACADEMIC})
a = {}
try:
    a = json.loads(txt)
except Exception:
    pass
check("POST /api/analyze 200", st == 200, f"HTTP {st}")
check("summary 不再是 dataclass repr", "AnalysisResult(" not in str(a.get("summary", "")),
      f"summary[:50]={str(a.get('summary'))[:50]!r}")
check("返回 words_cn / words_en（新增字段）",
      "words_cn" in a and "words_en" in a, f"words_cn={a.get('words_cn')} words_en={a.get('words_en')}")
check("返回 domain_scores（§5.8）", isinstance(a.get("domain_scores"), dict),
      f"{list(a.get('domain_scores', {}))[:6]}")
check("翻译研究文本判为 academic/translation/humanities",
      a.get("domain") in ("academic", "translation", "humanities"), f"domain={a.get('domain')}")
check("academic 得分 > 0", (a.get("domain_scores", {}).get("academic") or 0) > 0,
      f"academic={a.get('domain_scores', {}).get('academic')}")

EN_TEXT = ("This study examines post-editing effort in translation workflows. "
           "The research adopts a qualitative methodology and a corpus-based approach. "
           "Translators use translation memory and terminology management tools to ensure consistency. "
           "Machine translation quality assurance requires systematic revision of the target text.")
st, txt = post("/api/analyze", {"text": EN_TEXT})
a2 = {}
try:
    a2 = json.loads(txt)
except Exception:
    pass
check("纯英文输入 words_cn == 0（§5.9b）", a2.get("words_cn") == 0, f"words_cn={a2.get('words_cn')}")

time.sleep(1)
st, txt = post("/api/extract", {"text": ZH_ACADEMIC, "top_n": 12})
ex = {}
try:
    ex = json.loads(txt)
except Exception:
    pass
terms = [t.get("word", "") for t in ex.get("terms", [])]
note(f"extract → {terms}")
check("POST /api/extract 200", st == 200, f"HTTP {st}")
check("返回 rejected / verification（P4 已部署）",
      "rejected" in ex and "verification" in ex, f"verification={ex.get('verification')}")
check("pos 字段非空（§5.9a）", bool(terms) and all((t.get("pos") or "") for t in ex.get("terms", [])),
      f"pos={[t.get('pos') for t in ex.get('terms', [])][:5]}")
check("返回 score_breakdown（§5.7）",
      bool(terms) and all(isinstance(t.get("score_breakdown"), dict) for t in ex.get("terms", [])), "")
Fragments = ["考察译员", "语料来自", "研究发现", "保证术语", "借助术语库",
             "依赖人工判断", "机器翻译输出", "智能体编排改变", "被动修改转向主动规划",
             "study examines", "research adopts"]
leak = [t for t in terms if any(f in t for f in Fragments)]
check("无动宾/子句片段（P0/P1 生效）", not leak, f"泄漏={leak}")

time.sleep(1)
st, txt = post("/api/search", {"term": "post-editing effort", "limit": 5})
sc = {}
try:
    sc = json.loads(txt)
except Exception:
    pass
results = sc.get("results", [])
srcs = [r.get("source", "") for r in results]
note(f"search 来源={srcs}")
note(f"sources_used={sc.get('sources_used')} failed={sc.get('sources_failed')} ai_used={sc.get('ai_used')}")
check("POST /api/search 200", st == 200, f"HTTP {st}")
check("返回 > 1 条结果（§5.5 不再被 AI 短路）", len(results) > 1, f"n={len(results)}")
check("出现非 ai 来源（双源真的工作了）", any(not s.startswith("ai") for s in srcs), f"{srcs}")
check("definition 与 word 不同（§5.5）",
      all((r.get("definition") or "").strip().lower() != (r.get("word") or "").strip().lower()
          for r in results),
      f"{[(r.get('word'), (r.get('definition') or '')[:24]) for r in results][:4]}")
check("含 sources_used / sources_failed 新字段",
      "sources_used" in sc and "sources_failed" in sc, "")

# ── 汇总 ──
print("\n" + "=" * 78)
print(f"PASS: {len(PASS)}   FAIL: {len(FAIL)}")
if FAIL:
    print("\n失败项：")
    for f in FAIL:
        print("  -", f)
print("=" * 78)
print("\n本次测试未调用任何写入/破坏性端点：")
print("  /api/db/init · /api/term/add · /api/rag/upload · /api/translate/upload · /api/translate")
sys.exit(1 if FAIL else 0)
