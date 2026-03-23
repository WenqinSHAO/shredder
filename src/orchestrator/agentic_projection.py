from __future__ import annotations

from typing import Any

from src.orchestrator.agentic_text import _host_from_url, _peek_text


def _project_extract_url_row(
    *,
    url: str,
    row: dict[str, Any],
    hit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    item = dict(row or {})
    hit_row = dict(hit or {})
    normalized_url = str(url or "").strip()
    segments_done = max(0, int(item.get("segments_done") or 0))
    segment_total = max(0, int(item.get("segment_total") or 0))
    segments_pending = max(0, segment_total - segments_done)
    has_more = bool(item.get("coverage_has_more"))
    failed = bool(item.get("failed"))
    last_error = str(item.get("last_error") or "").strip()
    completed = bool(item.get("completed")) or (segment_total > 0 and segments_done >= segment_total and not has_more and not failed)
    if failed:
        status = "failed"
    elif completed:
        status = "completed"
    elif bool(item.get("fetched")) or segment_total > 0 or segments_done > 0:
        status = "in_progress"
    else:
        status = "new"
    coverage_pct = round((100.0 * float(segments_done) / float(segment_total)), 2) if segment_total > 0 else 0.0
    return {
        "url": normalized_url,
        "target_id": str(item.get("target_id") or ""),
        "title": _peek_text(str(hit_row.get("url_title") or hit_row.get("title") or item.get("url_title") or ""), 100),
        "host": str(hit_row.get("host") or _host_from_url(normalized_url)),
        "peek": _peek_text(str(hit_row.get("peek") or ""), 120),
        "status": status,
        "segments_done": segments_done,
        "segment_total": segment_total,
        "segments_pending": segments_pending,
        "coverage_pct": coverage_pct,
        "all_papers_extracted": completed and not failed,
        "has_more_results": has_more,
        "last_error": last_error,
    }


def _project_extract_url_rows(
    *,
    extract_state_by_url: dict[str, Any] | None,
    url_hits: list[dict[str, Any]] | None = None,
    max_items: int | None = None,
) -> list[dict[str, Any]]:
    state = extract_state_by_url if isinstance(extract_state_by_url, dict) else {}
    ordered_urls: list[str] = []
    hit_by_url: dict[str, dict[str, Any]] = {}
    for item in url_hits or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        if url not in hit_by_url:
            hit_by_url[url] = dict(item)
        if url not in ordered_urls:
            ordered_urls.append(url)
    for url in state:
        normalized = str(url or "").strip()
        if normalized and normalized not in ordered_urls:
            ordered_urls.append(normalized)

    out: list[dict[str, Any]] = []
    limit = int(max_items) if max_items is not None else 0
    for url in ordered_urls:
        row = state.get(url)
        if not isinstance(row, dict):
            row = {}
        out.append(
            _project_extract_url_row(
                url=url,
                row=row,
                hit=hit_by_url.get(url),
            )
        )
        if limit > 0 and len(out) >= limit:
            break
    return out
