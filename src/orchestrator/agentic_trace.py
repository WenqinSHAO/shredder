from __future__ import annotations

from typing import Any

from src.orchestrator.agentic_search import _peek_text, _unique_nonempty


# Build the compact extract debug payload that downstream trajectory views can project.
def _summarize_extract_action_debug(action_result: dict[str, Any]) -> dict[str, Any]:
    extract_trace = action_result.get("extract_windows_trace") if isinstance(action_result.get("extract_windows_trace"), list) else []
    return {
        "targets": [
            {
                "target_id": str(item.get("target_id") or ""),
                "url": str(item.get("url") or ""),
                "window_count": int(item.get("window_count") or 0),
                "segment_total": int(item.get("segment_total") or 0),
                "segment_batch_size": int(item.get("segment_batch_size") or 0),
                "batch_mode": str(item.get("batch_mode") or ""),
                "input_token_budget": int(item.get("input_token_budget") or 0),
                "segments_done": int(item.get("segments_done") or 0),
                "segments_pending": int(item.get("segments_pending") or 0),
                "coverage_pct": float(item.get("coverage_pct") or 0.0),
                "llm_requests": int(item.get("llm_requests") or 0),
                "llm_responses": int(item.get("llm_responses") or 0),
                "llm_empty_responses": int(item.get("llm_empty_responses") or 0),
                "llm_errors": int(item.get("llm_errors") or 0),
                "last_input_tokens_est": int(item.get("last_input_tokens_est") or 0),
                "last_segment_tokens_est": int(item.get("last_segment_tokens_est") or 0),
                "last_scaffold_tokens_est": int(item.get("last_scaffold_tokens_est") or 0),
                "llm_items_count": int(item.get("llm_items_count") or 0),
                "llm_items": [
                    str(row.get("paper_title") or "")
                    for row in (item.get("llm_items") or [])[:10]
                    if isinstance(row, dict)
                ],
            }
            for item in extract_trace[:8]
            if isinstance(item, dict)
        ],
    }


# Update the append-only cycle trace row; upstream is loop finalization and downstream is trajectory/debug projection.
def _record_cycle_summary(
    cycle_trace: list[dict[str, Any]],
    *,
    selected_action: str,
    action_result: dict[str, Any],
    raw_candidates: list[dict[str, Any]],
    extracted_paper_candidates: list[dict[str, Any]],
    url_shortlisted: list[dict[str, Any]],
) -> dict[str, Any]:
    cycle_delta = {
        "retrieved_count": len(raw_candidates),
        "shortlisted_count": len(url_shortlisted) if selected_action == "search_web" else len(extracted_paper_candidates),
        "final_candidate_delta": 0,
    }
    if cycle_trace:
        cycle_trace[-1]["action_result"] = {
            "status": str(action_result.get("status") or ""),
            "notes": _peek_text(str(action_result.get("notes") or ""), 220),
        }
        if selected_action == "extract_content":
            cycle_trace[-1]["action_debug"] = _summarize_extract_action_debug(action_result)
        cycle_trace[-1]["delta"] = cycle_delta
    return cycle_delta


# Attach compact artifact/raw refs to the current cycle trace row for downstream debugging and UI surfaces.
def _record_cycle_refs(
    cycle_trace: list[dict[str, Any]],
    *,
    fetched_records: list[dict[str, Any]],
    raw_event_ids: list[str],
) -> None:
    if not cycle_trace:
        return
    fetch_raw_paths = _unique_nonempty([str(row.get("raw_path") or "") for row in fetched_records], limit=12)
    cycle_trace[-1]["refs"] = {
        "raw_event_ids": list(raw_event_ids),
        "fetch_raw_paths": fetch_raw_paths,
    }
