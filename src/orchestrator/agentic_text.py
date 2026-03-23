from __future__ import annotations

import hashlib
import html as html_lib
import re
from typing import Any
from urllib.parse import urljoin, urlparse

NON_VENUE_ACRONYMS = {
    "ACM",
    "IEEE",
    "USENIX",
    "DOI",
    "ARXIV",
    "LLM",
    "NLP",
    "GPU",
    "CPU",
}

LISTING_TITLE_TOKENS = {
    "accepted papers",
    "program",
    "proceedings",
    "technical sessions",
    "technical program",
}

LISTING_HEADING_PHRASES = {
    "list of accepted papers",
    "accepted papers list",
    "proceedings of the",
    "technical sessions",
    "technical program",
    "conference program",
    "program schedule",
}

LISTING_URL_TOKENS = {
    "/accepted",
    "/accepted-papers",
    "/program",
    "/technical-sessions",
    "/proceedings",
    "/conference/",
}

DETAIL_URL_TOKENS = {
    "/doi/",
    "/abs/",
    "/pdf/",
    "/presentation/",
}

PAPER_SIGNAL_TOKENS = {
    "paper",
    "papers",
    "proceedings",
    "accepted",
    "conference",
    "symposium",
    "workshop",
    "technical sessions",
    "program",
    "journal",
    "preprint",
}

NON_PAPER_TITLE_TOKENS = {
    "keynote",
    "opening remarks",
    "closing remarks",
    "call for papers",
    "registration",
    "sponsor",
    "committee",
    "cfp",
    "tutorial",
    "panel",
}


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


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


def _unique_queries(items: list[str], limit: int) -> list[str]:
    return _unique_nonempty(items, limit)


def _host_from_url(url: str) -> str:
    try:
        return (urlparse(str(url or "")).netloc or "").lower()
    except Exception:
        return ""


def _read_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _peek_text(text: str, limit: int = 220) -> str:
    raw = str(text or "").strip()
    if len(raw) <= limit:
        return raw
    return raw[: max(0, limit - 3)] + "..."


def _is_listing_page(*, title: str, url: str) -> bool:
    lowered_title = str(title or "").lower()
    lowered_url = str(url or "").lower()
    if any(token in lowered_title for token in LISTING_TITLE_TOKENS):
        return True
    return any(token in lowered_url for token in LISTING_URL_TOKENS)


def _is_detail_page(*, title: str, url: str) -> bool:
    lowered_url = str(url or "").lower()
    return any(token in lowered_url for token in DETAIL_URL_TOKENS)


def _estimate_text_tokens(text: str) -> int:
    content = str(text or "")
    if not content:
        return 1
    cjk_chars = sum(1 for ch in content if "\u4e00" <= ch <= "\u9fff")
    other_chars = max(0, len(content) - cjk_chars)
    estimated = (cjk_chars * 0.6) + (other_chars * 0.3)
    return max(1, int(estimated + 0.999))


def _clean_text(raw: str, *, limit_chars: int) -> str:
    text = str(raw or "")
    text = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", text)
    text = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;|&#160;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit_chars:
        return text
    return text[: max(0, limit_chars - 3)] + "..."


def _clean_block_text(raw: str, *, limit_chars: int) -> str:
    text = str(raw or "")
    if not text:
        return ""
    text = re.sub(r"(?is)<script[^>]*>.*?</script>", "\n", text)
    text = re.sub(r"(?is)<style[^>]*>.*?</style>", "\n", text)
    text = re.sub(r"(?i)<br\\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(?:p|div|li|tr|article|section|h[1-6]|ul|ol|table|thead|tbody|tfoot)>", "\n", text)
    text = re.sub(r"(?i)<(?:p|div|li|tr|article|section|h[1-6]|ul|ol|table|thead|tbody|tfoot)[^>]*>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html_lib.unescape(text)
    text = text.replace("\u00a0", " ")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t\f\v]+", " ", line).strip() for line in text.split("\n")]
    out_lines: list[str] = []
    blank = False
    for line in lines:
        if not line:
            if out_lines and not blank:
                out_lines.append("")
            blank = True
            continue
        out_lines.append(line)
        blank = False
    cleaned = "\n".join(out_lines).strip()
    if len(cleaned) <= limit_chars:
        return cleaned
    return cleaned[: max(0, limit_chars - 3)] + "..."


