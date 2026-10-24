"""Verification for the case bundle (§5.4.1 reproducibility).

The claim being tested is *not* "a file is downloaded"; it is:

    after the container-local database is wiped, the exported bundle alone is
    sufficient to reproduce the case data.

So the test deliberately **deletes the databases mid-run** and then re-runs
extraction from nothing but the bundle's own `input.text` + `parameters`, and
requires the term list to be identical.

Runs in an isolated temp data dir (``TERMPREP_DATA_DIR``) so it never touches
real local data.
"""

import glob
import hashlib
import io
import json
import os
import sys
import tempfile
import zipfile

# ── isolate ALL runtime sqlite files into a throw-away dir BEFORE importing ──
# NOTE: this must live INSIDE the workspace — the sandbox blocks writes to the OS
# temp dir, and storage.data_dir() would then silently fall back to <project>/data
# (which is exactly the silent-persistence-failure this suite also checks for).
_TMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_casebundle_tmp")
import shutil as _shutil
_shutil.rmtree(_TMP, ignore_errors=True)
os.makedirs(_TMP, exist_ok=True)
os.environ["TERMPREP_DATA_DIR"] = _TMP
os.environ.setdefault("TERMPREP_ADMIN_TOKEN", "verify-token")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi.testclient import TestClient  # noqa: E402

from termprep import decision_log as DLOG  # noqa: E402
from termprep import export_bundle as EB  # noqa: E402
from termprep.extractor import extract  # noqa: E402
from termprep.web.server import create_app  # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))


TEXT = (
    "本文以翻译研究中的译后编辑为切入点，考察译员在术语管理过程中的决策行为。"
    "研究方法采用质性研究设计，语料来自某高校翻译硕士课程的课堂实践。"
    "研究发现，当机器翻译输出的术语一致性较低时，译员的译后编辑工作量显著增加。"
    "多智能体编排改变了译员的工作重心，使其从被动修改转向主动规划翻译流程。"
)
PARAMS = {"top_n": 15, "domain": "translation", "template": "full-pipeline",
          "ai_provider": "deepseek", "ai_model": "deepseek-chat"}
SID = "case-verify-001"
DECISIONS = [
    {"term": "术语管理", "system_suggestion": "term management", "translator_verdict": "accepted",
     "score": 0.81},
    {"term": "语料", "system_suggestion": "corpus", "translator_verdict": "rejected",
     "note": "误检：通用词"},
    {"term": "多智能体编排", "system_suggestion": "", "translator_verdict": "added",
     "note": "漏检：译员补充"},
]

client = TestClient(create_app())

print("\n=== 0. 数据目录解析（留存是否真的生效）===")
from termprep import storage as _ST  # noqa: E402

check("TERMPREP_DATA_DIR 生效（未被静默忽略）",
      os.path.abspath(_ST.data_dir()) == os.path.abspath(_TMP),
      f"data_dir={_ST.data_dir()}")
check("is_persistent() 为真", _ST.is_persistent() is True, "")
check("/api/health 暴露 storage 状态",
      "storage" in client.get("/api/health").json(),
      str(client.get("/api/health").json().get("storage", {}).get("persistent")))

print("\n=== 1. 会话输入（复现的锚点）是否真的存下来了 ===")
r = client.post("/api/decisions/record", json={
    "session_id": SID, "step": "extract", "input_digest": TEXT[:80],
    "input_text": TEXT, "parameters": PARAMS, "items": DECISIONS,
})
check("裁决写入 200", r.status_code == 200, f"HTTP {r.status_code}")
stored = DLOG.get_input(SID)
check("完整原文已持久化（不只是 80 字摘要）", bool(stored) and stored.get("text") == TEXT,
      f"chars={(stored or {}).get('chars')}")
check("运行参数已持久化", (stored or {}).get("parameters", {}).get("top_n") == PARAMS["top_n"],
      f"{((stored or {}).get('parameters'))}")

sessions = client.get("/api/cases/sessions").json().get("sessions", [])
check("GET /api/cases/sessions 列出该会话",
      any(s["session_id"] == SID for s in sessions), f"n={len(sessions)}")

print("\n=== 2. 数据包内容完整性 ===")
r = client.get("/api/cases/bundle", params={"session_id": SID, "format": "json"})
check("GET /api/cases/bundle 200", r.status_code == 200, f"HTTP {r.status_code}")
bundle = r.json()

need = {"manifest", "input", "parameters", "terms_recomputed", "decisions",
        "decision_summary", "termbases", "tm", "engines", "analysis", "pipeline"}
check("包含全部必需分区", need <= set(bundle), f"缺={sorted(need - set(bundle))}")

man = bundle.get("manifest", {})
check("manifest.format 正确", man.get("format") == "termprep-case-bundle", f"{man.get('format')}")
check("manifest 含 app_version", bool(man.get("app_version")), f"{man.get('app_version')}")
check("manifest 声明确定性口径",
      "deterministic" in json.dumps(man.get("determinism", {})).lower(), "")
check("manifest 声明不含 key", "no API keys" in json.dumps(man.get("privacy", "")), "")

check("input.sha256 与原文一致",
      bundle["input"]["sha256"] == hashlib.sha256(TEXT.encode("utf-8")).hexdigest(),
      bundle["input"]["sha256"][:16])
