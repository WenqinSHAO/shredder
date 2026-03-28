from __future__ import annotations

import re
from io import BytesIO
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse, urlunparse
from urllib.request import Request, urlopen

from src.orchestrator.agentic_extract_prepare import extract_target_filters
from src.orchestrator.agentic_text import (
    _clean_text,
    _discover_pagination_urls,
    _extract_listing_text_with_fallback,
    _extract_main_text_from_html,
    _extract_text_segments,
    _is_listing_page,
    _peek_text,
    _unique_nonempty,
)


def _read_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def decode_bytes(raw: Any) -> str:
    if isinstance(raw, str):
        return raw
    if not isinstance(raw, (bytes, bytearray)):
        return str(raw or "")
    try:
        return raw.decode("utf-8", errors="ignore")
    except Exception:
        try:
            return raw.decode("latin-1", errors="ignore")
        except Exception:
            return str(raw or "")


def extract_text_from_pdf_bytes(raw: bytes, *, max_chars: int) -> str:
    try:
        import pypdf  # type: ignore

        reader = pypdf.PdfReader(BytesIO(raw))
        parts: list[str] = []
        for page in reader.pages:
            text = page.extract_text() or ""
            if text:
                parts.append(text)
            if sum(len(part) for part in parts) >= max_chars:
                break
        if parts:
            return _clean_text("\n".join(parts), limit_chars=max_chars)
    except Exception:
        pass
    return _clean_text(decode_bytes(raw), limit_chars=max_chars)


def fetch_url_raw(url: str, *, timeout_s: float, max_bytes: int) -> tuple[bytes, str]:
    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; ShredderAgentic/0.1; +https://example.org)",
            "Accept": "text/html,application/xhtml+xml,application/xml,text/plain;q=0.9,*/*;q=0.8",
        },
    )
    with urlopen(req, timeout=timeout_s) as response:
        content_type = str(response.headers.get("Content-Type") or "").lower()
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
    return raw, content_type


def safe_name(value: str, default: str = "item") -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip()).strip("-")
    return text or default


def fetch_retry_urls(url: str) -> list[str]:
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
    return list(dict.fromkeys(out))[:3]


def save_raw_fetch(
    paths: dict[str, Path],
    *,
    cycle_index: int,
    target_id: str,
    url: str,
    raw: Any,
    content_type: str,
) -> str:
    base = paths["result"].parent / "fetch_raw"
    base.mkdir(parents=True, exist_ok=True)
    url_tail = safe_name((urlparse(url).path or "").split("/")[-1], default="page")
    ext = ".bin"
    lowered_type = str(content_type or "").lower()
    if "html" in lowered_type:
        ext = ".html"
    elif "pdf" in lowered_type:
        ext = ".pdf"
    elif "json" in lowered_type:
        ext = ".json"
    fname = f"cycle{cycle_index:02d}-{safe_name(target_id)}-{url_tail}{ext}"
    path = base / fname
    if isinstance(raw, str):
        data = raw.encode("utf-8", errors="ignore")
    elif isinstance(raw, bytes):
        data = raw
    else:
        data = str(raw or "").encode("utf-8", errors="ignore")
    path.write_bytes(data)
    return str(path.relative_to(paths["result"].parent))


