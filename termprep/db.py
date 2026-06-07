"""SQLite-based terminology database."""

import csv
import sqlite3
from pathlib import Path
from typing import Any

DB_DIR = Path(__file__).parent.parent / "data"
DB_PATH = DB_DIR / "terms.db"


class TermDB:
    """Local terminology database backed by SQLite."""

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._get_conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS terms (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    word TEXT NOT NULL,
                    lang TEXT NOT NULL DEFAULT 'auto',
                    type TEXT DEFAULT '',
                    translation TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS associations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    term_id INTEGER NOT NULL,
                    related_word TEXT NOT NULL,
                    relation_type TEXT NOT NULL,
                    score REAL DEFAULT 1.0,
                    source TEXT DEFAULT 'manual',
                    FOREIGN KEY (term_id) REFERENCES terms(id)
                );

                CREATE INDEX IF NOT EXISTS idx_terms_word ON terms(word);
                CREATE INDEX IF NOT EXISTS idx_assoc_term ON associations(term_id);
                CREATE INDEX IF NOT EXISTS idx_assoc_word ON associations(related_word);
            """)

    def search(self, term: str, limit: int = 20) -> list[dict[str, Any]]:
        """Search for a term and its associations."""
        with self._get_conn() as conn:
            rows = conn.execute("""
                SELECT a.related_word AS word,
                       a.relation_type AS type,
                       a.source,
                       a.score
                FROM terms t
                JOIN associations a ON t.id = a.term_id
                WHERE t.word LIKE ?
                ORDER BY a.score DESC
                LIMIT ?
            """, (f"%{term}%", limit)).fetchall()

        return [dict(r) for r in rows]

    def import_csv(self, csv_path: str) -> int:
        """Import terms from a CSV file (columns: word, translation, type)."""
        count = 0
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            with self._get_conn() as conn:
                for row in reader:
                    conn.execute(
                        "INSERT INTO terms (word, translation, type) VALUES (?, ?, ?)",
                        (row.get("word", ""), row.get("translation", ""), row.get("type", ""))
                    )
                    count += 1
        return count

    def export_csv(self, output_path: str) -> int:
        """Export all terms to a CSV file."""
        with self._get_conn() as conn:
            rows = conn.execute("SELECT word, translation, type FROM terms").fetchall()

        with open(output_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["word", "translation", "type"])
            writer.writeheader()
            for row in rows:
                writer.writerow(dict(row))

        return len(rows)
