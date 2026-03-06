from __future__ import annotations

from datetime import datetime, timezone
import json
import sqlite3
from pathlib import Path

from src.utils.paths import KB_DIR, KB_PATH


def _connect() -> sqlite3.Connection:
    KB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(KB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, column_sql: str) -> None:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    columns = {str(row["name"]) for row in rows}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column_sql}")


def init_kb() -> Path:
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS papers (
                id TEXT PRIMARY KEY,
                title TEXT,
                venue TEXT,
                year INTEGER,
                doi TEXT UNIQUE,
                arxiv_id TEXT,
                abstract TEXT,
                keywords_json TEXT,
                categories_json TEXT,
                pdf_url TEXT,
                html_url TEXT,
                created_at TEXT,
                updated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS authors (
                id TEXT PRIMARY KEY,
                name TEXT,
                orcid TEXT UNIQUE,
                created_at TEXT,
                updated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS orgs (
                id TEXT PRIMARY KEY,
                name TEXT,
                ror TEXT UNIQUE,
                country TEXT,
                created_at TEXT,
                updated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS paper_authors (
                paper_id TEXT,
                author_id TEXT,
                position INTEGER,
                PRIMARY KEY(paper_id, author_id)
            );
            CREATE TABLE IF NOT EXISTS paper_author_metadata (
                paper_id TEXT,
                author_id TEXT,
                source_ids_json TEXT,
                affiliations_json TEXT,
                PRIMARY KEY(paper_id, author_id)
            );
            CREATE TABLE IF NOT EXISTS author_orgs (
                author_id TEXT,
                org_id TEXT,
                role TEXT,
                PRIMARY KEY(author_id, org_id)
            );
            CREATE TABLE IF NOT EXISTS provenance (
                id TEXT PRIMARY KEY,
                entity_type TEXT,
                entity_id TEXT,
                source TEXT,
                source_key TEXT,
                confidence REAL,
                fetched_at TEXT,
                raw_ref TEXT
            );
            """
        )
        _ensure_column(conn, "papers", "arxiv_id", "arxiv_id TEXT")
        _ensure_column(conn, "papers", "keywords_json", "keywords_json TEXT")
        _ensure_column(conn, "papers", "categories_json", "categories_json TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_papers_arxiv_id ON papers(arxiv_id)")
    return KB_PATH


def upsert_paper(paper: dict) -> None:
    now = datetime.now(timezone.utc).isoformat()
    keywords_json = json.dumps(list(paper.get("keywords") or []), ensure_ascii=False)
    categories_json = json.dumps(list(paper.get("categories") or []), ensure_ascii=False)
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO papers (id, title, venue, year, doi, arxiv_id, abstract, keywords_json, categories_json, pdf_url, html_url, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              title=excluded.title,
              venue=excluded.venue,
              year=excluded.year,
              doi=excluded.doi,
              arxiv_id=excluded.arxiv_id,
              abstract=excluded.abstract,
              keywords_json=excluded.keywords_json,
              categories_json=excluded.categories_json,
              pdf_url=excluded.pdf_url,
              html_url=excluded.html_url,
              updated_at=excluded.updated_at
            """,
            (
                paper["id"],
                paper.get("title"),
                paper.get("venue"),
                paper.get("year"),
                paper.get("doi"),
                paper.get("arxiv_id"),
                paper.get("abstract"),
                keywords_json,
                categories_json,
                paper.get("pdf_url"),
                paper.get("html_url"),
                now,
                now,
            ),
        )


def upsert_author(author: dict) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO authors (id, name, orcid, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              name=excluded.name,
              orcid=excluded.orcid,
              updated_at=excluded.updated_at
            """,
            (author["id"], author.get("name"), author.get("orcid"), now, now),
        )


def upsert_org(org: dict) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO orgs (id, name, ror, country, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              name=excluded.name,
              ror=excluded.ror,
              country=excluded.country,
              updated_at=excluded.updated_at
            """,
            (org["id"], org.get("name"), org.get("ror"), org.get("country"), now, now),
        )


def upsert_paper_author(paper_id: str, author_id: str, position: int | None = None) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO paper_authors (paper_id, author_id, position) VALUES (?, ?, ?)",
            (paper_id, author_id, position),
        )


def list_paper_author_ids(paper_id: str) -> list[str]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT author_id FROM paper_authors WHERE paper_id = ? ORDER BY position ASC, author_id ASC",
            (paper_id,),
        ).fetchall()
    return [str(row["author_id"]) for row in rows if str(row["author_id"]).strip()]


def replace_paper_authors(paper_id: str, author_positions: list[tuple[str, int | None]]) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM paper_authors WHERE paper_id = ?", (paper_id,))
        for author_id, position in author_positions:
            conn.execute(
                "INSERT INTO paper_authors (paper_id, author_id, position) VALUES (?, ?, ?)",
                (paper_id, author_id, position),
            )


def replace_paper_author_metadata(paper_id: str, author_metadata: list[dict]) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM paper_author_metadata WHERE paper_id = ?", (paper_id,))
        for row in author_metadata:
            author_id = str(row.get("author_id") or "")
            if not author_id:
                continue
            source_ids_json = json.dumps(list(row.get("source_ids") or []), ensure_ascii=False)
            affiliations_json = json.dumps(list(row.get("affiliations") or []), ensure_ascii=False)
            conn.execute(
                """
                INSERT INTO paper_author_metadata (paper_id, author_id, source_ids_json, affiliations_json)
                VALUES (?, ?, ?, ?)
                """,
                (paper_id, author_id, source_ids_json, affiliations_json),
            )


