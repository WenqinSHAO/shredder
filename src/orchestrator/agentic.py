from __future__ import annotations

import hashlib
import json
import os
import re
from io import BytesIO
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse, urlunparse
from urllib.request import Request, urlopen

from src.connectors.http import get_json, normalize_arxiv_id, normalize_doi
from src.orchestrator.agentic_contracts import (
    _build_agent_messages,
    _parse_agent_action_response,
)
from src.orchestrator.agentic_extract import (
    canonicalize_candidate_title as _canonicalize_candidate_title_impl,
    extract_facts_with_llm as _extract_facts_with_llm_impl,
    extract_listing_candidates_from_segments as _extract_listing_candidates_from_segments_impl,
    extract_segment_token_budget as _extract_segment_token_budget_impl,
    extract_target_filters as _extract_target_filters_impl,
    extract_year_best as _extract_year_best_impl,
    infer_by_subject_from_prompt as _infer_by_subject_from_prompt_impl,
    infer_paper_title as _infer_paper_title_impl,
    infer_subject_kind as _infer_subject_kind_impl,
    infer_year_gte_from_prompt as _infer_year_gte_from_prompt_impl,
    is_authorish_title_fragment as _is_authorish_title_fragment_impl,
    looks_like_paper_candidate as _looks_like_paper_candidate_impl,
    normalize_fetch_target as _normalize_fetch_target_impl,
    resolve_extract_intent as _resolve_extract_intent_impl,
    slice_segments_by_token_budget as _slice_segments_by_token_budget_impl,
    to_paper_candidates_from_facts as _to_paper_candidates_from_facts_impl,
    execute_extract_content_action as _execute_extract_content_action_impl,
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
    _cleanup_agentic_artifacts,
    _compact_result_payload,
    _has_venue_evidence,
    _merge_paper_candidates,
    _persist_result,
    _requires_venue_evidence,
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


def _default_paper_state() -> dict[str, Any]:
    return {
        "final_candidates": [],
        "fallback_candidates": [],
        "coverage_summary": {
            "fetch_targets_total": 0,
            "fetch_targets_ok": 0,
            "fetch_targets_error": 0,
            "extract_records_total": 0,
            "extract_records_ok": 0,
            "extract_records_with_venue_evidence": 0,
            "venue_evidence_required": False,
        },
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
    stripped = text.strip()
    if not stripped:
        return {}
    if stripped.startswith("```"):
        lines = [line for line in stripped.splitlines() if not line.strip().startswith("```")]
        stripped = "\n".join(lines).strip()
    try:
        payload = json.loads(stripped)
        return payload if isinstance(payload, dict) else {}
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", stripped, flags=re.DOTALL)
    if not match:
        return {}
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _coerce_message_content_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if isinstance(item, dict):
                if isinstance(item.get("text"), str):
                    parts.append(item["text"])
                    continue
                if isinstance(item.get("content"), str):
                    parts.append(item["content"])
                    continue
        return "\n".join(part for part in parts if part).strip()
    return str(content)


def _response_message_content(response: Any) -> str:
    choices: Any = []
    if isinstance(response, dict):
        choices = response.get("choices") or []
    else:
        choices = getattr(response, "choices", []) or []
    if not choices:
        return ""

    first = choices[0]
    message: Any = {}
    if isinstance(first, dict):
        message = first.get("message") or {}
    else:
        message = getattr(first, "message", {}) or {}

    if isinstance(message, dict):
        return _coerce_message_content_text(message.get("content"))
    return _coerce_message_content_text(getattr(message, "content", ""))


def _resolve_openai_model_and_base_url(*, model: str, api_key_env: str) -> tuple[str, str]:
    resolved_model = str(model or "").strip()
    if not resolved_model:
        return "", ""
    base_url = ""
    if "/" in resolved_model:
        provider, bare_model = resolved_model.split("/", 1)
        provider_l = provider.strip().lower()
        if provider_l == "deepseek":
            resolved_model = bare_model.strip()
            base_url = str(os.environ.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com").strip()
        elif provider_l == "openai":
            resolved_model = bare_model.strip()
            base_url = str(os.environ.get("OPENAI_BASE_URL") or "").strip()
    else:
        model_l = resolved_model.lower()
        if api_key_env == "DS_API_KEY" or model_l.startswith("deepseek-"):
            base_url = str(os.environ.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com").strip()
        else:
            base_url = str(os.environ.get("OPENAI_BASE_URL") or "").strip()
    return resolved_model, base_url


def _openai_completion_json_payload(
    *,
    client: Any,
    model: str,
    messages: list[dict],
    with_response_format: bool,
    max_tokens: int | None = None,
) -> dict:
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0.1,
    }
    if with_response_format:
        kwargs["response_format"] = {"type": "json_object"}
    if max_tokens and max_tokens > 0:
        kwargs["max_tokens"] = int(max_tokens)
    response = client.chat.completions.create(**kwargs)
    return _extract_json_object(_response_message_content(response))


def _estimate_messages_metrics(messages: list[dict]) -> dict[str, int]:
    total_chars = 0
    total_tokens = 0
    for item in messages:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "")
        content = _coerce_message_content_text(item.get("content"))
        total_chars += len(role) + len(content)
        total_tokens += _estimate_text_tokens(role) + _estimate_text_tokens(content)
    return {
        "input_chars": total_chars,
        "input_tokens_est": total_tokens,
    }


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _decode_bytes(raw: Any) -> str:
    if isinstance(raw, str):
        return raw
    try:
        return raw.decode("utf-8", errors="ignore")
    except Exception:
        try:
            return raw.decode("latin-1", errors="ignore")
        except Exception:
            return str(raw or "")


def _extract_text_from_pdf_bytes(raw: bytes, *, max_chars: int) -> str:
    try:
        import pypdf  # type: ignore

        reader = pypdf.PdfReader(BytesIO(raw))
        parts: list[str] = []
        for page in reader.pages:
            text = page.extract_text() or ""
            if text:
                parts.append(text)
            if sum(len(p) for p in parts) >= max_chars:
                break
        if parts:
            return _clean_text("\n".join(parts), limit_chars=max_chars)
    except Exception:
        pass
    return _clean_text(_decode_bytes(raw), limit_chars=max_chars)


def _fetch_url_raw(url: str, *, timeout_s: float, max_bytes: int) -> tuple[bytes, str]:
    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; ShredderAgentic/0.1; +https://example.org)",
            "Accept": "text/html,application/xhtml+xml,application/xml,text/plain;q=0.9,*/*;q=0.8",
        },
    )
    with urlopen(req, timeout=timeout_s) as response:
        ctype = str(response.headers.get("Content-Type") or "").lower()
        content_length = _safe_int(response.headers.get("Content-Length"))
        hard_limit = int(max_bytes or 0)
        if hard_limit > 0 and content_length is not None and content_length > hard_limit:
            raise RuntimeError(f"content_length_exceeds_limit:{content_length}>{hard_limit}")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = response.read(64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if hard_limit > 0 and total > hard_limit:
                raise RuntimeError(f"response_exceeds_limit:{total}>{hard_limit}")
            chunks.append(chunk)
        raw = b"".join(chunks)
        if content_length is not None and len(raw) < content_length:
            raise RuntimeError(f"incomplete_response:{len(raw)}<{content_length}")
    return raw, ctype


def _safe_name(value: str, default: str = "item") -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip()).strip("-")
    return text or default


