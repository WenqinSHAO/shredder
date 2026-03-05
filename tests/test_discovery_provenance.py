from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.orchestrator.steps import run_discovery
from src.workspace.manager import init_project


class TestDiscoveryProvenance(unittest.TestCase):
    def test_provenance_entity_ids_exist_in_papers(self):
        raw_rows = [
            {
                "source": "openalex",
                "source_id": "w1",
                "paper_id": "title:cache systems:2024",
                "title": "Cache Systems",
                "venue": "NSDI",
                "year": "2024",
                "doi": "",
                "arxiv_id": "",
                "url": "",
            },
            {
                "source": "crossref",
                "source_id": "w2",
                "paper_id": "doi:10.1/xyz",
                "title": "Cache Systems",
                "venue": "NSDI",
                "year": "2024",
                "doi": "10.1/xyz",
                "arxiv_id": "",
                "url": "",
            },
        ]
        dedup_rows = [
            {
                "source": "openalex",
                "source_id": "w1",
                "paper_id": "doi:10.1/xyz",
                "title": "Cache Systems",
                "venue": "NSDI",
                "year": "2024",
                "doi": "10.1/xyz",
                "arxiv_id": "",
                "url": "",
            }
        ]
        mapping = {"openalex:w1": "doi:10.1/xyz", "crossref:w2": "doi:10.1/xyz"}

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ws = root / "workspace"
            ws.mkdir()
            kb_dir = root / "kb"
            kb_dir.mkdir()
            kb_path = kb_dir / "kb.sqlite"

            with patch("src.utils.paths.WORKSPACE_ROOT", ws), patch("src.kb.store.KB_DIR", kb_dir), patch(
                "src.kb.store.KB_PATH", kb_path
            ):
                init_project("demo", theme="systems")
                with patch(
                    "src.orchestrator.steps.run_discovery_aggregation", return_value=(raw_rows, dedup_rows, mapping)
                ):
                    run_discovery("demo")

            conn = sqlite3.connect(kb_path)
            try:
                paper_ids = {r[0] for r in conn.execute("SELECT id FROM papers").fetchall()}
                provenance_ids = {r[0] for r in conn.execute("SELECT entity_id FROM provenance").fetchall()}
            finally:
                conn.close()

        self.assertTrue(provenance_ids)
        self.assertTrue(provenance_ids.issubset(paper_ids))


if __name__ == "__main__":
    unittest.main()
