from __future__ import annotations

import unittest

from src.orchestrator.discovery import deduplicate_candidates


class TestDedupOrdering(unittest.TestCase):
    def test_order_invariant_output(self):
        rows = [
            {"source": "a", "source_id": "1", "title": "A Study on Systems", "venue": "", "year": "2024", "doi": "", "arxiv_id": "", "url": ""},
            {"source": "b", "source_id": "2", "title": "A study on systems.", "venue": "", "year": "2024", "doi": "", "arxiv_id": "", "url": ""},
            {"source": "c", "source_id": "3", "title": "Different", "venue": "", "year": "2023", "doi": "", "arxiv_id": "2401.1", "url": ""},
        ]
        d1, _ = deduplicate_candidates(rows)
        d2, _ = deduplicate_candidates(list(reversed(rows)))
        self.assertEqual(d1, d2)

    def test_doi_late_arrival_merges_to_single_canonical(self):
        rows = [
            {"source": "a", "source_id": "1", "title": "Cache Systems", "venue": "", "year": "2024", "doi": "", "arxiv_id": "", "url": ""},
            {"source": "b", "source_id": "2", "title": "Cache Systems", "venue": "", "year": "2024", "doi": "10.1/abc", "arxiv_id": "", "url": ""},
        ]
        dedup, _ = deduplicate_candidates(rows)
        self.assertEqual(len(dedup), 1)
        self.assertEqual(dedup[0]["paper_id"], "doi:10.1/abc")


if __name__ == "__main__":
    unittest.main()
