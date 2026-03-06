from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from src.kb import (
    add_provenance,
    init_kb,
    upsert_author,
    upsert_author_org,
    upsert_org,
    upsert_paper,
    upsert_paper_author,
)
from src.retrieval.service import (
    SOURCE_FIELDS,
    _candidate_dedup_key,
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
        "schema_version": "0.2.0",
        "updated_at": _utc_now(),
        "policy_default": policy_default,
        "papers": [],
        "queries": [],
    }


def _load_index(pdir: Path, policy_default: str) -> dict:
    path = _deterministic_index_path(pdir)
    if not path.exists():
        return _empty_index(policy_default)
    payload = load(path)
    if not isinstance(payload, dict) or "papers" not in payload or "queries" not in payload:
        return _empty_index(policy_default)
    return _compact_index_payload(payload)


def _save_index(pdir: Path, index_payload: dict) -> Path:
    out = _deterministic_index_path(pdir)
    out.parent.mkdir(parents=True, exist_ok=True)
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
    if paper.get("doi"):
        keys.add(f"doi:{paper['doi']}")
    if paper.get("arxiv_id"):
        keys.add(f"arxiv:{paper['arxiv_id']}")
    title = normalize_title(str(paper.get("title") or ""))
    if title:
        keys.add(f"title:{title}")
    return keys


def _find_cached_paper(index_payload: dict, query_key: str, query: dict) -> dict | None:
    for entry in index_payload.get("papers", []):
        keys = set(entry.get("query_keys") or [])
        paper = entry.get("paper") or {}
        keys |= _paper_keys(paper)
        if query_key in keys:
            return entry
        doi = (query.get("doi") or "").strip().lower()
        if doi and (paper.get("doi") or "").strip().lower() == doi:
            return entry
        arxiv_id = (query.get("arxiv_id") or "").strip().lower()
        if arxiv_id and (paper.get("arxiv_id") or "").strip().lower() == arxiv_id:
            return entry
    return None


