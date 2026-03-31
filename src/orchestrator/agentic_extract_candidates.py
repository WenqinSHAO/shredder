from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin, urlparse, urlunparse

from src.connectors.http import normalize_arxiv_id, normalize_doi
from src.orchestrator.agentic_search import (
    _as_list,
    _normalize_title_for_key,
    _peek_text,
    _rank_candidates,
    _strip_listing_author_tail,
)
from src.orchestrator.agentic_text import (
    NON_PAPER_TITLE_TOKENS,
    _clean_text,
    _safe_int,
    _unique_nonempty,
)


def _listify_text_items(value: Any) -> list[str]:
    if isinstance(value, list):
        items: list[str] = []
        for entry in value:
            text = str(entry or "").strip()
            if text:
                items.append(text)
        return items
    text = str(value or "").strip()
    if not text:
        return []
    if ";" in text:
        return [part.strip() for part in text.split(";") if part.strip()]
    if "\n" in text:
        return [part.strip() for part in text.splitlines() if part.strip()]
    return [text]


def normalize_author_affiliations(
    author_affiliations_raw: Any,
    *,
    authors_raw: Any,
    affiliations_raw: Any,
) -> list[dict[str, str]]:
    if isinstance(author_affiliations_raw, list):
        pairs: list[dict[str, str]] = []
        for entry in author_affiliations_raw:
            if not isinstance(entry, dict):
                continue
            author = str(entry.get("author") or entry.get("name") or entry.get("author_name") or "").strip()
            affiliation = str(entry.get("affiliation") or entry.get("institution") or "").strip()
            if author:
                pairs.append({"author": author, "affiliation": affiliation})
        if pairs:
            return pairs

    authors = _listify_text_items(authors_raw)
    affiliations = _listify_text_items(affiliations_raw)
    if not authors:
        return []
    if len(affiliations) == len(authors):
        return [{"author": author, "affiliation": affiliation} for author, affiliation in zip(authors, affiliations)]
    if len(affiliations) == 1:
        return [{"author": author, "affiliation": affiliations[0]} for author in authors]
    return [{"author": author, "affiliation": ""} for author in authors]


def format_author_affiliations(author_affiliations_raw: Any, *, authors_raw: Any, affiliations_raw: Any) -> str:
    pairs = normalize_author_affiliations(
        author_affiliations_raw,
        authors_raw=authors_raw,
        affiliations_raw=affiliations_raw,
    )
    formatted: list[str] = []
    for pair in pairs:
        author = str(pair.get("author") or "").strip()
        affiliation = str(pair.get("affiliation") or "").strip()
        if not author:
            continue
        formatted.append(f"{author} ({affiliation})" if affiliation else author)
    return "; ".join(formatted)


def extract_year_best(text: str, *, year_gte: int | None = None) -> str:
    years = [int(y) for y in re.findall(r"\b(?:19|20)\d{2}\b", str(text or ""))]
    if not years:
        return ""
    if year_gte is not None:
        scoped = [y for y in years if y >= year_gte]
        if scoped:
            return str(max(scoped))
    return str(max(years))


def infer_paper_title(text: str, fallback: str) -> str:
    cleaned = _clean_text(text, limit_chars=2000)
    sentences = [part.strip(" .") for part in re.split(r"\.\s+", cleaned) if part.strip()]
    for sentence in sentences:
        lowered = sentence.lower()
        if "accepted papers" in lowered or "proceedings" in lowered:
            continue
        if len(sentence.split()) < 4:
            continue
        if ":" in sentence or any(token in lowered for token in ("network", "model", "training", "system", "inference")):
            return _peek_text(sentence, 180)
    return str(fallback or "").strip()


