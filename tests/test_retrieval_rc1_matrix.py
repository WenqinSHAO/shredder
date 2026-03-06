from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.orchestrator.runner import run_step
from src.utils import yamlx


class _MatrixAdapter:
    def __init__(self):
        self.lookup_doi_calls = 0
        self.lookup_arxiv_calls = 0
        self.search_title_calls = 0

    def total_calls(self) -> int:
        return self.lookup_doi_calls + self.lookup_arxiv_calls + self.search_title_calls

    def _doi_row(self, doi: str, *, arxiv_id: str = "") -> dict:
        return {
            "source": "matrix",
            "source_id": f"doi:{doi}",
            "title": "Deterministic Systems",
            "venue": "NSDI",
            "year": "2024",
            "doi": doi,
            "arxiv_id": arxiv_id,
            "url": f"https://doi.org/{doi}",
            "abstract": "Deterministic lookup row.",
            "keywords": ["deterministic"],
            "categories": ["systems"],
            "authors": [],
            "score": 1.0,
            "reason": "lookup_doi",
        }

    def lookup_doi(self, doi: str) -> list[dict]:
        self.lookup_doi_calls += 1
        if doi == "10.1/xyz":
            return [self._doi_row("10.1/xyz")]
        return []

    def lookup_arxiv(self, arxiv_id: str) -> list[dict]:
        self.lookup_arxiv_calls += 1
        if arxiv_id == "1706.03762":
            return [
                {
                    "source": "matrix",
                    "source_id": "arxiv:1706.03762",
                    "title": "Attention Is All You Need",
                    "venue": "arXiv",
                    "year": "2017",
                    "doi": "",
                    "arxiv_id": "1706.03762",
                    "url": "https://arxiv.org/abs/1706.03762",
                    "abstract": "Canonical arXiv row.",
                    "keywords": ["cs.CL"],
                    "categories": ["cs.CL"],
                    "authors": [],
                    "score": 1.0,
                    "reason": "lookup_arxiv",
                }
            ]
        if arxiv_id == "2401.12345":
            return [self._doi_row("10.1/xyz", arxiv_id="2401.12345")]
        return []

    def search_title(self, title: str, limit: int = 5) -> list[dict]:
        self.search_title_calls += 1
        lowered = title.strip().lower()
        if lowered == "deterministic systems":
            return [
                {
                    "source": "matrix",
                    "source_id": "title-exact",
                    "title": "Deterministic Systems",
                    "venue": "NSDI",
                    "year": "2024",
                    "doi": "10.2000/title-exact",
                    "arxiv_id": "",
                    "url": "https://doi.org/10.2000/title-exact",
                    "abstract": "Exact title row.",
                    "keywords": ["deterministic"],
                    "categories": ["systems"],
                    "authors": [],
                    "score": 0.9,
                    "reason": "search_title",
                }
            ]
        if lowered == "cache systems for llm":
            return [
                {
                    "source": "matrix",
                    "source_id": "title-amb-1",
                    "title": "Cache Systems for LLM",
                    "venue": "OSDI",
                    "year": "2024",
                    "doi": "",
                    "arxiv_id": "",
                    "url": "",
                    "abstract": "",
                    "keywords": [],
                    "categories": [],
                    "authors": [],
                    "score": 0.8,
                    "reason": "search_title",
                },
                {
                    "source": "matrix",
                    "source_id": "title-amb-2",
                    "title": "Cache Systems for LLM",
                    "venue": "NSDI",
                    "year": "2023",
                    "doi": "",
                    "arxiv_id": "",
                    "url": "",
                    "abstract": "",
                    "keywords": [],
                    "categories": [],
                    "authors": [],
                    "score": 0.8,
                    "reason": "search_title",
                },
            ]
        return []


