from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

from src.orchestrator.agentic_text import (
    _normalize_anchor_terms,
    _safe_int,
    _unique_nonempty,
)


def extract_target_filters(item: dict) -> dict:
    filters = item.get("filters")
    if isinstance(filters, dict):
        return dict(filters)
    match = item.get("match")
    if not isinstance(match, dict):
        return {}

    def _first_text(value: Any) -> str:
        if isinstance(value, list):
            for entry in value:
                text = str(entry or "").strip()
                if text:
                    return text
            return ""
        return str(value or "").strip()

    out: dict[str, Any] = {}
    institution = _first_text(match.get("institution_any") or match.get("institution"))
    author = _first_text(match.get("author_any") or match.get("author"))
    venue = _first_text(match.get("venue_any") or match.get("venue"))
    topic = _first_text(match.get("topic_any") or match.get("topic"))
    year_gte = match.get("year_gte")
    if institution:
        out["institution"] = institution
    if author:
        out["author"] = author
    if venue:
        out["venue"] = venue
    if topic:
        out["topic"] = topic
    try:
        if year_gte not in (None, ""):
            out["year_gte"] = int(year_gte)
    except (TypeError, ValueError):
        pass
    return out


def normalize_fetch_target(item: dict, idx: int) -> dict:
    url = str(item.get("url") or "").strip()
    title = str(item.get("title") or item.get("url_title") or "").strip()
    why = str(item.get("why") or "").strip()
    target_id = str(item.get("target_id") or "").strip() or f"fetch-{idx}"
    status = str(item.get("status") or "todo").strip() or "todo"
    out = {
        "target_id": target_id,
        "url": url,
        "title": title,
        "why": why,
        "status": status,
    }
    filters = extract_target_filters(item)
    if filters:
        out["filters"] = dict(filters)
    anchor_terms = _normalize_anchor_terms(item.get("anchor_terms"))
    if anchor_terms:
        out["anchor_terms"] = list(anchor_terms)
    match = item.get("match")
    if isinstance(match, dict):
        out["match"] = dict(match)
    return out


def infer_year_gte_from_prompt(prompt: str) -> int | None:
    text = str(prompt or "")
    years = [int(y) for y in re.findall(r"\b(20\d{2})\b", text)]
    if not years:
        return None
    lowered = text.lower()
    if any(tok in lowered for tok in ("since", "from", "after", ">=", "at least", "newer than")):
        return min(years)
    if any(tok in lowered for tok in ("in ", "during", "for ", "within")):
        return max(years)
    return max(years)


def infer_by_subject_from_prompt(prompt: str) -> str:
    text = str(prompt or "").strip()
    if not text:
        return ""
    match = re.search(r"\bby\s+([A-Za-z][A-Za-z0-9 .,&\-]{1,80}?)(?:\s+at\s+|\s+in\s+|\s+since\s+|\s+from\s+|$)", text, flags=re.IGNORECASE)
    if not match:
        return ""
    return re.sub(r"\s+", " ", str(match.group(1) or "").strip(" ,.;:"))


def infer_subject_kind(subject: str) -> str:
    text = re.sub(r"\s+", " ", str(subject or "")).strip()
    if not text:
        return ""
    lowered = text.lower()
    org_markers = (
        "university",
        "college",
        "institute",
        "laboratory",
        "laboratories",
        "lab",
        "labs",
        "research",
        "cloud",
        "corp",
        "corporation",
        "inc",
        "llc",
        "ltd",
        "company",
        "technologies",
        "technology",
        "systems",
        "google",
        "alibaba",
        "bytedance",
        "microsoft",
        "meta",
        "amazon",
        "apple",
        "openai",
        "nvidia",
    )
    if any(marker in lowered for marker in org_markers):
        return "institution"
    parts = [part for part in text.split() if part]
    if len(parts) == 1:
        return "institution"
    if 2 <= len(parts) <= 4 and all(re.fullmatch(r"[A-Z][A-Za-z'\-]+", part) for part in parts):
        return "author"
    return "institution"


