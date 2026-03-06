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
        self.assertEqual(result["status"], "ambiguous_requires_selection")
        self.assertEqual(result["resolution_status"], "ambiguous_requires_selection")
        self.assertIsNone(result["paper"])

    def test_no_match_includes_diagnostics(self):
        class _EmptyAdapter:
            name = "empty"

            def lookup_doi(self, doi: str) -> list[dict]:
                return []

            def lookup_arxiv(self, arxiv_id: str) -> list[dict]:
                return []

            def search_title(self, title: str, limit: int = 5) -> list[dict]:
                return []

        result = resolve_deterministic({"doi": "https://arxiv.org/abs/1706.03762"}, [_EmptyAdapter()])
        self.assertEqual(result["status"], "not_found")
        self.assertIn("diagnostics", result)
        self.assertIn("search_trace", result)
        self.assertEqual(result["diagnostics"]["lookup_mode"], "doi")
        self.assertEqual(result["query_classification"], "doi")
        self.assertIn("doi_argument_looks_like_arxiv_url", result["diagnostics"]["input_warnings"])
        self.assertEqual(len(result["diagnostics"]["adapter_calls"]), 1)

    def test_fast_policy_stops_after_first_hit(self):
        class _HitAdapter:
            name = "hit"

            def __init__(self):
                self.calls = 0

            def lookup_doi(self, doi: str) -> list[dict]:
                self.calls += 1
                return [
                    {
                        "source": "hit",
                        "source_id": doi,
                        "title": "Paper",
                        "venue": "",
                        "year": "2024",
                        "doi": doi,
                        "arxiv_id": "",
                        "url": "",
                        "authors": [],
                        "score": 1.0,
                        "reason": "lookup_doi",
                    }
                ]

            def lookup_arxiv(self, arxiv_id: str) -> list[dict]:
                return []

            def search_title(self, title: str, limit: int = 5) -> list[dict]:
                return []

        class _SkippedAdapter:
            name = "skipped"

            def __init__(self):
                self.calls = 0

            def lookup_doi(self, doi: str) -> list[dict]:
                self.calls += 1
                return []

            def lookup_arxiv(self, arxiv_id: str) -> list[dict]:
                return []

            def search_title(self, title: str, limit: int = 5) -> list[dict]:
                return []

        first = _HitAdapter()
        second = _SkippedAdapter()
        result = resolve_deterministic({"doi": "10.1/xyz", "policy": "fast"}, [first, second])
        self.assertEqual(result["status"], "resolved")
        self.assertEqual(first.calls, 1)
        self.assertEqual(second.calls, 0)
        calls = result["diagnostics"]["adapter_calls"]
        self.assertEqual(calls[1]["action"], "skipped_due_fast_policy")


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
            policy="",
        )


if __name__ == "__main__":
    unittest.main()
