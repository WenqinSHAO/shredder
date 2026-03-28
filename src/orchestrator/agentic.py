from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from src.orchestrator import agentic_actions as actions_mod
from src.orchestrator.agentic_contracts import (
    _build_agent_messages,
    _parse_agent_action_response,
)
from src.orchestrator.agentic_extract_candidates import (
    canonicalize_candidate_title as _canonicalize_candidate_title_impl,
    extract_listing_candidates_from_segments as _extract_listing_candidates_from_segments_impl,
    extract_year_best as _extract_year_best_impl,
    to_paper_candidates_from_facts as _to_paper_candidates_from_facts_impl,
)
from src.orchestrator.agentic_extract import (
    extract_segment_token_budget as _extract_segment_token_budget_impl,
    extract_target_filters as _extract_target_filters_impl,
    infer_by_subject_from_prompt as _infer_by_subject_from_prompt_impl,
    infer_subject_kind as _infer_subject_kind_impl,
    infer_year_gte_from_prompt as _infer_year_gte_from_prompt_impl,
    normalize_fetch_target as _normalize_fetch_target_impl,
    resolve_extract_intent as _resolve_extract_intent_impl,
    slice_segments_by_token_budget as _slice_segments_by_token_budget_impl,
)
from src.orchestrator.agentic_llm import (
    estimate_messages_metrics as _estimate_messages_metrics_impl,
    openai_complete_json as _openai_complete_json_impl,
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
    _filter_records_by_urls,
    _filter_search_rows,
    _host_from_url,
    _make_hit_id,
    _merge_fetched_records,
    _merge_url_hits,
    _peek_text,
    _reuse_fetched_record_for_target,
    _select_diverse_shortlist,
    _to_url_hits as _to_url_hits_impl,
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


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


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
    message_metrics = _estimate_messages_metrics_impl(messages)
    payload = _openai_complete_json_impl(model=model, api_key_env=api_key_env, messages=messages, max_retries=1)
    return _parse_agent_action_response(
        payload=payload,
        queries_per_cycle=queries_per_cycle,
        supported_actions=AGENT_SUPPORTED_ACTIONS,
        debug_metrics=message_metrics,
    )

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
        year = _extract_year_best_impl(text, year_gte=_safe_int(filters.get("year_gte"))) or str(year_gte or "")
        listing_facts = _extract_listing_candidates_from_segments_impl(
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

    candidates = _to_paper_candidates_from_facts_impl(
        facts,
        canonicalize_candidate_title_fn=_canonicalize_candidate_title_impl,
    )
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
        execute_action_fn=actions_mod.execute_agent_action,
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
