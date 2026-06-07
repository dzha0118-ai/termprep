"""SQLite-based terminology database with multi-termbase support."""

import csv
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DB_DIR = Path(__file__).parent.parent / "data"
DEFAULT_DB = "terms.db"


@dataclass
class TermbaseInfo:
    """Metadata about a termbase."""
    name: str = ""
    path: str = ""
    domain: str = ""
    lang: str = ""
    total_terms: int = 0


class TermDB:
    """Local terminology database backed by SQLite.

    Supports multiple independent termbases stored as separate .db files
    in the data/ directory. Each termbase tracks term status, domain
    metadata, and associations.
    """

    def __init__(self, db_path: Path | None = None, db_name: str | None = None):
        """Connect to a termbase.

        Args:
            db_path: Direct path to a .db file. If given, db_name is ignored.
            db_name: Name of the termbase (e.g. "myproject"). Resolves to
                     data/{name}.db. Defaults to "terms".
        """
        if db_path:
            self.db_path = db_path
        elif db_name:
            db_name = db_name if db_name.endswith(".db") else f"{db_name}.db"
            self.db_path = DB_DIR / db_name
        else:
            self.db_path = DB_DIR / DEFAULT_DB
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    # ---- Schema / Setup ----

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_schema(self) -> None:
        with self._get_conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS terms (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    word TEXT NOT NULL,
                    lang TEXT NOT NULL DEFAULT 'auto',
                    type TEXT DEFAULT '',
                    translation TEXT DEFAULT '',
                    domain TEXT DEFAULT '',
                    status TEXT DEFAULT 'draft',
                    notes TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS associations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    term_id INTEGER NOT NULL,
                    related_word TEXT NOT NULL,
                    relation_type TEXT NOT NULL DEFAULT '',
                    score REAL DEFAULT 1.0,
                    source TEXT DEFAULT 'manual',
                    FOREIGN KEY (term_id) REFERENCES terms(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_terms_word ON terms(word);
                CREATE INDEX IF NOT EXISTS idx_terms_domain ON terms(domain);
                CREATE INDEX IF NOT EXISTS idx_terms_status ON terms(status);
                CREATE INDEX IF NOT EXISTS idx_assoc_term ON associations(term_id);
                CREATE INDEX IF NOT EXISTS idx_assoc_word ON associations(related_word);
            """)
            # Ensure meta has a name entry
            conn.execute(
                "INSERT OR IGNORE INTO meta (key, value) VALUES (?, ?)",
                ("name", self.db_path.stem)
            )

    # ---- Metadata ----

    def get_meta(self, key: str, default: str = "") -> str:
        """Get a metadata value."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT value FROM meta WHERE key = ?", (key,)
            ).fetchone()
            return row["value"] if row else default

    def set_meta(self, key: str, value: str) -> None:
        """Set a metadata value."""
        with self._get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                (key, value)
            )

    @property
    def name(self) -> str:
        return self.get_meta("name", self.db_path.stem)

    @name.setter
    def name(self, value: str) -> None:
        self.set_meta("name", value)

    @property
    def domain(self) -> str:
        return self.get_meta("domain")

    @domain.setter
    def domain(self, value: str) -> None:
        self.set_meta("domain", value)

    # ---- CRUD ----

    def add_term(self, word: str, translation: str = "", lang: str = "auto",
                 domain: str = "", type_: str = "", status: str = "draft",
                 notes: str = "") -> int:
        """Add a new term. Returns the term ID."""
        with self._get_conn() as conn:
            cur = conn.execute(
                """INSERT INTO terms (word, translation, lang, domain, type, status, notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (word, translation, lang, domain, type_, status, notes)
            )
            return cur.lastrowid or 0

    def update_term(self, term_id: int, **kwargs: Any) -> None:
        """Update term fields. Pass keyword args for fields to change."""
        allowed = {"word", "translation", "lang", "domain", "type",
                   "status", "notes"}
        fields = {k: v for k, v in kwargs.items() if k in allowed}
        if not fields:
            return
        fields["updated_at"] = "CURRENT_TIMESTAMP"
        set_clause = ", ".join(
            f"{k} = ?" if v != "CURRENT_TIMESTAMP" else f"{k} = CURRENT_TIMESTAMP"
            for k, v in fields.items()
        )
        values = [v for k, v in fields.items()
                  if v != "CURRENT_TIMESTAMP"]
        values.append(term_id)
        with self._get_conn() as conn:
            conn.execute(
                f"UPDATE terms SET {set_clause} WHERE id = ?", values
            )

    def delete_term(self, term_id: int) -> None:
        """Delete a term and its associations."""
        with self._get_conn() as conn:
            conn.execute("DELETE FROM associations WHERE term_id = ?", (term_id,))
            conn.execute("DELETE FROM terms WHERE id = ?", (term_id,))

    def get_term(self, term_id: int) -> dict[str, Any] | None:
        """Get a single term by ID."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM terms WHERE id = ?", (term_id,)
            ).fetchone()
            return dict(row) if row else None

    # ---- Search ----

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

    def search_terms(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """Search terms table by word, translation, or domain."""
        with self._get_conn() as conn:
            rows = conn.execute("""
                SELECT * FROM terms
                WHERE word LIKE ? OR translation LIKE ? OR domain LIKE ?
                ORDER BY updated_at DESC
                LIMIT ?
            """, (f"%{query}%", f"%{query}%", f"%{query}%", limit)).fetchall()
        return [dict(r) for r in rows]

    def filter_by_domain(self, domain: str, limit: int = 100) -> list[dict[str, Any]]:
        """Filter terms by domain."""
        with self._get_conn() as conn:
            rows = conn.execute("""
                SELECT * FROM terms WHERE domain = ?
                ORDER BY word ASC LIMIT ?
            """, (domain, limit)).fetchall()
        return [dict(r) for r in rows]

    def filter_by_status(self, status: str, limit: int = 100) -> list[dict[str, Any]]:
        """Filter terms by status: draft, confirmed, deprecated."""
        with self._get_conn() as conn:
            rows = conn.execute("""
                SELECT * FROM terms WHERE status = ?
                ORDER BY word ASC LIMIT ?
            """, (status, limit)).fetchall()
        return [dict(r) for r in rows]

    # ---- Associations ----

    def add_association(self, term_id: int, related_word: str,
                        relation_type: str = "", score: float = 1.0,
                        source: str = "manual") -> None:
        """Add an association to a term."""
        with self._get_conn() as conn:
            conn.execute(
                """INSERT INTO associations (term_id, related_word, relation_type, score, source)
                   VALUES (?, ?, ?, ?, ?)""",
                (term_id, related_word, relation_type, score, source)
            )

    def get_associations(self, term_id: int) -> list[dict[str, Any]]:
        """Get all associations for a term."""
        with self._get_conn() as conn:
            rows = conn.execute("""
                SELECT * FROM associations WHERE term_id = ?
                ORDER BY score DESC
            """, (term_id,)).fetchall()
        return [dict(r) for r in rows]

    # ---- Import / Export ----

    def import_csv(self, csv_path: str, dedup: bool = True) -> int:
        """Import terms from a CSV file (columns: word, translation, type).

        Args:
            csv_path: Path to CSV file. Required columns: word.
                      Optional: translation, type, domain, status, lang, notes.
            dedup: Skip rows where word+translation already exist in DB.

        Returns:
            Number of terms imported.
        """
        count = 0
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            with self._get_conn() as conn:
                for row in reader:
                    word = row.get("word", "").strip()
                    if not word:
                        continue
                    translation = row.get("translation", "").strip()
                    if dedup and self._term_exists(conn, word, translation):
                        continue
                    conn.execute(
                        """INSERT INTO terms (word, translation, type, domain, status, lang, notes)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (
                            word,
                            translation,
                            row.get("type", ""),
                            row.get("domain", ""),
                            row.get("status", "draft"),
                            row.get("lang", "auto"),
                            row.get("notes", ""),
                        )
                    )
                    count += 1
        return count

    def export_csv(self, output_path: str, status_filter: str | None = None) -> int:
        """Export all terms to a CSV file (includes status, domain, notes)."""
        with self._get_conn() as conn:
            if status_filter:
                rows = conn.execute(
                    "SELECT word, translation, type, domain, status, lang, notes FROM terms WHERE status = ?",
                    (status_filter,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT word, translation, type, domain, status, lang, notes FROM terms"
                ).fetchall()

        with open(output_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(
                f, fieldnames=["word", "translation", "type", "domain",
                               "status", "lang", "notes"]
            )
            writer.writeheader()
            for row in rows:
                writer.writerow(dict(row))
        return len(rows)

    @staticmethod
    def _term_exists(conn: sqlite3.Connection, word: str, translation: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM terms WHERE word = ? AND translation = ?",
            (word, translation)
        ).fetchone()
        return row is not None

    # ---- Stats ----

    def get_stats(self) -> dict[str, Any]:
        """Get database statistics."""
        with self._get_conn() as conn:
            total = conn.execute("SELECT COUNT(*) AS c FROM terms").fetchone()["c"]
            by_domain = {
                r["domain"]: r["c"] for r in
                conn.execute(
                    "SELECT domain, COUNT(*) AS c FROM terms GROUP BY domain ORDER BY c DESC"
                ).fetchall()
            }
            by_status = {
                r["status"]: r["c"] for r in
                conn.execute(
                    "SELECT status, COUNT(*) AS c FROM terms GROUP BY status ORDER BY c DESC"
                ).fetchall()
            }
            by_lang = {
                r["lang"]: r["c"] for r in
                conn.execute(
                    "SELECT lang, COUNT(*) AS c FROM terms GROUP BY lang ORDER BY c DESC"
                ).fetchall()
            }
            confirmed = conn.execute(
                "SELECT COUNT(*) AS c FROM terms WHERE status = 'confirmed'"
            ).fetchone()["c"]
            associations = conn.execute(
                "SELECT COUNT(*) AS c FROM associations"
            ).fetchone()["c"]
            recent = [
                dict(r) for r in conn.execute(
                    "SELECT word, translation, domain, status, created_at FROM terms ORDER BY created_at DESC LIMIT 10"
                ).fetchall()
            ]
        return {
            "name": self.name,
            "domain": self.domain,
            "total_terms": total,
            "confirmed": confirmed,
            "associations": associations,
            "by_domain": by_domain,
            "by_status": by_status,
            "by_lang": by_lang,
            "recent": recent,
        }

    # ---- Merge ----

    def merge(self, other_path: str, dedup: bool = True) -> dict[str, int]:
        """Merge terms from another termbase into this one.

        Args:
            other_path: Path to the other .db file.
            dedup: Skip terms that already exist in this database.

        Returns:
            Dict with keys: added (int), skipped (int), total_other (int).
        """
        other = TermDB(db_path=Path(other_path))
        stats = {"added": 0, "skipped": 0, "total_other": 0}

        with other._get_conn() as other_conn:
            other_terms = other_conn.execute(
                "SELECT word, translation, type, domain, status, lang, notes FROM terms"
            ).fetchall()
            stats["total_other"] = len(other_terms)

            with self._get_conn() as conn:
                for row in other_terms:
                    d = dict(row)
                    if dedup and self._term_exists(conn, d["word"], d["translation"]):
                        stats["skipped"] += 1
                        continue
                    conn.execute(
                        """INSERT INTO terms (word, translation, type, domain, status, lang, notes)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (d["word"], d["translation"], d["type"], d["domain"],
                         d["status"], d["lang"], d["notes"])
                    )
                    stats["added"] += 1

        return stats


# ---- Module-level helpers ----

def list_termbases() -> list[TermbaseInfo]:
    """List all termbase .db files in the data directory."""
    DB_DIR.mkdir(parents=True, exist_ok=True)
    dbs: list[TermbaseInfo] = []
    for f in sorted(DB_DIR.glob("*.db")):
        try:
            tdb = TermDB(db_path=f)
            stats = tdb.get_stats()
            dbs.append(TermbaseInfo(
                name=tdb.name,
                path=str(f),
                domain=tdb.domain,
                lang=", ".join(stats["by_lang"].keys()),
                total_terms=stats["total_terms"],
            ))
        except Exception:
            dbs.append(TermbaseInfo(
                name=f.stem,
                path=str(f),
                total_terms=0,
            ))
    return dbs


def init_termbase(name: str, domain: str = "") -> TermDB:
    """Create a new empty termbase."""
    db_name = name if name.endswith(".db") else f"{name}.db"
    db_path = DB_DIR / db_name
    if db_path.exists():
        raise FileExistsError(f"Termbase '{name}' already exists at {db_path}")
    tdb = TermDB(db_path=db_path)
    tdb.name = name
    if domain:
        tdb.domain = domain
    return tdb


def delete_termbase(name: str) -> None:
    """Delete a termbase file."""
    import gc
    import time
    db_name = name if name.endswith(".db") else f"{name}.db"
    db_path = DB_DIR / db_name
    if not db_path.exists():
        raise FileNotFoundError(f"Termbase '{name}' not found")
    # Windows may hold file locks briefly after the last connection closes
    gc.collect()
    for attempt in range(3):
        try:
            db_path.unlink()
            return
        except PermissionError:
            if attempt < 2:
                time.sleep(0.5)
                gc.collect()
            else:
                raise