def _fetch_retry_urls(url: str) -> list[str]:
    text = str(url or "").strip()
    if not text:
        return []
    parsed = urlparse(text)
    path = parsed.path or ""
    out: list[str] = []
    if path.endswith(".html"):
        base_path = path[: -len(".html")]
        if base_path:
            retry = parsed._replace(path=base_path + "/", query="", fragment="")
            out.append(urlunparse(retry))
    return _unique_queries(out, 3)


def _save_raw_fetch(paths: dict[str, Path], *, cycle_index: int, target_id: str, url: str, raw: Any, content_type: str) -> str:
    base = paths["result"].parent / "fetch_raw"
    base.mkdir(parents=True, exist_ok=True)
    url_tail = _safe_name((urlparse(url).path or "").split("/")[-1], default="page")
    ext = ".bin"
    ctype = str(content_type or "").lower()
    if "html" in ctype:
        ext = ".html"
    elif "pdf" in ctype:
        ext = ".pdf"
    elif "json" in ctype:
        ext = ".json"
    fname = f"cycle{cycle_index:02d}-{_safe_name(target_id)}-{url_tail}{ext}"
    path = base / fname
    if isinstance(raw, str):
        data = raw.encode("utf-8", errors="ignore")
    elif isinstance(raw, bytes):
        data = raw
    else:
        data = str(raw or "").encode("utf-8", errors="ignore")
    path.write_bytes(data)
    return str(path.relative_to(paths["result"].parent))


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
    url = str(target.get("url") or "").strip()
    title = str(target.get("title") or target.get("url_title") or "").strip()
    if not url:
        return {
            "session_id": session_id,
            "cycle_index": cycle_index,
            "target_id": str(target.get("target_id") or f"fetch-{idx}"),
            "requested_url": url,
            "url": "",
            "url_aliases": [],
            "url_title": title,
            "why": str(target.get("why") or ""),
            "status": "error",
            "error": "missing_url",
            "text_chars": 0,
            "text": "",
            "peek": "",
            "page_count": 0,
            "page_urls": [],
            "raw_path": "",
        }
    fetch_op_id = new_op_id_fn("web_fetch") if new_op_id_fn is not None else ""
    if raw_event_fn is not None and fetch_op_id:
        raw_event_fn(
            "op_start",
            {
                "op_id": fetch_op_id,
                "op_type": "web_fetch",
                "component": "fetch_content",
                "target_id": str(target.get("target_id") or f"fetch-{idx}"),
                "url": url,
            },
        )
    try:
        hard_limit = _read_int(params.get("max_bytes_hard"), 0)
        if hard_limit is None:
            hard_limit = 0
        if hard_limit < 0:
            hard_limit = 0
        max_chars = max(500_000, _read_int(params.get("max_chars"), 8_000_000))
        fetch_url = url
        try:
            raw, content_type = _fetch_url_raw(fetch_url, timeout_s=timeout_s, max_bytes=hard_limit)
        except Exception:
            raw = b""
            content_type = ""
            resolved = False
            for alt_url in _fetch_retry_urls(url):
                try:
                    raw, content_type = _fetch_url_raw(alt_url, timeout_s=timeout_s, max_bytes=hard_limit)
                    fetch_url = alt_url
                    resolved = True
                    break
                except Exception:
                    continue
            if not resolved:
                raise
        raw_path = ""
        raw_path = _save_raw_fetch(
            paths,
            cycle_index=cycle_index,
            target_id=str(target.get("target_id") or f"fetch-{idx}"),
            url=fetch_url,
            raw=raw,
            content_type=content_type,
        )
        url_lower = str(fetch_url or "").lower()
        segments: list[str] = []
        if "pdf" in str(content_type).lower() or url_lower.endswith(".pdf"):
            text = _extract_text_from_pdf_bytes(raw, max_chars=max_chars)
            raw_html = ""
            segments = _extract_text_segments(text, max_chars=1800)
        else:
            raw_html = _decode_bytes(raw)
            if _is_listing_page(title=title, url=fetch_url):
                text = _extract_listing_text_with_fallback(raw_html, max_chars=max_chars)
            else:
                text = _extract_main_text_from_html(raw_html, max_chars=max_chars)
            segments = _extract_text_segments(text, max_chars=1800)
        page_urls = [fetch_url]
        if _is_listing_page(title=title, url=fetch_url) and raw_html:
            extra_urls = _discover_pagination_urls(raw_html, fetch_url, max_extra_pages=max(0, min(8, _read_int(params.get("max_extra_pages"), 6))))
            for extra_url in extra_urls:
                page_op_id = new_op_id_fn("web_fetch_page") if new_op_id_fn is not None else ""
                if raw_event_fn is not None and page_op_id:
                    raw_event_fn(
                        "op_start",
                        {
                            "op_id": page_op_id,
                            "op_type": "web_fetch_page",
                            "component": "fetch_content",
                            "target_id": str(target.get("target_id") or f"fetch-{idx}"),
                            "url": extra_url,
                            "parent_op_id": fetch_op_id,
                        },
                    )
                try:
                    extra_raw, extra_ctype = _fetch_url_raw(extra_url, timeout_s=timeout_s, max_bytes=hard_limit)
                    _save_raw_fetch(
                        paths,
                        cycle_index=cycle_index,
                        target_id=f"{target.get('target_id') or f'fetch-{idx}'}-extra",
                        url=extra_url,
                        raw=extra_raw,
                        content_type=extra_ctype,
                    )
                    extra_raw_html = _decode_bytes(extra_raw)
                    if _is_listing_page(title=title, url=extra_url):
                        extra_text = _extract_listing_text_with_fallback(extra_raw_html, max_chars=max_chars)
                    else:
                        extra_text = _extract_main_text_from_html(extra_raw_html, max_chars=max_chars)
                    if extra_text:
                        text = f"{text}\n\n{extra_text}" if text else extra_text
                        page_urls.append(extra_url)
                    extra_segments = _extract_text_segments(extra_text, max_chars=1800)
                    if extra_segments:
                        segments.extend(extra_segments)
                    if raw_event_fn is not None and page_op_id:
                        raw_event_fn(
                            "op_end",
                            {
                                "op_id": page_op_id,
                                "op_type": "web_fetch_page",
                                "component": "fetch_content",
                                "target_id": str(target.get("target_id") or f"fetch-{idx}"),
                                "url": extra_url,
                                "parent_op_id": fetch_op_id,
                                "status": "ok",
                                "bytes_read": len(extra_raw),
                                "content_type": str(extra_ctype or ""),
                            },
                        )
                except Exception:
                    if raw_event_fn is not None and page_op_id:
                        raw_event_fn(
                            "op_end",
                            {
                                "op_id": page_op_id,
                                "op_type": "web_fetch_page",
                                "component": "fetch_content",
                                "target_id": str(target.get("target_id") or f"fetch-{idx}"),
                                "url": extra_url,
                                "parent_op_id": fetch_op_id,
                                "status": "error",
                            },
                        )
                    continue
        text = _clean_text(text, limit_chars=max_chars)
        if not segments:
            segments = _build_extraction_windows(text, _extract_target_filters(target), max_windows=30, radius=2, max_chars=1200)
        selected_segments = [str(seg) for seg in segments if str(seg).strip()]
        output = {
            "session_id": session_id,
            "cycle_index": cycle_index,
            "target_id": str(target.get("target_id") or f"fetch-{idx}"),
            "requested_url": url,
            "url": fetch_url,
            "url_aliases": _unique_nonempty([url, fetch_url, *page_urls], limit=16),
            "url_title": title,
            "why": str(target.get("why") or ""),
            "status": "ok",
            "content_type": content_type,
            "bytes_read": len(raw),
            "fetch_hard_limit_bytes": hard_limit,
            "text_chars": len(text),
            "text": text,
            "peek": _peek_text(text, 320),
            "page_count": len(page_urls),
            "page_urls": page_urls,
            "segments": selected_segments,
            "segment_count": len(selected_segments),
            "raw_path": raw_path,
        }
        if raw_event_fn is not None and fetch_op_id:
            refs = [str(output.get("raw_path") or "")] if str(output.get("raw_path") or "") else None
            raw_event_fn(
                "op_end",
                {
                    "op_id": fetch_op_id,
                    "op_type": "web_fetch",
                    "component": "fetch_content",
                    "target_id": str(output.get("target_id") or ""),
                    "url": str(output.get("url") or ""),
                    "status": "ok",
                    "bytes_read": int(output.get("bytes_read") or 0),
                    "content_type": str(output.get("content_type") or ""),
                    "page_count": int(output.get("page_count") or 0),
                    "segment_count": int(output.get("segment_count") or 0),
                },
                refs,
            )
        return output
    except Exception as exc:
        output = {
            "session_id": session_id,
            "cycle_index": cycle_index,
            "target_id": str(target.get("target_id") or f"fetch-{idx}"),
            "requested_url": url,
            "url": url,
            "url_aliases": _unique_nonempty([url], limit=4),
            "url_title": title,
            "why": str(target.get("why") or ""),
            "status": "error",
            "error": f"{type(exc).__name__}:{exc}",
            "text_chars": 0,
            "text": "",
            "peek": "",
            "page_count": 0,
            "page_urls": [],
            "segments": [],
            "segment_count": 0,
            "raw_path": "",
        }
        if raw_event_fn is not None and fetch_op_id:
            raw_event_fn(
                "op_end",
                {
                    "op_id": fetch_op_id,
                    "op_type": "web_fetch",
                    "component": "fetch_content",
                    "target_id": str(output.get("target_id") or ""),
                    "url": url,
                    "status": "error",
                    "error": f"{type(exc).__name__}:{exc}",
                },
            )
        return output


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
    if not text:
        return []
    lines = [part.strip() for part in re.split(r"\n+|(?<=\.)\s+", text) if part.strip()]
    if not lines:
        return []

    terms: list[str] = []
    institution_terms: list[str] = []
    for key in ("institution", "institution_contains", "author", "author_contains", "topic"):
        raw = str(filters.get(key) or "").strip()
        if raw:
            toks = [tok for tok in re.findall(r"[A-Za-z0-9][A-Za-z0-9\-]{1,}", raw) if len(tok) >= 3]
            terms.extend(toks)
            if key.startswith("institution"):
                institution_terms.extend(toks)
    terms.extend(["doi", "arxiv", "accepted", "proceedings", "conference", "paper", "papers"])
    terms_l = {term.lower() for term in terms}
    inst_l = {term.lower() for term in institution_terms}
    year_gte = _safe_int(filters.get("year_gte"))

    scored_hits: list[tuple[int, int]] = []
    for idx, line in enumerate(lines):
        lowered = line.lower()
        score = 0
        if inst_l and any(term in lowered for term in inst_l):
            score += 5
        if any(term in lowered for term in terms_l):
            score += 2
        if year_gte is not None:
            years = [int(v) for v in re.findall(r"\b(?:19|20)\d{2}\b", lowered)]
            if any(v >= year_gte for v in years):
                score += 1
        if score > 0:
            scored_hits.append((idx, score))
    if not scored_hits:
        scored_hits = [(idx, 1) for idx in range(min(len(lines), max_windows))]

    windows: list[str] = []
    seen: set[str] = set()
    selected_indexes: list[int] = []

    # Ensure spread across document to avoid only top/front matter windows.
    total = len(lines)
    if total > 0:
        boundaries = [0, total // 4, total // 2, (3 * total) // 4, total]
        for i in range(4):
            lo, hi = boundaries[i], boundaries[i + 1]
            seg_hits = [(idx, score) for idx, score in scored_hits if lo <= idx < hi]
            if not seg_hits:
                continue
            best = sorted(seg_hits, key=lambda p: (p[1], -p[0]), reverse=True)[0]
            selected_indexes.append(best[0])

    for idx, _score in sorted(scored_hits, key=lambda p: (p[1], -p[0]), reverse=True):
        if idx not in selected_indexes:
            selected_indexes.append(idx)
        if len(selected_indexes) >= max_windows * 2:
            break

    for idx in selected_indexes:
        start = max(0, idx - radius)
        end = min(len(lines), idx + radius + 1)
        block = " ".join(lines[start:end]).strip()
        if not block:
            continue
        block = _peek_text(block, max_chars)
        key = block.lower()
        if key in seen:
            continue
        seen.add(key)
        windows.append(block)
        if len(windows) >= max_windows:
            break
    return windows


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
    api_key = str(os.environ.get(api_key_env) or "").strip()
    if not api_key:
        raise RuntimeError(f"missing_api_key:{api_key_env}")

    try:
        from openai import OpenAI
    except ModuleNotFoundError as exc:
        raise RuntimeError("missing_dependency:openai") from exc

    resolved_model, base_url = _resolve_openai_model_and_base_url(model=model, api_key_env=api_key_env)
    client = OpenAI(
        api_key=api_key,
        base_url=base_url or None,
        max_retries=(int(max_retries) if max_retries is not None else 2),
    )
    option_kwargs: dict[str, Any] = {}
    if timeout_s and timeout_s > 0:
        option_kwargs["timeout"] = float(timeout_s)
    if max_retries is not None:
        option_kwargs["max_retries"] = int(max_retries)
    if option_kwargs:
        client = client.with_options(**option_kwargs)

    payload = _openai_completion_json_payload(
        client=client,
        model=resolved_model,
        messages=messages,
        with_response_format=True,
        max_tokens=max_tokens,
    )
    if payload:
        return payload

    retry_messages = list(messages) + [
        {
            "role": "system",
            "content": (
                "Your previous response was not parseable. "
                "Return ONLY a valid JSON object. No markdown, no prose."
            ),
        }
    ]
    payload = _openai_completion_json_payload(
        client=client,
        model=resolved_model,
        messages=retry_messages,
        with_response_format=False,
        max_tokens=max_tokens,
    )
    if not payload:
        raise RuntimeError("llm_invalid_json")
    return payload


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


def _default_plan_state() -> dict[str, Any]:
    return {
        "active_step": {},
        "todo": [],
    }

def _make_progress_record(*, source: str, step_id: str, status: str, note: str) -> dict[str, Any]:
    return {
        "source": str(source or ""),
        "step_id": str(step_id or ""),
        "status": str(status or ""),
        "note": _peek_text(str(note or ""), 120),
    }


@dataclass
class _CoverageSummary:
    shortlisted_urls_total: int = 0
    shortlisted_urls_complete: int = 0
    shortlisted_urls_with_more_results: int = 0
    url_checks: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "shortlisted_urls_total": int(self.shortlisted_urls_total or 0),
            "shortlisted_urls_complete": int(self.shortlisted_urls_complete or 0),
            "shortlisted_urls_with_more_results": int(self.shortlisted_urls_with_more_results or 0),
            "url_checks": [
                dict(row)
                for row in self.url_checks
                if isinstance(row, dict) and str(row.get("url") or "").strip()
            ],
        }


