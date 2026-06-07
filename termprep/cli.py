"""CLI entry point for TermPrep."""

import os
import click
from rich.console import Console
from rich.table import Table

from termprep.searcher import search_term
from termprep.db import TermDB
from termprep.analyzer import analyze, analyze_file, get_summary
from termprep.extractor import extract, extract_file, get_frequency_table

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
    pass


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


@main.group()
def db():
    """\u672c\u5730\u8bcd\u5e93\u7ba1\u7406\u3002"""
    pass


@db.command("import")
@click.argument("csv_file")
def db_import(csv_file):
    """\u4ece CSV \u5bfc\u5165\u8bcd\u6c47\u3002"""
    db_conn = TermDB()
    count = db_conn.import_csv(csv_file)
    console.print(f"[green]OK[/green] \u5df2\u5bfc\u5165 {count} \u6761\u8bcd\u6c47")


@db.command("export")
@click.option("--output", "-o", default="glossary.csv", help="\u8f93\u51fa\u6587\u4ef6")
def db_export(output):
    """\u5bfc\u51fa\u8bcd\u5e93\u5230 CSV\u3002"""
    db_conn = TermDB()
    count = db_conn.export_csv(output)
    console.print(f"[green]OK[/green] \u5df2\u5bfc\u51fa {count} \u6761\u8bcd\u6c47\u5230 {output}")


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


if __name__ == "__main__":
    main()
