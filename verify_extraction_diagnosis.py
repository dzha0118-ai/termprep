"""「为什么提取不出来术语」的可解释性与兜底回归测试。

对应用户反馈：「有时候会自动提取不出来术语」。

实测发现的真实成因（不是玄学）：
  1. 文本太短 —— jieba TF-IDF / TextRank / YAKE / C-Value 依赖词频与共现，
     15 字只能出 2 条，10 字出 1 条，≥80 字才稳定；
  2. AI 语义复核（P4）会主动剔除 —— 100 字摘要实测 dropped=5（约一半）；
  3. 极端情况 AI 会把**整批**候选都判成非术语 → 用户看到空表；
  4. 纯数字/符号、纯停用词这类输入本来就没有术语。

本套件验证：原因被如实返回（diagnostics）、被 AI 剔掉的项可以「加回候选」
（rejected 带 score/pos）、以及「AI 想清空全部候选」时不采信（兜底）。
"""

import os
import shutil
import sys

_TMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_hitl_tmp")
os.environ.setdefault("TERMPREP_DATA_DIR", _TMP)
os.environ.setdefault("TERMPREP_ADMIN_TOKEN", "verify-token")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi.testclient import TestClient  # noqa: E402

from termprep.engines.ai_translator import AITranslator  # noqa: E402
from termprep.extractor import TermEntry  # noqa: E402
from termprep.web import server as SRV  # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))


LONG = (
    "本研究以译后编辑工作量与多智能体编排为切入点，考察术语管理对翻译质量的影响。"
    "研究采用混合方法，对 32 名译员进行了眼动实验与回溯访谈，并使用翻译记忆库"
    "与术语库进行一致性校验。结果表明，译前术语准备能够显著降低译后编辑工作量，"
    "并提升术语一致性与译文可读性。多智能体编排在长文本场景下优于单一模型。"
)

print("=== 1. _apply_term_verification：只删该删的 ===")
from termprep.services.term_verifier import normalise_term  # noqa: E402

terms = [TermEntry(term=w, frequency=2, score=0.5, word_type="word")
         for w in ["术语管理", "翻译质量", "混合", "切入点"]]
decisions = {
    normalise_term("术语管理"): {"is_term": True, "type": "term", "reason": ""},
    normalise_term("翻译质量"): {"is_term": True, "type": "term", "reason": ""},
    normalise_term("混合"): {"is_term": False, "type": "generic", "reason": "形容词"},
    normalise_term("切入点"): {"is_term": False, "type": "generic", "reason": "通用词"},
}
kept, dropped, fallback = SRV._apply_term_verification(terms, decisions, normalise_term)
check("按 AI 结论剔除 2 项", len(kept) == 2 and len(dropped) == 2, f"kept={len(kept)} dropped={len(dropped)}")
check("kept 保留正确项", {t.term for t in kept} == {"术语管理", "翻译质量"}, "")
check("dropped 带 reason", all(d.get("reason") for d in dropped), f"{dropped}")
check("dropped 带 freq（便于加回候选）", all(d.get("freq") == 2 for d in dropped), f"{dropped}")
check("正常情况不触发兜底", fallback == "", f"fallback={fallback}")

print("\n=== 2. 兜底：AI 想把整批都删掉时不采信 ===")
all_false = {normalise_term(w): {"is_term": False, "type": "generic", "reason": "x"}
             for w in ["术语管理", "翻译质量", "混合", "切入点"]}
kept2, dropped2, fallback2 = SRV._apply_term_verification(terms, all_false, normalise_term)
check("全部判非术语 → 候选原样保留", len(kept2) == 4, f"kept={len(kept2)}")
check("全部判非术语 → 不返回剔除项", dropped2 == [], f"{dropped2}")
check("全部判非术语 → 标记 fallback", fallback2 == "would-drop-all", f"fallback={fallback2}")

print("\n=== 3. _extraction_diagnostics：把原因说清楚 ===")
d = SRV._extraction_diagnostics(LONG, 11, 8, 3)
check("正常情况 code=ai-dropped-many", d["code"] == "ai-dropped-many", f"{d['code']}")
check("ai-dropped-many 有 hint", "加回候选" in d["hint"], d["hint"])

d = SRV._extraction_diagnostics("术语管理可以提升译文一致性。", 2, 2, 0)
check("短文本 code=short-text", d["code"] == "short-text", f"{d['code']}")
check("短文本 hint 提到字数与建议篇幅", "≥300" in d["hint"], d["hint"])

d = SRV._extraction_diagnostics("12345 67 89 。，、！？ 2024-10-24", 0, 0, 0)
check("纯数字符号 code=no-wordy-content", d["code"] == "no-wordy-content", f"{d['code']}")
check("纯数字符号 hint 非空", bool(d["hint"]), d["hint"])

d = SRV._extraction_diagnostics("的 了 和 是 在", 0, 0, 0)
check("全是停用词 kind=no-wordy-content", d["code"] == "no-wordy-content", f"{d['code']}")

d = SRV._extraction_diagnostics(LONG, 11, 11, 0)
check("一切正常时不打扰用户", d["code"] == "ok" and d["hint"] == "", f"{d}")

print("\n=== 4. 端点：short text 返回可解释诊断 ===")
client = TestClient(SRV.create_app())
r = client.post("/api/pipeline/start",
                json={"text": "术语管理很重要。", "project_name": "diag",
                      "top_n": 10, "prefill_translations": False})