@dataclass
class _PaperState:
    final_candidates: list[dict[str, Any]] = field(default_factory=list)
    fallback_candidates: list[dict[str, Any]] = field(default_factory=list)
    coverage_summary: _CoverageSummary = field(default_factory=_CoverageSummary)

    def as_dict(self) -> dict[str, Any]:
        return {
            "final_candidates": [dict(row) for row in self.final_candidates if isinstance(row, dict)],
            "fallback_candidates": [dict(row) for row in self.fallback_candidates if isinstance(row, dict)],
            "coverage_summary": self.coverage_summary.as_dict(),
        }


@dataclass
class _AgenticSearchLoop:
    paths: dict[str, Path]
    prompt: str
    session_id: str
    max_cycles: int
    searxng_url: str
    debug_retrieval: bool
    progress_callback: ProgressCallback | None = None
    run_result: dict[str, Any] = field(default_factory=_new_result_state)
    paper_state: "_PaperState" = field(default_factory=lambda: _PaperState())
    agent_memory: dict[str, Any] = field(default_factory=dict)
    agent_plan: dict[str, Any] = field(default_factory=_default_plan_state)
    cycle_trace: list[dict[str, Any]] = field(default_factory=list)
    url_state: "_UrlState" = field(default_factory=lambda: _UrlState())
    extract_state: "_ExtractState" = field(default_factory=lambda: _ExtractState())
    run_state: "_RunState" = field(default_factory=lambda: _RunState())
    trace_state: "_TraceState" = field(default_factory=lambda: _TraceState())
    agent_config: "_AgentConfig" = field(default_factory=lambda: _AgentConfig())
    extract_config: "_ExtractConfig" = field(default_factory=lambda: _ExtractConfig())
    search_config: "_SearchConfig" = field(default_factory=lambda: _SearchConfig())
    result_config: "_ResultConfig" = field(default_factory=lambda: _ResultConfig())

    def _emit(self, **payload: Any) -> None:
        _emit_progress(self.progress_callback, **payload)

    def _append_raw(
        self,
        *,
        action_id: str,
        event_type: str,
        payload: dict[str, Any],
        refs: list[str] | None = None,
    ) -> str:
        return _append_raw_event(
            path=self.paths["raw"],
            trace_state=self.trace_state,
            session_id=self.session_id,
            cycle_index=int(self.run_state.cycle_index or 0),
            action_id=action_id,
            event_type=event_type,
            payload=payload,
            raw_refs=refs,
        )

    def _write_trajectory(self, *, status: str | None = None, stop_reason: str | None = None) -> None:
        agent_model = str(self.agent_config.llm_model or "deepseek/deepseek-chat")
        shortlist_size = max(1, int(self.search_config.shortlist_size or 1))
        _write_agentic_trajectory(
            self.paths["trajectory"],
            session_id=self.session_id,
            prompt=self.prompt,
            moves=self.cycle_trace,
            status=str(status if status is not None else self.run_state.status or "running"),
            stop_reason=str(stop_reason if stop_reason is not None else self.run_state.stop_reason or ""),
            llm_model=agent_model,
            max_cycles=self.max_cycles,
            top_n=shortlist_size,
        )

    def _write_result(self) -> None:
        agent_model = str(self.agent_config.llm_model or "deepseek/deepseek-chat")
        display_limit = max(0, int(self.result_config.display_limit or 0))
        compact_result = _compact_result_payload(
            run_result=self.run_result,
            paper_state=self.paper_state.as_dict(),
            prompt=self.prompt,
            llm_model=agent_model,
            display_top_n=display_limit,
        )
        write_yaml(self.paths["result"], compact_result)

    def _execute_action(self, *, action: str, params: dict) -> dict:
        cycle_index = int(self.run_state.cycle_index or 0)
        tool_runtime = {
            "url_hits": [row for row in self.url_state.hits if isinstance(row, dict)],
            "extract_state_by_url": {
                str(url): dict(row)
                for url, row in dict(self.extract_state.by_url or {}).items()
                if str(url).strip() and isinstance(row, dict)
            },
            "fetched_records": [
                row for row in self.extract_state.fetched_records if isinstance(row, dict)
            ],
        }
        result = _execute_agent_action(
            action=action,
            session_id=self.session_id,
            cycle_index=cycle_index,
            params=params,
            paths=self.paths,
            user_prompt=self.prompt,
            searxng_url=self.searxng_url,
            search_categories=str(self.search_config.categories or "general"),
            web_results_per_query=max(1, int(self.search_config.results_per_query or 1)),
            timeout_s=float(self.search_config.timeout_s or 8.0),
            llm_model=str(self.agent_config.llm_model or "deepseek/deepseek-chat"),
            llm_extractor_model=str(self.extract_config.llm_model or "deepseek/deepseek-chat"),
            llm_api_key_env=str(self.extract_config.api_key_env or "DS_API_KEY"),
            extract_use_llm_extractor=bool(self.extract_config.use_llm_extractor),
            progress_callback=self.progress_callback,
            runtime_state=tool_runtime,
            next_op_id_fn=lambda _state, prefix="op": _next_op_id(self.trace_state, prefix),
            raw_event_fn=lambda event_type, payload, refs=None: self._append_raw(
                action_id=f"c{cycle_index:02d}",
                event_type=event_type,
                payload=payload,
                refs=refs,
            ),
        )
        self.url_state.hits = [row for row in (tool_runtime.get("url_hits") or []) if isinstance(row, dict)]
        self.extract_state.by_url = {
            str(url): dict(row)
            for url, row in dict(tool_runtime.get("extract_state_by_url") or {}).items()
            if str(url).strip() and isinstance(row, dict)
        }
        self.extract_state.fetched_records = [
            row for row in (tool_runtime.get("fetched_records") or []) if isinstance(row, dict)
        ]
        return result

    def run(self) -> Path:
        return _run_agentic_search_loop(self)


