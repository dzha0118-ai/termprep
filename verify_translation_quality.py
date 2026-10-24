"""全文翻译质量回归测试。

对应用户反馈：「全文翻译质量非常一般、译文非常生硬」。用他给的真实文本做夹具。

定位到的四个根因（都不是"模型不行"）：

  1. **分段把原文结构弄坏了**——`_split_text` 用 `buf += s` 直接拼句子，
     句子之间没有分隔符，产生 `journey.Now` 这种英文粘连；段落换行更是完全丢失。
     AI 收到一大坨无段落、句子黏在一起的文本，输出必然是黏在一起的。
  2. **提示词只有一句 "translate naturally"**，没有句数守恒、没有中文反翻译腔要求。
  3. **译后什么都没做**——半角标点、中文字词间的空格、行末缺句号全部原样输出。
  4. **漏句无人发现**——用户译文里整句
     "smoothing the seasonality that has always troubled the visitor economy"
     消失，系统照报成功。
"""

import os
import re
import sys

_TMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_hitl_tmp")
os.environ.setdefault("TERMPREP_DATA_DIR", _TMP)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from termprep.engines import ai_translator as AT  # noqa: E402
from termprep.engines.ai_translator import (  # noqa: E402
    AITranslator,
    audit_translation,
    normalize_target_text,
    sentence_count,
)
from termprep.interfaces.translator import TranslationRequest  # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))


# ── 用户给的真实文本（节选：含被漏译的那句 + 两处结构断裂点）──
SPEECH = (
    "Let's look first at how quickly this separation has broken down.\n"
    "Global business travel spending is expected to reach a record US$1.57 trillion in 2025, "
    "and a growing share of those trips now continues after the meeting ends. In an "
    "international survey published in 2024, 74% of frequent business travellers said they "
    "had added leisure days to a work trip, and Marriott reports that business guests now "
    "stay roughly 20% longer than they did a few years ago. The business trip is quietly "
    "becoming a blended journey.\n"
    "Now, let's talk about the benefits this trend creates for a destination like Australia.\n"
    "First, it raises yield. Business travellers are famously low in price sensitivity and "
    "high in expenditure, so every extra night is additional export income.\n"
    "Second, it spreads visitors beyond the capital cities. A conference in Sydney or "
    "Melbourne can become a weekend in the Blue Mountains or a few days on the Great "
    "Barrier Reef, which sustains regional operators. Third, it strengthens resilience. "
    "Extended stays fill weekend and shoulder-season capacity, smoothing the seasonality "
    "that has always troubled the visitor economy."
)

print("=== 1. 句数统计：不能被 US$1.57 里的点骗到 ===")
check("US$1.57 不算句末", sentence_count("reach US$1.57 trillion in 2025.") == 1,
      f"{sentence_count('reach US$1.57 trillion in 2025.')}")
check("三句英文 = 3", sentence_count("A goes. B stops? C ends!") == 3,
      f"{sentence_count('A goes. B stops? C ends!')}")
check("三句中文 = 3", sentence_count("甲走了。乙停下？丙结束！") == 3, "")
check("无终止符但有内容 = 1", sentence_count("一段没有句号的文字") == 1, "")
check("空串 = 0", sentence_count("   ") == 0, "")

print("\n=== 2. 分段：段落与句子都不能被弄坏（旧代码的根因）===")
chunks = AITranslator._split_text(SPEECH, 200)
check("切出多个块", len(chunks) > 1, f"{len(chunks)} 块")
check("每块都带段落号", all(isinstance(c, tuple) and len(c) == 2 for c in chunks), f"{chunks[0]}")
para_ids = [c[0] for c in chunks]
check("段落号单调不减（段落顺序保持）", para_ids == sorted(para_ids), f"{para_ids}")
check("至少保留 3 个不同段落", len(set(para_ids)) >= 3, f"段落号={set(para_ids)}")
glued = [c[1] for c in chunks if re.search(r"[a-z]\.[A-Z]", c[1])]
check("没有 journey.Now 这类单词粘连", not glued, f"{glued[:1]}")
check("块尾是完整句（以句末标点结束）",
      all(c[1].rstrip().endswith((".", "?", "!")) for c in chunks),
      f"{[c[1][-12:] for c in chunks]}")