def _merge_sources(existing: list[dict], incoming: list[dict]) -> list[dict]:
    by_key: dict[str, dict] = {}
    for row in existing + incoming:
        source = (row.get("source") or "unknown").strip().lower()
        source_id = str(row.get("source_id") or "").strip()
        key = f"{source}:{source_id}" if source_id else f"{source}:{row.get('doi','')}:{row.get('arxiv_id','')}:{row.get('title','')}"
        by_key[key] = row
    return sorted(
        by_key.values(),
        key=lambda r: (
            float(r.get("score", 0.0)),
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


def _compact_source_row_for_index(row: dict) -> dict:
    return {
        "source": row.get("source", ""),
        "source_id": row.get("source_id", ""),
        "title": row.get("title", ""),
        "venue": row.get("venue", ""),
        "year": row.get("year", ""),
        "doi": row.get("doi", ""),
        "arxiv_id": row.get("arxiv_id", ""),
        "url": row.get("url", ""),
        "abstract": _truncate_text(str(row.get("abstract") or ""), 800),
        "keywords": list(row.get("keywords") or []),
        "categories": list(row.get("categories") or []),
        "score": row.get("score", 0.0),
        "reason": row.get("reason", ""),
    }


def _compact_sources_for_index(rows: list[dict], max_rows: int = 8) -> tuple[list[dict], int]:
    compacted = [_compact_source_row_for_index(row) for row in rows]
    total = len(compacted)
    return compacted[:max_rows], max(0, total - max_rows)


def _compact_index_payload(index_payload: dict) -> dict:
    papers = index_payload.get("papers") or []
    compacted: list[dict] = []
    for entry in papers:
        paper = entry.get("paper") or {}
        original_count = int(entry.get("source_count") or len(entry.get("sources") or []))
        compact_sources, sources_truncated = _compact_sources_for_index(entry.get("sources") or [])
        source_count = max(original_count, len(entry.get("sources") or []))
        sources_truncated = max(sources_truncated, source_count - len(compact_sources))
        compacted.append(
            {
                **entry,
                "paper": _compact_paper_for_index(paper),
                "source_count": source_count,
                "sources_truncated": sources_truncated,
                "sources": compact_sources,
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
        compact_sources, sources_truncated = _compact_sources_for_index(result.get("sources") or [])
        papers.append(
            {
                "paper_id": paper_id,
                "paper": paper,
                "query_keys": sorted({query_key}),
                "search_trace": result.get("search_trace", []),
                "source_count": len(result.get("sources") or []),
                "sources_truncated": sources_truncated,
                "sources": compact_sources,
                "diagnostics": result.get("diagnostics", {}),
                "policy_last_used": policy,
                "first_seen_at": _utc_now(),
                "last_seen_at": _utc_now(),
            }
        )
        return

    existing["paper"] = paper
    keys = set(existing.get("query_keys") or [])
    keys.add(query_key)
    keys |= _paper_keys(paper)
    existing["query_keys"] = sorted(keys)
    existing["search_trace"] = result.get("search_trace", [])
    merged_sources = _merge_sources(
        existing.get("sources") or [],
        [_compact_source_row_for_index(r) for r in (result.get("sources") or [])],
    )
    existing["source_count"] = len(merged_sources)
    existing["sources"], existing["sources_truncated"] = _compact_sources_for_index(merged_sources)
    existing["diagnostics"] = result.get("diagnostics", {})
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

    for pos, author in enumerate(paper.get("authors") or []):
        author_id = author["author_id"]
        upsert_author({"id": author_id, "name": author.get("name"), "orcid": author.get("orcid") or None})
        upsert_paper_author(paper["paper_id"], author_id, position=pos)
        add_provenance(
            entity_id=author_id,
            entity_type="author",
            source="resolver",
            source_key=author.get("orcid") or "|".join(author.get("source_ids") or []) or author_id,
            raw_ref={"paper_id": paper["paper_id"]},
        )
        add_provenance(
            entity_id=f"{paper['paper_id']}::{author_id}",
            entity_type="paper_author",
            source="resolver",
            source_key=str(pos),
            raw_ref={},
        )
        for aff in author.get("affiliations") or []:
            org_id = aff["org_id"]
            upsert_org({"id": org_id, "name": aff.get("name"), "ror": aff.get("ror") or None, "country": aff.get("country") or None})
            upsert_author_org(author_id, org_id)
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

    for source_row in result.get("sources") or []:
        if source_row.get("source"):
            add_provenance(
                entity_id=paper["paper_id"],
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
    index_payload = _load_index(pdir, policy_default=configured_policy)
    query_key = canonical_query_key(request_payload)
    cache_hit = False
    if requested_policy == "cache_first":
        _emit_progress(progress_callback, event="cache_lookup_start", query_key=query_key)
        cached = _find_cached_paper(index_payload, query_key, request_payload)
        if cached:
            cache_hit = True
            _emit_progress(
                progress_callback,
                event="cache_lookup_hit",
                query_key=query_key,
                paper_id=cached.get("paper_id"),
            )
            result = {
                "status": "resolved",
                "resolution_status": "resolved",
                "query_classification": "cache_first",
                "reason": "cache_hit",
                "query": request_payload,
                "paper": cached.get("paper"),
                "sources": cached.get("sources") or [],
                "diagnostics": {
                    "lookup_mode": "cache",
                    "candidate_count": len(cached.get("sources") or []),
                    "policy": requested_policy,
                    "effective_policy": "cache_hit",
                    "query_key": query_key,
                    "input_warnings": [],
                    "adapter_calls": [],
                },
                "search_trace": [
                    "query_classification=cache_first",
                    f"cache_hit paper_id={cached.get('paper_id')}",
                    "resolution_status=resolved reason=cache_hit",
                ],
            }
        else:
            _emit_progress(progress_callback, event="cache_lookup_miss", query_key=query_key)
            result = resolve_deterministic(request_payload, adapters, progress_callback=progress_callback)
    else:
        result = resolve_deterministic(request_payload, adapters, progress_callback=progress_callback)

    rdir = _retrieval_dir(pdir)
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
        merged_sources=len((updated_entry or {}).get("sources") or []),
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