@dataclass
class _PlanTurn:
    action_id: str
    raw_event_ids: list[str]
    selected_action: str
    action_params: dict[str, Any]
    agent_decision: "_AgentDecision"


@dataclass
class _ActionRun:
    raw_event_ids: list[str]
    action_result: dict[str, Any]


@dataclass
class _RunState:
    cycle_index: int = 0
    status: str = "running"
    stop_reason: str = ""


@dataclass
class _TraceState:
    raw_event_seq: int = 0
    op_event_seq: int = 0


@dataclass
class _UrlState:
    hits: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class _ExtractState:
    by_url: dict[str, dict[str, Any]] = field(default_factory=dict)
    fetched_records: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class _SearchConfig:
    shortlist_size: int = 5
    results_per_query: int = 8
    timeout_s: float = 8.0
    categories: str = "general"


@dataclass
class _AgentConfig:
    max_queries_per_turn: int = 4
    llm_model: str = "deepseek/deepseek-chat"
    api_key_env: str = "DS_API_KEY"


@dataclass
class _ExtractConfig:
    llm_model: str = "deepseek/deepseek-chat"
    api_key_env: str = "DS_API_KEY"
    use_llm_extractor: bool = True


@dataclass
class _ResultConfig:
    display_limit: int = 0


@dataclass
class _AgentDecision:
    mode: str
    reason: str


def _paper_coverage(loop: _AgenticSearchLoop) -> dict[str, Any]:
    return loop.paper_state.coverage_summary.as_dict()


def _paper_final_candidates(loop: _AgenticSearchLoop) -> list[dict[str, Any]]:
    return [row for row in loop.paper_state.final_candidates if isinstance(row, dict)]


def _paper_fallback_candidates(loop: _AgenticSearchLoop) -> list[dict[str, Any]]:
    return [row for row in loop.paper_state.fallback_candidates if isinstance(row, dict)]


