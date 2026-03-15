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


def _mock_search_web_cycle1(**kwargs):
    return (
        [
            {
                "timestamp": "2026-01-01T00:00:00Z",
                "session_id": kwargs["session_id"],
                "cycle_index": kwargs["cycle_index"],
                "query": "memory disaggregation systems",
                "query_rank": 1,
                "source": "searxng",
                "source_id": "https://doi.org/10.1/example",
                "title": "Memory Disaggregation at Scale",
                "url": "https://doi.org/10.1/example",
                "snippet": "A systems paper.",
                "venue": "NSDI",
                "year": "2024",
                "doi": "10.1/example",
                "arxiv_id": "",
                "author_hint": "Jane Doe|John Smith",
            }
        ],
        [
            {
                "source": "searxng",
                "source_id": "https://doi.org/10.1/example",
                "title": "Memory Disaggregation at Scale",
                "venue": "NSDI",
                "year": "2024",
                "doi": "10.1/example",
                "arxiv_id": "",
                "url": "https://doi.org/10.1/example",
                "abstract": "A systems paper.",
                "keywords": [],
                "categories": [],
                "score": 1.1,
                "reason": "searxng_search",
                "query_used": "memory disaggregation systems",
                "_snippet": "A systems paper.",
                "_author_hint": "Jane Doe|John Smith",
            }
        ],
    )


def _mock_search_web_empty(**kwargs):
    return ([], [])


