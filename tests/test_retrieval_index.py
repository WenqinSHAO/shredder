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


class _ArxivAliasAdapter:
    def __init__(self):
        self.lookup_doi_calls = 0
        self.lookup_arxiv_calls = 0

    def lookup_doi(self, doi: str) -> list[dict]:
        self.lookup_doi_calls += 1
        return [
            {
                "source": "pyalex",
                "source_id": "wrong-doi-match",
                "title": "Attention Is All You Need",
                "venue": "",
                "year": "2025",
                "doi": "10.65215/2q58a426",
                "arxiv_id": "",
                "url": "https://doi.org/10.65215/2q58a426",
                "abstract": "Wrongly returned by doi lookup.",
                "keywords": ["wrong"],
                "categories": ["wrong"],
                "authors": [],
                "score": 1.0,
                "reason": "lookup_doi",
            }
        ]

    def lookup_arxiv(self, arxiv_id: str) -> list[dict]:
        self.lookup_arxiv_calls += 1
        return [
            {
                "source": "arxiv",
                "source_id": arxiv_id,
                "title": "Attention Is All You Need",
                "venue": "arXiv",
                "year": "2017",
                "doi": "",
                "arxiv_id": arxiv_id,
                "url": f"https://arxiv.org/abs/{arxiv_id}",
                "abstract": "Canonical arxiv paper.",
                "keywords": ["cs.CL"],
                "categories": ["cs.CL"],
                "authors": [],
                "score": 1.0,
                "reason": "lookup_arxiv",
            }
        ]

    def search_title(self, title: str, limit: int = 5) -> list[dict]:
        return []


class _ArxivCanonicalDoiAdapter:
    def __init__(self):
        self.lookup_arxiv_calls = 0
        self.lookup_doi_calls = 0

    def lookup_doi(self, doi: str) -> list[dict]:
        self.lookup_doi_calls += 1
        return []

    def lookup_arxiv(self, arxiv_id: str) -> list[dict]:
        self.lookup_arxiv_calls += 1
        return [
            {
                "source": "pyalex",
                "source_id": "https://openalex.org/W123",
                "title": "Canonical DOI Identity for arXiv Query",
                "venue": "SystemsConf",
                "year": "2024",
                "doi": "10.1000/canonical-doi",
                "arxiv_id": arxiv_id,
                "url": "https://doi.org/10.1000/canonical-doi",
                "abstract": "Paper returned from arXiv lookup but canonically identified by DOI.",
                "keywords": ["systems"],
                "categories": ["computer science"],
                "authors": [],
                "score": 1.0,
                "reason": "lookup_arxiv",
            }
        ]

    def search_title(self, title: str, limit: int = 5) -> list[dict]:
        return []


class _FastIncompleteAdapter:
    def __init__(self):
        self.lookup_doi_calls = 0

    def lookup_doi(self, doi: str) -> list[dict]:
        self.lookup_doi_calls += 1
        return [
            {
                "source": "fast_incomplete",
                "source_id": doi,
                "title": "Deterministic Systems",
                "venue": "",
                "year": "",
                "doi": doi,
                "arxiv_id": "",
                "url": "",
                "abstract": "",
                "keywords": [],
                "categories": [],
                "authors": [],
                "score": 1.0,
                "reason": "lookup_doi",
            }
        ]

    def lookup_arxiv(self, arxiv_id: str) -> list[dict]:
        return []

    def search_title(self, title: str, limit: int = 5) -> list[dict]:
        return []


class _ShrinkingAuthorGraphAdapter:
    def __init__(self):
        self.lookup_doi_calls = 0

    def lookup_doi(self, doi: str) -> list[dict]:
        self.lookup_doi_calls += 1
        if self.lookup_doi_calls == 1:
            authors = [
                {
                    "name": "Alice Example",
                    "orcid": "",
                    "source_id": "alice",
                    "affiliations": [{"name": "Org A", "ror": "", "country": "US"}],
                },
                {
                    "name": "Bob Example",
                    "orcid": "",
                    "source_id": "bob",
                    "affiliations": [{"name": "Org B", "ror": "", "country": "US"}],
                },
            ]
        else:
            authors = [
                {
                    "name": "Alice Example",
                    "orcid": "",
                    "source_id": "alice",
                    "affiliations": [{"name": "Org C", "ror": "", "country": "US"}],
                }
            ]
        return [
            {
                "source": "author_reconcile",
                "source_id": doi,
                "title": "Deterministic Systems",
                "venue": "NSDI",
                "year": "2024",
                "doi": doi,
                "arxiv_id": "",
                "url": "https://doi.org/" + doi,
                "abstract": "Author graph changes across retrieval runs.",
                "keywords": ["deterministic", "systems"],
                "categories": ["distributed systems"],
                "authors": authors,
                "score": 1.0,
                "reason": "lookup_doi",
            }
        ]

    def lookup_arxiv(self, arxiv_id: str) -> list[dict]:
        return []

    def search_title(self, title: str, limit: int = 5) -> list[dict]:
        return []