def upsert_author_org(author_id: str, org_id: str, role: str = "") -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO author_orgs (author_id, org_id, role) VALUES (?, ?, ?)",
            (author_id, org_id, role),
        )


def replace_author_orgs(author_id: str, org_roles: list[tuple[str, str]]) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM author_orgs WHERE author_id = ?", (author_id,))
        for org_id, role in org_roles:
            conn.execute(
                "INSERT INTO author_orgs (author_id, org_id, role) VALUES (?, ?, ?)",
                (author_id, org_id, role),
            )


def count_papers_for_author(author_id: str) -> int:
    with _connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM paper_authors WHERE author_id = ?", (author_id,)).fetchone()
    if row is None:
        return 0
    return int(row["n"] or 0)


def add_provenance(
    entity_id: str,
    source: str,
    source_key: str = "",
    confidence: float = 0.5,
    raw_ref: dict | None = None,
    entity_type: str = "paper",
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    payload = json.dumps(raw_ref or {}, sort_keys=True)
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO provenance (id, entity_type, entity_id, source, source_key, confidence, fetched_at, raw_ref) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (f"{entity_type}::{entity_id}::{source}::{source_key}", entity_type, entity_id, source, source_key, confidence, now, payload),
        )


def delete_paper(paper_id: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM paper_authors WHERE paper_id = ?", (paper_id,))
        conn.execute("DELETE FROM paper_author_metadata WHERE paper_id = ?", (paper_id,))
        conn.execute("DELETE FROM provenance WHERE entity_type = 'paper' AND entity_id = ?", (paper_id,))
        conn.execute("DELETE FROM provenance WHERE entity_type = 'paper_author' AND entity_id LIKE ?", (f"{paper_id}::%",))
        conn.execute("DELETE FROM papers WHERE id = ?", (paper_id,))


def search_papers(query: str) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, title, venue, year, doi, abstract, keywords_json, categories_json FROM papers WHERE title LIKE ? ORDER BY year DESC",
            (f"%{query}%",),
        ).fetchall()
    out: list[dict] = []
    for row in rows:
        item = dict(row)
        item["keywords"] = json.loads(item.pop("keywords_json") or "[]")
        item["categories"] = json.loads(item.pop("categories_json") or "[]")
        out.append(item)
    return out


def find_paper_ids_by_doi(doi: str) -> list[str]:
    normalized = str(doi or "").strip().lower()
    if not normalized:
        return []
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id FROM papers WHERE lower(doi)=? ORDER BY updated_at DESC, created_at DESC",
            (normalized,),
        ).fetchall()
    return [str(row["id"]) for row in rows]


def find_paper_ids_by_arxiv(arxiv_id: str) -> list[str]:
    normalized = str(arxiv_id or "").strip().lower()
    if normalized.startswith("arxiv:"):
        normalized = normalized[len("arxiv:") :]
    if not normalized:
        return []
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id FROM papers WHERE lower(arxiv_id)=? ORDER BY updated_at DESC, created_at DESC",
            (normalized,),
        ).fetchall()
    return [str(row["id"]) for row in rows]


def get_paper_with_authors(paper_id: str) -> dict:
    with _connect() as conn:
        paper = conn.execute(
            "SELECT id, title, venue, year, doi, arxiv_id, abstract, keywords_json, categories_json, html_url FROM papers WHERE id=?",
            (paper_id,),
        ).fetchone()
        if not paper:
            return {}
        authors = conn.execute(
            """
            SELECT a.id, a.name, a.orcid, pa.position, pam.source_ids_json, pam.affiliations_json
            FROM paper_authors pa
            JOIN authors a ON a.id = pa.author_id
            LEFT JOIN paper_author_metadata pam ON pam.paper_id = pa.paper_id AND pam.author_id = pa.author_id
            WHERE pa.paper_id = ?
            ORDER BY pa.position ASC, a.name ASC
            """,
            (paper_id,),
        ).fetchall()
    paper_row = dict(paper)
    paper_row["keywords"] = json.loads(paper_row.pop("keywords_json") or "[]")
    paper_row["categories"] = json.loads(paper_row.pop("categories_json") or "[]")
    author_rows: list[dict] = []
    for raw_row in authors:
        row = dict(raw_row)
        row["source_ids"] = json.loads(row.pop("source_ids_json") or "[]")
        row["affiliations"] = json.loads(row.pop("affiliations_json") or "[]")
        author_rows.append(row)
    return {"paper": paper_row, "authors": author_rows}


def get_author_profile(author_id: str) -> dict:
    with _connect() as conn:
        author = conn.execute("SELECT id, name, orcid FROM authors WHERE id=?", (author_id,)).fetchone()
        if not author:
            return {}
        orgs = conn.execute(
            """
            SELECT o.id, o.name, o.ror, o.country, ao.role
            FROM author_orgs ao
            JOIN orgs o ON o.id = ao.org_id
            WHERE ao.author_id = ?
            ORDER BY o.name ASC
            """,
            (author_id,),
        ).fetchall()
    return {"author": dict(author), "orgs": [dict(r) for r in orgs]}
