from __future__ import annotations

import csv
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.orchestrator.runner import run_step
from src.retrieval.service import SOURCE_FIELDS
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


class _EmptyAdapter:
    def __init__(self):
        self.lookup_doi_calls = 0
        self.lookup_arxiv_calls = 0
        self.search_title_calls = 0

    def lookup_doi(self, doi: str) -> list[dict]:
        self.lookup_doi_calls += 1
        return []

    def lookup_arxiv(self, arxiv_id: str) -> list[dict]:
        self.lookup_arxiv_calls += 1
        return []

    def search_title(self, title: str, limit: int = 5) -> list[dict]:
        self.search_title_calls += 1
        return []


class _PermutationAdapter:
    def __init__(self):
        self.lookup_doi_calls = 0
        self.lookup_arxiv_calls = 0
        self.search_title_calls = 0

    def _row(self) -> dict:
        return {
            "source": "permutation",
            "source_id": "perm:10.1/xyz",
            "title": "Deterministic Systems",
            "venue": "NSDI",
            "year": "2024",
            "doi": "10.1/xyz",
            "arxiv_id": "2401.12345",
            "url": "https://doi.org/10.1/xyz",
            "abstract": "One canonical record reachable from multiple identifiers.",
            "keywords": ["deterministic", "systems"],
            "categories": ["distributed systems"],
            "authors": [],
            "score": 1.0,
            "reason": "synthetic",
        }

    def lookup_doi(self, doi: str) -> list[dict]:
        self.lookup_doi_calls += 1
        if doi == "10.1/xyz":
            return [self._row()]
        return []

    def lookup_arxiv(self, arxiv_id: str) -> list[dict]:
        self.lookup_arxiv_calls += 1
        if arxiv_id == "2401.12345":
            return [self._row()]
        return []

    def search_title(self, title: str, limit: int = 5) -> list[dict]:
        self.search_title_calls += 1
        if title.strip().lower() == "deterministic systems":
            return [self._row()]
        return []


class _RichMetadataAdapter:
    def __init__(self, source: str, abstract: str, keywords: list[str], categories: list[str]):
        self.source = source
        self.abstract = abstract
        self.keywords = keywords
        self.categories = categories
        self.lookup_doi_calls = 0

    def lookup_doi(self, doi: str) -> list[dict]:
        self.lookup_doi_calls += 1
        if doi != "10.1/rich":
            return []
        return [
            {
                "source": self.source,
                "source_id": f"{self.source}:{doi}",
                "title": "Rich Metadata Paper",
                "venue": "OSDI",
                "year": "2024",
                "doi": doi,
                "arxiv_id": "",
                "url": f"https://doi.org/{doi}",
                "abstract": self.abstract,
                "keywords": list(self.keywords),
                "categories": list(self.categories),
                "authors": [],
                "score": 0.9,
                "reason": "lookup_doi",
            }
        ]

    def lookup_arxiv(self, arxiv_id: str) -> list[dict]:
        return []

    def search_title(self, title: str, limit: int = 5) -> list[dict]:
        return []


