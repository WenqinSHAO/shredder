from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from src.connectors.http import normalize_arxiv_id, normalize_doi
from src.kb import (
    add_provenance,
    count_papers_for_author,
    delete_paper,
    find_paper_ids_by_arxiv,
    find_paper_ids_by_doi,
    get_paper_with_authors,
    init_kb,
    list_paper_author_ids,
    replace_author_orgs,
    replace_paper_author_metadata,
    replace_paper_authors,
    search_papers,
    upsert_author,
    upsert_author_org,
    upsert_org,
    upsert_paper,
)
from src.retrieval.service import (
    SOURCE_FIELDS,
    _candidate_dedup_key,
    arxiv_id_from_doi_alias,
    build_adapters,
    canonical_query_key,
    normalize_title,
    resolve_deterministic,
    run_open_retrieval,
    write_candidate_tsv,
    write_handoff_tsv,
    write_sources_tsv,
    write_yaml,
)
from src.utils.paths import project_dir
from src.utils.yamlx import dump_to_path, load

ProgressCallback = Callable[[dict], None]
SOURCE_HISTORY_FIELDS = ["timestamp", "query_key", "resolution_status", "paper_id", *SOURCE_FIELDS]


def _retrieval_dir(pdir: Path) -> Path:
    return pdir / "artifacts" / "retrieval"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _emit_progress(progress_callback: ProgressCallback | None, *, event: str, **payload) -> None:
    if progress_callback is None:
        return
    body = {"event": event}
    body.update(payload)
    progress_callback(body)


def _deterministic_index_path(pdir: Path) -> Path:
    return _retrieval_dir(pdir) / "deterministic_result.yaml"


def _empty_index(policy_default: str = "cache_first") -> dict:
    return {
        "artifact_type": "deterministic_retrieval_index",
        "schema_version": "0.3.0",
        "updated_at": _utc_now(),
        "policy_default": policy_default,
        "papers": [],
        "queries": [],
    }


def _normalize_query_key_aliases(keys: list[str]) -> list[str]:
    normalized: set[str] = set()
    for key in keys:
        text = str(key or "").strip().lower()
        if not text:
            continue
        if text.startswith("doi:"):
            alias_arxiv = arxiv_id_from_doi_alias(text[len("doi:") :])
            if alias_arxiv:
                normalized.add(f"arxiv:{alias_arxiv}")
        normalized.add(text)
    return sorted(normalized)


def _entry_arxiv_query_ids(entry: dict) -> set[str]:
    out: set[str] = set()
    for key in entry.get("query_keys") or []:
        text = str(key or "").strip().lower()
        if text.startswith("arxiv:") and text[len("arxiv:") :]:
            out.add(text[len("arxiv:") :])
    return out


def _choose_preferred_paper(existing: dict, incoming: dict, *, arxiv_query_ids: set[str]) -> dict:
    existing_arxiv = normalize_arxiv_id(str(existing.get("arxiv_id") or ""))
    incoming_arxiv = normalize_arxiv_id(str(incoming.get("arxiv_id") or ""))
    if arxiv_query_ids:
        existing_match = existing_arxiv in arxiv_query_ids
        incoming_match = incoming_arxiv in arxiv_query_ids
        if existing_match and not incoming_match:
            return existing
        if incoming_match and not existing_match:
            return incoming

    existing_score = (
        1 if existing_arxiv else 0,
        1 if normalize_doi(str(existing.get("doi") or "")) else 0,
        len(str(existing.get("abstract") or "")),
        int(existing.get("author_count") or len(existing.get("authors") or [])),
    )
    incoming_score = (
        1 if incoming_arxiv else 0,
        1 if normalize_doi(str(incoming.get("doi") or "")) else 0,
        len(str(incoming.get("abstract") or "")),
        int(incoming.get("author_count") or len(incoming.get("authors") or [])),
    )
    return existing if existing_score >= incoming_score else incoming


