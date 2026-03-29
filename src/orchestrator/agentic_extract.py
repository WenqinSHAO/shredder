from __future__ import annotations

from typing import Any, Callable

from src.orchestrator.agentic_extract_candidates import (
    canonicalize_discovered_url as _canonicalize_discovered_url_impl,
)
from src.orchestrator.agentic_extract_llm import (
    extract_candidate_urls_with_llm as _extract_candidate_urls_with_llm_impl,
    extract_facts_with_llm as _extract_facts_with_llm_impl,
    extract_segment_token_budget as _extract_segment_token_budget_impl,
    slice_segments_by_token_budget as _slice_segments_by_token_budget_impl,
)

ProgressCallback = Callable[[dict], None]


def extract_segment_token_budget(
    *,
    record: dict,
    filters: dict,
    user_prompt: str,
    intent: dict,
    context_limit_tokens: int,
    safety_margin: float,
    output_token_reserve: int,
    deps: dict[str, Any],
) -> int:
    return _extract_segment_token_budget_impl(
        record=record,
        filters=filters,
        user_prompt=user_prompt,
        intent=intent,
        context_limit_tokens=context_limit_tokens,
        safety_margin=safety_margin,
        output_token_reserve=output_token_reserve,
        deps=deps,
    )


def slice_segments_by_token_budget(
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


def extract_facts_with_llm(
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
    deps: dict[str, Any],
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
        deps=deps,
    )


def extract_candidate_urls_with_llm(
    *,
    user_prompt: str,
    intent: dict[str, Any],
    paper_candidates: list[dict[str, Any]],
    anchor_terms: list[str],
    known_urls: list[str],
    link_candidates: list[dict[str, Any]],
    model: str,
    api_key_env: str,
    timeout_s: float = 45.0,
    max_retries: int = 0,
    raw_event_fn: Callable[[str, Any], str] | None = None,
    llm_op_id: str = "",
    deps: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    llm_deps = dict(deps)
    llm_deps["canonicalize_discovered_url_fn"] = _canonicalize_discovered_url_impl
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
        deps=llm_deps,
    )