class _AnyDoiAdapter:
    def __init__(self):
        self.lookup_doi_calls = 0

    def lookup_doi(self, doi: str) -> list[dict]:
        self.lookup_doi_calls += 1
        normalized = str(doi).strip().lower()
        if not normalized.startswith("10.77/"):
            return []
        suffix = normalized.split("/", 1)[1]
        return [
            {
                "source": "any_doi",
                "source_id": normalized,
                "title": f"Synthetic Paper {suffix}",
                "venue": "SyntheticConf",
                "year": "2024",
                "doi": normalized,
                "arxiv_id": "",
                "url": f"https://doi.org/{normalized}",
                "abstract": f"Synthetic abstract for {suffix}.",
                "keywords": [f"k-{suffix}"],
                "categories": ["synthetic"],
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

    def test_cache_first_not_found_keeps_index_empty_and_logs_query(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ws = root / "workspace"
            kb_dir = root / "kb"
            kb_path = kb_dir / "kb.sqlite"
            ws.mkdir(parents=True, exist_ok=True)
            kb_dir.mkdir(parents=True, exist_ok=True)

            adapter = _EmptyAdapter()

            with patch("src.utils.paths.WORKSPACE_ROOT", ws), patch("src.kb.store.KB_DIR", kb_dir), patch(
                "src.kb.store.KB_PATH", kb_path
            ):
                run_step("demo", "init", theme="systems")
                with patch("src.orchestrator.retrieval.build_adapters", return_value=[adapter]):
                    run_step("demo", "retrieve-paper", doi="10.9/missing", policy="cache_first")

            index_path = ws / "demo" / "artifacts" / "retrieval" / "deterministic_result.yaml"
            payload = yamlx.load(index_path)
            self.assertEqual(payload["papers"], [])
            self.assertEqual(len(payload["queries"]), 1)
            self.assertEqual(payload["queries"][0]["query_key"], "doi:10.9/missing")
            self.assertEqual(payload["queries"][0]["resolution_status"], "not_found")
            self.assertEqual(payload["queries"][0]["reason"], "no_candidates")
            self.assertFalse(payload["queries"][0]["cache_hit"])

            request_log_path = ws / "demo" / "artifacts" / "retrieval" / "deterministic_request.yaml"
            request_log = yamlx.load(request_log_path)
            self.assertEqual(len(request_log["history"]), 1)
            self.assertEqual(request_log["history"][0]["query_key"], "doi:10.9/missing")
            self.assertEqual(request_log["history"][0]["resolution_status"], "not_found")
            self.assertEqual(request_log["history"][0]["reason"], "no_candidates")
            self.assertFalse(request_log["history"][0]["cache_hit"])

            sources_log_path = ws / "demo" / "artifacts" / "retrieval" / "deterministic_sources.tsv"
            with sources_log_path.open("r", encoding="utf-8") as f:
                rows = list(csv.DictReader(f, delimiter="\t"))
            self.assertEqual(rows, [])
            self.assertEqual(adapter.lookup_doi_calls, 1)

    def test_mixed_identifier_permutations_keep_single_canonical_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ws = root / "workspace"
            kb_dir = root / "kb"
            kb_path = kb_dir / "kb.sqlite"
            ws.mkdir(parents=True, exist_ok=True)
            kb_dir.mkdir(parents=True, exist_ok=True)

            adapter = _PermutationAdapter()

            with patch("src.utils.paths.WORKSPACE_ROOT", ws), patch("src.kb.store.KB_DIR", kb_dir), patch(
                "src.kb.store.KB_PATH", kb_path
            ):
                run_step("demo", "init", theme="systems")
                with patch("src.orchestrator.retrieval.build_adapters", return_value=[adapter]):
                    run_step("demo", "retrieve-paper", doi="10.1/xyz", policy="cache_first")
                    run_step("demo", "retrieve-paper", arxiv_id="2401.12345", policy="cache_first")
                    run_step("demo", "retrieve-paper", doi="https://doi.org/10.48550/arXiv.2401.12345", policy="cache_first")
                    run_step("demo", "retrieve-paper", title="Deterministic Systems", policy="cache_first")

            index_path = ws / "demo" / "artifacts" / "retrieval" / "deterministic_result.yaml"
            payload = yamlx.load(index_path)
            self.assertEqual(len(payload["papers"]), 1)
            entry = payload["papers"][0]
            self.assertEqual(entry["paper_id"], "doi:10.1/xyz")
            self.assertIn("doi:10.1/xyz", entry["query_keys"])
            self.assertIn("arxiv:2401.12345", entry["query_keys"])
            self.assertIn("title:deterministic systems", entry["query_keys"])

            query_keys = [row["query_key"] for row in payload["queries"]]
            self.assertEqual(
                query_keys,
                ["doi:10.1/xyz", "arxiv:2401.12345", "arxiv:2401.12345", "title:deterministic systems"],
            )
            cache_hits = [bool(row["cache_hit"]) for row in payload["queries"]]
            self.assertEqual(cache_hits, [False, True, True, True])

            self.assertEqual(adapter.lookup_doi_calls, 1)
            self.assertEqual(adapter.lookup_arxiv_calls, 0)
            self.assertEqual(adapter.search_title_calls, 0)

    def test_index_artifact_stays_compact_after_repeated_runs(self):
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
                    for _ in range(20):
                        run_step("demo", "retrieve-paper", doi="10.1/xyz", policy="cache_first")

            index_path = ws / "demo" / "artifacts" / "retrieval" / "deterministic_result.yaml"
            payload = yamlx.load(index_path)
            self.assertEqual(len(payload["papers"]), 1)
            self.assertEqual(len(payload["queries"]), 20)
            paper_entry = payload["papers"][0]
            self.assertNotIn("sources", paper_entry)
            self.assertNotIn("diagnostics", paper_entry)
            self.assertLess(index_path.stat().st_size, 18000)
            self.assertEqual(adapter.lookup_doi_calls, 1)

    def test_legacy_db_and_artifact_shapes_are_forward_compatible(self):
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
                rdir = ws / "demo" / "artifacts" / "retrieval"

                yamlx.dump_to_path(
                    rdir / "deterministic_request.yaml",
                    {
                        "title": "Legacy Query",
                        "doi": "",
                        "arxiv_url": "",
                        "arxiv_id": "",
                        "policy": "cache_first",
                        "ambiguity_delta": 0.05,
                    },
                )
                with (rdir / "deterministic_sources.tsv").open("w", encoding="utf-8", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=SOURCE_FIELDS, delimiter="\t")
                    writer.writeheader()
                    writer.writerow(
                        {
                            "source": "legacy",
                            "source_id": "legacy-1",
                            "title": "Legacy Row",
                            "venue": "",
                            "year": "",
                            "doi": "",
                            "arxiv_id": "",
                            "url": "",
                            "abstract": "",
                            "keywords": "",
                            "categories": "",
                            "score": "0.0",
                            "reason": "legacy",
                        }
                    )

                with sqlite3.connect(kb_path) as conn:
                    conn.executescript(
                        """
                        CREATE TABLE papers (
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
                        """
                    )

                adapter = _CountingAdapter()
                with patch("src.orchestrator.retrieval.build_adapters", return_value=[adapter]):
                    run_step("demo", "retrieve-paper", doi="10.1/xyz", policy="cache_first")

            with sqlite3.connect(kb_path) as conn:
                paper_columns = [str(row[1]) for row in conn.execute("PRAGMA table_info(papers)").fetchall()]
                table_names = {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            self.assertIn("arxiv_id", paper_columns)
            self.assertIn("keywords_json", paper_columns)
            self.assertIn("categories_json", paper_columns)
            self.assertIn("paper_author_metadata", table_names)

            request_log_path = ws / "demo" / "artifacts" / "retrieval" / "deterministic_request.yaml"
            request_log = yamlx.load(request_log_path)
            self.assertGreaterEqual(len(request_log["history"]), 2)
            self.assertEqual(request_log["history"][0]["reason"], "legacy_single_snapshot")
            self.assertEqual(request_log["history"][-1]["query_key"], "doi:10.1/xyz")
            self.assertEqual(request_log["history"][-1]["resolution_status"], "resolved")

            sources_log_path = ws / "demo" / "artifacts" / "retrieval" / "deterministic_sources.tsv"
            with sources_log_path.open("r", encoding="utf-8", newline="") as f:
                reader = csv.DictReader(f, delimiter="\t")
                rows = list(reader)
                fieldnames = reader.fieldnames or []
            self.assertEqual(fieldnames[:4], ["timestamp", "query_key", "resolution_status", "paper_id"])
            self.assertGreaterEqual(len(rows), 2)

    def test_cache_first_uses_fast_path_without_consensus_fallback_when_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ws = root / "workspace"
            kb_dir = root / "kb"
            kb_path = kb_dir / "kb.sqlite"
            ws.mkdir(parents=True, exist_ok=True)
            kb_dir.mkdir(parents=True, exist_ok=True)

            fast_adapter = _CountingAdapter()
            fallback_adapter = _EmptyAdapter()

            with patch("src.utils.paths.WORKSPACE_ROOT", ws), patch("src.kb.store.KB_DIR", kb_dir), patch(
                "src.kb.store.KB_PATH", kb_path
            ):
                run_step("demo", "init", theme="systems")
                with patch("src.orchestrator.retrieval.build_adapters", return_value=[fast_adapter, fallback_adapter]):
                    run_step("demo", "retrieve-paper", doi="10.1/xyz", policy="cache_first")

            index_path = ws / "demo" / "artifacts" / "retrieval" / "deterministic_result.yaml"
            payload = yamlx.load(index_path)
            self.assertEqual(len(payload["queries"]), 1)
            self.assertEqual(payload["queries"][0]["resolution_status"], "resolved")
            self.assertFalse(payload["queries"][0]["cache_hit"])
            self.assertEqual(payload["queries"][0]["paper_id"], "doi:10.1/xyz")
            self.assertEqual(fast_adapter.lookup_doi_calls, 1)
            self.assertEqual(fallback_adapter.lookup_doi_calls, 0)

    def test_multi_adapter_metadata_richness_merges_and_persists(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ws = root / "workspace"
            kb_dir = root / "kb"
            kb_path = kb_dir / "kb.sqlite"
            ws.mkdir(parents=True, exist_ok=True)
            kb_dir.mkdir(parents=True, exist_ok=True)

            adapter_a = _RichMetadataAdapter(
                source="meta_a",
                abstract="Short abstract.",
                keywords=["deterministic", "retrieval"],
                categories=["systems"],
            )
            adapter_b = _RichMetadataAdapter(
                source="meta_b",
                abstract="Longer abstract with more context for merge preference.",
                keywords=["metadata", "retrieval"],
                categories=["databases"],
            )

            with patch("src.utils.paths.WORKSPACE_ROOT", ws), patch("src.kb.store.KB_DIR", kb_dir), patch(
                "src.kb.store.KB_PATH", kb_path
            ):
                run_step("demo", "init", theme="systems")
                with patch("src.orchestrator.retrieval.build_adapters", return_value=[adapter_a, adapter_b]):
                    run_step("demo", "retrieve-paper", doi="10.1/rich", policy="consensus")

            index_path = ws / "demo" / "artifacts" / "retrieval" / "deterministic_result.yaml"
            payload = yamlx.load(index_path)
            self.assertEqual(len(payload["papers"]), 1)
            paper = payload["papers"][0]["paper"]
            self.assertEqual(paper["paper_id"], "doi:10.1/rich")
            self.assertEqual(paper["abstract"], "Longer abstract with more context for merge preference.")
            self.assertEqual(set(paper["keywords"]), {"deterministic", "retrieval", "metadata"})
            self.assertEqual(set(paper["categories"]), {"systems", "databases"})

            with sqlite3.connect(kb_path) as conn:
                row = conn.execute(
                    "SELECT abstract, keywords_json, categories_json FROM papers WHERE id='doi:10.1/rich'"
                ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row[0], "Longer abstract with more context for merge preference.")
            self.assertEqual(set(json.loads(row[1])), {"deterministic", "retrieval", "metadata"})
            self.assertEqual(set(json.loads(row[2])), {"systems", "databases"})

    def test_artifact_size_growth_is_bounded_by_query_and_paper_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ws = root / "workspace"
            kb_dir = root / "kb"
            kb_path = kb_dir / "kb.sqlite"
            ws.mkdir(parents=True, exist_ok=True)
            kb_dir.mkdir(parents=True, exist_ok=True)

            adapter = _AnyDoiAdapter()
            doi_queries = [f"10.77/p{i}" for i in range(12)]
            replay_queries = doi_queries + doi_queries[:8]

            with patch("src.utils.paths.WORKSPACE_ROOT", ws), patch("src.kb.store.KB_DIR", kb_dir), patch(
                "src.kb.store.KB_PATH", kb_path
            ):
                run_step("demo", "init", theme="systems")
                with patch("src.orchestrator.retrieval.build_adapters", return_value=[adapter]):
                    for doi in replay_queries:
                        run_step("demo", "retrieve-paper", doi=doi, policy="cache_first")

            index_path = ws / "demo" / "artifacts" / "retrieval" / "deterministic_result.yaml"
            payload = yamlx.load(index_path)
            self.assertEqual(len(payload["papers"]), 12)
            self.assertEqual(len(payload["queries"]), len(replay_queries))
            for entry in payload["papers"]:
                self.assertNotIn("sources", entry)
                self.assertNotIn("diagnostics", entry)
                self.assertGreaterEqual(int(entry.get("source_count") or 0), 1)

            # Bounded-growth heuristic: compact index should stay well below 3KB per paper/query pair.
            size_bound = 3000 * (len(payload["papers"]) + len(payload["queries"]))
            self.assertLess(index_path.stat().st_size, size_bound)
            # Adapter should only be called on first-time DOI queries; replays should hit DB cache.
            self.assertEqual(adapter.lookup_doi_calls, len(doi_queries))


if __name__ == "__main__":
    unittest.main()