check("input 含完整原文", bundle["input"]["text"] == TEXT, f"chars={bundle['input']['chars']}")
check("parameters 被带回", bundle["parameters"].get("top_n") == PARAMS["top_n"], f"{bundle['parameters']}")
check("decisions 齐全", len(bundle["decisions"]) == 3, f"n={len(bundle['decisions'])}")
check("decision_summary 计数正确",
      bundle["decision_summary"].get("translator_rejected") == 1
      and bundle["decision_summary"].get("translator_added") == 1, f"{bundle['decision_summary']}")
check("terms_recomputed 非空", len(bundle["terms_recomputed"]) > 0, f"n={len(bundle['terms_recomputed'])}")
check("terms_recomputed 带 score_breakdown / pos",
      all("score_breakdown" in t and t.get("pos") for t in bundle["terms_recomputed"]), "")

# ── privacy: 任何形式都不能出现 key ──
serialized = json.dumps(bundle, ensure_ascii=False)
check("数据包不含 ai_api_key 字段", "ai_api_key" not in serialized, "")
check("数据包含明文 key 形态（sk-）", "sk-" not in serialized, "")

print("\n=== 3. 可复现性证明（清库后仅凭数据包重建）===")
original_terms = [t["term"] for t in bundle["terms_recomputed"]]

# 先证明「确定性」：同一输入同一参数，再跑一次结果一致
again = [t.term for t in extract(TEXT, top_n=PARAMS["top_n"])]
check("规则层确定性：重复运行结果一致", again == original_terms, f"{len(again)} vs {len(original_terms)}")

# 模拟容器重建：把本地数据库全部删掉
db_files = glob.glob(os.path.join(_TMP, "*.db*"))
for f in db_files:
    try:
        os.remove(f)
    except Exception:
        pass
check("本地数据库已被清空（模拟容器重建）",
      DLOG.get_input(SID) is None, f"删除文件数={len(db_files)}")

# 仅凭数据包重建：用 bundle 自己的 input.text + parameters 重跑
rebuilt = [t.term for t in extract(bundle["input"]["text"], top_n=bundle["parameters"]["top_n"])]
check("清库后仅凭数据包即可复现术语列表", rebuilt == original_terms,
      f"重建 {len(rebuilt)} 条 vs 原件 {len(original_terms)} 条")
check("复现出的术语确有实质内容", len(rebuilt) >= 5, f"{rebuilt[:5]}")

print("\n=== 4. ZIP 数据包（论文附录可直接用）===")
r = client.get("/api/cases/bundle", params={"session_id": "", "format": "zip"})
check("GET /api/cases/bundle?format=zip 200", r.status_code == 200, f"HTTP {r.status_code}")
check("返回 zip 内容类型", "zip" in (r.headers.get("content-type") or ""),
      f"{r.headers.get('content-type')}")
with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
    names = set(zf.namelist())
    check("zip 含 case-bundle.json", "case-bundle.json" in names, f"{sorted(names)}")
    check("zip 含 manifest.json", "manifest.json" in names, "")
    check("zip 含 decisions.csv", "decisions.csv" in names, "")
    check("zip 含 terms.csv", "terms.csv" in names, "")
    check("zip 含 README.txt（复现说明）", "README.txt" in names, "")
    readme = zf.read("README.txt").decode("utf-8")
    check("README 说明了复现步骤", "POST /api/extract" in readme, "")
    check("README 声明 AI 阶段不确定", "不确定" in readme or "NOT deterministic" in readme, "")
    check("zip 内不含 sk- 明文", "sk-" not in zf.read("case-bundle.json").decode("utf-8"), "")

print("\n=== 5. 流水线自动落包 ===")
r = client.post("/api/pipeline/v2", json={
    "text": TEXT, "project_name": "case-auto", "top_n": 10,
    "template": "quick-analysis", "use_ai": False,
})
body = r.json()
case = body.get("case", {})
check("流水线响应含 case 分区", bool(case), f"{case}")
check("自动保存成功", case.get("auto_saved") is True, f"{case.get('error', '')}")
check("返回可直接下载的 URL", "/api/cases/bundle" in (case.get("download_url") or ""),
      f"{case.get('download_url')}")
check("session_id = pipeline_id", case.get("session_id") == body.get("pipeline_id"), "")

snaps = client.get("/api/cases/snapshots").json().get("snapshots", [])
check("快照列表非空", len(snaps) > 0, f"n={len(snaps)}")
if snaps:
    name = snaps[0]["name"]
    r = client.get(f"/api/cases/snapshots/{name}")
    check("可下载快照", r.status_code == 200 and len(r.content) > 0,
          f"HTTP {r.status_code} bytes={len(r.content)}")
    check("快照名不可穿越（../ 被拒）",
          client.get("/api/cases/snapshots/..%2F..%2Fetc%2Fpasswd").status_code == 404, "")

print("\n=== 6. 输入不入包时可只导哈希（隐私选项）===")
r = client.post("/api/decisions/record", json={
    "session_id": "case-notext", "step": "extract", "input_text": TEXT, "items": DECISIONS[:1],
})
b2 = client.get("/api/cases/bundle",
                params={"session_id": "case-notext", "include_text": "false"}).json()
check("include_text=false 时不带原文", b2["input"]["text"] == "", "")
check("include_text=false 时仍保留 sha256", bool(b2["input"]["sha256"]), b2["input"]["sha256"][:16])

print("\n" + "=" * 64)
print(f"PASS: {len(PASS)}   FAIL: {len(FAIL)}")
if FAIL:
    print("FAILED:")
    for f in FAIL:
        print("  -", f)
print("=" * 64)
sys.exit(1 if FAIL else 0)
