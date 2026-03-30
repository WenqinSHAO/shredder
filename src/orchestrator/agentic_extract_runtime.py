from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from src.orchestrator.agentic_extract_prepare import (
    prepare_extract_target as _prepare_extract_target_impl,
    resolve_extract_request as _resolve_extract_request_impl,
)

ProgressCallback = Callable[[dict], None]

DEFAULT_EXTRACT_BATCH_SIZE = 6
DEFAULT_EXTRACT_MAX_CALLS = 6
LISTING_EXTRACT_BATCH_CAP = 4
DETAIL_EXTRACT_BATCH_CAP = 3


def _effective_extract_batch_size(
    *,
    coverage_batch_size: int,
    batch_mode: str,
    segment_count: int,
) -> int:
    base = max(1, min(int(coverage_batch_size or 0), max(1, int(segment_count or 0))))
    hard_cap = LISTING_EXTRACT_BATCH_CAP if str(batch_mode or "") == "page" else DETAIL_EXTRACT_BATCH_CAP
    return max(1, min(base, hard_cap))


def _auto_fetch_counts(auto_fetched_records: list[dict[str, Any]]) -> tuple[int, int]:
    ok = sum(1 for row in auto_fetched_records if str(row.get("status") or "") == "ok")
    error = sum(1 for row in auto_fetched_records if str(row.get("status") or "") != "ok")
    return ok, error


def _build_extract_no_records_result(
    *,
    requested_urls: list[str],
    extract_intent: dict[str, Any],
    auto_fetched_records: list[dict[str, Any]],
) -> dict[str, Any]:
    requested_count = len([url for url in requested_urls if str(url).strip()])
    auto_fetched_ok, auto_fetched_error = _auto_fetch_counts(auto_fetched_records)
    return {
        "status": "error",
        "tool_calls": "extract:0",
        "web_rows": [],
        "raw_candidates": [],
        "paper_candidates": [],
        "candidate_urls": [],
        "stop": False,
        "stop_reason": "",
        "notes": f"extract_failed_no_fetched_records requested_urls={requested_count}",
        "extracted_records": [],
        "coverage_has_more": False,
        "coverage_passes": 0,
        "extract_timeout_errors": 0,
        "extract_empty_semantic": 0,
        "extract_windows_trace": [],
        "extract_intent": extract_intent,
        "auto_fetched_count": len(auto_fetched_records),
        "auto_fetched_ok": auto_fetched_ok,
        "auto_fetched_error": auto_fetched_error,
    }


def _build_extract_action_result(
    *,
    facts: list[dict[str, Any]],
    paper_candidates: list[dict[str, Any]],
    candidate_urls: list[dict[str, Any]],
    requested_urls: list[str],
    llm_extract_attempted: int,
    llm_extract_applied: int,
    llm_timeout_errors: int,
    llm_empty_semantic: int,
    coverage_has_more: bool,
    coverage_passes: int,
    extract_windows_trace: list[dict[str, Any]],
    extract_intent: dict[str, Any],
    auto_fetched_records: list[dict[str, Any]],
    paper_dedup_clusters: int = 0,
    paper_dedup_reduced: int = 0,
) -> dict[str, Any]:
    auto_fetched_ok, auto_fetched_error = _auto_fetch_counts(auto_fetched_records)
    return {
        "status": "ok",
        "tool_calls": f"extract:{len(facts)}",
        "web_rows": [],
        "raw_candidates": [],
        "paper_candidates": paper_candidates,
        "candidate_urls": candidate_urls,
        "stop": False,
        "stop_reason": "",
        "notes": (
            f"requested_urls={len(requested_urls)} extracted_rows={len(facts)} "
            f"llm_extract_attempted={llm_extract_attempted} llm_extract_applied={llm_extract_applied} "
            f"llm_timeout_errors={llm_timeout_errors} llm_empty_semantic={llm_empty_semantic} "
            f"coverage_has_more={coverage_has_more} coverage_passes={coverage_passes} "
            f"candidate_urls={len(candidate_urls)} "
            f"paper_dedup_clusters={paper_dedup_clusters} paper_dedup_reduced={paper_dedup_reduced}"
        ),
        "extracted_records": facts,
        "coverage_has_more": coverage_has_more,
        "coverage_passes": coverage_passes,
        "extract_timeout_errors": llm_timeout_errors,
        "extract_empty_semantic": llm_empty_semantic,
        "extract_windows_trace": extract_windows_trace,
        "extract_intent": extract_intent,
        "auto_fetched_count": len(auto_fetched_records),
        "auto_fetched_ok": auto_fetched_ok,
        "auto_fetched_error": auto_fetched_error,
        "paper_dedup_clusters": paper_dedup_clusters,
        "paper_dedup_reduced": paper_dedup_reduced,
    }


