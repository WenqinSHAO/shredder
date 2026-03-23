from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urlparse

from src.connectors.http import get_json, normalize_arxiv_id, normalize_doi
from src.orchestrator.agentic_text import PAPER_SIGNAL_TOKENS, _is_detail_page, _is_listing_page

ProgressCallback = Callable[[dict], None]

STOPWORDS = {
    "the",
    "and",
    "for",
    "from",
    "with",
    "this",
    "that",
    "into",
    "using",
    "toward",
    "towards",
    "based",
    "study",
    "analysis",
    "system",
    "paper",
    "research",
}

NOISY_TITLE_TOKENS = {
    "webmail",
    "whatsapp",
    "login",
    "song",
    "youtube",
    "dailymotion",
    "google play",
}

ACADEMIC_HOST_TOKENS = {
    "acm.org",
    "ieee.org",
    "arxiv.org",
    "usenix.org",
    "openreview.net",
    "dblp.org",
    "doi.org",
    ".edu",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _emit_progress(progress_callback: ProgressCallback | None, *, event: str, **payload) -> None:
    if progress_callback is None:
        return
    body = {"event": event}
    body.update(payload)
    progress_callback(body)


def _host_from_url(url: str) -> str:
    try:
        return (urlparse(url).netloc or "").lower()
    except Exception:
        return ""


def _peek_text(text: str, limit: int = 220) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 3)] + "..."


def _listing_page_kind(*, title: str, url: str) -> str:
    lowered_title = str(title or "").lower()
    lowered_url = str(url or "").lower()
    if any(token in lowered_title or token in lowered_url for token in ("accepted papers", "/accepted-papers", "/accepted", "/accept.php")):
        return "accepted"
    if any(token in lowered_title or token in lowered_url for token in ("technical sessions", "/technical-sessions", "/program", "/papers-info")):
        return "program"
    if any(token in lowered_title or token in lowered_url for token in ("proceedings", "/proceedings/", "/doi/proceedings/")):
        return "proceedings"
    if _is_listing_page(title=title, url=url):
        return "listing"
    return ""


def _normalize_title_for_key(title: str) -> str:
    lowered = str(title or "").strip().lower()
    lowered = re.sub(r"\b(session|chair|presenter|presenters?)\b.*$", "", lowered).strip()
    lowered = re.sub(r"[^a-z0-9]+", " ", lowered)
    lowered = re.sub(r"\s+", " ", lowered).strip()
    return lowered


def _strip_listing_author_tail(title: str) -> str:
    text = str(title or "").strip()
    if not text:
        return ""
    m = re.search(r"\s+[A-Z][A-Za-z'\-]+(?:\s+[A-Z][A-Za-z'\-]+){0,3}\s*\(", text)
    if m:
        text = text[: m.start()].strip(" -:;,")
    if ");" in text:
        text = text.split(");", 1)[0].strip(" -:;,")
    m2 = re.search(r"\s+[A-Z][a-z'\-]{2,}\s+[A-Z][A-Za-z'\-]{2,}\s*,", text)
    if m2:
        text = text[: m2.start()].strip(" -:;,")
    return text


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        parts = [part.strip() for part in re.split(r"[;,|]", value) if part.strip()]
        return parts
    return [str(value).strip()]


def _make_hit_id(url: str, title: str) -> str:
    text = f"{str(url or '').strip().lower()}|{str(title or '').strip().lower()}"
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def _unique_nonempty(items: list[str], limit: int) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        value = str(item or "").strip()
        if not value:
            continue
        lowered = value.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        out.append(value)
        if len(out) >= limit:
            break
    return out


def _domain_quality_adjustment(url: str, title: str) -> float:
    host = _host_from_url(url)
    lowered_title = str(title or "").lower()
    score = 0.0
    if any(token in host for token in ACADEMIC_HOST_TOKENS):
        score += 0.25
    if any(token in lowered_title for token in NOISY_TITLE_TOKENS):
        score -= 0.25
    if any(token in host for token in ("webmail", "whatsapp", "telenet", "play.google.com", "youtube.com", "dailymotion.com")):
        score -= 0.35
    return score


