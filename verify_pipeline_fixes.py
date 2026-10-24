"""Verification for the two reported pipeline defects (offline-safe).

Bug A — 全自动流水线看不到第四步「全文翻译」
    Checks that (1) the backend reports the step, (2) a step that produced
    nothing is reported as FAILED rather than silently absent, (3) the template
    really gates execution, and (4) the front-end actually renders the field.

Bug B — 术语提取翻译「有的没翻译全」
    Stubs the AI with a realistic partial / format-drifted reply and checks that
    every requested term comes back translated.
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("TERMPREP_ADMIN_TOKEN", "verify-token")

from fastapi.testclient import TestClient
from termprep.web.server import create_app

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))


client = TestClient(create_app())

TEXT = ("多智能体编排改变了译员的工作重心。术语管理可以降低译后编辑工作量，"
        "并提升翻译质量的一致性与可审计性。")

print("\n=== Bug A: 第四步「全文翻译」是否可见、可信 ===")

r = client.post("/api/pipeline/v2", json={
    "text": TEXT, "project_name": "verify_full", "top_n": 8,
    "template": "full-pipeline", "use_ai": False, "translate_mode": "auto",
})
d = r.json()
check("HTTP 200", r.status_code == 200, f"status={r.status_code}")

status = {s["name"]: s for s in d.get("step_status", [])}
print(f"        步骤: {[(s['name'], s['status'], s['detail'][:28]) for s in d.get('step_status', [])]}")

check("响应含 step_status", bool(d.get("step_status")), f"n={len(d.get('step_status', []))}")
check("step_status 含 full_translate（全文翻译）", "full_translate" in status,
      f"keys={sorted(status)}")
check("full_translate 有明确状态 ok/failed",
      status.get("full_translate", {}).get("status") in ("ok", "failed"),
      f"status={status.get('full_translate', {}).get('status')}")

ft = d.get("full_translation") or ""
if ft.strip():
    check("有译文时步骤状态为 ok", status.get("full_translate", {}).get("status") == "ok",
          f"len={len(ft)}")
    check("步骤 detail 报告了译文字符数", "字符" in status.get("full_translate", {}).get("detail", ""),
          status.get("full_translate", {}).get("detail", ""))
else:
    check("无译文时步骤状态为 failed（不再静默）",
          status.get("full_translate", {}).get("status") == "failed",
          f"status={status.get('full_translate', {}).get('status')}")
    check("无译文时给出可读错误", any("全文翻译" in e for e in d.get("errors", [])),
          f"errors={d.get('errors')}")

check("响应保留 full_translation 字段（前端可渲染）", "full_translation" in d)
check("响应含 errors 字段（前端可展示）", "errors" in d)
check("full-pipeline 含 export 步骤", "export" in status)

# ── the template must actually gate execution ──
r2 = client.post("/api/pipeline/v2", json={
    "text": TEXT, "project_name": "verify_quick", "top_n": 8,
    "template": "quick-analysis", "use_ai": False,
})
d2 = r2.json()
s2 = {s["name"]: s for s in d2.get("step_status", [])}
check("quick-analysis 跳过全文翻译",
      s2.get("full_translate", {}).get("status") == "skipped",
      f"status={s2.get('full_translate', {}).get('status')}")
check("quick-analysis 跳过术语库",
      s2.get("termbase", {}).get("status") == "skipped",
      f"status={s2.get('termbase', {}).get('status')}")
check("quick-analysis 仍在跑分析+提取",
      s2.get("analyze", {}).get("status") == "ok" and s2.get("extract", {}).get("status") == "ok",
      "")
check("两个模板的步骤状态不同（模板真的生效了）",
      s2 != status, "")

# ── front-end actually renders the field ──
html = client.get("/").text
check("前端新增全文翻译渲染区块", 'id="pipeline-translation"' in html)
check("前端渲染 full_translation", "full_translation" in html)
check("前端渲染 step_status（真实步骤状态）", "step_status" in html)
check("前端展示 errors（此前被丢弃）", 'id="pipeline-errors"' in html)

print("\n=== Bug B: 术语翻译是否翻全 ===")

from termprep.engines.ai_translator import AITranslator
from termprep.services.translation_service import TranslationService


def stub_chat(self, user_prompt, system_prompt="", max_tokens=None):
    """Model answers ~60% of the requested terms, drifting format halfway."""
    asked = re.findall(r"^\s*\d+\.\s*(.+?)\s*$", user_prompt, re.M)
    keep = asked[: max(1, int(len(asked) * 0.6))]
    out = []
    for i, t in enumerate(keep):
        out.append(f"{t} || TR[{t}]" if i % 2 == 0 else f"{i+1}. {t} - TR[{t}]")
    return "\n".join(out)


TERMS = [f"term-{i:02d}" for i in range(1, 31)]
real = AITranslator._chat
AITranslator._chat = stub_chat
try:
    svc = TranslationService({"ai_provider": "stub", "ai_api_key": "k",
                              "ai_model": "m", "ai_base_url": "http://x/v1"})
    svc.load_engines()
    res = svc.translate_terms(TERMS, "zh", "en", "it", engine="ai")
finally:
    AITranslator._chat = real

got = [x for x in res if x.get("translation")]
by_engine = {}
for x in res:
    by_engine[x.get("engine") or "(未译)"] = by_engine.get(x.get("engine") or "(未译)", 0) + 1
check("批量翻译结果与输入等长", len(res) == len(TERMS), f"{len(res)} vs {len(TERMS)}")
check("AI 只答 60% 时仍全部译出", len(got) == len(TERMS), f"{len(got)}/{len(TERMS)} {by_engine}")
check("多通道补齐（不只有单次调用）", len([k for k in by_engine if k != "(未译)"]) >= 2, f"{by_engine}")

# format tolerance
from termprep.services.translation_service import TranslationService as TS
cases = {
    "pipe": "a || 甲\nb || 乙",
    "numbered-dash": "1. a - 甲\n2. b - 乙",
    "arrow": "a → 甲\nb → 乙",
    "colon": "a: 甲\nb: 乙",
    "json": '{"a": "甲", "b": "乙"}',
    "json-list": '[{"term": "a", "translation": "甲"}]',
    "table": "| term | translation |\n|---|---|\n| a | 甲 |",
    "fenced": "```\na || 甲\n```",
}
for label, raw in cases.items():
    parsed = TS._parse_batch_output(["a", "b"], raw)
    check(f"解析格式 {label}", parsed.get("a") == "甲", f"{parsed}")

bad = TS._parse_batch_output(["a"], "aa || 错值\nzz || 别的")
check("不会把别的术语译文错配过来（宁可缺失不可错配）", bad.get("a") is None, f"{bad}")

print("\n" + "=" * 62)
print(f"PASS: {len(PASS)}   FAIL: {len(FAIL)}")
if FAIL:
    print("FAILED:")
    for f in FAIL:
        print("  -", f)
print("=" * 62)
sys.exit(1 if FAIL else 0)