def _merge_overlapping_entries(entries: list[dict]) -> tuple[list[dict], list[str]]:
    out: list[dict] = []
    removed_ids: list[str] = []
    for entry in entries:
        incoming = dict(entry)
        incoming["query_keys"] = _normalize_query_key_aliases(list(incoming.get("query_keys") or []))
        incoming_keys = set(incoming["query_keys"])
        if not incoming_keys:
            out.append(incoming)
            continue

        merged = False
        for idx, existing in enumerate(out):
            existing_keys = set(existing.get("query_keys") or [])
            if not (existing_keys & incoming_keys):
                continue
            arxiv_query_ids = _entry_arxiv_query_ids(existing) | _entry_arxiv_query_ids(incoming)
            chosen_paper = _choose_preferred_paper(existing.get("paper") or {}, incoming.get("paper") or {}, arxiv_query_ids=arxiv_query_ids)
            dropped_paper_id = (incoming.get("paper") or {}).get("paper_id")
            if chosen_paper == (incoming.get("paper") or {}):
                dropped_paper_id = (existing.get("paper") or {}).get("paper_id")
            chosen_paper_id = str(chosen_paper.get("paper_id") or "")

            existing["paper"] = chosen_paper
            existing["paper_id"] = chosen_paper.get("paper_id", existing.get("paper_id", ""))
            existing["query_keys"] = sorted(existing_keys | incoming_keys)
            existing["source_count"] = max(int(existing.get("source_count") or 0), int(incoming.get("source_count") or 0))
            existing["sources_truncated"] = int(existing["source_count"])
            trace = list(existing.get("search_trace") or [])
            for line in incoming.get("search_trace") or []:
                if line not in trace:
                    trace.append(line)
            existing["search_trace"] = trace
            existing["first_seen_at"] = min(
                str(existing.get("first_seen_at") or ""),
                str(incoming.get("first_seen_at") or ""),
            ) or str(existing.get("first_seen_at") or incoming.get("first_seen_at") or "")
            existing["last_seen_at"] = max(
                str(existing.get("last_seen_at") or ""),
                str(incoming.get("last_seen_at") or ""),
            ) or str(existing.get("last_seen_at") or incoming.get("last_seen_at") or "")
            existing["policy_last_used"] = incoming.get("policy_last_used") or existing.get("policy_last_used")
            out[idx] = existing
            if dropped_paper_id and str(dropped_paper_id) != chosen_paper_id:
                removed_ids.append(str(dropped_paper_id))
            merged = True
            break

        if not merged:
            out.append(incoming)
    # Keep insertion order stable while deduping removed ids.
    seen_removed: set[str] = set()
    ordered_removed: list[str] = []
    for paper_id in removed_ids:
        if paper_id in seen_removed:
            continue
        seen_removed.add(paper_id)
        ordered_removed.append(paper_id)
    return out, ordered_removed


def _load_index(pdir: Path, policy_default: str) -> dict:
    path = _deterministic_index_path(pdir)
    if not path.exists():
        return _empty_index(policy_default)
    payload = load(path)
    if not isinstance(payload, dict) or "papers" not in payload or "queries" not in payload:
        return _empty_index(policy_default)
    compacted = _compact_index_payload(payload)
    merged_entries, removed_ids = _merge_overlapping_entries(list(compacted.get("papers") or []))
    compacted["papers"] = merged_entries
    compacted["_removed_paper_ids"] = removed_ids
    return compacted


def _save_index(pdir: Path, index_payload: dict) -> Path:
    out = _deterministic_index_path(pdir)
    out.parent.mkdir(parents=True, exist_ok=True)
    index_payload.pop("_removed_paper_ids", None)
    index_payload["updated_at"] = _utc_now()
    dump_to_path(out, index_payload)
    return out


def _load_request_log(path: Path) -> dict:
    base = {
        "artifact_type": "deterministic_request_log",
        "schema_version": "0.1.0",
        "updated_at": _utc_now(),
        "latest_request": {},
        "history": [],
    }
    if not path.exists():
        return base

    payload = load(path)
    if isinstance(payload, dict) and isinstance(payload.get("history"), list):
        return payload

    if isinstance(payload, dict) and any(k in payload for k in ("title", "doi", "arxiv_url", "arxiv_id")):
        legacy_request = {
            "title": payload.get("title", ""),
            "doi": payload.get("doi", ""),
            "arxiv_url": payload.get("arxiv_url", ""),
            "arxiv_id": payload.get("arxiv_id", ""),
            "policy": payload.get("policy", ""),
            "ambiguity_delta": payload.get("ambiguity_delta", 0.05),
        }
        base["latest_request"] = legacy_request
        base["history"].append(
            {
                "timestamp": "",
                "query_key": canonical_query_key(legacy_request),
                "query": legacy_request,
                "policy": legacy_request.get("policy", ""),
                "resolution_status": "",
                "paper_id": "",
                "reason": "legacy_single_snapshot",
                "cache_hit": False,
            }
        )
    return base


def _write_request_artifacts(
    rdir: Path,
    *,
    request_payload: dict,
    query_key: str,
    result: dict,
    cache_hit: bool,
) -> tuple[Path, Path]:
    latest_request = dict(request_payload)
    latest_path = write_yaml(rdir / "deterministic_request_latest.yaml", latest_request)
    log_path = rdir / "deterministic_request.yaml"
    log_payload = _load_request_log(log_path)
    log_payload["updated_at"] = _utc_now()
    log_payload["latest_request"] = latest_request
    log_payload.setdefault("history", []).append(
        {
            "timestamp": _utc_now(),
            "query_key": query_key,
            "query": dict(request_payload),
            "policy": request_payload.get("policy", ""),
            "resolution_status": result.get("resolution_status", result.get("status", "")),
            "paper_id": ((result.get("paper") or {}).get("paper_id") or ""),
            "reason": result.get("reason", ""),
            "cache_hit": bool(cache_hit),
        }
    )
    dump_to_path(log_path, log_payload)
    return log_path, latest_path