print("\n=== 3. 译后规范化：用户译文里的四处痕迹 ===")
cases = [
    ("混合型旅行 现在，让我们谈谈", "句子之间的多余空格", "混合型旅行现在，让我们谈谈。"),
    ("发布有关注意义务、保险和可持续发展的明确指导", "行末缺句号", "发布有关注意义务、保险和可持续发展的明确指导。"),
    ("一是数据盲点:我们国家的统计仍然将商务旅行和休闲旅行分开", "半角冒号", "一是数据盲点：我们国家的统计仍然将商务旅行和休闲旅行分开。"),
    ("那么,我们如何才能前进?", "半角逗号与问号", "那么，我们如何才能前进？"),
    ("第一 它提高了收益 第二 它把游客带出首府城市", "中文字词间空格", "第一它提高了收益第二它把游客带出首府城市。"),
]
for raw, why, want in cases:
    got = normalize_target_text(raw, "zh")
    check(f"{why}已修正", got == want, f"{got!r} != {want!r}")

check("数字与英文之间的空格保留", "2025 年" in normalize_target_text("2025 年达到 1,000 例", "zh"),
      normalize_target_text("2025 年达到 1,000 例", "zh"))
check("不会把 1,000 变成 1，000", "1,000" in normalize_target_text("共 1,000 例", "zh"),
      normalize_target_text("共 1,000 例", "zh"))
check("清掉 [Translation missing] 占位符",
      "Translation missing" not in normalize_target_text("第一段。[Translation missing for segment 2]第二段。", "zh"),
      normalize_target_text("第一段。[Translation missing for segment 2]第二段。", "zh"))
check("多余空行折叠成一个", "\n\n\n" not in normalize_target_text("甲。\n\n\n\n乙。", "zh"), "")
check("英文目标保留正常空格", "post-editing effort" in normalize_target_text(
    "the post-editing effort，namely.", "en"),
      normalize_target_text("the post-editing effort，namely.", "en"))

print("\n=== 4. 漏译审计：用户那次丢的整句必须被抓出来 ===")
FULL_ZH = (
    "让我们先看看这种分离瓦解得多快。\n"
    "全球商务旅行支出预计在 2025 年达到创纪录的 1.57 万亿美元，而这些行程中越来越多在会议结束后仍在继续。"
    "在 2024 年发布的一项国际调查中，74% 的常旅客表示他们在出差中加了休闲日，"
    "万豪报告称商务客人现在的停留时间比几年前长约 20%。商务出差正在悄然变成混合行程。\n"
    "接下来谈谈这一趋势为澳大利亚这样的目的地带来的好处。\n"
    "第一，它提高了收益。商务旅客的典型特征是价格敏感度低、消费水平高，所以每多一晚都是额外的出口收入。\n"
    "第二，它把游客带出首府城市。悉尼或墨尔本的一场会议可以延伸成蓝山的周末或大堡礁的几天，"
    "从而支撑区域经营者。第三，它增强了韧性。延长停留填补了周末与平季的接待能力，"
    "平抑了一直困扰旅游经济的季节性问题。"
)
ok_audit = audit_translation(SPEECH, FULL_ZH, "zh")
check("完整译文通过审计", ok_audit["ok"], f"{ok_audit}")
check("完整译文句数覆盖 ≥0.75", ok_audit["coverage"] >= 0.75, f"{ok_audit['coverage']}")

# 复刻用户译文：把 "smoothing the seasonality..." 整句删掉
LOST_ZH = FULL_ZH.replace(
    "平抑了一直困扰旅游经济的季节性问题。", ""
).replace("延长停留填补了周末与平季的接待能力，", "延长停留填补了周末与平季的接待能力。")

# 说明：整篇审计对"丢一整句"其实不够敏感（丢 1 句 = 覆盖 10/11 ≈ 0.91），
# 汉语本来就常合法地把两句并成一句。所以敏感的那张网是**分块审计**
# （每块只有 1–3 句，丢 1 句就掉到 0.67，必被抓到），见第 6 节的端到端。
GROSS_ZH = FULL_ZH.split("\n")[0]      # 只剩第一段：整段丢失
gross = audit_translation(SPEECH, GROSS_ZH, "zh")
check("整段丢失被判为不合格", not gross["ok"], f"{gross}")
check("问题描述里点出句数偏少或过短",
      any("句数" in i or "过短" in i for i in gross["issues"]), f"{gross['issues']}")

check("译文为空判失败", not audit_translation("abc", "", "zh")["ok"], "")
check("译文夹带解释会被判过长",
      not audit_translation("Hi.", "翻译如下：" + "很长的说明" * 40, "zh")["ok"], "")