@unittest.skipUnless(HAS_PYYAML, "PyYAML is not installed in this environment")
class TestAgenticRetrievalI1(unittest.TestCase):
    def test_agentic_single_cycle_writes_all_contract_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "workspace"
            ws.mkdir(parents=True, exist_ok=True)
            with patch("src.utils.paths.WORKSPACE_ROOT", ws):
                run_step("demo", "init", theme="systems")
                with patch.dict("os.environ", {"SEARXNG_URL": "http://searxng:8080", "DS_API_KEY": "dummy"}, clear=False):
                    with patch(
                        "src.orchestrator.agentic._agent_next_action_llm",
                        return_value={
                            "action": "search_web",
                            "queries": ["memory disaggregation systems"],
                            "rationale": "seed",
                            "stop": True,
                            "stop_reason": "",
                        },
                    ):
                        with patch("src.orchestrator.agentic._search_web_queries", side_effect=_mock_search_web_cycle1):
                            result_path = run_step(
                                "demo",
                                "retrieve-agentic",
                                prompt="memory disaggregation",
                                top_n=2,
                            )

            self.assertTrue(result_path.exists())
            rdir = ws / "demo" / "artifacts" / "retrieval"

            request = yamlx.load(rdir / "agentic_request.yaml")
            session = yamlx.load(rdir / "agentic_session.yaml")
            result = yamlx.load(rdir / "agentic_result.yaml")
            questions = yamlx.load(rdir / "agentic_questions.yaml")
            llm_payloads = yamlx.load(rdir / "agentic_llm_payloads.yaml")

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
            self.assertEqual(result["final_candidates"], [])
            self.assertGreaterEqual(len(result.get("latest_url_shortlist") or []), 1)
            self.assertEqual(questions["pending"], [])
            self.assertEqual(len(llm_payloads["cycles"]), 1)

            cycles_path = rdir / "agentic_cycles.tsv"
            candidates_path = rdir / "agentic_candidates_latest.tsv"
            web_results_path = rdir / "agentic_web_results.tsv"
            actions_path = rdir / "agentic_actions.tsv"
            url_hits_path = rdir / "agentic_url_hits_latest.yaml"
            fetch_queue_path = rdir / "agentic_fetch_queue.yaml"
            self.assertTrue(cycles_path.exists())
            self.assertTrue(candidates_path.exists())
            self.assertTrue(web_results_path.exists())
            self.assertTrue(actions_path.exists())
            self.assertTrue(url_hits_path.exists())
            self.assertTrue(fetch_queue_path.exists())

            with cycles_path.open("r", encoding="utf-8") as f:
                cycle_rows = list(csv.DictReader(f, delimiter="\t"))
            self.assertEqual(len(cycle_rows), 1)
            self.assertEqual(cycle_rows[0]["decision"], "stop")
            self.assertEqual(cycle_rows[0]["state_path"], "plan>action>observe")
            self.assertEqual(cycle_rows[0]["workflow"], "searxng_meta_refine_v1")

            with candidates_path.open("r", encoding="utf-8") as f:
                candidate_rows = list(csv.DictReader(f, delimiter="\t"))
            self.assertGreaterEqual(len(candidate_rows), 1)
            self.assertEqual(candidate_rows[0]["rank"], "1")
            self.assertEqual(candidate_rows[0]["selected"], "1")
            with actions_path.open("r", encoding="utf-8") as f:
                action_rows = list(csv.DictReader(f, delimiter="\t"))
            self.assertEqual(len(action_rows), 1)
            self.assertEqual(action_rows[0]["action"], "search_web")
            self.assertEqual(action_rows[0]["status"], "ok")

    def test_agentic_empty_results_stops_with_no_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "workspace"
            ws.mkdir(parents=True, exist_ok=True)
            with patch("src.utils.paths.WORKSPACE_ROOT", ws):
                run_step("demo", "init", theme="systems")
                with patch.dict("os.environ", {"SEARXNG_URL": "http://searxng:8080", "DS_API_KEY": "dummy"}, clear=False):
                    with patch(
                        "src.orchestrator.agentic._agent_next_action_llm",
                        return_value={
                            "action": "search_web",
                            "queries": ["nonexistent topic"],
                            "rationale": "seed",
                            "stop": True,
                            "stop_reason": "",
                        },
                    ):
                        with patch("src.orchestrator.agentic._search_web_queries", side_effect=_mock_search_web_empty):
                            run_step(
                                "demo",
                                "retrieve-agentic",
                                prompt="nonexistent topic",
                                top_n=3,
                            )

            rdir = ws / "demo" / "artifacts" / "retrieval"
            result = yamlx.load(rdir / "agentic_result.yaml")
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["stop_reason"], "no_candidates")
            self.assertEqual(result["final_candidates"], [])
            self.assertEqual(result.get("latest_url_shortlist"), [])

            with (rdir / "agentic_cycles.tsv").open("r", encoding="utf-8") as f:
                cycle_rows = list(csv.DictReader(f, delimiter="\t"))
            self.assertEqual(len(cycle_rows), 1)
            self.assertEqual(cycle_rows[0]["decision_reason"], "no_candidates")

            with (rdir / "agentic_candidates_latest.tsv").open("r", encoding="utf-8") as f:
                candidate_rows = list(csv.DictReader(f, delimiter="\t"))
            self.assertEqual(candidate_rows, [])

    def test_agentic_unimplemented_action_stub_stops_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "workspace"
            ws.mkdir(parents=True, exist_ok=True)
            with patch("src.utils.paths.WORKSPACE_ROOT", ws):
                run_step("demo", "init", theme="systems")
                with patch.dict("os.environ", {"SEARXNG_URL": "http://searxng:8080", "DS_API_KEY": "dummy"}, clear=False):
                    with patch(
                        "src.orchestrator.agentic._agent_next_action_llm",
                        return_value={
                            "action": "ask_user",
                            "queries": [],
                            "rationale": "need tie-break",
                            "stop": False,
                            "stop_reason": "",
                        },
                    ):
                        run_step(
                            "demo",
                            "retrieve-agentic",
                            prompt="same-name authors at systems conference",
                            top_n=2,
                        )

            rdir = ws / "demo" / "artifacts" / "retrieval"
            result = yamlx.load(rdir / "agentic_result.yaml")
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["stop_reason"], "action_not_implemented:ask_user")

            with (rdir / "agentic_actions.tsv").open("r", encoding="utf-8") as f:
                action_rows = list(csv.DictReader(f, delimiter="\t"))
            self.assertEqual(len(action_rows), 1)
            self.assertEqual(action_rows[0]["action"], "ask_user")
            self.assertEqual(action_rows[0]["status"], "not_implemented")

    def test_agentic_missing_searxng_env_fails_with_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "workspace"
            ws.mkdir(parents=True, exist_ok=True)
            with patch("src.utils.paths.WORKSPACE_ROOT", ws):
                run_step("demo", "init", theme="systems")
                with patch.dict("os.environ", {"SEARXNG_URL": "", "DS_API_KEY": "dummy"}, clear=False):
                    run_step(
                        "demo",
                        "retrieve-agentic",
                        prompt="memory disaggregation",
                        top_n=2,
                    )

            rdir = ws / "demo" / "artifacts" / "retrieval"
            session = yamlx.load(rdir / "agentic_session.yaml")
            result = yamlx.load(rdir / "agentic_result.yaml")
            self.assertEqual(session["status"], "failed")
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["stop_reason"], "missing_env:SEARXNG_URL")

    def test_agentic_missing_ds_api_key_marks_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "workspace"
            ws.mkdir(parents=True, exist_ok=True)
            with patch("src.utils.paths.WORKSPACE_ROOT", ws):
                run_step("demo", "init", theme="systems")
                with patch.dict("os.environ", {"SEARXNG_URL": "http://searxng:8080", "DS_API_KEY": ""}, clear=False):
                    run_step(
                        "demo",
                        "retrieve-agentic",
                        prompt="memory disaggregation",
                        top_n=2,
                    )

            rdir = ws / "demo" / "artifacts" / "retrieval"
            session = yamlx.load(rdir / "agentic_session.yaml")
            result = yamlx.load(rdir / "agentic_result.yaml")
            self.assertEqual(session["status"], "failed")
            self.assertEqual(result["status"], "failed")
            self.assertIn("missing_api_key:DS_API_KEY", result["stop_reason"])


if __name__ == "__main__":
    unittest.main()
