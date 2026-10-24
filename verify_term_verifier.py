"""Verification for P4 (AI term verification).

Covers three things:

1. **Plumbing** — format-tolerant parsing, conservative matching, graceful
   degradation when the AI is unconfigured / fails / replies garbage.
2. **API surface** — ``/api/extract`` exposes ``rejected`` + ``verification`` and
   never drops a candidate without an explicit, matched verdict.
3. **Ceiling, stated honestly** — P4 can only *remove* candidates, so it can lift
   precision but cannot lift recall. This script quantifies exactly how much of
   the remaining error is P4-addressable, and states what is NOT claimed.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("TERMPREP_ADMIN_TOKEN", "verify-token")

from fastapi.testclient import TestClient

from termprep.engines.ai_translator import AITranslator
from termprep.services import term_verifier as TV
from termprep.web.server import create_app

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))


SETTINGS = {
    "ai_provider": "stub",
    "ai_api_key": "stub-key",
    "ai_model": "stub-model",
    "ai_base_url": "http://localhost/unused",
}

print("\n=== 1. 解析容错（模型回复格式漂移）===")
CANDIDATES = ["术语管理", "高校翻译硕士课程", "post-editing effort"]

parsers = {
    "decisions 包装": '{"decisions": [{"term": "术语管理", "is_term": true}, '
                      '{"term": "高校翻译硕士课程", "is_term": false, "type": "clause"}]}',
    "裸数组": '[{"term": "术语管理", "is_term": true}, {"term": "高校翻译硕士课程", "is_term": false}]',
    "布尔字典": '{"术语管理": true, "高校翻译硕士课程": false}',
    "代码块包裹": '```json\n{"decisions": [{"term": "术语管理", "is_term": true}, '
                  '{"term": "高校翻译硕士课程", "is_term": false}]}\n```',
    "行式 yes/no": '术语管理 || yes\n高校翻译硕士课程 || no',
    "字符串布尔": '{"decisions": [{"term": "术语管理", "is_term": "true"}, '
                  '{"term": "高校翻译硕士课程", "is_term": "false"}]}',
}
for label, raw in parsers.items():
    d = TV._parse_decisions(raw)
    ok = (d.get(TV.normalise_term("术语管理"), {}).get("is_term") is True
          and d.get(TV.normalise_term("高校翻译硕士课程"), {}).get("is_term") is False)
    check(f"解析格式：{label}", ok, f"{d}")

check("垃圾回复 → 空决策（降级）", TV._parse_decisions("抱歉，我无法完成。") == {}, "")
check("空回复 → 空决策", TV._parse_decisions("") == {}, "")

print("\n=== 2. 优雅降级：AI 未配置 / 调用失败 ===")
d, meta = TV.verify_terms(CANDIDATES, "ctx", settings={})
check("无 ai_api_key → 不产生决策", d == {}, f"meta={meta}")
check("无 ai_api_key → used=False", meta.get("used") is False, "")
check("无候选 → 空决策", TV.verify_terms([], "ctx", settings=SETTINGS)[0] == {}, "")

real_chat = AITranslator._chat


def boom(self, user_prompt, system_prompt="", max_tokens=None):
    raise RuntimeError("network down")


AITranslator._chat = boom
d, meta = TV.verify_terms(CANDIDATES, "ctx", settings=SETTINGS)
AITranslator._chat = real_chat
check("AI 抛异常 → 空决策（不抛给上层）", d == {}, f"meta={meta}")
check("AI 抛异常 → reason 记录了原因", "failed" in meta.get("reason", ""), f"{meta.get('reason')}")


def garbage(self, user_prompt, system_prompt="", max_tokens=None):
    return "I cannot help with that."


AITranslator._chat = garbage
d, meta = TV.verify_terms(CANDIDATES, "ctx", settings=SETTINGS)
AITranslator._chat = real_chat
check("AI 回复不可解析 → 空决策", d == {}, f"meta={meta}")

print("\n=== 3. 保守匹配：只认原样匹配的判定 ===")


def partial(self, user_prompt, system_prompt="", max_tokens=None):
    return json.dumps({"decisions": [
        {"term": "术语管理", "is_term": True},
        {"term": "高校翻译硕士课程", "is_term": False},
        {"term": "完全不存在的词", "is_term": False},
    ]}, ensure_ascii=False)


AITranslator._chat = partial
d, meta = TV.verify_terms(CANDIDATES, "ctx", settings=SETTINGS)
AITranslator._chat = real_chat
check("匹配到的候选被采纳", TV.normalise_term("术语管理") in d, f"{list(d)}")
check("未请求过的词被忽略", TV.normalise_term("完全不存在的词") not in d, f"{list(d)}")
check("meta 记录 checked/dropped", meta.get("checked") == 2 and meta.get("dropped") == 1, f"{meta}")

print("\n=== 4. /api/extract 接口面 ===")
client = TestClient(create_app())

r = client.post("/api/extract", json={
    "text": "本文以翻译研究中的译后编辑为切入点，考察译员在术语管理过程中的决策行为。"
            "高校翻译硕士课程的课堂实践表明，术语一致性影响译后编辑工作量。",
    "top_n": 15,
})
body = r.json()
check("返回 rejected 字段", "rejected" in body, f"keys={sorted(body.keys())}")
check("返回 verification 字段", "verification" in body, f"{body.get('verification')}")
check("无 AI key 时 verification.used=False",
      body.get("verification", {}).get("used") is False, f"{body.get('verification')}")
check("无 AI key 时 candidates 未被删减（降级）", len(body.get("terms", [])) > 0 and body.get("rejected") == [],
      f"terms={len(body.get('terms', []))} rejected={body.get('rejected')}")

# with a stubbed AI that rejects exactly one candidate we know is in the list
words = [t["word"] for t in body.get("terms", [])]
target = words[0] if words else "术语管理"


def reject_first(self, user_prompt, system_prompt="", max_tokens=None):
    return json.dumps({"decisions": [
        {"term": target, "is_term": False, "type": "clause", "reason": "unit test"}
    ]}, ensure_ascii=False)


# With an AI key present the endpoint normally takes the AI-extraction path.
# Force the rule-based path (simulating an AI-extraction failure) so the P4
# verification pass is actually exercised.
from termprep.agents.orchestrator import AgentOrchestrator

_real_run_term = AgentOrchestrator.run_term_agent


def _force_rule_based(self, *a, **kw):
    raise RuntimeError("forced fallback for test")


AITranslator._chat = reject_first
AgentOrchestrator.run_term_agent = _force_rule_based
try:
    r2 = client.post(
        "/api/extract",
        json={"text": "本文以翻译研究中的译后编辑为切入点，考察译员在术语管理过程中的决策行为。", "top_n": 15},
        # deepseek has a default base_url in AITranslator._PROVIDERS, so the engine
        # counts as available without an explicit X-AI-Base-Url. `_chat` is stubbed,
        # so no network call is made.
        headers={"X-AI-Api-Key": "stub-key", "X-AI-Provider": "deepseek", "X-AI-Model": "deepseek-chat"},
    )
    b2 = r2.json()
finally:
    AgentOrchestrator.run_term_agent = _real_run_term
    AITranslator._chat = real_chat

check("有 AI key 时 verification.used=True", b2.get("verification", {}).get("used") is True,
      f"{b2.get('verification')}")
check("被明确判为非术语的候选被剔除并记录",
      target not in [t["word"] for t in b2.get("terms", [])] and any(x["word"] == target for x in b2.get("rejected", [])),
      f"rejected={b2.get('rejected')}")

print("\n=== 5. 天花板测量（诚实口径）===")
# The gold set lives next to this script in the source tree; when the suite is
# copied into the deploy folder for a smoke test it may be renamed/absent, in
# which case this measurement section is skipped (it is a measurement, not a
# correctness assertion).
_gold_path = next(
    (p for p in ("eval_termset.json", "_eval.json") if os.path.isfile(p)), None
)
if _gold_path is None:
    print("  [SKIP] 未找到 eval_termset.json —— 跳过天花板测量（不影响上面的正确性断言）")
    gold_cases = []
else:
    with open(_gold_path, encoding="utf-8") as _fh:
        gold_cases = json.load(_fh)["texts"]

from termprep.extractor import extract

cur_n = cur_strict = cur_lenient = cur_extra = 0
for case in gold_cases:
    gold = [TV.normalise_term(g) for g in case["gold"]]
    extra = [TV.normalise_term(g) for g in case.get("ok_extra", [])]
    got = [t.term for t in extract(case["text"], top_n=20)]
    for g in got:
        ng = TV.normalise_term(g)
        cur_n += 1
        if ng in gold:
            cur_strict += 1
        elif any(x and (x in ng or ng in x) for x in gold):
            cur_lenient += 1
        elif any(x and (x in ng or ng in x) for x in extra):
            cur_extra += 1

pct = lambda a, b: (100.0 * a / b) if b else 0.0
if gold_cases:
    print(f"  当前（P0/P1，无 AI 复核）：{cur_n} 条 = 精确 {cur_strict} + 冗长 {cur_lenient} + 有效非术语 {cur_extra}")
    print(f"  严格精确率            : {pct(cur_strict, cur_n):.1f}%")
    print("  ⚠️ P4 只能**删**不能**补**：召回率不受影响，天花板仍取决于 P0/P1 的召回")
    print(f"  若 AI 完美剔除「冗长形式 + 有效但非术语」，严格精确率上限 ≈ {pct(cur_strict, cur_strict):.1f}%")
    print(f"  （即：{cur_lenient + cur_extra} 条可被 P4 删掉，剩 {cur_strict} 条精确项）")
    print("  说明：该数字是**理论上限**，不是 AI 实测精度；真实精度需联网用真 AI 才能测。")

    check("P4 语义可删项 > 0（说明 P4 有实际作用面）", cur_lenient + cur_extra > 0,
          f"{cur_lenient + cur_extra} 条")

print("\n" + "=" * 62)
print(f"PASS: {len(PASS)}   FAIL: {len(FAIL)}")
if FAIL:
    print("FAILED:")
    for f in FAIL:
        print("  -", f)
print("=" * 62)
sys.exit(1 if FAIL else 0)
