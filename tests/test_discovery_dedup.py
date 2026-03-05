from __future__ import annotations

import unittest

from src.orchestrator.discovery import deduplicate_candidates


class TestDiscoveryDedup(unittest.TestCase):
    def test_dedup_prefers_doi(self):
        rows = [
            {"source": "a", "source_id": "1", "title": "Paper A", "venue": "NSDI", "year": "2024", "doi": "10.1/x", "arxiv_id": "", "url": ""},
            {"source": "b", "source_id": "2", "title": "Paper A Extended", "venue": "NSDI", "year": "2024", "doi": "10.1/x", "arxiv_id": "", "url": ""},
        ]
        dedup = deduplicate_candidates(rows)
        self.assertEqual(len(dedup), 1)
        self.assertEqual(dedup[0]["paper_id"], "doi:10.1/x")

    def test_dedup_arxiv_when_no_doi(self):
        rows = [
            {"source": "a", "source_id": "1", "title": "Paper B", "venue": "", "year": "2023", "doi": "", "arxiv_id": "2401.12345", "url": ""},
            {"source": "b", "source_id": "2", "title": "Paper B preprint", "venue": "", "year": "2023", "doi": "", "arxiv_id": "2401.12345", "url": ""},
        ]
        dedup = deduplicate_candidates(rows)
        self.assertEqual(len(dedup), 1)
        self.assertEqual(dedup[0]["paper_id"], "arxiv:2401.12345")

    def test_dedup_fuzzy_title_and_year(self):
        rows = [
            {"source": "a", "source_id": "1", "title": "Efficient Caching for LLM Systems", "venue": "", "year": "2022", "doi": "", "arxiv_id": "", "url": ""},
            {"source": "b", "source_id": "2", "title": "Efficient caching for LLM systems.", "venue": "", "year": "2022", "doi": "", "arxiv_id": "", "url": ""},
        ]
        dedup = deduplicate_candidates(rows)
        self.assertEqual(len(dedup), 1)
        self.assertTrue(dedup[0]["paper_id"].startswith("title:"))


if __name__ == "__main__":
    unittest.main()
