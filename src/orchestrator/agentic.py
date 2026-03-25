from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from src.connectors.http import get_json, normalize_arxiv_id, normalize_doi
from src.orchestrator.agentic_contracts import (
    _build_agent_messages,
    _parse_agent_action_response,
)
from src.orchestrator.agentic_extract_candidates import (
    canonicalize_candidate_title as _canonicalize_candidate_title_impl,
    canonicalize_discovered_url as _canonicalize_discovered_url_impl,
    collect_candidate_url_inputs_from_records as _collect_candidate_url_inputs_from_records_impl,
    extract_listing_candidates_from_segments as _extract_listing_candidates_from_segments_impl,
    extract_year_best as _extract_year_best_impl,
    infer_paper_title as _infer_paper_title_impl,
    is_authorish_title_fragment as _is_authorish_title_fragment_impl,
    looks_like_paper_candidate as _looks_like_paper_candidate_impl,
    to_paper_candidates_from_facts as _to_paper_candidates_from_facts_impl,
)
from src.orchestrator.agentic_extract import (
    extract_facts_with_llm as _extract_facts_with_llm_impl,
    extract_candidate_urls_with_llm as _extract_candidate_urls_with_llm_impl,
    extract_segment_token_budget as _extract_segment_token_budget_impl,
    extract_target_filters as _extract_target_filters_impl,
    infer_by_subject_from_prompt as _infer_by_subject_from_prompt_impl,
    infer_subject_kind as _infer_subject_kind_impl,
    infer_year_gte_from_prompt as _infer_year_gte_from_prompt_impl,
    normalize_fetch_target as _normalize_fetch_target_impl,
    resolve_extract_intent as _resolve_extract_intent_impl,
    slice_segments_by_token_budget as _slice_segments_by_token_budget_impl,
    execute_extract_content_action as _execute_extract_content_action_impl,
)
from src.orchestrator.agentic_fetch import (
    build_extraction_windows as _build_extraction_windows_impl,
    decode_bytes as _decode_bytes_impl,
    extract_text_from_pdf_bytes as _extract_text_from_pdf_bytes_impl,
    fetch_retry_urls as _fetch_retry_urls_impl,
    fetch_target_record as _fetch_target_record_impl,
    fetch_url_raw as _fetch_url_raw_impl,
    safe_name as _safe_name_impl,
    save_raw_fetch as _save_raw_fetch_impl,
)
from src.orchestrator.agentic_llm import (
    coerce_message_content_text as _coerce_message_content_text_impl,
    estimate_messages_metrics as _estimate_messages_metrics_impl,
    extract_json_object as _extract_json_object_impl,
    openai_complete_json as _openai_complete_json_impl,
    openai_completion_json_payload as _openai_completion_json_payload_impl,
    resolve_openai_model_and_base_url as _resolve_openai_model_and_base_url_impl,
    response_message_content as _response_message_content_impl,
)
from src.orchestrator.agentic_loop import (
    _AgentConfig,
    _AgenticSearchLoop,
    _ExtractConfig,
    _ResultConfig,
    _SearchConfig,
    _next_op_id,
)
from src.orchestrator.agentic_search import (
    _apply_shortlist_hints,
    _as_list,
    _candidate_dedup_key,
    execute_search_web_action as _execute_search_web_action_impl,
    _domain_quality_adjustment,
    _filter_records_by_urls,
    _filter_search_rows,
    _host_from_url,
    _make_hit_id,
    _merge_fetched_records,
    _merge_url_hits,
    _normalize_searx_row,
    _normalize_title_for_key,
    _peek_text,
    _rank_candidates,
    _reuse_fetched_record_for_target,
    _search_web_queries as _search_web_queries_impl,
    _select_diverse_shortlist,
    _strip_listing_author_tail,
    _to_url_hits as _to_url_hits_impl,
    _unique_nonempty,
)
from src.orchestrator.agentic_result import (
    _merge_paper_candidates,
)
from src.orchestrator.agentic_state_apply import (
    _apply_search_action_result as _apply_search_action_result_impl,
    _paper_fallback_candidates,
    _paper_final_candidates,
)
from src.orchestrator.agentic_text import (
    LISTING_HEADING_PHRASES,
    NON_PAPER_TITLE_TOKENS,
    NON_VENUE_ACRONYMS,
    PAPER_SIGNAL_TOKENS,
    _anchor_segments_for_filters,
    _clean_block_text,
    _clean_text,
    _discover_pagination_urls,
    _estimate_text_tokens,
    _extract_html_structural_segments,
    _extract_listing_text_with_fallback,
    _extract_main_text_from_html,
    _extract_text_segments,
    _is_detail_page,
    _is_listing_page,
    _normalize_anchor_terms,
    _prepare_extract_segments,
    _resolve_active_extract_filters,
    _resolve_extract_anchor_terms,
)
from src.orchestrator.agentic_view import (
    _apply_plan_update,
    _build_agent_memory,
    _build_agent_working_state,
    _build_progress_snapshot,
    _normalize_shortlist_hints,
    _sanitize_agent_action_params,
    _trace_action_input,
    _write_agentic_trajectory,
)
from src.retrieval.service import write_yaml
from src.utils.paths import project_dir
from src.utils.yamlx import load

