from __future__ import annotations

import json
import re
from typing import Any, Callable

from src.connectors.http import normalize_arxiv_id, normalize_doi
from src.orchestrator.agentic_text import (
    LISTING_HEADING_PHRASES,
    NON_PAPER_TITLE_TOKENS,
    _estimate_text_tokens,
)

MAX_CANDIDATE_URL_PAPERS = 8
MAX_CANDIDATE_URL_KNOWN_URLS = 24
MAX_CANDIDATE_URL_LINKS = 32


def extract_llm_system_prompt() -> str:
    return (
        "You extract academic paper rows from conference/web listing segments. "
        "Return JSON only with key `items` as list of row objects. "
        "Each row object keys: "
        "is_paper, paper_title_raw, paper_title_normalized, authors, affiliations, author_affiliations, venue, year, doi, arxiv_id, abstract, abstract_snippet, "
        "institution_hits, match_decision, decision_reason, evidence_span, confidence. "
        "Hard rules: "
        "Extract one row per concrete paper entry. "
        "Institution/author matching must be row-local to the extracted title and authors. "
        "Interpret institution filters as a coauthor-affiliation test: a row matches if at least one coauthor affiliation matches the institution filter. "
        "Interpret author filters as a coauthor-name test: a row matches if at least one author name matches the author filter. "
        "Do not require majority authorship or first-author authorship unless the prompt explicitly requires that. "
        "Do not emit navigation/menu text, sponsor text, keynote/session headings, schedule blocks, or page headers. "
        "Do not emit acronym-only strings as paper titles. "
        "If authors and affiliations are present, pair them in `author_affiliations` as list items with `author` and `affiliation` keys when possible. "
        "If authors, affiliations, or abstract are present in the local segment for a kept paper row, extract them in the same row instead of omitting them. "
        "Prefer `abstract` as the full abstract text when the segment contains it; use `abstract_snippet` only if you only have a partial abstract fragment. "
        "If unsure whether row matches intent filters, set match_decision=uncertain. "
        "Use match_decision=non_match for valid paper rows not matching intent constraints."
    )


def extract_llm_user_payload(
    *,
    record: dict,
    filters: dict,
    user_prompt: str,
    intent: dict,
    segments: list[str],
) -> dict[str, Any]:
    return {
        "task": "extract_paper_rows",
        "user_prompt": user_prompt,
        "url": str(record.get("url") or ""),
        "url_title": str(record.get("url_title") or ""),
        "filters": filters,
        "intent": intent,
        "segments": segments,
    }


def extract_llm_messages(
    *,
    record: dict,
    filters: dict,
    user_prompt: str,
    intent: dict,
    segments: list[str],
) -> tuple[list[dict], dict[str, Any]]:
    user_payload = extract_llm_user_payload(
        record=record,
        filters=filters,
        user_prompt=user_prompt,
        intent=intent,
        segments=segments,
    )
    messages = [
        {"role": "system", "content": extract_llm_system_prompt()},
        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=True)},
    ]
    return messages, user_payload


def extract_candidate_urls_llm_system_prompt() -> str:
    return (
        "You identify complementary URLs already present on fetched pages. "
        "Return JSON only with key `candidate_urls` as a list of objects with keys: "
        "url, title, why. "
        "Only choose URLs from the supplied link_candidates list. "
        "Suggest direct next-page complements that may provide paper metadata, author affiliation, abstract, "
        "PDF/proceedings content, or closely related venue subpages relevant to the overall query. "
        "Prefer paper detail pages, proceedings PDFs, author pages tied to extracted papers, and nearby venue subpages. "
        "Avoid generic conference homepages, schedules, login/registration/policy pages, and broad site navigation unless no closer complement exists. "
        "Do not invent, rewrite, or normalize URLs beyond choosing from the provided candidates. "
        "Exclude links that are already known or already covered."
    )


