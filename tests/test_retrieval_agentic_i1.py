from __future__ import annotations

import csv
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.orchestrator import agentic as agentic_mod
from src.orchestrator import agentic_extract as extract_mod
from src.orchestrator import agentic_state_apply as state_apply_mod
from src.orchestrator import agentic_trace as trace_mod
from src.orchestrator.runner import run_step
from src.utils import yamlx

HAS_PYYAML = importlib.util.find_spec("yaml") is not None


def _agent_reply(
    *,
    action: str,
    params: dict | None = None,
    queries: list[str] | None = None,
    stop: bool = False,
    reason: str = "",
    state_delta: dict | None = None,
    progress: dict | None = None,
) -> dict:
    return {
        "action": str(action or ""),
        "queries": list(queries or []),
        "params": dict(params or {}),
        "decision": {
            "mode": "stop" if stop else "continue",
            "reason": str(reason or ""),
        },
        "state_delta": dict(state_delta or {}),
        "progress": dict(progress or {}),
    }


def _read_raw_events(path: Path) -> list[dict]:
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text:
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


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
    def test_extract_json_object_accepts_fenced_json(self):
        payload = agentic_mod._extract_json_object(
            """```json
            {"action": "search_web", "params": {"queries": ["nsdi 2025 accepted papers"]}}
            ```"""
        )
        self.assertEqual(payload["action"], "search_web")
        self.assertEqual(payload["params"]["queries"], ["nsdi 2025 accepted papers"])

    def test_estimate_messages_metrics_coerces_list_content(self):
        metrics = agentic_mod._estimate_messages_metrics(
            [
                {
                    "role": "user",
                    "content": [
                        {"text": "hello"},
                        {"content": "world"},
                    ],
                }
            ]
        )
        self.assertEqual(metrics["input_chars"], len("user") + len("hello\nworld"))
        self.assertGreater(metrics["input_tokens_est"], 0)

    def test_resolve_openai_model_and_base_url_supports_provider_prefix(self):
        with patch.dict(
            "os.environ",
            {
                "DEEPSEEK_BASE_URL": "https://deepseek.example/v1",
                "OPENAI_BASE_URL": "https://openai.example/v1",
            },
            clear=False,
        ):
            deepseek_model, deepseek_url = agentic_mod._resolve_openai_model_and_base_url(
                model="deepseek/deepseek-chat",
                api_key_env="DS_API_KEY",
            )
            openai_model, openai_url = agentic_mod._resolve_openai_model_and_base_url(
                model="openai/gpt-4o-mini",
                api_key_env="OPENAI_API_KEY",
            )
        self.assertEqual(deepseek_model, "deepseek-chat")
        self.assertEqual(deepseek_url, "https://deepseek.example/v1")
        self.assertEqual(openai_model, "gpt-4o-mini")
        self.assertEqual(openai_url, "https://openai.example/v1")

    def test_generic_windows_robust_to_order_and_boilerplate_mutation(self):
        core = (
            "SimAI: Unifying Architecture Design and Performance Tuning for Large-Scale LLM Training. "
            "Authors from Alibaba Cloud and Alibaba Group. NSDI 2025. "
            "Learning Production-Optimized Congestion Control Selection for Alibaba Cloud CDN. "
            "Alibaba Cloud authors. 2025."
        )
        noisy_prefix = "Opening Remarks. Registration Desk. Welcome note. " * 12
        noisy_suffix = "Coffee Break. Sponsor Session. Logistics update. " * 10
        text_a = f"{noisy_prefix} {core} {noisy_suffix}"
        text_b = f"{noisy_suffix} {core} {noisy_prefix}"
        windows_a = agentic_mod._build_extraction_windows(
            text_a,
            {"institution": "Alibaba", "year_gte": 2025, "topic": "papers by Alibaba at NSDI in 2025"},
            max_windows=8,
            radius=2,
            max_chars=1500,
        )
        windows_b = agentic_mod._build_extraction_windows(
            text_b,
            {"institution": "Alibaba", "year_gte": 2025, "topic": "papers by Alibaba at NSDI in 2025"},
            max_windows=8,
            radius=2,
            max_chars=1500,
        )
        joined_a = " ".join(windows_a).lower()
        joined_b = " ".join(windows_b).lower()
        self.assertTrue("simai" in joined_a or "congestion control" in joined_a)
        self.assertTrue("simai" in joined_b or "congestion control" in joined_b)

    def test_generic_windows_across_source_style_fixtures(self):
        root = Path("tests/fixtures/extract_generic")
        files = sorted(root.glob("*.html"))
        self.assertGreaterEqual(len(files), 4)
        for fixture in files:
            raw_html = fixture.read_text(encoding="utf-8", errors="ignore")
            text = agentic_mod._extract_listing_text_with_fallback(raw_html, max_chars=500000)
            windows = agentic_mod._build_extraction_windows(
                text,
                {"institution": "Alibaba", "year_gte": 2025, "topic": "papers by Alibaba in 2025"},
                max_windows=8,
                radius=2,
                max_chars=1500,
            )
            joined = " ".join(windows).lower()
            self.assertIn("alibaba", joined, msg=f"fixture {fixture.name} missing Alibaba in windows")
            self.assertTrue(
                any(token in joined for token in ("paper", "papers", "simai", "albatross", "hermes", "resccl")),
                msg=f"fixture {fixture.name} lacks paper-like signal in windows",
            )

    def test_fixture_nsdi_html_windows_include_paper_signals(self):
        fixture = Path("tests/fixtures/agentic_fetch_raw/cycle03-auto-fetch-2-technical-sessions.html")
        self.assertTrue(fixture.exists())
        raw_html = fixture.read_text(encoding="utf-8", errors="ignore")
        text = agentic_mod._extract_listing_text_with_fallback(raw_html, max_chars=800000)
        windows = agentic_mod._build_extraction_windows(
            text,
            {"institution": "Alibaba", "year_gte": 2025, "topic": "papers by Alibaba at NSDI in 2025"},
            max_windows=8,
            radius=2,
            max_chars=1500,
        )
        joined = " ".join(windows).lower()
        self.assertTrue("simai" in joined or "aliccs" in joined or "alibaba cloud" in joined)

    def test_structural_segments_capture_many_venue_entries(self):
        fixture = Path("tests/fixtures/agentic_fetch_raw/cycle03-auto-fetch-1-page.html")
        self.assertTrue(fixture.exists())
        raw_html = fixture.read_text(encoding="utf-8", errors="ignore")
        segments = agentic_mod._extract_html_structural_segments(raw_html, max_segments=240, max_chars=1800)
        joined = " ".join(segments).lower()
        self.assertGreaterEqual(len(segments), 20)
        self.assertIn("hermes", joined)
        self.assertIn("alibaba stellar", joined)

    def test_extract_text_segments_preserves_bulleted_entries(self):
        text = """
        ACM SIGCOMM 2025 Accepted Papers

        - ParserHawk: Hardware-aware parser generator using program synthesis
          Xiangyu Gao; Bili Dong (Google)

        - Falcon: A Reliable, Low Latency Hardware Transport
          Arjun Singhvi (Google)
        """
        segments = agentic_mod._extract_text_segments(text, max_chars=240)
        self.assertGreaterEqual(len(segments), 2)
        self.assertTrue(any("ParserHawk" in seg for seg in segments))
        self.assertTrue(any("Falcon" in seg for seg in segments))

    def test_clean_block_text_preserves_line_breaks(self):
        raw = "<ul><li>ParserHawk: Hardware-aware parser generator using program synthesis</li><li>Falcon: A Reliable, Low Latency Hardware Transport</li></ul>"
        cleaned = agentic_mod._clean_block_text(raw, limit_chars=1000)
        self.assertIn("\n", cleaned)
        self.assertIn("ParserHawk", cleaned)
        self.assertIn("Falcon", cleaned)

    def test_anchor_segments_with_filters_keeps_target_bearing_context(self):
        fixture = Path("tests/fixtures/agentic_fetch_raw/cycle03-auto-fetch-1-page.html")
        self.assertTrue(fixture.exists())
        raw_html = fixture.read_text(encoding="utf-8", errors="ignore")
        text = agentic_mod._extract_listing_text_with_fallback(raw_html, max_chars=500000)
        segments = agentic_mod._extract_text_segments(text, max_chars=1800)
        selected = agentic_mod._anchor_segments_for_filters(
            segments,
            {"institution": "Google", "year_gte": 2025},
            anchor_terms=["Google", "ParserHawk", "Falcon", "Firefly"],
            radius=1,
        )
        self.assertGreaterEqual(len(segments), 40)
        self.assertTrue(any("Google" in seg for seg in selected))
        self.assertTrue(any("ParserHawk" in seg or "Falcon" in seg or "Firefly" in seg for seg in selected))

    def test_extract_text_segments_does_not_truncate_late_otter_entry(self):
        fixture = Path("tests/fixtures/agentic_fetch_raw/cycle03-auto-fetch-2-technical-sessions.html")
        self.assertTrue(fixture.exists())
        raw_html = fixture.read_text(encoding="utf-8", errors="ignore")
        text = agentic_mod._extract_listing_text_with_fallback(raw_html, max_chars=800000)
        segments = agentic_mod._extract_text_segments(text, max_chars=1800)
        self.assertTrue(any("Efficient Multi-WAN Transport for 5G with OTTER" in seg for seg in segments))

    def test_llm_extract_filters_listing_heading_and_acronym_only(self):
        record = {
            "session_id": "s1",
            "cycle_index": 1,
            "target_id": "t1",
            "url": "https://conferences.sigcomm.org/sigcomm/2025/accepted-papers/",
            "url_title": "ACM SIGCOMM 2025 List of Accepted Papers",
            "text": "dummy",
        }
        with patch(
            "src.orchestrator.agentic._openai_complete_json",
            return_value={
                "items": [
                    {
                        "is_paper": True,
                        "paper_title": "ACM SIGCOMM 2025 List of Accepted Papers",
                        "year": "2025",
                        "confidence": "high",
                        "evidence": "ACM SIGCOMM 2025 List of Accepted Papers",
                    },
                    {
                        "is_paper": True,
                        "paper_title": "AliCCS",
                        "year": "2025",
                        "confidence": "high",
                        "evidence": "AliCCS appears in the text.",
                    },
                    {
                        "is_paper": True,
                        "paper_title": "Hermes: Enhancing Layer-7 Cloud Load Balancers with Userspace-Directed I/O Event Notification",
                        "year": "2025",
                        "confidence": "high",
                        "evidence": "Hermes: Enhancing Layer-7 Cloud Load Balancers with Userspace-Directed I/O Event Notification ... Alibaba Cloud",
                    },
                ]
            },
        ):
            facts, _trace = agentic_mod._extract_facts_with_llm(
                record=record,
                filters={"institution": "Alibaba", "year_gte": 2025},
                user_prompt="papers by Alibaba at SIGCOMM in 2025",
                model="dummy",
                api_key_env="DS_API_KEY",
                intent={"query_goal": "papers by Alibaba at SIGCOMM in 2025", "must_match": {"institution_any": ["Alibaba"], "year_gte": 2025}},
                segments=[
                    "ACM SIGCOMM 2025 List of Accepted Papers",
                    "AliCCS appears in the text.",
                    "Hermes: Enhancing Layer-7 Cloud Load Balancers with Userspace-Directed I/O Event Notification ... Alibaba Cloud",
                ],
            )
        titles = [str(row.get("paper_title") or "") for row in facts]
        self.assertEqual(len(titles), 1)
        self.assertIn("Hermes:", titles[0])

    def test_llm_extract_confidence_text_labels_are_parsed(self):
        record = {
            "session_id": "s1",
            "cycle_index": 1,
            "target_id": "t1",
            "url": "https://www.usenix.org/conference/nsdi25/technical-sessions",
            "url_title": "NSDI",
            "text": "SimAI paper by Alibaba Cloud. Learning Production-Optimized Congestion Control Selection for Alibaba Cloud CDN.",
        }
        with patch(
            "src.orchestrator.agentic._openai_complete_json",
            return_value={
                "items": [
                    {
                        "is_paper": True,
                        "paper_title": "SimAI: Unifying Architecture Design and Performance Tuning for Large-Scale Large Language Model Training with Scalability and Precision",
                        "year": "2025",
                        "doi": "",
                        "arxiv_id": "",
                        "institution_match": "Alibaba Cloud",
                        "venue_hint": "NSDI",
                        "evidence": "SimAI ... Alibaba Cloud ... NSDI 2025",
                        "confidence": "high",
                    },
                    {
                        "is_paper": True,
                        "paper_title": "Learning Production-Optimized Congestion Control Selection for Alibaba Cloud CDN",
                        "year": "2025",
                        "doi": "",
                        "arxiv_id": "",
                        "institution_match": "Alibaba Cloud",
                        "venue_hint": "NSDI",
                        "evidence": "AliCCS ... Alibaba Cloud CDN ... NSDI 2025",
                        "confidence": "medium",
                    },
                ]
            },
        ):
            facts, trace = agentic_mod._extract_facts_with_llm(
                record=record,
                filters={"institution": "Alibaba", "year_gte": 2025},
                user_prompt="papers by Alibaba at NSDI in 2025",
                model="dummy",
                api_key_env="DS_API_KEY",
                intent={"query_goal": "papers by Alibaba at NSDI in 2025", "must_match": {"institution_any": ["Alibaba"], "year_gte": 2025}},
            )
        self.assertEqual(len(facts), 2)
        scores = [float(row.get("score") or 0.0) for row in facts]
        self.assertTrue(max(scores) >= 0.9)
        self.assertTrue(min(scores) >= 0.6)
        self.assertGreaterEqual(int(trace.get("response_items_count") or 0), 2)

    def test_llm_extract_marks_row_completeness_without_dropping_strong_title(self):
        record = {
            "session_id": "s1",
            "cycle_index": 1,
            "target_id": "t1",
            "url": "https://example.org/venue",
            "url_title": "Venue Program",
            "text": "Falcon: A Reliable, Low Latency Hardware Transport",
        }
        with patch(
            "src.orchestrator.agentic._openai_complete_json",
            return_value={
                "items": [
                    {
                        "is_paper": True,
                        "paper_title_raw": "Falcon: A Reliable, Low Latency Hardware Transport",
                        "paper_title_normalized": "Falcon: A Reliable, Low Latency Hardware Transport",
                        "authors": [],
                        "affiliations": [],
                        "year": "2025",
                        "confidence": 0.92,
                        "match_decision": "match",
                        "evidence_span": "Falcon: A Reliable, Low Latency Hardware Transport",
                    }
                ]
            },
        ):
            facts, _trace = agentic_mod._extract_facts_with_llm(
                record=record,
                filters={"institution": "Google", "year_gte": 2025},
                user_prompt="papers by google at SIGCOMM and NSDI in 2025",
                model="dummy",
                api_key_env="DS_API_KEY",
                intent={"query_goal": "papers by google", "must_match": {"institution_any": ["Google"], "year_gte": 2025}},
                segments=["Falcon: A Reliable, Low Latency Hardware Transport"],
            )
        self.assertEqual(len(facts), 1)
        self.assertEqual(str(facts[0].get("row_completeness") or ""), "partial")
        self.assertIn("authors", list(facts[0].get("missing_fields") or []))
        self.assertIn("affiliations", list(facts[0].get("missing_fields") or []))

    def test_llm_extract_keeps_match_when_evidence_span_only_has_affiliation_hit(self):
        record = {
            "session_id": "s1",
            "cycle_index": 1,
            "target_id": "t1",
            "url": "https://conferences.sigcomm.org/sigcomm/2025/accepted-papers/",
            "url_title": "SIGCOMM 2025 accepted papers",
            "text": "ParserHawk listing text",
        }
        with patch(
            "src.orchestrator.agentic._openai_complete_json",
            return_value={
                "items": [
                    {
                        "is_paper": True,
                        "paper_title_raw": "ParserHawk: Hardware-aware parser generator using program synthesis",
                        "paper_title_normalized": "ParserHawk: Hardware-aware parser generator using program synthesis",
                        "authors": ["Jiaqi Gao", "Bili Dong"],
                        "affiliations": ["Alibaba Cloud", "Google"],
                        "year": "2025",
                        "match_decision": "match",
                        "decision_reason": "Bili Dong is affiliated with Google",
                        "evidence_span": "Bili Dong (Google)",
                        "confidence": 0.85,
                    }
                ]
            },
        ):
            facts, _trace = agentic_mod._extract_facts_with_llm(
                record=record,
                filters={"institution": "Google", "year_gte": 2025},
                user_prompt="papers by google at SIGCOMM in 2025",
                model="dummy",
                api_key_env="DS_API_KEY",
                intent={"query_goal": "papers by google", "must_match": {"institution_any": ["Google"], "year_gte": 2025}},
                segments=["ParserHawk listing text"],
            )
        self.assertEqual(len(facts), 1)
        self.assertIn("ParserHawk", str(facts[0].get("paper_title") or ""))

    def test_llm_extract_keeps_program_synthesis_titles(self):
        record = {
            "session_id": "s1",
            "cycle_index": 1,
            "target_id": "t1",
            "url": "https://conferences.sigcomm.org/sigcomm/2025/accepted-papers/",
            "url_title": "SIGCOMM 2025 accepted papers",
            "text": "ParserHawk: Hardware-aware parser generator using program synthesis ... Alibaba Cloud.",
        }
        with patch(
            "src.orchestrator.agentic._openai_complete_json",
            return_value={
                "items": [
                    {
                        "is_paper": True,
                        "paper_title": "ParserHawk: Hardware-aware parser generator using program synthesis",
                        "year": "2025",
                        "confidence": "high",
                        "evidence": "ParserHawk ... program synthesis ... Alibaba Cloud",
                    }
                ]
            },
        ):
            facts, _trace = agentic_mod._extract_facts_with_llm(
                record=record,
                filters={"institution": "Alibaba", "year_gte": 2025},
                user_prompt="papers by Alibaba at SIGCOMM in 2025",
                model="dummy",
                api_key_env="DS_API_KEY",
                intent={"query_goal": "papers by Alibaba at SIGCOMM in 2025", "must_match": {"institution_any": ["Alibaba"], "year_gte": 2025}},
                segments=["ParserHawk ... program synthesis ... Alibaba Cloud"],
            )
        self.assertEqual(len(facts), 1)
        self.assertIn("ParserHawk", str(facts[0].get("paper_title") or ""))

    def test_resolve_extract_intent_infers_year_from_prompt(self):
        intent = agentic_mod._resolve_extract_intent(
            params={},
            filters={"institution": "Google"},
            user_prompt="papers by google at SIGCOMM and NSDI in 2025",
        )
        must = intent.get("must_match") if isinstance(intent.get("must_match"), dict) else {}
        self.assertEqual(must.get("year_gte"), 2025)
        self.assertIn("Google", must.get("institution_any") or [])

    def test_resolve_extract_intent_treats_org_by_subject_as_institution(self):
        intent = agentic_mod._resolve_extract_intent(
            params={},
            filters={},
            user_prompt="papers by Google at SIGCOMM in 2025",
        )
        must = intent.get("must_match") if isinstance(intent.get("must_match"), dict) else {}
        self.assertIn("Google", must.get("institution_any") or [])
        self.assertEqual(must.get("author_any") or [], [])

    def test_resolve_extract_intent_treats_person_by_subject_as_author(self):
        intent = agentic_mod._resolve_extract_intent(
            params={},
            filters={},
            user_prompt="papers by Alice Smith at SIGCOMM in 2025",
        )
        must = intent.get("must_match") if isinstance(intent.get("must_match"), dict) else {}
        self.assertIn("Alice Smith", must.get("author_any") or [])
        self.assertEqual(must.get("institution_any") or [], [])

    def test_extract_candidates_require_match_decision_when_present(self):
        facts = [
            {
                "status": "ok",
                "paper_title": "Falcon: A Reliable, Low Latency Hardware Transport",
                "year": "2025",
                "doi": "",
                "arxiv_id": "",
                "url": "https://conferences.sigcomm.org/sigcomm/2025/accepted-papers/",
                "url_title": "SIGCOMM 2025 accepted papers",
                "evidence": "Falcon: A Reliable, Low Latency Hardware Transport ... Google LLC",
                "score": 0.9,
                "filters": {"institution": "Google", "year_gte": 2025},
                "extract_source": "llm",
                "llm_extract": {"match_decision": "match", "institution_match": True},
            },
            {
                "status": "ok",
                "paper_title": "Albatross: A Containerized Cloud Gateway Platform with FPGA-accelerated Packet-level Load Balancing",
                "year": "2025",
                "doi": "",
                "arxiv_id": "",
                "url": "https://conferences.sigcomm.org/sigcomm/2025/accepted-papers/",
                "url_title": "SIGCOMM 2025 accepted papers",
                "evidence": "Albatross ... Alibaba Cloud",
                "score": 0.9,
                "filters": {"institution": "Google", "year_gte": 2025},
                "extract_source": "llm",
                "llm_extract": {"match_decision": "non_match", "institution_match": False},
            },
        ]
        candidates = agentic_mod._to_paper_candidates_from_facts(facts)
        titles = [str(row.get("title") or "") for row in candidates]
        self.assertIn("Falcon: A Reliable, Low Latency Hardware Transport", titles)
        self.assertNotIn(
            "Albatross: A Containerized Cloud Gateway Platform with FPGA-accelerated Packet-level Load Balancing",
            titles,
        )

    def test_extract_match_decision_can_pass_without_venue_token_match(self):
        facts = [
            {
                "status": "ok",
                "paper_title": "NDD: A Decision Diagram for Network Verification",
                "year": "2025",
                "doi": "",
                "arxiv_id": "",
                "url": "https://www.usenix.org/conference/nsdi25/technical-sessions",
                "url_title": "NSDI '25 Technical Sessions",
                "evidence": "NDD: A Decision Diagram for Network Verification ... Hongkun Yang (Google)",
                "score": 0.9,
                "filters": {"institution": "Google", "year_gte": 2025, "venue": "SIGCOMM"},
                "extract_source": "llm",
                "extract_intent": {"must_match": {"venue_any": ["SIGCOMM", "NSDI"], "year_gte": 2025}},
                "llm_extract": {"match_decision": "match", "institution_match": True},
            }
        ]
        candidates = agentic_mod._to_paper_candidates_from_facts(facts)
        self.assertEqual(len(candidates), 1)
        self.assertIn("NDD", str(candidates[0].get("title") or ""))

    def test_slice_segments_by_token_budget(self):
        segments = ["a" * 8000, "b" * 8000, "c" * 8000]
        batch = agentic_mod._slice_segments_by_token_budget(
            segments,
            start=0,
            max_segments=10,
            token_budget=5000,
            min_segments=1,
        )
        self.assertGreaterEqual(len(batch), 2)
        self.assertLessEqual(sum(agentic_mod._estimate_text_tokens(x) for x in batch), 5200)

    def test_extract_segment_token_budget_leaves_headroom(self):
        budget = agentic_mod._extract_segment_token_budget(
            record={"url": "https://example.org/listing", "url_title": "Listing"},
            filters={"institution": "Google", "year_gte": 2025},
            user_prompt="papers by google at SIGCOMM in 2025",
            intent={"query_goal": "papers by google", "must_match": {"institution_any": ["Google"], "year_gte": 2025}},
            context_limit_tokens=128000,
            safety_margin=0.18,
            output_token_reserve=6000,
        )
        self.assertGreaterEqual(budget, 4000)
        self.assertLess(budget, 128000)

    def test_merge_url_hits_preserves_cross_cycle_shortlist(self):
        merged = agentic_mod._merge_url_hits(
            [
                {
                    "hit_id": "sig1",
                    "rank": 1,
                    "url_title": "SIGCOMM 2025 accepted papers",
                    "url": "https://conferences.sigcomm.org/sigcomm/2025/accepted-papers/",
                    "score": 1.2,
                }
            ],
            [
                {
                    "hit_id": "nsdi1",
                    "rank": 1,
                    "url_title": "NSDI '25 Technical Sessions",
                    "url": "https://www.usenix.org/conference/nsdi25/technical-sessions",
                    "score": 1.3,
                }
            ],
        )
        urls = {str(row.get("url") or "") for row in merged}
        self.assertIn("https://conferences.sigcomm.org/sigcomm/2025/accepted-papers/", urls)
        self.assertIn("https://www.usenix.org/conference/nsdi25/technical-sessions", urls)

    def test_filter_records_by_urls_matches_requested_and_redirected_url(self):
        records = [
            {
                "target_id": "fetch-1",
                "requested_url": "https://conferences.sigcomm.org/sigcomm/2025/program.html",
                "url": "https://conferences.sigcomm.org/sigcomm/2025/program/",
                "url_aliases": [
                    "https://conferences.sigcomm.org/sigcomm/2025/program.html",
                    "https://conferences.sigcomm.org/sigcomm/2025/program/",
                ],
            }
        ]
        matched = agentic_mod._filter_records_by_urls(
            records,
            {"https://conferences.sigcomm.org/sigcomm/2025/program.html"},
        )
        self.assertEqual(len(matched), 1)

    def test_resolve_extract_request_matches_fetched_record_by_url_alias(self):
        redirected_url = "https://conferences.sigcomm.org/sigcomm/2025/program/"
        requested_url = "https://conferences.sigcomm.org/sigcomm/2025/program.html"
        request = extract_mod._resolve_extract_request(
            session_id="s1",
            cycle_index=1,
            params={
                "urls": [requested_url],
                "filters": {"institution": "Google", "year_gte": 2025},
                "auto_fetch": False,
            },
            paths={"result": Path("workspace/demo/artifacts/retrieval/agentic_result.yaml")},
            user_prompt="papers by google at SIGCOMM and NSDI in 2025",
            timeout_s=8.0,
            runtime_state={
                "url_hits": [],
                "fetched_records": [
                    {
                        "target_id": "fetch-1",
                        "requested_url": requested_url,
                        "url": redirected_url,
                        "url_aliases": [requested_url, redirected_url],
                        "url_title": "SIGCOMM 2025 program",
                        "status": "ok",
                        "segments": ["Preventing Network Bottlenecks ... Google"],
                    }
                ],
            },
            raw_event_fn=None,
            deps={
                "normalize_fetch_target_fn": extract_mod.normalize_fetch_target,
                "extract_target_filters_fn": extract_mod.extract_target_filters,
                "resolve_extract_intent_fn": extract_mod.resolve_extract_intent,
                "resolve_extract_anchor_terms_fn": agentic_mod._resolve_extract_anchor_terms,
                "safe_int_fn": agentic_mod._safe_int,
                "reuse_fetched_record_for_target_fn": agentic_mod._reuse_fetched_record_for_target,
                "fetch_target_record_fn": lambda **kwargs: (_ for _ in ()).throw(AssertionError("unexpected auto-fetch")),
                "merge_fetched_records_fn": agentic_mod._merge_fetched_records,
                "filter_records_by_urls_fn": agentic_mod._filter_records_by_urls,
                "next_op_id_fn": lambda _state, prefix="op": f"{prefix}-000001",
                "normalize_anchor_terms_fn": agentic_mod._normalize_anchor_terms,
            },
        )
        self.assertEqual(request["requested_urls"], [requested_url])
        self.assertEqual(len(request["records"]), 1)
        self.assertEqual(str(request["records"][0].get("url") or ""), redirected_url)

    def test_extract_candidates_accept_bool_llm_institution_match(self):
        facts = [
            {
                "status": "ok",
                "paper_title": "ParserHawk: Hardware-aware parser generator using program synthesis",
                "year": "2025",
                "doi": "",
                "arxiv_id": "",
                "url": "https://conferences.sigcomm.org/sigcomm/2025/accepted-papers/",
                "url_title": "SIGCOMM 2025 accepted papers",
                "evidence": "ParserHawk: Hardware-aware parser generator using program synthesis Xiangyu Gao (University of Washington); Jiaqi Gao (Alibaba Cloud)",
                "score": 0.9,
                "filters": {"institution": "Alibaba", "year_gte": 2025},
                "llm_extract": {"institution_match": True, "venue_hint": "SIGCOMM"},
                "extract_source": "llm",
            }
        ]
        candidates = agentic_mod._to_paper_candidates_from_facts(facts)
        self.assertEqual(len(candidates), 1)
        self.assertIn("ParserHawk", str(candidates[0].get("title") or ""))

    def test_strip_listing_author_tail(self):
        raw = "ParserHawk: Hardware-aware parser generator using program synthesis Xiangyu Gao (University of Washington); Jiaqi Gao (Alibaba Cloud)"
        stripped = agentic_mod._strip_listing_author_tail(raw)
        self.assertEqual(stripped, "ParserHawk: Hardware-aware parser generator using program synthesis")

    def test_strip_listing_author_tail_keeps_last_title_token(self):
        raw = "Firefly: Scalable, Ultra-Accurate Clock Synchronization for Datacenters"
        stripped = agentic_mod._strip_listing_author_tail(raw)
        self.assertEqual(stripped, raw)

    def test_strip_listing_author_tail_handles_name_comma_tail(self):
        raw = "ZENITH: Towards A Formally Verified Highly-Available Control Plane Pooria Namyar, Arvin Ghavidel"
        stripped = agentic_mod._strip_listing_author_tail(raw)
        self.assertEqual(stripped, "ZENITH: Towards A Formally Verified Highly-Available Control Plane")

    def test_authorish_title_fragment_is_rejected(self):
        self.assertTrue(
            agentic_mod._is_authorish_title_fragment(
                "Zhejiang University and Alibaba Cloud); Ju Zhang, Bowen Yang, Yi Wang"
            )
        )
        self.assertTrue(
            agentic_mod._is_authorish_title_fragment(
                "Shenzhen Institutes of Advanced Technology, Chinese Academy of Sciences"
            )
        )
        self.assertTrue(
            agentic_mod._is_authorish_title_fragment(
                "Hangzhou Feitian Cloud and Alibaba Cloud"
            )
        )
        self.assertFalse(
            agentic_mod._is_authorish_title_fragment(
                "Alibaba Stellar: A New Generation RDMA Network for Cloud AI"
            )
        )

    def test_extract_candidate_key_uses_title_for_extract_rows(self):
        key1 = agentic_mod._candidate_dedup_key(
            {
                "source": "agentic_extract",
                "reason": "extract_content",
                "title": "Paper A",
                "year": "2025",
                "url": "https://example.org/program",
                "doi": "",
                "arxiv_id": "",
            }
        )
        key2 = agentic_mod._candidate_dedup_key(
            {
                "source": "agentic_extract",
                "reason": "extract_content",
                "title": "Paper B",
                "year": "2025",
                "url": "https://example.org/program",
                "doi": "",
                "arxiv_id": "",
            }
        )
        self.assertNotEqual(key1, key2)

    def test_extract_candidates_require_local_institution_evidence(self):
        facts = [
            {
                "status": "ok",
                "paper_title": "SCX: Scheduler Extension for Linux",
                "year": "2025",
                "doi": "",
                "arxiv_id": "",
                "url": "https://example.org/venue",
                "url_title": "Venue Program",
                "evidence": "SCX: Scheduler Extension for Linux. Authors: Foo Bar, Baz Qux. Nearby entry mentions Alibaba Cloud for another paper.",
                "score": 0.8,
                "filters": {"institution": "Alibaba", "year_gte": 2025},
                "llm_extract": {"institution_match": ""},
            },
            {
                "status": "ok",
                "paper_title": "Alibaba Stellar: A New Generation RDMA Network for Cloud AI",
                "year": "2025",
                "doi": "",
                "arxiv_id": "",
                "url": "https://example.org/venue",
                "url_title": "Venue Program",
                "evidence": "Alibaba Stellar: A New Generation RDMA Network for Cloud AI. Authors ... (Alibaba Cloud).",
                "score": 0.9,
                "filters": {"institution": "Alibaba", "year_gte": 2025},
                "llm_extract": {"institution_match": "Alibaba Cloud"},
            },
        ]
        candidates = agentic_mod._to_paper_candidates_from_facts(facts)
        titles = [str(row.get("title") or "") for row in candidates]
        self.assertIn("Alibaba Stellar: A New Generation RDMA Network for Cloud AI", titles)
        self.assertNotIn("SCX: Scheduler Extension for Linux", titles)

    def test_deterministic_canonicalize_candidates_merges_malformed_aliases(self):
        candidates = [
            {
                "title": "Falcon: A Reliable, Low Latency Hardware Transport",
                "year": "2025",
                "url": "https://conferences.sigcomm.org/sigcomm/2025/accepted-papers/",
                "score": 0.9,
                "source": "agentic_extract",
                "reason": "extract_content",
                "source_id": "t1",
            },
            {
                "title": "Falcon: A Reliable, Low Latency Hardware Transport Arjun",
                "year": "2025",
                "url": "https://conferences.sigcomm.org/sigcomm/2025/accepted-papers/",
                "score": 0.72,
                "source": "agentic_extract",
                "reason": "extract_content",
                "source_id": "t2",
            },
            {
                "title": "Placement Preventing Network Bottlenecks: Accelerating Datacenter Services with Hotspot-Aware Placement for Compute and Storage",
                "year": "2025",
                "url": "https://www.usenix.org/conference/nsdi25/technical-sessions",
                "score": 0.9,
                "source": "agentic_extract",
                "reason": "extract_content",
                "source_id": "t3",
            },
        ]
        out, info = agentic_mod._deterministic_canonicalize_candidates(candidates)
        titles = [str(row.get("title") or "") for row in out]
        self.assertIn("Falcon: A Reliable, Low Latency Hardware Transport", titles)
        self.assertIn(
            "Preventing Network Bottlenecks: Accelerating Datacenter Services with Hotspot-Aware Placement for Compute and Storage",
            titles,
        )
        self.assertGreaterEqual(int(info.get("dropped") or 0), 1)

    def test_llm_canonicalize_candidates_merges_by_rep(self):
        candidates = [
            {
                "title": "ParserHawk: Hardware-aware parser generator using program synthesis",
                "year": "2025",
                "url": "https://conferences.sigcomm.org/sigcomm/2025/program/papers-info/",
                "score": 0.9,
                "source": "agentic_extract",
                "reason": "extract_content",
                "source_id": "t1",
            },
            {
                "title": "ParserHawk: Hardware-aware parser generator using program synthesis Xiangyu",
                "year": "2025",
                "url": "https://conferences.sigcomm.org/sigcomm/2025/accepted-papers/",
                "score": 0.72,
                "source": "agentic_extract",
                "reason": "extract_content",
                "source_id": "t2",
            },
        ]
        with patch(
            "src.orchestrator.agentic._openai_complete_json",
            return_value={
                "items": [
                    {
                        "candidate_id": "c0001",
                        "decision": "keep",
                        "canonical_candidate_id": "c0001",
                        "canonical_title": "ParserHawk: Hardware-aware parser generator using program synthesis",
                        "verification": "match",
                        "reason": "canonical",
                    },
                    {
                        "candidate_id": "c0002",
                        "decision": "keep",
                        "canonical_candidate_id": "c0001",
                        "canonical_title": "ParserHawk: Hardware-aware parser generator using program synthesis",
                        "verification": "match",
                        "reason": "author_tail_alias",
                    },
                ]
            },
        ):
            out, info = agentic_mod._canonicalize_candidates_with_llm(
                candidates=candidates,
                user_prompt="papers by Google at SIGCOMM in 2025",
                intent={"must_match": {"institution_any": ["Google"], "year_gte": 2025}},
                model="dummy",
                api_key_env="DS_API_KEY",
            )
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["title"], "ParserHawk: Hardware-aware parser generator using program synthesis")
        self.assertEqual(str(info.get("status") or ""), "ok")

    def test_llm_canonicalize_candidates_falls_back_on_empty_items(self):
        candidates = [
            {
                "title": "Firefly: Scalable, Ultra-Accurate Clock Synchronization for Datacenters",
                "year": "2025",
                "url": "https://conferences.sigcomm.org/sigcomm/2025/program/papers-info/",
                "score": 0.9,
                "source": "agentic_extract",
                "reason": "extract_content",
                "source_id": "t1",
            },
            {
                "title": "Firefly: Scalable, Ultra-Accurate Clock Synchronization for Datacenters Pooria",
                "year": "2025",
                "url": "https://conferences.sigcomm.org/sigcomm/2025/accepted-papers/",
                "score": 0.72,
                "source": "agentic_extract",
                "reason": "extract_content",
                "source_id": "t2",
            },
        ]
        with patch("src.orchestrator.agentic._openai_complete_json", return_value={"items": []}):
            out, info = agentic_mod._canonicalize_candidates_with_llm(
                candidates=candidates,
                user_prompt="papers by Google at SIGCOMM in 2025",
                intent={"must_match": {"institution_any": ["Google"], "year_gte": 2025}},
                model="dummy",
                api_key_env="DS_API_KEY",
            )
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["title"], "Firefly: Scalable, Ultra-Accurate Clock Synchronization for Datacenters")
        self.assertEqual(str(info.get("status") or ""), "fallback")

    def test_llm_canonicalize_candidates_drops_non_match_verification(self):
        candidates = [
            {
                "title": "Falcon: A Reliable, Low Latency Hardware Transport",
                "year": "2025",
                "url": "https://conferences.sigcomm.org/sigcomm/2025/program/papers-info/",
                "score": 0.9,
                "source": "agentic_extract",
                "reason": "extract_content",
                "source_id": "t1",
            },
            {
                "title": "Noisy Session Header Title",
                "year": "2025",
                "url": "https://example.org/noise",
                "score": 0.7,
                "source": "agentic_extract",
                "reason": "extract_content",
                "source_id": "t2",
            },
        ]
        with patch(
            "src.orchestrator.agentic._openai_complete_json",
            return_value={
                "items": [
                    {
                        "candidate_id": "c0001",
                        "decision": "keep",
                        "canonical_candidate_id": "c0001",
                        "canonical_title": "Falcon: A Reliable, Low Latency Hardware Transport",
                        "verification": "match",
                        "reason": "valid",
                    },
                    {
                        "candidate_id": "c0002",
                        "decision": "keep",
                        "canonical_candidate_id": "c0002",
                        "canonical_title": "Noisy Session Header Title",
                        "verification": "non_match",
                        "reason": "not_a_paper",
                    },
                ]
            },
        ):
            out, _info = agentic_mod._canonicalize_candidates_with_llm(
                candidates=candidates,
                user_prompt="papers by google at SIGCOMM and NSDI in 2025",
                intent={"must_match": {"institution_any": ["Google"], "year_gte": 2025}},
                model="dummy",
                api_key_env="DS_API_KEY",
            )
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["title"], "Falcon: A Reliable, Low Latency Hardware Transport")

    def test_llm_canonicalize_candidates_keeps_guarded_non_match_when_local_intent_matches(self):
        candidates = [
            {
                "title": "Firefly: Scalable, Ultra-Accurate Clock Synchronization for Datacenters",
                "year": "2025",
                "url": "https://conferences.sigcomm.org/sigcomm/2025/accepted-papers/",
                "score": 0.72,
                "source": "agentic_extract",
                "reason": "extract_content",
                "source_id": "t1",
                "authors": "Pooria Namyar; Yuliang Li; Nandita Dukkipati",
                "affiliations": "USC & Google LLC; Google LLC; Google LLC",
                "abstract_snippet": "SIGCOMM 2025 paper with multiple Google LLC coauthors",
            }
        ]
        with patch(
            "src.orchestrator.agentic._openai_complete_json",
            return_value={
                "items": [
                    {
                        "candidate_id": "c0001",
                        "decision": "drop",
                        "canonical_candidate_id": "c0001",
                        "canonical_title": "Firefly: Scalable, Ultra-Accurate Clock Synchronization for Datacenters",
                        "verification": "non_match",
                        "reason": "only one Google-affiliated coauthor",
                    }
                ]
            },
        ):
            out, info = agentic_mod._canonicalize_candidates_with_llm(
                candidates=candidates,
                user_prompt="papers by google at SIGCOMM in 2025",
                intent={"must_match": {"institution_any": ["Google"], "venue_any": ["SIGCOMM"], "year_gte": 2025}},
                model="dummy",
                api_key_env="DS_API_KEY",
            )
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["title"], "Firefly: Scalable, Ultra-Accurate Clock Synchronization for Datacenters")
        self.assertEqual(int(info.get("overridden_non_match") or 0), 1)

    def test_llm_preferred_over_listing_deterministic_for_same_target(self):
        deterministic = {
            "session_id": "s1",
            "cycle_index": 1,
            "target_id": "fetch-1",
            "url": "https://example.org/listing",
            "url_title": "Listing",
            "paper_title": "Zhejiang University and Alibaba Cloud); Ju Zhang, Bowen Yang",
            "doi": "",
            "arxiv_id": "",
            "year": "2025",
            "filters": {"institution": "Alibaba", "year_gte": 2025},
            "evidence": "Zhejiang University and Alibaba Cloud); Ju Zhang, Bowen Yang",
            "score": 0.72,
            "status": "ok",
            "extract_source": "listing_deterministic",
        }
        llm = {
            "session_id": "s1",
            "cycle_index": 1,
            "target_id": "fetch-1",
            "url": "https://example.org/listing",
            "url_title": "Listing",
            "paper_title": "ParserHawk: Hardware-aware parser generator using program synthesis",
            "doi": "",
            "arxiv_id": "",
            "year": "2025",
            "filters": {"institution": "Alibaba", "year_gte": 2025},
            "evidence": "ParserHawk: Hardware-aware parser generator using program synthesis Xiangyu Gao (University of Washington); Jiaqi Gao (Alibaba Cloud)",
            "score": 0.9,
            "status": "ok",
            "extract_source": "llm",
            "llm_extract": {"institution_match": True, "venue_hint": "SIGCOMM"},
        }
        # Mirrors merge preference logic in extract path: listing_deterministic rows
        # from targets with llm facts are dropped before candidate assembly.
        merged_facts = [llm]
        candidates = agentic_mod._to_paper_candidates_from_facts(merged_facts)
        titles = [str(row.get("title") or "") for row in candidates]
        self.assertIn("ParserHawk: Hardware-aware parser generator using program synthesis", titles)
        self.assertNotIn("Zhejiang University and Alibaba Cloud); Ju Zhang, Bowen Yang", titles)

    def test_candidate_filter_uses_structured_match_fields_not_topic_tokens(self):
        facts = [
            {
                "status": "ok",
                "paper_title": "Learnings from Deploying Network QoS Alignment to Application Priorities for Storage Services",
                "year": "2025",
                "url": "https://dblp.org/db/conf/nsdi/nsdi2025",
                "evidence": "Matthew Buckley, Parsa Pazhooheshy, Z. Morley Mao, Nandita Dukkipati, Hamid Hajabdolali Bazzaz",
                "filters": {"institution": "Google", "year_gte": 2025, "topic": "papers by google at SIGCOMM and NSDI in 2025"},
                "extract_intent": {"must_match": {"institution_any": ["Google"], "venue_any": ["SIGCOMM", "NSDI"], "year_gte": 2025}},
                "llm_extract": {
                    "match_decision": "",
                    "authors": ["Matthew Buckley", "Parsa Pazhooheshy"],
                    "affiliations": ["Google", "Google and University of Toronto"],
                    "institution_hits": ["Google"],
                    "abstract_snippet": "",
                },
            }
        ]
        candidates = agentic_mod._to_paper_candidates_from_facts(facts)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["title"], "Learnings from Deploying Network QoS Alignment to Application Priorities for Storage Services")

    def test_tail_variant_deduplicates_to_clean_title(self):
        facts = [
            {
                "status": "ok",
                "paper_title": "Nezha: SmartNIC-based Virtual Switch Load Sharing",
                "year": "2025",
                "doi": "",
                "arxiv_id": "",
                "url": "https://example.org/listing",
                "url_title": "Listing",
                "evidence": "Nezha: SmartNIC-based Virtual Switch Load Sharing ... Alibaba Cloud",
                "score": 0.9,
                "filters": {"institution": "Alibaba", "year_gte": 2025},
                "extract_source": "llm",
                "llm_extract": {"institution_match": True},
            },
            {
                "status": "ok",
                "paper_title": "Nezha: SmartNIC-based Virtual Switch Load Sharing Xing",
                "year": "2025",
                "doi": "",
                "arxiv_id": "",
                "url": "https://example.org/listing",
                "url_title": "Listing",
                "evidence": "Nezha: SmartNIC-based Virtual Switch Load Sharing Xing Li (Zhejiang University and Alibaba Cloud)",
                "score": 0.72,
                "filters": {"institution": "Alibaba", "year_gte": 2025},
                "extract_source": "listing_deterministic",
            },
        ]
        candidates = agentic_mod._to_paper_candidates_from_facts(facts)
        titles = [str(row.get("title") or "") for row in candidates]
        self.assertEqual(sum(1 for t in titles if t.startswith("Nezha: SmartNIC-based Virtual Switch Load Sharing")), 1)

    def test_discover_pagination_urls(self):
        html = """
        <html><body>
          <a href="/conference/osdi25/technical-sessions?page=2">Next</a>
          <a href="/conference/osdi25/technical-sessions?page=3">3</a>
          <a href="https://example.com/other">external</a>
        </body></html>
        """
        urls = agentic_mod._discover_pagination_urls(
            html,
            "https://www.usenix.org/conference/osdi25/technical-sessions",
            max_extra_pages=4,
        )
        self.assertIn("https://www.usenix.org/conference/osdi25/technical-sessions?page=2", urls)
        self.assertIn("https://www.usenix.org/conference/osdi25/technical-sessions?page=3", urls)
        self.assertTrue(all(url.startswith("https://www.usenix.org/") for url in urls))

    def test_extract_year_best_prefers_recent_year(self):
        text = "Bio 2016 and 2020. Proceedings 2025. Session notes."
        self.assertEqual(agentic_mod._extract_year_best(text, year_gte=2025), "2025")
        self.assertEqual(agentic_mod._extract_year_best(text, year_gte=2026), "2025")

    def test_shortlist_hints_pipe_format_is_normalized(self):
        normalized = agentic_mod._normalize_shortlist_hints(
            {"prefer": ["venue_program_pages|author_sources|avoid_detail_pages", "unknown_hint"]}
        )
        self.assertEqual(
            normalized,
            {"prefer": ["venue_program_pages", "author_sources", "avoid_detail_pages"]},
        )

    def test_shortlist_hints_can_promote_program_pages(self):
        ranked = [
            {"title": "Paper detail", "url": "https://dl.acm.org/doi/10.1/x", "score": 1.2},
            {"title": "OSDI '25 Technical Sessions", "url": "https://www.usenix.org/conference/osdi25/technical-sessions", "score": 1.1},
            {"title": "SOSP 2025 Program", "url": "https://sosp.org/2025/program", "score": 1.0},
            {"title": "EuroSys 2025 Accepted Papers", "url": "https://eurosys.org/2025/accepted-papers", "score": 0.9},
        ]
        reranked = agentic_mod._apply_shortlist_hints(ranked, {"prefer": ["venue_program_pages", "avoid_detail_pages"]})
        self.assertIn("Technical Sessions", str((reranked[0] or {}).get("title") or ""))

    def test_multi_venue_query_is_split_into_one_venue_per_query(self):
        queries = agentic_mod._normalize_search_queries(
            ["Bytedance 2025 OSDI SOSP ASPLOS ISCA MICRO EuroSys"],
            max_total=16,
        )
        self.assertGreaterEqual(len(queries), 5)
        self.assertTrue(any("OSDI" in q for q in queries))
        self.assertTrue(any("SOSP" in q for q in queries))
        self.assertTrue(any("ASPLOS" in q for q in queries))
        self.assertTrue(any("ISCA" in q for q in queries))
        self.assertTrue(any("MICRO" in q for q in queries))
        self.assertTrue(any("EuroSys" in q for q in queries))

    def test_diverse_shortlist_prefers_query_coverage(self):
        ranked = [
            {"title": "OSDI page A", "url": "https://a.org/osdi", "query_used": "OSDI 2025 accepted papers", "score": 2.0},
            {"title": "OSDI page B", "url": "https://b.org/osdi", "query_used": "OSDI 2025 accepted papers", "score": 1.9},
            {"title": "SOSP page", "url": "https://c.org/sosp", "query_used": "SOSP 2025 accepted papers", "score": 1.8},
            {"title": "ASPLOS page", "url": "https://d.org/asplos", "query_used": "ASPLOS 2025 accepted papers", "score": 1.7},
        ]
        shortlisted = agentic_mod._select_diverse_shortlist(ranked, 3)
        queries = {str(r.get("query_used") or "") for r in shortlisted}
        self.assertIn("OSDI 2025 accepted papers", queries)
        self.assertIn("SOSP 2025 accepted papers", queries)
        self.assertIn("ASPLOS 2025 accepted papers", queries)

    def test_diverse_shortlist_keeps_complementary_official_listing_pages(self):
        ranked = [
            {
                "title": "Proceedings of the ACM SIGCOMM 2025 Conference",
                "url": "https://conferences.sigcomm.org/sigcomm/2025/program/papers-info/",
                "query_used": "SIGCOMM 2025 accepted papers",
                "score": 1.4,
            },
            {
                "title": "ACM SIGCOMM 2025 List of Accepted Papers - Events",
                "url": "https://conferences.sigcomm.org/sigcomm/2025/accepted-papers/",
                "query_used": "SIGCOMM 2025 accepted papers",
                "score": 1.35,
            },
            {
                "title": "SIGCOMM 2025 | Awesome Papers",
                "url": "https://paper.lingyunyang.com/reading-notes/conference/sigcomm-2025",
                "query_used": "SIGCOMM 2025 accepted papers",
                "score": 1.1,
            },
        ]
        shortlisted = agentic_mod._select_diverse_shortlist(ranked, 2)
        urls = {str(r.get("url") or "") for r in shortlisted}
        self.assertIn("https://conferences.sigcomm.org/sigcomm/2025/program/papers-info/", urls)
        self.assertIn("https://conferences.sigcomm.org/sigcomm/2025/accepted-papers/", urls)

    def test_filter_keeps_conference_signal_on_non_whitelisted_host(self):
        kept, rejected = agentic_mod._filter_search_rows(
            [
                {
                    "title": "MICRO 2025 accepted papers",
                    "url": "https://microarch.org/micro58/program",
                    "abstract": "Conference accepted papers list",
                    "_snippet": "Conference accepted papers list",
                    "_host": "microarch.org",
                }
            ]
        )
        self.assertEqual(len(kept), 1)
        self.assertEqual(rejected, {})

    def test_non_paper_fact_is_dropped(self):
        candidates = agentic_mod._to_paper_candidates_from_facts(
            [
                {
                    "status": "ok",
                    "paper_title": "Opening Remarks and Keynote",
                    "evidence": "Opening remarks for the conference day 1",
                    "url": "https://example.org/conference/program",
                    "target_id": "x",
                    "year": "2025",
                    "doi": "",
                    "arxiv_id": "",
                    "score": 0.8,
                }
            ]
        )
        self.assertEqual(candidates, [])

    def test_single_candidate_title_is_canonicalized(self):
        candidates = agentic_mod._to_paper_candidates_from_facts(
            [
                {
                    "status": "ok",
                    "paper_title": "Placement Preventing Network Bottlenecks: Accelerating Datacenter Services with Hotspot-Aware Placement for Compute and Storage",
                    "evidence": "Placement Preventing Network Bottlenecks: Accelerating Datacenter Services with Hotspot-Aware Placement for Compute and Storage [ Paper ] Google",
                    "url": "https://paper.lingyunyang.com/reading-notes/conference/nsdi-2025",
                    "target_id": "x",
                    "year": "2025",
                    "doi": "",
                    "arxiv_id": "",
                    "score": 0.9,
                    "llm_extract": {"match_decision": "match", "institution_match": True},
                    "filters": {"institution": "Google", "year_gte": 2025},
                }
            ]
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(
            str(candidates[0].get("title") or ""),
            "Preventing Network Bottlenecks: Accelerating Datacenter Services with Hotspot-Aware Placement for Compute and Storage",
        )

    def test_llm_decision_false_excludes_candidate(self):
        candidates = agentic_mod._to_paper_candidates_from_facts(
            [
                {
                    "status": "ok",
                    "paper_title": "Some Plausible Paper Title",
                    "evidence": "Conference listing line",
                    "url": "https://example.org/conference/program",
                    "target_id": "x",
                    "year": "2025",
                    "doi": "",
                    "arxiv_id": "",
                    "score": 0.8,
                    "llm_is_paper": False,
                }
            ]
        )
        self.assertEqual(candidates, [])

    def test_llm_decision_true_can_include_candidate(self):
        candidates = agentic_mod._to_paper_candidates_from_facts(
            [
                {
                    "status": "ok",
                    "paper_title": "This line by itself may be weak",
                    "evidence": "",
                    "url": "https://example.org/misc",
                    "target_id": "x",
                    "year": "2025",
                    "doi": "",
                    "arxiv_id": "",
                    "score": 0.8,
                    "llm_is_paper": True,
                }
            ]
        )
        self.assertEqual(len(candidates), 1)

    def test_extract_listing_text_fallback_prefers_raw_when_main_too_short(self):
        html = "<html><body>" + "Program Item. " * 2000 + "</body></html>"
        with patch("src.orchestrator.agentic._extract_main_text_from_html", return_value="short text"):
            text = agentic_mod._extract_listing_text_with_fallback(html, max_chars=30000)
        self.assertGreater(len(text), 1000)
        self.assertIn("Program Item", text)

    def test_merge_paper_candidates_keeps_prior_cycle_results(self):
        existing = [
            {
                "title": "Paper A",
                "year": "2025",
                "doi": "10.1/a",
                "arxiv_id": "",
                "url": "https://x.org/a",
                "score": 0.9,
            }
        ]
        incoming = []
        merged = agentic_mod._merge_paper_candidates(existing, incoming, top_n=5)
        self.assertEqual(len(merged), 1)
        self.assertEqual(str(merged[0].get("title") or ""), "Paper A")

    def test_search_web_query_timeout_does_not_fail_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "workspace"
            ws.mkdir(parents=True, exist_ok=True)
            with patch("src.utils.paths.WORKSPACE_ROOT", ws):
                run_step("demo", "init", theme="systems")
                with patch.dict("os.environ", {"SEARXNG_URL": "http://searxng:8080", "DS_API_KEY": "dummy"}, clear=False):
                    with patch(
                        "src.orchestrator.agentic._agent_next_action_llm",
                        return_value=_agent_reply(
                            action="search_web",
                            queries=[
                                "Bytedance systems 2025 OSDI SOSP",
                                "Bytedance systems 2025 ASPLOS",
                            ],
                            stop=True,
                        ),
                    ):
                        with patch(
                            "src.orchestrator.agentic.get_json",
                            side_effect=[
                                {
                                    "results": [
                                        {
                                            "title": "Bytedance OSDI 2025 paper",
                                            "url": "https://arxiv.org/abs/2501.00001",
                                            "content": "Bytedance systems paper at OSDI 2025",
                                            "engine": "searxng",
                                        }
                                    ]
                                },
                                TimeoutError("timed out"),
                                {
                                    "results": [
                                        {
                                            "title": "Bytedance ASPLOS 2025 paper",
                                            "url": "https://doi.org/10.1145/example",
                                            "content": "Bytedance architecture paper at ASPLOS 2025",
                                            "engine": "searxng",
                                        }
                                    ]
                                },
                            ],
                        ):
                            run_step(
                                "demo",
                                "retrieve-agentic",
                                prompt="papers by Bytedance at system and architecture top conferences in 2025",
                                top_n=5,
                            )

            rdir = ws / "demo" / "artifacts" / "retrieval"
            result = yamlx.load(rdir / "agentic_result.yaml")
            self.assertEqual(result["status"], "completed")
            self.assertNotEqual(result.get("stop_reason"), "agentic_error:TimeoutError:timed out")
            self.assertIn("papers", result)
            self.assertTrue((rdir / "agentic_trajectory.yaml").exists())

    def test_agentic_single_cycle_writes_all_contract_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "workspace"
            ws.mkdir(parents=True, exist_ok=True)
            with patch("src.utils.paths.WORKSPACE_ROOT", ws):
                run_step("demo", "init", theme="systems")
                with patch.dict("os.environ", {"SEARXNG_URL": "http://searxng:8080", "DS_API_KEY": "dummy"}, clear=False):
                    with patch(
                        "src.orchestrator.agentic._agent_next_action_llm",
                        return_value=_agent_reply(
                            action="search_web",
                            queries=["memory disaggregation systems"],
                            stop=True,
                        ),
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

            result = yamlx.load(rdir / "agentic_result.yaml")
            self.assertEqual(result["artifact_type"], "agentic_result")
            self.assertEqual(result["schema_version"], "0.2.0")
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["papers"], [])
            self.assertTrue((rdir / "agentic_trajectory.yaml").exists())
            self.assertTrue((rdir / "agentic_raw.ndjson").exists())

    def test_raw_trace_contains_paired_atomic_op_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "workspace"
            ws.mkdir(parents=True, exist_ok=True)
            with patch("src.utils.paths.WORKSPACE_ROOT", ws):
                run_step("demo", "init", theme="systems")
                with patch.dict("os.environ", {"SEARXNG_URL": "http://searxng:8080", "DS_API_KEY": "dummy"}, clear=False):
                    with patch(
                        "src.orchestrator.agentic._agent_next_action_llm",
                        side_effect=[
                            _agent_reply(
                                action="search_web",
                                queries=["SIGCOMM 2025 accepted papers"],
                                params={"queries": ["SIGCOMM 2025 accepted papers"]},
                            ),
                            _agent_reply(
                                action="extract_content",
                                params={
                                    "targets": [
                                        {
                                            "url": "https://conferences.sigcomm.org/sigcomm/2025/accepted-papers/",
                                            "title": "SIGCOMM 2025 accepted papers",
                                            "match": {"institution_any": ["Alibaba"], "year_gte": 2025},
                                            "anchor_terms": ["Alibaba", "ParserHawk"],
                                        }
                                    ]
                                },
                                stop=True,
                                reason="done",
                            ),
                        ],
                    ):
                        with patch(
                            "src.orchestrator.agentic.get_json",
                            return_value={
                                "results": [
                                    {
                                        "title": "ACM SIGCOMM 2025 List of Accepted Papers - Events",
                                        "url": "https://conferences.sigcomm.org/sigcomm/2025/accepted-papers/",
                                        "content": "ACM SIGCOMM 2025 List of Accepted Papers.",
                                        "engine": "startpage",
                                    }
                                ]
                            },
                        ):
                            with patch(
                                "src.orchestrator.agentic._fetch_url_raw",
                                return_value=(
                                    "<html><body>ParserHawk: Hardware-aware parser generator using program synthesis (Alibaba Cloud).</body></html>",
                                    "text/html",
                                ),
                            ):
                                with patch(
                                    "src.orchestrator.agentic._extract_html_structural_segments",
                                    return_value=[
                                        "segment 1 ParserHawk Alibaba Cloud",
                                        "segment 2 ParserHawk Alibaba Cloud",
                                        "segment 3 ParserHawk Alibaba Cloud",
                                        "segment 4 ParserHawk Alibaba Cloud",
                                        "segment 5 ParserHawk Alibaba Cloud",
                                    ],
                                ):
                                    with patch(
                                        "src.orchestrator.agentic._extract_facts_with_llm",
                                        return_value=(
                                            [
                                                {
                                                    "session_id": "s1",
                                                    "cycle_index": 1,
                                                    "target_id": "fetch-1",
                                                    "url": "https://conferences.sigcomm.org/sigcomm/2025/accepted-papers/",
                                                    "url_title": "SIGCOMM 2025 accepted papers",
                                                    "paper_title": "ParserHawk: Hardware-aware parser generator using program synthesis",
                                                    "doi": "",
                                                    "arxiv_id": "",
                                                    "year": "2025",
                                                    "filters": {"institution": "Alibaba", "year_gte": 2025},
                                                    "evidence": "ParserHawk ... Alibaba Cloud",
                                                    "score": 0.9,
                                                    "status": "ok",
                                                    "extract_source": "llm",
                                                    "llm_extract": {"confidence": 0.9, "institution_match": "Alibaba Cloud"},
                                                }
                                            ],
                                            {"response_items_count": 1},
                                        ),
                                    ):
                                        run_step(
                                            "demo",
                                            "retrieve-agentic",
                                            prompt="papers by Alibaba at SIGCOMM 2025",
                                            top_n=5,
                                        )

            raw_path = ws / "demo" / "artifacts" / "retrieval" / "agentic_raw.ndjson"
            events = _read_raw_events(raw_path)
            starts: dict[str, dict] = {}
            ends: dict[str, dict] = {}
            for row in events:
                event_type = str(row.get("event_type") or "")
                payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
                op_id = str(payload.get("op_id") or "")
                if not op_id:
                    continue
                if event_type == "op_start":
                    starts[op_id] = payload
                if event_type == "op_end":
                    ends[op_id] = payload
            self.assertGreaterEqual(len(starts), 4)
            self.assertEqual(set(starts), set(ends))
            op_types = {str(payload.get("op_type") or "") for payload in starts.values()}
            self.assertIn("agent_llm", op_types)
            self.assertIn("web_fetch", op_types)
            self.assertIn("extract_llm", op_types)
            self.assertTrue(any(str(payload.get("op_type") or "") == "web_search_query" for payload in starts.values()))

    def test_agentic_debug_retrieval_still_uses_compact_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "workspace"
            ws.mkdir(parents=True, exist_ok=True)
            with patch("src.utils.paths.WORKSPACE_ROOT", ws):
                run_step("demo", "init", theme="systems")
                with patch.dict("os.environ", {"SEARXNG_URL": "http://searxng:8080", "DS_API_KEY": "dummy"}, clear=False):
                    with patch(
                        "src.orchestrator.agentic._agent_next_action_llm",
                        return_value=_agent_reply(
                            action="search_web",
                            queries=["memory disaggregation systems"],
                            stop=True,
                        ),
                    ):
                        with patch("src.orchestrator.agentic._search_web_queries", side_effect=_mock_search_web_cycle1):
                            run_step(
                                "demo",
                                "retrieve-agentic",
                                prompt="memory disaggregation",
                                top_n=2,
                                debug_retrieval=True,
                            )
            rdir = ws / "demo" / "artifacts" / "retrieval"
            self.assertTrue((rdir / "agentic_result.yaml").exists())
            self.assertTrue((rdir / "agentic_trajectory.yaml").exists())
            self.assertFalse((rdir / "agentic_questions.yaml").exists())

    def test_agentic_empty_results_stops_with_no_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "workspace"
            ws.mkdir(parents=True, exist_ok=True)
            with patch("src.utils.paths.WORKSPACE_ROOT", ws):
                run_step("demo", "init", theme="systems")
                with patch.dict("os.environ", {"SEARXNG_URL": "http://searxng:8080", "DS_API_KEY": "dummy"}, clear=False):
                    with patch(
                        "src.orchestrator.agentic._agent_next_action_llm",
                        return_value=_agent_reply(
                            action="search_web",
                            queries=["nonexistent topic"],
                            stop=True,
                        ),
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
            self.assertEqual(result["papers"], [])
            trajectory = yamlx.load(rdir / "agentic_trajectory.yaml")
            self.assertEqual((trajectory.get("user_view") or [{}])[-1].get("decision_reason"), "no_candidates")

    def test_agentic_unimplemented_action_stub_stops_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "workspace"
            ws.mkdir(parents=True, exist_ok=True)
            with patch("src.utils.paths.WORKSPACE_ROOT", ws):
                run_step("demo", "init", theme="systems")
                with patch.dict("os.environ", {"SEARXNG_URL": "http://searxng:8080", "DS_API_KEY": "dummy"}, clear=False):
                    with patch(
                        "src.orchestrator.agentic._agent_next_action_llm",
                        return_value=_agent_reply(action="ask_user"),
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
            self.assertEqual(result["stop_reason"], "unsupported_action:ask_user")
            trajectory = yamlx.load(rdir / "agentic_trajectory.yaml")
            self.assertEqual(((trajectory.get("steps") or [{}])[0].get("action_result") or {}).get("status"), "blocked")

    def test_extract_target_ids_remap_from_url_hits_to_fetched_urls(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "workspace"
            ws.mkdir(parents=True, exist_ok=True)
            with patch("src.utils.paths.WORKSPACE_ROOT", ws):
                run_step("demo", "init", theme="systems")
                with patch.dict("os.environ", {"SEARXNG_URL": "http://searxng:8080", "DS_API_KEY": "dummy"}, clear=False):
                    with patch(
                        "src.orchestrator.agentic._agent_next_action_llm",
                        side_effect=[
                            _agent_reply(
                                action="search_web",
                                queries=["OSDI 2025 accepted papers"],
                                params={"queries": ["OSDI 2025 accepted papers"]},
                            ),
                            _agent_reply(
                                action="extract_content",
                                params={
                                    "target_ids": ["abc123hitid"],
                                    "filters": {"institution": "Bytedance", "year_gte": 2025},
                                },
                                stop=True,
                                reason="done",
                            ),
                        ],
                    ):
                        with patch(
                            "src.orchestrator.agentic._search_web_queries",
                            return_value=(
                                [],
                                [
                                    {
                                        "source": "startpage",
                                        "source_id": "x",
                                        "title": "OSDI '25 Technical Sessions - USENIX",
                                        "venue": "",
                                        "year": "2025",
                                        "doi": "",
                                        "arxiv_id": "",
                                        "url": "https://www.usenix.org/conference/osdi25/technical-sessions",
                                        "abstract": "sessions page",
                                        "keywords": [],
                                        "categories": [],
                                        "score": 1.0,
                                        "reason": "searxng_search",
                                        "query_used": "OSDI 2025 accepted papers",
                                        "_snippet": "sessions page",
                                        "_author_hint": "",
                                        "_host": "www.usenix.org",
                                    }
                                ],
                            ),
                        ):
                            with patch("src.orchestrator.agentic._make_hit_id", return_value="abc123hitid"):
                                with patch(
                                    "src.orchestrator.agentic._fetch_url_raw",
                                    return_value=(
                                        "<html><body>To PRI or Not To PRI, That's the question. Alibaba Group and Bytedance mention. OSDI 2025.</body></html>",
                                        "text/html",
                                    ),
                                ):
                                    with patch(
                                        "src.orchestrator.agentic._extract_facts_with_llm",
                                        return_value=(
                                            [
                                                {
                                                    "session_id": "s1",
                                                    "cycle_index": 1,
                                                    "target_id": "fetch-1",
                                                    "url": "https://www.usenix.org/conference/osdi25/technical-sessions",
                                                    "url_title": "OSDI '25 Technical Sessions - USENIX",
                                                    "paper_title": "To PRI or Not To PRI, That's the question",
                                                    "doi": "",
                                                    "arxiv_id": "",
                                                    "year": "2025",
                                                    "filters": {"institution": "Bytedance", "year_gte": 2025},
                                                    "evidence": "To PRI or Not To PRI, That's the question. Bytedance mention.",
                                                    "score": 0.9,
                                                    "status": "ok",
                                                    "extract_source": "llm",
                                                }
                                            ],
                                            {"response_items_count": 1},
                                        ),
                                    ):
                                        run_step(
                                            "demo",
                                            "retrieve-agentic",
                                            prompt="papers by Bytedance at system and architecture top conferences in 2025",
                                            top_n=5,
                                        )

            rdir = ws / "demo" / "artifacts" / "retrieval"
            result = yamlx.load(rdir / "agentic_result.yaml")
            self.assertEqual(result["status"], "completed")
            self.assertGreaterEqual(len(result.get("papers") or []), 1)
            first = (result.get("papers") or [])[0]
            self.assertIn("to pri or not to pri", str(first.get("title") or "").lower())
            self.assertEqual(
                str(first.get("source_url") or ""),
                "https://www.usenix.org/conference/osdi25/technical-sessions",
            )

    def test_agentic_search_fetch_extract_promotes_final_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "workspace"
            ws.mkdir(parents=True, exist_ok=True)
            with patch("src.utils.paths.WORKSPACE_ROOT", ws):
                run_step("demo", "init", theme="systems")
                with patch.dict("os.environ", {"SEARXNG_URL": "http://searxng:8080", "DS_API_KEY": "dummy"}, clear=False):
                    with patch(
                        "src.orchestrator.agentic._agent_next_action_llm",
                        side_effect=[
                            _agent_reply(
                                action="search_web",
                                queries=["SIGCOMM 2024 accepted papers Alibaba"],
                                params={"queries": ["SIGCOMM 2024 accepted papers Alibaba"]},
                            ),
                            _agent_reply(
                                action="extract_content",
                                params={
                                    "targets": [
                                        {
                                            "url": "https://conferences.sigcomm.org/sigcomm/2024/accepted-papers/",
                                            "title": "List of Accepted Papers - Events - acm sigcomm",
                                            "match": {"institution_any": ["Alibaba"], "year_gte": 2024},
                                            "anchor_terms": ["Alibaba", "Alibaba HPN"],
                                        }
                                    ]
                                },
                                stop=True,
                                reason="done",
                            ),
                        ],
                    ):
                        with patch("src.orchestrator.agentic._search_web_queries", side_effect=_mock_search_web_cycle1):
                            with patch(
                                "src.orchestrator.agentic._fetch_url_raw",
                                return_value=(
                                    "<html><body>ACM SIGCOMM 2024 accepted papers. Alibaba HPN: A Data Center Network for Large Language Model Training. "
                                    "Authors from Alibaba Cloud. DOI 10.1145/3651890.3672265.</body></html>",
                                    "text/html",
                                ),
                            ):
                                with patch(
                                    "src.orchestrator.agentic._extract_facts_with_llm",
                                    return_value=(
                                        [
                                            {
                                                "session_id": "s1",
                                                "cycle_index": 1,
                                                "target_id": "fetch-1",
                                                "url": "https://conferences.sigcomm.org/sigcomm/2024/accepted-papers/",
                                                "url_title": "List of Accepted Papers - Events - acm sigcomm",
                                                "paper_title": "Alibaba HPN: A Data Center Network for Large Language Model Training",
                                                "doi": "10.1145/3651890.3672265",
                                                "arxiv_id": "",
                                                "year": "2024",
                                                "filters": {"institution": "Alibaba", "year_gte": 2024},
                                                "evidence": "Alibaba HPN ... DOI 10.1145/3651890.3672265",
                                                "score": 0.9,
                                                "status": "ok",
                                                "extract_source": "llm",
                                            }
                                        ],
                                        {"response_items_count": 1},
                                    ),
                                ):
                                    run_step(
                                        "demo",
                                        "retrieve-agentic",
                                        prompt="papers by Alibaba at SIGCOMM since 2024",
                                        top_n=3,
                                    )

            rdir = ws / "demo" / "artifacts" / "retrieval"
            result = yamlx.load(rdir / "agentic_result.yaml")
            self.assertEqual(result["status"], "completed")
            self.assertGreaterEqual(len(result.get("papers") or []), 1)
            first = (result.get("papers") or [])[0]
            self.assertIn("alibaba hpn", str(first.get("title") or "").lower())
            self.assertEqual(str(first.get("doi") or ""), "10.1145/3651890.3672265")
            self.assertTrue((rdir / "agentic_trajectory.yaml").exists())

    def test_agentic_extract_content_auto_fetch_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "workspace"
            ws.mkdir(parents=True, exist_ok=True)
            with patch("src.utils.paths.WORKSPACE_ROOT", ws):
                run_step("demo", "init", theme="systems")
                with patch.dict("os.environ", {"SEARXNG_URL": "http://searxng:8080", "DS_API_KEY": "dummy"}, clear=False):
                    with patch(
                        "src.orchestrator.agentic._agent_next_action_llm",
                        side_effect=[
                            _agent_reply(
                                action="extract_content",
                                params={
                                    "auto_fetch": True,
                                    "urls": ["https://dl.acm.org/doi/10.1145/3651890.3672265"],
                                    "filters": {"institution": "Alibaba", "year_gte": 2024},
                                },
                                stop=True,
                                reason="done",
                            )
                        ],
                    ):
                        with patch(
                            "src.orchestrator.agentic._fetch_url_raw",
                            return_value=(
                                "<html><body>Alibaba HPN: A Data Center Network for Large Language Model Training. "
                                "ACM SIGCOMM 2024. DOI 10.1145/3651890.3672265. Alibaba Cloud.</body></html>",
                                "text/html",
                            ),
                        ):
                            with patch(
                                "src.orchestrator.agentic._extract_facts_with_llm",
                                return_value=(
                                    [
                                        {
                                            "session_id": "s1",
                                            "cycle_index": 1,
                                            "target_id": "fetch-1",
                                            "url": "https://dl.acm.org/doi/10.1145/3651890.3672265",
                                            "url_title": "",
                                            "paper_title": "Alibaba HPN: A Data Center Network for Large Language Model Training",
                                            "doi": "10.1145/3651890.3672265",
                                            "arxiv_id": "",
                                            "year": "2024",
                                            "filters": {"institution": "Alibaba", "year_gte": 2024},
                                            "evidence": "Alibaba HPN ... DOI 10.1145/3651890.3672265",
                                            "score": 0.9,
                                            "status": "ok",
                                            "extract_source": "llm",
                                        }
                                    ],
                                    {"response_items_count": 1},
                                ),
                            ):
                                run_step(
                                    "demo",
                                    "retrieve-agentic",
                                    prompt="papers by Alibaba at SIGCOMM since 2024",
                                    top_n=3,
                                )

            rdir = ws / "demo" / "artifacts" / "retrieval"
            result = yamlx.load(rdir / "agentic_result.yaml")
            self.assertEqual(result["status"], "completed")
            self.assertGreaterEqual(len(result.get("papers") or []), 1)
            self.assertEqual(str((result.get("papers") or [])[0].get("doi") or ""), "10.1145/3651890.3672265")

    def test_agentic_extract_content_auto_fetch_matches_redirected_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "workspace"
            ws.mkdir(parents=True, exist_ok=True)
            with patch("src.utils.paths.WORKSPACE_ROOT", ws):
                run_step("demo", "init", theme="systems")
                with patch.dict("os.environ", {"SEARXNG_URL": "http://searxng:8080", "DS_API_KEY": "dummy"}, clear=False):
                    with patch(
                        "src.orchestrator.agentic._agent_next_action_llm",
                        side_effect=[
                            _agent_reply(
                                action="extract_content",
                                params={
                                    "auto_fetch": True,
                                    "urls": ["https://conferences.sigcomm.org/sigcomm/2025/program.html"],
                                    "filters": {"institution": "Google", "year_gte": 2025},
                                },
                                stop=True,
                                reason="done",
                            )
                        ],
                    ):
                        def _fetch_side_effect(url, *, timeout_s, max_bytes):
                            if url.endswith("program.html"):
                                raise RuntimeError("redirect")
                            return (
                                "<html><body>Preventing Network Bottlenecks: Accelerating Datacenter Services with Hotspot-Aware Placement for Compute and Storage. Google.</body></html>",
                                "text/html",
                            )

                        with patch("src.orchestrator.agentic._fetch_url_raw", side_effect=_fetch_side_effect):
                            with patch(
                                "src.orchestrator.agentic._extract_facts_with_llm",
                                return_value=(
                                    [
                                        {
                                            "session_id": "s1",
                                            "cycle_index": 1,
                                            "target_id": "auto-fetch-1",
                                            "url": "https://conferences.sigcomm.org/sigcomm/2025/program/",
                                            "url_title": "",
                                            "paper_title": "Preventing Network Bottlenecks: Accelerating Datacenter Services with Hotspot-Aware Placement for Compute and Storage",
                                            "doi": "",
                                            "arxiv_id": "",
                                            "year": "2025",
                                            "filters": {"institution": "Google", "year_gte": 2025},
                                            "evidence": "Preventing Network Bottlenecks ... Google",
                                            "score": 0.9,
                                            "status": "ok",
                                            "extract_source": "llm",
                                        }
                                    ],
                                    {"response_items_count": 1},
                                ),
                            ):
                                run_step(
                                    "demo",
                                    "retrieve-agentic",
                                    prompt="papers by google at SIGCOMM and NSDI in 2025",
                                    top_n=3,
                                )

            rdir = ws / "demo" / "artifacts" / "retrieval"
            result = yamlx.load(rdir / "agentic_result.yaml")
            self.assertEqual(result["status"], "completed")
            self.assertGreaterEqual(len(result.get("papers") or []), 1)
            first = (result.get("papers") or [])[0]
            self.assertIn("preventing network bottlenecks", str(first.get("title") or "").lower())
            self.assertEqual(
                str(first.get("source_url") or ""),
                "https://conferences.sigcomm.org/sigcomm/2025/program/",
            )

    def test_agentic_extract_content_does_not_fallback_to_deterministic_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "workspace"
            ws.mkdir(parents=True, exist_ok=True)
            with patch("src.utils.paths.WORKSPACE_ROOT", ws):
                run_step("demo", "init", theme="systems")
                with patch.dict("os.environ", {"SEARXNG_URL": "http://searxng:8080", "DS_API_KEY": "dummy"}, clear=False):
                    with patch(
                        "src.orchestrator.agentic._agent_next_action_llm",
                        side_effect=[
                            _agent_reply(
                                action="extract_content",
                                params={
                                    "auto_fetch": True,
                                    "urls": ["https://conferences.sigcomm.org/sigcomm/2025/accepted-papers/"],
                                    "filters": {"institution": "Google", "year_gte": 2025},
                                },
                                stop=True,
                                reason="done",
                            )
                        ],
                    ):
                        with patch(
                            "src.orchestrator.agentic._fetch_url_raw",
                            return_value=(
                                "<html><body><ul><li>ParserHawk: Hardware-aware parser generator using program synthesis "
                                "Xiangyu Gao; Bili Dong (Google)</li></ul></body></html>",
                                "text/html",
                            ),
                        ):
                            with patch(
                                "src.orchestrator.agentic._extract_facts_with_llm",
                                side_effect=TimeoutError("timed out"),
                            ):
                                run_step(
                                    "demo",
                                    "retrieve-agentic",
                                    prompt="papers by google at SIGCOMM in 2025",
                                    top_n=3,
                                )

            rdir = ws / "demo" / "artifacts" / "retrieval"
            result = yamlx.load(rdir / "agentic_result.yaml")
            self.assertEqual(result["status"], "completed")
            self.assertEqual(len(result.get("papers") or []), 0)

    def test_agentic_extract_content_reuses_fetch_and_skips_failed_page_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "workspace"
            ws.mkdir(parents=True, exist_ok=True)
            llm_batches: list[int] = []
            with patch("src.utils.paths.WORKSPACE_ROOT", ws):
                run_step("demo", "init", theme="systems")
                with patch.dict("os.environ", {"SEARXNG_URL": "http://searxng:8080", "DS_API_KEY": "dummy"}, clear=False):
                    with patch(
                        "src.orchestrator.agentic._agent_next_action_llm",
                        side_effect=[
                            _agent_reply(
                                action="extract_content",
                                params={
                                    "auto_fetch": True,
                                    "urls": ["https://conferences.sigcomm.org/sigcomm/2025/accepted-papers/"],
                                    "filters": {"institution": "Google", "year_gte": 2025},
                                    "coverage": {"batch_size": 8, "continue_until_exhausted": True, "max_passes": 4},
                                },
                            ),
                            _agent_reply(
                                action="extract_content",
                                params={
                                    "auto_fetch": True,
                                    "urls": ["https://conferences.sigcomm.org/sigcomm/2025/accepted-papers/"],
                                    "filters": {"institution": "Google", "year_gte": 2025},
                                    "coverage": {"batch_size": 8, "continue_until_exhausted": True, "max_passes": 4},
                                },
                                stop=True,
                                reason="done",
                            ),
                        ],
                    ):
                        with patch(
                            "src.orchestrator.agentic._fetch_url_raw",
                            return_value=(
                                "<html><body><ul><li>ParserHawk: Hardware-aware parser generator using program synthesis</li></ul></body></html>",
                                "text/html",
                            ),
                        ) as fetch_mock:
                            with patch(
                                "src.orchestrator.agentic._extract_text_segments",
                                return_value=[f"segment {idx}" for idx in range(40)],
                            ):

                                def _extract_side_effect(*, segments, **kwargs):
                                    llm_batches.append(len(segments))
                                    if len(llm_batches) == 1:
                                        return (
                                            [
                                                {
                                                    "session_id": "s1",
                                                    "cycle_index": 1,
                                                    "target_id": "auto-fetch-1",
                                                    "url": "https://conferences.sigcomm.org/sigcomm/2025/accepted-papers/",
                                                    "url_title": "",
                                                    "paper_title": "ParserHawk: Hardware-aware parser generator using program synthesis",
                                                    "doi": "",
                                                    "arxiv_id": "",
                                                    "year": "2025",
                                                    "filters": {"institution": "Google", "year_gte": 2025},
                                                    "evidence": "ParserHawk ... Bili Dong (Google)",
                                                    "score": 0.9,
                                                    "status": "ok",
                                                    "extract_source": "llm",
                                                }
                                            ],
                                            {"response_items_count": 1},
                                        )
                                    raise TimeoutError("timed out")

                                with patch(
                                    "src.orchestrator.agentic._extract_facts_with_llm",
                                    side_effect=_extract_side_effect,
                                ):
                                    run_step(
                                        "demo",
                                        "retrieve-agentic",
                                        prompt="papers by google at SIGCOMM in 2025",
                                        top_n=3,
                                    )

            rdir = ws / "demo" / "artifacts" / "retrieval"
            result = yamlx.load(rdir / "agentic_result.yaml")
            self.assertEqual(result["status"], "completed")
            self.assertEqual(fetch_mock.call_count, 1)
            self.assertEqual(llm_batches, [32, 8])
            self.assertEqual(len(result.get("papers") or []), 1)
            self.assertIn("parserhawk", str((result.get("papers") or [])[0].get("title") or "").lower())

    def test_conference_query_uses_venue_gate_and_keeps_fallback_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "workspace"
            ws.mkdir(parents=True, exist_ok=True)
            with patch("src.utils.paths.WORKSPACE_ROOT", ws):
                run_step("demo", "init", theme="systems")
                with patch.dict("os.environ", {"SEARXNG_URL": "http://searxng:8080", "DS_API_KEY": "dummy"}, clear=False):
                    with patch(
                        "src.orchestrator.agentic._agent_next_action_llm",
                        side_effect=[
                            _agent_reply(
                                action="extract_content",
                                params={
                                    "auto_fetch": True,
                                    "urls": ["https://openreview.net/profile?id=~YuYue2"],
                                    "filters": {"institution": "Bytedance", "year_gte": 2025},
                                },
                                stop=True,
                                reason="done",
                            )
                        ],
                    ):
                        with patch(
                            "src.orchestrator.agentic._fetch_url_raw",
                            return_value=(
                                "<html><body>ByteDance Systems Architecture Research Profile 2025. Researcher at Bytedance. Publications listed.</body></html>",
                                "text/html",
                            ),
                        ):
                            run_step(
                                "demo",
                                "retrieve-agentic",
                                prompt="papers by Bytedance at top conferences in 2025",
                                top_n=3,
                            )

            rdir = ws / "demo" / "artifacts" / "retrieval"
            result = yamlx.load(rdir / "agentic_result.yaml")
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result.get("papers") or [], [])

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
            result = yamlx.load(rdir / "agentic_result.yaml")
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
            result = yamlx.load(rdir / "agentic_result.yaml")
            self.assertEqual(result["status"], "failed")
            self.assertIn("missing_api_key:DS_API_KEY", result["stop_reason"])

    # Verify the extracted trace helper writes compact action results and extract debug data.
    def test_trace_record_cycle_summary_adds_extract_debug(self):
        cycle_trace = [{"action_result": {}, "delta": {}}]
        delta = trace_mod._record_cycle_summary(
            cycle_trace,
            selected_action="extract_content",
            action_result={
                "status": "ok",
                "notes": "finished extraction",
                "extract_windows_trace": [
                    {
                        "target_id": "t1",
                        "url": "https://conf.example/program",
                        "segments_done": 3,
                        "segments_pending": 1,
                        "segment_total": 4,
                        "llm_requests": 2,
                        "llm_items": [{"paper_title": "Paper A"}],
                    }
                ],
            },
            raw_candidates=[],
            extracted_paper_candidates=[{"title": "Paper A"}],
            url_shortlisted=[],
        )
        self.assertEqual(delta["shortlisted_count"], 1)
        self.assertEqual(cycle_trace[-1]["action_result"]["status"], "ok")
        self.assertEqual(cycle_trace[-1]["action_debug"]["targets"][0]["llm_items"], ["Paper A"])

    # Verify the extracted trace helper attaches raw-event and fetch-artifact refs for downstream UI/debug views.
    def test_trace_record_cycle_refs_attaches_fetch_paths(self):
        cycle_trace = [{}]
        trace_mod._record_cycle_refs(
            cycle_trace,
            fetched_records=[{"raw_path": "fetch_raw/cycle01-page.html"}, {"raw_path": ""}],
            raw_event_ids=["raw-000001", "raw-000002"],
        )
        self.assertEqual(cycle_trace[-1]["refs"]["raw_event_ids"], ["raw-000001", "raw-000002"])
        self.assertEqual(cycle_trace[-1]["refs"]["fetch_raw_paths"], ["fetch_raw/cycle01-page.html"])

    def test_state_apply_build_extract_coverage_summary_counts_completion(self):
        summary = state_apply_mod._build_extract_coverage_summary(
            {
                "https://a.example": {"target_id": "t1", "segments_done": 3, "segment_total": 3, "coverage_has_more": False},
                "https://b.example": {"target_id": "t2", "segments_done": 1, "segment_total": 4, "coverage_has_more": True},
            }
        )
        self.assertEqual(summary["shortlisted_urls_total"], 2)
        self.assertEqual(summary["shortlisted_urls_complete"], 1)
        self.assertEqual(summary["shortlisted_urls_with_more_results"], 1)

    def test_state_apply_search_action_result_merges_hits(self):
        raw_candidates = [
            {
                "source": "searxng",
                "source_id": "https://conf.example/program",
                "title": "NSDI 2025 Accepted Papers",
                "venue": "NSDI",
                "year": "2025",
                "url": "https://conf.example/program",
                "abstract": "Official program page.",
                "keywords": [],
                "categories": [],
                "score": 2.0,
                "reason": "searxng_search",
                "query_used": "NSDI 2025 accepted papers",
                "_snippet": "Official program page.",
            }
        ]
        finalized = state_apply_mod._apply_search_action_result(
            session_id="sess-1",
            cycle_index=1,
            raw_candidates=raw_candidates,
            shortlist_hints={"prefer": ["venue_program_pages"]},
            existing_url_hits=[],
            shortlist_size=2,
            max_cycles=3,
        )
        self.assertEqual(len(finalized["url_shortlisted"]), 1)
        self.assertEqual(finalized["url_hits"][0]["url"], "https://conf.example/program")

    def test_state_apply_extract_candidate_results_splits_venue_scoped_candidates(self):
        finalized = state_apply_mod._apply_extract_candidate_results(
            prompt="papers at NSDI 2025",
            agent_plan={"cue_breakdown": ["venue:NSDI"]},
            existing_final_candidates=[],
            existing_fallback_candidates=[],
            extracted_paper_candidates=[
                {
                    "title": "Paper On The Program",
                    "url": "https://conf.example/accepted",
                    "score": 0.9,
                    "abstract": "Accepted papers at the conference.",
                },
                {
                    "title": "Loose Candidate",
                    "url": "https://author.example/profile",
                    "score": 0.3,
                    "abstract": "Author profile page with no venue cues.",
                },
            ],
        )
        self.assertEqual(len(finalized["final_candidates"]), 1)
        self.assertEqual(len(finalized["fallback_candidates"]), 1)

    def test_resolve_extract_anchor_terms_prefers_agent_supplied_terms(self):
        terms = agentic_mod._resolve_extract_anchor_terms(
            params={"anchor_terms": ["Google", "NDD", "technical sessions"]},
            filters={"institution": "Google", "topic": "formal verification"},
            intent={},
            user_prompt="papers by Google at NSDI in 2025",
        )
        self.assertEqual(terms, ["Google", "NDD", "technical sessions"])

    def test_sanitize_agent_action_params_removes_extract_micropolicy(self):
        params = agentic_mod._sanitize_agent_action_params(
            "extract_content",
            {
                "urls": ["https://example.com/program"],
                "filters": {"institution": "Google", "year_gte": 2025},
                "anchor_terms": ["Google", "NSDI"],
                "intent": {"query_goal": "find papers"},
                "auto_fetch": True,
                "coverage": {"batch_size": 8, "continue_until_exhausted": True, "max_passes": 9},
                "max_calls_per_target": 99,
            },
        )
        self.assertEqual(params["urls"], ["https://example.com/program"])
        self.assertEqual(params["anchor_terms"], ["Google", "NSDI"])
        self.assertNotIn("auto_fetch", params)
        self.assertNotIn("coverage", params)
        self.assertNotIn("max_calls_per_target", params)

    def test_sanitize_agent_action_params_normalizes_extract_targets(self):
        params = agentic_mod._sanitize_agent_action_params(
            "extract_content",
            {
                "targets": [
                    {
                        "url": "https://example.com/program",
                        "anchor_terms": ["Google", "Nandita Dukkipati"],
                        "match": {
                            "institution_any": ["Google", "Google LLC"],
                            "year_gte": "2025",
                        },
                        "coverage": {"batch_size": 8},
                    }
                ],
            },
        )
        self.assertEqual(
            params,
            {
                "targets": [
                    {
                        "url": "https://example.com/program",
                        "title": "",
                        "why": "",
                        "anchor_terms": ["Google", "Nandita Dukkipati"],
                        "match": {"institution_any": ["Google", "Google LLC"], "year_gte": "2025"},
                        "filters": {"institution": "Google", "year_gte": 2025},
                    }
                ]
            },
        )

    def test_build_agent_working_state_is_compact(self):
        working = agentic_mod._build_agent_working_state(
            user_prompt="papers by Google at SIGCOMM and NSDI in 2025",
            cycle_index=3,
            max_cycles=5,
            agent_memory={
                "active_step": {"step_id": "step3", "action": "extract_content", "goal": "Extract candidate papers"},
                "todo": {"total": 1, "doing": 1},
                "known_urls": [{"url": "https://sigcomm.example", "status": "in_progress"}],
                "matched_papers": [{"title": "Falcon", "url": "https://sigcomm.example/falcon"}],
                "last_step": {"cycle_index": 2, "action": "search_web", "status": "ok"},
                "last_change": {"retrieved_count": 12},
            },
        )
        self.assertEqual(working["cycle"], {"index": 3, "max": 5})
        self.assertIn("memory", working)
        self.assertIn("active_step", working["memory"])
        self.assertNotIn("summary", working)

    def test_build_agent_memory_includes_last_step_and_urls(self):
        memory = agentic_mod._build_agent_memory(
            user_prompt="papers by Google at SIGCOMM and NSDI in 2025",
            plan_state={
                "active_step": {"step_id": "step3", "action": "extract_content", "goal": "Extract candidate papers"},
                "todo": [{"todo_id": "todo2", "action": "extract_content", "status": "doing", "target": "official venue pages"}],
            },
            url_hits=[
                {
                    "url": "https://sigcomm.example",
                    "url_title": "SIGCOMM 2025 accepted papers",
                    "host": "sigcomm.example",
                    "peek": "Google, Alibaba, Meta",
                }
            ],
            extract_state_by_url={
                "https://sigcomm.example": {"fetched": True, "segments_done": 32},
                "https://nsdi.example": {"failed": True, "last_error": "timeout"},
            },
            papers=[{"title": "Falcon", "url": "https://sigcomm.example/falcon", "year": "2025"}],
            cycle_trace=[
                {
                    "cycle_index": 2,
                    "selected_action": "search_web",
                    "action_result": {"status": "ok", "notes": "searched official pages"},
                    "delta": {"retrieved_count": 12, "shortlisted_count": 4, "final_candidate_delta": 1},
                }
            ],
            stop_reason="",
        )
        self.assertEqual(memory["active_step"]["step_id"], "step3")
        self.assertEqual(memory["known_urls"][0]["status"], "in_progress")
        self.assertEqual(memory["matched_papers"][0]["title"], "Falcon")
        self.assertEqual(memory["last_step"]["action"], "search_web")
        self.assertEqual(memory["last_change"]["retrieved_count"], 12)
        self.assertTrue(any("timeout" in item for item in memory["blockers"]))

    def test_apply_plan_update_merges_deltas(self):
        merged = agentic_mod._apply_plan_update(
            {
                "active_step": {"step_id": "step1", "action": "search_web", "goal": "Find venue pages"},
                "todo": [{"todo_id": "todo1", "action": "search_web", "status": "doing", "target": "venue pages"}],
            },
            {
                "active_step": {"step_id": "step2", "action": "extract_content", "goal": "Extract matched papers"},
                "todo_updates": [{"todo_id": "todo1", "status": "done"}],
                "todo_append": [{"todo_id": "todo2", "action": "extract_content", "status": "doing", "target": "official pages"}],
            },
        )
        self.assertEqual(merged["active_step"]["step_id"], "step2")
        self.assertEqual(merged["active_step"]["action"], "extract_content")
        todo_by_id = {str(item.get("todo_id")): item for item in merged["todo"]}
        self.assertEqual(str(todo_by_id["todo1"].get("status")), "done")
        self.assertEqual(str(todo_by_id["todo2"].get("status")), "doing")

    def test_parse_agent_action_response_accepts_new_envelope(self):
        parsed = agentic_mod._parse_agent_action_response(
            payload={
                "decision": {"mode": "continue", "reason": "need venue pages"},
                "state_delta": {
                    "active_step": {"step_id": "step1", "action": "search_web", "goal": "Find official venue pages"},
                    "todo_append": [{"todo_id": "todo1", "action": "search_web", "status": "doing", "target": "official venue pages"}],
                },
                "progress": {"phase": "searching", "status": "running", "note": "Searching official venue pages"},
                "action": {
                    "name": "search_web",
                    "params": {"queries": ["SIGCOMM 2025 accepted papers", "NSDI 2025 accepted papers"]},
                },
            },
            queries_per_cycle=4,
            supported_actions={"search_web", "extract_content"},
            debug_metrics={"input_chars": 100, "input_tokens_est": 25},
        )
        self.assertEqual(parsed["action"], "search_web")
        self.assertEqual(parsed["decision"]["mode"], "continue")
        self.assertEqual(parsed["decision"]["reason"], "need venue pages")
        self.assertEqual(parsed["state_delta"]["active_step"]["step_id"], "step1")
        self.assertEqual(parsed["progress"]["phase"], "searching")
        self.assertEqual(parsed["progress"]["step_id"], "step1")
        self.assertEqual(parsed["queries"], ["SIGCOMM 2025 accepted papers", "NSDI 2025 accepted papers"])


if __name__ == "__main__":
    unittest.main()
