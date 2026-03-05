from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.extraction.writer import write_extraction
from src.parsing.normalize import write_sections_yaml
from src.utils import yamlx
from src.workspace.manager import init_project


HAS_PYYAML = importlib.util.find_spec("yaml") is not None


@unittest.skipUnless(HAS_PYYAML, "PyYAML is not installed in this environment")
class TestYamlRoundTrip(unittest.TestCase):
    def test_project_yaml_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "workspace" / "roundtrip-project"
            project_dir.mkdir(parents=True, exist_ok=True)
            with patch("src.workspace.manager.project_dir", return_value=project_dir):
                init_project("roundtrip-project", theme="Reliable systems")

            project_yaml = project_dir / "project.yaml"
            loaded = yamlx.load(project_yaml)
            rewritten = project_dir / "project.rewrite.yaml"
            yamlx.dump_to_path(rewritten, loaded)
            reloaded = yamlx.load(rewritten)
            self.assertEqual(reloaded, loaded)

    def test_sections_yaml_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "sections.yaml"
            sections = [
                {"section_id": "s1", "title": "Intro", "text": "hello"},
                {"section_id": "s2", "title": "Method", "text": "world"},
            ]
            write_sections_yaml(out_path, "paper-1", sections)
            loaded = yamlx.load(out_path)

            rewritten = Path(tmp) / "sections.rewrite.yaml"
            yamlx.dump_to_path(rewritten, loaded)
            reloaded = yamlx.load(rewritten)
            self.assertEqual(reloaded, loaded)
            self.assertEqual(reloaded["artifact_type"], "sections")
            self.assertEqual(reloaded["paper_id"], "paper-1")

    def test_extraction_yaml_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "paper-1.yaml"
            data = {
                "claim": {"value": "throughput improves", "confidence": 0.9},
                "evidence": {"value": "table 2", "confidence": 0.7},
            }
            write_extraction(out_path, "paper-1", data)
            loaded = yamlx.load(out_path)

            rewritten = Path(tmp) / "paper-1.rewrite.yaml"
            yamlx.dump_to_path(rewritten, loaded)
            reloaded = yamlx.load(rewritten)
            self.assertEqual(reloaded, loaded)
            self.assertEqual(reloaded["artifact_type"], "extraction")
            self.assertEqual(reloaded["data"], data)


class TestYamlDependencyFallback(unittest.TestCase):
    def test_missing_pyyaml_error_message(self):
        with patch("src.utils.yamlx.find_spec", return_value=None):
            with self.assertRaises(yamlx.YamlDependencyError) as exc:
                yamlx.loads("a: 1")
        self.assertIn("PyYAML is required", str(exc.exception))
        self.assertIn("pip install -e .", str(exc.exception))


if __name__ == "__main__":
    unittest.main()
