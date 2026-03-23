from __future__ import annotations

from typing import Any

from src.orchestrator.agentic_result import (
    _has_venue_evidence,
    _merge_paper_candidates,
    _requires_venue_evidence,
)
from src.orchestrator.agentic_search import (
    _apply_shortlist_hints,
    _filter_search_rows,
    _merge_url_hits,
    _rank_candidates,
    _select_diverse_shortlist,
    _to_url_hits,
)


# Read the compact final-candidate projection from paper state.
def _paper_final_candidates(paper_state: Any) -> list[dict[str, Any]]:
    return [row for row in getattr(paper_state, "final_candidates", []) if isinstance(row, dict)]


# Read the compact fallback-candidate projection from paper state.
def _paper_fallback_candidates(paper_state: Any) -> list[dict[str, Any]]:
    return [row for row in getattr(paper_state, "fallback_candidates", []) if isinstance(row, dict)]


# Replace paper-state candidate lists after loop-side merging decisions.
def _set_paper_candidates(
    paper_state: Any,
    *,
    final_candidates: list[dict[str, Any]],
    fallback_candidates: list[dict[str, Any]],
) -> None:
    paper_state.final_candidates = [dict(row) for row in final_candidates if isinstance(row, dict)]
    paper_state.fallback_candidates = [dict(row) for row in fallback_candidates if isinstance(row, dict)]


# Project extract-state progress into the compact result coverage contract.
def _build_extract_coverage_summary(extract_state_by_url: dict[str, dict[str, Any]]) -> dict[str, Any]:
    url_checks: list[dict[str, Any]] = []
    for url, row in dict(extract_state_by_url or {}).items():
        if not str(url).strip() or not isinstance(row, dict):
            continue
        segments_done = int(row.get("segments_done") or 0)
        segment_total = int(row.get("segment_total") or 0)
        has_more = bool(row.get("coverage_has_more"))
        url_checks.append(
            {
                "url": str(url),
                "target_id": str(row.get("target_id") or ""),
                "segments_done": segments_done,
                "segment_total": segment_total,
                "all_papers_extracted": segment_total > 0 and segments_done >= segment_total and not has_more,
                "has_more_results": has_more,
            }
        )
    return {
        "shortlisted_urls_total": len(url_checks),
        "shortlisted_urls_complete": sum(1 for row in url_checks if bool(row.get("all_papers_extracted"))),
        "shortlisted_urls_with_more_results": sum(1 for row in url_checks if bool(row.get("has_more_results"))),
        "url_checks": url_checks,
    }


# Apply per-URL extract progress from an action result back onto loop extract state.
def _apply_extract_coverage_update(
    extract_state_by_url: dict[str, dict[str, Any]],
    *,
    action_result: dict[str, Any],
) -> None:
    extract_trace = action_result.get("extract_windows_trace")
    if not isinstance(extract_trace, list):
        extract_trace = []
    for item in extract_trace:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        page_state = extract_state_by_url.setdefault(url, {})
        page_state["target_id"] = str(item.get("target_id") or page_state.get("target_id") or "")
        page_state["segments_done"] = int(item.get("segments_done") or page_state.get("segments_done") or 0)
        page_state["segment_total"] = int(item.get("segment_total") or page_state.get("segment_total") or 0)
        page_state["coverage_has_more"] = bool(item.get("coverage_has_more"))


# Apply search results into persisted URL-hit state; upstream is search execution and downstream is later extract targeting.
def _apply_search_action_result(
    *,
    session_id: str,
    cycle_index: int,
    raw_candidates: list[dict[str, Any]],
    shortlist_hints: Any,
    existing_url_hits: list[dict[str, Any]],
    shortlist_size: int,
    max_cycles: int,
    to_url_hits_fn: Any = _to_url_hits,
) -> dict[str, Any]:
    filtered_rows, _ = _filter_search_rows(raw_candidates)
    ranked_rows = _rank_candidates(filtered_rows)
    ranked_rows = _apply_shortlist_hints(ranked_rows, shortlist_hints)
    url_shortlisted = _select_diverse_shortlist(ranked_rows, shortlist_size)
    url_hits = to_url_hits_fn(session_id, cycle_index, url_shortlisted, shortlist_size)
    merged_url_hits = _merge_url_hits(
        [row for row in existing_url_hits if isinstance(row, dict)],
        url_hits,
        limit=max(shortlist_size * max(2, max_cycles), 16),
    )
    return {
        "url_shortlisted": url_shortlisted,
        "url_hits": merged_url_hits,
    }


# Apply extracted paper candidates into final/fallback paper-state lists for result writing.
def _apply_extract_candidate_results(
    *,
    prompt: str,
    agent_plan: dict[str, Any],
    existing_final_candidates: list[dict[str, Any]],
    existing_fallback_candidates: list[dict[str, Any]],
    extracted_paper_candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    cycle_candidates = [row for row in extracted_paper_candidates if isinstance(row, dict)]
    requires_venue = _requires_venue_evidence(prompt, agent_plan)
    if requires_venue:
        venue_scoped = [row for row in cycle_candidates if _has_venue_evidence(row)]
        fallback_scoped = [row for row in cycle_candidates if not _has_venue_evidence(row)]
        return {
            "paper_candidates": cycle_candidates,
            "final_candidates": _merge_paper_candidates(existing_final_candidates, venue_scoped),
            "fallback_candidates": _merge_paper_candidates(existing_fallback_candidates, fallback_scoped),
        }
    return {
        "paper_candidates": cycle_candidates,
        "final_candidates": _merge_paper_candidates(existing_final_candidates, cycle_candidates),
        "fallback_candidates": list(existing_fallback_candidates),
    }