def _extract_main_text_from_html(raw_html: str, *, max_chars: int) -> str:
    try:
        import trafilatura  # type: ignore

        extract_kwargs = {
            "include_comments": False,
            "include_tables": True,
            "include_formatting": True,
            "favor_recall": True,
            "output_format": "markdown",
        }
        try:
            extracted = trafilatura.extract(raw_html, **extract_kwargs)
        except TypeError:
            extract_kwargs.pop("output_format", None)
            extracted = trafilatura.extract(raw_html, **extract_kwargs)
        if extracted:
            return _clean_block_text(extracted, limit_chars=max_chars)
    except Exception:
        pass
    return _clean_block_text(raw_html, limit_chars=max_chars)


def _extract_listing_text_with_fallback(raw_html: str, *, max_chars: int) -> str:
    main_text = _extract_main_text_from_html(raw_html, max_chars=max_chars)
    raw_text = _clean_block_text(raw_html, limit_chars=max_chars)
    if len(main_text) < max(1200, int(len(raw_text) * 0.45)):
        return raw_text
    return main_text


def _extract_html_structural_segments(raw_html: str, *, max_segments: int = 160, max_chars: int = 1800) -> list[str]:
    if not raw_html:
        return []
    segments: list[str] = []
    seen: set[str] = set()
    patterns = [
        r"(?is)<article[^>]*>(.*?)</article>",
        r"(?is)<li[^>]*>(.*?)</li>",
        r"(?is)<tr[^>]*>(.*?)</tr>",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, raw_html):
            frag = _clean_text(match.group(1), limit_chars=max_chars)
            if not frag or len(frag) < 80:
                continue
            lowered = frag.lower()
            if any(token in lowered for token in ("cookie", "privacy policy", "copyright", "all rights reserved")):
                continue
            key = hashlib.sha1(lowered.encode("utf-8")).hexdigest()[:16]
            if key in seen:
                continue
            seen.add(key)
            segments.append(frag)
            if len(segments) >= max_segments:
                return segments
    if segments:
        return segments
    lines = [part.strip() for part in re.split(r"\n+|(?<=\.)\s+", _clean_text(raw_html, limit_chars=2_000_000)) if part.strip()]
    chunk: list[str] = []
    for line in lines:
        chunk.append(line)
        if len(" ".join(chunk)) >= max_chars:
            joined = _clean_text(" ".join(chunk), limit_chars=max_chars)
            if joined:
                segments.append(joined)
            chunk = []
            if len(segments) >= max_segments:
                break
    if chunk and len(segments) < max_segments:
        joined = _clean_text(" ".join(chunk), limit_chars=max_chars)
        if joined:
            segments.append(joined)
    return segments


def _extract_text_segments(text: str, *, max_chars: int = 1800) -> list[str]:
    if not text:
        return []
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    parts: list[str] = []
    for block in re.split(r"\n{2,}", normalized):
        block = block.strip()
        if not block:
            continue
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        current: list[str] = []
        for line in lines:
            is_bullet = bool(re.match(r"^(?:[-*+]\s+|\d+\.\s+|#+\s+)", line))
            candidate = "\n".join(current + [line]) if current else line
            if current and (is_bullet or len(candidate) > max_chars):
                joined = _clean_text("\n".join(current), limit_chars=max_chars)
                if joined:
                    parts.append(joined)
                current = [line]
            else:
                current.append(line)
        if current:
            joined = _clean_text("\n".join(current), limit_chars=max_chars)
            if joined:
                parts.append(joined)
    if not parts:
        parts = [normalized]
    compact: list[str] = []
    for part in parts:
        if len(part) <= max_chars:
            compact.append(part)
            continue
        sentences = [seg.strip() for seg in re.split(r"(?<=[.!?])\s+", part) if seg.strip()]
        chunk: list[str] = []
        for sentence in sentences:
            candidate = " ".join(chunk + [sentence]) if chunk else sentence
            if chunk and len(candidate) > max_chars:
                compact.append(_clean_text(" ".join(chunk), limit_chars=max_chars))
                chunk = [sentence]
            else:
                chunk.append(sentence)
        if chunk:
            compact.append(_clean_text(" ".join(chunk), limit_chars=max_chars))
    seen: set[str] = set()
    deduped: list[str] = []
    for part in compact:
        cleaned = _clean_text(part, limit_chars=max_chars)
        if not cleaned or len(cleaned) < 40:
            continue
        key = hashlib.sha1(cleaned.lower().encode("utf-8")).hexdigest()[:16]
        if key in seen:
            continue
        seen.add(key)
        deduped.append(cleaned)
    return deduped


def _normalize_anchor_terms(value: Any) -> list[str]:
    items: list[str] = []
    if isinstance(value, list):
        items = [str(v).strip() for v in value if str(v).strip()]
    elif isinstance(value, str):
        items = [part.strip() for part in re.split(r"[,\n;|]", value) if part.strip()]
    return _unique_nonempty(items, limit=24)