class _ConsensusEnricherAdapter:
    def __init__(self):
        self.lookup_doi_calls = 0

    def lookup_doi(self, doi: str) -> list[dict]:
        self.lookup_doi_calls += 1
        return [
            {
                "source": "consensus_enricher",
                "source_id": doi,
                "title": "Deterministic Systems",
                "venue": "NSDI",
                "year": "2024",
                "doi": doi,
                "arxiv_id": "",
                "url": "https://doi.org/" + doi,
                "abstract": "Enriched abstract from consensus fallback.",
                "keywords": ["deterministic", "systems"],
                "categories": ["distributed systems"],
                "authors": [{"name": "Alice Example", "orcid": "", "source_id": "a1", "affiliations": []}],
                "score": 0.95,
                "reason": "lookup_doi",
            }
        ]

    def lookup_arxiv(self, arxiv_id: str) -> list[dict]:
        return []

    def search_title(self, title: str, limit: int = 5) -> list[dict]:
        return []


class _TitleCollisionMissingYearAdapter:
    def __init__(self):
        self.lookup_doi_calls = 0
        self.search_title_calls = 0

    def lookup_doi(self, doi: str) -> list[dict]:
        self.lookup_doi_calls += 1
        return [
            {
                "source": "title_collision",
                "source_id": doi,
                "title": "Cache Systems for LLM",
                "venue": "NSDI",
                "year": "2024",
                "doi": doi,
                "arxiv_id": "",
                "url": "https://doi.org/" + doi,
                "abstract": "DOI-backed canonical paper.",
                "keywords": ["cache"],
                "categories": ["systems"],
                "authors": [],
                "score": 1.0,
                "reason": "lookup_doi",
            }
        ]

    def lookup_arxiv(self, arxiv_id: str) -> list[dict]:
        return []

    def search_title(self, title: str, limit: int = 5) -> list[dict]:
        self.search_title_calls += 1
        return [
            {
                "source": "title_collision",
                "source_id": "title-only",
                "title": "Cache Systems for LLM",
                "venue": "",
                "year": "",
                "doi": "",
                "arxiv_id": "",
                "url": "",
                "abstract": "Title-only record with missing year/identifier.",
                "keywords": [],
                "categories": [],
                "authors": [],
                "score": 0.9,
                "reason": "search_title",
            }
        ]


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
            self.assertEqual(payload["papers"][0]["paper"]["authors"][0]["affiliation_count"], 1)
            self.assertEqual(payload["papers"][0]["paper"]["authors"][0]["source_ids"], ["a1"])
            self.assertNotIn("affiliations", payload["papers"][0]["paper"]["authors"][0])
            self.assertNotIn("sources", payload["papers"][0])
            self.assertNotIn("diagnostics", payload["papers"][0])
            self.assertEqual(payload["papers"][0]["source_count"], 1)
            self.assertEqual(payload["papers"][0]["sources_truncated"], 1)
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
                author_meta_row = conn.execute(
                    """
                    SELECT source_ids_json, affiliations_json
                    FROM paper_author_metadata
                    WHERE paper_id='doi:10.1/xyz' AND author_id='habanero:a1'
                    """
                ).fetchone()
            finally:
                conn.close()
            self.assertIsNotNone(db_row)
            self.assertEqual(db_row[0], "A concise abstract about deterministic systems.")
            self.assertEqual(json.loads(db_row[1]), ["deterministic", "systems"])
            self.assertEqual(json.loads(db_row[2]), ["distributed systems"])
            self.assertIsNotNone(author_meta_row)
            self.assertEqual(json.loads(author_meta_row[0]), ["a1"])
            affiliations = json.loads(author_meta_row[1])
            self.assertEqual(len(affiliations), 1)
            self.assertEqual(affiliations[0]["name"], "Example University")

    def test_arxiv_doi_alias_hits_same_cached_entry_and_keeps_db_consistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ws = root / "workspace"
            kb_dir = root / "kb"
            kb_path = kb_dir / "kb.sqlite"
            ws.mkdir(parents=True, exist_ok=True)
            kb_dir.mkdir(parents=True, exist_ok=True)

            adapter = _ArxivAliasAdapter()

            with patch("src.utils.paths.WORKSPACE_ROOT", ws), patch("src.kb.store.KB_DIR", kb_dir), patch(
                "src.kb.store.KB_PATH", kb_path
            ):
                run_step("demo", "init", theme="systems")
                with patch("src.orchestrator.retrieval.build_adapters", return_value=[adapter]):
                    run_step("demo", "retrieve-paper", arxiv_url="https://arxiv.org/abs/1706.03762", policy="cache_first")
                    run_step("demo", "retrieve-paper", doi="https://doi.org/10.48550/arXiv.1706.03762", policy="cache_first")

            index_path = ws / "demo" / "artifacts" / "retrieval" / "deterministic_result.yaml"
            payload = yamlx.load(index_path)
            self.assertEqual(len(payload["papers"]), 1)
            self.assertEqual(payload["papers"][0]["paper_id"], "arxiv:1706.03762")
            self.assertEqual(payload["queries"][0]["query_key"], "arxiv:1706.03762")
            self.assertEqual(payload["queries"][1]["query_key"], "arxiv:1706.03762")
            self.assertFalse(payload["queries"][0]["cache_hit"])
            self.assertTrue(payload["queries"][1]["cache_hit"])

            self.assertEqual(adapter.lookup_arxiv_calls, 1)
            self.assertEqual(adapter.lookup_doi_calls, 0)

            conn = sqlite3.connect(kb_path)
            try:
                rows = conn.execute("SELECT id FROM papers ORDER BY id ASC").fetchall()
            finally:
                conn.close()
            self.assertEqual([row[0] for row in rows], ["arxiv:1706.03762"])

    def test_arxiv_query_cache_hit_when_canonical_id_is_doi(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ws = root / "workspace"
            kb_dir = root / "kb"
            kb_path = kb_dir / "kb.sqlite"
            ws.mkdir(parents=True, exist_ok=True)
            kb_dir.mkdir(parents=True, exist_ok=True)

            adapter = _ArxivCanonicalDoiAdapter()

            with patch("src.utils.paths.WORKSPACE_ROOT", ws), patch("src.kb.store.KB_DIR", kb_dir), patch(
                "src.kb.store.KB_PATH", kb_path
            ):
                run_step("demo", "init", theme="systems")
                with patch("src.orchestrator.retrieval.build_adapters", return_value=[adapter]):
                    run_step("demo", "retrieve-paper", arxiv_id="2401.12345", policy="cache_first")
                    run_step("demo", "retrieve-paper", arxiv_id="2401.12345", policy="cache_first")

            index_path = ws / "demo" / "artifacts" / "retrieval" / "deterministic_result.yaml"
            payload = yamlx.load(index_path)
            self.assertEqual(len(payload["papers"]), 1)
            self.assertEqual(payload["papers"][0]["paper_id"], "doi:10.1000/canonical-doi")
            self.assertEqual(payload["queries"][0]["query_key"], "arxiv:2401.12345")
            self.assertEqual(payload["queries"][1]["query_key"], "arxiv:2401.12345")
            self.assertFalse(payload["queries"][0]["cache_hit"])
            self.assertTrue(payload["queries"][1]["cache_hit"])
            self.assertEqual(adapter.lookup_arxiv_calls, 1)
            self.assertEqual(adapter.lookup_doi_calls, 0)

            conn = sqlite3.connect(kb_path)
            try:
                row = conn.execute("SELECT id, arxiv_id FROM papers WHERE id='doi:10.1000/canonical-doi'").fetchone()
            finally:
                conn.close()
            self.assertIsNotNone(row)
            self.assertEqual(row[0], "doi:10.1000/canonical-doi")
            self.assertEqual(row[1], "2401.12345")

    def test_cache_first_uses_fast_then_fallback_to_consensus_for_incomplete_paper(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ws = root / "workspace"
            kb_dir = root / "kb"
            kb_path = kb_dir / "kb.sqlite"
            ws.mkdir(parents=True, exist_ok=True)
            kb_dir.mkdir(parents=True, exist_ok=True)

            fast_adapter = _FastIncompleteAdapter()
            consensus_adapter = _ConsensusEnricherAdapter()

            with patch("src.utils.paths.WORKSPACE_ROOT", ws), patch("src.kb.store.KB_DIR", kb_dir), patch(
                "src.kb.store.KB_PATH", kb_path
            ):
                run_step("demo", "init", theme="systems")
                with patch("src.orchestrator.retrieval.build_adapters", return_value=[fast_adapter, consensus_adapter]):
                    run_step("demo", "retrieve-paper", doi="10.1/xyz", policy="cache_first")

            self.assertEqual(fast_adapter.lookup_doi_calls, 2)
            self.assertEqual(consensus_adapter.lookup_doi_calls, 1)

            index_path = ws / "demo" / "artifacts" / "retrieval" / "deterministic_result.yaml"
            payload = yamlx.load(index_path)
            self.assertEqual(payload["queries"][0]["resolution_status"], "resolved")
            self.assertFalse(payload["queries"][0]["cache_hit"])
            self.assertEqual(payload["papers"][0]["paper_id"], "doi:10.1/xyz")
            self.assertEqual(payload["papers"][0]["paper"]["year"], "2024")
            self.assertIn("Enriched abstract", payload["papers"][0]["paper"]["abstract"])

            conn = sqlite3.connect(kb_path)
            try:
                db_row = conn.execute("SELECT year, abstract FROM papers WHERE id='doi:10.1/xyz'").fetchone()
            finally:
                conn.close()
            self.assertEqual(db_row[0], 2024)
            self.assertEqual(db_row[1], "Enriched abstract from consensus fallback.")

    def test_retrieval_reconciles_stale_paper_author_and_author_org_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ws = root / "workspace"
            kb_dir = root / "kb"
            kb_path = kb_dir / "kb.sqlite"
            ws.mkdir(parents=True, exist_ok=True)
            kb_dir.mkdir(parents=True, exist_ok=True)

            adapter = _ShrinkingAuthorGraphAdapter()

            with patch("src.utils.paths.WORKSPACE_ROOT", ws), patch("src.kb.store.KB_DIR", kb_dir), patch(
                "src.kb.store.KB_PATH", kb_path
            ):
                run_step("demo", "init", theme="systems")
                with patch("src.orchestrator.retrieval.build_adapters", return_value=[adapter]):
                    run_step("demo", "retrieve-paper", doi="10.1/xyz", policy="consensus")
                    run_step("demo", "retrieve-paper", doi="10.1/xyz", policy="consensus")

            conn = sqlite3.connect(kb_path)
            try:
                paper_authors = conn.execute(
                    "SELECT author_id, position FROM paper_authors WHERE paper_id='doi:10.1/xyz' ORDER BY position ASC"
                ).fetchall()
                author_orgs = conn.execute("SELECT author_id, org_id FROM author_orgs ORDER BY author_id ASC, org_id ASC").fetchall()
                org_rows = conn.execute("SELECT id, name FROM orgs ORDER BY id ASC").fetchall()
            finally:
                conn.close()

            self.assertEqual(adapter.lookup_doi_calls, 2)
            self.assertEqual(paper_authors, [("author_reconcile:alice", 0)])
            self.assertEqual(len(author_orgs), 1)
            self.assertEqual(author_orgs[0][0], "author_reconcile:alice")
            org_name_by_id = {org_id: name for org_id, name in org_rows}
            linked_org_id = author_orgs[0][1]
            self.assertEqual(org_name_by_id.get(linked_org_id), "Org C")

    def test_title_only_missing_year_does_not_merge_into_existing_doi_paper(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ws = root / "workspace"
            kb_dir = root / "kb"
            kb_path = kb_dir / "kb.sqlite"
            ws.mkdir(parents=True, exist_ok=True)
            kb_dir.mkdir(parents=True, exist_ok=True)

            adapter = _TitleCollisionMissingYearAdapter()

            with patch("src.utils.paths.WORKSPACE_ROOT", ws), patch("src.kb.store.KB_DIR", kb_dir), patch(
                "src.kb.store.KB_PATH", kb_path
            ):
                run_step("demo", "init", theme="systems")
                with patch("src.orchestrator.retrieval.build_adapters", return_value=[adapter]):
                    run_step("demo", "retrieve-paper", doi="10.1/xyz", policy="consensus")
                    run_step("demo", "retrieve-paper", title="Cache Systems for LLM", policy="consensus")

            index_path = ws / "demo" / "artifacts" / "retrieval" / "deterministic_result.yaml"
            payload = yamlx.load(index_path)
            self.assertEqual(len(payload["papers"]), 2)
            paper_ids = {entry["paper_id"] for entry in payload["papers"]}
            self.assertIn("doi:10.1/xyz", paper_ids)
            self.assertIn("title:cache systems for llm:", paper_ids)
            self.assertEqual(payload["queries"][0]["paper_id"], "doi:10.1/xyz")
            self.assertEqual(payload["queries"][1]["paper_id"], "title:cache systems for llm:")

            conn = sqlite3.connect(kb_path)
            try:
                db_ids = [row[0] for row in conn.execute("SELECT id FROM papers ORDER BY id ASC").fetchall()]
            finally:
                conn.close()
            self.assertIn("doi:10.1/xyz", db_ids)
            self.assertIn("title:cache systems for llm:", db_ids)


if __name__ == "__main__":
    unittest.main()
