from __future__ import annotations

import importlib.util
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from src.cli import _print_retrieve_agentic_progress
from src.orchestrator.runner import run_step

HAS_PYYAML = importlib.util.find_spec("yaml") is not None


@unittest.skipUnless(HAS_PYYAML, "PyYAML is not installed in this environment")
class TestCliFlow(unittest.TestCase):
    def test_print_retrieve_agentic_progress_extract_action_start_shows_target_urls(self):
        stream = io.StringIO()
        with redirect_stdout(stream):
            _print_retrieve_agentic_progress(
                {
                    "event": "agentic_action_start",
                    "cycle_index": 2,
                    "action_id": "c02",
                    "active_step_id": "extract_venue_papers",
                    "action": "extract_content",
                    "target_count": 3,
                    "target_urls": [
                        "https://conf.example/program",
                        "https://conf.example/papers-info",
                        "https://usenix.example/technical-sessions",
                    ],
                    "raw_event_id": "raw-15",
                }
            )
        output = stream.getvalue()
        self.assertIn("targets=3", output)
        self.assertIn("https://conf.example/program", output)
        self.assertIn("https://conf.example/papers-info", output)

    def test_print_retrieve_agentic_progress_agent_response_shows_queries(self):
        stream = io.StringIO()
        with redirect_stdout(stream):
            _print_retrieve_agentic_progress(
                {
                    "event": "agentic_llm_agent_response",
                    "cycle_index": 1,
                    "action_id": "c01",
                    "selected_action": "search_web",
                    "planned_queries": ["SIGCOMM 2025 accepted papers Alibaba", "NSDI 2025 accepted papers Alibaba"],
                    "raw_event_id": "raw-3",
                }
            )
        output = stream.getvalue()
        self.assertIn("selected_action=search_web", output)
        self.assertIn("SIGCOMM 2025 accepted papers Alibaba", output)

    def test_print_retrieve_agentic_progress_candidate_url_stage_is_readable(self):
        stream = io.StringIO()
        with redirect_stdout(stream):
            _print_retrieve_agentic_progress(
                {
                    "event": "agentic_extract_stage",
                    "cycle_index": 2,
                    "stage": "candidate_url_proposal",
                    "link_candidates": 80,
                }
            )
        output = stream.getvalue()
        self.assertIn("stage=candidate_url_proposal", output)
        self.assertIn("link_candidates=80", output)

    def test_print_retrieve_agentic_progress_extract_start_shows_mixed_venues(self):
        stream = io.StringIO()
        with redirect_stdout(stream):
            _print_retrieve_agentic_progress(
                {
                    "event": "agentic_extract_stage",
                    "cycle_index": 2,
                    "stage": "extract_start",
                    "target_count": 3,
                    "institutions": ["Alibaba"],
                    "venues": ["SIGCOMM", "NSDI"],
                    "filters": {"institution": "Alibaba", "venue": "SIGCOMM"},
                }
            )
        output = stream.getvalue()
        self.assertIn("institution=Alibaba", output)
        self.assertIn("venue=SIGCOMM,NSDI", output)

    def test_print_retrieve_agentic_progress_extract_batch_uses_segment_range(self):
        stream = io.StringIO()
        with redirect_stdout(stream):
            _print_retrieve_agentic_progress(
                {
                    "event": "agentic_extract_batch_start",
                    "cycle_index": 2,
                    "target_id": "fetch-1",
                    "pass_index": 3,
                    "batch_start": 8,
                    "batch_size": 4,
                    "segment_total": 39,
                }
            )
        output = stream.getvalue()
        self.assertIn("segments=9-12/39", output)

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