def _new_extract_window_trace(
    *,
    prepared: dict[str, Any],
    extract_intent: dict[str, Any],
    coverage_batch_size: int,
) -> dict[str, Any]:
    row = prepared["row"]
    ranked_segments = list(prepared["ranked_segments"])
    target_extract_intent = prepared.get("extract_intent") if isinstance(prepared.get("extract_intent"), dict) else extract_intent
    return {
        "target_id": str(row.get("target_id") or ""),
        "url": str(row.get("url") or ""),
        "url_title": str(row.get("url_title") or ""),
        "status": str(row.get("status") or ""),
        "filters": dict(prepared["filters"]),
        "anchor_terms": list(prepared["anchor_terms"]),
        "intent": dict(target_extract_intent or {}),
        "window_count": len(ranked_segments),
        "windows": ranked_segments[: min(12, len(ranked_segments))],
        "segment_total": len(ranked_segments),
        "segment_batch_size": coverage_batch_size,
        "batch_mode": str(prepared["batch_mode"]),
        "input_token_budget": int(prepared["token_budget"]),
        "llm_items_count": 0,
        "llm_items": [],
        "segments_done": 0,
        "segments_pending": len(ranked_segments),
        "llm_requests": 0,
        "llm_responses": 0,
        "llm_empty_responses": 0,
        "llm_errors": 0,
        "llm_timeout_errors": 0,
        "coverage_pct": 0.0,
    }


def _merge_extracted_facts(
    *,
    llm_facts: list[dict[str, Any]],
    cycle_index: int,
    progress_callback: ProgressCallback | None,
    deps: dict[str, Any],
) -> list[dict[str, Any]]:
    candidate_dedup_key_fn = deps["candidate_dedup_key_fn"]
    emit_progress_fn = deps["emit_progress_fn"]

    def _fact_rank_key(fact: dict[str, Any]) -> tuple[int, int, float, int]:
        status = str(fact.get("status") or "")
        status_rank = 3 if status == "ok" else (2 if status == "weak_signal" else 1)
        score = float(fact.get("score") or 0.0)
        title_len = len(str(fact.get("paper_title") or ""))
        evidence_len = len(str(fact.get("evidence") or ""))
        return (status_rank, int(score * 1000), evidence_len, title_len)

    by_key: dict[str, dict[str, Any]] = {}
    for fact in llm_facts:
        key = candidate_dedup_key_fn(
            {
                "source": "agentic_extract",
                "reason": "extract_content",
                "title": fact.get("paper_title"),
                "year": fact.get("year"),
                "doi": fact.get("doi"),
                "arxiv_id": fact.get("arxiv_id"),
                "url": fact.get("url"),
            }
        )
        prev = by_key.get(key)
        if prev is None or _fact_rank_key(fact) > _fact_rank_key(prev):
            by_key[key] = fact
    facts = list(by_key.values())
    emit_progress_fn(
        progress_callback,
        event="agentic_extract_stage",
        cycle_index=cycle_index,
        stage="merge_results",
        llm_count=len(llm_facts),
        merged_count=len(facts),
    )
    return facts