def _resolve_extract_anchor_terms(*, params: dict, filters: dict, intent: dict, user_prompt: str) -> list[str]:
    terms = _normalize_anchor_terms(params.get("anchor_terms"))
    if not terms:
        terms = _normalize_anchor_terms(intent.get("anchor_terms"))
    if terms:
        return terms

    generated: list[str] = []
    for key in ("institution", "institution_contains", "author", "author_contains", "venue", "topic"):
        raw = str(filters.get(key) or "").strip()
        if raw:
            generated.append(raw)
    return _unique_nonempty(generated, limit=24)


def _anchor_segments_for_filters(
    segments: list[str],
    filters: dict,
    *,
    anchor_terms: list[str] | None = None,
    radius: int = 1,
) -> list[str]:
    cleaned_segments = [str(seg) for seg in segments if str(seg).strip()]
    if not cleaned_segments:
        return []
    lowered_terms = [term.lower() for term in _normalize_anchor_terms(anchor_terms or []) if term]
    year_gte = _safe_int(filters.get("year_gte"))
    if not lowered_terms and year_gte is None:
        return cleaned_segments

    hit_indexes: list[int] = []
    for idx, segment in enumerate(cleaned_segments):
        lowered = segment.lower()
        matched = any(term in lowered for term in lowered_terms)
        if not matched and year_gte is not None:
            years = [int(v) for v in re.findall(r"\b(?:19|20)\d{2}\b", lowered)]
            matched = any(v >= year_gte for v in years)
        if matched:
            hit_indexes.append(idx)
    if not hit_indexes:
        return cleaned_segments

    selected_indexes: list[int] = []
    seen: set[int] = set()
    for idx in hit_indexes:
        for pos in range(max(0, idx - radius), min(len(cleaned_segments), idx + radius + 1)):
            if pos in seen:
                continue
            seen.add(pos)
            selected_indexes.append(pos)
    return [cleaned_segments[idx] for idx in selected_indexes]


def _discover_pagination_urls(raw_html: str, base_url: str, *, max_extra_pages: int = 4) -> list[str]:
    base_host = _host_from_url(base_url)
    anchors = re.findall(r'(?is)<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', raw_html)
    candidates: list[str] = []
    for href, label in anchors:
        resolved = urljoin(base_url, str(href or "").strip())
        if not resolved:
            continue
        if _host_from_url(resolved) != base_host:
            continue
        lowered_url = resolved.lower()
        lowered_label = _clean_text(str(label or ""), limit_chars=120).lower()
        if resolved.rstrip("/") == base_url.rstrip("/"):
            continue
        is_paged = bool(
            re.search(r"(?:[?&](?:page|p|start|offset)=\d+)|(?:/page/\d+)|(?:/p/\d+)|(?:-page-\d+)", lowered_url)
        )
        is_next = ("next" in lowered_label) or ("older" in lowered_label) or ("next" in lowered_url)
        if is_paged or is_next:
            candidates.append(resolved)
    return _unique_queries(candidates, max_extra_pages)


def _resolve_active_extract_filters(filters: dict, must_match: dict, user_prompt: str) -> dict:
    active_filters = dict(filters)
    if not str(active_filters.get("institution") or "").strip():
        inst_any = must_match.get("institution_any")
        if isinstance(inst_any, list) and inst_any:
            active_filters["institution"] = str(inst_any[0])
    if not str(active_filters.get("author") or "").strip():
        author_any = must_match.get("author_any")
        if isinstance(author_any, list) and author_any:
            active_filters["author"] = str(author_any[0])
    if _safe_int(active_filters.get("year_gte")) is None:
        year_hint = _safe_int(must_match.get("year_gte"))
        if year_hint is not None:
            active_filters["year_gte"] = year_hint
    if not str(active_filters.get("topic") or "").strip():
        active_filters["topic"] = user_prompt
    return active_filters


def _prepare_extract_segments(
    *,
    row: dict,
    filters: dict,
    anchor_terms: list[str],
) -> tuple[list[str], str]:
    segments = [str(v) for v in (row.get("segments") or []) if str(v).strip()]
    if not segments:
        segments = _extract_text_segments(str(row.get("text") or ""), max_chars=1800)
    page_is_listing = _is_listing_page(title=str(row.get("url_title") or ""), url=str(row.get("url") or ""))
    batch_mode = "page" if page_is_listing else "segments"
    ranked_segments = _anchor_segments_for_filters(segments, filters, anchor_terms=anchor_terms, radius=1)
    return ranked_segments, batch_mode
