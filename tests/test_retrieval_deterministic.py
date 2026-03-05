from __future__ import annotations

import sys
import unittest
from unittest.mock import patch

from src.cli import main
from src.retrieval.service import resolve_deterministic


class _DoiAdapter:
    def lookup_doi(self, doi: str) -> list[dict]:
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


class _ArxivAdapter:
    def lookup_doi(self, doi: str) -> list[dict]:
        return []

    def lookup_arxiv(self, arxiv_id: str) -> list[dict]:
        return [
            {
                "source": "arxiv",
                "source_id": arxiv_id,
                "title": "Arxiv Paper",
                "venue": "arXiv",
                "year": "2023",
                "doi": "",
                "arxiv_id": arxiv_id,
                "url": f"https://arxiv.org/abs/{arxiv_id}",
                "authors": [],
                "score": 1.0,
                "reason": "lookup_arxiv",
            }
        ]

    def search_title(self, title: str, limit: int = 5) -> list[dict]:
        return []


class _AmbiguousTitleAdapter:
    def lookup_doi(self, doi: str) -> list[dict]:
        return []

    def lookup_arxiv(self, arxiv_id: str) -> list[dict]:
        return []

    def search_title(self, title: str, limit: int = 5) -> list[dict]:
        return [
            {
                "source": "pyalex",
                "source_id": "w1",
                "title": "Cache Systems for LLM",
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
                "title": "Cache Systems for LLMs",
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


class TestDeterministicResolverUnit(unittest.TestCase):
    def test_doi_resolution_is_canonical(self):
        result = resolve_deterministic({"doi": "10.1/xyz"}, [_DoiAdapter()])
        self.assertEqual(result["status"], "resolved")
        self.assertEqual(result["paper"]["paper_id"], "doi:10.1/xyz")

    def test_arxiv_url_resolution_normalizes_id(self):
        result = resolve_deterministic({"arxiv_url": "https://arxiv.org/abs/2401.12345"}, [_ArxivAdapter()])
        self.assertEqual(result["status"], "resolved")
        self.assertEqual(result["paper"]["paper_id"], "arxiv:2401.12345")

    def test_title_resolution_ambiguous_returns_no_write_signal(self):
        result = resolve_deterministic({"title": "Cache Systems for LLM"}, [_AmbiguousTitleAdapter()])
        self.assertEqual(result["status"], "ambiguous")
        self.assertIsNone(result["paper"])


class TestCliDispatchUnit(unittest.TestCase):
    def test_cli_retrieve_paper_dispatches_without_project_bootstrap(self):
        argv = ["cli.py", "retrieve-paper", "demo", "--doi", "10.1/xyz"]
        with patch.object(sys, "argv", argv), patch("src.cli.run_step", return_value="/tmp/result.yaml") as run_step:
            main()
        run_step.assert_called_once_with(
            "demo",
            "retrieve-paper",
            title="",
            doi="10.1/xyz",
            arxiv_url="",
            arxiv_id="",
        )


if __name__ == "__main__":
    unittest.main()