def _set_paper_candidates(
    loop: _AgenticSearchLoop,
    *,
    final_candidates: list[dict[str, Any]],
    fallback_candidates: list[dict[str, Any]],
) -> None:
    loop.paper_state.final_candidates = [dict(row) for row in final_candidates if isinstance(row, dict)]
    loop.paper_state.fallback_candidates = [dict(row) for row in fallback_candidates if isinstance(row, dict)]


def _update_paper_coverage(
    loop: _AgenticSearchLoop,
    *,
    action_result: dict[str, Any],
    extracted_paper_candidates: list[dict[str, Any]],
) -> None:
    coverage = loop.paper_state.coverage_summary
    extract_trace = action_result.get("extract_windows_trace")
    if not isinstance(extract_trace, list):
        extract_trace = []
    url_checks: list[dict[str, Any]] = []
    for item in extract_trace:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        segments_done = int(item.get("segments_done") or 0)
        segment_total = int(item.get("segment_total") or 0)
        has_more = bool(item.get("coverage_has_more"))
        url_checks.append(
            {
                "url": url,
                "target_id": str(item.get("target_id") or ""),
                "segments_done": segments_done,
                "segment_total": segment_total,
                "all_papers_extracted": segment_total > 0 and segments_done >= segment_total and not has_more,
                "has_more_results": has_more,
            }
        )
    coverage.url_checks = url_checks
    coverage.shortlisted_urls_total = len(url_checks)
    coverage.shortlisted_urls_complete = sum(
        1 for row in url_checks if bool(row.get("all_papers_extracted"))
    )
    coverage.shortlisted_urls_with_more_results = sum(
        1 for row in url_checks if bool(row.get("has_more_results"))
    )


def _refresh_agent_memory(loop: _AgenticSearchLoop) -> None:
    loop.agent_memory = _build_agent_memory(
        user_prompt=loop.prompt,
        plan_state=loop.agent_plan,
        url_hits=[row for row in loop.url_state.hits if isinstance(row, dict)],
        extract_state_by_url={str(url): dict(row) for url, row in dict(loop.extract_state.by_url or {}).items() if str(url).strip() and isinstance(row, dict)},
        papers=_paper_final_candidates(loop),
        cycle_trace=[row for row in loop.cycle_trace if isinstance(row, dict)],
        stop_reason=str(loop.run_state.stop_reason or ""),
    )


def _run_agentic_cycle(loop: _AgenticSearchLoop, cycle_index: int) -> bool:
    plan_result = _run_agent_turn(loop, cycle_index)
    if plan_result is None:
        return True

    action_ctx = _run_cycle_action(loop, cycle_index, plan_result)
    return _finalize_cycle(loop, cycle_index, plan_result, action_ctx)


def _finalize_cycle(
    loop: _AgenticSearchLoop,
    cycle_index: int,
    plan_result: _PlanTurn,
    action_ctx: _ActionRun,
) -> bool:
    action_id = str(plan_result.action_id)
    selected_action = str(plan_result.selected_action)
    planned_queries = list((plan_result.action_params or {}).get("queries") or [])
    agent_decision = plan_result.agent_decision
    raw_event_ids = list(action_ctx.raw_event_ids)
    action_result = dict(action_ctx.action_result)
    raw_candidates = list(action_result.get("raw_candidates") or [])
    extracted_paper_candidates = list(action_result.get("paper_candidates") or [])

    if selected_action == "search_web":
        url_shortlisted = _finalize_search_action(
            loop=loop,
            cycle_index=cycle_index,
            raw_candidates=raw_candidates,
            shortlist_hints=action_result.get("shortlist_hints"),
        )
    else:
        url_shortlisted = _finalize_extract_action(
            loop=loop,
            extracted_paper_candidates=extracted_paper_candidates,
            action_result=action_result,
        )
    cycle_delta = _record_cycle_summary(
        loop=loop,
        selected_action=selected_action,
        action_result=action_result,
        raw_candidates=raw_candidates,
        extracted_paper_candidates=extracted_paper_candidates,
        url_shortlisted=url_shortlisted,
    )

    decision_info = _decide_cycle_outcome(
        loop=loop,
        cycle_index=cycle_index,
        selected_action=selected_action,
        planned_queries=planned_queries,
        agent_decision=agent_decision,
        action_result=action_result,
        url_shortlisted=url_shortlisted,
        extracted_paper_candidates=extracted_paper_candidates,
    )
    decision = str(decision_info["decision"])
    decision_reason = str(decision_info["decision_reason"])
    cycle_stop_reason = str(decision_info["stop_reason"])

    before_final_count = len(_paper_final_candidates(loop))
    if selected_action == "extract_content":
        extract_finalize = _finalize_extract_candidates(
            loop=loop,
            action_id=action_id,
            action_result=action_result,
            extracted_paper_candidates=extracted_paper_candidates,
            raw_event_ids=raw_event_ids,
        )
        extracted_paper_candidates = list(extract_finalize["paper_candidates"])
        raw_event_ids = list(extract_finalize["raw_event_ids"])

    after_final_count = len(_paper_final_candidates(loop))
    cycle_delta["final_candidate_delta"] = after_final_count - before_final_count
    shortlisted_count = len(url_shortlisted) if selected_action == "search_web" else len(extracted_paper_candidates)
    latest_tool_progress = _make_progress_record(
        source="tool",
        step_id=str((loop.agent_plan.get("active_step") or {}).get("step_id") or ""),
        status=str(action_result.get("status") or ""),
        note=str(action_result.get("notes") or ""),
    )
    _record_cycle_outcome(
        loop=loop,
        cycle_index=cycle_index,
        action_id=action_id,
        selected_action=selected_action,
        decision=decision,
        decision_reason=decision_reason,
        cycle_stop_reason=cycle_stop_reason,
        raw_candidate_count=len(raw_candidates),
        shortlisted_count=shortlisted_count,
        action_result=action_result,
        cycle_delta=cycle_delta,
        raw_event_ids=raw_event_ids,
        latest_progress=latest_tool_progress,
    )
    return _commit_cycle_state(
        loop=loop,
        decision=decision,
        cycle_stop_reason=cycle_stop_reason,
    )


def _finalize_search_action(
    *,
    loop: _AgenticSearchLoop,
    cycle_index: int,
    raw_candidates: list[dict[str, Any]],
    shortlist_hints: Any,
) -> list[dict[str, Any]]:
    shortlist_size = max(1, int(loop.search_config.shortlist_size or 1))
    filtered_rows, _ = _filter_search_rows(raw_candidates)
    ranked_rows = _rank_candidates(filtered_rows)
    ranked_rows = _apply_shortlist_hints(ranked_rows, shortlist_hints)
    url_shortlisted = _select_diverse_shortlist(ranked_rows, shortlist_size)
    url_hits = _to_url_hits(loop.session_id, cycle_index, url_shortlisted, shortlist_size)
    loop.url_state.hits = _merge_url_hits(
        [row for row in loop.url_state.hits if isinstance(row, dict)],
        url_hits,
        limit=max(shortlist_size * max(2, loop.max_cycles), 16),
    )
    return url_shortlisted


def _finalize_extract_action(
    *,
    loop: _AgenticSearchLoop,
    extracted_paper_candidates: list[dict[str, Any]],
    action_result: dict[str, Any],
) -> list[dict[str, Any]]:
    _update_paper_coverage(
        loop=loop,
        action_result=action_result,
        extracted_paper_candidates=extracted_paper_candidates,
    )
    return []