def _run_prepared_extract_target(
    *,
    cycle_index: int,
    prepared: dict[str, Any],
    trace_entry: dict[str, Any],
    user_prompt: str,
    extract_intent: dict[str, Any],
    llm_extractor_model: str,
    llm_api_key_env: str,
    progress_callback: ProgressCallback | None,
    runtime_state: dict[str, Any],
    extract_state_by_url: dict[str, Any],
    raw_event_fn: Callable[[str, Any, list[str] | None], str] | None,
    coverage_batch_size: int,
    max_calls_per_target: int,
    deps: dict[str, Any],
) -> dict[str, Any]:
    emit_progress_fn = deps["emit_progress_fn"]
    slice_segments_by_token_budget_fn = deps["slice_segments_by_token_budget_fn"]
    next_op_id_fn = deps["next_op_id_fn"]
    extract_facts_with_llm_fn = deps["extract_facts_with_llm_fn"]

    row = prepared["row"]
    active_filters = dict(prepared["filters"])
    row_anchor_terms = list(prepared["anchor_terms"])
    target_extract_intent = (
        dict(prepared.get("extract_intent"))
        if isinstance(prepared.get("extract_intent"), dict)
        else dict(extract_intent)
    )
    ranked_segments = list(prepared["ranked_segments"])
    effective_batch_mode = str(prepared["batch_mode"])
    segment_token_budget = int(prepared["token_budget"])
    if str(row.get("status") or "") != "ok":
        return {
            "facts": [],
            "llm_extract_attempted": 0,
            "llm_extract_applied": 0,
            "llm_timeout_errors": 0,
            "llm_empty_semantic": 0,
            "coverage_has_more": False,
            "coverage_passes": 0,
        }

    url_key = str(row.get("url") or "").strip()
    page_state = extract_state_by_url.setdefault(
        url_key,
        {
            "segments_done": 0,
            "segment_total": 0,
            "failed": False,
            "completed": False,
            "coverage_has_more": False,
            "last_error": "",
        },
    )
    if bool(page_state.get("failed")) or bool(page_state.get("completed")):
        trace_entry["status"] = "skipped"
        trace_entry["skip_reason"] = "failed" if bool(page_state.get("failed")) else "completed"
        trace_entry["failed"] = bool(page_state.get("failed"))
        trace_entry["completed"] = bool(page_state.get("completed"))
        trace_entry["coverage_has_more"] = bool(page_state.get("coverage_has_more"))
        trace_entry["last_error"] = str(page_state.get("last_error") or "")
        trace_entry["segments_done"] = int(page_state.get("segments_done") or 0)
        trace_entry["segments_pending"] = max(
            0,
            int(page_state.get("segment_total") or 0) - int(page_state.get("segments_done") or 0),
        )
        return {
            "facts": [],
            "llm_extract_attempted": 0,
            "llm_extract_applied": 0,
            "llm_timeout_errors": 0,
            "llm_empty_semantic": 0,
            "coverage_has_more": False,
            "coverage_passes": 0,
        }

    emit_progress_fn(
        progress_callback,
        event="agentic_extract_target_start",
        cycle_index=cycle_index,
        target_id=str(row.get("target_id") or ""),
        url=str(row.get("url") or ""),
    )
    page_state["segment_total"] = len(ranked_segments)
    effective_batch_size = _effective_extract_batch_size(
        coverage_batch_size=coverage_batch_size,
        batch_mode=effective_batch_mode,
        segment_count=len(ranked_segments),
    )
    start = max(0, min(int(page_state.get("segments_done") or 0), len(ranked_segments)))
    if start >= len(ranked_segments):
        page_state["completed"] = True
        page_state["coverage_has_more"] = False
        trace_entry["status"] = "skipped"
        trace_entry["skip_reason"] = "completed"
        trace_entry["failed"] = False
        trace_entry["completed"] = True
        trace_entry["coverage_has_more"] = False
        trace_entry["segments_done"] = start
        trace_entry["segments_pending"] = 0
        trace_entry["coverage_pct"] = 100.0
        trace_entry["anchor_terms"] = list(row_anchor_terms)
        trace_entry["filters"] = active_filters
        return {
            "facts": [],
            "llm_extract_attempted": 0,
            "llm_extract_applied": 0,
            "llm_timeout_errors": 0,
            "llm_empty_semantic": 0,
            "coverage_has_more": False,
            "coverage_passes": 0,
        }

    emit_progress_fn(
        progress_callback,
        event="agentic_extract_stage",
        cycle_index=cycle_index,
        target_id=str(row.get("target_id") or ""),
        url=str(row.get("url") or ""),
        stage="llm_prepare",
        segments_total=len([str(v) for v in (row.get("segments") or []) if str(v).strip()]) or len(ranked_segments),
        segments_ranked=len(ranked_segments),
        batch_mode=effective_batch_mode,
        token_budget=segment_token_budget,
    )

    llm_facts: list[dict[str, Any]] = []
    local_trace_items: list[dict[str, Any]] = []
    local_count = 0
    pass_count = 0
    llm_extract_attempted = 0
    llm_extract_applied = 0
    llm_timeout_errors = 0
    llm_empty_semantic = 0
    coverage_has_more = False
    coverage_passes = 0

    while start < len(ranked_segments):
        if pass_count >= max_calls_per_target:
            coverage_has_more = True
            break
        pass_count += 1
        coverage_passes += 1
        batch_start = start
        batch = slice_segments_by_token_budget_fn(
            ranked_segments,
            start=batch_start,
            max_segments=effective_batch_size,
            token_budget=segment_token_budget,
            min_segments=1 if effective_batch_mode == "page" else 2,
        )
        if not batch:
            break
        llm_extract_attempted += 1
        trace_entry["llm_requests"] = int(trace_entry.get("llm_requests") or 0) + 1
        emit_progress_fn(
            progress_callback,
            event="agentic_extract_batch_start",
            cycle_index=cycle_index,
            target_id=str(row.get("target_id") or ""),
            batch_start=start,
            batch_size=len(batch),
            segment_total=len(ranked_segments),
            batch_mode=effective_batch_mode,
            token_budget=segment_token_budget,
            pass_index=pass_count,
        )
        llm_raw_refs = [str(row.get("raw_path") or "")] if str(row.get("raw_path") or "") else []
        extracted: list[dict[str, Any]] = []
        llm_trace: dict[str, Any] = {}
        last_error = ""
        llm_op_id = next_op_id_fn(runtime_state, "extract_llm")
        if raw_event_fn is not None:
            raw_event_fn(
                "op_start",
                {
                    "op_id": llm_op_id,
                    "op_type": "extract_llm",
                    "component": "extract_content",
                    "target_id": str(row.get("target_id") or ""),
                    "url": str(row.get("url") or ""),
                    "batch_start": batch_start,
                    "batch_size": len(batch),
                    "segment_total": len(ranked_segments),
                    "batch_mode": effective_batch_mode,
                    "max_segments": len(batch),
                    "token_budget": segment_token_budget,
                    "model": llm_extractor_model,
                    "pass_index": pass_count,
                },
                llm_raw_refs,
            )
        try:
            extracted, llm_trace = extract_facts_with_llm_fn(
                record=row,
                filters=active_filters,
                user_prompt=user_prompt,
                model=llm_extractor_model,
                api_key_env=llm_api_key_env,
                intent=target_extract_intent,
                segments=batch,
                timeout_s=45.0,
                max_retries=0,
                raw_event_fn=(
                    (lambda event_type, payload: raw_event_fn(event_type, payload, llm_raw_refs))
                    if raw_event_fn is not None
                    else None
                ),
                llm_op_id=llm_op_id,
                batch_mode=effective_batch_mode,
            )
            if raw_event_fn is not None:
                raw_event_fn(
                    "op_end",
                    {
                        "op_id": llm_op_id,
                        "op_type": "extract_llm",
                        "component": "extract_content",
                        "target_id": str(row.get("target_id") or ""),
                        "url": str(row.get("url") or ""),
                        "status": "ok",
                        "batch_start": batch_start,
                        "batch_size": len(batch),
                        "segment_total": len(ranked_segments),
                        "extracted_count": len(extracted),
                        "batch_mode": effective_batch_mode,
                        "max_segments": len(batch),
                        "token_budget": segment_token_budget,
                        "input_tokens_est": int(llm_trace.get("input_tokens_est") or 0),
                        "segment_tokens_est": int(llm_trace.get("segment_tokens_est") or 0),
                        "scaffold_tokens_est": int(llm_trace.get("scaffold_tokens_est") or 0),
                        "model": llm_extractor_model,
                        "pass_index": pass_count,
                    },
                    llm_raw_refs,
                )
        except Exception as exc:
            last_error = f"{type(exc).__name__}:{exc}"
            trace_entry["llm_errors"] = int(trace_entry.get("llm_errors") or 0) + 1
            if "timeout" in last_error.lower():
                llm_timeout_errors += 1
                trace_entry["llm_timeout_errors"] = int(trace_entry.get("llm_timeout_errors") or 0) + 1
            page_state["failed"] = True
            page_state["completed"] = False
            page_state["coverage_has_more"] = False
            page_state["last_error"] = last_error
            page_state["segments_done"] = max(int(page_state.get("segments_done") or 0), batch_start)
            if raw_event_fn is not None:
                raw_event_fn(
                    "op_end",
                    {
                        "op_id": llm_op_id,
                        "op_type": "extract_llm",
                        "component": "extract_content",
                        "target_id": str(row.get("target_id") or ""),
                        "url": str(row.get("url") or ""),
                        "status": "error",
                        "batch_start": batch_start,
                        "batch_size": len(batch),
                        "segment_total": len(ranked_segments),
                        "batch_mode": effective_batch_mode,
                        "max_segments": len(batch),
                        "token_budget": segment_token_budget,
                        "error": last_error,
                        "model": llm_extractor_model,
                        "pass_index": pass_count,
                    },
                    llm_raw_refs,
                )
            emit_progress_fn(
                progress_callback,
                event="agentic_extract_batch_done",
                cycle_index=cycle_index,
                target_id=str(row.get("target_id") or ""),
                extracted_count=0,
                pass_index=pass_count,
                error=f"llm_extract_failed:{last_error}",
            )
            break

        llm_trace["batch_start"] = batch_start
        llm_trace["batch_size"] = len(batch)
        llm_trace["segment_total"] = len(ranked_segments)
        llm_trace["pass_index"] = pass_count
        trace_entry["llm_responses"] = int(trace_entry.get("llm_responses") or 0) + 1
        trace_entry["batch_mode"] = effective_batch_mode
        trace_entry["input_token_budget"] = segment_token_budget
        trace_entry["last_input_tokens_est"] = int(llm_trace.get("input_tokens_est") or 0)
        trace_entry["last_segment_tokens_est"] = int(llm_trace.get("segment_tokens_est") or 0)
        trace_entry["last_scaffold_tokens_est"] = int(llm_trace.get("scaffold_tokens_est") or 0)
        if extracted:
            llm_extract_applied += 1
            local_count += len(extracted)
            llm_facts.extend(extracted)
            local_trace_items.extend(extracted)
        else:
            trace_entry["llm_empty_responses"] = int(trace_entry.get("llm_empty_responses") or 0) + 1
            llm_empty_semantic += 1
        emit_progress_fn(
            progress_callback,
            event="agentic_extract_batch_done",
            cycle_index=cycle_index,
            target_id=str(row.get("target_id") or ""),
            extracted_count=len(extracted),
            pass_index=pass_count,
        )
        start = batch_start + len(batch)
        page_state["segments_done"] = max(int(page_state.get("segments_done") or 0), start)

    if start >= len(ranked_segments):
        page_state["completed"] = True
        page_state["failed"] = False
    if not bool(page_state.get("failed")) and start < len(ranked_segments):
        coverage_has_more = True
    page_state["coverage_has_more"] = coverage_has_more
    if not bool(page_state.get("failed")):
        page_state["last_error"] = ""

    trace_entry["llm_items_count"] = local_count
    trace_entry["llm_items"] = [
        {
            "paper_title": str(item.get("paper_title") or ""),
            "year": str(item.get("year") or ""),
            "doi": str(item.get("doi") or ""),
            "arxiv_id": str(item.get("arxiv_id") or ""),
            "status": str(item.get("status") or ""),
            "score": float(item.get("score") or 0.0),
        }
        for item in local_trace_items
    ]
    trace_entry["status"] = (
        "failed"
        if bool(page_state.get("failed"))
        else ("completed" if bool(page_state.get("completed")) else "in_progress")
    )
    trace_entry["failed"] = bool(page_state.get("failed"))
    trace_entry["completed"] = bool(page_state.get("completed"))
    trace_entry["coverage_has_more"] = coverage_has_more
    trace_entry["last_error"] = str(page_state.get("last_error") or "")
    trace_entry["segments_done"] = min(start, len(ranked_segments))
    trace_entry["segments_pending"] = max(0, len(ranked_segments) - int(trace_entry.get("segments_done") or 0))
    done = float(trace_entry.get("segments_done") or 0)
    total = float(len(ranked_segments) or 1)
    trace_entry["coverage_pct"] = round(100.0 * done / total, 2)
    emit_progress_fn(
        progress_callback,
        event="agentic_extract_target_done",
        cycle_index=cycle_index,
        target_id=str(row.get("target_id") or ""),
        extracted_count=local_count,
        coverage_has_more=coverage_has_more,
        segments_done=min(start, len(ranked_segments)),
        segments_total=len(ranked_segments),
    )
    return {
        "facts": llm_facts,
        "llm_extract_attempted": llm_extract_attempted,
        "llm_extract_applied": llm_extract_applied,
        "llm_timeout_errors": llm_timeout_errors,
        "llm_empty_semantic": llm_empty_semantic,
        "coverage_has_more": coverage_has_more,
        "coverage_passes": coverage_passes,
    }


