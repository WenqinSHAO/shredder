from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from src.connectors.http import normalize_arxiv_id, normalize_doi
from src.orchestrator.agentic_search import (
    _as_list,
    _normalize_title_for_key,
    _peek_text,
    _rank_candidates,
    _strip_listing_author_tail,
)
from src.orchestrator.agentic_text import (
    LISTING_HEADING_PHRASES,
    NON_PAPER_TITLE_TOKENS,
    _clean_text,
    _estimate_text_tokens,
    _normalize_anchor_terms,
    _safe_int,
    _unique_nonempty,
)

ProgressCallback = Callable[[dict], None]


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

    def _structured_match_text(fact: dict) -> str:
        llm_extract = fact.get("llm_extract") if isinstance(fact.get("llm_extract"), dict) else {}
        parts = [
            str(fact.get("paper_title") or ""),
            str(llm_extract.get("decision_reason") or ""),
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

    def _subject_filters(fact: dict) -> tuple[list[str], list[str], int | None]:
        filters = fact.get("filters") if isinstance(fact.get("filters"), dict) else {}
        intent = fact.get("extract_intent") if isinstance(fact.get("extract_intent"), dict) else {}
        must_match = intent.get("must_match") if isinstance(intent.get("must_match"), dict) else {}
        institution_terms: list[str] = []
        author_terms: list[str] = []
        for key in ("institution", "institution_contains"):
            raw = str(filters.get(key) or "").strip()
            if raw:
                institution_terms.extend(_tokenize_filter(raw))
        if not institution_terms:
            institution_terms.extend(_tokenize_filter(" ".join(_as_list(must_match.get("institution_any")))))
        for key in ("author", "author_contains"):
            raw = str(filters.get(key) or "").strip()
            if raw:
                author_terms.extend(_tokenize_filter(raw))
        if not author_terms:
            author_terms.extend(_tokenize_filter(" ".join(_as_list(must_match.get("author_any")))))
        year_gte = _safe_int(filters.get("year_gte"))
        if year_gte is None:
            year_gte = _safe_int(must_match.get("year_gte"))
        return (
            _unique_nonempty(institution_terms, limit=16),
            _unique_nonempty(author_terms, limit=16),
            year_gte,
        )

    def _passes_fact_filters(fact: dict) -> bool:
        llm_extract = fact.get("llm_extract")
        llm_match = ""
        if isinstance(llm_extract, dict):
            llm_match = str(llm_extract.get("match_decision") or "").strip().lower()
        if llm_match == "non_match":
            return False
        institution_terms, author_terms, year_gte = _subject_filters(fact)
        if year_gte is not None:
            year = _safe_int(fact.get("year"))
            if year is None or year < year_gte:
                return False
        if llm_match == "match":
            return True
        structured = _structured_match_text(fact)
        local = _local_evidence_text(fact)
        if institution_terms and not (
            any(term in structured for term in institution_terms)
            or any(term in local for term in institution_terms)
        ):
            return False
        if author_terms and not (
            any(term in structured for term in author_terms)
            or any(term in local for term in author_terms)
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
                "abstract": evidence,
                "abstract_snippet": (
                    _peek_text(str((llm_extract or {}).get("abstract_snippet") or ""), 320)
                    if isinstance(llm_extract, dict)
                    else ""
                ),
                "authors": (
                    "; ".join([str(v).strip() for v in ((llm_extract or {}).get("authors") or []) if str(v).strip()][:20])
                    if isinstance((llm_extract or {}).get("authors"), list)
                    else str((llm_extract or {}).get("authors") or "")
                ),
                "affiliations": (
                    "; ".join([str(v).strip() for v in ((llm_extract or {}).get("affiliations") or []) if str(v).strip()][:20])
                    if isinstance((llm_extract or {}).get("affiliations"), list)
                    else str((llm_extract or {}).get("affiliations") or "")
                ) or (
                    "; ".join([str(v).strip() for v in ((llm_extract or {}).get("institution_hits") or []) if str(v).strip()][:20])
                    if isinstance((llm_extract or {}).get("institution_hits"), list)
                    else str((llm_extract or {}).get("institution_hits") or "")
                ),
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


def canonicalize_candidate_title(row: dict) -> str:
    title = _strip_listing_author_tail(str(row.get("title") or "").strip())
    if not title:
        return ""
    lowered = title.lower()
    if lowered.startswith("placement "):
        title = title[len("placement ") :].strip()
    title = re.sub(r"^\d{1,2}:\d{2}\s*[|:\-–— ]+\s*", "", title).strip()
    title = re.sub(r"\s{2,}", " ", title).strip(" -:;|")
    if "|" in title and ":" not in title:
        recovered = _strip_listing_author_tail(infer_paper_title(str(row.get("abstract") or ""), title))
        if recovered:
            title = recovered
    return title.strip(" -:;|")


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


def extract_llm_system_prompt() -> str:
    return (
        "You extract academic paper rows from conference/web listing segments. "
        "Return JSON only with key `items` as list of row objects. "
        "Each row object keys: "
        "is_paper, paper_title_raw, paper_title_normalized, authors, affiliations, venue, year, doi, arxiv_id, abstract_snippet, "
        "institution_hits, match_decision, decision_reason, evidence_span, confidence. "
        "Hard rules: "
        "Extract one row per concrete paper entry. "
        "Institution/author matching must be row-local to the extracted title and authors. "
        "Interpret institution filters as a coauthor-affiliation test: a row matches if at least one coauthor affiliation matches the institution filter. "
        "Interpret author filters as a coauthor-name test: a row matches if at least one author name matches the author filter. "
        "Do not require majority authorship or first-author authorship unless the prompt explicitly requires that. "
        "Do not emit navigation/menu text, sponsor text, keynote/session headings, schedule blocks, or page headers. "
        "Do not emit acronym-only strings as paper titles. "
        "If authors, affiliations, or abstract are present in the local segment for a kept paper row, extract them in the same row instead of omitting them. "
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
        if re.fullmatch(r"[A-Za-z0-9\-]{2,15}", title):
            continue
        if len(title.split()) < 3 and not (is_paper or doi or arxiv_id):
            continue
        if match_decision == "non_match" and not is_paper:
            continue
        authors_raw = item.get("authors")
        affiliations_raw = item.get("affiliations")
        abstract_snippet = peek_text_fn(str(item.get("abstract_snippet") or ""), 320)
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
                    "paper_title_raw": title_raw,
                    "paper_title_normalized": title_norm or title,
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


def _block_extract_requires_fetched_content(
    *,
    requested_target_ids: set[str],
    requested_urls: set[str],
    mapped_urls: set[str],
) -> dict:
    return {
        "status": "blocked",
        "tool_calls": "extract:0",
        "web_rows": [],
        "raw_candidates": [],
        "paper_candidates": [],
        "stop": True,
        "stop_reason": "extract_requires_fetched_content",
        "notes": (
            "no fetched content available for selected targets "
            f"requested_target_ids={len(requested_target_ids)} requested_urls={len(requested_urls)} "
            f"mapped_urls={len(mapped_urls)}"
        ),
    }


def _prepare_extract_target(
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


def _resolve_extract_request(
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


def _build_extract_action_result(
    *,
    facts: list[dict[str, Any]],
    paper_candidates: list[dict[str, Any]],
    requested_urls: list[str],
    llm_extract_attempted: int,
    llm_extract_applied: int,
    llm_timeout_errors: int,
    llm_empty_semantic: int,
    coverage_has_more: bool,
    coverage_passes: int,
    extract_windows_trace: list[dict[str, Any]],
    extract_intent: dict[str, Any],
    auto_fetched_records: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "status": "ok",
        "tool_calls": f"extract:{len(facts)}",
        "web_rows": [],
        "raw_candidates": [],
        "paper_candidates": paper_candidates,
        "stop": False,
        "stop_reason": "",
        "notes": (
            f"requested_urls={len(requested_urls)} extracted_rows={len(facts)} "
            f"llm_extract_attempted={llm_extract_attempted} llm_extract_applied={llm_extract_applied} "
            f"llm_timeout_errors={llm_timeout_errors} llm_empty_semantic={llm_empty_semantic} "
            f"coverage_has_more={coverage_has_more} coverage_passes={coverage_passes}"
        ),
        "extracted_records": facts,
        "coverage_has_more": coverage_has_more,
        "coverage_passes": coverage_passes,
        "extract_timeout_errors": llm_timeout_errors,
        "extract_empty_semantic": llm_empty_semantic,
        "extract_windows_trace": extract_windows_trace,
        "extract_intent": extract_intent,
        "auto_fetched_count": len(auto_fetched_records),
        "auto_fetched_ok": sum(1 for row in auto_fetched_records if str(row.get("status") or "") == "ok"),
        "auto_fetched_error": sum(1 for row in auto_fetched_records if str(row.get("status") or "") != "ok"),
    }


def _new_extract_window_trace(
    *,
    prepared: dict[str, Any],
    extract_intent: dict[str, Any],
    coverage_batch_size: int,
) -> dict[str, Any]:
    row = prepared["row"]
    ranked_segments = list(prepared["ranked_segments"])
    return {
        "target_id": str(row.get("target_id") or ""),
        "url": str(row.get("url") or ""),
        "url_title": str(row.get("url_title") or ""),
        "status": str(row.get("status") or ""),
        "filters": dict(prepared["filters"]),
        "anchor_terms": list(prepared["anchor_terms"]),
        "intent": dict(extract_intent),
        "window_count": len(ranked_segments),
        "windows": ranked_segments[: min(12, len(ranked_segments))],
        "segment_total": len(ranked_segments),
        "segment_batch_size": coverage_batch_size,
        "batch_mode": str(prepared["batch_mode"]),
        "input_token_budget": int(prepared["token_budget"]),
        "llm_items_count": 0,
        "llm_items": [],
        "segments_done": 0,
        "segments_pending": len(ranked_segments),
        "llm_requests": 0,
        "llm_responses": 0,
        "llm_empty_responses": 0,
        "llm_errors": 0,
        "llm_timeout_errors": 0,
        "coverage_pct": 0.0,
    }


def _merge_extracted_facts(
    *,
    llm_facts: list[dict[str, Any]],
    cycle_index: int,
    progress_callback: ProgressCallback | None,
    deps: dict[str, Any],
) -> list[dict[str, Any]]:
    candidate_dedup_key_fn = deps["candidate_dedup_key_fn"]
    emit_progress_fn = deps["emit_progress_fn"]

    def _fact_rank_key(fact: dict) -> tuple[int, int, float, int]:
        status = str(fact.get("status") or "")
        status_rank = 3 if status == "ok" else (2 if status == "weak_signal" else 1)
        score = float(fact.get("score") or 0.0)
        title_len = len(str(fact.get("paper_title") or ""))
        evidence_len = len(str(fact.get("evidence") or ""))
        return (status_rank, int(score * 1000), evidence_len, title_len)

    by_key: dict[str, dict] = {}
    for fact in llm_facts:
        key = candidate_dedup_key_fn(
            {
                "source": "agentic_extract",
                "reason": "extract_content",
                "title": fact.get("paper_title"),
                "year": fact.get("year"),
                "doi": fact.get("doi"),
                "arxiv_id": fact.get("arxiv_id"),
                "url": fact.get("url"),
            }
        )
        prev = by_key.get(key)
        if prev is None or _fact_rank_key(fact) > _fact_rank_key(prev):
            by_key[key] = fact
    facts = list(by_key.values())
    emit_progress_fn(
        progress_callback,
        event="agentic_extract_stage",
        cycle_index=cycle_index,
        stage="merge_results",
        llm_count=len(llm_facts),
        merged_count=len(facts),
    )
    return facts


def _run_prepared_extract_target(
    *,
    session_id: str,
    cycle_index: int,
    prepared: dict[str, Any],
    trace_entry: dict[str, Any],
    user_prompt: str,
    extract_intent: dict[str, Any],
    llm_extractor_model: str,
    llm_api_key_env: str,
    progress_callback: ProgressCallback | None,
    runtime_state: dict[str, Any],
    extract_state_by_url: dict[str, Any],
    raw_event_fn: Callable[[str, Any, list[str] | None], str] | None,
    coverage_batch_size: int,
    max_calls_per_target: int,
    deps: dict[str, Any],
) -> dict[str, Any]:
    emit_progress_fn = deps["emit_progress_fn"]
    slice_segments_by_token_budget_fn = deps["slice_segments_by_token_budget_fn"]
    next_op_id_fn = deps["next_op_id_fn"]
    extract_facts_with_llm_fn = deps["extract_facts_with_llm_fn"]

    row = prepared["row"]
    active_filters = dict(prepared["filters"])
    row_anchor_terms = list(prepared["anchor_terms"])
    ranked_segments = list(prepared["ranked_segments"])
    effective_batch_mode = str(prepared["batch_mode"])
    segment_token_budget = int(prepared["token_budget"])
    if str(row.get("status") or "") != "ok":
        return {
            "facts": [],
            "llm_extract_attempted": 0,
            "llm_extract_applied": 0,
            "llm_timeout_errors": 0,
            "llm_empty_semantic": 0,
            "coverage_has_more": False,
            "coverage_passes": 0,
        }

    url_key = str(row.get("url") or "").strip()
    page_state = extract_state_by_url.setdefault(
        url_key,
        {
            "segments_done": 0,
            "segment_total": 0,
            "failed": False,
            "completed": False,
            "last_error": "",
        },
    )
    if bool(page_state.get("failed")) or bool(page_state.get("completed")):
        trace_entry["status"] = "skipped"
        trace_entry["skip_reason"] = "failed" if bool(page_state.get("failed")) else "completed"
        trace_entry["last_error"] = str(page_state.get("last_error") or "")
        trace_entry["segments_done"] = int(page_state.get("segments_done") or 0)
        trace_entry["segments_pending"] = max(
            0,
            int(page_state.get("segment_total") or 0) - int(page_state.get("segments_done") or 0),
        )
        return {
            "facts": [],
            "llm_extract_attempted": 0,
            "llm_extract_applied": 0,
            "llm_timeout_errors": 0,
            "llm_empty_semantic": 0,
            "coverage_has_more": False,
            "coverage_passes": 0,
        }

    emit_progress_fn(
        progress_callback,
        event="agentic_extract_target_start",
        cycle_index=cycle_index,
        target_id=str(row.get("target_id") or ""),
        url=str(row.get("url") or ""),
    )
    page_state["segment_total"] = len(ranked_segments)
    effective_batch_size = max(1, min(coverage_batch_size, len(ranked_segments)))
    start = max(0, min(int(page_state.get("segments_done") or 0), len(ranked_segments)))
    if start >= len(ranked_segments):
        page_state["completed"] = True
        trace_entry["status"] = "skipped"
        trace_entry["skip_reason"] = "completed"
        trace_entry["segments_done"] = start
        trace_entry["segments_pending"] = 0
        trace_entry["coverage_pct"] = 100.0
        trace_entry["anchor_terms"] = list(row_anchor_terms)
        trace_entry["filters"] = active_filters
        return {
            "facts": [],
            "llm_extract_attempted": 0,
            "llm_extract_applied": 0,
            "llm_timeout_errors": 0,
            "llm_empty_semantic": 0,
            "coverage_has_more": False,
            "coverage_passes": 0,
        }

    emit_progress_fn(
        progress_callback,
        event="agentic_extract_stage",
        cycle_index=cycle_index,
        target_id=str(row.get("target_id") or ""),
        url=str(row.get("url") or ""),
        stage="llm_prepare",
        segments_total=len([str(v) for v in (row.get("segments") or []) if str(v).strip()]) or len(ranked_segments),
        segments_ranked=len(ranked_segments),
        batch_mode=effective_batch_mode,
        token_budget=segment_token_budget,
    )

    llm_facts: list[dict[str, Any]] = []
    local_trace_items: list[dict[str, Any]] = []
    local_count = 0
    pass_count = 0
    llm_extract_attempted = 0
    llm_extract_applied = 0
    llm_timeout_errors = 0
    llm_empty_semantic = 0
    coverage_has_more = False
    coverage_passes = 0

    while start < len(ranked_segments):
        if pass_count >= max_calls_per_target:
            coverage_has_more = True
            break
        pass_count += 1
        coverage_passes += 1
        batch_start = start
        batch = slice_segments_by_token_budget_fn(
            ranked_segments,
            start=batch_start,
            max_segments=effective_batch_size,
            token_budget=segment_token_budget,
            min_segments=1 if effective_batch_mode == "page" else 2,
        )
        if not batch:
            break
        llm_extract_attempted += 1
        trace_entry["llm_requests"] = int(trace_entry.get("llm_requests") or 0) + 1
        emit_progress_fn(
            progress_callback,
            event="agentic_extract_batch_start",
            cycle_index=cycle_index,
            target_id=str(row.get("target_id") or ""),
            batch_start=start,
            batch_size=len(batch),
            segment_total=len(ranked_segments),
            batch_mode=effective_batch_mode,
            token_budget=segment_token_budget,
            pass_index=pass_count,
        )
        llm_raw_refs = [str(row.get("raw_path") or "")] if str(row.get("raw_path") or "") else []
        extracted: list[dict] = []
        llm_trace: dict[str, Any] = {}
        last_error = ""
        llm_op_id = next_op_id_fn(runtime_state, "extract_llm")
        if raw_event_fn is not None:
            raw_event_fn(
                "op_start",
                {
                    "op_id": llm_op_id,
                    "op_type": "extract_llm",
                    "component": "extract_content",
                    "target_id": str(row.get("target_id") or ""),
                    "url": str(row.get("url") or ""),
                    "batch_start": batch_start,
                    "batch_size": len(batch),
                    "segment_total": len(ranked_segments),
                    "batch_mode": effective_batch_mode,
                    "max_segments": len(batch),
                    "token_budget": segment_token_budget,
                    "model": llm_extractor_model,
                    "pass_index": pass_count,
                },
                llm_raw_refs,
            )
        try:
            extracted, llm_trace = extract_facts_with_llm_fn(
                record=row,
                filters=active_filters,
                user_prompt=user_prompt,
                model=llm_extractor_model,
                api_key_env=llm_api_key_env,
                intent=extract_intent,
                segments=batch,
                timeout_s=45.0,
                max_retries=0,
                raw_event_fn=(
                    (lambda event_type, payload: raw_event_fn(event_type, payload, llm_raw_refs))
                    if raw_event_fn is not None
                    else None
                ),
                llm_op_id=llm_op_id,
                batch_mode=effective_batch_mode,
            )
            if raw_event_fn is not None:
                raw_event_fn(
                    "op_end",
                    {
                        "op_id": llm_op_id,
                        "op_type": "extract_llm",
                        "component": "extract_content",
                        "target_id": str(row.get("target_id") or ""),
                        "url": str(row.get("url") or ""),
                        "status": "ok",
                        "batch_start": batch_start,
                        "batch_size": len(batch),
                        "segment_total": len(ranked_segments),
                        "extracted_count": len(extracted),
                        "batch_mode": effective_batch_mode,
                        "max_segments": len(batch),
                        "token_budget": segment_token_budget,
                        "input_tokens_est": int(llm_trace.get("input_tokens_est") or 0),
                        "segment_tokens_est": int(llm_trace.get("segment_tokens_est") or 0),
                        "scaffold_tokens_est": int(llm_trace.get("scaffold_tokens_est") or 0),
                        "model": llm_extractor_model,
                        "pass_index": pass_count,
                    },
                    llm_raw_refs,
                )
        except Exception as exc:
            last_error = f"{type(exc).__name__}:{exc}"
            trace_entry["llm_errors"] = int(trace_entry.get("llm_errors") or 0) + 1
            if "timeout" in last_error.lower():
                llm_timeout_errors += 1
                trace_entry["llm_timeout_errors"] = int(trace_entry.get("llm_timeout_errors") or 0) + 1
            page_state["failed"] = True
            page_state["last_error"] = last_error
            page_state["segments_done"] = max(int(page_state.get("segments_done") or 0), batch_start)
            if raw_event_fn is not None:
                raw_event_fn(
                    "op_end",
                    {
                        "op_id": llm_op_id,
                        "op_type": "extract_llm",
                        "component": "extract_content",
                        "target_id": str(row.get("target_id") or ""),
                        "url": str(row.get("url") or ""),
                        "status": "error",
                        "batch_start": batch_start,
                        "batch_size": len(batch),
                        "segment_total": len(ranked_segments),
                        "batch_mode": effective_batch_mode,
                        "max_segments": len(batch),
                        "token_budget": segment_token_budget,
                        "error": last_error,
                        "model": llm_extractor_model,
                        "pass_index": pass_count,
                    },
                    llm_raw_refs,
                )
            emit_progress_fn(
                progress_callback,
                event="agentic_extract_batch_done",
                cycle_index=cycle_index,
                target_id=str(row.get("target_id") or ""),
                extracted_count=0,
                pass_index=pass_count,
                error=f"llm_extract_failed:{last_error}",
            )
            break

        llm_trace["batch_start"] = batch_start
        llm_trace["batch_size"] = len(batch)
        llm_trace["segment_total"] = len(ranked_segments)
        llm_trace["pass_index"] = pass_count
        trace_entry["llm_responses"] = int(trace_entry.get("llm_responses") or 0) + 1
        trace_entry["batch_mode"] = effective_batch_mode
        trace_entry["input_token_budget"] = segment_token_budget
        trace_entry["last_input_tokens_est"] = int(llm_trace.get("input_tokens_est") or 0)
        trace_entry["last_segment_tokens_est"] = int(llm_trace.get("segment_tokens_est") or 0)
        trace_entry["last_scaffold_tokens_est"] = int(llm_trace.get("scaffold_tokens_est") or 0)
        if extracted:
            llm_extract_applied += 1
            local_count += len(extracted)
            llm_facts.extend(extracted)
            local_trace_items.extend(extracted)
        else:
            trace_entry["llm_empty_responses"] = int(trace_entry.get("llm_empty_responses") or 0) + 1
            llm_empty_semantic += 1
        emit_progress_fn(
            progress_callback,
            event="agentic_extract_batch_done",
            cycle_index=cycle_index,
            target_id=str(row.get("target_id") or ""),
            extracted_count=len(extracted),
            pass_index=pass_count,
        )
        start = batch_start + len(batch)
        page_state["segments_done"] = max(int(page_state.get("segments_done") or 0), start)

    if start >= len(ranked_segments):
        page_state["completed"] = True
    if not bool(page_state.get("failed")) and start < len(ranked_segments):
        coverage_has_more = True

    trace_entry["llm_items_count"] = local_count
    trace_entry["llm_items"] = [
        {
            "paper_title": str(item.get("paper_title") or ""),
            "year": str(item.get("year") or ""),
            "doi": str(item.get("doi") or ""),
            "arxiv_id": str(item.get("arxiv_id") or ""),
            "status": str(item.get("status") or ""),
            "score": float(item.get("score") or 0.0),
        }
        for item in local_trace_items
    ]
    trace_entry["segments_done"] = min(start, len(ranked_segments))
    trace_entry["segments_pending"] = max(0, len(ranked_segments) - int(trace_entry.get("segments_done") or 0))
    done = float(trace_entry.get("segments_done") or 0)
    total = float(len(ranked_segments) or 1)
    trace_entry["coverage_pct"] = round(100.0 * done / total, 2)
    emit_progress_fn(
        progress_callback,
        event="agentic_extract_target_done",
        cycle_index=cycle_index,
        target_id=str(row.get("target_id") or ""),
        extracted_count=local_count,
        coverage_has_more=coverage_has_more,
        segments_done=min(start, len(ranked_segments)),
        segments_total=len(ranked_segments),
    )
    return {
        "facts": llm_facts,
        "llm_extract_attempted": llm_extract_attempted,
        "llm_extract_applied": llm_extract_applied,
        "llm_timeout_errors": llm_timeout_errors,
        "llm_empty_semantic": llm_empty_semantic,
        "coverage_has_more": coverage_has_more,
        "coverage_passes": coverage_passes,
    }


def _run_extract_targets(
    *,
    session_id: str,
    cycle_index: int,
    prepared_targets: list[dict[str, Any]],
    extract_windows_trace: list[dict[str, Any]],
    user_prompt: str,
    extract_intent: dict[str, Any],
    llm_extractor_model: str,
    llm_api_key_env: str,
    progress_callback: ProgressCallback | None,
    runtime_state: dict[str, Any],
    raw_event_fn: Callable[[str, Any, list[str] | None], str] | None,
    coverage_batch_size: int,
    max_calls_per_target: int,
    deps: dict[str, Any],
) -> dict[str, Any]:
    llm_extract_attempted = 0
    llm_extract_applied = 0
    llm_timeout_errors = 0
    llm_empty_semantic = 0
    coverage_has_more = False
    coverage_passes = 0
    llm_facts: list[dict[str, Any]] = []
    extract_state_by_url = runtime_state.setdefault("extract_state_by_url", {})

    for prepared, trace_entry in zip(prepared_targets, extract_windows_trace):
        target_result = _run_prepared_extract_target(
            session_id=session_id,
            cycle_index=cycle_index,
            prepared=prepared,
            trace_entry=trace_entry,
            user_prompt=user_prompt,
            extract_intent=extract_intent,
            llm_extractor_model=llm_extractor_model,
            llm_api_key_env=llm_api_key_env,
            progress_callback=progress_callback,
            runtime_state=runtime_state,
            extract_state_by_url=extract_state_by_url,
            raw_event_fn=raw_event_fn,
            coverage_batch_size=coverage_batch_size,
            max_calls_per_target=max_calls_per_target,
            deps=deps,
        )
        llm_facts.extend(list(target_result["facts"]))
        llm_extract_attempted += int(target_result["llm_extract_attempted"])
        llm_extract_applied += int(target_result["llm_extract_applied"])
        llm_timeout_errors += int(target_result["llm_timeout_errors"])
        llm_empty_semantic += int(target_result["llm_empty_semantic"])
        coverage_passes += int(target_result["coverage_passes"])
        coverage_has_more = coverage_has_more or bool(target_result["coverage_has_more"])

    facts: list[dict[str, Any]] = []
    if llm_facts:
        facts = _merge_extracted_facts(
            llm_facts=llm_facts,
            cycle_index=cycle_index,
            progress_callback=progress_callback,
            deps=deps,
        )

    return {
        "facts": facts,
        "llm_extract_attempted": llm_extract_attempted,
        "llm_extract_applied": llm_extract_applied,
        "llm_timeout_errors": llm_timeout_errors,
        "llm_empty_semantic": llm_empty_semantic,
        "coverage_has_more": coverage_has_more,
        "coverage_passes": coverage_passes,
    }


def execute_extract_content_action(
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
    raw_event_fn: Callable[[str, Any, list[str] | None], str] | None,
    deps: dict[str, Any],
) -> dict:
    request = _resolve_extract_request(
        session_id=session_id,
        cycle_index=cycle_index,
        params=params,
        paths=paths,
        user_prompt=user_prompt,
        timeout_s=timeout_s,
        runtime_state=runtime_state,
        raw_event_fn=raw_event_fn,
        deps=deps,
    )
    target_scope_by_url = dict(request["target_scope_by_url"])
    filters = dict(request["filters"])
    extract_intent = dict(request["extract_intent"])
    anchor_terms = list(request["anchor_terms"])
    records = list(request["records"])
    requested_urls = list(request["requested_urls"])
    auto_fetched_records = list(request["auto_fetched_records"])

    if not records:
        requested_count = len([url for url in requested_urls if str(url).strip()])
        return {
            "status": "error",
            "tool_calls": "extract:0",
            "web_rows": [],
            "raw_candidates": [],
            "paper_candidates": [],
            "stop": False,
            "stop_reason": "",
            "notes": f"extract_failed_no_fetched_records requested_urls={requested_count}",
            "extracted_records": [],
            "coverage_has_more": False,
            "coverage_passes": 0,
            "extract_timeout_errors": 0,
            "extract_empty_semantic": 0,
            "extract_windows_trace": [],
            "extract_intent": extract_intent,
            "auto_fetched_count": len(auto_fetched_records),
            "auto_fetched_ok": sum(1 for row in auto_fetched_records if str(row.get('status') or '') == 'ok'),
            "auto_fetched_error": sum(1 for row in auto_fetched_records if str(row.get('status') or '') != 'ok'),
        }

    coverage_batch_size = 32
    context_limit_tokens = int(deps["context_limit_tokens"])
    safety_margin = float(deps["safety_margin"])
    output_token_reserve = int(deps["output_token_reserve"])
    max_calls_per_target = 4
    emit_progress_fn = deps["emit_progress_fn"]

    extract_windows_trace: list[dict] = []
    emit_progress_fn(
        progress_callback,
        event="agentic_extract_stage",
        cycle_index=cycle_index,
        stage="extract_start",
        target_count=len(records),
        filters=dict(filters),
        anchor_terms=anchor_terms,
        intent=extract_intent,
    )
    must_match = extract_intent.get("must_match") if isinstance(extract_intent.get("must_match"), dict) else {}
    prepared_targets = [
        _prepare_extract_target(
            row=row,
            target_scope_by_url=target_scope_by_url,
            filters=filters,
            anchor_terms=anchor_terms,
            must_match=must_match,
            extract_intent=extract_intent,
            user_prompt=user_prompt,
            context_limit_tokens=context_limit_tokens,
            safety_margin=safety_margin,
            output_token_reserve=output_token_reserve,
            deps=deps,
        )
        for row in records
    ]
    for prepared in prepared_targets:
        row = prepared["row"]
        ranked_segments = list(prepared["ranked_segments"])
        emit_progress_fn(
            progress_callback,
            event="agentic_extract_stage",
            cycle_index=cycle_index,
            target_id=str(row.get("target_id") or ""),
            url=str(row.get("url") or ""),
            stage="segment_filter",
            segments_total=len([str(v) for v in (row.get("segments") or []) if str(v).strip()]) or len(ranked_segments),
            segments_ranked=len(ranked_segments),
            batch_mode=str(prepared["batch_mode"]),
            token_budget=int(prepared["token_budget"]),
        )
        extract_windows_trace.append(
            _new_extract_window_trace(
                prepared=prepared,
                extract_intent=extract_intent,
                coverage_batch_size=coverage_batch_size,
            )
        )

    facts: list[dict] = []
    llm_extract_attempted = 0
    llm_extract_applied = 0
    llm_timeout_errors = 0
    llm_empty_semantic = 0
    coverage_has_more = False
    coverage_passes = 0
    if extract_use_llm_extractor:
        extract_run = _run_extract_targets(
            session_id=session_id,
            cycle_index=cycle_index,
            prepared_targets=prepared_targets,
            extract_windows_trace=extract_windows_trace,
            user_prompt=user_prompt,
            extract_intent=extract_intent,
            llm_extractor_model=llm_extractor_model,
            llm_api_key_env=llm_api_key_env,
            progress_callback=progress_callback,
            runtime_state=runtime_state,
            raw_event_fn=raw_event_fn,
            coverage_batch_size=coverage_batch_size,
            max_calls_per_target=max_calls_per_target,
            deps=deps,
        )
        facts = list(extract_run["facts"])
        llm_extract_attempted = int(extract_run["llm_extract_attempted"])
        llm_extract_applied = int(extract_run["llm_extract_applied"])
        llm_timeout_errors = int(extract_run["llm_timeout_errors"])
        llm_empty_semantic = int(extract_run["llm_empty_semantic"])
        coverage_has_more = bool(extract_run["coverage_has_more"])
        coverage_passes = int(extract_run["coverage_passes"])

    paper_candidates = deps["to_paper_candidates_from_facts_fn"](facts)
    return _build_extract_action_result(
        facts=facts,
        paper_candidates=paper_candidates,
        requested_urls=requested_urls,
        llm_extract_attempted=llm_extract_attempted,
        llm_extract_applied=llm_extract_applied,
        llm_timeout_errors=llm_timeout_errors,
        llm_empty_semantic=llm_empty_semantic,
        coverage_has_more=coverage_has_more,
        coverage_passes=coverage_passes,
        extract_windows_trace=extract_windows_trace,
        extract_intent=extract_intent,
        auto_fetched_records=auto_fetched_records,
    )
