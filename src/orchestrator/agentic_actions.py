from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from src.connectors.http import get_json
from src.orchestrator import agentic_extract as extract_mod
from src.orchestrator import agentic_extract_candidates as candidate_mod
from src.orchestrator import agentic_extract_dedup as dedup_mod
from src.orchestrator import agentic_extract_prepare as prepare_mod
from src.orchestrator import agentic_extract_runtime as extract_runtime_mod
from src.orchestrator import agentic_fetch as fetch_mod
from src.orchestrator import agentic_llm as llm_mod
from src.orchestrator import agentic_search as search_mod
from src.orchestrator import agentic_text as text_mod
from src.orchestrator import agentic_view as view_mod

ProgressCallback = Callable[[dict], None]

DEFAULT_EXTRACTOR_CONTEXT_LIMIT_TOKENS = 128_000
DEFAULT_EXTRACTOR_SAFETY_MARGIN = 0.18
DEFAULT_EXTRACTOR_OUTPUT_TOKEN_RESERVE = 6_000


def _emit_progress(progress_callback: ProgressCallback | None, *, event: str, **payload: Any) -> None:
    if progress_callback is None:
        return
    body = {"event": event}
    body.update(payload)
    progress_callback(body)


def _search_web_queries(**kwargs: Any) -> tuple[list[dict], list[dict]]:
    return search_mod._search_web_queries(
        **kwargs,
        get_json_fn=get_json,
        normalize_row_fn=search_mod._normalize_searx_row,
        emit_progress_fn=_emit_progress,
    )


def _unique_queries(items: Any, limit: int) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items or []:
        query = str(item or "").strip()
        if not query:
            continue
        lowered = query.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        out.append(query)
        if len(out) >= limit:
            break
    return out


def _extract_action_queries(payload: dict, *, limit: int) -> list[str]:
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    if isinstance(params.get("queries"), list):
        return _unique_queries(params.get("queries"), limit)
    return _unique_queries(payload.get("queries"), limit)


def _venue_like_tokens(query: str) -> list[str]:
    tokens = re.findall(r"\b[A-Za-z][A-Za-z0-9\-]{2,}\b", str(query or ""))
    out: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        if re.fullmatch(r"(19|20)\d{2}", token):
            continue
        upper = token.upper()
        if upper in text_mod.NON_VENUE_ACRONYMS:
            continue
        is_acronym = bool(re.fullmatch(r"[A-Z]{4,}", token))
        is_mixed = bool(re.fullmatch(r"[A-Z][a-z]+[A-Z][A-Za-z]+", token))
        if not (is_acronym or is_mixed):
            continue
        if upper in seen:
            continue
        seen.add(upper)
        out.append(token)
    return out


def _split_multi_venue_query(query: str) -> list[str]:
    original = str(query or "").strip()
    if not original:
        return []
    venues = _venue_like_tokens(original)
    if len(venues) <= 1:
        return [original]
    base = original
    for venue in venues:
        base = re.sub(rf"\b{re.escape(venue)}\b", " ", base)
    base = re.sub(r"\s+", " ", base).strip()
    if not base:
        return [original]
    out: list[str] = []
    seen: set[str] = set()
    for venue in venues:
        query_text = re.sub(r"\s+", " ", f"{base} {venue}").strip()
        lowered = query_text.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        out.append(query_text)
    return out or [original]


def normalize_search_queries(queries: list[str], *, max_total: int) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in queries or []:
        for item in _split_multi_venue_query(str(raw or "")):
            normalized = re.sub(r"\s+", " ", item).strip()
            if not normalized:
                continue
            lowered = normalized.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            out.append(normalized)
            if len(out) >= max_total:
                return out
    return out


def _read_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _decode_bytes(raw: Any) -> str:
    return fetch_mod.decode_bytes(raw)


def _extract_text_from_pdf_bytes(raw: bytes, *, max_chars: int) -> str:
    return fetch_mod.extract_text_from_pdf_bytes(raw, max_chars=max_chars)


def _fetch_url_raw(url: str, *, timeout_s: float, max_bytes: int) -> tuple[bytes, str]:
    return fetch_mod.fetch_url_raw(url, timeout_s=timeout_s, max_bytes=max_bytes)


def _safe_name(value: str, default: str = "item") -> str:
    return fetch_mod.safe_name(value, default)


def _fetch_retry_urls(url: str) -> list[str]:
    return fetch_mod.fetch_retry_urls(url)


def _save_raw_fetch(paths: dict[str, Path], *, cycle_index: int, target_id: str, url: str, raw: Any, content_type: str) -> str:
    return fetch_mod.save_raw_fetch(
        paths,
        cycle_index=cycle_index,
        target_id=target_id,
        url=url,
        raw=raw,
        content_type=content_type,
    )


