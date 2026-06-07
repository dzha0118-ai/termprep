"""CLI entry point for TermPrep."""

import os
import click
from pathlib import Path
from rich.console import Console
from rich.table import Table

from termprep.searcher import search_term
from termprep.db import TermDB, list_termbases, init_termbase, delete_termbase
from termprep.analyzer import analyze, analyze_file, get_summary
from termprep.extractor import extract, extract_file, get_frequency_table
from termprep.report import generate_report, save_report
from termprep.pipeline import run_pipeline, format_pipeline_result

# Load .env file if available
_env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
if os.path.exists(_env_path):
    try:
        with open(_env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, val = line.partition("=")
                    os.environ.setdefault(key.strip(), val.strip())
    except Exception:
        pass

console = Console()


@click.group()
def main():
    """TermPrep - \u8bd1\u524d\u672f\u8bed\u51c6\u5907\u52a9\u624b"""


# ============================================================
# search
# ============================================================
@main.command()
@click.argument("term")
@click.option("--source", "-s", default="all", help="\u641c\u7d22\u6e90: local, web, all")
@click.option("--limit", "-n", default=20, help="\u8fd4\u56de\u7ed3\u679c\u6570\u91cf")
def search(term, source, limit):
    """\u641c\u7d22\u672f\u8bed\u53ca\u5173\u8054\u8bcd\u6c47\u3002"""
    results = search_term(term, source=source, limit=limit)

    table = Table(title=f"\u641c\u7d22\u7ed3\u679c: {term}")
    table.add_column("\u5173\u8054\u8bcd", style="cyan")
    table.add_column("\u7c7b\u578b", style="green")
    table.add_column("\u6765\u6e90", style="yellow")
    table.add_column("\u76f8\u5173\u5ea6", style="magenta")

    for r in results:
        table.add_row(r["word"], r["type"], r["source"], f"{r['score']:.2f}")

    console.print(table)


# ============================================================
# batch
# ============================================================
@main.command()
@click.argument("input_file")
@click.option("--output", "-o", default="result.csv", help="\u8f93\u51fa\u6587\u4ef6")
def batch(input_file, output):
    """\u6279\u91cf\u67e5\u8be2\u672f\u8bed\u3002"""
    with open(input_file, "r", encoding="utf-8") as f:
        terms = [line.strip() for line in f if line.strip()]

    results = []
    for term in terms:
        results.extend(search_term(term))

    import csv
    with open(output, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["query", "word", "type", "source", "score"])
        writer.writeheader()
        for r in results:
            writer.writerow(r)

    console.print(f"[green]OK[/green] \u5df2\u5bfc\u51fa {len(results)} \u6761\u7ed3\u679c\u5230 {output}")


# ============================================================
# analyze
# ============================================================
@main.command(name="analyze")
@click.argument("file", required=False)
@click.option("--text", "-t", default="", help="\u76f4\u63a5\u8f93\u5165\u6587\u672c\u5206\u6790")
def _analyze(file, text):
    """\u9879\u76ee\u5206\u6790\uff1a\u8bed\u8a00\u68c0\u6d4b\u3001\u5b57\u6570\u7edf\u8ba1\u3001\u9886\u57df\u8bc6\u522b\u3002"""
    if file:
        result = analyze_file(file)
    elif text:
        result = analyze(text)
    else:
        console.print("[red]\u9519\u8bef:[/red] \u8bf7\u63d0\u4f9b\u6587\u4ef6\u8def\u5f84\u6216 --text \u53c2\u6570")
        return
    console.print(get_summary(result))


# ============================================================
# extract
# ============================================================
@main.command(name="extract")
@click.argument("file", required=False)
@click.option("--text", "-t", default="", help="\u76f4\u63a5\u8f93\u5165\u6587\u672c\u63d0\u53d6")
@click.option("--top", "-n", default=30, help="\u8fd4\u56de\u672f\u8bed\u6570\u91cf")
def _extract(file, text, top):
    """\u672f\u8bed\u63d0\u53d6\uff1a\u9ad8\u9891\u8bcd\u3001N-gram\u3001\u4e13\u6709\u540d\u8bcd\u3002"""
    if file:
        terms = extract_file(file, top_n=top)
    elif text:
        terms = extract(text, top_n=top)
    else:
        console.print("[red]\u9519\u8bef:[/red] \u8bf7\u63d0\u4f9b\u6587\u4ef6\u8def\u5f84\u6216 --text \u53c2\u6570")
        return
    console.print(get_frequency_table(terms))


# ============================================================
# db
# ============================================================
@main.group()
def db():
    """\u672c\u5730\u8bcd\u5e93\u7ba1\u7406\u3002"""


@db.command("list")
def db_list():
    """\u5217\u51fa\u6240\u6709\u672c\u5730\u8bcd\u5e93\u3002"""
    dbs = list_termbases()
    if not dbs:
        console.print("[yellow]\u6682\u65e0\u8bcd\u5e93\u3002\u4f7f\u7528 `termprep db init <name>` \u521b\u5efa\u3002[/yellow]")
        return

    table = Table(title="\u672c\u5730\u8bcd\u5e93")
    table.add_column("\u540d\u79f0", style="cyan")
    table.add_column("\u9886\u57df", style="green")
    table.add_column("\u8bed\u8a00", style="yellow")
    table.add_column("\u672f\u8bed\u6570", style="magenta")
    table.add_column("\u8def\u5f84")

    for d in dbs:
        table.add_row(
            d.name,
            d.domain or "-",
            d.lang or "-",
            str(d.total_terms),
            d.path,
        )
    console.print(table)


@db.command("init")
@click.argument("name")
@click.option("--domain", "-d", default="", help="\u672f\u8bed\u5e93\u9886\u57df")
def db_init(name, domain):
    """\u521b\u5efa\u65b0\u7684\u672c\u5730\u8bcd\u5e93\u3002"""
    try:
        tdb = init_termbase(name, domain=domain)
        console.print(f"[green]OK[/green] \u5df2\u521b\u5efa\u8bcd\u5e93: {tdb.db_path}")
    except FileExistsError as e:
        console.print(f"[red]\u9519\u8bef:[/red] {e}")


@db.command("info")
@click.option("--db", "db_name", default=None, help="\u8bcd\u5e93\u540d\u79f0 (\u9ed8\u8ba4: terms)")
def db_info(db_name):
    """\u67e5\u770b\u5f53\u524d\u8bcd\u5e93\u7edf\u8ba1\u4fe1\u606f\u3002"""
    tdb = TermDB(db_name=db_name) if db_name else TermDB()
    stats = tdb.get_stats()

    console.print(f"[bold]\u8bcd\u5e93:[/bold] {stats['name']}")
    console.print(f"[bold]\u9886\u57df:[/bold] {stats.get('domain', '-')}")
    console.print(f"[bold]\u8def\u5f84:[/bold] {tdb.db_path}")
    console.print("")
    console.print(f"\u672f\u8bed\u603b\u6570: {stats['total_terms']}")
    console.print(f"\u5df2\u786e\u8ba4: {stats['confirmed']}")
    console.print(f"\u5173\u8054\u8bcd: {stats['associations']}")
    console.print("")

    if stats["by_domain"]:
        console.print("[bold]\u6309\u9886\u57df:[/bold]")
        for domain, count in stats["by_domain"].items():
            console.print(f"  {domain or 'uncategorized'}: {count}")

    if stats["by_status"]:
        console.print("[bold]\u6309\u72b6\u6001:[/bold]")
        for status, count in stats["by_status"].items():
            console.print(f"  {status}: {count}")

    if stats["recent"]:
        console.print("[bold]\u6700\u65b0\u6dfb\u52a0:[/bold]")
        for r in stats["recent"][:5]:
            console.print(f"  {r['word']} -> {r['translation']} [{r['status']}]")


@db.command("delete")
@click.argument("name")
def db_delete(name):
    """\u5220\u9664\u4e00\u4e2a\u8bcd\u5e93\u3002"""
    try:
        delete_termbase(name)
        console.print(f"[green]OK[/green] \u5df2\u5220\u9664\u8bcd\u5e93: {name}")
    except (FileNotFoundError, OSError) as e:
        console.print(f"[red]\u9519\u8bef:[/red] {e}")


@db.command("merge")
@click.argument("source_db")
@click.option("--target", "-t", default=None, help="\u76ee\u6807\u8bcd\u5e93 (\u9ed8\u8ba4: terms)")
@click.option("--no-dedup", is_flag=True, help="\u5141\u8bb8\u91cd\u590d\u672f\u8bed")
def db_merge(source_db, target, no_dedup):
    """\u5408\u5e76\u53e6\u4e00\u4e2a\u8bcd\u5e93\u5230\u5f53\u524d\u8bcd\u5e93\u3002"""
    target_db = TermDB(db_name=target) if target else TermDB()

    # Resolve source path
    from termprep.db import DB_DIR
    src_path = Path(source_db)
    if not src_path.exists():
        src_path = DB_DIR / (source_db if source_db.endswith(".db") else f"{source_db}.db")
    if not src_path.exists():
        console.print(f"[red]\u9519\u8bef:[/red] \u627e\u4e0d\u5230\u6e90\u8bcd\u5e93: {source_db}")
        return

    stats = target_db.merge(str(src_path), dedup=not no_dedup)
    console.print("[green]OK[/green] \u5408\u5e76\u5b8c\u6210:")
    console.print(f"  \u65b0\u589e: {stats['added']}")
    console.print(f"  \u8df3\u8fc7(\u91cd\u590d): {stats['skipped']}")
    console.print(f"  \u6e90\u8bcd\u5e93\u603b\u6570: {stats['total_other']}")


# ============================================================
# import / export
# ============================================================
@db.command("import")
@click.argument("csv_file")
@click.option("--db", "db_name", default=None, help="\u76ee\u6807\u8bcd\u5e93\u540d\u79f0")
@click.option("--no-dedup", is_flag=True, help="\u5141\u8bb8\u5bfc\u5165\u91cd\u590d\u672f\u8bed")
def db_import(csv_file, db_name, no_dedup):
    """\u4ece CSV \u5bfc\u5165\u8bcd\u6c47\u5230\u8bcd\u5e93\u3002"""
    tdb = TermDB(db_name=db_name) if db_name else TermDB()
    count = tdb.import_csv(csv_file, dedup=not no_dedup)
    console.print(f"[green]OK[/green] \u5df2\u5bfc\u5165 {count} \u6761\u8bcd\u6c47")


@db.command("export")
@click.option("--output", "-o", default=None, help="\u8f93\u51fa\u6587\u4ef6\u8def\u5f84")
@click.option("--format", "fmt", default="csv", help="\u683c\u5f0f: csv, xlsx, tbx, json")
@click.option("--db", "db_name", default=None, help="\u6e90\u8bcd\u5e93\u540d\u79f0")
@click.option("--status", default=None, help="\u6309\u72b6\u6001\u7b5b\u9009 (draft/confirmed/deprecated)")
def db_export(output, fmt, db_name, status):
    """\u5bfc\u51fa\u8bcd\u5e93\u5230\u591a\u79cd\u683c\u5f0f\u3002"""
    tdb = TermDB(db_name=db_name) if db_name else TermDB()

    if not output:
        output = f"{tdb.name}.{fmt}"
        if fmt == "xlsx":
            output = f"{tdb.name}.xlsx"

    fmt = fmt.lower()
    if fmt == "csv":
        from termprep.exporter import export_csv
        count = export_csv(tdb, output, status_filter=status)
    elif fmt == "xlsx":
        from termprep.exporter import export_xlsx
        count = export_xlsx(tdb, output, status_filter=status)
    elif fmt == "tbx":
        from termprep.exporter import export_tbx
        count = export_tbx(tdb, output, status_filter=status)
    elif fmt == "json":
        from termprep.exporter import export_json
        count = export_json(tdb, output, status_filter=status)
    else:
        console.print(f"[red]\u9519\u8bef:[/red] \u4e0d\u652f\u6301\u7684\u683c\u5f0f '{fmt}'. \u652f\u6301: csv, xlsx, tbx, json")
        return

    console.print(f"[green]OK[/green] \u5df2\u5bfc\u51fa {count} \u6761\u8bcd\u6c47\u5230 {output}")


# ============================================================
# report
# ============================================================
@main.command()
@click.argument("file", required=False)
@click.option("--text", "-t", default="", help="\u76f4\u63a5\u8f93\u5165\u6587\u672c")
@click.option("--output", "-o", default="termprep-report.md", help="\u8f93\u51fa\u6587\u4ef6\u8def\u5f84")
@click.option("--name", "project_name", default="Untitled", help="\u9879\u76ee\u540d\u79f0")
@click.option("--db", "db_name", default=None, help="\u8bcd\u5e93\u540d\u79f0")
def report(file, text, output, project_name, db_name):
    """\u751f\u6210\u8bd1\u524d\u51c6\u5907\u62a5\u544a (Markdown)\u3002"""
    # Analysis
    if file:
        analysis = analyze_file(file)
    elif text:
        analysis = analyze(text)
    else:
        console.print("[red]\u9519\u8bef:[/red] \u8bf7\u63d0\u4f9b\u6587\u4ef6\u8def\u5f84\u6216 --text \u53c2\u6570")
        return

    # Extraction
    if file:
        terms = extract_file(file)
    else:
        terms = extract(text)

    # Termbase stats
    tdb = TermDB(db_name=db_name) if db_name else TermDB()
    db_stats = tdb.get_stats()

    report_text = generate_report(
        analysis=analysis,
        terms=terms,
        db_stats=db_stats,
        project_name=project_name,
        source_file=file or "",
    )
    save_report(report_text, output)
    console.print(f"[green]OK[/green] \u62a5\u544a\u5df2\u751f\u6210: {output}")
    console.print("")
    console.print(report_text[:2000] + ("..." if len(report_text) > 2000 else ""))


# ============================================================
# sources
# ============================================================
@main.command()
def sources():
    """\u67e5\u770b\u53ef\u7528\u7684\u5728\u7ebf\u8bcd\u5178\u6e90\u3002"""
    from termprep.sources import get_available_sources

    srcs = get_available_sources()
    if not srcs:
        console.print("[yellow]\u6682\u65e0\u53ef\u7528\u7684\u5728\u7ebf\u8bcd\u5178\u6e90\u3002[/yellow]")
        console.print("\u8bbe\u7f6e\u73af\u5883\u53d8\u91cf\u4ee5\u542f\u7528\uff1a")
        console.print("  TERMPREP_YOUDAO_KEY / TERMPREP_YOUDAO_SECRET")
        console.print("  TERMPREP_WEBSTER_KEY")
        return

    table = Table(title="\u53ef\u7528\u8bcd\u5178\u6e90")
    table.add_column("\u6e90\u540d\u79f0", style="cyan")
    table.add_column("\u72b6\u6001", style="green")
    table.add_column("\u8bf4\u660e")

    for src in srcs:
        status = "[green]OK \u5df2\u914d\u7f6e[/green]" if src.available else "[red]X \u672a\u914d\u7f6e[/red]"
        table.add_row(src.name, status, f"API key: {src.name}")
    console.print(table)


# ============================================================
# term (add/update/delete individual terms)
# ============================================================
@main.group()
def term():
    """\u5355\u4e2a\u672f\u8bed\u7ba1\u7406\u3002"""


@term.command("add")
@click.argument("word")
@click.option("--translation", "-t", default="", help="\u7ffb\u8bd1")
@click.option("--type", "type_", default="", help="\u8bcd\u6027")
@click.option("--domain", "-d", default="", help="\u9886\u57df")
@click.option("--status", default="draft", help="\u72b6\u6001: draft, confirmed, deprecated")
@click.option("--db", "db_name", default=None, help="\u8bcd\u5e93\u540d\u79f0")
def term_add(word, translation, type_, domain, status, db_name):
    """\u6dfb\u52a0\u4e00\u4e2a\u672f\u8bed\u5230\u8bcd\u5e93\u3002"""
    tdb = TermDB(db_name=db_name) if db_name else TermDB()
    tid = tdb.add_term(word, translation=translation, type_=type_,
                       domain=domain, status=status)
    console.print(f"[green]OK[/green] \u5df2\u6dfb\u52a0\u672f\u8bed [id={tid}]: {word}")


@term.command("delete")
@click.argument("term_id", type=int)
@click.option("--db", "db_name", default=None, help="\u8bcd\u5e93\u540d\u79f0")
def term_delete(term_id, db_name):
    """\u5220\u9664\u4e00\u4e2a\u672f\u8bed\u3002"""
    tdb = TermDB(db_name=db_name) if db_name else TermDB()
    tdb.delete_term(term_id)
    console.print(f"[green]OK[/green] \u5df2\u5220\u9664\u672f\u8bed id={term_id}")


@term.command("update")
@click.argument("term_id", type=int)
@click.option("--translation", default=None, help="\u7ffb\u8bd1")
@click.option("--status", default=None, help="\u72b6\u6001: draft, confirmed, deprecated")
@click.option("--type", "type_", default=None, help="\u8bcd\u6027")
@click.option("--domain", default=None, help="\u9886\u57df")
@click.option("--db", "db_name", default=None, help="\u8bcd\u5e93\u540d\u79f0")
def term_update(term_id, translation, status, type_, domain, db_name):
    """\u66f4\u65b0\u4e00\u4e2a\u672f\u8bed\u3002"""
    tdb = TermDB(db_name=db_name) if db_name else TermDB()
    kwargs = {}
    if translation is not None:
        kwargs["translation"] = translation
    if status is not None:
        kwargs["status"] = status
    if type_ is not None:
        kwargs["type"] = type_
    if domain is not None:
        kwargs["domain"] = domain
    tdb.update_term(term_id, **kwargs)
    console.print(f"[green]OK[/green] \u5df2\u66f4\u65b0\u672f\u8bed id={term_id}")


@term.command("search")
@click.argument("query")
@click.option("--db", "db_name", default=None, help="\u8bcd\u5e93\u540d\u79f0")
def term_search(query, db_name):
    """\u5728\u8bcd\u5e93\u4e2d\u641c\u7d22\u672f\u8bed\u3002"""
    tdb = TermDB(db_name=db_name) if db_name else TermDB()
    results = tdb.search_terms(query)

    if not results:
        console.print(f"[yellow]\u672a\u627e\u5230\u5339\u914d\u7684\u672f\u8bed: {query}[/yellow]")
        return

    table = Table(title=f"\u8bcd\u5e93\u641c\u7d22\u7ed3\u679c: {query}")
    table.add_column("ID", style="dim")
    table.add_column("\u672f\u8bed", style="cyan")
    table.add_column("\u7ffb\u8bd1", style="green")
    table.add_column("\u7c7b\u578b", style="yellow")
    table.add_column("\u9886\u57df", style="magenta")
    table.add_column("\u72b6\u6001")

    for r in results:
        table.add_row(
            str(r.get("id", "")),
            r.get("word", ""),
            r.get("translation", ""),
            r.get("type", ""),
            r.get("domain", ""),
            r.get("status", ""),
        )
    console.print(table)


# ============================================================
# pipeline
# ============================================================
@main.command()
@click.argument("file", required=False)
@click.option("--text", "-t", default="", help="\u76f4\u63a5\u8f93\u5165\u6587\u672c")
@click.option("--name", "project_name", default="Untitled", help="\u9879\u76ee\u540d\u79f0")
@click.option("--top", default=20, help="\u63d0\u53d6\u672f\u8bed\u6570\u91cf", type=int)
@click.option("--search", "search_limit", default=5, help="\u6bcf\u4e2a\u672f\u8bed\u641c\u7d22\u7ed3\u679c\u6570", type=int)
@click.option("--db", "db_name", default=None, help="\u8bcd\u5e93\u540d\u79f0")
@click.option("--output", "-o", default="", help="\u62a5\u544a\u8f93\u51fa\u8def\u5f84")
@click.option("--export", "export_fmt", default="", help="\u5bfc\u51fa\u683c\u5f0f (csv/xlsx/tbx/json, \u591a\u4e2a\u7528\u9017\u53f7\u5206\u9694)")
@click.option("--notes", default="", help="\u9879\u76ee\u5907\u6ce8")
def pipeline(file, text, project_name, top, search_limit, db_name, output, export_fmt, notes):
    """\u5168\u81ea\u52a8\u8bd1\u524d\u51c6\u5907\u6d41\u6c34\u7ebf: \u5206\u6790 -> \u63d0\u53d6 -> \u641c\u7d22 -> \u8bcd\u5e93 -> \u62a5\u544a -> \u5bfc\u51fa\u3002"""
    if not file and not text:
        console.print("[red]\u9519\u8bef:[/red] \u8bf7\u63d0\u4f9b\u6587\u4ef6\u8def\u5f84\u6216 --text \u53c2\u6570")
        return

    export_formats = [f.strip() for f in export_fmt.split(",") if f.strip()] if export_fmt else None

    with console.status("[bold green]\u8fd0\u884c\u8bd1\u524d\u51c6\u5907\u6d41\u6c34\u7ebf...[/bold green]"):
        result = run_pipeline(
            file_path=file,
            text=text or None,
            project_name=project_name,
            top_n=top,
            search_limit=search_limit,
            db_name=db_name,
            report_output=output,
            export_formats=export_formats,
            notes=notes,
        )

    console.print(format_pipeline_result(result))


if __name__ == "__main__":
    main()
