from __future__ import annotations

import csv
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.orchestrator.runner import run_step
from src.utils import yamlx

HAS_PYYAML = importlib.util.find_spec("yaml") is not None


class _AgenticAdapter:
    def search_open(self, query: str, limit: int = 20) -> list[dict]:
        return [
            {
                "source": "pyalex",
                "source_id": "w1",
                "title": "Memory Disaggregation at Scale",
                "venue": "NSDI",
                "year": "2024",
                "doi": "10.1/example",
                "arxiv_id": "",
                "url": "https://doi.org/10.1/example",
                "authors": [],
                "score": 0.92,
                "reason": "search_open",
            },
            {
                "source": "semanticscholar",
                "source_id": "p2",
                "title": "Composable Memory Fabrics",
                "venue": "OSDI",
                "year": "2023",
                "doi": "",
                "arxiv_id": "2401.12345",
                "url": "https://arxiv.org/abs/2401.12345",
                "authors": [],
                "score": 0.84,
                "reason": "search_open",
            },
        ]


class _EmptyAgenticAdapter:
    def search_open(self, query: str, limit: int = 20) -> list[dict]:
        return []


@unittest.skipUnless(HAS_PYYAML, "PyYAML is not installed in this environment")
class TestAgenticRetrievalI1(unittest.TestCase):
    def test_agentic_single_cycle_writes_all_contract_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "workspace"
            ws.mkdir(parents=True, exist_ok=True)
            with patch("src.utils.paths.WORKSPACE_ROOT", ws):
                run_step("demo", "init", theme="systems")
                with patch("src.orchestrator.agentic.build_adapters", return_value=[_AgenticAdapter()]):
                    result_path = run_step(
                        "demo",
                        "retrieve-agentic",
                        prompt="memory disaggregation",
                        workflow="theme_refine",
                        top_n=2,
                        max_cycles=1,
                    )

            self.assertTrue(result_path.exists())
            rdir = ws / "demo" / "artifacts" / "retrieval"

            request = yamlx.load(rdir / "agentic_request.yaml")
            session = yamlx.load(rdir / "agentic_session.yaml")
            result = yamlx.load(rdir / "agentic_result.yaml")
            questions = yamlx.load(rdir / "agentic_questions.yaml")

            self.assertEqual(request["artifact_type"], "agentic_request")
            self.assertEqual(session["artifact_type"], "agentic_session")
            self.assertEqual(result["artifact_type"], "agentic_result")
            self.assertEqual(questions["artifact_type"], "agentic_questions")
            self.assertEqual(request["schema_version"], "0.1.0")
            self.assertEqual(session["schema_version"], "0.1.0")
            self.assertEqual(result["schema_version"], "0.1.0")
            self.assertEqual(questions["schema_version"], "0.1.0")
            self.assertEqual(session["status"], "completed")
            self.assertEqual(session["state"], "completed")
            self.assertEqual(int(session["current_cycle"]), 1)
            self.assertEqual(result["status"], "completed")
            self.assertGreaterEqual(len(result["final_candidates"]), 1)
            self.assertEqual(questions["pending"], [])

            cycles_path = rdir / "agentic_cycles.tsv"
            candidates_path = rdir / "agentic_candidates_latest.tsv"
            self.assertTrue(cycles_path.exists())
            self.assertTrue(candidates_path.exists())

            with cycles_path.open("r", encoding="utf-8") as f:
                cycle_rows = list(csv.DictReader(f, delimiter="\t"))
            self.assertEqual(len(cycle_rows), 1)
            self.assertEqual(cycle_rows[0]["decision"], "stop")
            self.assertEqual(cycle_rows[0]["state_path"], "plan>retrieve>rank>decide")
            self.assertEqual(cycle_rows[0]["workflow"], "theme_refine")

            with candidates_path.open("r", encoding="utf-8") as f:
                candidate_rows = list(csv.DictReader(f, delimiter="\t"))
            self.assertGreaterEqual(len(candidate_rows), 1)
            self.assertEqual(candidate_rows[0]["rank"], "1")
            self.assertEqual(candidate_rows[0]["selected"], "1")

    def test_agentic_empty_results_stops_with_no_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "workspace"
            ws.mkdir(parents=True, exist_ok=True)
            with patch("src.utils.paths.WORKSPACE_ROOT", ws):
                run_step("demo", "init", theme="systems")
                with patch("src.orchestrator.agentic.build_adapters", return_value=[_EmptyAgenticAdapter()]):
                    run_step(
                        "demo",
                        "retrieve-agentic",
                        prompt="nonexistent topic",
                        workflow="theme_refine",
                        top_n=3,
                        max_cycles=1,
                    )

            rdir = ws / "demo" / "artifacts" / "retrieval"
            result = yamlx.load(rdir / "agentic_result.yaml")
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["stop_reason"], "no_candidates")
            self.assertEqual(result["final_candidates"], [])

            with (rdir / "agentic_cycles.tsv").open("r", encoding="utf-8") as f:
                cycle_rows = list(csv.DictReader(f, delimiter="\t"))
            self.assertEqual(len(cycle_rows), 1)
            self.assertEqual(cycle_rows[0]["decision_reason"], "no_candidates")

            with (rdir / "agentic_candidates_latest.tsv").open("r", encoding="utf-8") as f:
                candidate_rows = list(csv.DictReader(f, delimiter="\t"))
            self.assertEqual(candidate_rows, [])


if __name__ == "__main__":
    unittest.main()