def extract_candidate_urls_llm_user_payload(
    *,
    user_prompt: str,
    intent: dict[str, Any],
    paper_candidates: list[dict[str, Any]],
    anchor_terms: list[str],
    known_urls: list[str],
    link_candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    compact_papers: list[dict[str, Any]] = []
    for row in paper_candidates[:MAX_CANDIDATE_URL_PAPERS]:
        if not isinstance(row, dict):
            continue
        compact_papers.append(
            {
                "title": str(row.get("title") or ""),
                "authors": str(row.get("authors") or ""),
                "affiliations": str(row.get("affiliations") or ""),
                "source_url": str(row.get("url") or ""),
            }
        )
    return {
        "task": "identify_complementary_urls",
        "user_prompt": user_prompt,
        "intent": intent,
        "anchor_terms": [str(v) for v in anchor_terms if str(v).strip()][:16],
        "known_urls": [str(v) for v in known_urls if str(v).strip()][:MAX_CANDIDATE_URL_KNOWN_URLS],
        "paper_candidates": compact_papers,
        "link_candidates": [
            {
                "url": str(row.get("url") or ""),
                "label": str(row.get("label") or ""),
                "context": str(row.get("context") or ""),
                "source_url": str(row.get("source_url") or ""),
                "source_title": str(row.get("source_title") or ""),
            }
            for row in link_candidates[:MAX_CANDIDATE_URL_LINKS]
            if isinstance(row, dict) and str(row.get("url") or "").strip()
        ],
    }


def extract_candidate_urls_llm_messages(
    *,
    user_prompt: str,
    intent: dict[str, Any],
    paper_candidates: list[dict[str, Any]],
    anchor_terms: list[str],
    known_urls: list[str],
    link_candidates: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    user_payload = extract_candidate_urls_llm_user_payload(
        user_prompt=user_prompt,
        intent=intent,
        paper_candidates=paper_candidates,
        anchor_terms=anchor_terms,
        known_urls=known_urls,
        link_candidates=link_candidates,
    )
    messages = [
        {"role": "system", "content": extract_candidate_urls_llm_system_prompt()},
        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=True)},
    ]
    return messages, user_payload


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
    estimate_messages_metrics_fn = deps["estimate_messages_metrics_fn"]
    scaffold_messages, _payload = extract_llm_messages(
        record=record,
        filters=filters,
        user_prompt=user_prompt,
        intent=intent,
        segments=[],
    )
    scaffold_tokens = int(estimate_messages_metrics_fn(scaffold_messages).get("input_tokens_est") or 0)
    usable_tokens = int(context_limit_tokens * max(0.4, 1.0 - float(safety_margin or 0.0))) - int(output_token_reserve or 0) - scaffold_tokens
    return max(4_000, usable_tokens)


