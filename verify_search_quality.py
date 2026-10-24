"""搜索提速的「质量守卫」回归测试。

用户明确要求：**保证质量的同时提速**。

因此这里锁死两条底线：

1. **最终结果必须完整**——快路径（deadline 900ms）可以只返回先到的源，
   但**完整请求不得设截止**，所有源（含慢源）与 AI 的结果都必须出现在最终结果里。
   换言之：首屏可以「先给一部分」，但最终一屏必须和优化前逐条一致。
2. **缓存不得吞掉本来能查到的结果**——空结果只缓存很短时间，AI 的空结果不缓存，
   避免一次瞬时故障让用户在一段时间内查不到词。
"""

import os
import sys
import time

_TMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_hitl_tmp")
os.environ.setdefault("TERMPREP_DATA_DIR", _TMP)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from termprep import searcher as S  # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))


class FakeResult:
    def __init__(self, word, definition, source):
        self.query = word
        self.word = word
        self.word_type = "term"
        self.source = source
        self.score = 0.9
        self.definition = definition


class FakeSource:
    """name / delay / results —— 用延迟模拟线上真实情况。"""

    def __init__(self, name, delay, results, available=True):
        self.name = name
        self.delay = delay
        self._results = results
        self.available = available
        self.calls = 0

    def search(self, term, limit=10):
        self.calls += 1
        time.sleep(self.delay)
        return [FakeResult(w, d, self.name) for w, d in self._results]


FAST = FakeSource("youdao", 0.05, [("译后编辑工作量", "post-editing effort")])
SLOW = FakeSource("wikipedia", 1.4, [("译后编辑", "post-editing")])   # 慢于 900ms 截止
AI = [("编后编辑", "post-editing 的口语说法")]

_real_sources = S.get_available_sources


def install(sources):
    S.get_available_sources = lambda: sources
    S.clear_source_cache()


def ai_stub(*_a, **_kw):
    time.sleep(0.2)


print("=== 1. 快路径：可以只给一部分，但不得超过截止时间 ===")
install([FAST, SLOW])
t0 = time.time()
fast = S.search_multi("post-editing effort", limit=10, skip_ai=True, deadline_ms=900)
t_fast = time.time() - t0
check("快路径在 900ms 左右返回", t_fast < 1.3, f"{t_fast:.2f}s")
check("快路径包含快源的结果", any(r["word"] == "译后编辑工作量" for r in fast["results"]),
      f"{[r['word'] for r in fast['results']]}")
check("慢源被标记为 pending", "wikipedia" in fast.get("sources_pending", []),
      f"pending={fast.get('sources_pending')}")

print("\n=== 2. 完整请求：不设截止 → 慢源结果必须也在（质量底线）===")
install([FAST, SLOW])
t0 = time.time()
full = S.search_multi("post-editing effort", limit=10, ai_callable=lambda: [
    {"word": w, "type": "translation", "definition": d, "score": 0.9} for w, d in AI
])
t_full = time.time() - t0
words = [r["word"] for r in full["results"]]
check("完整请求等到了慢源", "译后编辑" in words, f"{words}")
check("完整请求含快源结果", "译后编辑工作量" in words, f"{words}")
check("完整请求含 AI 结果", "编后编辑" in words, f"{words}")
check("没有源被标记 pending", full.get("sources_pending") == [],
      f"{full.get('sources_pending')}")
check("三个来源都在 sources_used", len(full.get("sources_used", [])) == 3,
      f"{full.get('sources_used')}")

print("\n=== 3. 缓存：命中后不再请求源（提速），但不得改变结果 ===")
install([FAST, SLOW])
S.search_multi("term-x", limit=10, skip_ai=True, deadline_ms=900)   # 冷
time.sleep(1.6)                                                     # 让慢源线程写完缓存
fast_calls_1 = FAST.calls
t0 = time.time()
warm = S.search_multi("term-x", limit=10, skip_ai=True, deadline_ms=900)
t_warm = time.time() - t0
check("二次请求很快（命中缓存）", t_warm < 1.0, f"{t_warm:.2f}s")
check("慢源缓存后不再被请求或更快命中", "wikipedia" in warm.get("sources_used", []) or
      "wikipedia" in warm.get("sources_pending", []), f"{warm.get('sources_used')}")

print("\n=== 4. 空结果缓存要短：不能把「本来能查到」的词长期判死 ===")
check("空结果 TTL 很短（<=30s）", S._EMPTY_TTL <= 30, f"{S._EMPTY_TTL}s")
EMPTY = FakeSource("youdao", 0.0, [])
install([EMPTY])
S.search_multi("empty-term", limit=10, skip_ai=True)
n1 = EMPTY.calls
# 模拟瞬时故障恢复：源开始有结果
EMPTY._results = [("恢复", "recovered")]
S._SOURCE_CACHE.clear()          # 只清缓存以验证「恢复后能查到」
res = S.search_multi("empty-term", limit=10, skip_ai=True)
check("源恢复后能查到结果（缓存不会长期判死）",
      any(r["word"] == "恢复" for r in res["results"]), f"{[r['word'] for r in res['results']]}")

print("\n=== 5. AI 的空结果不缓存（模型抖动不该粘住）===")
install([FAST])
calls = {"n": 0}


def flaky_ai():
    calls["n"] += 1
    return [] if calls["n"] == 1 else [{"word": "第二次才有", "type": "translation",
                                        "definition": "second try", "score": 0.9}]


S.search_multi("ai-term", limit=10, ai_callable=flaky_ai)
r2 = S.search_multi("ai-term", limit=10, ai_callable=flaky_ai)
check("AI 首次空、二次有结果 → 最终能拿到",
      any(x["word"] == "第二次才有" for x in r2["results"]),
      f"calls={calls['n']} results={[x['word'] for x in r2['results']]}")

S.get_available_sources = _real_sources
S.clear_source_cache()

print("\n" + "=" * 62)
print(f"PASS: {len(PASS)}   FAIL: {len(FAIL)}")
print("=" * 62)
if FAIL:
    print("失败项：")
    for f in FAIL:
        print("  - " + f)
sys.exit(0 if not FAIL else 1)
