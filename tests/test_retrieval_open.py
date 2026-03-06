from __future__ import annotations

import csv
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.orchestrator.runner import run_step
from src.utils import yamlx


class _OpenAdapter:
    def lookup_doi(self, doi: str) -> list[dict]:
        if doi == "10.2/ok":
            return [
                {
                    "source": "habanero",
                    "source_id": doi,
                    "title": "Deterministic Candidate",
                    "venue": "NSDI",
                    "year": "2024",
                    "doi": doi,
                    "arxiv_id": "",
                    "url": "https://doi.org/" + doi,
                    "authors": [{"name": "Author A", "orcid": "0000-0002-0000-0002", "source_id": "a1", "affiliations": []}],
                    "score": 1.0,
                    "reason": "lookup_doi",
                }
            ]
        return []

    def lookup_arxiv(self, arxiv_id: str) -> list[dict]:
        return []

    def search_title(self, title: str, limit: int = 5) -> list[dict]:
        if title.lower().startswith("ambiguous"):
            return [
                {
                    "source": "pyalex",
                    "source_id": "w1",
                    "title": "Ambiguous Candidate One",
                    "venue": "OSDI",
                    "year": "2024",
                    "doi": "",
                    "arxiv_id": "",
                    "url": "",
                    "authors": [],
                    "score": 0.8,
                    "reason": "search_title",
                },
                {
                    "source": "semanticscholar",
                    "source_id": "p2",
                    "title": "Ambiguous Candidate Two",
                    "venue": "OSDI",
                    "year": "2024",
                    "doi": "",
                    "arxiv_id": "",
                    "url": "",
                    "authors": [],
                    "score": 0.8,
                    "reason": "search_title",
                },
            ]
        return []

    def search_open(self, query: str, limit: int = 20) -> list[dict]:
        return [
            {
                "source": "habanero",
                "source_id": "10.2/ok",
                "title": "Deterministic Candidate",
                "venue": "NSDI",
                "year": "2024",
                "doi": "10.2/ok",
                "arxiv_id": "",
                "url": "https://doi.org/10.2/ok",
                "authors": [],
                "score": 0.95,
                "reason": "open_query",
            },
            {
                "source": "pyalex",
                "source_id": "amb-1",
                "title": "Ambiguous Candidate",
                "venue": "OSDI",
                "year": "2024",
                "doi": "",
                "arxiv_id": "",
                "url": "",
                "authors": [],
                "score": 0.7,
                "reason": "open_query",
            },
        ]


class TestOpenRetrieval(unittest.TestCase):
    def test_open_retrieval_outputs_candidates_and_handoff_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ws = root / "workspace"
            kb_dir = root / "kb"
            kb_path = kb_dir / "kb.sqlite"
            ws.mkdir(parents=True, exist_ok=True)
            kb_dir.mkdir(parents=True, exist_ok=True)

            with patch("src.utils.paths.WORKSPACE_ROOT", ws), patch("src.kb.store.KB_DIR", kb_dir), patch(
                "src.kb.store.KB_PATH", kb_path
            ):
                run_step("demo", "init", theme="systems")

                project_yaml = ws / "demo" / "project.yaml"
                meta = yamlx.load(project_yaml)
                meta["retrieval"]["open_enabled"] = True
                yamlx.dump_to_path(project_yaml, meta)

                with patch("src.orchestrator.retrieval.build_adapters", return_value=[_OpenAdapter()]):
                    summary = run_step("demo", "retrieve-open", prompt="memory disaggregation", top_n=2)

            self.assertTrue(summary.exists())
            payload = yamlx.load(summary)
            self.assertTrue(payload["open_enabled"])

            rdir = ws / "demo" / "artifacts" / "retrieval"
            self.assertTrue((rdir / "candidates_raw.tsv").exists())
            self.assertTrue((rdir / "candidates_ranked.tsv").exists())
            self.assertTrue((rdir / "handoff.tsv").exists())

            with (rdir / "handoff.tsv").open("r", encoding="utf-8") as f:
                rows = list(csv.DictReader(f, delimiter="\t"))
            statuses = {row["status"] for row in rows}
            self.assertIn("resolved", statuses)
            self.assertTrue("ambiguous_requires_selection" in statuses or "not_found" in statuses)

            conn = sqlite3.connect(kb_path)
            try:
                paper_rows = conn.execute("SELECT id FROM papers").fetchall()
                self.assertEqual(len(paper_rows), 1)
                self.assertEqual(paper_rows[0][0], "doi:10.2/ok")
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