APP_SUPPORTED_ACTIONS = {
    "search_web",
    "extract_content",
}
AGENT_SUPPORTED_ACTIONS = {
    "search_web",
    "extract_content",
}

ProgressCallback = Callable[[dict], None]

DEFAULT_EXTRACTOR_CONTEXT_LIMIT_TOKENS = 128_000
DEFAULT_EXTRACTOR_SAFETY_MARGIN = 0.18
DEFAULT_EXTRACTOR_OUTPUT_TOKEN_RESERVE = 6_000


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _emit_progress(progress_callback: ProgressCallback | None, *, event: str, **payload) -> None:
    if progress_callback is None:
        return
    body = {"event": event}
    body.update(payload)
    progress_callback(body)


def _search_web_queries(**kwargs: Any) -> tuple[list[dict], list[dict]]:
    return _search_web_queries_impl(
        **kwargs,
        get_json_fn=get_json,
        normalize_row_fn=_normalize_searx_row,
        emit_progress_fn=_emit_progress,
    )


def _to_url_hits(session_id: str, cycle_index: int, shortlisted: list[dict], top_n: int) -> list[dict]:
    return _to_url_hits_impl(
        session_id,
        cycle_index,
        shortlisted,
        top_n,
        make_hit_id_fn=_make_hit_id,
        peek_text_fn=_peek_text,
        host_from_url_fn=_host_from_url,
    )


def _apply_search_action_result(
    *,
    session_id: str,
    cycle_index: int,
    raw_candidates: list[dict[str, Any]],
    shortlist_hints: Any,
    existing_url_hits: list[dict[str, Any]],
    shortlist_size: int,
    max_cycles: int,
) -> dict[str, Any]:
    return _apply_search_action_result_impl(
        session_id=session_id,
        cycle_index=cycle_index,
        raw_candidates=raw_candidates,
        shortlist_hints=shortlist_hints,
        existing_url_hits=existing_url_hits,
        shortlist_size=shortlist_size,
        max_cycles=max_cycles,
        to_url_hits_fn=_to_url_hits,
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
        if upper in NON_VENUE_ACRONYMS:
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
    return [f"{base} {venue}".strip() for venue in venues]


def _normalize_search_queries(queries: list[str], *, max_total: int) -> list[str]:
    expanded: list[str] = []
    for query in queries:
        for item in _split_multi_venue_query(query):
            text = str(item or "").strip()
            if text:
                expanded.append(text)
    return _unique_queries(expanded, max_total)


def _retrieval_dir(pdir: Path) -> Path:
    return pdir / "artifacts" / "retrieval"


def _agentic_paths(pdir: Path) -> dict[str, Path]:
    rdir = _retrieval_dir(pdir)
    return {
        "result": rdir / "agentic_result.yaml",
        "trajectory": rdir / "agentic_trajectory.yaml",
        "raw": rdir / "agentic_raw.ndjson",
    }


def _append_raw_event(
    *,
    path: Path,
    trace_state: "_TraceState",
    session_id: str,
    cycle_index: int,
    action_id: str,
    event_type: str,
    payload: Any,
    raw_refs: list[str] | None = None,
) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    seq = int(trace_state.raw_event_seq or 0) + 1
    trace_state.raw_event_seq = seq
    event_id = f"raw-{seq:06d}"
    row = {
        "event_id": event_id,
        "ts": _utc_now(),
        "run_id": session_id,
        "cycle_index": cycle_index,
        "action_id": action_id,
        "event_type": event_type,
        "raw_refs": list(raw_refs or []),
        "payload": payload,
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False))
        fh.write("\n")
    return event_id


def _next_op_id(trace_state: "_TraceState", prefix: str = "op") -> str:
    seq = int(trace_state.op_event_seq or 0) + 1
    trace_state.op_event_seq = seq
    token = re.sub(r"[^a-z0-9]+", "-", str(prefix or "op").strip().lower()).strip("-") or "op"
    return f"{token}-{seq:06d}"