class TestRetrievalRC1Matrix(unittest.TestCase):
    FIXTURE_PATH = Path(__file__).parent / "fixtures" / "deterministic_benchmark_cases.json"

    def _run_single_case(
        self,
        *,
        query: dict,
        policy: str,
        expected_query_key: str,
        expected_status: str,
        expected_paper_id: str,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ws = root / "workspace"
            kb_dir = root / "kb"
            kb_path = kb_dir / "kb.sqlite"
            ws.mkdir(parents=True, exist_ok=True)
            kb_dir.mkdir(parents=True, exist_ok=True)

            adapter = _MatrixAdapter()

            with patch("src.utils.paths.WORKSPACE_ROOT", ws), patch("src.kb.store.KB_DIR", kb_dir), patch(
                "src.kb.store.KB_PATH", kb_path
            ):
                run_step("demo", "init", theme="systems")
                with patch("src.orchestrator.retrieval.build_adapters", return_value=[adapter]):
                    run_step("demo", "retrieve-paper", policy=policy, **query)

            index_path = ws / "demo" / "artifacts" / "retrieval" / "deterministic_result.yaml"
            index_payload = yamlx.load(index_path)
            self.assertEqual(len(index_payload["queries"]), 1)
            event = index_payload["queries"][0]
            self.assertEqual(event["query_key"], expected_query_key)
            self.assertEqual(event["resolution_status"], expected_status)
            self.assertFalse(event["cache_hit"])

            if expected_status == "resolved":
                self.assertEqual(event["paper_id"], expected_paper_id)
                self.assertEqual(len(index_payload["papers"]), 1)
                self.assertEqual(index_payload["papers"][0]["paper_id"], expected_paper_id)
            else:
                self.assertEqual(event["paper_id"], "")
                self.assertEqual(index_payload["papers"], [])

    def _run_case(
        self,
        *,
        query: dict,
        expected_query_key: str,
        expected_status: str,
        expected_paper_id: str,
        expect_replay_cache_hit: bool,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ws = root / "workspace"
            kb_dir = root / "kb"
            kb_path = kb_dir / "kb.sqlite"
            ws.mkdir(parents=True, exist_ok=True)
            kb_dir.mkdir(parents=True, exist_ok=True)

            adapter = _MatrixAdapter()

            with patch("src.utils.paths.WORKSPACE_ROOT", ws), patch("src.kb.store.KB_DIR", kb_dir), patch(
                "src.kb.store.KB_PATH", kb_path
            ):
                run_step("demo", "init", theme="systems")
                with patch("src.orchestrator.retrieval.build_adapters", return_value=[adapter]):
                    run_step("demo", "retrieve-paper", policy="cache_first", **query)
                    run_step("demo", "retrieve-paper", policy="cache_first", **query)

            index_path = ws / "demo" / "artifacts" / "retrieval" / "deterministic_result.yaml"
            index_payload = yamlx.load(index_path)
            self.assertEqual(len(index_payload["queries"]), 2)
            self.assertEqual(index_payload["queries"][0]["query_key"], expected_query_key)
            self.assertEqual(index_payload["queries"][1]["query_key"], expected_query_key)
            self.assertEqual(index_payload["queries"][0]["resolution_status"], expected_status)
            self.assertEqual(index_payload["queries"][1]["resolution_status"], expected_status)
            self.assertFalse(index_payload["queries"][0]["cache_hit"])
            self.assertEqual(bool(index_payload["queries"][1]["cache_hit"]), expect_replay_cache_hit)

            request_path = ws / "demo" / "artifacts" / "retrieval" / "deterministic_request.yaml"
            request_payload = yamlx.load(request_path)
            self.assertEqual(len(request_payload["history"]), 2)
            self.assertEqual(request_payload["history"][0]["query_key"], expected_query_key)
            self.assertEqual(request_payload["history"][1]["query_key"], expected_query_key)
            self.assertEqual(request_payload["history"][0]["resolution_status"], expected_status)
            self.assertEqual(request_payload["history"][1]["resolution_status"], expected_status)

            if expected_status == "resolved":
                self.assertEqual(len(index_payload["papers"]), 1)
                self.assertEqual(index_payload["papers"][0]["paper_id"], expected_paper_id)
                self.assertEqual(index_payload["queries"][0]["paper_id"], expected_paper_id)
                self.assertEqual(index_payload["queries"][1]["paper_id"], expected_paper_id)
            else:
                self.assertEqual(index_payload["papers"], [])
                self.assertEqual(index_payload["queries"][0]["paper_id"], "")
                self.assertEqual(index_payload["queries"][1]["paper_id"], "")

            if expect_replay_cache_hit:
                self.assertEqual(adapter.total_calls(), 1)
            else:
                self.assertEqual(adapter.total_calls(), 2)

    def test_cold_start_matrix_and_cache_replay(self):
        scenarios = [
            {
                "name": "doi_exact",
                "query": {"doi": "10.1/xyz"},
                "expected_query_key": "doi:10.1/xyz",
                "expected_status": "resolved",
                "expected_paper_id": "doi:10.1/xyz",
                "expect_replay_cache_hit": True,
            },
            {
                "name": "arxiv_url",
                "query": {"arxiv_url": "https://arxiv.org/abs/1706.03762"},
                "expected_query_key": "arxiv:1706.03762",
                "expected_status": "resolved",
                "expected_paper_id": "arxiv:1706.03762",
                "expect_replay_cache_hit": True,
            },
            {
                "name": "arxiv_id_doi_canonical",
                "query": {"arxiv_id": "2401.12345"},
                "expected_query_key": "arxiv:2401.12345",
                "expected_status": "resolved",
                "expected_paper_id": "doi:10.1/xyz",
                "expect_replay_cache_hit": True,
            },
            {
                "name": "arxiv_doi_alias",
                "query": {"doi": "https://doi.org/10.48550/arXiv.1706.03762"},
                "expected_query_key": "arxiv:1706.03762",
                "expected_status": "resolved",
                "expected_paper_id": "arxiv:1706.03762",
                "expect_replay_cache_hit": True,
            },
            {
                "name": "title_exact",
                "query": {"title": "Deterministic Systems"},
                "expected_query_key": "title:deterministic systems",
                "expected_status": "resolved",
                "expected_paper_id": "doi:10.2000/title-exact",
                "expect_replay_cache_hit": True,
            },
            {
                "name": "title_ambiguous",
                "query": {"title": "Cache Systems for LLM"},
                "expected_query_key": "title:cache systems for llm",
                "expected_status": "ambiguous_requires_selection",
                "expected_paper_id": "",
                "expect_replay_cache_hit": False,
            },
        ]

        for scenario in scenarios:
            with self.subTest(case=scenario["name"]):
                self._run_case(
                    query=scenario["query"],
                    expected_query_key=scenario["expected_query_key"],
                    expected_status=scenario["expected_status"],
                    expected_paper_id=scenario["expected_paper_id"],
                    expect_replay_cache_hit=scenario["expect_replay_cache_hit"],
                )

    def test_identifier_benchmark_fixture_is_stable_across_policies(self):
        fixture_cases = json.loads(self.FIXTURE_PATH.read_text(encoding="utf-8"))
        for case in fixture_cases:
            for policy in ("consensus", "fast", "cache_first"):
                with self.subTest(case=case["name"], policy=policy):
                    self._run_single_case(
                        query=case["query"],
                        policy=policy,
                        expected_query_key=case["expected_query_key"],
                        expected_status=case["expected_status"],
                        expected_paper_id=case.get("expected_paper_id", ""),
                    )


if __name__ == "__main__":
    unittest.main()
