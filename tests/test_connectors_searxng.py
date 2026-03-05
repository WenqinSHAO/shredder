from __future__ import annotations

import unittest
from unittest.mock import patch

from src.connectors.base import ConnectorConfig
from src.connectors.searxng import SearxngConnector


class TestSearxngConnector(unittest.TestCase):
    def test_normalizes_results(self):
        payload = {
            "results": [
                {
                    "title": "Example Paper",
                    "url": "https://arxiv.org/abs/2401.12345",
                    "metadata": {
                        "doi": "https://doi.org/10.1000/xyz",
                        "journal": "NSDI",
                        "year": 2024,
                    },
                }
            ]
        }
        connector = SearxngConnector(ConnectorConfig(base_url="https://searx.local", timeout_s=2.0))
        with patch("src.connectors.searxng.get_json", return_value=payload):
            rows = connector.search("systems", ["NSDI"], 2020, 2025, 10)

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["source"], "searxng")
        self.assertEqual(row["doi"], "10.1000/xyz")
        self.assertEqual(row["arxiv_id"], "2401.12345")
        self.assertEqual(row["venue"], "NSDI")


if __name__ == "__main__":
    unittest.main()
