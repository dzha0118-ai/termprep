"""Pre-translation preparation report generator.

Combines project analysis, term extraction, and termbase stats into
a comprehensive markdown report suitable for printing or sharing.
"""

from datetime import datetime
from typing import Any

from termprep.analyzer import AnalysisResult
from termprep.extractor import TermEntry


def generate_report(
    analysis: AnalysisResult | None = None,
    terms: list[TermEntry] | None = None,
    db_stats: dict[str, Any] | None = None,
    project_name: str = "Untitled Project",
    source_file: str = "",
    notes: str = "",
) -> str:
    """Generate a pre-translation preparation report in Markdown.

    Args:
        analysis: Result from analyzer.analyze().
        terms: Result from extractor.extract().
        db_stats: Result from TermDB.get_stats().
        project_name: Name of the translation project.
        source_file: Path to the source file.
        notes: Additional project notes.

    Returns:
        Markdown-formatted report string.
    """
    lines: list[str] = []
    lines.append(f"# Pre-Translation Report: {project_name}")
    lines.append("")
    lines.append(f"*Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}*")
    lines.append("")
    lines.append("---")
    lines.append("")

    # 1. Project Overview
    lines.append("## 1. Project Overview")
    lines.append("")
    if source_file:
        lines.append(f"- **Source File:** `{source_file}`")
    lines.append(f"- **Project:** {project_name}")
    if notes:
        lines.append(f"- **Notes:** {notes}")
    lines.append("")

    # 2. Text Analysis
    if analysis:
        lines.append("## 2. Text Analysis")
        lines.append("")
        lines.append("| Metric | Value |")
        lines.append("|--------|-------|")
        lines.append(f"| Language | {analysis.lang} |")
        lines.append(f"| Total Characters | {analysis.chars_total} |")
        lines.append(f"| Characters (no spaces) | {analysis.chars_no_space} |")
        lines.append(f"| Chinese Words (est.) | {analysis.words_cn} |")
        lines.append(f"| English Words | {analysis.words_en} |")
        lines.append(f"| Paragraphs | {analysis.paragraphs} |")
        lines.append(f"| Sentences | {analysis.sentences} |")
        lines.append(f"| **Domain** | **{analysis.domain}** |")
        lines.append(f"| **Difficulty** | **{analysis.difficulty}** |")
        lines.append("")

        # Domain scores
        if analysis.domain_scores:
            lines.append("### Domain Scores")
            lines.append("")
            for domain, score in sorted(
                analysis.domain_scores.items(), key=lambda x: x[1], reverse=True
            ):
                bar = "█" * int(score * 20) + "░" * (20 - int(score * 20))
                lines.append(f"- **{domain}:** {bar} {score:.1%}")
            lines.append("")

    # 3. Extracted Terms
    if terms:
        lines.append(f"## 3. Extracted Terms (Top {len(terms)})")
        lines.append("")
        lines.append("| # | Term | Freq | Score | Type |")
        lines.append("|---|------|------|-------|------|")
        for i, t in enumerate(terms[:50], 1):  # Top 50 in report
            lines.append(f"| {i} | {t.term} | {t.frequency} | {t.score:.2f} | {t.word_type} |")
        lines.append("")

        # Group by type
        type_groups: dict[str, list[TermEntry]] = {}
        for t in terms:
            type_groups.setdefault(t.word_type, []).append(t)
        if len(type_groups) > 1:
            lines.append("### Terms by Type")
            lines.append("")
            for ttype, tlist in type_groups.items():
                lines.append(f"- **{ttype}:** {', '.join(t.term for t in tlist[:10])}")
            lines.append("")

    # 4. Termbase
    if db_stats:
        lines.append(f"## 4. Termbase: {db_stats.get('name', 'terms')}")
        lines.append("")
        lines.append("| Metric | Value |")
        lines.append("|--------|-------|")
        lines.append(f"| Total Terms | {db_stats.get('total_terms', 0)} |")
        lines.append(f"| Confirmed | {db_stats.get('confirmed', 0)} |")
        lines.append(f"| Associations | {db_stats.get('associations', 0)} |")
        lines.append(f"| Domain | {db_stats.get('domain', '-')} |")
        lines.append("")

        by_domain = db_stats.get("by_domain", {})
        if by_domain:
            lines.append("### Terms by Domain")
            lines.append("")
            for domain, count in by_domain.items():
                d = domain if domain else "uncategorized"
                lines.append(f"- **{d}:** {count}")
            lines.append("")

        by_status = db_stats.get("by_status", {})
        if by_status:
            lines.append("### Terms by Status")
            lines.append("")
            for status, count in by_status.items():
                lines.append(f"- **{status}:** {count}")
            lines.append("")

        recent = db_stats.get("recent", [])
        if recent:
            lines.append("### Recently Added")
            lines.append("")
            lines.append("| Word | Translation | Status |")
            lines.append("|------|-------------|--------|")
            for r in recent[:10]:
                lines.append(f"| {r.get('word', '')} | {r.get('translation', '')} | {r.get('status', '')} |")
            lines.append("")

    # 5. Recommendations
    lines.append("## 5. Recommendations")
    lines.append("")
    recs: list[str] = []

    if analysis:
        if analysis.difficulty == "hard":
            recs.append(
                "- **Allow extra time.** This text is rated **hard** "
                "due to long sentences and/or technical domain."
            )
        if analysis.lang == "mixed":
            recs.append(
                "- **Verify language pairs.** Mixed Chinese/English text "
                "may require glossaries for both directions."
            )
        if analysis.domain not in ("general", ""):
            recs.append(
                f"- **Review domain terminology.** "
                f"The text is in the **{analysis.domain}** domain. "
                f"Ensure you have domain-specific glossaries ready."
            )

    if db_stats:
        if db_stats.get("confirmed", 0) < db_stats.get("total_terms", 0) * 0.5:
            recs.append(
                f"- **Review unconfirmed terms.** "
                f"Only {db_stats.get('confirmed', 0)} of "
                f"{db_stats.get('total_terms', 0)} terms are confirmed."
            )

    if terms:
        recs.append(
            f"- **Check high-frequency terms.** "
            f"The most frequent term '{terms[0].term if terms else ''}' "
            f"appears {terms[0].frequency if terms else 0} times."
        )

    if not recs:
        recs.append("- No specific recommendations. Proceed with standard preparation.")

    lines.extend(recs)
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("*Report generated by TermPrep v0.3*")

    return "\n".join(lines)


def save_report(report: str, output_path: str) -> None:
    """Save the report to a file."""
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(report)
