from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.orchestrator.discovery import run_discovery_aggregation


class TestDiscoveryConnectors(unittest.TestCase):
    def test_searxng_participates_when_configured(self):
        pmeta = {
            "theme": "systems",
            "venues": [],
            "year_min": 2020,
            "year_max": 2025,
            "discovery": {
                "limit": 10,
                "connectors": {
                    "openalex": {"enabled": True},
                    "crossref": {"enabled": True},
                    "semantic_scholar": {"enabled": True},
                    "searxng": {"enabled": True, "base_url": "https://searx.local"},
                },
                "rate_limits": {},
            },
        }
        searx_rows = [
            {
                "source": "searxng",
                "source_id": "u1",
                "title": "Searx result",
                "venue": "",
                "year": "2024",
                "doi": "",
                "arxiv_id": "",
                "url": "https://example.org/p",
            }
        ]

        with tempfile.TemporaryDirectory() as tmp:
            pdir = Path(tmp)
            with patch("src.orchestrator.discovery.OpenAlexConnector.search", return_value=[]), patch(
                "src.orchestrator.discovery.CrossrefConnector.search", return_value=[]
            ), patch("src.orchestrator.discovery.SemanticScholarConnector.search", return_value=[]), patch(
                "src.orchestrator.discovery.SearxngConnector.search", return_value=searx_rows
            ):
                raw, dedup, _mapping = run_discovery_aggregation(pdir, pmeta)

        self.assertEqual(len(raw), 1)
        self.assertEqual(len(dedup), 1)
        self.assertEqual(raw[0]["source"], "searxng")


if __name__ == "__main__":
    unittest.main()