def _reject_reason(row: dict) -> str:
    host = str(row.get("_host") or _host_from_url(str(row.get("url") or ""))).lower()
    title = str(row.get("title") or "").lower()
    if any(token in host for token in ("youtube.com", "music.youtube", "play.google.com", "apps.apple.com")):
        return "consumer_app"
    if any(token in host for token in ("support.google.com/maps",)) or "maps" in host:
        return "off_topic_host"
    if any(token in title for token in NOISY_TITLE_TOKENS):
        return "noisy_title"
    content_text = " ".join(
        [
            title,
            str(row.get("url") or "").lower(),
            str(row.get("_snippet") or row.get("abstract") or "").lower(),
        ]
    )
    has_academic_signal = any(token in host for token in ACADEMIC_HOST_TOKENS) or any(
        token in content_text for token in PAPER_SIGNAL_TOKENS
    )
    if not has_academic_signal:
        return "low_academic_signal"
    return ""


def _filter_search_rows(rows: list[dict]) -> tuple[list[dict], dict[str, int]]:
    kept: list[dict] = []
    rejected: dict[str, int] = {}
    for row in rows:
        reason = _reject_reason(row)
        if not reason:
            kept.append(row)
            continue
        rejected[reason] = rejected.get(reason, 0) + 1
    return kept, rejected


def _normalize_searx_row(*, session_id: str, cycle_index: int, query: str, query_rank: int, item: dict) -> tuple[dict, dict]:
    metadata = item.get("metadata") or {}
    title = str(item.get("title") or metadata.get("title") or "").strip()
    url = str(item.get("url") or metadata.get("url") or "").strip()
    source_id = str(item.get("id") or url or title).strip()
    snippet = str(item.get("content") or item.get("snippet") or "").strip()
    source = str(item.get("engine") or item.get("source") or "searxng").strip()
    venue = str(metadata.get("journal") or metadata.get("venue") or metadata.get("source") or "").strip()
    year = str(metadata.get("year") or "").strip()
    doi = normalize_doi(str(metadata.get("doi") or ""))
    arxiv_raw = str(metadata.get("arxiv") or "").strip()
    if not arxiv_raw and "arxiv.org/" in url.lower():
        arxiv_raw = url
    arxiv_id = normalize_arxiv_id(arxiv_raw)

    author_candidates = []
    author_candidates.extend(_as_list(metadata.get("authors")))
    author_candidates.extend(_as_list(metadata.get("author")))
    author_hint = "|".join(dict.fromkeys(author_candidates))

    web_row = {
        "timestamp": _utc_now(),
        "session_id": session_id,
        "cycle_index": cycle_index,
        "query": query,
        "query_rank": query_rank,
        "source": source,
        "source_id": source_id,
        "title": title,
        "url": url,
        "snippet": snippet,
        "venue": venue,
        "year": year,
        "doi": doi,
        "arxiv_id": arxiv_id,
        "author_hint": author_hint,
    }

    quality_score = 0.0
    quality_score += max(0.0, 1.0 - (query_rank * 0.03))
    quality_score += 0.2 if doi else 0.0
    quality_score += 0.15 if arxiv_id else 0.0
    quality_score += 0.05 if venue else 0.0
    quality_score += _domain_quality_adjustment(url, title)

    candidate_row = {
        "source": source,
        "source_id": source_id,
        "title": title,
        "venue": venue,
        "year": year,
        "doi": doi,
        "arxiv_id": arxiv_id,
        "url": url,
        "abstract": snippet,
        "keywords": [],
        "categories": [],
        "score": round(quality_score, 4),
        "reason": "searxng_search",
        "query_used": query,
        "_snippet": snippet,
        "_author_hint": author_hint,
        "_host": _host_from_url(url),
    }
    return web_row, candidate_row