def looks_like_paper_candidate(title: str, evidence: str, url: str) -> bool:
    title_l = str(title or "").lower()
    evidence_l = str(evidence or "").lower()
    _ = str(url or "").lower()
    page_level_tokens = (
        "accepted papers",
        "list of accepted papers",
        "proceedings",
        "technical sessions",
        "technical program",
        "program page",
    )
    if not title_l:
        return False
    if any(token in title_l for token in NON_PAPER_TITLE_TOKENS):
        return False
    if any(token in title_l for token in page_level_tokens):
        return False
    if len(title_l.split()) < 4:
        return False
    if re.fullmatch(r"[a-z0-9\-]{2,15}", title_l):
        return False
    if any(token in evidence_l for token in NON_PAPER_TITLE_TOKENS):
        return False
    if any(token in evidence_l for token in page_level_tokens) and title_l not in evidence_l:
        return False
    return True


def is_authorish_title_fragment(title: str) -> bool:
    text = str(title or "").strip()
    if not text:
        return True
    lowered = text.lower()
    if re.search(r"\b(university|institute|institutes|laboratory|laboratories|academy|academies|school of|department of)\b", lowered) and ":" not in text:
        return True
    comma_count = text.count(",")
    semicolon_count = text.count(";")
    if semicolon_count > 0:
        return True
    if comma_count >= 3 and ":" not in text:
        return True
    if "cloud" in lowered and ":" not in text and len(text.split()) <= 10:
        return True
    if re.fullmatch(r"[A-Z][a-z'\-]+(?:\s+[A-Z][a-z'\-]+){0,6}", text):
        return True
    return False