def _finalize_extract_candidates(
    *,
    loop: _AgenticSearchLoop,
    action_id: str,
    action_result: dict[str, Any],
    extracted_paper_candidates: list[dict[str, Any]],
    raw_event_ids: list[str],
) -> dict[str, Any]:
    cycle_candidates = [row for row in extracted_paper_candidates if isinstance(row, dict)]
    cycle_candidates = list(cycle_candidates)
    requires_venue = _requires_venue_evidence(loop.prompt, loop.agent_plan)
    existing_final = _paper_final_candidates(loop)
    existing_fallback = _paper_fallback_candidates(loop)
    if requires_venue:
        venue_scoped = [row for row in cycle_candidates if _has_venue_evidence(row)]
        fallback_scoped = [row for row in cycle_candidates if not _has_venue_evidence(row)]
        _set_paper_candidates(
            loop,
            final_candidates=_merge_paper_candidates(existing_final, venue_scoped),
            fallback_candidates=_merge_paper_candidates(existing_fallback, fallback_scoped),
        )
    else:
        _set_paper_candidates(
            loop,
            final_candidates=_merge_paper_candidates(existing_final, cycle_candidates),
            fallback_candidates=existing_fallback,
        )
    return {
        "paper_candidates": cycle_candidates,
        "raw_event_ids": raw_event_ids,
    }


def _decide_cycle_outcome(
    *,
    loop: _AgenticSearchLoop,
    cycle_index: int,
    selected_action: str,
    planned_queries: list[str],
    agent_decision: _AgentDecision,
    action_result: dict[str, Any],
    url_shortlisted: list[dict[str, Any]],
    extracted_paper_candidates: list[dict[str, Any]],
) -> dict[str, str]:
    agent_decision_mode = str(agent_decision.mode or "").strip().lower()
    agent_decision_reason = str(agent_decision.reason or "").strip()
    agent_should_stop = agent_decision_mode == "stop"
    if bool(action_result.get("stop", False)):
        return {
            "decision": "stop",
            "decision_reason": "action_stop",
            "stop_reason": str(action_result.get("stop_reason") or "action_stop"),
        }
    if selected_action == "search_web" and not url_shortlisted:
        return {
            "decision": "stop",
            "decision_reason": "no_candidates",
            "stop_reason": "no_candidates",
        }
    if agent_should_stop:
        return {
            "decision": "stop",
            "decision_reason": "llm_converged",
            "stop_reason": agent_decision_reason or "llm_converged",
        }
    if cycle_index >= loop.max_cycles:
        return {
            "decision": "stop",
            "decision_reason": "max_cycles_reached",
            "stop_reason": "max_cycles_reached",
        }
    if selected_action == "search_web" and not planned_queries:
        return {
            "decision": "stop",
            "decision_reason": "agent_no_queries",
            "stop_reason": "agent_no_queries",
        }
    return {
        "decision": "continue",
        "decision_reason": "llm_continue",
        "stop_reason": "",
    }


def _record_cycle_summary(
    *,
    loop: _AgenticSearchLoop,
    selected_action: str,
    action_result: dict[str, Any],
    raw_candidates: list[dict[str, Any]],
    extracted_paper_candidates: list[dict[str, Any]],
    url_shortlisted: list[dict[str, Any]],
) -> dict[str, Any]:
    cycle_delta = {
        "retrieved_count": len(raw_candidates),
        "shortlisted_count": len(url_shortlisted) if selected_action == "search_web" else len(extracted_paper_candidates),
        "final_candidate_delta": 0,
    }
    if loop.cycle_trace:
        loop.cycle_trace[-1]["action_result"] = {
            "status": str(action_result.get("status") or ""),
            "notes": _peek_text(str(action_result.get("notes") or ""), 220),
        }
        if selected_action == "extract_content":
            extract_trace = action_result.get("extract_windows_trace") if isinstance(action_result.get("extract_windows_trace"), list) else []
            loop.cycle_trace[-1]["action_debug"] = {
                "targets": [
                    {
                        "target_id": str(item.get("target_id") or ""),
                        "url": str(item.get("url") or ""),
                        "window_count": int(item.get("window_count") or 0),
                        "segment_total": int(item.get("segment_total") or 0),
                        "segment_batch_size": int(item.get("segment_batch_size") or 0),
                        "batch_mode": str(item.get("batch_mode") or ""),
                        "input_token_budget": int(item.get("input_token_budget") or 0),
                        "segments_done": int(item.get("segments_done") or 0),
                        "segments_pending": int(item.get("segments_pending") or 0),
                        "coverage_pct": float(item.get("coverage_pct") or 0.0),
                        "llm_requests": int(item.get("llm_requests") or 0),
                        "llm_responses": int(item.get("llm_responses") or 0),
                        "llm_empty_responses": int(item.get("llm_empty_responses") or 0),
                        "llm_errors": int(item.get("llm_errors") or 0),
                        "last_input_tokens_est": int(item.get("last_input_tokens_est") or 0),
                        "last_segment_tokens_est": int(item.get("last_segment_tokens_est") or 0),
                        "last_scaffold_tokens_est": int(item.get("last_scaffold_tokens_est") or 0),
                        "llm_items_count": int(item.get("llm_items_count") or 0),
                        "llm_items": [
                            str(row.get("paper_title") or "")
                            for row in (item.get("llm_items") or [])[:10]
                            if isinstance(row, dict)
                        ],
                    }
                    for item in extract_trace[:8]
                    if isinstance(item, dict)
                ],
            }
        loop.cycle_trace[-1]["delta"] = cycle_delta
    return cycle_delta


def _record_cycle_refs(
    *,
    loop: _AgenticSearchLoop,
    raw_event_ids: list[str],
) -> None:
    if not loop.cycle_trace:
        return
    fetched_rows_for_refs = [
        row for row in loop.extract_state.fetched_records if isinstance(row, dict)
    ]
    fetch_raw_paths = _unique_nonempty([str(row.get("raw_path") or "") for row in fetched_rows_for_refs], limit=12)
    loop.cycle_trace[-1]["refs"] = {
        "raw_event_ids": list(raw_event_ids),
        "fetch_raw_paths": fetch_raw_paths,
    }


def _record_cycle_outcome(
    *,
    loop: _AgenticSearchLoop,
    cycle_index: int,
    action_id: str,
    selected_action: str,
    decision: str,
    decision_reason: str,
    cycle_stop_reason: str,
    raw_candidate_count: int,
    shortlisted_count: int,
    action_result: dict[str, Any],
    cycle_delta: dict[str, Any],
    raw_event_ids: list[str],
    latest_progress: dict[str, Any] | None = None,
) -> None:
    if loop.cycle_trace:
        loop.cycle_trace[-1]["delta"] = cycle_delta
    _record_cycle_refs(loop=loop, raw_event_ids=raw_event_ids)
    loop._emit(
        event="agentic_cycle_decision",
        cycle_index=cycle_index,
        action_id=action_id,
        decision=decision,
        decision_reason=decision_reason,
        stop_reason=cycle_stop_reason,
        raw_candidates=raw_candidate_count,
        shortlisted=shortlisted_count,
        final_candidates=len(_paper_final_candidates(loop)),
    )
    loop.run_result["cycle_count"] = cycle_index
    progress_snapshot = _build_progress_snapshot(
        cycle_index=cycle_index,
        selected_action=selected_action,
        plan_state=loop.agent_plan,
        latest_progress=latest_progress,
        decision=decision,
        decision_reason=decision_reason,
        final_candidates=len(_paper_final_candidates(loop)),
        fallback_candidates=len(_paper_fallback_candidates(loop)),
    )
    if loop.cycle_trace:
        loop.cycle_trace[-1]["decision"] = {
            "decision": decision,
            "decision_reason": decision_reason,
            "stop_reason": cycle_stop_reason,
        }
        loop.cycle_trace[-1]["progress"] = progress_snapshot
    loop._emit(
        event="agentic_progress_snapshot",
        cycle_index=cycle_index,
        action_id=action_id,
        progress=progress_snapshot,
    )