def _fetch_target_record(
    *,
    session_id: str,
    cycle_index: int,
    target: dict,
    idx: int,
    timeout_s: float,
    params: dict,
    paths: dict[str, Path],
    raw_event_fn: Callable[[str, Any, list[str] | None], str] | None = None,
    new_op_id_fn: Callable[[str], str] | None = None,
) -> dict:
    return fetch_mod.fetch_target_record(
        session_id=session_id,
        cycle_index=cycle_index,
        target=target,
        idx=idx,
        timeout_s=timeout_s,
        params=params,
        paths=paths,
        raw_event_fn=raw_event_fn,
        new_op_id_fn=new_op_id_fn,
        deps={
            "read_int_fn": _read_int,
            "fetch_url_raw_fn": _fetch_url_raw,
            "fetch_retry_urls_fn": _fetch_retry_urls,
            "save_raw_fetch_fn": _save_raw_fetch,
            "extract_text_from_pdf_bytes_fn": _extract_text_from_pdf_bytes,
            "decode_bytes_fn": _decode_bytes,
            "build_extraction_windows_fn": fetch_mod.build_extraction_windows,
            "clean_text_fn": text_mod._clean_text,
            "discover_pagination_urls_fn": text_mod._discover_pagination_urls,
            "extract_listing_text_with_fallback_fn": text_mod._extract_listing_text_with_fallback,
            "extract_main_text_from_html_fn": text_mod._extract_main_text_from_html,
            "extract_text_segments_fn": text_mod._extract_text_segments,
            "is_listing_page_fn": text_mod._is_listing_page,
        },
    )


def _extract_facts_with_llm(
    *,
    record: dict,
    filters: dict,
    user_prompt: str,
    model: str,
    api_key_env: str,
    intent: dict,
    segments: list[str] | None = None,
    timeout_s: float = 45.0,
    max_retries: int = 0,
    raw_event_fn: Callable[[str, Any], str] | None = None,
    llm_op_id: str = "",
    batch_mode: str = "",
) -> tuple[list[dict], dict]:
    return extract_mod.extract_facts_with_llm(
        record=record,
        filters=filters,
        user_prompt=user_prompt,
        model=model,
        api_key_env=api_key_env,
        intent=intent,
        segments=segments,
        timeout_s=timeout_s,
        max_retries=max_retries,
        raw_event_fn=raw_event_fn,
        llm_op_id=llm_op_id,
        batch_mode=batch_mode,
        deps={
            "build_extraction_windows_fn": fetch_mod.build_extraction_windows,
            "estimate_messages_metrics_fn": llm_mod.estimate_messages_metrics,
            "openai_complete_json_fn": llm_mod.openai_complete_json,
            "peek_text_fn": search_mod._peek_text,
            "strip_listing_author_tail_fn": search_mod._strip_listing_author_tail,
            "extract_year_best_fn": candidate_mod.extract_year_best,
        },
    )


def execute_search_web_action(
    *,
    session_id: str,
    cycle_index: int,
    params: dict,
    searxng_url: str,
    search_categories: str,
    web_results_per_query: int,
    timeout_s: float,
    progress_callback: ProgressCallback | None,
    runtime_state: dict[str, Any],
    next_op_id_fn: Callable[[dict[str, Any], str], str] | None = None,
    raw_event_fn: Callable[[str, Any, list[str] | None], str] | None = None,
) -> dict:
    return search_mod.execute_search_web_action(
        session_id=session_id,
        cycle_index=cycle_index,
        params=params,
        searxng_url=searxng_url,
        search_categories=search_categories,
        web_results_per_query=web_results_per_query,
        timeout_s=timeout_s,
        progress_callback=progress_callback,
        runtime_state=runtime_state,
        raw_event_fn=raw_event_fn,
        deps={
            "extract_action_queries_fn": _extract_action_queries,
            "normalize_search_queries_fn": normalize_search_queries,
            "search_web_queries_fn": _search_web_queries,
            "normalize_shortlist_hints_fn": view_mod._normalize_shortlist_hints,
            "next_op_id_fn": next_op_id_fn or (lambda _state, prefix="op": prefix),
        },
    )