def _run_extract_targets(
    *,
    cycle_index: int,
    prepared_targets: list[dict[str, Any]],
    extract_windows_trace: list[dict[str, Any]],
    user_prompt: str,
    extract_intent: dict[str, Any],
    llm_extractor_model: str,
    llm_api_key_env: str,
    progress_callback: ProgressCallback | None,
    runtime_state: dict[str, Any],
    raw_event_fn: Callable[[str, Any, list[str] | None], str] | None,
    coverage_batch_size: int,
    max_calls_per_target: int,
    deps: dict[str, Any],
) -> dict[str, Any]:
    llm_extract_attempted = 0
    llm_extract_applied = 0
    llm_timeout_errors = 0
    llm_empty_semantic = 0
    coverage_has_more = False
    coverage_passes = 0
    llm_facts: list[dict[str, Any]] = []
    extract_state_by_url = runtime_state.setdefault("extract_state_by_url", {})

    for prepared, trace_entry in zip(prepared_targets, extract_windows_trace):
        target_result = _run_prepared_extract_target(
            cycle_index=cycle_index,
            prepared=prepared,
            trace_entry=trace_entry,
            user_prompt=user_prompt,
            extract_intent=extract_intent,
            llm_extractor_model=llm_extractor_model,
            llm_api_key_env=llm_api_key_env,
            progress_callback=progress_callback,
            runtime_state=runtime_state,
            extract_state_by_url=extract_state_by_url,
            raw_event_fn=raw_event_fn,
            coverage_batch_size=coverage_batch_size,
            max_calls_per_target=max_calls_per_target,
            deps=deps,
        )
        llm_facts.extend(list(target_result["facts"]))
        llm_extract_attempted += int(target_result["llm_extract_attempted"])
        llm_extract_applied += int(target_result["llm_extract_applied"])
        llm_timeout_errors += int(target_result["llm_timeout_errors"])
        llm_empty_semantic += int(target_result["llm_empty_semantic"])
        coverage_passes += int(target_result["coverage_passes"])
        coverage_has_more = coverage_has_more or bool(target_result["coverage_has_more"])

    facts: list[dict[str, Any]] = []
    if llm_facts:
        facts = _merge_extracted_facts(
            llm_facts=llm_facts,
            cycle_index=cycle_index,
            progress_callback=progress_callback,
            deps=deps,
        )

    return {
        "facts": facts,
        "llm_extract_attempted": llm_extract_attempted,
        "llm_extract_applied": llm_extract_applied,
        "llm_timeout_errors": llm_timeout_errors,
        "llm_empty_semantic": llm_empty_semantic,
        "coverage_has_more": coverage_has_more,
        "coverage_passes": coverage_passes,
    }