def _commit_cycle_state(
    *,
    loop: _AgenticSearchLoop,
    decision: str,
    cycle_stop_reason: str,
) -> bool:
    loop._write_trajectory(status="running", stop_reason="")
    _refresh_agent_memory(loop)

    if decision == "stop":
        loop.run_state.stop_reason = str(cycle_stop_reason or "")
        _refresh_agent_memory(loop)
        return True
    return False


def _run_cycle_action(
    loop: _AgenticSearchLoop,
    cycle_index: int,
    plan_result: _PlanTurn,
) -> _ActionRun:
    action_id = str(plan_result.action_id)
    raw_event_ids = list(plan_result.raw_event_ids)
    selected_action = str(plan_result.selected_action)
    action_params = dict(plan_result.action_params)

    _start_cycle_action(
        loop=loop,
        cycle_index=cycle_index,
        action_id=action_id,
        selected_action=selected_action,
        action_params=action_params,
        raw_event_ids=raw_event_ids,
    )

    action_result = loop._execute_action(action=selected_action, params=action_params)
    _finish_cycle_action(
        loop=loop,
        cycle_index=cycle_index,
        action_id=action_id,
        selected_action=selected_action,
        action_result=action_result,
        raw_event_ids=raw_event_ids,
    )

    return _ActionRun(raw_event_ids=raw_event_ids, action_result=action_result)


def _start_cycle_action(
    *,
    loop: _AgenticSearchLoop,
    cycle_index: int,
    action_id: str,
    selected_action: str,
    action_params: dict[str, Any],
    raw_event_ids: list[str],
) -> None:
    action_params["debug_retrieval"] = loop.debug_retrieval
    action_input_raw_id = loop._append_raw(
        action_id=action_id,
        event_type="action_input",
        payload={"action": selected_action, "params": action_params},
    )
    raw_event_ids.append(action_input_raw_id)
    loop._emit(
        event="agentic_action_start",
        cycle_index=cycle_index,
        action_id=action_id,
        action=selected_action,
        active_step_id=str((loop.agent_plan.get("active_step") or {}).get("step_id") or ""),
        raw_event_id=action_input_raw_id,
    )
    loop._write_trajectory(status="running", stop_reason="")


def _finish_cycle_action(
    *,
    loop: _AgenticSearchLoop,
    cycle_index: int,
    action_id: str,
    selected_action: str,
    action_result: dict[str, Any],
    raw_event_ids: list[str],
) -> None:
    action_output_raw_id = loop._append_raw(
        action_id=action_id,
        event_type="action_output",
        payload=action_result,
    )
    raw_event_ids.append(action_output_raw_id)
    loop._emit(
        event="agentic_action_done",
        cycle_index=cycle_index,
        action_id=action_id,
        action=selected_action,
        status=str(action_result.get("status") or ""),
        notes=_peek_text(str(action_result.get("notes") or ""), 160),
        raw_event_id=action_output_raw_id,
    )


def _run_agent_turn(loop: _AgenticSearchLoop, cycle_index: int) -> _PlanTurn | None:
    loop.run_state.cycle_index = int(cycle_index)
    _refresh_agent_memory(loop)
    loop._emit(
        event="agentic_cycle_start",
        cycle_index=cycle_index,
        max_cycles=loop.max_cycles,
    )
    agent_input = _build_agent_working_state(
        user_prompt=loop.prompt,
        cycle_index=cycle_index,
        max_cycles=loop.max_cycles,
        agent_memory=loop.agent_memory,
    )
    action_id = f"c{cycle_index:02d}"
    raw_event_ids, agent_op_id = _start_agent_turn(
        loop=loop,
        cycle_index=cycle_index,
        action_id=action_id,
        agent_input=agent_input,
    )
    try:
        agent_output = _agent_next_action_llm(
            working_state=agent_input,
            queries_per_cycle=max(1, int(loop.agent_config.max_queries_per_turn or 1)),
            model=str(loop.agent_config.llm_model or "deepseek/deepseek-chat"),
            api_key_env=str(loop.agent_config.api_key_env or "DS_API_KEY"),
        )
    except Exception as exc:
        _fail_agent_turn(
            loop=loop,
            action_id=action_id,
            agent_op_id=agent_op_id,
            raw_event_ids=raw_event_ids,
            exc=exc,
        )
        raise

    normalized_output = _normalize_agent_turn_output(agent_output)
    selected_action = str(normalized_output["selected_action"])
    planned_queries = list(normalized_output["planned_queries"])
    action_params = dict(normalized_output["action_params"])
    state_delta = dict(normalized_output["state_delta"])
    latest_progress = dict(normalized_output["latest_progress"])
    agent_decision = normalized_output["agent_decision"]
    agent_debug = dict(normalized_output["agent_debug"])
    _finish_agent_turn(
        loop=loop,
        cycle_index=cycle_index,
        action_id=action_id,
        agent_output=agent_output,
        raw_event_ids=raw_event_ids,
        agent_op_id=agent_op_id,
        agent_debug=agent_debug,
    )

    if state_delta:
        loop.agent_plan = _apply_plan_update(loop.agent_plan, state_delta)
    _refresh_agent_memory(loop)
    if _should_stop_after_agent_turn(agent_decision, selected_action, planned_queries):
        loop.run_state.stop_reason = str(agent_decision.reason or "").strip() or "llm_converged"
        loop._emit(
            event="agentic_cycle_decision",
            cycle_index=cycle_index,
            action_id=action_id,
            decision="stop",
            decision_reason="agent_stop",
            stop_reason=str(loop.run_state.stop_reason or ""),
        )
        return None

    _seed_cycle_trace(
        loop=loop,
        cycle_index=cycle_index,
        action_id=action_id,
        selected_action=selected_action,
        action_params=action_params,
        state_delta=state_delta,
        latest_progress=latest_progress,
    )
    return _PlanTurn(
        action_id=action_id,
        raw_event_ids=raw_event_ids,
        selected_action=selected_action,
        action_params=action_params,
        agent_decision=agent_decision,
    )


def _normalize_agent_turn_output(agent_output: dict[str, Any]) -> dict[str, Any]:
    selected_action = str(agent_output.get("action") or "search_web")
    planned_queries = list(agent_output.get("queries") or [])
    action_params = _sanitize_agent_action_params(selected_action, dict(agent_output.get("params") or {}))
    if selected_action == "search_web" and not list(action_params.get("queries") or []):
        action_params["queries"] = planned_queries
    state_delta = dict(agent_output.get("state_delta") or {})
    progress_update = dict(agent_output.get("progress") or {})
    agent_decision = agent_output.get("decision") if isinstance(agent_output.get("decision"), dict) else {}
    latest_progress = {}
    if progress_update:
        latest_progress = _make_progress_record(
            source="agent",
            step_id=str(progress_update.get("step_id") or ""),
            status=str(progress_update.get("status") or ""),
            note=str(progress_update.get("note") or ""),
        )
    return {
        "selected_action": selected_action,
        "planned_queries": planned_queries,
        "action_params": action_params,
        "state_delta": state_delta,
        "latest_progress": latest_progress,
        "agent_decision": _AgentDecision(
            mode=str(agent_decision.get("mode") or "").strip().lower(),
            reason=str(agent_decision.get("reason") or "").strip(),
        ),
        "agent_debug": agent_output.get("_debug") if isinstance(agent_output.get("_debug"), dict) else {},
    }