def execute_extract_content_action(
    *,
    session_id: str,
    cycle_index: int,
    params: dict,
    paths: dict[str, Path],
    user_prompt: str,
    timeout_s: float,
    llm_extractor_model: str,
    llm_api_key_env: str,
    extract_use_llm_extractor: bool,
    progress_callback: ProgressCallback | None,
    runtime_state: dict[str, Any],
    next_op_id_fn: Callable[[dict[str, Any], str], str] | None = None,
    raw_event_fn: Callable[[str, Any, list[str] | None], str] | None = None,
) -> dict:
    return extract_runtime_mod.execute_extract_content_action(
        session_id=session_id,
        cycle_index=cycle_index,
        params=params,
        paths=paths,
        user_prompt=user_prompt,
        timeout_s=timeout_s,
        llm_extractor_model=llm_extractor_model,
        llm_api_key_env=llm_api_key_env,
        extract_use_llm_extractor=extract_use_llm_extractor,
        progress_callback=progress_callback,
        runtime_state=runtime_state,
        raw_event_fn=raw_event_fn,
        deps={
            "normalize_anchor_terms_fn": text_mod._normalize_anchor_terms,
            "resolve_extract_text_filters_fn": text_mod._resolve_extract_text_filters,
            "resolve_active_extract_filters_fn": text_mod._resolve_active_extract_filters,
            "prepare_extract_segments_fn": text_mod._prepare_extract_segments,
            "resolve_extract_request_fn": prepare_mod.resolve_extract_request,
            "prepare_extract_target_fn": prepare_mod.prepare_extract_target,
            "extract_segment_token_budget_fn": (
                lambda **kwargs: extract_mod.extract_segment_token_budget(
                    **kwargs,
                    deps={
                        "estimate_messages_metrics_fn": llm_mod.estimate_messages_metrics,
                    },
                )
            ),
            "normalize_fetch_target_fn": prepare_mod.normalize_fetch_target,
            "extract_target_filters_fn": prepare_mod.extract_target_filters,
            "resolve_extract_intent_fn": prepare_mod.resolve_extract_intent,
            "safe_int_fn": text_mod._safe_int,
            "reuse_fetched_record_for_target_fn": search_mod._reuse_fetched_record_for_target,
            "fetch_target_record_fn": _fetch_target_record,
            "merge_fetched_records_fn": search_mod._merge_fetched_records,
            "filter_records_by_urls_fn": search_mod._filter_records_by_urls,
            "next_op_id_fn": next_op_id_fn or (lambda _state, prefix="op": prefix),
            "emit_progress_fn": _emit_progress,
            "slice_segments_by_token_budget_fn": extract_mod.slice_segments_by_token_budget,
            "extract_facts_with_llm_fn": _extract_facts_with_llm,
            "candidate_dedup_key_fn": search_mod._candidate_dedup_key,
            "to_paper_candidates_from_facts_fn": (
                lambda facts: candidate_mod.to_paper_candidates_from_facts(
                    facts,
                    canonicalize_candidate_title_fn=candidate_mod.canonicalize_candidate_title,
                )
            ),
            "dedup_paper_candidates_with_llm_fn": dedup_mod.dedup_paper_candidates_with_llm,
            "enable_candidate_url_proposal": False,
            "collect_candidate_url_inputs_from_records_fn": candidate_mod.collect_candidate_url_inputs_from_records,
            "extract_candidate_urls_with_llm_fn": extract_mod.extract_candidate_urls_with_llm,
            "estimate_messages_metrics_fn": llm_mod.estimate_messages_metrics,
            "openai_complete_json_fn": llm_mod.openai_complete_json,
            "peek_text_fn": search_mod._peek_text,
            "context_limit_tokens": DEFAULT_EXTRACTOR_CONTEXT_LIMIT_TOKENS,
            "safety_margin": DEFAULT_EXTRACTOR_SAFETY_MARGIN,
            "output_token_reserve": DEFAULT_EXTRACTOR_OUTPUT_TOKEN_RESERVE,
        },
    )


def execute_agent_action(
    *,
    action: str,
    session_id: str,
    cycle_index: int,
    params: dict,
    paths: dict[str, Path],
    user_prompt: str,
    searxng_url: str,
    search_categories: str,
    web_results_per_query: int,
    timeout_s: float,
    llm_model: str,
    llm_extractor_model: str,
    llm_api_key_env: str,
    extract_use_llm_extractor: bool,
    progress_callback: ProgressCallback | None,
    runtime_state: dict[str, Any],
    next_op_id_fn: Callable[[dict[str, Any], str], str] | None = None,
    raw_event_fn: Callable[[str, Any, list[str] | None], str] | None = None,
) -> dict:
    if action == "search_web":
        return execute_search_web_action(
            session_id=session_id,
            cycle_index=cycle_index,
            params=params,
            searxng_url=searxng_url,
            search_categories=search_categories,
            web_results_per_query=web_results_per_query,
            timeout_s=timeout_s,
            progress_callback=progress_callback,
            runtime_state=runtime_state,
            next_op_id_fn=next_op_id_fn,
            raw_event_fn=raw_event_fn,
        )

    if action == "extract_content":
        return execute_extract_content_action(
            session_id=session_id,
            cycle_index=cycle_index,
            params=params,
            paths=paths,
            user_prompt=user_prompt,
            timeout_s=timeout_s,
            llm_extractor_model=llm_extractor_model,
            llm_api_key_env=llm_api_key_env,
            extract_use_llm_extractor=extract_use_llm_extractor,
            progress_callback=progress_callback,
            runtime_state=runtime_state,
            next_op_id_fn=next_op_id_fn,
            raw_event_fn=raw_event_fn,
        )

    return {
        "status": "blocked",
        "tool_calls": f"invalid:{action}",
        "web_rows": [],
        "raw_candidates": [],
        "paper_candidates": [],
        "candidate_urls": [],
        "stop": True,
        "stop_reason": f"unsupported_action:{action}",
        "notes": f"unsupported action params={json.dumps(params, ensure_ascii=True)}",
    }