def _search_web_queries(
    *,
    session_id: str,
    cycle_index: int,
    queries: list[str],
    searxng_url: str,
    search_categories: str,
    web_results_per_query: int,
    timeout_s: float,
    progress_callback: ProgressCallback | None = None,
    raw_event_fn: Callable[[str, Any, list[str] | None], str] | None = None,
    new_op_id_fn: Callable[[str], str] | None = None,
    get_json_fn: Callable[..., Any] = get_json,
    normalize_row_fn: Callable[..., tuple[dict, dict]] | None = None,
    emit_progress_fn: Callable[..., None] | None = None,
) -> tuple[list[dict], list[dict]]:
    normalize_row = normalize_row_fn or _normalize_searx_row
    emit_progress = emit_progress_fn or _emit_progress
    endpoint = f"{searxng_url.rstrip('/')}/search"
    web_rows: list[dict] = []
    candidates: list[dict] = []
    for query in queries:
        search_op_id = new_op_id_fn("web_search_query") if new_op_id_fn is not None else ""
        if raw_event_fn is not None and search_op_id:
            raw_event_fn(
                "op_start",
                {
                    "op_id": search_op_id,
                    "op_type": "web_search_query",
                    "component": "search_web",
                    "query": query,
                    "categories": search_categories,
                    "limit": web_results_per_query,
                },
            )
        emit_progress(
            progress_callback,
            event="agentic_web_search_query_start",
            cycle_index=cycle_index,
            query=query,
            categories=search_categories,
            limit=web_results_per_query,
        )
        try:
            payload = get_json_fn(
                endpoint,
                {"q": query, "format": "json", "categories": search_categories},
                timeout_s=timeout_s,
                min_interval_s=0.0,
            )
        except Exception as exc:
            if raw_event_fn is not None and search_op_id:
                raw_event_fn(
                    "op_end",
                    {
                        "op_id": search_op_id,
                        "op_type": "web_search_query",
                        "component": "search_web",
                        "query": query,
                        "status": "error",
                        "error": f"{type(exc).__name__}:{exc}",
                        "raw_results": 0,
                        "used_results": 0,
                    },
                )
            emit_progress(
                progress_callback,
                event="agentic_web_search_query_done",
                cycle_index=cycle_index,
                query=query,
                raw_results=0,
                used_results=0,
                error=f"{type(exc).__name__}:{exc}",
            )
            continue
        scoped = (payload.get("results") or [])[:web_results_per_query]
        for rank, item in enumerate(scoped, start=1):
            if not isinstance(item, dict):
                continue
            web_row, candidate_row = normalize_row(
                session_id=session_id,
                cycle_index=cycle_index,
                query=query,
                query_rank=rank,
                item=item,
            )
            web_rows.append(web_row)
            candidates.append(candidate_row)
        emit_progress(
            progress_callback,
            event="agentic_web_search_query_done",
            cycle_index=cycle_index,
            query=query,
            raw_results=len(payload.get("results") or []),
            used_results=len(scoped),
        )
        if raw_event_fn is not None and search_op_id:
            raw_event_fn(
                "op_end",
                {
                    "op_id": search_op_id,
                    "op_type": "web_search_query",
                    "component": "search_web",
                    "query": query,
                    "status": "ok",
                    "raw_results": len(payload.get("results") or []),
                    "used_results": len(scoped),
                },
            )
    return web_rows, candidates


def _candidate_dedup_key(row: dict) -> str:
    source = str(row.get("source") or "").strip().lower()
    reason = str(row.get("reason") or "").strip().lower()
    is_extract = source == "agentic_extract" or reason == "extract_content"
    doi = normalize_doi(str(row.get("doi") or ""))
    if doi:
        return f"doi:{doi}"
    arxiv = normalize_arxiv_id(str(row.get("arxiv_id") or ""))
    if arxiv:
        return f"arxiv:{arxiv}"
    title_raw = str(row.get("title") or "")
    if is_extract:
        title_raw = _strip_listing_author_tail(title_raw)
    title = _normalize_title_for_key(title_raw)
    year = str(row.get("year") or "").strip()
    if is_extract and title:
        return f"extract:{title}:{year}"
    url = str(row.get("url") or "").strip().lower()
    if url:
        return f"url:{url}"
    return f"title:{title}:{year}"


def _rank_candidates(rows: list[dict]) -> list[dict]:
    best_by_key: dict[str, dict] = {}
    for row in rows:
        key = _candidate_dedup_key(row)
        prev = best_by_key.get(key)
        if prev is None or float(row.get("score", 0.0)) > float(prev.get("score", 0.0)):
            best_by_key[key] = row
    return sorted(
        best_by_key.values(),
        key=lambda r: (
            float(r.get("score", 0.0)),
            1 if r.get("doi") else 0,
            1 if r.get("arxiv_id") else 0,
            len(str(r.get("title") or "")),
        ),
        reverse=True,
    )


