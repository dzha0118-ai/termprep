"""Full automated pre-translation pipeline.

One command to run the entire workflow:
  analyze -> extract -> search -> build termbase -> generate report -> export
"""

import time
from dataclasses import dataclass, field

from termprep.analyzer import AnalysisResult, analyze, analyze_file
from termprep.extractor import TermEntry, extract, extract_file
from termprep.searcher import search_term as do_search
from termprep.db import TermDB, init_termbase
from termprep.report import generate_report, save_report


@dataclass
class PipelineResult:
    """Result of a full pipeline run."""
    project_name: str = ""
    source_file: str = ""
    analysis: AnalysisResult | None = None
    terms: list[TermEntry] = field(default_factory=list)
    termbase_name: str = ""
    termbase_terms: int = 0
    report_path: str = ""
    exports: dict[str, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    duration: float = 0.0
    search_results_count: int = 0


def run_pipeline(
    file_path: str | None = None,
    text: str | None = None,
    project_name: str = "Untitled",
    top_n: int = 20,
    search_limit: int = 5,
    db_name: str | None = None,
    report_output: str = "",
    export_formats: list[str] | None = None,
    notes: str = "",
) -> PipelineResult:
    """Run the full pre-translation preparation pipeline.

    Steps:
      1. Analyze source text (language, domain, difficulty)
      2. Extract key terms
      3. Search each extracted term via all configured sources
      4. Build/update a termbase with results
      5. Generate a preparation report
      6. Export termbase to requested formats

    Args:
        file_path: Path to source file.
        text: Direct source text (used if file_path is None).
        project_name: Name for this project.
        top_n: Number of terms to extract.
        search_limit: Search results per term.
        db_name: Termbase name (defaults to project name).
        report_output: Path for the report file.
        export_formats: List of export formats (e.g. ["csv", "xlsx"]).
        notes: Additional project notes.

    Returns:
        PipelineResult with all outputs.
    """
    result = PipelineResult(
        project_name=project_name,
        source_file=file_path or "",
    )
    start = time.time()

    # ---- Step 1: Analyze ----
    try:
        if file_path:
            result.analysis = analyze_file(file_path)
        elif text:
            result.analysis = analyze(text)
        else:
            result.errors.append("No input provided (file or text required)")
            return result
    except Exception as e:
        result.errors.append(f"Analysis failed: {e}")
        return result

    # ---- Step 2: Extract Terms ----
    try:
        if file_path:
            result.terms = extract_file(file_path, top_n=top_n)
        else:
            result.terms = extract(text, top_n=top_n)  # type: ignore[arg-type]
    except Exception as e:
        result.errors.append(f"Extraction failed: {e}")

    # ---- Step 3: Search Terms ----
    if result.terms:
        search_count = 0
        for term_entry in result.terms:
            try:
                term_results = do_search(term_entry.term, source="web", limit=search_limit)
                if term_results:
                    search_count += 1
                    # Store results in memory for the report
                    term_entry.search_results = term_results
            except Exception:
                continue
        result.search_results_count = search_count

    # ---- Step 4: Build Termbase ----
    tdb_name = db_name or project_name.lower().replace(" ", "-")
    try:
        # Try to create a new termbase, or use existing one
        try:
            tdb = init_termbase(tdb_name, domain=result.analysis.domain if result.analysis else "")
        except FileExistsError:
            tdb = TermDB(db_name=tdb_name)

        # Add extracted terms as confirmed entries
        added = 0
        for term_entry in result.terms:
            # Try to find a translation from search results
            translation = ""
            if hasattr(term_entry, 'search_results') and term_entry.search_results:
                for sr in term_entry.search_results:
                    if sr.get("type") == "translation":
                        translation = sr.get("word", "")
                        break

            try:
                tdb.add_term(
                    word=term_entry.term,
                    translation=translation,
                    domain=result.analysis.domain if result.analysis else "",
                    type_=term_entry.word_type,
                    status="draft",
                )
                added += 1
            except Exception:
                continue

        result.termbase_name = tdb_name
        result.termbase_terms = added
    except Exception as e:
        result.errors.append(f"Termbase failed: {e}")

    # ---- Step 5: Generate Report ----
    try:
        tdb = TermDB(db_name=tdb_name)
        db_stats = tdb.get_stats()

        if not report_output:
            report_output = f"{project_name.lower().replace(' ', '-')}-report.md"

        report_text = generate_report(
            analysis=result.analysis,
            terms=result.terms,
            db_stats=db_stats,
            project_name=project_name,
            source_file=file_path or "",
            notes=notes,
        )
        save_report(report_text, report_output)
        result.report_path = report_output
    except Exception as e:
        result.errors.append(f"Report generation failed: {e}")

    # ---- Step 6: Export ----
    if export_formats:
        tdb = TermDB(db_name=tdb_name)
        for fmt in export_formats:
            try:
                fmt = fmt.lower().strip(".")
                out_path = f"{project_name.lower().replace(' ', '-')}-terms.{fmt}"
                if fmt == "csv":
                    from termprep.exporter import export_csv
                    export_csv(tdb, out_path)
                elif fmt == "xlsx":
                    from termprep.exporter import export_xlsx
                    export_xlsx(tdb, out_path)
                elif fmt == "tbx":
                    from termprep.exporter import export_tbx
                    export_tbx(tdb, out_path)
                elif fmt == "json":
                    from termprep.exporter import export_json
                    export_json(tdb, out_path)
                else:
                    result.errors.append(f"Unknown format: {fmt}")
                    continue
                result.exports[fmt] = out_path
            except Exception as e:
                result.errors.append(f"Export {fmt} failed: {e}")

    result.duration = time.time() - start
    return result


def format_pipeline_result(result: PipelineResult) -> str:
    """Format a PipelineResult into a readable summary."""
    lines: list[str] = []
    lines.append(f"Pipeline complete: {result.project_name}")
    lines.append(f"Duration: {result.duration:.1f}s")
    lines.append("")

    if result.analysis:
        lines.append(f"  Analysis: {result.analysis.lang}, {result.analysis.domain}, "
                     f"difficulty={result.analysis.difficulty}")
        lines.append(f"  Characters: {result.analysis.chars_total} | "
                     f"Words: {result.analysis.words_cn + result.analysis.words_en}")

    if result.terms:
        lines.append(f"  Terms extracted: {len(result.terms)}")
        lines.append(f"  Terms searched: {result.search_results_count}")
        lines.append(f"  Top terms: {', '.join(t.term for t in result.terms[:10])}")

    if result.termbase_name:
        lines.append(f"  Termbase: {result.termbase_name} ({result.termbase_terms} terms added)")

    if result.report_path:
        lines.append(f"  Report: {result.report_path}")

    if result.exports:
        for fmt, path in result.exports.items():
            lines.append(f"  Export [{fmt}]: {path}")

    if result.errors:
        lines.append("")
        lines.append("  Errors:")
        for err in result.errors:
            lines.append(f"    - {err}")

    return "\n".join(lines)