def resolve_extract_intent(
    *,
    params: dict,
    filters: dict,
    user_prompt: str,
) -> dict:
    base = params.get("intent") if isinstance(params.get("intent"), dict) else {}
    must_match = base.get("must_match") if isinstance(base.get("must_match"), dict) else {}
    return_fields = base.get("return_fields") if isinstance(base.get("return_fields"), list) else []
    selection_policy = str(base.get("selection_policy") or "").strip() or "strict_row_match"
    confidence_policy = base.get("confidence_policy") if isinstance(base.get("confidence_policy"), dict) else {}
    anchor_terms = _normalize_anchor_terms(base.get("anchor_terms"))

    institution = str(filters.get("institution") or "").strip()
    author = str(filters.get("author") or "").strip()
    venue = str(filters.get("venue") or "").strip()
    topic = str(filters.get("topic") or user_prompt).strip()
    by_subject = infer_by_subject_from_prompt(user_prompt)
    subject_kind = infer_subject_kind(by_subject)
    if by_subject and not institution and not author:
        if subject_kind == "author":
            author = by_subject
        else:
            institution = by_subject
    year_gte = _safe_int(filters.get("year_gte"))
    if year_gte is None:
        year_gte = _safe_int(must_match.get("year_gte"))
    if year_gte is None:
        year_gte = infer_year_gte_from_prompt(user_prompt)

    institution_any = [institution] if institution else []
    author_any = [author] if author else []
    venue_any = [venue] if venue else []
    topic_any = [topic] if topic else []

    if isinstance(must_match.get("institution_any"), list):
        institution_any = _unique_nonempty([*institution_any, *[str(v) for v in must_match.get("institution_any")]], limit=12)
    if isinstance(must_match.get("author_any"), list):
        author_any = _unique_nonempty([*author_any, *[str(v) for v in must_match.get("author_any")]], limit=12)
    if isinstance(must_match.get("venue_any"), list):
        venue_any = _unique_nonempty([*venue_any, *[str(v) for v in must_match.get("venue_any")]], limit=12)
    if isinstance(must_match.get("topic_any"), list):
        topic_any = _unique_nonempty([*topic_any, *[str(v) for v in must_match.get("topic_any")]], limit=12)

    if not return_fields:
        return_fields = [
            "paper_title_raw",
            "paper_title_normalized",
            "authors",
            "affiliations",
            "venue",
            "year",
            "doi",
            "arxiv_id",
            "abstract_snippet",
            "institution_hits",
            "match_decision",
            "decision_reason",
            "evidence_span",
            "confidence",
        ]

    return {
        "query_goal": str(base.get("query_goal") or user_prompt),
        "anchor_terms": anchor_terms,
        "must_match": {
            "institution_any": institution_any,
            "author_any": author_any,
            "venue_any": venue_any,
            "topic_any": topic_any,
            "year_gte": year_gte,
            "year_lte": _safe_int(must_match.get("year_lte")),
        },
        "return_fields": [str(v) for v in return_fields if str(v).strip()],
        "selection_policy": selection_policy,
        "match_semantics": {
            "institution_rule": "at_least_one_matching_coauthor_affiliation",
            "author_rule": "at_least_one_matching_author_name",
            "by_subject_interpretation": subject_kind or "",
            "strong_title_policy": "keep_if_title_and_local_evidence_are_strong_even_when_metadata_is_partial",
        },
        "confidence_policy": {
            "min_confidence_match": float(confidence_policy.get("min_confidence_match", 0.55) or 0.55),
            "min_confidence_uncertain": float(confidence_policy.get("min_confidence_uncertain", 0.35) or 0.35),
        },
    }