def _seed_cycle_trace(
    *,
    loop: _AgenticSearchLoop,
    cycle_index: int,
    action_id: str,
    selected_action: str,
    action_params: dict[str, Any],
    state_delta: dict[str, Any],
    latest_progress: dict[str, Any],
) -> None:
    loop.cycle_trace.append(
        {
            "action_id": action_id,
            "cycle_index": cycle_index,
            "timestamp": _utc_now(),
            "state_delta": state_delta,
            "action_input": _trace_action_input(selected_action, action_params),
            "action_result": {
                "status": "running",
                "notes": "action_started",
            },
            "progress": _build_progress_snapshot(
                cycle_index=cycle_index,
                selected_action=selected_action,
                plan_state=loop.agent_plan,
                latest_progress=latest_progress,
            ),
            "delta": {},
        }
    )


def _should_stop_after_agent_turn(
    agent_decision: _AgentDecision,
    selected_action: str,
    planned_queries: list[str],
) -> bool:
    return (
        str(agent_decision.mode or "").strip().lower() == "stop"
        and selected_action == "search_web"
        and not planned_queries
    )


def _start_agent_turn(
    *,
    loop: _AgenticSearchLoop,
    cycle_index: int,
    action_id: str,
    agent_input: dict[str, Any],
) -> tuple[list[str], str]:
    raw_event_ids: list[str] = []
    request_raw_id = loop._append_raw(action_id=action_id, event_type="agent_request", payload=agent_input)
    raw_event_ids.append(request_raw_id)
    loop._emit(
        event="agentic_llm_agent_request",
        cycle_index=cycle_index,
        action_id=action_id,
        payload=agent_input,
        raw_event_id=request_raw_id,
    )
    agent_op_id = _next_op_id(loop.trace_state, "agent_llm")
    raw_event_ids.append(
        loop._append_raw(
            action_id=action_id,
            event_type="op_start",
            payload={
                "op_id": agent_op_id,
                "op_type": "agent_llm",
                "component": "agent",
                "model": str(loop.agent_config.llm_model or "deepseek/deepseek-chat"),
                "input_chars": 0,
                "input_tokens_est": 0,
                "request_ref": request_raw_id,
            },
            refs=[request_raw_id],
        )
    )
    return raw_event_ids, agent_op_id


def _fail_agent_turn(
    *,
    loop: _AgenticSearchLoop,
    action_id: str,
    agent_op_id: str,
    raw_event_ids: list[str],
    exc: Exception,
) -> None:
    raw_event_ids.append(
        loop._append_raw(
            action_id=action_id,
            event_type="op_end",
            payload={
                "op_id": agent_op_id,
                "op_type": "agent_llm",
                "component": "agent",
                "model": str(loop.agent_config.llm_model or "deepseek/deepseek-chat"),
                "status": "error",
                "error": f"{type(exc).__name__}:{exc}",
            },
        )
    )


def _finish_agent_turn(
    *,
    loop: _AgenticSearchLoop,
    cycle_index: int,
    action_id: str,
    agent_output: dict[str, Any],
    raw_event_ids: list[str],
    agent_op_id: str,
    agent_debug: dict[str, Any],
) -> None:
    agent_response_raw_id = loop._append_raw(
        action_id=action_id,
        event_type="agent_response",
        payload={**agent_output, "_debug": agent_debug},
    )
    raw_event_ids.append(agent_response_raw_id)
    raw_event_ids.append(
        loop._append_raw(
            action_id=action_id,
            event_type="op_end",
            payload={
                "op_id": agent_op_id,
                "op_type": "agent_llm",
                "component": "agent",
                "model": str(loop.agent_config.llm_model or "deepseek/deepseek-chat"),
                "status": "ok",
                "input_chars": int(agent_debug.get("input_chars") or 0),
                "input_tokens_est": int(agent_debug.get("input_tokens_est") or 0),
                "response_ref": agent_response_raw_id,
            },
            refs=[agent_response_raw_id],
        )
    )
    loop._emit(
        event="agentic_llm_agent_response",
        cycle_index=cycle_index,
        action_id=action_id,
        payload=agent_output,
        raw_event_id=agent_response_raw_id,
    )


def _run_agentic_search_loop(loop: _AgenticSearchLoop) -> Path:
    if not loop.searxng_url:
        loop.run_state.status = "failed"
        loop.run_state.stop_reason = "missing_env:SEARXNG_URL"
        loop.run_result["status"] = "failed"
        loop.run_result["stop_reason"] = str(loop.run_state.stop_reason or "")
        loop._write_trajectory(
            status=str(loop.run_state.status or "failed"),
            stop_reason=str(loop.run_state.stop_reason or ""),
        )
        loop._write_result()
        _cleanup_agentic_artifacts(loop.paths)
        loop._emit(event="agentic_failed", reason=str(loop.run_state.stop_reason or ""))
        return loop.paths["result"]

    loop.paths["raw"].parent.mkdir(parents=True, exist_ok=True)
    loop.paths["raw"].write_text("", encoding="utf-8")
    loop._write_trajectory(status="running", stop_reason="")
    loop._write_result()

    try:
        for cycle_index in range(1, loop.max_cycles + 1):
            if _run_agentic_cycle(loop, cycle_index):
                break

        if not str(loop.run_state.stop_reason or ""):
            loop.run_state.stop_reason = "max_cycles_reached"

        loop.run_state.status = "completed"
        loop.run_result["status"] = "completed"
        loop.run_result["stop_reason"] = str(loop.run_state.stop_reason or "")
        loop.run_result["cycle_count"] = int(loop.run_state.cycle_index or 0)
        loop._emit(
            event="agentic_complete",
            status="completed",
            cycle_count=int(loop.run_state.cycle_index or 0),
            stop_reason=str(loop.run_state.stop_reason or ""),
            final_candidates=len(_paper_final_candidates(loop)),
        )
    except KeyboardInterrupt:
        loop.run_state.stop_reason = "interrupted:keyboard"
        loop.run_state.status = "failed"
        loop.run_result["status"] = "failed"
        loop.run_result["stop_reason"] = str(loop.run_state.stop_reason or "")
        loop.run_result["cycle_count"] = int(loop.run_state.cycle_index or 0)
        loop._emit(event="agentic_failed", reason=str(loop.run_state.stop_reason or ""))
    except Exception as exc:
        loop.run_state.stop_reason = f"agentic_error:{type(exc).__name__}:{exc}"
        loop.run_state.status = "failed"
        loop.run_result["status"] = "failed"
        loop.run_result["stop_reason"] = str(loop.run_state.stop_reason or "")
        loop.run_result["cycle_count"] = int(loop.run_state.cycle_index or 0)
        loop._emit(event="agentic_failed", reason=str(loop.run_state.stop_reason or ""))

    loop._write_trajectory(
        status=str(loop.run_state.status or "running"),
        stop_reason=str(loop.run_result.get("stop_reason") or loop.run_state.stop_reason or ""),
    )
    loop._write_result()
    _cleanup_agentic_artifacts(loop.paths)
    return loop.paths["result"]


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