def _new_session_id(prompt: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    digest = hashlib.sha1(prompt.strip().lower().encode("utf-8")).hexdigest()[:10]
    return f"agentic-{stamp}-{digest}"


def _new_result_state() -> dict:
    return {
        "status": "running",
        "stop_reason": "",
        "cycle_count": 0,
    }


def _read_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _read_str(value: Any, default: str) -> str:
    text = str(value or "").strip()
    return text if text else default


def _read_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _extract_json_object(text: str) -> dict:
    return _extract_json_object_impl(text)


def _coerce_message_content_text(content: Any) -> str:
    return _coerce_message_content_text_impl(content)


def _response_message_content(response: Any) -> str:
    return _response_message_content_impl(response)


def _resolve_openai_model_and_base_url(*, model: str, api_key_env: str) -> tuple[str, str]:
    return _resolve_openai_model_and_base_url_impl(model=model, api_key_env=api_key_env)


def _openai_completion_json_payload(
    *,
    client: Any,
    model: str,
    messages: list[dict],
    with_response_format: bool,
    max_tokens: int | None = None,
) -> dict:
    return _openai_completion_json_payload_impl(
        client=client,
        model=model,
        messages=messages,
        with_response_format=with_response_format,
        max_tokens=max_tokens,
    )


def _estimate_messages_metrics(messages: list[dict]) -> dict[str, int]:
    return _estimate_messages_metrics_impl(messages)


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _decode_bytes(raw: Any) -> str:
    return _decode_bytes_impl(raw)


def _extract_text_from_pdf_bytes(raw: bytes, *, max_chars: int) -> str:
    return _extract_text_from_pdf_bytes_impl(raw, max_chars=max_chars)


def _fetch_url_raw(url: str, *, timeout_s: float, max_bytes: int) -> tuple[bytes, str]:
    return _fetch_url_raw_impl(url, timeout_s=timeout_s, max_bytes=max_bytes)


def _safe_name(value: str, default: str = "item") -> str:
    return _safe_name_impl(value, default)


def _fetch_retry_urls(url: str) -> list[str]:
    return _fetch_retry_urls_impl(url)


def _save_raw_fetch(paths: dict[str, Path], *, cycle_index: int, target_id: str, url: str, raw: Any, content_type: str) -> str:
    return _save_raw_fetch_impl(
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
    return _fetch_target_record_impl(
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
            "build_extraction_windows_fn": _build_extraction_windows,
            "clean_text_fn": _clean_text,
            "discover_pagination_urls_fn": _discover_pagination_urls,
            "extract_listing_text_with_fallback_fn": _extract_listing_text_with_fallback,
            "extract_main_text_from_html_fn": _extract_main_text_from_html,
            "extract_text_segments_fn": _extract_text_segments,
            "is_listing_page_fn": _is_listing_page,
        },
    )


def _normalize_fetch_target(item: dict, idx: int) -> dict:
    return _normalize_fetch_target_impl(item, idx)


def _extract_target_filters(item: dict) -> dict:
    return _extract_target_filters_impl(item)


def _infer_year_gte_from_prompt(prompt: str) -> int | None:
    return _infer_year_gte_from_prompt_impl(prompt)


def _infer_by_subject_from_prompt(prompt: str) -> str:
    return _infer_by_subject_from_prompt_impl(prompt)


def _infer_subject_kind(subject: str) -> str:
    return _infer_subject_kind_impl(subject)


def _resolve_extract_intent(
    *,
    params: dict,
    filters: dict,
    user_prompt: str,
) -> dict:
    return _resolve_extract_intent_impl(
        params=params,
        filters=filters,
        user_prompt=user_prompt,
    )


def _extract_segment_token_budget(
    *,
    record: dict,
    filters: dict,
    user_prompt: str,
    intent: dict,
    context_limit_tokens: int,
    safety_margin: float,
    output_token_reserve: int,
) -> int:
    return _extract_segment_token_budget_impl(
        record=record,
        filters=filters,
        user_prompt=user_prompt,
        intent=intent,
        context_limit_tokens=context_limit_tokens,
        safety_margin=safety_margin,
        output_token_reserve=output_token_reserve,
        deps={
            "estimate_messages_metrics_fn": _estimate_messages_metrics,
        },
    )


def _build_extraction_windows(text: str, filters: dict, *, max_windows: int = 6, radius: int = 2, max_chars: int = 1400) -> list[str]:
    return _build_extraction_windows_impl(
        text,
        filters,
        max_windows=max_windows,
        radius=radius,
        max_chars=max_chars,
    )


def _slice_segments_by_token_budget(
    segments: list[str],
    *,
    start: int,
    max_segments: int,
    token_budget: int,
    min_segments: int = 1,
) -> list[str]:
    return _slice_segments_by_token_budget_impl(
        segments,
        start=start,
        max_segments=max_segments,
        token_budget=token_budget,
        min_segments=min_segments,
    )


def _extract_year_best(text: str, *, year_gte: int | None = None) -> str:
    return _extract_year_best_impl(text, year_gte=year_gte)


def _infer_paper_title(text: str, fallback: str) -> str:
    return _infer_paper_title_impl(text, fallback)


def _looks_like_paper_candidate(title: str, evidence: str, url: str) -> bool:
    return _looks_like_paper_candidate_impl(title, evidence, url)

def _is_authorish_title_fragment(title: str) -> bool:
    return _is_authorish_title_fragment_impl(title)


def _extract_listing_candidates_from_segments(
    *,
    session_id: str,
    cycle_index: int,
    target_id: str,
    url: str,
    url_title: str,
    segments: list[str],
    filters: dict,
    fallback_year: str,
) -> list[dict]:
    return _extract_listing_candidates_from_segments_impl(
        session_id=session_id,
        cycle_index=cycle_index,
        target_id=target_id,
        url=url,
        url_title=url_title,
        segments=segments,
        filters=filters,
        fallback_year=fallback_year,
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
    return _extract_facts_with_llm_impl(
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
            "build_extraction_windows_fn": _build_extraction_windows,
            "estimate_messages_metrics_fn": _estimate_messages_metrics,
            "openai_complete_json_fn": _openai_complete_json,
            "peek_text_fn": _peek_text,
            "strip_listing_author_tail_fn": _strip_listing_author_tail,
            "extract_year_best_fn": _extract_year_best,
        },
    )


def _to_paper_candidates_from_facts(facts: list[dict]) -> list[dict]:
    return _to_paper_candidates_from_facts_impl(
        facts,
        canonicalize_candidate_title_fn=_canonicalize_candidate_title,
    )


def _canonicalize_discovered_url(url: str) -> str:
    return _canonicalize_discovered_url_impl(url)


def _collect_candidate_url_inputs_from_records(
    records: list[dict],
    *,
    paths: dict[str, Path],
    known_urls: list[str],
) -> list[dict]:
    return _collect_candidate_url_inputs_from_records_impl(
        records,
        paths=paths,
        known_urls=known_urls,
    )


def _extract_candidate_urls_with_llm(
    *,
    user_prompt: str,
    intent: dict,
    paper_candidates: list[dict],
    anchor_terms: list[str],
    known_urls: list[str],
    link_candidates: list[dict],
    model: str,
    api_key_env: str,
    timeout_s: float = 45.0,
    max_retries: int = 0,
    raw_event_fn: Callable[[str, Any], str] | None = None,
    llm_op_id: str = "",
) -> tuple[list[dict], dict]:
    return _extract_candidate_urls_with_llm_impl(
        user_prompt=user_prompt,
        intent=intent,
        paper_candidates=paper_candidates,
        anchor_terms=anchor_terms,
        known_urls=known_urls,
        link_candidates=link_candidates,
        model=model,
        api_key_env=api_key_env,
        timeout_s=timeout_s,
        max_retries=max_retries,
        raw_event_fn=raw_event_fn,
        llm_op_id=llm_op_id,
        deps={
            "estimate_messages_metrics_fn": _estimate_messages_metrics,
            "openai_complete_json_fn": _openai_complete_json,
            "peek_text_fn": _peek_text,
        },
    )


def _canonicalize_candidate_title(row: dict) -> str:
    return _canonicalize_candidate_title_impl(row)


def _canonical_candidate_key(row: dict) -> str:
    doi = normalize_doi(str(row.get("doi") or ""))
    if doi:
        return f"doi:{doi.lower()}"
    arxiv_id = normalize_arxiv_id(str(row.get("arxiv_id") or ""))
    if arxiv_id:
        return f"arxiv:{arxiv_id.lower()}"
    title = _canonicalize_candidate_title(row)
    title_key = _normalize_title_for_key(title)
    if title_key:
        return f"title:{title_key}:{str(row.get('year') or '').strip()}"
    return _candidate_dedup_key(row)


def _candidate_preference_key(row: dict) -> tuple[float, int, int, int]:
    score = float(row.get("score") or 0.0)
    domain_bonus = _domain_quality_adjustment(str(row.get("url") or ""), str(row.get("title") or ""))
    doi_present = 1 if normalize_doi(str(row.get("doi") or "")) else 0
    arxiv_present = 1 if normalize_arxiv_id(str(row.get("arxiv_id") or "")) else 0
    meta_rich = 0
    if str(row.get("authors") or "").strip():
        meta_rich += 1
    if str(row.get("affiliations") or "").strip():
        meta_rich += 1
    if str(row.get("abstract_snippet") or row.get("abstract") or "").strip():
        meta_rich += 1
    return (round(score + domain_bonus, 6), doi_present + arxiv_present, meta_rich, len(str(row.get("title") or "")))


def _candidate_matches_intent_guard(row: dict, intent: dict) -> bool:
    must_match = intent.get("must_match") if isinstance(intent.get("must_match"), dict) else {}
    context_parts = [
        str(row.get("title") or ""),
        str(row.get("authors") or ""),
        str(row.get("affiliations") or ""),
        str(row.get("abstract_snippet") or row.get("abstract") or ""),
        str(row.get("url") or ""),
        str(row.get("venue") or ""),
    ]
    aliases = row.get("aliases")
    if isinstance(aliases, list):
        context_parts.extend(str(v) for v in aliases[:8])
    context = " ".join(context_parts).lower()

    def _hits(values: Any) -> bool:
        if not isinstance(values, list) or not values:
            return True
        for raw in values:
            terms = [tok for tok in re.findall(r"[A-Za-z0-9][A-Za-z0-9\-]{2,}", str(raw or "").lower()) if len(tok) >= 3]
            if not terms:
                continue
            if all(term in context for term in terms):
                return True
        return False

    if not _hits(must_match.get("institution_any")):
        return False
    if not _hits(must_match.get("author_any")):
        return False
    if not _hits(must_match.get("venue_any")):
        return False
    year_gte = _safe_int(must_match.get("year_gte"))
    if year_gte is not None:
        year = _safe_int(row.get("year"))
        if year is None or year < year_gte:
            return False
    year_lte = _safe_int(must_match.get("year_lte"))
    if year_lte is not None:
        year = _safe_int(row.get("year"))
        if year is None or year > year_lte:
            return False
    return True


def _deterministic_canonicalize_candidates(candidates: list[dict]) -> tuple[list[dict], dict]:
    def _is_tail_variant_title(base: str, other: str) -> bool:
        a = str(base or "").strip()
        b = str(other or "").strip()
        if not a or not b:
            return False
        short, long = (a, b) if len(a) <= len(b) else (b, a)
        if not long.lower().startswith(short.lower()):
            return False
        tail = long[len(short) :].strip()
        if not tail:
            return False
        words = [w for w in re.findall(r"[A-Za-z][A-Za-z'\-]{1,}", tail) if w]
        if not words or len(words) > 3:
            return False
        return all(w[:1].isupper() for w in words)

    grouped: dict[str, list[dict]] = {}
    for row in candidates:
        if not isinstance(row, dict):
            continue
        title = _canonicalize_candidate_title(row)
        if not title:
            continue
        clean = dict(row)
        clean["title"] = title
        grouped.setdefault(_canonical_candidate_key(clean), []).append(clean)
    out: list[dict] = []
    dropped = 0
    for _key, rows in grouped.items():
        best = max(rows, key=_candidate_preference_key)
        merged = dict(best)
        merged["title"] = _canonicalize_candidate_title(best)
        alias_titles = _unique_nonempty([str(item.get("title") or "") for item in rows], limit=12)
        if alias_titles:
            merged["aliases"] = alias_titles
        for field in ("doi", "arxiv_id", "year", "authors", "affiliations", "abstract_snippet"):
            if str(merged.get(field) or "").strip():
                continue
            for item in rows:
                value = str(item.get(field) or "").strip()
                if value:
                    merged[field] = value
                    break
        out.append(merged)
        dropped += max(0, len(rows) - 1)
    ranked = _rank_candidates(out)
    deduped: list[dict] = []
    for cand in ranked:
        title = str(cand.get("title") or "")
        year = str(cand.get("year") or "")
        merged_into_existing = False
        for kept in deduped:
            if str(kept.get("year") or "") != year:
                continue
            kept_title = str(kept.get("title") or "")
            if not _is_tail_variant_title(kept_title, title):
                continue
            aliases = _unique_nonempty(
                [*list(kept.get("aliases") or []), str(cand.get("title") or "")],
                limit=16,
            )
            if aliases:
                kept["aliases"] = aliases
            merged_into_existing = True
            dropped += 1
            break
        if not merged_into_existing:
            deduped.append(cand)
    return _rank_candidates(deduped), {"groups": len(grouped), "dropped": dropped}


def _canonicalize_candidates_with_llm(
    *,
    candidates: list[dict],
    user_prompt: str,
    intent: dict,
    model: str,
    api_key_env: str,
    timeout_s: float = 35.0,
    raw_event_fn: Callable[[str, Any], str] | None = None,
    llm_op_id: str = "",
) -> tuple[list[dict], dict]:
    if not candidates:
        return [], {"status": "skipped", "reason": "no_candidates", "scope": "cycle_local"}
    prepared: list[dict] = []
    index: dict[str, dict] = {}
    for idx, row in enumerate(candidates, start=1):
        cid = f"c{idx:04d}"
        title = _canonicalize_candidate_title(row)
        if not title:
            continue
        entry = {
            "candidate_id": cid,
            "title": title,
            "year": str(row.get("year") or ""),
            "doi": normalize_doi(str(row.get("doi") or "")),
            "arxiv_id": normalize_arxiv_id(str(row.get("arxiv_id") or "")),
            "url": str(row.get("url") or ""),
            "source": str(row.get("source") or ""),
            "score": float(row.get("score") or 0.0),
            "authors": str(row.get("authors") or ""),
            "affiliations": str(row.get("affiliations") or ""),
            "abstract_snippet": _peek_text(str(row.get("abstract_snippet") or row.get("abstract") or ""), 320),
            "evidence": _peek_text(str(row.get("abstract") or ""), 360),
        }
        prepared.append(entry)
        index[cid] = dict(row)
        index[cid]["title"] = title
    if not prepared:
        return _rank_candidates(candidates), {"status": "fallback", "reason": "no_prepared_candidates", "scope": "cycle_local"}
    user_payload = {
        "task": "canonicalize_extracted_candidates",
        "policy": {
            "no_new_papers": True,
            "allowed_actions": ["merge", "normalize_title", "drop"],
            "drop_reasons": ["alias_of_existing", "malformed_title", "non_match", "insufficient_evidence"],
        },
        "user_prompt": user_prompt,
        "intent": intent,
        "candidates": prepared,
    }
    messages = [
        {
            "role": "system",
            "content": (
                "You clean and deduplicate extracted academic paper candidates. "
                "Return JSON only with key `items` as list. "
                "Each item keys: candidate_id, decision, canonical_candidate_id, canonical_title, verification, reason. "
                "decision is one of keep|drop. verification is one of match|uncertain|non_match. "
                "Do not introduce new papers or new candidate ids. "
                "If two rows are the same paper with malformed variants, merge by pointing to the same canonical_candidate_id. "
                "Interpret institution filters as: the paper matches if at least one coauthor affiliation matches the institution filter. "
                "Interpret author filters as: the paper matches if at least one author name matches the author filter. "
                "Do not require majority authorship or first-author authorship unless the prompt explicitly requires that. "
                "Keep strong paper rows even when some metadata fields are missing."
            ),
        },
        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=True)},
    ]
    message_metrics = _estimate_messages_metrics(messages)
    if raw_event_fn is not None:
        raw_event_fn(
            "canonicalize_llm_request",
            {
                "model": model,
                "api_key_env": api_key_env,
                "op_id": llm_op_id,
                "messages": messages,
                "candidate_count": len(prepared),
                "input_chars": int(message_metrics.get("input_chars") or 0),
                "input_tokens_est": int(message_metrics.get("input_tokens_est") or 0),
            },
        )
    payload = _openai_complete_json(
        model=model,
        api_key_env=api_key_env,
        messages=messages,
        timeout_s=timeout_s,
        max_tokens=1800,
        max_retries=0,
    )
    if raw_event_fn is not None:
        response_payload: dict[str, Any]
        if isinstance(payload, dict):
            response_payload = dict(payload)
            response_payload["op_id"] = llm_op_id
        else:
            response_payload = {"op_id": llm_op_id, "payload": payload}
        payload_text = json.dumps(response_payload, ensure_ascii=False)
        response_payload["output_chars"] = len(payload_text)
        response_payload["output_tokens_est"] = _estimate_text_tokens(payload_text)
        raw_event_fn("canonicalize_llm_response", response_payload)

    rows = payload.get("items") if isinstance(payload, dict) else []
    if not isinstance(rows, list) or not rows:
        fallback, info = _deterministic_canonicalize_candidates(list(index.values()))
        info.update({"status": "fallback", "reason": "empty_llm_items", "scope": "cycle_local"})
        return fallback, info

    plan_by_id: dict[str, dict] = {}
    verification_counts = {"match": 0, "uncertain": 0, "non_match": 0}
    drop_reasons: dict[str, int] = {}
    overridden_non_match = 0
    for item in rows:
        if not isinstance(item, dict):
            continue
        cid = str(item.get("candidate_id") or "").strip()
        if cid not in index:
            continue
        decision = str(item.get("decision") or "").strip().lower()
        if decision not in {"keep", "drop"}:
            decision = "keep"
        rep = str(item.get("canonical_candidate_id") or cid).strip()
        if rep not in index:
            rep = cid
        canonical_title = _canonicalize_candidate_title({"title": str(item.get("canonical_title") or "")})
        if not canonical_title:
            canonical_title = str(index.get(rep, {}).get("title") or "")
        # Guardrail: do not allow hallucinated title replacements.
        rep_title = str(index.get(rep, {}).get("title") or "")
        if canonical_title and rep_title:
            a = _normalize_title_for_key(canonical_title)
            b = _normalize_title_for_key(rep_title)
            if a and b and a not in b and b not in a:
                canonical_title = rep_title
        plan_by_id[cid] = {
            "decision": decision,
            "rep": rep,
            "canonical_title": canonical_title,
            "verification": str(item.get("verification") or "").strip().lower(),
            "reason": str(item.get("reason") or "").strip(),
        }
        verification = str(plan_by_id[cid].get("verification") or "")
        if verification in verification_counts:
            verification_counts[verification] += 1
        reason = str(plan_by_id[cid].get("reason") or "").strip()
        if verification == "non_match":
            if _candidate_matches_intent_guard(index[cid], intent):
                plan_by_id[cid]["decision"] = "keep"
                plan_by_id[cid]["verification"] = "uncertain"
                plan_by_id[cid]["reason"] = f"guard_keep:{reason or 'local_intent_match'}"
                verification_counts["non_match"] = max(0, verification_counts["non_match"] - 1)
                verification_counts["uncertain"] += 1
                overridden_non_match += 1
            else:
                plan_by_id[cid]["decision"] = "drop"
                drop_reasons[reason or "non_match"] = int(drop_reasons.get(reason or "non_match", 0)) + 1
        elif decision == "drop":
            drop_reasons[reason or "drop"] = int(drop_reasons.get(reason or "drop", 0)) + 1

    grouped: dict[str, list[str]] = {}
    for cid in index:
        plan = plan_by_id.get(cid, {"decision": "keep", "rep": cid})
        if plan.get("decision") == "drop":
            continue
        rep = str(plan.get("rep") or cid)
        grouped.setdefault(rep, []).append(cid)
    if not grouped:
        fallback, info = _deterministic_canonicalize_candidates(list(index.values()))
        info.update({"status": "fallback", "reason": "all_dropped", "scope": "cycle_local"})
        return fallback, info

    out: list[dict] = []
    dropped_count = 0
    for rep, members in grouped.items():
        rows_for_group = [index[cid] for cid in members if cid in index]
        if not rows_for_group:
            continue
        best = max(rows_for_group, key=_candidate_preference_key)
        merged = dict(best)
        merged["title"] = str((plan_by_id.get(rep) or {}).get("canonical_title") or merged.get("title") or "")
        merged["aliases"] = _unique_nonempty([str(index[cid].get("title") or "") for cid in members], limit=12)
        merged["canonical_member_ids"] = members
        for field in ("doi", "arxiv_id", "year", "authors", "affiliations", "abstract_snippet"):
            if str(merged.get(field) or "").strip():
                continue
            for cid in members:
                value = str(index[cid].get(field) or "").strip()
                if value:
                    merged[field] = value
                    break
        out.append(merged)
        dropped_count += max(0, len(members) - 1)
    return _rank_candidates(out), {
        "status": "ok",
        "input_count": len(index),
        "output_count": len(out),
        "dropped": dropped_count,
        "scope": "cycle_local",
        "verification_counts": verification_counts,
        "drop_reasons": drop_reasons,
        "overridden_non_match": overridden_non_match,
    }