def prepare_extract_target(
    *,
    row: dict[str, Any],
    target_scope_by_url: dict[str, dict[str, Any]],
    filters: dict[str, Any],
    anchor_terms: list[str],
    must_match: dict[str, Any],
    extract_intent: dict[str, Any],
    user_prompt: str,
    context_limit_tokens: int,
    safety_margin: float,
    output_token_reserve: int,
    deps: dict[str, Any],
) -> dict[str, Any]:
    normalize_anchor_terms_fn = deps["normalize_anchor_terms_fn"]
    resolve_active_extract_filters_fn = deps["resolve_active_extract_filters_fn"]
    prepare_extract_segments_fn = deps["prepare_extract_segments_fn"]
    extract_segment_token_budget_fn = deps["extract_segment_token_budget_fn"]

    row_aliases = {
        str(row.get("url") or "").strip(),
        str(row.get("requested_url") or "").strip(),
        *[str(v).strip() for v in (row.get("url_aliases") or []) if str(v).strip()],
    }
    row_scope = next((scope for url, scope in target_scope_by_url.items() if url in row_aliases), {})
    scoped_filters = dict(filters)
    scoped_filters.update(dict(row_scope.get("filters") or {}))
    row_anchor_terms = normalize_anchor_terms_fn(list(anchor_terms) + list(row_scope.get("anchor_terms") or []))
    active_filters = resolve_active_extract_filters_fn(scoped_filters, must_match, user_prompt)
    ranked_segments, batch_mode = prepare_extract_segments_fn(
        row=row,
        filters=active_filters,
        anchor_terms=row_anchor_terms,
    )
    token_budget = extract_segment_token_budget_fn(
        record=row,
        filters=active_filters,
        user_prompt=user_prompt,
        intent=extract_intent,
        context_limit_tokens=context_limit_tokens,
        safety_margin=safety_margin,
        output_token_reserve=output_token_reserve,
    )
    return {
        "row": row,
        "filters": active_filters,
        "anchor_terms": row_anchor_terms,
        "ranked_segments": ranked_segments,
        "batch_mode": batch_mode,
        "token_budget": token_budget,
    }