def _extract_doi(text: str) -> str:
    match = re.search(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+\b", text)
    return normalize_doi(match.group(0)) if match else ""


def _extract_arxiv(text: str) -> str:
    match = re.search(r"\b(?:arxiv:)?(\d{4}\.\d{4,5})(?:v\d+)?\b", text, flags=re.IGNORECASE)
    return normalize_arxiv_id(match.group(1)) if match else ""


def extract_listing_candidates_from_segments(
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
    def _value_terms(raw: str) -> list[str]:
        return [tok for tok in re.findall(r"[A-Za-z0-9][A-Za-z0-9\\-]{2,}", str(raw or "").lower()) if len(tok) >= 3][:8]

    def _matches_filters_in_text(text: str, filters: dict) -> bool:
        lowered = str(text or "").lower()
        field_specs = (
            ("institution", 1),
            ("institution_contains", 1),
            ("author", 1),
            ("author_contains", 1),
            ("topic", 0),
            ("venue", 0),
        )
        for field, min_hits in field_specs:
            raw = str(filters.get(field) or "").strip()
            if not raw:
                continue
            terms = _value_terms(raw)
            if not terms:
                continue
            hits = sum(1 for term in terms if term in lowered)
            if hits < max(1, min_hits):
                return False
            for term in terms:
                if re.search(rf"\\b(?:no|not|without|non)\\s+{re.escape(term)}\\b", lowered):
                    return False
        return True

    out: list[dict] = []
    for seg in segments:
        text = _clean_text(str(seg or ""), limit_chars=1800)
        if len(text) < 40:
            continue
        if any(token in text.lower() for token in NON_PAPER_TITLE_TOKENS):
            continue
        title_patterns = [
            re.compile(
                r"([A-Z][A-Za-z0-9'`:;,/()\-\s]{15,220}?)\s+[A-Z][A-Za-z0-9'\-]+\s*\(",
                flags=re.MULTILINE,
            ),
            re.compile(
                r"([A-Z][A-Za-z0-9'`\-/()\s]{3,80}:\s*[A-Za-z0-9'`\-/(),\s]{8,200}?)\s+[A-Z][A-Za-z0-9'\-]+\s*\(",
                flags=re.MULTILINE,
            ),
        ]
        seen_spans: set[tuple[int, int]] = set()
        matches: list[re.Match[str]] = []
        for pat in title_patterns:
            for m in pat.finditer(text):
                span = (m.start(1), m.end(1))
                if span in seen_spans:
                    continue
                seen_spans.add(span)
                matches.append(m)
        if not matches:
            matches = [re.search(r"(.{20,240})", text)] if text else []
        for m in matches:
            if m is None:
                continue
            title_raw = _peek_text(str(m.group(1) or "").strip(" -:;"), 240).strip()
            title = _strip_listing_author_tail(title_raw)
            if not title:
                continue
            if title.lower().startswith("abstract:"):
                continue
            if is_authorish_title_fragment(title):
                continue
            start = max(0, m.start(1))
            end = min(len(text), m.end() + 240)
            context = text[start:end]
            if not _matches_filters_in_text(context, filters):
                continue
            if not looks_like_paper_candidate(title, context, url):
                continue
            year = extract_year_best(context, year_gte=_safe_int(filters.get("year_gte"))) or fallback_year
            out.append(
                {
                    "session_id": session_id,
                    "cycle_index": cycle_index,
                    "target_id": target_id,
                    "url": url,
                    "url_title": url_title,
                    "paper_title": title,
                    "doi": _extract_doi(context),
                    "arxiv_id": _extract_arxiv(context),
                    "year": str(year or ""),
                    "filters": dict(filters),
                    "evidence": _peek_text(context, 320),
                    "score": 0.72,
                    "status": "ok",
                    "extract_source": "listing_deterministic",
                }
            )
    dedup: dict[str, dict] = {}
    for item in out:
        key = f"{_normalize_title_for_key(str(item.get('paper_title') or ''))}:{str(item.get('year') or '')}"
        if not key:
            continue
        prev = dedup.get(key)
        if prev is None or float(item.get("score") or 0.0) > float(prev.get("score") or 0.0):
            dedup[key] = item
    return list(dedup.values())


def to_paper_candidates_from_facts(
    facts: list[dict],
    *,
    canonicalize_candidate_title_fn: Callable[[dict], str],
) -> list[dict]:
    def _tokenize_filter(value: str) -> list[str]:
        return [tok for tok in re.findall(r"[A-Za-z0-9][A-Za-z0-9\\-]{2,}", str(value or "").lower()) if len(tok) >= 3][:10]

    def _normalize_filter_phrases(value: Any) -> list[str]:
        if isinstance(value, list):
            items = value
        else:
            items = [value]
        phrases: list[str] = []
        for raw in items:
            text = re.sub(r"\s+", " ", str(raw or "").strip().lower())
            if text:
                phrases.append(text)
        return _unique_nonempty(phrases, limit=12)

    def _structured_match_text(fact: dict) -> str:
        llm_extract = fact.get("llm_extract") if isinstance(fact.get("llm_extract"), dict) else {}
        parts = [
            str(fact.get("paper_title") or ""),
            str(fact.get("url_title") or ""),
            str(fact.get("venue") or ""),
            str(fact.get("url") or ""),
            str(llm_extract.get("venue_hint") or ""),
            str(llm_extract.get("abstract_snippet") or ""),
        ]
        for key in ("authors", "affiliations", "institution_hits"):
            value = llm_extract.get(key)
            if isinstance(value, list):
                parts.extend(str(v).strip() for v in value if str(v).strip())
            else:
                parts.append(str(value or ""))
        for key in ("institution_match", "author_match"):
            parts.append(str(llm_extract.get(key) or ""))
        return " ".join(parts).lower()

    def _local_evidence_text(fact: dict) -> str:
        title = str(fact.get("paper_title") or "").strip().lower()
        evidence = str(fact.get("evidence") or "").strip().lower()

        def _first_two_sentences(text: str) -> str:
            parts = [seg.strip() for seg in re.split(r"(?<=[.!?])\s+", text) if seg.strip()]
            return " ".join(parts[:2])

        if not evidence:
            return ""
        if not title:
            return _first_two_sentences(evidence)
        anchor = evidence.find(title)
        if anchor < 0:
            return _first_two_sentences(evidence)
        return _first_two_sentences(evidence[anchor:])

    def _subject_filters(fact: dict) -> tuple[list[str], list[str], list[str], list[str], list[str], int | None]:
        filters = fact.get("filters") if isinstance(fact.get("filters"), dict) else {}
        intent = fact.get("extract_intent") if isinstance(fact.get("extract_intent"), dict) else {}
        must_match = intent.get("must_match") if isinstance(intent.get("must_match"), dict) else {}
        institution_terms: list[str] = []
        institution_phrases: list[str] = []
        author_terms: list[str] = []
        author_phrases: list[str] = []
        venue_terms: list[str] = []
        for key in ("institution", "institution_contains"):
            raw = str(filters.get(key) or "").strip()
            if raw:
                institution_phrases.extend(_normalize_filter_phrases(raw))
                institution_terms.extend(_tokenize_filter(raw))
        if not institution_terms and not institution_phrases:
            institution_any = _as_list(must_match.get("institution_any"))
            institution_phrases.extend(_normalize_filter_phrases(institution_any))
            institution_terms.extend(_tokenize_filter(" ".join(institution_any)))
        for key in ("author", "author_contains"):
            raw = str(filters.get(key) or "").strip()
            if raw:
                author_phrases.extend(_normalize_filter_phrases(raw))
                author_terms.extend(_tokenize_filter(raw))
        if not author_terms:
            author_any = _as_list(must_match.get("author_any"))
            author_phrases.extend(_normalize_filter_phrases(author_any))
            author_terms.extend(_tokenize_filter(" ".join(author_any)))
        for key in ("venue", "venue_contains"):
            raw = str(filters.get(key) or "").strip()
            if raw:
                venue_terms.extend(_tokenize_filter(raw))
        if not venue_terms:
            venue_terms.extend(_tokenize_filter(" ".join(_as_list(must_match.get("venue_any")))))
        year_gte = _safe_int(filters.get("year_gte"))
        if year_gte is None:
            year_gte = _safe_int(must_match.get("year_gte"))
        return (
            _unique_nonempty(institution_terms, limit=16),
            _unique_nonempty(institution_phrases, limit=12),
            _unique_nonempty(author_terms, limit=16),
            _unique_nonempty(author_phrases, limit=12),
            _unique_nonempty(venue_terms, limit=16),
            year_gte,
        )

    def _matches_filter_phrase(text: str, phrase: str) -> bool:
        normalized_text = re.sub(r"\s+", " ", str(text or "").lower())
        normalized_phrase = re.sub(r"\s+", " ", str(phrase or "").strip().lower())
        if not normalized_phrase:
            return False
        if normalized_phrase in normalized_text:
            return True
        phrase_tokens = _tokenize_filter(normalized_phrase)
        if not phrase_tokens:
            return False
        if len(phrase_tokens) == 1:
            return phrase_tokens[0] in normalized_text
        return all(token in normalized_text for token in phrase_tokens)

    def _passes_fact_filters(fact: dict) -> bool:
        llm_extract = fact.get("llm_extract")
        llm_match = ""
        if isinstance(llm_extract, dict):
            llm_match = str(llm_extract.get("match_decision") or "").strip().lower()
        if llm_match == "non_match":
            return False
        institution_terms, institution_phrases, author_terms, author_phrases, venue_terms, year_gte = _subject_filters(fact)
        if year_gte is not None:
            year = _safe_int(fact.get("year"))
            if year is None or year < year_gte:
                return False
        if llm_match == "match":
            return True
        structured = _structured_match_text(fact)
        local = _local_evidence_text(fact)
        if institution_phrases:
            if not any(
                _matches_filter_phrase(structured, phrase) or _matches_filter_phrase(local, phrase)
                for phrase in institution_phrases
            ):
                return False
        elif institution_terms and not (
            any(term in structured for term in institution_terms)
            or any(term in local for term in institution_terms)
        ):
            return False
        if author_phrases:
            if not any(
                _matches_filter_phrase(structured, phrase) or _matches_filter_phrase(local, phrase)
                for phrase in author_phrases
            ):
                return False
        elif author_terms and not (
            any(term in structured for term in author_terms)
            or any(term in local for term in author_terms)
        ):
            return False
        if venue_terms and not (
            any(term in structured for term in venue_terms)
            or any(term in local for term in venue_terms)
        ):
            return False
        return True

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

    candidates: list[dict] = []
    for fact in facts:
        if str(fact.get("status") or "") != "ok":
            continue
        if not _passes_fact_filters(fact):
            continue
        llm_extract = fact.get("llm_extract")
        title_norm = ""
        if isinstance(llm_extract, dict):
            title_norm = _strip_listing_author_tail(str(llm_extract.get("paper_title_normalized") or "").strip())
        raw_title = _strip_listing_author_tail(str(fact.get("paper_title") or "").strip())
        title = title_norm or raw_title
        title = canonicalize_candidate_title_fn({"title": title, "abstract": str(fact.get("evidence") or "")})
        if not title:
            continue
        if is_authorish_title_fragment(title):
            continue
        lowered_title = title.lower()
        if "session chair" in lowered_title or "available media" in lowered_title:
            continue
        if "|" in title and not (":" in title and len(title.split()) >= 4):
            continue
        evidence = str(fact.get("evidence") or "").strip()
        if evidence:
            page_level_tokens = ("accepted papers", "list of accepted papers", "technical sessions", "proceedings")
            if any(tok in title.lower() for tok in page_level_tokens):
                for part in [seg.strip() for seg in evidence.split("|") if seg.strip()]:
                    cand = _strip_listing_author_tail(part)
                    if ":" in cand and len(cand.split()) >= 4 and not any(tok in cand.lower() for tok in page_level_tokens):
                        title = cand
                        break
                recovered = _strip_listing_author_tail(infer_paper_title(evidence, title))
                if recovered and recovered != title:
                    title = recovered
        llm_decision = fact.get("llm_is_paper")
        if isinstance(llm_decision, bool):
            if not llm_decision:
                continue
        elif not looks_like_paper_candidate(title, evidence, str(fact.get("url") or "")):
            continue
        authors_raw = (llm_extract or {}).get("authors") if isinstance(llm_extract, dict) else ""
        affiliations_raw = (llm_extract or {}).get("affiliations") if isinstance(llm_extract, dict) else ""
        author_affiliations_raw = (llm_extract or {}).get("author_affiliations") if isinstance(llm_extract, dict) else []
        author_affiliations = normalize_author_affiliations(
            author_affiliations_raw,
            authors_raw=authors_raw,
            affiliations_raw=affiliations_raw,
        )
        authors_with_affiliations = format_author_affiliations(
            author_affiliations_raw,
            authors_raw=authors_raw,
            affiliations_raw=affiliations_raw,
        )
        abstract_text = (
            str((llm_extract or {}).get("abstract") or "").strip()
            if isinstance(llm_extract, dict)
            else ""
        )
        candidates.append(
            {
                "source": "agentic_extract",
                "source_id": str(fact.get("target_id") or ""),
                "title": title,
                "venue": "",
                "year": str(fact.get("year") or ""),
                "doi": normalize_doi(str(fact.get("doi") or "")),
                "arxiv_id": normalize_arxiv_id(str(fact.get("arxiv_id") or "")),
                "url": str(fact.get("url") or ""),
                "abstract": abstract_text or evidence,
                "abstract_snippet": (
                    _peek_text(abstract_text or str((llm_extract or {}).get("abstract_snippet") or evidence), 320)
                    if isinstance(llm_extract, dict)
                    else ""
                ),
                "authors": (
                    "; ".join(_listify_text_items(authors_raw)[:20])
                ),
                "affiliations": (
                    "; ".join(_listify_text_items(affiliations_raw)[:20])
                ) or (
                    "; ".join([str(v).strip() for v in ((llm_extract or {}).get("institution_hits") or []) if str(v).strip()][:20])
                    if isinstance((llm_extract or {}).get("institution_hits"), list)
                    else str((llm_extract or {}).get("institution_hits") or "")
                ),
                "author_affiliations": author_affiliations,
                "authors_with_affiliations": authors_with_affiliations,
                "keywords": [],
                "categories": [],
                "score": float(fact.get("score") or 0.0),
                "reason": "extract_content",
                "field_presence": dict(fact.get("field_presence") or {}),
                "missing_fields": list(fact.get("missing_fields") or []),
                "row_completeness": str(fact.get("row_completeness") or ""),
            }
        )
    ranked = _rank_candidates(candidates)
    deduped: list[dict] = []
    for cand in ranked:
        title = str(cand.get("title") or "")
        year = str(cand.get("year") or "")
        url = str(cand.get("url") or "")
        duplicate = False
        for kept in deduped:
            if str(kept.get("url") or "") != url:
                continue
            if str(kept.get("year") or "") != year:
                continue
            if _is_tail_variant_title(str(kept.get("title") or ""), title):
                duplicate = True
                break
        if not duplicate:
            deduped.append(cand)
    return _rank_candidates(deduped)


def canonicalize_discovered_url(url: str) -> str:
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme.lower() not in {"http", "https"}:
        return ""
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    if path != "/":
        path = path.rstrip("/")
    normalized = parsed._replace(
        scheme=parsed.scheme.lower(),
        netloc=parsed.netloc.lower(),
        path=path or "/",
        fragment="",
    )
    return urlunparse(normalized)


def collect_candidate_url_inputs_from_records(
    records: list[dict],
    *,
    paths: dict[str, Path],
    known_urls: list[str],
    max_links: int = 32,
) -> list[dict[str, Any]]:
    known_url_set = {
        canonicalize_discovered_url(value).lower()
        for value in known_urls
        if canonicalize_discovered_url(value)
    }
    result_parent = paths["result"].parent
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    for record in records:
        if not isinstance(record, dict):
            continue
        base_url = str(record.get("url") or "").strip()
        raw_path = str(record.get("raw_path") or "").strip()
        if not base_url or not raw_path.lower().endswith(".html"):
            continue
        raw_file = result_parent / raw_path
        if not raw_file.exists():
            continue
        raw_html = raw_file.read_text(encoding="utf-8", errors="ignore")
        if not raw_html:
            continue
        current_page_urls = {
            canonicalize_discovered_url(value).lower()
            for value in [
                str(record.get("url") or ""),
                str(record.get("requested_url") or ""),
                *[str(v) for v in (record.get("page_urls") or []) if str(v).strip()],
                *[str(v) for v in (record.get("url_aliases") or []) if str(v).strip()],
            ]
            if canonicalize_discovered_url(value)
        }
        for match in re.finditer(r'(?is)<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', raw_html):
            href = str(match.group(1) or "").strip()
            lowered_href = href.lower()
            if lowered_href.startswith(("mailto:", "javascript:", "#")):
                continue
            resolved = canonicalize_discovered_url(urljoin(base_url, href))
            if not resolved:
                continue
            lowered_url = resolved.lower()
            if lowered_url in seen or lowered_url in known_url_set or lowered_url in current_page_urls:
                continue
            seen.add(lowered_url)
            label = _clean_text(str(match.group(2) or ""), limit_chars=240).strip()
            context = _clean_text(raw_html[max(0, match.start() - 180): min(len(raw_html), match.end() + 240)], limit_chars=320)
            out.append(
                {
                    "url": resolved,
                    "label": label,
                    "context": _peek_text(context, 160),
                    "source_url": base_url,
                    "source_title": str(record.get("url_title") or ""),
                }
            )
            if len(out) >= max(1, int(max_links or 1)):
                return out
    return out


def canonicalize_candidate_title(row: dict) -> str:
    title = _strip_listing_author_tail(str(row.get("title") or "").strip())
    if not title:
        return ""
    lowered = title.lower()
    if lowered.startswith("placement "):
        title = title[len("placement "):].strip()
    title = re.sub(r"^\d{1,2}:\d{2}\s*[|:\-–— ]+\s*", "", title).strip()
    title = re.sub(r"\s{2,}", " ", title).strip(" -:;|")
    if "|" in title and ":" not in title:
        recovered = _strip_listing_author_tail(infer_paper_title(str(row.get("abstract") or ""), title))
        if recovered:
            title = recovered
    return title.strip(" -:;|")