def _openai_complete_json(
    *,
    model: str,
    api_key_env: str,
    messages: list[dict],
    timeout_s: float | None = None,
    max_tokens: int | None = None,
    max_retries: int | None = None,
) -> dict:
    return _openai_complete_json_impl(
        model=model,
        api_key_env=api_key_env,
        messages=messages,
        timeout_s=timeout_s,
        max_tokens=max_tokens,
        max_retries=max_retries,
    )


def _agent_next_action_llm(
    *,
    working_state: dict,
    queries_per_cycle: int,
    model: str,
    api_key_env: str,
) -> dict:
    messages = _build_agent_messages(
        working_state=working_state,
        queries_per_cycle=queries_per_cycle,
        supported_actions=sorted(AGENT_SUPPORTED_ACTIONS),
    )
    message_metrics = _estimate_messages_metrics(messages)
    payload = _openai_complete_json(model=model, api_key_env=api_key_env, messages=messages, max_retries=1)
    return _parse_agent_action_response(
        payload=payload,
        queries_per_cycle=queries_per_cycle,
        supported_actions=AGENT_SUPPORTED_ACTIONS,
        debug_metrics=message_metrics,
    )

def _execute_search_web_action(
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
    return _execute_search_web_action_impl(
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
            "normalize_search_queries_fn": _normalize_search_queries,
            "search_web_queries_fn": _search_web_queries,
            "normalize_shortlist_hints_fn": _normalize_shortlist_hints,
            "next_op_id_fn": next_op_id_fn or _next_op_id,
        },
    )