print("\n=== 5. 提示词必须写死关键约束 ===")
sys_p = AT._SYSTEM_PROMPT
for needle, why in [
    ("漏译", "禁止漏译"),
    ("句数必须与原文一致", "句数守恒"),
    ("段落", "段落结构"),
    ("全角标点", "中文标点规范"),
    ("不要在中文字词之间加空格", "中文空格规范"),
    ("意合", "反翻译腔（汉语意合）"),
    ("术语表", "术语表优先"),
]:
    check(f"系统提示词包含{why}", needle in sys_p, f"缺少 '{needle}'")
check("重试提示词要求逐句完整", "一句都不能省略" in AT._COMPLETENESS_NUDGE, "")
check("校对提示词把漏译列为首要问题",
      "漏译" in AT._QA_SYSTEM_PROMPT and "最高优先级" in AT._QA_SYSTEM_PROMPT, "")
check("校对提示词禁止删减", "不得减少句子" in AT._QA_SYSTEM_PROMPT, "")
check("general 领域提示词提到地道表达",
      "地道" in AT.DOMAIN_PROMPTS["general"], AT.DOMAIN_PROMPTS["general"])
check("speech 领域词存在（讲稿语体）", "speech" in AT.DOMAIN_PROMPTS, "")
check("translation 领域词存在", "translation" in AT.DOMAIN_PROMPTS, "")

print("\n=== 6. 分块路径：段落拼回 + 单句漏译被逐块抓住（敏感的那张网）===")
SETTINGS = {"ai_provider": "stub", "ai_api_key": "k", "ai_model": "m",
            "ai_base_url": "http://stub/v1", "ai_temperature": 0.3}
real_chat = AITranslator._chat
calls = {"n": 0}
# 每块对应的中文（用原文块做键，桩里直接回一段像样的译文）
_ZH_BY_SNIPPET = {
    "separation has broken down": "让我们先看看这种分离瓦解得多快。",
    "Global business travel spending": "全球商务旅行支出预计在 2025 年创下 1.57 万亿美元的纪录，而这些行程中越来越多延续到会议之后。",
    "Now, let's talk about the benefits": "接下来谈谈这一趋势为澳大利亚这样的目的地带来的好处。",
    "First, it raises yield": "第一，它提高了收益。",
    "Second, it spreads visitors": "第二，它把游客带出首府城市。",
    "Third, it strengthens resilience": "第三，它增强了韧性。",
}


def _looks_like_source(p: str) -> bool:
    for k in _ZH_BY_SNIPPET:
        if k in p:
            return True
    return False


def chunk_stub(self, user_prompt, system_prompt="", max_tokens=None):
    """按块返回中文；对"收益"那块故意漏译半句，验证逐块审计能抓到。"""
    calls["n"] += 1
    strict = "一句都不能省略" in user_prompt
    if "Second, it spreads visitors" in user_prompt:
        # 先把两块合并成一句（漏掉 "which sustains regional operators"）
        return "第二，它把游客带出首府城市。"
    if "First, it raises yield" in user_prompt and not strict:
        return "第一，它提高了收益。"          # 漏掉后半句 → 长度比过低 → 触发重试
    if "First, it raises yield" in user_prompt and strict:
        return "第一，它提高了收益。商务旅客价格敏感度低、消费水平高，所以每多一晚都是额外出口收入。"
    for k, v in _ZH_BY_SNIPPET.items():
        if k in user_prompt:
            return v
    return "这是一段测试译文。"


AITranslator._chat = chunk_stub
try:
    eng = AITranslator(SETTINGS)
    req = TranslationRequest(text=SPEECH, source_lang="en", target_lang="zh",
                             domain="general")
    long_zh = eng._translate_long_with_context(req, eng._build_system_prompt(req), 200)
finally:
    AITranslator._chat = real_chat

check("分块译文非空", bool(long_zh.strip()), f"{long_zh[:60]!r}")
check("段落结构被拼回（至少 3 个空行分段）", long_zh.count("\n\n") >= 2,
      f"空行数={long_zh.count(chr(10) + chr(10))}")
check("漏半句的那块被重试补齐", "价格敏感度低" in long_zh, f"{long_zh[:120]!r}")
check("块与块之间没有粘连（不出现句号紧跟汉字却无空格/无标点问题）",
      "。。" not in long_zh and not re.search(r"[\u4e00-\u9fff]\.[A-Za-z]", long_zh),
      f"{long_zh[:80]!r}")