def _apply_shortlist_hints(ranked_rows: list[dict], shortlist_hints: dict | None) -> list[dict]:
    hints = shortlist_hints if isinstance(shortlist_hints, dict) else {}
    prefer = {str(v).strip().lower() for v in (hints.get("prefer") or []) if str(v).strip()}
    if not prefer:
        return ranked_rows

    adjusted: list[dict] = []
    for row in ranked_rows:
        row2 = dict(row)
        extra = 0.0
        title = str(row2.get("title") or "")
        url = str(row2.get("url") or "")
        host = str(row2.get("_host") or _host_from_url(url))
        if "venue_program_pages" in prefer and _is_listing_page(title=title, url=url):
            extra += 0.35
        if "author_sources" in prefer and any(token in host for token in ("dblp.org", "arxiv.org", "scholar.google.", "openreview.net")):
            extra += 0.3
        if "doi_landing" in prefer and "doi.org" in host:
            extra += 0.2
        if "avoid_detail_pages" in prefer and _is_detail_page(title=title, url=url):
            extra -= 0.2
        row2["score"] = round(float(row2.get("score", 0.0) or 0.0) + extra, 4)
        adjusted.append(row2)
    return _rank_candidates(adjusted)


def _extract_keywords(rows: list[dict], max_terms: int = 12) -> list[str]:
    counter: dict[str, int] = {}
    for row in rows:
        title = str(row.get("title") or "")
        for token in re.findall(r"[A-Za-z][A-Za-z0-9\\-]{2,}", title):
            lowered = token.lower()
            if lowered in STOPWORDS:
                continue
            counter[lowered] = counter.get(lowered, 0) + 1
    return [term for term, _ in sorted(counter.items(), key=lambda item: (-item[1], item[0]))[:max_terms]]


def _merge_url_hits(existing: list[dict], incoming: list[dict], *, limit: int | None = None) -> list[dict]:
    best_by_url: dict[str, dict] = {}
    for row in [*(existing or []), *(incoming or [])]:
        if not isinstance(row, dict):
            continue
        url = str(row.get("url") or "").strip().lower()
        if not url:
            continue
        prev = best_by_url.get(url)
        if prev is None or float(row.get("score") or 0.0) >= float(prev.get("score") or 0.0):
            best_by_url[url] = dict(row)
    merged = sorted(
        best_by_url.values(),
        key=lambda row: (
            float(row.get("score") or 0.0),
            1 if _is_listing_page(title=str(row.get("url_title") or ""), url=str(row.get("url") or "")) else 0,
            len(str(row.get("url_title") or "")),
        ),
        reverse=True,
    )
    if limit is not None and limit > 0:
        merged = merged[:limit]
    for idx, row in enumerate(merged, start=1):
        row["rank"] = idx
    return merged


def _summary_from_url_hits(url_hits: list[dict], *, rejected_counts: dict[str, int] | None = None) -> dict[str, Any]:
    rows = [
        {
            "title": str(item.get("url_title") or ""),
            "url": str(item.get("url") or ""),
            "abstract": str(item.get("peek") or ""),
            "score": float(item.get("score") or 0.0),
            "source": str(item.get("source") or ""),
        }
        for item in url_hits
        if isinstance(item, dict)
    ]
    return {
        "result_count": len(rows),
        "top_hits": [
            {
                "title": str(item.get("title") or ""),
                "url": str(item.get("url") or ""),
                "peek": _peek_text(str(item.get("abstract") or ""), 220),
                "source": str(item.get("source") or ""),
                "host": _host_from_url(str(item.get("url") or "")),
                "score": float(item.get("score") or 0.0),
            }
            for item in rows[:8]
        ],
        "paper_titles": _unique_nonempty([str(item.get("title") or "") for item in rows], limit=10),
        "venues": [],
        "authors": [],
        "keywords": _extract_keywords(rows, max_terms=12),
        "rejected_counts": dict(rejected_counts or {}),
    }


def _record_url_aliases(row: dict) -> set[str]:
    aliases = {
        str(row.get("url") or "").strip(),
        str(row.get("requested_url") or "").strip(),
    }
    aliases.update(str(v).strip() for v in (row.get("url_aliases") or []) if str(v).strip())
    return {value for value in aliases if value}


def _filter_records_by_urls(records: list[dict], urls: set[str]) -> list[dict]:
    requested = {str(v).strip() for v in urls if str(v).strip()}
    if not requested:
        return list(records)
    out: list[dict] = []
    for row in records:
        if not isinstance(row, dict):
            continue
        if _record_url_aliases(row) & requested:
            out.append(row)
    return out