def _load_sources_history(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        existing_fields = reader.fieldnames or []
        rows = list(reader)

    out: list[dict] = []
    if existing_fields == SOURCE_HISTORY_FIELDS:
        for row in rows:
            out.append({k: row.get(k, "") for k in SOURCE_HISTORY_FIELDS})
        return out

    # Legacy deterministic_sources.tsv only contained SOURCE_FIELDS.
    for row in rows:
        converted = {k: "" for k in SOURCE_HISTORY_FIELDS}
        for field in SOURCE_FIELDS:
            converted[field] = row.get(field, "")
        out.append(converted)
    return out


def _write_sources_artifacts(
    rdir: Path,
    *,
    query_key: str,
    result: dict,
) -> tuple[Path, Path]:
    latest_path = write_sources_tsv(rdir / "deterministic_sources_latest.tsv", result.get("sources") or [])
    history_path = rdir / "deterministic_sources.tsv"
    rows = _load_sources_history(history_path)

    timestamp = _utc_now()
    resolution_status = result.get("resolution_status", result.get("status", ""))
    paper_id = ((result.get("paper") or {}).get("paper_id") or "")
    for source_row in result.get("sources") or []:
        row = {
            "timestamp": timestamp,
            "query_key": query_key,
            "resolution_status": resolution_status,
            "paper_id": paper_id,
        }
        for field in SOURCE_FIELDS:
            value = source_row.get(field, "")
            if isinstance(value, list):
                row[field] = "|".join(str(v) for v in value if str(v).strip())
            else:
                row[field] = str(value) if value is not None else ""
        rows.append(row)

    history_path.parent.mkdir(parents=True, exist_ok=True)
    with history_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=SOURCE_HISTORY_FIELDS, delimiter="\t")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in SOURCE_HISTORY_FIELDS})

    return history_path, latest_path


def _paper_keys(paper: dict) -> set[str]:
    keys: set[str] = set()
    if paper.get("paper_id"):
        keys.add(str(paper["paper_id"]))
    doi = normalize_doi(str(paper.get("doi") or ""))
    if doi:
        keys.add(f"doi:{doi}")
    arxiv_id = normalize_arxiv_id(str(paper.get("arxiv_id") or ""))
    if arxiv_id:
        keys.add(f"arxiv:{arxiv_id}")
        keys.add(f"doi:10.48550/arxiv.{arxiv_id}")
    doi_alias_arxiv = arxiv_id_from_doi_alias(doi)
    if doi_alias_arxiv:
        keys.add(f"arxiv:{doi_alias_arxiv}")
        keys.add(f"doi:10.48550/arxiv.{doi_alias_arxiv}")
    title = normalize_title(str(paper.get("title") or ""))
    if title:
        keys.add(f"title:{title}")
    return keys


def _normalize_year(value: str | int | None) -> str:
    text = str(value or "").strip()
    return text if text.isdigit() else ""


def _normalize_venue(value: str | None) -> str:
    return normalize_title(str(value or ""))


def _papers_equivalent(a: dict, b: dict) -> bool:
    a_doi = normalize_doi(str(a.get("doi") or ""))
    b_doi = normalize_doi(str(b.get("doi") or ""))
    a_arxiv = normalize_arxiv_id(str(a.get("arxiv_id") or ""))
    b_arxiv = normalize_arxiv_id(str(b.get("arxiv_id") or ""))
    if a_doi and b_doi and a_doi == b_doi:
        return True
    if a_arxiv and b_arxiv and a_arxiv == b_arxiv:
        return True
    if a_arxiv and arxiv_id_from_doi_alias(b_doi) == a_arxiv:
        return True
    if b_arxiv and arxiv_id_from_doi_alias(a_doi) == b_arxiv:
        return True

    a_title = normalize_title(str(a.get("title") or ""))
    b_title = normalize_title(str(b.get("title") or ""))
    if not a_title or a_title != b_title:
        return False
    a_year = _normalize_year(a.get("year"))
    b_year = _normalize_year(b.get("year"))
    # Title-only equivalence is intentionally conservative to avoid collapsing
    # distinct papers that share a common/templated title.
    if not a_year or not b_year:
        return False
    if a_year != b_year:
        return False
    a_venue = _normalize_venue(a.get("venue"))
    b_venue = _normalize_venue(b.get("venue"))
    if a_venue and b_venue and a_venue != b_venue:
        return False
    return True


def _find_equivalent_paper_entry(papers: list[dict], paper: dict) -> dict | None:
    for entry in papers:
        candidate = entry.get("paper") or {}
        if _papers_equivalent(candidate, paper):
            return entry
    return None


def _find_cached_paper(index_payload: dict, query_key: str, query: dict) -> dict | None:
    for entry in index_payload.get("papers", []):
        keys = set(entry.get("query_keys") or [])
        paper = entry.get("paper") or {}
        keys |= _paper_keys(paper)
        if query_key in keys:
            return entry
        doi = normalize_doi(str(query.get("doi") or ""))
        paper_doi = normalize_doi(str(paper.get("doi") or ""))
        if doi and paper_doi == doi:
            return entry
        arxiv_id = normalize_arxiv_id(str(query.get("arxiv_id") or ""))
        if not arxiv_id:
            arxiv_id = normalize_arxiv_id(str(query.get("arxiv_url") or ""))
        if not arxiv_id:
            arxiv_id = arxiv_id_from_doi_alias(doi)
        paper_arxiv = normalize_arxiv_id(str(paper.get("arxiv_id") or ""))
        if arxiv_id and (paper_arxiv == arxiv_id or arxiv_id_from_doi_alias(paper_doi) == arxiv_id):
            return entry
    return None


