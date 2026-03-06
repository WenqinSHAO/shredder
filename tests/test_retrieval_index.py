from __future__ import annotations

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
                "authors": [],
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
            self.assertEqual(len(payload["queries"]), 2)
            self.assertFalse(payload["queries"][0]["cache_hit"])
            self.assertTrue(payload["queries"][1]["cache_hit"])
            self.assertEqual(adapter.lookup_doi_calls, 1)


if __name__ == "__main__":
    unittest.main()