def execute_resolved_extract_request(
    *,
    cycle_index: int,
    request: dict[str, Any],
    paths: dict[str, Path],
    user_prompt: str,
    timeout_s: float,
    llm_extractor_model: str,
    llm_api_key_env: str,
    extract_use_llm_extractor: bool,
    progress_callback: ProgressCallback | None,
    runtime_state: dict[str, Any],
    raw_event_fn: Callable[[str, Any, list[str] | None], str] | None,
    deps: dict[str, Any],
) -> dict[str, Any]:
    target_scope_by_url = dict(request["target_scope_by_url"])
    filters = dict(request["filters"])
    extract_intent = dict(request["extract_intent"])
    anchor_terms = list(request["anchor_terms"])
    records = list(request["records"])
    requested_urls = list(request["requested_urls"])
    auto_fetched_records = list(request["auto_fetched_records"])
    if not records:
        return _build_extract_no_records_result(
            requested_urls=requested_urls,
            extract_intent=extract_intent,
            auto_fetched_records=auto_fetched_records,
        )

    coverage_batch_size = DEFAULT_EXTRACT_BATCH_SIZE
    max_calls_per_target = DEFAULT_EXTRACT_MAX_CALLS
    context_limit_tokens = int(deps["context_limit_tokens"])
    safety_margin = float(deps["safety_margin"])
    output_token_reserve = int(deps["output_token_reserve"])
    emit_progress_fn = deps["emit_progress_fn"]
    prepare_extract_target_fn = deps.get("prepare_extract_target_fn", _prepare_extract_target_impl)
    scope_institutions: list[str] = []
    scope_venues: list[str] = []
    for scope in target_scope_by_url.values():
        if not isinstance(scope, dict):
            continue
        scoped_filters = scope.get("filters") if isinstance(scope.get("filters"), dict) else {}
        institution = str(scoped_filters.get("institution") or "").strip()
        venue = str(scoped_filters.get("venue") or "").strip()
        if institution and institution not in scope_institutions:
            scope_institutions.append(institution)
        if venue and venue not in scope_venues:
            scope_venues.append(venue)
    if not scope_institutions and str(filters.get("institution") or "").strip():
        scope_institutions.append(str(filters.get("institution") or "").strip())
    if not scope_venues and str(filters.get("venue") or "").strip():
        scope_venues.append(str(filters.get("venue") or "").strip())

    extract_windows_trace: list[dict[str, Any]] = []
    emit_progress_fn(
        progress_callback,
        event="agentic_extract_stage",
        cycle_index=cycle_index,
        stage="extract_start",
        target_count=len(records),
        filters=dict(filters),
        institutions=scope_institutions,
        venues=scope_venues,
        anchor_terms=anchor_terms,
        intent=extract_intent,
    )
    must_match = extract_intent.get("must_match") if isinstance(extract_intent.get("must_match"), dict) else {}
    prepared_targets = [
        prepare_extract_target_fn(
            row=row,
            target_scope_by_url=target_scope_by_url,
            filters=filters,
            anchor_terms=anchor_terms,
            must_match=must_match,
            extract_intent=extract_intent,
            user_prompt=user_prompt,
            context_limit_tokens=context_limit_tokens,
            safety_margin=safety_margin,
            output_token_reserve=output_token_reserve,
            deps=deps,
        )
        for row in records
    ]
    for prepared in prepared_targets:
        row = prepared["row"]
        ranked_segments = list(prepared["ranked_segments"])
        emit_progress_fn(
            progress_callback,
            event="agentic_extract_stage",
            cycle_index=cycle_index,
            target_id=str(row.get("target_id") or ""),
            url=str(row.get("url") or ""),
            stage="segment_filter",
            segments_total=len([str(v) for v in (row.get("segments") or []) if str(v).strip()]) or len(ranked_segments),
            segments_ranked=len(ranked_segments),
            batch_mode=str(prepared["batch_mode"]),
            token_budget=int(prepared["token_budget"]),
        )
        extract_windows_trace.append(
            _new_extract_window_trace(
                prepared=prepared,
                extract_intent=extract_intent,
                coverage_batch_size=coverage_batch_size,
            )
        )

    facts: list[dict[str, Any]] = []
    llm_extract_attempted = 0
    llm_extract_applied = 0
    llm_timeout_errors = 0
    llm_empty_semantic = 0
    coverage_has_more = False
    coverage_passes = 0
    if extract_use_llm_extractor:
        extract_run = _run_extract_targets(
            cycle_index=cycle_index,
            prepared_targets=prepared_targets,
            extract_windows_trace=extract_windows_trace,
            user_prompt=user_prompt,
            extract_intent=extract_intent,
            llm_extractor_model=llm_extractor_model,
            llm_api_key_env=llm_api_key_env,
            progress_callback=progress_callback,
            runtime_state=runtime_state,
            raw_event_fn=raw_event_fn,
            coverage_batch_size=coverage_batch_size,
            max_calls_per_target=max_calls_per_target,
            deps=deps,
        )
        facts = list(extract_run["facts"])
        llm_extract_attempted = int(extract_run["llm_extract_attempted"])
        llm_extract_applied = int(extract_run["llm_extract_applied"])
        llm_timeout_errors = int(extract_run["llm_timeout_errors"])
        llm_empty_semantic = int(extract_run["llm_empty_semantic"])
        coverage_has_more = bool(extract_run["coverage_has_more"])
        coverage_passes = int(extract_run["coverage_passes"])

    paper_candidates = deps["to_paper_candidates_from_facts_fn"](facts)
    paper_dedup_trace = {"cluster_count": 0, "reduced_count": 0}
    dedup_paper_candidates_with_llm_fn = deps.get("dedup_paper_candidates_with_llm_fn")
    if extract_use_llm_extractor and paper_candidates and dedup_paper_candidates_with_llm_fn is not None:
        dedup_op_prefix = "extract_paper_dedup"
        paper_candidates, paper_dedup_trace = dedup_paper_candidates_with_llm_fn(
            paper_candidates=paper_candidates,
            user_prompt=user_prompt,
            model=llm_extractor_model,
            api_key_env=llm_api_key_env,
            timeout_s=timeout_s,
            max_retries=0,
            raw_event_fn=(
                (lambda event_type, payload: raw_event_fn(event_type, payload, None))
                if raw_event_fn is not None
                else None
            ),
            llm_op_id_prefix=dedup_op_prefix,
            deps={
                "estimate_messages_metrics_fn": deps["estimate_messages_metrics_fn"],
                "openai_complete_json_fn": deps["openai_complete_json_fn"],
                "next_op_id_fn": (lambda prefix: deps["next_op_id_fn"](runtime_state, prefix)),
            },
        )
        emit_progress_fn(
            progress_callback,
            event="agentic_extract_stage",
            cycle_index=cycle_index,
            stage="paper_dedup_done",
            paper_dedup_clusters=int(paper_dedup_trace.get("cluster_count") or 0),
            paper_dedup_reduced=int(paper_dedup_trace.get("reduced_count") or 0),
        )
    known_urls = [
        str(row.get("url") or "")
        for row in (runtime_state.get("url_hits") or [])
        if isinstance(row, dict) and str(row.get("url") or "").strip()
    ]
    known_urls.extend(
        str(url or "")
        for url in dict(runtime_state.get("extract_state_by_url") or {}).keys()
        if str(url or "").strip()
    )
    known_urls.extend(str(url or "") for url in requested_urls if str(url or "").strip())
    known_urls.extend(
        str(row.get("url") or "")
        for row in records
        if isinstance(row, dict) and str(row.get("url") or "").strip()
    )
    link_candidates = deps["collect_candidate_url_inputs_from_records_fn"](
        records,
        paths=paths,
        known_urls=known_urls,
    )
    candidate_urls: list[dict[str, Any]] = []
    if paper_candidates and link_candidates:
        emit_progress_fn(
            progress_callback,
            event="agentic_extract_stage",
            cycle_index=cycle_index,
            stage="candidate_url_proposal",
            link_candidates=len(link_candidates),
        )
        candidate_url_op_id = deps["next_op_id_fn"](runtime_state, "extract_candidate_urls")
        candidate_urls, _candidate_url_trace = deps["extract_candidate_urls_with_llm_fn"](
            user_prompt=user_prompt,
            intent=extract_intent,
            paper_candidates=paper_candidates,
            anchor_terms=anchor_terms,
            known_urls=known_urls,
            link_candidates=link_candidates,
            model=llm_extractor_model,
            api_key_env=llm_api_key_env,
            timeout_s=timeout_s,
            max_retries=0,
            raw_event_fn=(
                (lambda event_type, payload: raw_event_fn(event_type, payload, None))
                if raw_event_fn is not None
                else None
            ),
            llm_op_id=candidate_url_op_id,
            deps={
                "estimate_messages_metrics_fn": deps["estimate_messages_metrics_fn"],
                "openai_complete_json_fn": deps["openai_complete_json_fn"],
                "peek_text_fn": deps["peek_text_fn"],
            },
        )
        emit_progress_fn(
            progress_callback,
            event="agentic_extract_stage",
            cycle_index=cycle_index,
            stage="candidate_url_done",
            link_candidates=len(link_candidates),
            candidate_url_count=len(candidate_urls),
        )
    return _build_extract_action_result(
        facts=facts,
        paper_candidates=paper_candidates,
        candidate_urls=candidate_urls,
        requested_urls=requested_urls,
        llm_extract_attempted=llm_extract_attempted,
        llm_extract_applied=llm_extract_applied,
        llm_timeout_errors=llm_timeout_errors,
        llm_empty_semantic=llm_empty_semantic,
        coverage_has_more=coverage_has_more,
        coverage_passes=coverage_passes,
        extract_windows_trace=extract_windows_trace,
        extract_intent=extract_intent,
        auto_fetched_records=auto_fetched_records,
        paper_dedup_clusters=int(paper_dedup_trace.get("cluster_count") or 0),
        paper_dedup_reduced=int(paper_dedup_trace.get("reduced_count") or 0),
    )