def _source_cache_key(row: dict) -> str:
    source = (row.get("source") or "unknown").strip().lower()
    source_id = str(row.get("source_id") or "").strip()
    if source_id:
        return f"{source}:{source_id}"
    doi = str(row.get("doi") or "").strip().lower()
    if doi:
        return f"{source}:doi:{doi}"
    arxiv_id = str(row.get("arxiv_id") or "").strip().lower()
    if arxiv_id:
        return f"{source}:arxiv:{arxiv_id}"
    title = normalize_title(str(row.get("title") or ""))
    year = str(row.get("year") or "")
    return f"{source}:title:{title}:{year}"


def _decode_pipe_list(value: str | list) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    text = str(value or "").strip()
    if not text:
        return []
    return [item.strip() for item in text.split("|") if item.strip()]


def _db_payload_to_paper(payload: dict) -> dict:
    paper_row = payload.get("paper") or {}
    paper_id = str(paper_row.get("id") or "")
    doi = normalize_doi(str(paper_row.get("doi") or ""))
    arxiv_id = normalize_arxiv_id(str(paper_row.get("arxiv_id") or ""))
    if not arxiv_id and paper_id.lower().startswith("arxiv:"):
        arxiv_id = normalize_arxiv_id(paper_id[len("arxiv:") :])
    if not arxiv_id:
        arxiv_id = arxiv_id_from_doi_alias(doi)
    author_rows = list(payload.get("authors") or [])
    author_rows.sort(
        key=lambda row: (
            row.get("position") is None,
            int(row.get("position") or 10**9),
            str(row.get("name") or ""),
        )
    )
    authors = [
        {
            "author_id": str(row.get("id") or ""),
            "name": str(row.get("name") or ""),
            "orcid": str(row.get("orcid") or ""),
            "source_ids": list(row.get("source_ids") or []),
            "affiliations": list(row.get("affiliations") or []),
        }
        for row in author_rows
        if str(row.get("id") or "").strip()
    ]
    return {
        "paper_id": paper_id,
        "title": str(paper_row.get("title") or ""),
        "venue": str(paper_row.get("venue") or ""),
        "year": str(paper_row.get("year") or ""),
        "doi": doi,
        "arxiv_id": arxiv_id,
        "url": str(paper_row.get("html_url") or ""),
        "abstract": str(paper_row.get("abstract") or ""),
        "keywords": list(paper_row.get("keywords") or []),
        "categories": list(paper_row.get("categories") or []),
        "authors": authors,
    }


def _load_cached_paper_from_db(query: dict) -> dict | None:
    init_kb()
    doi = normalize_doi(str(query.get("doi") or ""))
    arxiv_id = normalize_arxiv_id(str(query.get("arxiv_id") or ""))
    if not arxiv_id:
        arxiv_id = normalize_arxiv_id(str(query.get("arxiv_url") or ""))
    if not arxiv_id:
        arxiv_id = arxiv_id_from_doi_alias(doi)

    candidate_ids: list[str] = []
    if arxiv_id:
        candidate_ids.append(f"arxiv:{arxiv_id}")
        candidate_ids.extend(find_paper_ids_by_arxiv(arxiv_id))
        candidate_ids.extend(find_paper_ids_by_doi(f"10.48550/arxiv.{arxiv_id}"))
    if doi:
        candidate_ids.append(f"doi:{doi}")
        candidate_ids.extend(find_paper_ids_by_doi(doi))

    title_raw = str(query.get("title") or "").strip()
    title_norm = normalize_title(title_raw)
    if title_norm:
        exact_title_rows = [row for row in search_papers(title_raw) if normalize_title(str(row.get("title") or "")) == title_norm]
        if len(exact_title_rows) == 1:
            candidate_ids.append(str(exact_title_rows[0].get("id") or ""))

    seen: set[str] = set()
    ordered_ids: list[str] = []
    for paper_id in candidate_ids:
        pid = str(paper_id or "").strip()
        if not pid or pid in seen:
            continue
        seen.add(pid)
        ordered_ids.append(pid)

    for paper_id in ordered_ids:
        payload = get_paper_with_authors(paper_id)
        if payload.get("paper"):
            return _db_payload_to_paper(payload)
    return None


def _is_paper_complete_for_db_hardening(paper: dict) -> bool:
    if not paper:
        return False
    has_title = bool(str(paper.get("title") or "").strip())
    has_identifier = bool(normalize_doi(str(paper.get("doi") or "")) or normalize_arxiv_id(str(paper.get("arxiv_id") or "")))
    has_year_or_venue = bool(_normalize_year(paper.get("year")) or str(paper.get("venue") or "").strip())
    has_semantics = bool(str(paper.get("abstract") or "").strip() or list(paper.get("keywords") or []) or list(paper.get("categories") or []))
    has_attribution = bool(list(paper.get("authors") or []))
    return has_title and has_identifier and has_year_or_venue and (has_semantics or has_attribution)


