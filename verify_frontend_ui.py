#!/usr/bin/env python
"""前端静态一致性回归套件（零依赖，不需要浏览器/node）。

这套检查针对本项目真实踩过的坑：

  * **点击没反应**：`onclick="markVerdict(this)"` 之类引用了不存在的函数，
    按钮渲染出来是死的（历史 bug：裁决按钮 onclick 里塞 JSON.stringify 导致
    属性被截断）。
  * **静默失效**：JS 里 `getElementById('xxx')` 拿到的元素在 HTML 里根本不存在，
    于是 `if (el)` 静默跳过，界面上"什么都没发生"。
  * **重复 id**：两个页面用了同一个 id，`getElementById` 只命中第一个，
    导致结果渲染到隐藏页面里（历史 bug：hitl-result / add-term-*）。
  * **前端丢掉后端字段**：后端返回 `full_translation`，前端一次都没渲染
    （历史 bug：全自动流水线"没有第四步"）。

用法：
    python verify_frontend_ui.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

HTML_PATH = Path(__file__).resolve().parent / "web" / "static" / "index.html"

_PASS = 0
_FAIL = 0


def check(ok: bool, name: str, detail: str = "") -> None:
    global _PASS, _FAIL
    if ok:
        _PASS += 1
        print(f"  [PASS] {name}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {name}" + (f" -- {detail}" if detail else ""))


def main() -> int:
    if not HTML_PATH.is_file():
        print(f"找不到 {HTML_PATH}")
        return 1
    html = HTML_PATH.read_text(encoding="utf-8")
    print(f"检查 {HTML_PATH.name}（{len(html)} 字符）\n")

    # ── 1. 静态 HTML 里的 id 不允许重复 ──
    print("1) id 唯一性")
    static_ids = re.findall(r'\sid="([^"]+)"', html)
    dupes = sorted({i for i in static_ids if static_ids.count(i) > 1})
    check(not dupes, "静态 id 无重复", f"重复: {dupes}")

    # ── 2. JS 里引用的 id 必须存在（静态 HTML 或 JS 字符串内动态生成）──
    print("\n2) getElementById 目标存在性")
    dynamic_ids = set(re.findall(r"""id=\\?["']([A-Za-z][\w-]*)\\?["']""", html))
    known_ids = set(static_ids) | dynamic_ids
    refs = set(re.findall(r"""getElementById\(\s*['"]([^'"]+)['"]\s*\)""", html))
    missing = sorted(r for r in refs if r not in known_ids)
    check(not missing, f"引用的 {len(refs)} 个 id 全部存在", f"缺失: {missing}")

    # ── 3. 内联事件处理引用的函数必须已定义 ──
    print("\n3) 内联事件处理函数已定义")
    defined = set(re.findall(r"\bfunction\s+([A-Za-z_$][\w$]*)\s*\(", html))
    defined |= set(re.findall(r"\b(?:var|let|const)\s+([A-Za-z_$][\w$]*)\s*=\s*"
                              r"(?:async\s*)?(?:function|\()", html))
    # 只取「以函数调用形式出现在事件属性里」的裸标识符
    handlers = set()
    for body in re.findall(r'\son(?:click|change|input|submit)="([^"]*)"', html):
        for name in re.findall(r"(?<![\w.$])([A-Za-z_$][\w$]*)\s*\(", body):
            handlers.add(name)
    # onclick 也可能写在 JS 模板串里
    for body in re.findall(r"""onclick=\\?["']([^"']*)\\?["']""", html):
        for name in re.findall(r"(?<![\w.$])([A-Za-z_$][\w$]*)\s*\(", body):
            handlers.add(name)

    builtins = {
        "if", "for", "while", "return", "typeof", "alert", "confirm", "prompt",
        "parseInt", "parseFloat", "String", "Number", "Boolean", "Array", "Object",
        "JSON", "Math", "Date", "encodeURIComponent", "decodeURIComponent", "setTimeout",
        "clearTimeout", "setInterval", "clearInterval", "fetch", "console", "Promise",
        "isNaN", "RegExp", "Error", "await", "async", "function", "new", "catch",
    }
    undef = sorted(h for h in handlers if h not in defined and h not in builtins)
    check(not undef, f"{len(handlers)} 个内联处理函数全部已定义", f"未定义: {undef}")

    # ── 4. 后端关键字段必须真的被前端渲染 ──
    print("\n4) 后端字段在前端有渲染")
    for field in ["full_translation", "step_status", "glossary", "termbase_terms",
                  "report", "exports", "adjudication", "case"]:
        check(field in html, f"字段 {field} 被前端使用")

    # ── 5. 检查点（HITL）分步 UI 完整性 ──
    print("\n5) 检查点分步 UI")
    for marker, label in [
        ("function renderHitlResume", "裁决后结果渲染函数"),
        ("function hitlShowStage", "阶段切换函数"),
        ("function hitlStepBy", "上一步/下一步"),
        ("data-hitl-card", "阶段卡片"),
        ("data-hitl-next", "「下一步」按钮"),
        ("data-hitl-prev", "「上一步」按钮"),
        ("data-hitl-tab", "阶段标签"),
        ("data-hitl-count", "步数指示"),
        ("HITL_STAGES", "阶段定义表"),
        ("/report'", "报告下载端点引用"),
        ("/export/csv", "CSV 下载端点引用"),
        ("/export/json", "JSON 下载端点引用"),
        ("translation", "裁决表译文列"),
        ("补译", "空译文的补译入口"),
    ]:
        check(marker in html, label, marker)

    # ── 6. 报告/导出下载必须真的指向接口，而不是死链接 ──
    print("\n6) 下载按钮指向真实接口")
    for path, label in [
        ("/api/pipeline/' + encodeURIComponent(sid) + '/report", "报告走 /api/pipeline/{sid}/report"),
        ("/api/pipeline/' + encodeURIComponent(sid) + '/export/csv", "CSV 走 /export/csv"),
        ("/api/pipeline/' + encodeURIComponent(sid) + '/export/json", "JSON 走 /export/json"),
        ("/api/cases/bundle", "个案数据包"),
    ]:
        check(path in html, label, path)

    # ── 7. 多容器并存：裁决表与结果容器不能互相覆盖成同一个 id ──
    print("\n7) 双页面渲染隔离")
    check("['hitl-table', 'extract-table']" in html, "裁决表同时渲染两个容器")
    check("'pipeline-hitl-result', 'hitl-result'" in html, "结果同时渲染两个容器")
    check("data-role" in html, "新增术语表单用 data-role 作用域（避免重复 id）")

    # ── 8. 阶段条必须有 skipped/failed 的真实状态，不能一律绿 ──
    print("\n8) 阶段条如实反映失败")
    check("=== 'failed'" in html or "=== 'failed'" in html.replace(" ", ""),
          "阶段条有 failed 分支")
    check("'skipped'" in html, "阶段条有 skipped 分支")

    # ── 9. 「提取不出来」必须可解释、可挽回 ──
    print("\n9) 提取诊断与挽回入口")
    for marker, label in [
        ('id="hitl-diagnosis"', "诊断面板容器"),
        ("function renderHitlDiagnosis", "诊断渲染函数"),
        ("function addBackRejected", "「加回候选」函数"),
        ("_hitlRejected", "保存 AI 剔除项的状态"),
        ("_hitlDiagnosis", "保存诊断结果的状态"),
        ("diagnostics", "使用后端 diagnostics 字段"),
        ("全部加回候选", "加回按钮文案"),
        ("没提取到术语", "零结果时给出原因而不是静默"),
    ]:
        check(marker in html, label, marker)
    check(html.count("_hitlRejected = data.rejected") >= 2,
          "两处入口（流水线页 / 提取页）都写入 rejected",
          f"出现 {html.count('_hitlRejected = data.rejected')} 次")

    # ── 10. 流水线模板下拉框应已移除（与顶部模式切换重复）──
    print("\n10) 已删除重复的「流水线模板」下拉框")
    check('id="pipeline-template"' not in html, "模板 select 已移除")
    check("pipeline-template-wrap" not in html, "模板容器已移除")
    check("stepsByTemplate" not in html, "前端硬编码的模板步骤表已移除")
    check("template: template" not in html, "请求里不再传 template")
    check("运行中…" in html, "全自动进度改为中性文案（不伪造步骤名）")

    # ── 11. 手机适配：只许追加 @media，桌面规则必须一个字节都不动 ──
    print("\n11) 手机适配（桌面视角保持不变）")
    check('name="viewport"' in html and "width=device-width" in html,
          "有 viewport meta（否则手机按 980px 渲染）")

    style = re.search(r"<style[^>]*>([\s\S]*?)</style>", html, re.I)
    css = style.group(1) if style else ""
    begin = css.find("/* ==MOBILE-ADAPT-BEGIN== */")
    mend = css.find("/* ==MOBILE-ADAPT-END== */")
    check(begin > 0, "存在移动端适配块起始标记")
    check(mend > begin > 0, "存在移动端适配块结束标记")
    mobile_css = css[begin:mend] if begin >= 0 and mend > begin else ""
    desktop_css = css[:begin] if begin >= 0 else css

    # 背景接缝修复是**全局**规则（桌面也有同一条线），单独标记，不属于移动端块
    bstart = css.find("/* ==BG-SEAM-FIX-BEGIN== */")
    bend = css.find("/* ==BG-SEAM-FIX-END== */")
    bgfix = css[bstart:bend] if bstart >= 0 and bend > bstart else ""
    check(bool(bgfix), "存在背景接缝修复块（全局，桌面与手机都生效）")
    check("min-height: 100" in bgfix and "100dvh" in bgfix,
          "body 至少铺满一屏（dvh 兼容移动端地址栏）")
    check("background-repeat: no-repeat" in bgfix,
          "深色主题不再平铺背景（background 简写重置 attachment 的后果）")

    # 不变式：把 @media 组剥掉之后，移动端块里不允许剩下任何顶层规则。
    # 换句话说，所有新增声明都被关在媒体查询里 —— 桌面不可能被影响。
    stripped, depth, i = [], 0, 0
    while i < len(mobile_css):
        ch = mobile_css[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        elif depth == 0:
            stripped.append(ch)
        i += 1
    top_level = re.sub(r"/\*[\s\S]*?\*/", "", "".join(stripped))
    # 剥完后顶层只应剩下 @media 关键字与空白
    leftovers = re.sub(r"@media[^{]*", "", top_level)
    leftovers = re.sub(r"\s+", "", leftovers)
    check(leftovers == "", "移动端块里没有任何顶层规则（全部在 @media 内）",
          f"残留: {leftovers[:60]!r}")
    check("max-width:640px" in mobile_css, "有 640px 断点")
    check("max-width:768px" in mobile_css, "有 768px 断点（触摸目标）")

    # 桌面规则里不许出现移动端才该有的选择器
    for bad in ("#watermark-grid{opacity", ".glossary-grid", ".hitl-grid"):
        check(bad not in desktop_css, f"桌面 CSS 里没有 {bad}")

    # 列数必须与表格类名对齐：8 列表用 hitl-grid，4 列表用 glossary-grid。
    # 混用会让 nth-child(3) 误伤译文列（真实踩过的坑）。
    check(html.count('class="hitl-grid"') == 2,
          "恰好 2 张 8 列裁决表带 hitl-grid", f"{html.count('class=\"hitl-grid\"')} 张")
    check('class="glossary-grid"' in html, "4 列术语表用 glossary-grid")
    check("hitl-grid glossary-grid" not in html,
          "4 列表没有混用 hitl-grid（否则译文列会被隐藏）")
    check("nth-child(3)" in mobile_css and "glossary-grid" in mobile_css,
          "两类表的列隐藏规则各自独立")

    # 关键移动端修复项
    for needle, why in [
        (".hero h1", "hero 标题在窄屏缩小"),
        ("#watermark-grid", "手机上压暗星空白纹"),
        ("min-height: 40px", "触摸目标不小于 40px"),
        ("-webkit-overflow-scrolling", "表格可滑动"),
        ("white-space: nowrap; font-size: 13px", "术语列不再竖排成单字"),
        ("text-overflow: ellipsis", "长会话 id 截断"),
    ]:
        check(needle in mobile_css, why, needle)

    # ── 12. 词典搜索：两段式渲染（先出词典结果，再补 AI）──
    print("\n12) 词典搜索两段式渲染")
    for marker, label in [
        ("fast: true", "快路径请求（跳过 AI）"),
        ("search-partial-note", "「AI 结果加载中」提示容器"),
        ("function _searchRender", "结果渲染抽成函数（可二次渲染）"),
        ("function _searchRenderAssoc", "关联词渲染抽成函数"),
        ("_searchCache", "客户端搜索结果缓存"),
        ("_searchSeq", "只认最后一次搜索（防慢响应覆盖新结果）"),
        ("Promise.race", "维基摘要限时等待，不阻塞关联词"),
    ]:
        check(marker in html, label, marker)
    check("fast: bool = False" in open(
        Path(__file__).resolve().parent / "termprep" / "web" / "server.py",
        encoding="utf-8").read(), "后端 /api/search 支持 fast 参数")
    check("skip_ai" in open(
        Path(__file__).resolve().parent / "termprep" / "searcher.py",
        encoding="utf-8").read(), "searcher 支持 skip_ai")
    check("_cached_source" in open(
        Path(__file__).resolve().parent / "termprep" / "searcher.py",
        encoding="utf-8").read(), "单源结果带 TTL 缓存（重复查询秒回）")

    print("\n" + "=" * 62)
    print(f"PASS: {_PASS}   FAIL: {_FAIL}")
    print("=" * 62)
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