check("start 200", r.status_code == 200, str(r.status_code))
data = r.json()
dg = data.get("diagnostics") or {}
check("返回 diagnostics", bool(dg), f"{list(dg)[:5]}")
check("diagnostics 有 code 与 hint", dg.get("code") and dg.get("hint") is not None, f"{dg}")
check("diagnostics.chars 与文本一致", dg.get("chars") == len("术语管理很重要。"), f"{dg.get('chars')}")
check("diagnostics.extracted 与候选数一致", dg.get("extracted") == len(data.get("terms") or []),
      f"extracted={dg.get('extracted')} terms={len(data.get('terms') or [])}")

print("\n=== 5. 端点：纯符号文本 → 0 候选 + 明确原因 ===")
r = client.post("/api/pipeline/start",
                json={"text": "12345 67 89 。，、！？", "project_name": "diag2",
                      "top_n": 10, "prefill_translations": False})
data = r.json()
check("start 200", r.status_code == 200, str(r.status_code))
check("候选为 0", len(data.get("terms") or []) == 0, f"{len(data.get('terms') or [])}")
dg = data.get("diagnostics") or {}
check("code=no-wordy-content", dg.get("code") == "no-wordy-content", f"{dg.get('code')}")
check("hint 非空（用户能看到原因）", bool(dg.get("hint")), f"{dg.get('hint')}")

print("\n=== 6. 端点：AI 想清空全部候选时不采信（真实链路）===")
SETTINGS = {"ai_api_key": "test-key", "ai_provider": "deepseek",
            "ai_model": "deepseek-chat", "ai_base_url": "https://example.com/v1"}
real_chat = AITranslator._chat


def drop_all(self, user_prompt, system_prompt="", max_tokens=None):
    """模拟一个"把所有候选都判成非术语"的模型回复。"""
    import json as _json
    import re as _re
    # 从 prompt 里把候选捞出来（编号列表）
    words = _re.findall(r"^\s*\d+\.\s*(.+)$", user_prompt, _re.M)
    return _json.dumps(
        {"decisions": [{"term": w.strip(), "is_term": False, "type": "generic",
                        "reason": "模拟：全部判为非术语"} for w in words]},
        ensure_ascii=False,
    )


AITranslator._chat = drop_all
try:
    r = client.post(
        "/api/pipeline/start",
        json={"text": LONG, "project_name": "diag3", "top_n": 12,
              "prefill_translations": False},
        headers={f"x-ai-provider": SETTINGS["ai_provider"],
                 f"x-ai-api-key": SETTINGS["ai_api_key"],
                 f"x-ai-model": SETTINGS["ai_model"],
                 f"x-ai-base-url": SETTINGS["ai_base_url"]},
    )
    data = r.json()
    check("start 200", r.status_code == 200, str(r.status_code))
    terms_kept = data.get("terms") or []
    check("候选没有被清空（兜底生效）", len(terms_kept) > 0, f"terms={len(terms_kept)}")
    ver = data.get("verification") or {}
    check("verification.fallback = would-drop-all", ver.get("fallback") == "would-drop-all",
          f"{ver}")
    check("verification.dropped 归零", ver.get("dropped") == 0, f"{ver}")
    check("rejected 为空（没真的删）", (data.get("rejected") or []) == [],
          f"{data.get('rejected')}")
finally:
    AITranslator._chat = real_chat

print("\n=== 7. 端点：AI 只删一部分时，剔除项可加回（带 score/pos）===")


def drop_some(self, user_prompt, system_prompt="", max_tokens=None):
    import json as _json
    import re as _re
    words = [w.strip() for w in _re.findall(r"^\s*\d+\.\s*(.+)$", user_prompt, _re.M)]
    out = []
    for i, w in enumerate(words):
        out.append({"term": w, "is_term": i % 2 == 0,
                    "type": "term" if i % 2 == 0 else "generic",
                    "reason": "" if i % 2 == 0 else "模拟：通用词"})
    return _json.dumps({"decisions": out}, ensure_ascii=False)


AITranslator._chat = drop_some
try:
    r = client.post(
        "/api/pipeline/start",
        json={"text": LONG, "project_name": "diag4", "top_n": 12,
              "prefill_translations": False},
        headers={"x-ai-provider": SETTINGS["ai_provider"],
                 "x-ai-api-key": SETTINGS["ai_api_key"],
                 "x-ai-model": SETTINGS["ai_model"],
                 "x-ai-base-url": SETTINGS["ai_base_url"]},
    )
    data = r.json()
    rej = data.get("rejected") or []
    check("start 200", r.status_code == 200, str(r.status_code))
    check("有剔除项", len(rej) > 0, f"{len(rej)}")
    if rej:
        first = rej[0]
        check("剔除项带 word", bool(first.get("word")), f"{first}")
        check("剔除项带 score（可加回候选后排序）",
              isinstance(first.get("score"), (int, float)), f"{first.get('score')}")
        check("剔除项带 pos", "pos" in first, f"{first.keys()}")
        check("剔除项带 reason", "reason" in first, f"{first.keys()}")
    sid = data.get("session_id", "")
    r2 = client.get(f"/api/pipeline/{sid}")
    sess = r2.json()
    check("会话刷新后仍带 rejected", len(sess.get("rejected") or []) == len(rej),
          f"{len(sess.get('rejected') or [])} vs {len(rej)}")
    check("会话刷新后仍带 diagnostics", bool(sess.get("diagnostics")),
          f"{list(sess.get('diagnostics') or {})[:4]}")
finally:
    AITranslator._chat = real_chat

print("\n" + "=" * 62)
print(f"PASS: {len(PASS)}   FAIL: {len(FAIL)}")
print("=" * 62)
if FAIL:
    print("失败项：")
    for f in FAIL:
        print("  - " + f)
shutil.rmtree(_TMP, ignore_errors=True)
sys.exit(0 if not FAIL else 1)