def execute_extract_content_action(
    *,
    session_id: str,
    cycle_index: int,
    params: dict[str, Any],
    paths: dict[str, Path],
    user_prompt: str,
    timeout_s: float,
    llm_extractor_model: str,
    llm_api_key_env: str,
    extract_use_llm_extractor: bool,
    progress_callback: ProgressCallback | None,
    runtime_state: dict[str, Any],
    raw_event_fn: Callable[[str, Any, list[str] | None], str] | None,
    deps: dict[str, Any],
) -> dict[str, Any]:
    resolve_extract_request_fn = deps.get("resolve_extract_request_fn", _resolve_extract_request_impl)
    request = resolve_extract_request_fn(
        session_id=session_id,
        cycle_index=cycle_index,
        params=params,
        paths=paths,
        user_prompt=user_prompt,
        timeout_s=timeout_s,
        runtime_state=runtime_state,
        raw_event_fn=raw_event_fn,
        deps=deps,
    )
    return execute_resolved_extract_request(
        cycle_index=cycle_index,
        request=request,
        paths=paths,
        user_prompt=user_prompt,
        timeout_s=timeout_s,
        llm_extractor_model=llm_extractor_model,
        llm_api_key_env=llm_api_key_env,
        extract_use_llm_extractor=extract_use_llm_extractor,
        progress_callback=progress_callback,
        runtime_state=runtime_state,
        raw_event_fn=raw_event_fn,
        deps=deps,
    )