def slice_segments_by_token_budget(
    segments: list[str],
    *,
    start: int,
    max_segments: int,
    token_budget: int,
    min_segments: int = 1,
) -> list[str]:
    scoped = [str(v) for v in segments[start:] if str(v).strip()]
    if not scoped:
        return []
    limit_segments = max(1, int(max_segments or 0))
    budget = max(1_000, int(token_budget or 0))
    batch: list[str] = []
    total_tokens = 0
    for seg in scoped:
        seg_tokens = _estimate_text_tokens(seg)
        if batch and (len(batch) >= limit_segments or (total_tokens + seg_tokens) > budget):
            break
        batch.append(seg)
        total_tokens += seg_tokens
        if len(batch) >= limit_segments:
            break
    if batch:
        return batch
    keep = min(max(min_segments, 1), len(scoped))
    return scoped[:keep]


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
    build_extraction_windows_fn = deps["build_extraction_windows_fn"]
    estimate_messages_metrics_fn = deps["estimate_messages_metrics_fn"]
    openai_complete_json_fn = deps["openai_complete_json_fn"]
    peek_text_fn = deps["peek_text_fn"]
    strip_listing_author_tail_fn = deps["strip_listing_author_tail_fn"]
    extract_year_best_fn = deps["extract_year_best_fn"]

    text = str(record.get("text") or "")
    chunks = [str(v) for v in (segments or []) if str(v).strip()]
    if not chunks:
        chunks = build_extraction_windows_fn(text, filters, max_windows=8, radius=2, max_chars=1400)
    if not chunks:
        return [], {
            "target_id": str(record.get("target_id") or ""),
            "url": str(record.get("url") or ""),
            "url_title": str(record.get("url_title") or ""),
            "filters": dict(filters),
            "windows": [],
            "response_items": [],
            "response_items_count": 0,
        }
    messages, _user_payload = extract_llm_messages(
        record=record,
        filters=filters,
        user_prompt=user_prompt,
        intent=intent,
        segments=chunks,
    )
    message_metrics = estimate_messages_metrics_fn(messages)
    segment_text = "\n\n".join(chunks)
    segment_chars = len(segment_text)
    segment_tokens = _estimate_text_tokens(segment_text)
    scaffold_chars = max(0, int(message_metrics.get("input_chars") or 0) - segment_chars)
    scaffold_tokens = max(0, int(message_metrics.get("input_tokens_est") or 0) - segment_tokens)
    max_completion_tokens = min(8_000, max(2_400, int((message_metrics.get("input_tokens_est") or 0) * 0.25)))
    if raw_event_fn is not None:
        raw_event_fn(
            "extract_llm_request",
            {
                "model": model,
                "api_key_env": api_key_env,
                "op_id": llm_op_id,
                "messages": messages,
                "batch_mode": str(batch_mode or ""),
                "segments_count": len(chunks),
                "input_chars": int(message_metrics.get("input_chars") or 0),
                "input_tokens_est": int(message_metrics.get("input_tokens_est") or 0),
                "segment_chars": segment_chars,
                "segment_tokens_est": segment_tokens,
                "scaffold_chars": scaffold_chars,
                "scaffold_tokens_est": scaffold_tokens,
                "max_completion_tokens": max_completion_tokens,
                "record_meta": {
                    "target_id": str(record.get("target_id") or ""),
                    "url": str(record.get("url") or ""),
                },
            },
        )
    payload = openai_complete_json_fn(
        model=model,
        api_key_env=api_key_env,
        messages=messages,
        timeout_s=timeout_s,
        max_tokens=max_completion_tokens,
        max_retries=max_retries,
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
        raw_event_fn("extract_llm_response", response_payload)
    rows = payload.get("items") if isinstance(payload, dict) else []
    out: list[dict] = []
    for item in (rows if isinstance(rows, list) else []):
        if not isinstance(item, dict):
            continue
        conf_raw = item.get("confidence")
        confidence = 0.0
        if isinstance(conf_raw, (int, float)):
            confidence = float(conf_raw)
        else:
            conf_text = str(conf_raw or "").strip().lower()
            if conf_text in {"high", "strong"}:
                confidence = 0.9
            elif conf_text in {"medium", "moderate"}:
                confidence = 0.6
            elif conf_text in {"low", "weak"}:
                confidence = 0.3
            else:
                try:
                    confidence = float(conf_text)
                except (TypeError, ValueError):
                    confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))
        title_raw = str(item.get("paper_title_raw") or item.get("paper_title") or "").strip()
        title_norm = str(item.get("paper_title_normalized") or "").strip()
        title = strip_listing_author_tail_fn(title_norm or title_raw)
        doi = normalize_doi(str(item.get("doi") or ""))
        arxiv_id = normalize_arxiv_id(str(item.get("arxiv_id") or ""))
        year = extract_year_best_fn(str(item.get("year") or ""))
        evidence = peek_text_fn(str(item.get("evidence_span") or item.get("evidence") or ""), 420)
        is_paper = bool(item.get("is_paper", False))
        match_decision = str(item.get("match_decision") or "").strip().lower()
        if match_decision not in {"match", "non_match", "uncertain"}:
            if bool(item.get("institution_match")) or bool(item.get("author_match")):
                match_decision = "match"
            else:
                match_decision = "uncertain"
        title_l = title.lower()
        if not title or len(title) > 280:
            continue
        if any(token in title_l for token in NON_PAPER_TITLE_TOKENS):
            continue
        if any(token in title_l for token in LISTING_HEADING_PHRASES):
            continue
        if "session chair" in title_l or "available media" in title_l:
            continue
        if re.fullmatch(r"[A-Za-z0-9\\-]{2,15}", title):
            continue
        if len(title.split()) < 3 and not (is_paper or doi or arxiv_id):
            continue
        if match_decision == "non_match" and not is_paper:
            continue
        authors_raw = item.get("authors")
        affiliations_raw = item.get("affiliations")
        abstract_text = str(item.get("abstract") or item.get("abstract_snippet") or "").strip()
        abstract_snippet = peek_text_fn(abstract_text, 320)
        field_presence = {
            "authors": bool(
                (isinstance(authors_raw, list) and any(str(v).strip() for v in authors_raw))
                or (not isinstance(authors_raw, list) and str(authors_raw or "").strip())
            ),
            "affiliations": bool(
                (isinstance(affiliations_raw, list) and any(str(v).strip() for v in affiliations_raw))
                or (not isinstance(affiliations_raw, list) and str(affiliations_raw or "").strip())
            ),
            "abstract_snippet": bool(str(abstract_snippet or "").strip()),
        }
        missing_fields = [key for key, present in field_presence.items() if not present]
        out.append(
            {
                "session_id": str(record.get("session_id") or ""),
                "cycle_index": int(record.get("cycle_index") or 0),
                "target_id": str(record.get("target_id") or ""),
                "url": str(record.get("url") or ""),
                "url_title": str(record.get("url_title") or ""),
                "paper_title": title,
                "doi": doi,
                "arxiv_id": arxiv_id,
                "year": year,
                "filters": dict(filters),
                "evidence": evidence,
                "score": round(max(0.2, confidence or (0.55 if match_decision == "match" else 0.4)), 4),
                "status": "ok" if (title and (is_paper or match_decision in {"match", "uncertain"})) else "weak_signal",
                "extract_source": "llm",
                "extract_intent": dict(intent),
                "field_presence": field_presence,
                "missing_fields": missing_fields,
                "row_completeness": ("complete" if not missing_fields else "partial"),
                "llm_extract": {
                    "confidence": confidence,
                    "institution_match": item.get("institution_match"),
                    "author_match": item.get("author_match"),
                    "match_decision": match_decision,
                    "decision_reason": str(item.get("decision_reason") or ""),
                    "institution_hits": item.get("institution_hits"),
                    "authors": authors_raw,
                    "affiliations": affiliations_raw,
                    "author_affiliations": item.get("author_affiliations"),
                    "paper_title_raw": title_raw,
                    "paper_title_normalized": title_norm or title,
                    "abstract": abstract_text,
                    "abstract_snippet": abstract_snippet,
                    "venue_hint": str(item.get("venue_hint") or ""),
                },
            }
        )
    return out, {
        "target_id": str(record.get("target_id") or ""),
        "url": str(record.get("url") or ""),
        "url_title": str(record.get("url_title") or ""),
        "filters": dict(filters),
        "intent": dict(intent),
        "windows": chunks,
        "input_chars": int(message_metrics.get("input_chars") or 0),
        "input_tokens_est": int(message_metrics.get("input_tokens_est") or 0),
        "segment_chars": segment_chars,
        "segment_tokens_est": segment_tokens,
        "scaffold_chars": scaffold_chars,
        "scaffold_tokens_est": scaffold_tokens,
        "batch_mode": str(batch_mode or ""),
        "response_items_count": len(rows if isinstance(rows, list) else []),
        "response_items": rows if isinstance(rows, list) else [],
    }


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
    estimate_messages_metrics_fn = deps["estimate_messages_metrics_fn"]
    openai_complete_json_fn = deps["openai_complete_json_fn"]
    peek_text_fn = deps["peek_text_fn"]
    canonicalize_discovered_url_fn = deps["canonicalize_discovered_url_fn"]

    cleaned_links = [
        row
        for row in link_candidates[:MAX_CANDIDATE_URL_LINKS]
        if isinstance(row, dict) and str(row.get("url") or "").strip()
    ]
    if not cleaned_links:
        return [], {
            "link_candidates_count": 0,
            "response_candidate_urls_count": 0,
            "response_candidate_urls": [],
        }

    messages, user_payload = extract_candidate_urls_llm_messages(
        user_prompt=user_prompt,
        intent=intent,
        paper_candidates=paper_candidates,
        anchor_terms=anchor_terms,
        known_urls=known_urls,
        link_candidates=cleaned_links,
    )
    message_metrics = estimate_messages_metrics_fn(messages)
    max_completion_tokens = min(1_200, max(400, int((message_metrics.get("input_tokens_est") or 0) * 0.12)))
    if raw_event_fn is not None:
        raw_event_fn(
            "extract_candidate_urls_request",
            {
                "model": model,
                "api_key_env": api_key_env,
                "op_id": llm_op_id,
                "messages": messages,
                "input_chars": int(message_metrics.get("input_chars") or 0),
                "input_tokens_est": int(message_metrics.get("input_tokens_est") or 0),
                "max_completion_tokens": max_completion_tokens,
                "link_candidates_count": len(cleaned_links),
            },
        )
    payload = openai_complete_json_fn(
        model=model,
        api_key_env=api_key_env,
        messages=messages,
        timeout_s=timeout_s,
        max_tokens=max_completion_tokens,
        max_retries=max_retries,
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
        raw_event_fn("extract_candidate_urls_response", response_payload)

    by_url = {
        canonicalize_discovered_url_fn(str(row.get("url") or "")).lower(): dict(row)
        for row in cleaned_links
        if canonicalize_discovered_url_fn(str(row.get("url") or ""))
    }
    rows = payload.get("candidate_urls") if isinstance(payload, dict) else []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in (rows if isinstance(rows, list) else []):
        if not isinstance(item, dict):
            continue
        url = canonicalize_discovered_url_fn(str(item.get("url") or ""))
        if not url:
            continue
        key = url.lower()
        source = by_url.get(key)
        if source is None or key in seen:
            continue
        seen.add(key)
        title = str(item.get("title") or source.get("label") or url).strip()
        why = peek_text_fn(str(item.get("why") or source.get("context") or ""), 220)
        out.append(
            {
                "url": url,
                "title": title or url,
                "why": why,
                "source_url": str(source.get("source_url") or ""),
                "source_title": str(source.get("source_title") or ""),
            }
        )
    return out[:8], {
        "link_candidates_count": len(cleaned_links),
        "response_candidate_urls_count": len(rows if isinstance(rows, list) else []),
        "response_candidate_urls": rows if isinstance(rows, list) else [],
        "user_payload": user_payload,
    }
