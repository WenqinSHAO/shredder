from __future__ import annotations

import csv
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.orchestrator.runner import run_step
from src.utils import yamlx


class _CountingAdapter:
    def __init__(self):
        self.lookup_doi_calls = 0

    def lookup_doi(self, doi: str) -> list[dict]:
        self.lookup_doi_calls += 1
        return [
            {
                "source": "habanero",
                "source_id": doi,
                "title": "Deterministic Systems",
                "venue": "NSDI",
                "year": "2024",
                "doi": doi,
                "arxiv_id": "",
                "url": "https://doi.org/" + doi,
                "abstract": "A concise abstract about deterministic systems.",
                "keywords": ["deterministic", "systems"],
                "categories": ["distributed systems"],
                "authors": [
                    {
                        "name": "Alice Example",
                        "orcid": "",
                        "source_id": "a1",
                        "affiliations": [{"name": "Example University", "ror": "", "country": "US"}],
                    }
                ],
                "score": 1.0,
                "reason": "lookup_doi",
            }
        ]

    def lookup_arxiv(self, arxiv_id: str) -> list[dict]:
        return []

    def search_title(self, title: str, limit: int = 5) -> list[dict]:
        return []


class TestRetrievalIndex(unittest.TestCase):
    def test_cache_first_appends_query_history_and_keeps_unique_paper(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ws = root / "workspace"
            kb_dir = root / "kb"
            kb_path = kb_dir / "kb.sqlite"
            ws.mkdir(parents=True, exist_ok=True)
            kb_dir.mkdir(parents=True, exist_ok=True)

            adapter = _CountingAdapter()

            with patch("src.utils.paths.WORKSPACE_ROOT", ws), patch("src.kb.store.KB_DIR", kb_dir), patch(
                "src.kb.store.KB_PATH", kb_path
            ):
                run_step("demo", "init", theme="systems")
                with patch("src.orchestrator.retrieval.build_adapters", return_value=[adapter]):
                    run_step("demo", "retrieve-paper", doi="10.1/xyz", policy="cache_first")
                    run_step("demo", "retrieve-paper", doi="10.1/xyz", policy="cache_first")

            index_path = ws / "demo" / "artifacts" / "retrieval" / "deterministic_result.yaml"
            payload = yamlx.load(index_path)
            self.assertEqual(payload["artifact_type"], "deterministic_retrieval_index")
            self.assertEqual(len(payload["papers"]), 1)
            self.assertEqual(payload["papers"][0]["paper_id"], "doi:10.1/xyz")
            self.assertTrue(payload["papers"][0]["search_trace"])
            self.assertEqual(payload["papers"][0]["paper"]["keywords"], ["deterministic", "systems"])
            self.assertEqual(payload["papers"][0]["paper"]["categories"], ["distributed systems"])
            self.assertIn("affiliation_count", payload["papers"][0]["paper"]["authors"][0])
            self.assertNotIn("affiliations", payload["papers"][0]["paper"]["authors"][0])
            self.assertNotIn("authors", payload["papers"][0]["sources"][0])
            self.assertEqual(len(payload["queries"]), 2)
            self.assertFalse(payload["queries"][0]["cache_hit"])
            self.assertTrue(payload["queries"][1]["cache_hit"])
            self.assertEqual(adapter.lookup_doi_calls, 1)

            request_log_path = ws / "demo" / "artifacts" / "retrieval" / "deterministic_request.yaml"
            request_log = yamlx.load(request_log_path)
            self.assertEqual(request_log["artifact_type"], "deterministic_request_log")
            self.assertEqual(len(request_log["history"]), 2)
            self.assertEqual(request_log["history"][0]["query_key"], "doi:10.1/xyz")
            self.assertEqual(request_log["history"][1]["query_key"], "doi:10.1/xyz")

            sources_log_path = ws / "demo" / "artifacts" / "retrieval" / "deterministic_sources.tsv"
            with sources_log_path.open("r", encoding="utf-8") as f:
                rows = list(csv.DictReader(f, delimiter="\t"))
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["query_key"], "doi:10.1/xyz")
            self.assertEqual(rows[1]["query_key"], "doi:10.1/xyz")

            conn = sqlite3.connect(kb_path)
            try:
                db_row = conn.execute("SELECT abstract, keywords_json, categories_json FROM papers WHERE id='doi:10.1/xyz'").fetchone()
            finally:
                conn.close()
            self.assertIsNotNone(db_row)
            self.assertEqual(db_row[0], "A concise abstract about deterministic systems.")
            self.assertEqual(json.loads(db_row[1]), ["deterministic", "systems"])
            self.assertEqual(json.loads(db_row[2]), ["distributed systems"])


if __name__ == "__main__":
    unittest.main()
