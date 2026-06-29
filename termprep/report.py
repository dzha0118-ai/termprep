"""Pre-translation preparation report generator — rich agent-grade report."""

from datetime import datetime
from typing import Any

from termprep.analyzer import AnalysisResult
from termprep.extractor import TermEntry


def generate_report(
    analysis: AnalysisResult | None = None,
    terms: list[TermEntry] | None = None,
    glossary: list[dict] | None = None,
    full_translation: str = "",
    db_stats: dict[str, Any] | None = None,
    project_name: str = "Untitled Project",
    source_file: str = "",
    notes: str = "",
    translation_analysis: dict[str, Any] | None = None,
    segments: list[dict] | None = None,
    source_text: str = "",
    translation_qa: dict[str, Any] | None = None,
) -> str:
    """Generate a comprehensive agent-grade pre-translation preparation report.

    The report is structured for both human readers and downstream AI agents:
    - Full bilingual text (source + translation)
    - Complete term extraction with rich metadata
    - Translation difficulty analysis with per-segment breakdown
    - Specific, actionable translation strategy recommendations
    """
    lines: list[str] = []
    _w = lines.append

    # ════════════════════════════════════════════════════════════════
    # HEADER
    # ════════════════════════════════════════════════════════════════
    _w(f"# 译前准备报告 / Pre-Translation Report: {project_name}")
    _w("")
    _w(f"*生成时间 / Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}*")
    _w("")
    _w("---")

    # ════════════════════════════════════════════════════════════════
    # 1. 项目概览 / Project Overview
    # ════════════════════════════════════════════════════════════════
    _w("## 1. 项目概览 / Project Overview")
    _w("")
    if source_file:
        _w(f"- **源文件 / Source File:** `{source_file}`")
    _w(f"- **项目名称 / Project:** {project_name}")
    if notes:
        _w(f"- **备注 / Notes:** {notes}")
    _w("")

    # ════════════════════════════════════════════════════════════════
    # 2. 文本分析 / Text Analysis
    # ════════════════════════════════════════════════════════════════
    if analysis:
        _w("## 2. 文本分析 / Text Analysis")
        _w("")
        _w("### 2.1 基础统计 / Basic Statistics")
        _w("")
        _w("| 指标 / Metric | 值 / Value |")
        _w("|---|---|")
        _w(f"| 语言 / Language | {analysis.lang} |")
        _w(f"| 总字符 / Total Characters | {analysis.chars_total} |")
        _w(f"| 纯字符数（去空格）/ Characters (no spaces) | {analysis.chars_no_space} |")
        _w(f"| 中文词数（估）/ Chinese Words (est.) | {analysis.words_cn} |")
        _w(f"| 英文词数 / English Words | {analysis.words_en} |")
        _w(f"| 段落数 / Paragraphs | {analysis.paragraphs} |")
        _w(f"| 句数 / Sentences | {analysis.sentences} |")
        _w("")

        _w("### 2.2 领域识别 / Domain Identification")
        _w("")
        _w("| 领域 / Domain | 匹配分数 / Score |")
        _w("|---|---|")
        for domain, score in sorted(analysis.domain_scores.items(), key=lambda x: -x[1]):
            bar = "█" * int(score * 20) + "░" * (20 - int(score * 20))
            _w(f"| {domain} | {bar} {score:.1%} |")
        _w(f"| **判定结果 / Best Match** | **{analysis.domain}** |")
        _w("")

        _w("### 2.3 难度评估 / Difficulty Assessment")
        _w("")
        difficulty_map = {"easy": "简单 / Easy", "medium": "中等 / Medium", "hard": "困难 / Hard"}
        _w(f"- **整体难度 / Overall:** **{difficulty_map.get(analysis.difficulty, analysis.difficulty)}**")
        if analysis.sentences > 0:
            avg_sent = analysis.chars_no_space / analysis.sentences
            _w(f"- **平均句长 / Avg. Sentence Length:** {avg_sent:.0f} 字符 ({'较长' if avg_sent > 50 else '适中' if avg_sent > 30 else '较短'})")
        total_words = analysis.words_cn + analysis.words_en
        _w(f"- **总词数 / Total Words:** {total_words}")
        if analysis.lang == "mixed":
            _w("- ⚠️ **中英混合 / Mixed Language:** 需注意双向翻译质量")
        if analysis.domain not in ("general", ""):
            _w(f"- ⚠️ **专业领域 / Technical Domain:** {analysis.domain} — 需要领域知识")
        _w("")

    # ════════════════════════════════════════════════════════════════
    # 3. 源文本 / Source Text
    # ════════════════════════════════════════════════════════════════
    if source_text:
        _w("## 3. 源文本 / Source Text")
        _w("")
        _w("```text")
        _w(source_text[:5000])
        if len(source_text) > 5000:
            _w("")
            _w(f"... [源文本过长，已截断至 5000 字符，全文共 {len(source_text)} 字符]")
        _w("```")
        _w("")

    # ════════════════════════════════════════════════════════════════
    # 4. 全文翻译 / Full Translation
    # ════════════════════════════════════════════════════════════════
    if full_translation:
        _w("## 4. 全文翻译 / Full Translation")
        _w("")
        if segments:
            _w("### 4.1 逐段对照 / Segment Alignment")
            _w("")
            for seg in segments:
                src = seg.get("source", "")
                tgt = seg.get("target", "")
                idx = seg.get("index", 0)
                if src and tgt:
                    _w(f"**段落 {idx + 1}**")
                    _w("")
                    _w(f"> **源文 / Source:** {src}")
                    _w(f"> **译文 / Translation:** {tgt}")
                    highlights = seg.get("highlights", [])
                    if highlights:
                        hl_terms = [h.get("term", "") for h in highlights if h.get("term")]
                        if hl_terms:
                            _w(f"  > *术语高亮 / Term Highlights: {', '.join(hl_terms)}*")
                    _w("")
        _w("### 4.2 完整译文 / Full Translated Text")
        _w("")
        _w(full_translation)
        _w("")

    # ════════════════════════════════════════════════════════════════
    # 5. 完整术语提取 / Complete Term Extraction
    # ════════════════════════════════════════════════════════════════
    if terms:
        _w(f"## 5. 术语提取 / Term Extraction ({len(terms)} 项)")
        _w("")
        _w("### 5.1 完整术语列表 / All Extracted Terms")
        _w("")
        _w("| # | 术语 / Term | 频次 / Freq | 评分 / Score | 类型 / Type |")
        _w("|---|---:|---:|---|---|")
        for i, t in enumerate(terms, 1):
            _w(f"| {i} | {t.term} | {t.frequency} | {t.score:.3f} | {t.word_type} |")
        _w("")

        # Group by type
        by_type: dict[str, list[TermEntry]] = {}
        for t in terms:
            tp = t.word_type or "other"
            by_type.setdefault(tp, []).append(t)

        _w("### 5.2 按类型分组 / Grouped by Type")
        _w("")
        for tp, ts in sorted(by_type.items(), key=lambda x: -len(x[1])):
            type_labels = {
                "keyword": "关键词 / Keyword (TF-IDF)",
                "textrank": "TextRank 关键词",
                "phrase": "短语 / Phrase (POS Chunking)",
                "proper_noun": "专有名词 / Proper Noun",
                "yake": "YAKE 关键短语",
                "cvalue": "C-Value 嵌套术语",
            }
            label = type_labels.get(tp, tp)
            _w(f"- **{label}:** {', '.join(t.term for t in ts[:10])}")
            if len(ts) > 10:
                _w(f"  - *...及其他 {len(ts) - 10} 项*")
        _w("")

        # High frequency terms
        high_freq = [t for t in terms if t.frequency >= 3]
        if high_freq:
            _w("### 5.3 高频术语 / High-Frequency Terms (频次 ≥ 3)")
            _w("")
            for t in high_freq:
                _w(f"- **{t.term}** — 出现 {t.frequency} 次 (类型: {t.word_type}, 评分: {t.score:.3f})")
            _w("")

    # ════════════════════════════════════════════════════════════════
    # 6. 双语术语表 / Bilingual Glossary
    # ════════════════════════════════════════════════════════════════
    if glossary:
        _w(f"## 6. 双语术语表 / Bilingual Glossary ({len(glossary)} 项)")
        _w("")
        is_cn = analysis and analysis.lang in ("zh", "mixed")
        src_label = "源词 (中文)" if is_cn else "Source (English)"
        tgt_label = "译词 (English)" if is_cn else "Translation (中文)"
        _w(f"| # | {src_label} | {tgt_label} | 频次 / Freq | 置信度 / Confidence | 上下文 / Context |")
        _w("|---|---|---|---|---|---|")
        for i, g in enumerate(glossary, 1):
            ctx = g.get("context", "")[:50].replace("|", "/").replace("\n", " ")
            conf = g.get("confidence", "") or g.get("status", "")
            trans = g.get("translation", "-") or "-"
            if trans == "[未找到翻译]":
                trans = "⚠️ 未翻译"
            _w(f"| {i} | **{g['term']}** | {trans} | {g.get('freq', '')} | {conf} | {ctx} |")
        _w("")

        # Untranslated terms alert
        untranslated = [g for g in glossary if not g.get("translation") or g.get("translation") in ("[未找到翻译]", "-")]
        if untranslated:
            _w(f"> ⚠️ **{len(untranslated)} 项术语尚待翻译:** {', '.join(g['term'] for g in untranslated[:10])}")
            _w("")

    # ════════════════════════════════════════════════════════════════
    # 7. 翻译难度分析 / Translation Difficulty Analysis
    # ════════════════════════════════════════════════════════════════
    if translation_analysis:
        _w("## 7. 翻译难度分析 / Translation Difficulty Analysis")
        _w("")
        overall = translation_analysis.get("overall_difficulty", "medium")
        diff_labels = {"easy": "🟢 简单", "medium": "🟡 中等", "hard": "🔴 困难"}
        _w(f"### 整体评估 / Overall Assessment: **{diff_labels.get(overall, overall)}**")
        _w("")
        total_seg = translation_analysis.get("total_segments", 0)

        # Segment difficulty breakdown
        seg_analyses = translation_analysis.get("segment_analyses", [])
        hard_count = sum(1 for s in seg_analyses if s.get("difficulty") == "hard")
        medium_count = sum(1 for s in seg_analyses if s.get("difficulty") == "medium")
        easy_count = sum(1 for s in seg_analyses if s.get("difficulty") == "easy")
        _w(f"- 分析段落数 / Segments Analyzed: **{total_seg}**")
        _w(f"- 🔴 困难 / Hard: **{hard_count}** ({hard_count / max(total_seg, 1) * 100:.0f}%)")
        _w(f"- 🟡 中等 / Medium: **{medium_count}** ({medium_count / max(total_seg, 1) * 100:.0f}%)")
        _w(f"- 🟢 简单 / Easy: **{easy_count}** ({easy_count / max(total_seg, 1) * 100:.0f}%)")
        _w("")

        # Overall flags
        flags = translation_analysis.get("overall_flags", [])
        if flags:
            flag_labels = {
                "long_sentence": "长句 / Long Sentences",
                "legal": "法律术语 / Legal Terminology",
                "idiom": "习语 / Idioms",
                "ambiguous": "多义词 / Ambiguous Terms",
                "jargon": "行话 / Buzzwords",
                "cn_four_char_idiom": "中文成语 / Chinese Idioms",
                "cn_idiom": "中文习语 / Chinese Idiomatic Expressions",
                "cn_legal": "中文法律术语 / Chinese Legal Terms",
                "measurement_unit": "度量单位 / Measurement Units",
                "parenthetical": "括号注释 / Parenthetical Expressions",
            }
            _w("### 7.1 检测到的挑战 / Detected Challenges")
            _w("")
            for flag in flags:
                label = flag_labels.get(flag, flag)
                _w(f"- ⚠️ **{label}**")
            _w("")

        # Per-segment breakdown
        if seg_analyses:
            hard_segs = [s for s in seg_analyses if s.get("difficulty") == "hard"]
            med_segs = [s for s in seg_analyses if s.get("difficulty") == "medium"]

            if hard_segs:
                _w("### 7.2 🔴 困难段分析 / Hard Segments")
                _w("")
                for i, seg in enumerate(hard_segs[:5], 1):
                    _w(f"**困难段 {i}:**")
                    _w(f"- 文本片段: `{seg.get('text', '')[:80]}...`")
                    _w(f"- 字符数: {seg.get('char_count', 0)} | 词汇数: {seg.get('word_count', 0)}")
                    sf = seg.get("flags", [])
                    if sf:
                        _w(f"- 标志: {', '.join(sf)}")
                    stf = seg.get("technical_terms_found", [])
                    if stf:
                        _w(f"- 技术术语: {', '.join(stf[:5])}")
                    _w("")

            if med_segs:
                _w("### 7.3 🟡 中等难度段概览 / Medium Segments Overview")
                _w("")
                for i, seg in enumerate(med_segs[:5], 1):
                    _w(f"- 段 {i}: `{seg.get('text', '')[:60]}...`— 标志: {', '.join(seg.get('flags', []))}")
                _w("")

    # ════════════════════════════════════════════════════════════════
    # 8. 翻译建议 / Translation Recommendations
    # ════════════════════════════════════════════════════════════════
    if translation_analysis:
        _w("## 8. 翻译策略建议 / Translation Strategy Recommendations")
        _w("")
        recs = translation_analysis.get("recommendations", [])
        for rec in recs:
            _w(f"### {rec.get('type', '')}")
            _w("")
            _w(f"{rec.get('recommendation', '')}")
            _w("")

    # Fallback: legacy recs if no translation analysis
    if not translation_analysis:
        _w("## 7. 翻译建议 / Translation Recommendations")
        _w("")
        recs: list[str] = []
        if analysis:
            if analysis.difficulty == "hard":
                recs.append("- **建议预留更多时间。** 文本难度为 **困难**，需要充分的译前准备。")
            if analysis.domain not in ("general", ""):
                recs.append(f"- **领域:** {analysis.domain} — 建议准备领域参考材料。")
            if analysis.lang == "mixed":
                recs.append("- **混合语言:** 注意双向翻译的准确性。")
        if glossary:
            untranslated = [g for g in glossary if not g.get("translation")]
            if untranslated:
                recs.append(f"- **{len(untranslated)} 个术语需要手动翻译:** {', '.join(g['term'] for g in untranslated[:5])}")
            high_freq = [g for g in glossary if g.get("freq", 0) > 5]
            if high_freq:
                recs.append(f"- **需优先确认的高频术语:** {', '.join(g['term'] for g in high_freq[:5])}")
        if not recs:
            recs.append("- 无特殊建议，按标准流程准备即可。")
        for r in recs:
            _w(r)
        _w("")

    # ════════════════════════════════════════════════════════════════
    # 9. 术语库 / Termbase
    # ════════════════════════════════════════════════════════════════
    if db_stats:
        _w("## 9. 术语库 / Termbase")
        _w("")
        _w(f"| 指标 / Metric | 值 / Value |")
        _w(f"|---|---|")
        _w(f"| 名称 / Name | {db_stats.get('name', 'terms')} |")
        _w(f"| 总术语数 / Total Terms | {db_stats.get('total_terms', 0)} |")
        _w(f"| 已确认 / Confirmed | {db_stats.get('confirmed', 0)} |")
        _w(f"| 领域 / Domain | {db_stats.get('domain', '-')} |")
        _w(f"| 语言 / Language | {db_stats.get('lang', '-')} |")
        _w("")

    # ════════════════════════════════════════════════════════════════
    # X. Agent 摘要 / Agent Summary (机器可读的结构化摘要)
    # ════════════════════════════════════════════════════════════════
    _w("---")
    _w("## Z. Agent 执行摘要 / Agent Execution Summary")
    _w("")
    _w("以下是为下游 Agent 准备的结构化执行摘要：")
    _w("")
    _w("### 翻译要点 / Key Translation Points")
    _w("")
    if analysis:
        _w(f"- 语言方向: {analysis.lang} → {'英文' if analysis.lang in ('zh', 'mixed') else '中文'}")
        _w(f"- 专业领域: {analysis.domain}")
        _w(f"- 文本难度: {analysis.difficulty}")
    if translation_analysis:
        flags = translation_analysis.get("overall_flags", [])
        if flags:
            _w(f"- 关键注意点: {', '.join(flags[:5])}")
    if glossary:
        _w(f"- 术语表: {len(glossary)} 项术语可供注入翻译提示词")
        confirmed = sum(1 for g in glossary if g.get("confidence") == "high" or g.get("status") == "confirmed")
        _w(f"- 高置信度术语: {confirmed} 项")
    if translation_qa:
        qa_refined = translation_qa.get("qa_refined", False)
        if qa_refined:
            qa_summary = translation_qa.get("qa_summary", {})
            _w(f"- **AI 自检精修**: 翻译后经 AI QA 审校并修正")
            if qa_summary:
                _w(f"  - 原译文长度: {qa_summary.get('original_length', 'N/A')} 字符")
                _w(f"  - 精修后长度: {qa_summary.get('refined_length', 'N/A')} 字符")
                _w(f"  - 词汇替换量: {qa_summary.get('words_replaced', 'N/A')} 项")
        else:
            _w(f"- **AI 自检**: 翻译后经 AI QA 审校，未发现问题")
        provider = translation_qa.get("provider", "")
        model = translation_qa.get("model", "")
        if provider:
            _w(f"- **翻译引擎**: {provider} / {model if model else 'default'}")
    _w("")
    _w("### 翻译执行建议 / Translation Execution Guidance")
    _w("")
    _w("1. **术语一致性**: 翻译时确保使用的术语与上述术语表一致")
    _w("2. **领域适配**: 根据识别到的领域调整翻译风格和术语选择")
    _w("3. **难度优先级**: 标记为困难的段落优先处理、重点校对")
    if translation_analysis:
        recs = translation_analysis.get("recommendations", [])
        for i, rec in enumerate(recs, 1):
            rtype = rec.get("type", "")
            rtext = rec.get("recommendation", "")
            _w(f"4. **{rtype}**: {rtext}")
    _w("")
    _w("---")
    _w("*报告由 TermPrep v0.5 生成 / Report generated by TermPrep v0.5*")
    _w("*本报告同时面向人工阅读和 AI Agent 消费 / This report is designed for both human readers and AI agents*")

    return "\n".join(lines)


def save_report(report: str, output_path: str) -> None:
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(report)