def _pick_richer_fetched_record(existing: dict | None, incoming: dict | None) -> dict | None:
    if not isinstance(existing, dict):
        return dict(incoming or {}) if isinstance(incoming, dict) else None
    if not isinstance(incoming, dict):
        return dict(existing)
    existing_ok = str(existing.get("status") or "") == "ok"
    incoming_ok = str(incoming.get("status") or "") == "ok"
    if incoming_ok and not existing_ok:
        winner = dict(incoming)
    elif existing_ok and not incoming_ok:
        winner = dict(existing)
    else:
        existing_chars = int(existing.get("text_chars") or 0)
        incoming_chars = int(incoming.get("text_chars") or 0)
        winner = dict(incoming if incoming_chars >= existing_chars else existing)
    aliases = _unique_nonempty(
        [
            *list(_record_url_aliases(existing)),
            *list(_record_url_aliases(incoming)),
        ],
        limit=24,
    )
    if aliases:
        winner["url_aliases"] = aliases
    return winner


def _merge_fetched_records(existing: list[dict], incoming: list[dict]) -> list[dict]:
    merged: list[dict] = []
    for row in [*(existing or []), *(incoming or [])]:
        if not isinstance(row, dict):
            continue
        winner = dict(row)
        aliases = _record_url_aliases(winner)
        if not aliases:
            continue
        merged_into_existing = False
        for idx, prev in enumerate(merged):
            if _record_url_aliases(prev) & aliases:
                merged[idx] = _pick_richer_fetched_record(prev, winner) or dict(prev)
                merged_into_existing = True
                break
        if not merged_into_existing:
            merged.append(winner)
    return merged


def _reuse_fetched_record_for_target(records: list[dict], target: dict, idx: int) -> dict | None:
    target_url = str(target.get("url") or "").strip()
    if not target_url:
        return None
    cached = next(
        (
            row
            for row in (records or [])
            if isinstance(row, dict)
            and str(row.get("status") or "") == "ok"
            and target_url in _record_url_aliases(row)
        ),
        None,
    )
    if not isinstance(cached, dict):
        return None
    reused = dict(cached)
    reused["target_id"] = str(target.get("target_id") or reused.get("target_id") or f"fetch-{idx}")
    reused["requested_url"] = target_url
    reused["url_title"] = str(target.get("title") or reused.get("url_title") or "")
    reused["why"] = str(target.get("why") or reused.get("why") or "")
    reused["url_aliases"] = _unique_nonempty(
        [target_url, *list(_record_url_aliases(cached))],
        limit=24,
    )
    if isinstance(target.get("filters"), dict):
        reused["filters"] = dict(target.get("filters") or {})
    if isinstance(target.get("match"), dict):
        reused["match"] = dict(target.get("match") or {})
    anchor_terms = target.get("anchor_terms")
    if isinstance(anchor_terms, list):
        reused["anchor_terms"] = [str(v).strip() for v in anchor_terms if str(v).strip()][:16]
    return reused


def _to_url_hits(
    session_id: str,
    cycle_index: int,
    shortlisted: list[dict],
    top_n: int,
    *,
    make_hit_id_fn: Callable[[str, str], str] | None = None,
    peek_text_fn: Callable[[str, int], str] | None = None,
    host_from_url_fn: Callable[[str], str] | None = None,
) -> list[dict]:
    make_hit_id = make_hit_id_fn or _make_hit_id
    peek_text = peek_text_fn or _peek_text
    host_from_url = host_from_url_fn or _host_from_url
    url_hits: list[dict] = []
    for idx, candidate in enumerate(shortlisted[:top_n], start=1):
        row = dict(candidate)
        row["session_id"] = session_id
        row["cycle_index"] = cycle_index
        row["rank"] = idx
        url_hits.append(
            {
                "hit_id": make_hit_id(str(row.get("url") or ""), str(row.get("title") or "")),
                "rank": idx,
                "url_title": str(row.get("title") or ""),
                "url": str(row.get("url") or ""),
                "peek": peek_text(str(row.get("_snippet") or row.get("abstract") or ""), 220),
                "source": str(row.get("source") or ""),
                "host": str(row.get("_host") or host_from_url(str(row.get("url") or ""))),
                "query_used": str(row.get("query_used") or ""),
                "doi": str(row.get("doi") or ""),
                "arxiv_id": str(row.get("arxiv_id") or ""),
                "score": float(row.get("score", 0.0) or 0.0),
            }
        )
    return url_hits