def _execute_extract_content_action(
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
    return _execute_extract_content_action_impl(
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
            "normalize_anchor_terms_fn": _normalize_anchor_terms,
            "resolve_active_extract_filters_fn": _resolve_active_extract_filters,
            "prepare_extract_segments_fn": _prepare_extract_segments,
            "extract_segment_token_budget_fn": _extract_segment_token_budget,
            "normalize_fetch_target_fn": _normalize_fetch_target,
            "extract_target_filters_fn": _extract_target_filters,
            "resolve_extract_intent_fn": _resolve_extract_intent,
            "resolve_extract_anchor_terms_fn": _resolve_extract_anchor_terms,
            "safe_int_fn": _safe_int,
            "reuse_fetched_record_for_target_fn": _reuse_fetched_record_for_target,
            "fetch_target_record_fn": _fetch_target_record,
            "merge_fetched_records_fn": _merge_fetched_records,
            "filter_records_by_urls_fn": _filter_records_by_urls,
            "next_op_id_fn": next_op_id_fn or _next_op_id,
            "emit_progress_fn": _emit_progress,
            "slice_segments_by_token_budget_fn": _slice_segments_by_token_budget,
            "extract_facts_with_llm_fn": _extract_facts_with_llm,
            "candidate_dedup_key_fn": _candidate_dedup_key,
            "to_paper_candidates_from_facts_fn": _to_paper_candidates_from_facts,
            "collect_candidate_url_inputs_from_records_fn": _collect_candidate_url_inputs_from_records,
            "extract_candidate_urls_with_llm_fn": _extract_candidate_urls_with_llm,
            "estimate_messages_metrics_fn": _estimate_messages_metrics,
            "openai_complete_json_fn": _openai_complete_json,
            "peek_text_fn": _peek_text,
            "context_limit_tokens": DEFAULT_EXTRACTOR_CONTEXT_LIMIT_TOKENS,
            "safety_margin": DEFAULT_EXTRACTOR_SAFETY_MARGIN,
            "output_token_reserve": DEFAULT_EXTRACTOR_OUTPUT_TOKEN_RESERVE,
        },
    )