def _load_cached_sources_for_paper(rdir: Path, *, paper_id: str, query_key: str) -> list[dict]:
    if not paper_id:
        return []
    history_path = rdir / "deterministic_sources.tsv"
    rows = _load_sources_history(history_path)
    if not rows:
        return []

    by_paper = [row for row in rows if str(row.get("paper_id") or "") == paper_id]
    if not by_paper:
        return []

    by_query = [row for row in by_paper if str(row.get("query_key") or "") == query_key]
    selected = by_query or by_paper

    deduped: dict[str, dict] = {}
    for row in reversed(selected):
        cache_key = _source_cache_key(row)
        if cache_key in deduped:
            continue
        restored = {field: row.get(field, "") for field in SOURCE_FIELDS}
        restored["keywords"] = _decode_pipe_list(restored.get("keywords", ""))
        restored["categories"] = _decode_pipe_list(restored.get("categories", ""))
        deduped[cache_key] = restored

    return sorted(
        deduped.values(),
        key=lambda r: (
            float(r.get("score", 0.0) or 0.0),
            str(r.get("source") or ""),
            str(r.get("source_id") or ""),
        ),
        reverse=True,
    )


def _compact_author(author: dict) -> dict:
    affiliation_count = author.get("affiliation_count")
    if isinstance(affiliation_count, int):
        aff_count = affiliation_count
    else:
        aff_count = len(author.get("affiliations") or [])
    return {
        "author_id": author.get("author_id", ""),
        "name": author.get("name", ""),
        "orcid": author.get("orcid", ""),
        "source_ids": list(author.get("source_ids") or []),
        "affiliation_count": aff_count,
    }


def _truncate_text(value: str, max_chars: int) -> str:
    text = str(value or "").strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _compact_paper_for_index(paper: dict) -> dict:
    authors = [_compact_author(a) for a in (paper.get("authors") or [])]
    preview_limit = 12
    existing_truncated = int(paper.get("authors_truncated") or 0)
    existing_count = int(paper.get("author_count") or 0)
    author_count = max(existing_count, len(authors) + existing_truncated)
    return {
        "paper_id": paper.get("paper_id", ""),
        "title": paper.get("title", ""),
        "venue": paper.get("venue", ""),
        "year": paper.get("year", ""),
        "doi": paper.get("doi", ""),
        "arxiv_id": paper.get("arxiv_id", ""),
        "url": paper.get("url", ""),
        "abstract": _truncate_text(str(paper.get("abstract") or ""), 1200),
        "keywords": list(paper.get("keywords") or []),
        "categories": list(paper.get("categories") or []),
        "author_count": author_count,
        "authors": authors[:preview_limit],
        "authors_truncated": max(0, author_count - min(author_count, preview_limit)),
    }


def _compact_index_payload(index_payload: dict) -> dict:
    index_payload["artifact_type"] = "deterministic_retrieval_index"
    index_payload["schema_version"] = "0.3.0"
    papers = index_payload.get("papers") or []
    compacted: list[dict] = []
    for entry in papers:
        paper = entry.get("paper") or {}
        source_count = max(
            int(entry.get("source_count") or 0),
            len(entry.get("sources") or []),
        )
        compact_entry = {k: v for k, v in entry.items() if k not in {"sources", "diagnostics"}}
        compacted.append(
            {
                **compact_entry,
                "paper": _compact_paper_for_index(paper),
                "source_count": source_count,
                # Human-facing YAML omits source rows; full provenance lives in deterministic_sources.tsv.
                "sources_truncated": source_count,
            }
        )
    index_payload["papers"] = compacted
    return index_payload


def _append_query_event(index_payload: dict, *, query: dict, policy: str, result: dict, cache_hit: bool) -> None:
    query_key = canonical_query_key(query)
    paper_id = ((result.get("paper") or {}).get("paper_id") or "")
    index_payload.setdefault("queries", []).append(
        {
            "query_key": query_key,
            "query": query,
            "policy": policy,
            "resolution_status": result.get("resolution_status", result.get("status", "")),
            "paper_id": paper_id,
            "reason": result.get("reason", ""),
            "cache_hit": bool(cache_hit),
            "timestamp": _utc_now(),
        }
    )


def _upsert_paper_entry(index_payload: dict, *, query_key: str, result: dict, policy: str) -> None:
    if result.get("status") != "resolved" or not result.get("paper"):
        return
    paper = _compact_paper_for_index(result["paper"])
    paper_id = paper["paper_id"]
    papers = index_payload.setdefault("papers", [])
    existing = next((p for p in papers if (p.get("paper") or {}).get("paper_id") == paper_id), None)
    if existing is None:
        existing = _find_equivalent_paper_entry(papers, paper)
    if existing is None:
        source_count = len(result.get("sources") or [])
        papers.append(
            {
                "paper_id": paper_id,
                "paper": paper,
                "query_keys": sorted({query_key}),
                "search_trace": result.get("search_trace", []),
                "source_count": source_count,
                # Human-facing YAML omits source rows; full provenance lives in deterministic_sources.tsv.
                "sources_truncated": source_count,
                "policy_last_used": policy,
                "first_seen_at": _utc_now(),
                "last_seen_at": _utc_now(),
            }
        )
        return

    if paper_id != (existing.get("paper") or {}).get("paper_id"):
        paper["paper_id"] = (existing.get("paper") or {}).get("paper_id") or paper_id
    existing["paper"] = paper
    keys = set(existing.get("query_keys") or [])
    keys.add(query_key)
    keys |= _paper_keys(paper)
    existing["query_keys"] = sorted(keys)
    existing["search_trace"] = result.get("search_trace", [])
    existing["source_count"] = max(
        int(existing.get("source_count") or len(existing.get("sources") or [])),
        len(result.get("sources") or []),
    )
    existing["sources_truncated"] = int(existing["source_count"])
    existing.pop("sources", None)
    existing.pop("diagnostics", None)
    existing["policy_last_used"] = policy
    existing["last_seen_at"] = _utc_now()


