from __future__ import annotations

from datetime import datetime, timezone
import sqlite3
from pathlib import Path

from src.utils.paths import KB_DIR, KB_PATH


def _connect() -> sqlite3.Connection:
    KB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(KB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


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
                abstract TEXT,
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
    return KB_PATH


def upsert_paper(paper: dict, source: str = "unknown") -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO papers (id, title, venue, year, doi, abstract, pdf_url, html_url, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              title=excluded.title,
              venue=excluded.venue,
              year=excluded.year,
              doi=excluded.doi,
              abstract=excluded.abstract,
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
                paper.get("abstract"),
                paper.get("pdf_url"),
                paper.get("html_url"),
                now,
                now,
            ),
        )
        conn.execute(
            "INSERT OR REPLACE INTO provenance (id, entity_type, entity_id, source, source_key, confidence, fetched_at, raw_ref) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (f"{paper['id']}::{source}", "paper", paper["id"], source, paper.get("doi", ""), 0.5, now, "{}"),
        )


def search_papers(query: str) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, title, venue, year, doi FROM papers WHERE title LIKE ? ORDER BY year DESC", (f"%{query}%",)
        ).fetchall()
    return [dict(r) for r in rows]