def resolve_extract_request(
    *,
    session_id: str,
    cycle_index: int,
    params: dict,
    paths: dict[str, Path],
    user_prompt: str,
    timeout_s: float,
    runtime_state: dict[str, Any],
    raw_event_fn: Callable[[str, Any, list[str] | None], str] | None,
    deps: dict[str, Any],
) -> dict[str, Any]:
    normalize_fetch_target_fn = deps["normalize_fetch_target_fn"]
    extract_target_filters_fn = deps["extract_target_filters_fn"]
    resolve_extract_intent_fn = deps["resolve_extract_intent_fn"]
    resolve_extract_anchor_terms_fn = deps["resolve_extract_anchor_terms_fn"]
    safe_int_fn = deps["safe_int_fn"]
    reuse_fetched_record_for_target_fn = deps["reuse_fetched_record_for_target_fn"]
    fetch_target_record_fn = deps["fetch_target_record_fn"]
    merge_fetched_records_fn = deps["merge_fetched_records_fn"]
    filter_records_by_urls_fn = deps["filter_records_by_urls_fn"]
    next_op_id_fn = deps["next_op_id_fn"]
    params_targets = params.get("targets") if isinstance(params.get("targets"), list) else []
    requested_target_ids = [str(v).strip() for v in (params.get("target_ids") or []) if str(v).strip()]
    requested_urls_from_params = [str(v).strip() for v in (params.get("urls") or []) if str(v).strip()]
    target_scope_by_url: dict[str, dict[str, Any]] = {}
    requested_urls: list[str] = []
    normalized_targets: list[dict[str, Any]] = []
    for idx, item in enumerate(params_targets, start=1):
        if not isinstance(item, dict):
            continue
        normalized = normalize_fetch_target_fn(item, idx)
        if not normalized["url"]:
            continue
        normalized_targets.append(normalized)
        target_scope_by_url[normalized["url"]] = {
            "filters": extract_target_filters_fn(item),
            "anchor_terms": deps["normalize_anchor_terms_fn"](item.get("anchor_terms")),
        }
        requested_urls.append(normalized["url"])

    hit_id_to_url = {
        str(row.get("hit_id") or "").strip(): str(row.get("url") or "").strip()
        for row in (runtime_state.get("url_hits") or [])
        if isinstance(row, dict)
    }
    mapped_urls = [hit_id_to_url.get(target_id, "") for target_id in requested_target_ids]
    for url in requested_urls_from_params + mapped_urls:
        if url and url not in requested_urls:
            requested_urls.append(url)

    if not normalized_targets and requested_urls:
        shared_filters = extract_target_filters_fn({"filters": params.get("filters")})
        shared_anchor_terms = deps["normalize_anchor_terms_fn"](params.get("anchor_terms"))
        for idx, url in enumerate(requested_urls, start=1):
            normalized = {
                "target_id": f"auto-fetch-{idx}",
                "url": url,
                "title": "",
                "why": "extract_auto_fetch",
                "status": "todo",
            }
            normalized_targets.append(normalized)
            target_scope_by_url[url] = {
                "filters": dict(shared_filters),
                "anchor_terms": list(shared_anchor_terms),
            }

    if not normalized_targets:
        return {
            "target_scope_by_url": {},
            "filters": {},
            "extract_intent": {},
            "anchor_terms": [],
            "records": [],
            "requested_urls": [],
            "auto_fetched_records": [],
        }

    filters = extract_target_filters_fn({"filters": params.get("filters")})
    if not filters:
        for target in normalized_targets:
            scoped = target_scope_by_url.get(str(target.get("url") or ""), {})
            scoped_filters = scoped.get("filters") if isinstance(scoped.get("filters"), dict) else {}
            for key, value in scoped_filters.items():
                if key not in filters and value not in ("", None):
                    filters[key] = value
    extract_intent = resolve_extract_intent_fn(params=params, filters=filters, user_prompt=user_prompt)
    anchor_terms = resolve_extract_anchor_terms_fn(
        params=params,
        filters=filters,
        intent=extract_intent,
        user_prompt=user_prompt,
    )
    if safe_int_fn(filters.get("year_gte")) is None:
        intent_year = safe_int_fn((extract_intent.get("must_match") or {}).get("year_gte"))
        if intent_year is not None:
            filters["year_gte"] = intent_year

    records = [row for row in (runtime_state.get("fetched_records") or []) if isinstance(row, dict)]
    requested_url_set = {url for url in requested_urls if url}

    auto_fetched_records: list[dict] = []
    if bool(params.get("auto_fetch", True)):
        fetched_now: list[dict] = []
        for idx, target in enumerate(normalized_targets, start=1):
            reused = reuse_fetched_record_for_target_fn(records, target, idx)
            if reused is not None:
                fetched_now.append(reused)
                continue
            fetched_now.append(
                fetch_target_record_fn(
                    session_id=session_id,
                    cycle_index=cycle_index,
                    target=target,
                    idx=idx,
                    timeout_s=timeout_s,
                    params=params,
                    paths=paths,
                    raw_event_fn=raw_event_fn,
                    new_op_id_fn=(lambda prefix: next_op_id_fn(runtime_state, prefix)),
                )
            )
        auto_fetched_records = list(fetched_now)
        if fetched_now:
            records = merge_fetched_records_fn(records, fetched_now)
            runtime_state["fetched_records"] = records

    records = [
        row for row in filter_records_by_urls_fn(records, requested_url_set)
        if isinstance(row, dict)
    ]

    return {
        "target_scope_by_url": target_scope_by_url,
        "filters": filters,
        "extract_intent": extract_intent,
        "anchor_terms": anchor_terms,
        "records": records,
        "requested_urls": requested_urls,
        "auto_fetched_records": auto_fetched_records,
    }