def _persist_resolved(result: dict) -> None:
    if result.get("status") != "resolved" or not result.get("paper"):
        return
    paper = result["paper"]

    init_kb()
    upsert_paper(
        {
            "id": paper["paper_id"],
            "title": paper.get("title"),
            "venue": paper.get("venue"),
            "year": int(paper["year"]) if str(paper.get("year", "")).isdigit() else None,
            "doi": paper.get("doi") or None,
            "arxiv_id": paper.get("arxiv_id") or None,
            "abstract": paper.get("abstract") or None,
            "keywords": list(paper.get("keywords") or []),
            "categories": list(paper.get("categories") or []),
            "html_url": paper.get("url") or None,
        }
    )
    add_provenance(
        entity_id=paper["paper_id"],
        entity_type="paper",
        source="resolver",
        source_key=paper.get("doi") or paper.get("arxiv_id") or paper["paper_id"],
        raw_ref={"status": result.get("status"), "reason": result.get("reason")},
    )

    paper_id = paper["paper_id"]
    existing_author_ids = set(list_paper_author_ids(paper_id))
    current_authors = list(paper.get("authors") or [])
    current_positions: list[tuple[str, int | None]] = []
    current_author_ids: set[str] = set()
    author_org_edges: dict[str, list[tuple[str, str]]] = {}
    author_metadata_rows: list[dict] = []
    for pos, author in enumerate(current_authors):
        author_id = str(author.get("author_id") or "")
        if not author_id:
            continue
        current_positions.append((author_id, pos))
        current_author_ids.add(author_id)
        author_metadata_rows.append(
            {
                "author_id": author_id,
                "source_ids": list(author.get("source_ids") or []),
                "affiliations": list(author.get("affiliations") or []),
            }
        )
    replace_paper_authors(paper_id, current_positions)
    replace_paper_author_metadata(paper_id, author_metadata_rows)

    for pos, author in enumerate(current_authors):
        author_id = str(author.get("author_id") or "")
        if not author_id:
            continue
        upsert_author({"id": author_id, "name": author.get("name"), "orcid": author.get("orcid") or None})
        add_provenance(
            entity_id=author_id,
            entity_type="author",
            source="resolver",
            source_key=author.get("orcid") or "|".join(author.get("source_ids") or []) or author_id,
            raw_ref={"paper_id": paper_id},
        )
        add_provenance(
            entity_id=f"{paper_id}::{author_id}",
            entity_type="paper_author",
            source="resolver",
            source_key=str(pos),
            raw_ref={},
        )
        org_edges: list[tuple[str, str]] = []
        for aff in author.get("affiliations") or []:
            org_id = str(aff.get("org_id") or "")
            if not org_id:
                continue
            upsert_org({"id": org_id, "name": aff.get("name"), "ror": aff.get("ror") or None, "country": aff.get("country") or None})
            org_edges.append((org_id, ""))
            add_provenance(
                entity_id=org_id,
                entity_type="org",
                source="resolver",
                source_key=aff.get("ror") or aff.get("name") or org_id,
                raw_ref={"author_id": author_id},
            )
            add_provenance(
                entity_id=f"{author_id}::{org_id}",
                entity_type="author_org",
                source="resolver",
                source_key=aff.get("ror") or aff.get("name") or "",
                raw_ref={},
            )
        author_org_edges[author_id] = org_edges

    for author_id in current_author_ids:
        if count_papers_for_author(author_id) <= 1:
            replace_author_orgs(author_id, author_org_edges.get(author_id, []))
            continue
        for org_id, role in author_org_edges.get(author_id, []):
            upsert_author_org(author_id, org_id, role=role)

    stale_author_ids = existing_author_ids - current_author_ids
    for author_id in stale_author_ids:
        if count_papers_for_author(author_id) == 0:
            replace_author_orgs(author_id, [])

    for source_row in result.get("sources") or []:
        if source_row.get("source"):
            add_provenance(
                entity_id=paper_id,
                entity_type="paper",
                source=source_row.get("source", "unknown"),
                source_key=source_row.get("source_id") or source_row.get("doi") or source_row.get("arxiv_id") or "",
                raw_ref={"title": source_row.get("title"), "year": source_row.get("year")},
            )