def build_extraction_windows(
    text: str,
    filters: dict,
    *,
    max_windows: int = 6,
    radius: int = 2,
    max_chars: int = 1400,
) -> list[str]:
    if not text:
        return []
    lines = [part.strip() for part in re.split(r"\n+|(?<=\.)\s+", text) if part.strip()]
    if not lines:
        return []

    terms: list[str] = []
    institution_terms: list[str] = []
    for key in ("institution", "institution_contains", "author", "author_contains", "topic"):
        raw = str(filters.get(key) or "").strip()
        if not raw:
            continue
        tokens = [tok for tok in re.findall(r"[A-Za-z0-9][A-Za-z0-9\-]{1,}", raw) if len(tok) >= 3]
        terms.extend(tokens)
        if key.startswith("institution"):
            institution_terms.extend(tokens)
    terms.extend(["doi", "arxiv", "accepted", "proceedings", "conference", "paper", "papers"])
    terms_l = {term.lower() for term in terms}
    institution_terms_l = {term.lower() for term in institution_terms}
    year_gte = _safe_int(filters.get("year_gte"))

    scored_hits: list[tuple[int, int]] = []
    for idx, line in enumerate(lines):
        lowered = line.lower()
        score = 0
        if institution_terms_l and any(term in lowered for term in institution_terms_l):
            score += 5
        if any(term in lowered for term in terms_l):
            score += 2
        if year_gte is not None:
            years = [int(value) for value in re.findall(r"\b(?:19|20)\d{2}\b", lowered)]
            if any(value >= year_gte for value in years):
                score += 1
        if score > 0:
            scored_hits.append((idx, score))
    if not scored_hits:
        scored_hits = [(idx, 1) for idx in range(min(len(lines), max_windows))]

    windows: list[str] = []
    seen: set[str] = set()
    selected_indexes: list[int] = []

    total = len(lines)
    if total > 0:
        boundaries = [0, total // 4, total // 2, (3 * total) // 4, total]
        for i in range(4):
            lo, hi = boundaries[i], boundaries[i + 1]
            segment_hits = [(idx, score) for idx, score in scored_hits if lo <= idx < hi]
            if not segment_hits:
                continue
            best = sorted(segment_hits, key=lambda item: (item[1], -item[0]), reverse=True)[0]
            selected_indexes.append(best[0])

    for idx, _score in sorted(scored_hits, key=lambda item: (item[1], -item[0]), reverse=True):
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


def fetch_target_record(
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
    deps: dict[str, Any] | None = None,
) -> dict:
    deps = deps or {}
    read_int_fn = deps.get("read_int_fn", _read_int)
    fetch_url_raw_fn = deps.get("fetch_url_raw_fn", fetch_url_raw)
    fetch_retry_urls_fn = deps.get("fetch_retry_urls_fn", fetch_retry_urls)
    save_raw_fetch_fn = deps.get("save_raw_fetch_fn", save_raw_fetch)
    extract_text_from_pdf_bytes_fn = deps.get("extract_text_from_pdf_bytes_fn", extract_text_from_pdf_bytes)
    decode_bytes_fn = deps.get("decode_bytes_fn", decode_bytes)
    build_extraction_windows_fn = deps.get("build_extraction_windows_fn", build_extraction_windows)
    clean_text_fn = deps.get("clean_text_fn", _clean_text)
    discover_pagination_urls_fn = deps.get("discover_pagination_urls_fn", _discover_pagination_urls)
    extract_listing_text_with_fallback_fn = deps.get(
        "extract_listing_text_with_fallback_fn",
        _extract_listing_text_with_fallback,
    )
    extract_main_text_from_html_fn = deps.get(
        "extract_main_text_from_html_fn",
        _extract_main_text_from_html,
    )
    extract_text_segments_fn = deps.get("extract_text_segments_fn", _extract_text_segments)
    is_listing_page_fn = deps.get("is_listing_page_fn", _is_listing_page)

    url = str(target.get("url") or "").strip()
    title = str(target.get("title") or target.get("url_title") or "").strip()
    target_id = str(target.get("target_id") or f"fetch-{idx}")
    why = str(target.get("why") or "")
    if not url:
        return {
            "session_id": session_id,
            "cycle_index": cycle_index,
            "target_id": target_id,
            "requested_url": url,
            "url": "",
            "url_aliases": [],
            "url_title": title,
            "why": why,
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
                "target_id": target_id,
                "url": url,
            },
        )

    try:
        hard_limit = read_int_fn(params.get("max_bytes_hard"), 0)
        if hard_limit < 0:
            hard_limit = 0
        max_chars = max(500_000, read_int_fn(params.get("max_chars"), 8_000_000))
        fetch_url = url
        try:
            raw, content_type = fetch_url_raw_fn(fetch_url, timeout_s=timeout_s, max_bytes=hard_limit)
        except Exception:
            raw = b""
            content_type = ""
            resolved = False
            for alt_url in fetch_retry_urls_fn(url):
                try:
                    raw, content_type = fetch_url_raw_fn(alt_url, timeout_s=timeout_s, max_bytes=hard_limit)
                    fetch_url = alt_url
                    resolved = True
                    break
                except Exception:
                    continue
            if not resolved:
                raise

        raw_path = save_raw_fetch_fn(
            paths,
            cycle_index=cycle_index,
            target_id=target_id,
            url=fetch_url,
            raw=raw,
            content_type=content_type,
        )

        url_lower = str(fetch_url or "").lower()
        segments: list[str] = []
        if "pdf" in str(content_type).lower() or url_lower.endswith(".pdf"):
            text = extract_text_from_pdf_bytes_fn(raw, max_chars=max_chars)
            raw_html = ""
            segments = extract_text_segments_fn(text, max_chars=1800)
        else:
            raw_html = decode_bytes_fn(raw)
            if is_listing_page_fn(title=title, url=fetch_url):
                text = extract_listing_text_with_fallback_fn(raw_html, max_chars=max_chars)
            else:
                text = extract_main_text_from_html_fn(raw_html, max_chars=max_chars)
            segments = extract_text_segments_fn(text, max_chars=1800)

        page_urls = [fetch_url]
        if is_listing_page_fn(title=title, url=fetch_url) and raw_html:
            extra_urls = discover_pagination_urls_fn(
                raw_html,
                fetch_url,
                max_extra_pages=max(0, min(8, read_int_fn(params.get("max_extra_pages"), 6))),
            )
            for extra_url in extra_urls:
                page_op_id = new_op_id_fn("web_fetch_page") if new_op_id_fn is not None else ""
                if raw_event_fn is not None and page_op_id:
                    raw_event_fn(
                        "op_start",
                        {
                            "op_id": page_op_id,
                            "op_type": "web_fetch_page",
                            "component": "fetch_content",
                            "target_id": target_id,
                            "url": extra_url,
                            "parent_op_id": fetch_op_id,
                        },
                    )
                try:
                    extra_raw, extra_ctype = fetch_url_raw_fn(extra_url, timeout_s=timeout_s, max_bytes=hard_limit)
                    save_raw_fetch_fn(
                        paths,
                        cycle_index=cycle_index,
                        target_id=f"{target_id}-extra",
                        url=extra_url,
                        raw=extra_raw,
                        content_type=extra_ctype,
                    )
                    extra_raw_html = decode_bytes_fn(extra_raw)
                    if is_listing_page_fn(title=title, url=extra_url):
                        extra_text = extract_listing_text_with_fallback_fn(extra_raw_html, max_chars=max_chars)
                    else:
                        extra_text = extract_main_text_from_html_fn(extra_raw_html, max_chars=max_chars)
                    if extra_text:
                        text = f"{text}\n\n{extra_text}" if text else extra_text
                        page_urls.append(extra_url)
                    extra_segments = extract_text_segments_fn(extra_text, max_chars=1800)
                    if extra_segments:
                        segments.extend(extra_segments)
                    if raw_event_fn is not None and page_op_id:
                        raw_event_fn(
                            "op_end",
                            {
                                "op_id": page_op_id,
                                "op_type": "web_fetch_page",
                                "component": "fetch_content",
                                "target_id": target_id,
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
                                "target_id": target_id,
                                "url": extra_url,
                                "parent_op_id": fetch_op_id,
                                "status": "error",
                            },
                        )
                    continue

        text = clean_text_fn(text, limit_chars=max_chars)
        if not segments:
            segments = build_extraction_windows_fn(
                text,
                extract_target_filters(target),
                max_windows=30,
                radius=2,
                max_chars=1200,
            )
        selected_segments = [str(segment) for segment in segments if str(segment).strip()]
        output = {
            "session_id": session_id,
            "cycle_index": cycle_index,
            "target_id": target_id,
            "requested_url": url,
            "url": fetch_url,
            "url_aliases": _unique_nonempty([url, fetch_url, *page_urls], limit=16),
            "url_title": title,
            "why": why,
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
                    "target_id": target_id,
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
            "target_id": target_id,
            "requested_url": url,
            "url": url,
            "url_aliases": _unique_nonempty([url], limit=4),
            "url_title": title,
            "why": why,
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
                    "target_id": target_id,
                    "url": url,
                    "status": "error",
                    "error": f"{type(exc).__name__}:{exc}",
                },
            )
        return output
