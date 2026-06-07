"""CLI entry point for TermPrep."""

import click
from rich.console import Console
from rich.table import Table

from termprep.searcher import search_term
from termprep.db import TermDB

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

    console.print(f"[green]\u2714[/green] \u5df2\u5bfc\u51fa {len(results)} \u6761\u7ed3\u679c\u5230 {output}")


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
    console.print(f"[green]\u2714[/green] \u5df2\u5bfc\u5165 {count} \u6761\u8bcd\u6c47")


@db.command("export")
@click.option("--output", "-o", default="glossary.csv", help="\u8f93\u51fa\u6587\u4ef6")
def db_export(output):
    """\u5bfc\u51fa\u8bcd\u5e93\u5230 CSV\u3002"""
    db_conn = TermDB()
    count = db_conn.export_csv(output)
    console.print(f"[green]\u2714[/green] \u5df2\u5bfc\u51fa {count} \u6761\u8bcd\u6c47\u5230 {output}")


if __name__ == "__main__":
    main()
