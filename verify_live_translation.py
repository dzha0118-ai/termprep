"""线上全文翻译质量体检（用真实 AI，可选）。

用法：
    python verify_live_translation.py

它会：
  1. 把一段真实的英文讲稿送进线上 Space 的检查点流水线；
  2. 取回 full_translation，逐项体检：段落是否保留、有无「汉字 空格 汉字」断裂、
     行末是否都有标点、关键信息（金额/百分比/术语）是否都在；
  3. 打印 translation_qa.audit（句数守恒 coverage）。

注意：这是**写入型**探测，会在线上新建一个 qualitycheck-<时间> 术语库，
并消耗一次 AI 额度。本地零依赖的回归网是 verify_translation_quality.py。
"""
import json
import re
import time
import urllib.error
import urllib.request

BASE = "https://dzha0118-termprep.hf.space"
TAG = "qualitycheck-" + time.strftime("%m%d%H%M")

SRC = """Let's look first at how quickly this separation has broken down.
Global business travel spending is expected to reach a record US$1.57 trillion in 2025, and a growing share of those trips now continues after the meeting ends. In an international survey published in 2024, 74% of frequent business travellers said they had added leisure days to a work trip, and Marriott reports that business guests now stay roughly 20% longer than they did a few years ago. The business trip is quietly becoming a blended journey.
Now, let's talk about the benefits this trend creates for a destination like Australia.
First, it raises yield. Business travellers are famously low in price sensitivity and high in expenditure, so every extra night is additional export income. Second, it spreads visitors beyond the capital cities. A conference in Sydney or Melbourne can become a weekend in the Blue Mountains or a few days on the Great Barrier Reef, which sustains regional operators. Third, it strengthens resilience. Extended stays fill weekend and shoulder-season capacity, smoothing the seasonality that has always troubled the visitor economy.
However, this trend also brings three new challenges.
The first is a data blind spot: our national statistics still separate business and leisure travel, so the leisure spending of a business delegate is almost invisible. The second is duty of care. Once an employee stays on for personal days, who is responsible for their safety, insurance and medical costs? The third is legitimacy. If a work trip quietly funds a family holiday, companies leave themselves open to criticism over cost control and carbon emissions.
So, how can we move forward?
I believe Australia should focus on three priorities.
First, measure it - add questions on extended stays and accompanying travellers to our national visitor surveys.
Second, design for it - offer flexible room rates, weekend add-on packages and regional itineraries at the point of booking.
Third, govern it - publish clear guidance on duty of care, insurance and sustainability for extended business trips.
To conclude, bleisure is not a passing fashion; it is a structural change in the way business tourism creates value. The future lies in integration - treating the business delegate and the leisure visitor as one person rather than two separate markets. If Australia can measure, design for and govern this shift, business tourism will keep paying dividends long after the closing session.
Thank you very much for listening. I'd be happy to answer any questions."""


def post(p, payload, timeout=900):
    req = urllib.request.Request(BASE + p, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:500]


print(f"源文本: {len(SRC)} 字符 / {len([l for l in SRC.splitlines() if l.strip()])} 行")
print(f"术语库名: {TAG}\n")

st, start = post("/api/pipeline/start",
                 {"text": SRC, "project_name": TAG, "top_n": 12})
print("start:", st)
if st != 200:
    print(start)
    raise SystemExit(1)
sid = start["session_id"]
terms = start["terms"]
print(f"候选术语 {len(terms)} 条")

items = [{"word": t["word"], "verdict": "accepted", "freq": t.get("freq"),
          "type": t.get("type"), "score": t.get("score")} for t in terms]

t0 = time.time()
st, res = post(f"/api/pipeline/{sid}/resume", {
    "terms": items, "project_name": TAG, "top_n": 12,
    "translate_mode": "auto", "use_ai": True, "export_formats": ["csv"],
})
print(f"resume: {st}  (耗时 {time.time() - t0:.1f}s)")
if st != 200:
    print(res)
    raise SystemExit(1)

zh = res.get("full_translation") or ""
print(f"\n全文翻译 {len(zh)} 字符")
print("step_status:", [(s["name"], s["status"]) for s in res.get("step_status", [])])
print("errors:", res.get("errors"))
qa = res.get("translation_qa") or {}
print("translation_qa:", json.dumps(qa, ensure_ascii=False)[:300])
print("\n" + "=" * 78)
print(zh)
print("=" * 78)

# ── 自动体检 ──
print("\n--- 自动体检 ---")


def chk(cond, label, extra=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + (f"  << {extra}" if extra else ""))


paras = [ln for ln in zh.split("\n") if ln.strip()]
chk(len(paras) >= 5, f"段落/分行数 {len(paras)}（原文 13 行；旧版糊成一大段）")
chk("Translation missing" not in zh, "无占位符")
# 旧版把两句粘成 "混合型旅行 现在，" —— 检查是否还有「汉字 空格 汉字」被空格隔开
spaced = re.findall(r"[\u4e00-\u9fff] [\u4e00-\u9fff]", zh)
chk(not spaced, "没有「汉字 空格 汉字」的断裂", f"{spaced[:5]}")
# 行末缺句号
bad_end = [ln for ln in zh.split("\n") if ln.strip()
           and re.search(r"[\u4e00-\u9fff]$", ln.strip())
           and not ln.strip().endswith(("。", "！", "？", "：", "”", "）"))]
chk(not bad_end, "每段行末都有标点", f"{[b[-12:] for b in bad_end[:3]]}")

# 关键信息点是否都在（旧版丢了 "smoothing the seasonality"）
MUST = {
    "1.57": "金额 1.57 万亿",
    "74%": "调查比例",
    "20%": "停留时长",
    "大堡礁": "地名",
    "注意义务": "duty of care",
    "延长停留": "extended stays",
    "正当性": "legitimacy",
    "季节性": "smoothing the seasonality（旧版整句丢失）",
    "随行": "accompanying travellers",
    "融合": "integration（旧版作「一体化」，可接受）",
}
for needle, what in MUST.items():
    ok = needle in zh or (needle == "融合" and "一体化" in zh)
    chk(ok, f"含关键信息：{what}（{needle}）")