def run_retrieve_paper(
    project_id: str,
    *,
    title: str = "",
    doi: str = "",
    arxiv_url: str = "",
    arxiv_id: str = "",
    policy: str = "",
    progress_callback: ProgressCallback | None = None,
) -> Path:
    if not (title or doi or arxiv_url or arxiv_id):
        raise ValueError("Provide at least one of: title, doi, arxiv_url, arxiv_id")

    pdir = project_dir(project_id)
    pmeta = load(pdir / "project.yaml")
    retrieval_cfg = pmeta.get("retrieval", {})
    deterministic_cfg = retrieval_cfg.get("deterministic", {})
    configured_policy = str(deterministic_cfg.get("policy", "cache_first")).strip().lower()
    requested_policy = str(policy or configured_policy or "cache_first").strip().lower()
    if requested_policy not in {"consensus", "fast", "cache_first"}:
        requested_policy = "cache_first"
    query_mode = "title" if title else ("doi" if doi else ("arxiv" if (arxiv_url or arxiv_id) else "unknown"))
    _emit_progress(
        progress_callback,
        event="retrieve_paper_start",
        query_mode=query_mode,
        requested_policy=requested_policy,
    )
    adapters = build_adapters(pmeta)
    request_payload = {
        "title": title,
        "doi": doi,
        "arxiv_url": arxiv_url,
        "arxiv_id": arxiv_id,
        "policy": requested_policy,
        "ambiguity_delta": float(deterministic_cfg.get("ambiguity_delta", 0.05)),
    }
    rdir = _retrieval_dir(pdir)
    index_payload = _load_index(pdir, policy_default=configured_policy)
    removed_paper_ids = list(index_payload.pop("_removed_paper_ids", []) or [])
    if removed_paper_ids:
        kept_ids = {str((entry.get("paper") or {}).get("paper_id") or "") for entry in (index_payload.get("papers") or [])}
        init_kb()
        deleted = 0
        for paper_id in removed_paper_ids:
            if not paper_id or paper_id in kept_ids:
                continue
            delete_paper(str(paper_id))
            deleted += 1
        if deleted:
            _emit_progress(progress_callback, event="index_cleanup_db_deleted", deleted_papers=deleted)
    query_key = canonical_query_key(request_payload)
    cache_hit = False
    if requested_policy == "cache_first":
        _emit_progress(progress_callback, event="cache_lookup_start", query_key=query_key, cache_layer="db")
        cached_paper = _load_cached_paper_from_db(request_payload)
        if cached_paper:
            cache_hit = True
            cached_paper_id = str(cached_paper.get("paper_id") or "")
            cached_sources = _load_cached_sources_for_paper(rdir, paper_id=cached_paper_id, query_key=query_key)
            _emit_progress(
                progress_callback,
                event="cache_lookup_hit",
                query_key=query_key,
                paper_id=cached_paper_id,
                cache_layer="db",
            )
            result = {
                "status": "resolved",
                "resolution_status": "resolved",
                "query_classification": "cache_first",
                "reason": "cache_hit_db",
                "query": request_payload,
                "paper": cached_paper,
                "sources": cached_sources,
                "diagnostics": {
                    "lookup_mode": "cache",
                    "candidate_count": len(cached_sources),
                    "policy": requested_policy,
                    "effective_policy": "cache_hit",
                    "query_key": query_key,
                    "input_warnings": [],
                    "adapter_calls": [],
                },
                "search_trace": [
                    "query_classification=cache_first",
                    f"cache_hit layer=db paper_id={cached_paper_id}",
                    "resolution_status=resolved reason=cache_hit_db",
                ],
            }
        else:
            _emit_progress(progress_callback, event="cache_lookup_miss", query_key=query_key, cache_layer="db")
            fast_query = dict(request_payload)
            fast_query["policy"] = "fast"
            result = resolve_deterministic(fast_query, adapters, progress_callback=progress_callback)
            if result.get("status") == "resolved" and not _is_paper_complete_for_db_hardening(result.get("paper") or {}):
                _emit_progress(
                    progress_callback,
                    event="cache_miss_fast_incomplete_fallback_consensus",
                    query_key=query_key,
                    paper_id=((result.get("paper") or {}).get("paper_id") or ""),
                )
                consensus_query = dict(request_payload)
                consensus_query["policy"] = "consensus"
                result = resolve_deterministic(consensus_query, adapters, progress_callback=progress_callback)
                trace = list(result.get("search_trace") or [])
                trace.insert(0, "resolution_strategy=cache_miss_fast_incomplete_fallback_consensus")
                result["search_trace"] = trace
    else:
        result = resolve_deterministic(request_payload, adapters, progress_callback=progress_callback)

    if result.get("status") == "resolved" and result.get("paper"):
        candidate = result.get("paper") or {}
        equivalent = _find_equivalent_paper_entry(index_payload.get("papers", []), candidate)
        existing_paper = (equivalent or {}).get("paper") or {}
        canonical_paper_id = str(existing_paper.get("paper_id") or "")
        if canonical_paper_id and canonical_paper_id != str(candidate.get("paper_id") or ""):
            rewritten = dict(candidate)
            rewritten["paper_id"] = canonical_paper_id
            if not rewritten.get("doi") and existing_paper.get("doi"):
                rewritten["doi"] = existing_paper.get("doi")
            if not rewritten.get("arxiv_id") and existing_paper.get("arxiv_id"):
                rewritten["arxiv_id"] = existing_paper.get("arxiv_id")
            result = dict(result)
            result["paper"] = rewritten
            trace = list(result.get("search_trace") or [])
            trace.append(f"paper_id_reconciled from={candidate.get('paper_id')} to={canonical_paper_id}")
            result["search_trace"] = trace

    paper_id = ((result.get("paper") or {}).get("paper_id") or "")
    existing_entry = None
    if paper_id:
        existing_entry = next((p for p in index_payload.get("papers", []) if p.get("paper_id") == paper_id), None)
    _emit_progress(
        progress_callback,
        event="reconcile_start",
        status=result.get("status", ""),
        paper_id=paper_id,
        existing_entry=bool(existing_entry),
        source_count=len(result.get("sources") or []),
    )

    if result.get("status") == "resolved":
        _persist_resolved(result)
        _emit_progress(
            progress_callback,
            event="kb_persist_done",
            paper_id=paper_id,
            author_count=len((result.get("paper") or {}).get("authors") or []),
        )
    else:
        _emit_progress(
            progress_callback,
            event="kb_persist_skipped",
            status=result.get("status", ""),
            reason=result.get("reason", ""),
        )

    _upsert_paper_entry(index_payload, query_key=query_key, result=result, policy=requested_policy)
    updated_entry = None
    if paper_id:
        updated_entry = next((p for p in index_payload.get("papers", []) if p.get("paper_id") == paper_id), None)
    _emit_progress(
        progress_callback,
        event="reconcile_done",
        status=result.get("status", ""),
        paper_id=paper_id,
        existing_entry=bool(existing_entry),
        query_keys=len((updated_entry or {}).get("query_keys") or []),
        merged_sources=int((updated_entry or {}).get("source_count") or 0),
        total_papers=len(index_payload.get("papers", [])),
    )

    _append_query_event(index_payload, query=request_payload, policy=requested_policy, result=result, cache_hit=cache_hit)
    request_path, request_latest_path = _write_request_artifacts(
        rdir,
        request_payload=request_payload,
        query_key=query_key,
        result=result,
        cache_hit=cache_hit,
    )
    sources_path, sources_latest_path = _write_sources_artifacts(
        rdir,
        query_key=query_key,
        result=result,
    )
    result_path = _save_index(pdir, index_payload)
    _emit_progress(
        progress_callback,
        event="retrieve_paper_artifacts_written",
        request_path=str(request_path),
        request_latest_path=str(request_latest_path),
        sources_path=str(sources_path),
        sources_latest_path=str(sources_latest_path),
        result_path=str(result_path),
    )
    _emit_progress(
        progress_callback,
        event="retrieve_paper_complete",
        status=result.get("status", ""),
        reason=result.get("reason", ""),
        paper_id=(result.get("paper") or {}).get("paper_id", ""),
    )
    return result_path


