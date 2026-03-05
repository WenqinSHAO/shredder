from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.orchestrator.runner import run_step

HAS_PYYAML = importlib.util.find_spec("yaml") is not None


@unittest.skipUnless(HAS_PYYAML, "PyYAML is not installed in this environment")
class TestCliFlow(unittest.TestCase):
    def test_end_to_end_flow(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace_root = Path(tmp) / "workspace"
            workspace_root.mkdir(parents=True, exist_ok=True)
            with patch("src.utils.paths.WORKSPACE_ROOT", workspace_root):
                run_step("demo", "init", theme="network systems")
                run_step("demo", "discovery")
                run_step("demo", "parsing", paper_id="sample", pdf_path="examples/sample.pdf")
                run_step("demo", "extraction", paper_id="sample")
                report, slides = run_step("demo", "render")

            self.assertTrue(report.exists())
            self.assertTrue(slides.exists())


if __name__ == "__main__":
    unittest.main()