def _execute_agent_action(
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
        return _execute_search_web_action(
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
        return _execute_extract_content_action(
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

def run_extract_agentic_local(
    project_id: str,
    *,
    institution: str = "",
    year_gte: int = 0,
    url_contains: str = "",
) -> Path:
    pdir = project_dir(project_id)
    paths = _agentic_paths(pdir)
    fetch_dir = paths["result"].parent / "fetch_raw"
    out_path = paths["result"].parent / "agentic_extract_local.yaml"

    filters: dict[str, Any] = {}
    if str(institution or "").strip():
        filters["institution"] = str(institution).strip()
    if int(year_gte or 0) > 0:
        filters["year_gte"] = int(year_gte)

    url_hint = str(url_contains or "").strip().lower()
    facts: list[dict] = []
    inspected: list[dict] = []
    for idx, path in enumerate(sorted(fetch_dir.glob("*.html")), start=1):
        raw_html = path.read_text(encoding="utf-8", errors="ignore")
        text = _extract_listing_text_with_fallback(raw_html, max_chars=8_000_000)
        segments = _extract_html_structural_segments(raw_html, max_segments=240, max_chars=1800)
        pseudo_url = f"file:{path.name}"
        if url_hint and url_hint not in pseudo_url.lower() and url_hint not in text.lower():
            continue
        year = _extract_year_best(text, year_gte=_safe_int(filters.get("year_gte"))) or str(year_gte or "")
        listing_facts = _extract_listing_candidates_from_segments(
            session_id="local",
            cycle_index=0,
            target_id=f"local-{idx}",
            url=pseudo_url,
            url_title=path.name,
            segments=segments,
            filters=filters,
            fallback_year=year,
        )
        facts.extend(listing_facts)
        inspected.append(
            {
                "file": path.name,
                "segment_count": len(segments),
                "text_chars": len(text),
                "extracted_count": len(listing_facts),
            }
        )

    candidates = _to_paper_candidates_from_facts(facts)
    payload = {
        "artifact_type": "agentic_extract_local",
        "schema_version": "0.1.0",
        "filters": filters,
        "url_contains": str(url_contains or ""),
        "inspected": inspected,
        "result_count": len(candidates),
        "papers": [
            {
                "title": str(row.get("title") or ""),
                "year": str(row.get("year") or ""),
                "doi": str(row.get("doi") or ""),
                "arxiv_id": str(row.get("arxiv_id") or ""),
                "source_url": str(row.get("url") or ""),
                "confidence": float(row.get("score") or 0.0),
                "evidence": _peek_text(str(row.get("abstract") or ""), 220),
            }
            for row in candidates
        ],
        "updated_at": _utc_now(),
    }
    write_yaml(out_path, payload)
    return out_path


def run_retrieve_agentic(
    project_id: str,
    *,
    prompt: str = "",
    top_n: int = 5,
    final_limit: int = 0,
    debug_retrieval: bool | None = None,
    progress_callback: ProgressCallback | None = None,
) -> Path:
    pdir = project_dir(project_id)
    paths = _agentic_paths(pdir)
    pmeta = load(pdir / "project.yaml")
    retrieval_cfg = (pmeta.get("retrieval") or {}) if isinstance(pmeta, dict) else {}
    agentic_cfg = (retrieval_cfg.get("agentic") or {}) if isinstance(retrieval_cfg, dict) else {}
    llm_cfg = (agentic_cfg.get("llm") or {}) if isinstance(agentic_cfg, dict) else {}

    requested_top_n = _read_int(top_n or agentic_cfg.get("top_n", 5), 5)
    effective_top_n = max(1, requested_top_n)
    final_limit_cfg = _read_int(agentic_cfg.get("final_result_limit", 0), 0)
    effective_final_limit = max(0, _read_int(final_limit if int(final_limit or 0) > 0 else final_limit_cfg, 0))
    effective_max_cycles = max(1, _read_int(agentic_cfg.get("max_cycles", 3), 3))
    queries_per_cycle = max(1, _read_int(agentic_cfg.get("queries_per_cycle", 4), 4))
    web_results_per_query = max(1, _read_int(agentic_cfg.get("web_results_per_query", 8), 8))
    searxng_timeout_s = float(agentic_cfg.get("searxng_timeout_s", 8.0) or 8.0)
    searxng_categories = _read_str(agentic_cfg.get("searxng_categories"), "general")
    llm_model = _read_str(llm_cfg.get("model"), "deepseek/deepseek-chat")
    llm_extractor_model = _read_str(llm_cfg.get("extractor_model"), "deepseek/deepseek-chat")
    llm_api_key_env = _read_str(llm_cfg.get("api_key_env"), "DS_API_KEY")
    extract_use_llm_extractor = _read_bool(agentic_cfg.get("extract_use_llm_extractor"), True)
    effective_debug_retrieval = bool(debug_retrieval) if debug_retrieval is not None else False
    _emit_progress(
        progress_callback,
        event="agentic_start",
        project_id=project_id,
        top_n=effective_top_n,
        final_result_limit=effective_final_limit,
        max_cycles=effective_max_cycles,
        queries_per_cycle=queries_per_cycle,
        web_results_per_query=web_results_per_query,
        searxng_categories=searxng_categories,
        llm_model=llm_model,
        llm_extractor_model=llm_extractor_model,
        llm_api_key_env=llm_api_key_env,
        extract_use_llm_extractor=extract_use_llm_extractor,
        debug_retrieval=effective_debug_retrieval,
    )

    resolved_prompt = str(prompt or "").strip()
    if not resolved_prompt:
        raise ValueError("Prompt is required")
    resolved_session_id = _new_session_id(resolved_prompt)

    searxng_url = str(os.environ.get("SEARXNG_URL") or "").strip()
    loop = _AgenticSearchLoop(
        paths=paths,
        prompt=resolved_prompt,
        session_id=resolved_session_id,
        max_cycles=effective_max_cycles,
        searxng_url=searxng_url,
        debug_retrieval=effective_debug_retrieval,
        execute_action_fn=_execute_agent_action,
        agent_next_action_fn=_agent_next_action_llm,
        emit_progress_fn=_emit_progress,
        apply_search_action_result_fn=_apply_search_action_result,
        progress_callback=progress_callback,
        agent_config=_AgentConfig(
            max_queries_per_turn=queries_per_cycle,
            llm_model=llm_model,
            api_key_env=llm_api_key_env,
        ),
        extract_config=_ExtractConfig(
            llm_model=llm_extractor_model,
            api_key_env=llm_api_key_env,
            use_llm_extractor=extract_use_llm_extractor,
        ),
        search_config=_SearchConfig(
            shortlist_size=effective_top_n,
            results_per_query=web_results_per_query,
            timeout_s=searxng_timeout_s,
            categories=searxng_categories,
        ),
        result_config=_ResultConfig(
            display_limit=effective_final_limit,
        ),
    )
    return loop.run()