def run_retrieve_open(project_id: str, *, prompt: str, top_n: int = 5) -> Path:
    if not prompt.strip():
        raise ValueError("Prompt is required")
    pdir = project_dir(project_id)
    pmeta = load(pdir / "project.yaml")
    retrieval_cfg = pmeta.get("retrieval", {})
    open_enabled = bool(retrieval_cfg.get("open_enabled", False))
    effective_top_n = int(top_n or retrieval_cfg.get("open_top_n", 5))
    adapters = build_adapters(pmeta)

    result = run_open_retrieval(prompt=prompt, adapters=adapters, top_n=effective_top_n)
    raw = result["raw"]
    ranked = result["ranked"]

    rdir = _retrieval_dir(pdir)
    write_candidate_tsv(rdir / "candidates_raw.tsv", raw, include_query=True)
    write_candidate_tsv(rdir / "candidates_ranked.tsv", ranked, include_query=True)

    handoff_rows = []
    if open_enabled:
        for candidate in ranked[:effective_top_n]:
            det = resolve_deterministic(
                {
                    "doi": candidate.get("doi", ""),
                    "arxiv_id": candidate.get("arxiv_id", ""),
                    "title": candidate.get("title", ""),
                    "ambiguity_delta": float((retrieval_cfg.get("deterministic", {}) or {}).get("ambiguity_delta", 0.05)),
                },
                adapters,
            )
            canonical_paper_id = (det.get("paper") or {}).get("paper_id", "")
            handoff_rows.append(
                {
                    "candidate_key": _candidate_dedup_key(candidate),
                    "status": det.get("status", ""),
                    "canonical_paper_id": canonical_paper_id,
                    "reason": det.get("reason", ""),
                }
            )
            if det.get("status") == "resolved":
                _persist_resolved(det)
    else:
        for candidate in ranked[:effective_top_n]:
            handoff_rows.append(
                {
                    "candidate_key": _candidate_dedup_key(candidate),
                    "status": "skipped",
                    "canonical_paper_id": "",
                    "reason": "open_retrieval_feature_flag_disabled",
                }
            )

    summary = {
        "prompt": prompt,
        "query_plan": result.get("query_plan", []),
        "counts": {"raw": len(raw), "ranked": len(ranked), "handoff": len(handoff_rows)},
        "top_n": effective_top_n,
        "open_enabled": open_enabled,
    }
    write_handoff_tsv(rdir / "handoff.tsv", handoff_rows)
    write_yaml(rdir / "candidates_summary.yaml", summary)
    return rdir / "candidates_summary.yaml"