def _select_diverse_shortlist(ranked_rows: list[dict], top_n: int) -> list[dict]:
    if top_n <= 0:
        return []
    out: list[dict] = []
    used_keys: set[str] = set()
    seen_queries: set[str] = set()
    seen_hosts: set[str] = set()
    listing_kinds_by_query: dict[str, set[str]] = {}

    def key_of(row: dict) -> str:
        return _candidate_dedup_key(row)

    for row in ranked_rows:
        q = str(row.get("query_used") or "").strip().lower()
        if not q or q in seen_queries:
            continue
        key = key_of(row)
        if key in used_keys:
            continue
        out.append(row)
        used_keys.add(key)
        seen_queries.add(q)
        seen_hosts.add(str(row.get("_host") or _host_from_url(str(row.get("url") or ""))).lower())
        kind = _listing_page_kind(title=str(row.get("title") or ""), url=str(row.get("url") or ""))
        if kind:
            listing_kinds_by_query.setdefault(q, set()).add(kind)
        if len(out) >= top_n:
            return out

    for row in ranked_rows:
        q = str(row.get("query_used") or "").strip().lower()
        if not q or q not in seen_queries:
            continue
        key = key_of(row)
        if key in used_keys:
            continue
        kind = _listing_page_kind(title=str(row.get("title") or ""), url=str(row.get("url") or ""))
        if not kind or kind == "listing":
            continue
        seen_kinds = listing_kinds_by_query.setdefault(q, set())
        if kind in seen_kinds:
            continue
        if _domain_quality_adjustment(str(row.get("url") or ""), str(row.get("title") or "")) < -0.05:
            continue
        out.append(row)
        used_keys.add(key)
        seen_hosts.add(str(row.get("_host") or _host_from_url(str(row.get("url") or ""))).lower())
        seen_kinds.add(kind)
        if len(out) >= top_n:
            return out

    for row in ranked_rows:
        host = str(row.get("_host") or _host_from_url(str(row.get("url") or ""))).lower()
        key = key_of(row)
        if key in used_keys or host in seen_hosts:
            continue
        out.append(row)
        used_keys.add(key)
        seen_hosts.add(host)
        if len(out) >= top_n:
            return out

    for row in ranked_rows:
        key = key_of(row)
        if key in used_keys:
            continue
        out.append(row)
        used_keys.add(key)
        if len(out) >= top_n:
            return out
    return out


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
    raw_event_fn: Callable[[str, Any, list[str] | None], str] | None,
    deps: dict[str, Any],
) -> dict:
    extract_action_queries_fn = deps["extract_action_queries_fn"]
    normalize_search_queries_fn = deps["normalize_search_queries_fn"]
    search_web_queries_fn = deps["search_web_queries_fn"]
    normalize_shortlist_hints_fn = deps["normalize_shortlist_hints_fn"]
    next_op_id_fn = deps["next_op_id_fn"]

    raw_queries = extract_action_queries_fn({"params": params}, limit=16)
    normalized_queries = normalize_search_queries_fn(raw_queries, max_total=16)
    web_rows, raw_candidates = search_web_queries_fn(
        session_id=session_id,
        cycle_index=cycle_index,
        queries=normalized_queries,
        searxng_url=searxng_url,
        search_categories=search_categories,
        web_results_per_query=web_results_per_query,
        timeout_s=timeout_s,
        progress_callback=progress_callback,
        raw_event_fn=raw_event_fn,
        new_op_id_fn=(lambda prefix: next_op_id_fn(runtime_state, prefix)),
    )
    shortlist_hints = normalize_shortlist_hints_fn(params.get("shortlist_hints"))
    return {
        "status": "ok",
        "tool_calls": f"searxng:{len(normalized_queries)}",
        "web_rows": web_rows,
        "raw_candidates": raw_candidates,
        "paper_candidates": [],
        "shortlist_hints": shortlist_hints,
        "stop": False,
        "stop_reason": "",
        "notes": (
            f"executed_web_search queries={len(normalized_queries)} "
            f"shortlist_hints={json.dumps(shortlist_hints, ensure_ascii=True)}"
        ),
    }