check("调用了多次（含重试）", calls["n"] >= 2, f"{calls['n']} 次")

print("\n=== 7. 端到端：某块 AI 返回空，不许把占位符塞进译文 ===")


def flaky_chat(self, user_prompt, system_prompt="", max_tokens=None):
    if _looks_like_source(user_prompt):
        return ""            # 模拟这一段失败（含重试仍失败）
    return "这是一段测试译文。"


AITranslator._chat = flaky_chat
try:
    eng = AITranslator(SETTINGS)
    req2 = TranslationRequest(text=SPEECH, source_lang="en", target_lang="zh",
                              domain="general")
    eng._translate_long_with_context(req2, eng._build_system_prompt(req2), 200)
    errors = list(eng._chunk_errors)
finally:
    AITranslator._chat = real_chat

check("失败块被记录成错误（用户能看到）", bool(errors), f"{errors[:1]}")
check("错误信息里不含占位符文本", all("Translation missing" not in e for e in errors), "")

print("\n=== 8. 端到端：校对把内容改短了 → 拒绝采纳 ===")
qa_calls = {"n": 0}


def qa_shrinks_chat(self, user_prompt, system_prompt="", max_tokens=None):
    qa_calls["n"] += 1
    if "审校" in system_prompt:
        return "太短了。"                 # 校对把译文砍成一句
    return FULL_ZH


AITranslator._chat = qa_shrinks_chat
try:
    eng = AITranslator(SETTINGS)
    res3 = eng.translate(TranslationRequest(
        text=SPEECH, source_lang="en", target_lang="zh", domain="general",
        glossary=[{"term": "bleisure", "translation": "商务休闲"}]))
finally:
    AITranslator._chat = real_chat

check("校对确实被调用了", qa_calls["n"] >= 2, f"调用 {qa_calls['n']} 次")
check("被砍短的校对结果没被采纳", len(res3.text) > 100, f"最终长度 {len(res3.text)}")
check("metadata 标明拒绝了校对", res3.metadata.get("qa_rejected") is not None,
      f"{ {k: v for k, v in res3.metadata.items() if k.startswith('qa')} }")

print("\n=== 9. 校对不再只在有术语表时才跑 + 段落数指令 + 可关闭 ===")
p2 = AITranslator(SETTINGS)._build_chunk_prompt(
    "First para here.\n\nSecond para here.\n\nThird para here.", "en", "zh", None)
check("prompt 里说明了段落数", "共 3 段" in p2 and "同样是 3 段" in p2, p2[:80])
check("prompt 明确要求逐句完整", "不得漏句" in p2, "")

qa_no_glossary = {"n": 0}


def count_qa_chat(self, user_prompt, system_prompt="", max_tokens=None):
    if "审校" in system_prompt:
        qa_no_glossary["n"] += 1
        return FULL_ZH
    return FULL_ZH


AITranslator._chat = count_qa_chat
try:
    eng = AITranslator(SETTINGS)
    # 没有 glossary，但正文成篇 → 仍然应该校对
    eng.translate(TranslationRequest(text=SPEECH, source_lang="en",
                                     target_lang="zh", domain="general"))
finally:
    AITranslator._chat = real_chat
check("没有术语表也会校对（成篇文档）", qa_no_glossary["n"] >= 1, f"校对调用 {qa_no_glossary['n']} 次")

qa_off = {"n": 0}


def count_qa_off(self, user_prompt, system_prompt="", max_tokens=None):
    if "审校" in system_prompt:
        qa_off["n"] += 1
        return FULL_ZH
    return FULL_ZH


AITranslator._chat = count_qa_off
try:
    eng = AITranslator(dict(SETTINGS, ai_full_text_qa=False))
    eng.translate(TranslationRequest(text=SPEECH, source_lang="en",
                                     target_lang="zh", domain="general"))
finally:
    AITranslator._chat = real_chat
check("ai_full_text_qa=false 时不做全文校对", qa_off["n"] == 0, f"校对调用 {qa_off['n']} 次")

print("\n" + "=" * 64)
print(f"PASS: {len(PASS)}   FAIL: {len(FAIL)}")
print("=" * 64)
if FAIL:
    print("失败项：")
    for f in FAIL:
        print("  - " + f)
sys.exit(0 if not FAIL else 1)
