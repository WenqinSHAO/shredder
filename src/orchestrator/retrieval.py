from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

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


def _retrieval_dir(pdir: Path) -> Path:
    return pdir / "artifacts" / "retrieval"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    return payload


def _save_index(pdir: Path, index_payload: dict) -> Path:
    out = _deterministic_index_path(pdir)
    out.parent.mkdir(parents=True, exist_ok=True)
    index_payload["updated_at"] = _utc_now()
    dump_to_path(out, index_payload)
    return out


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
    return sorted(by_key.values(), key=lambda r: ((r.get("source") or ""), (r.get("source_id") or "")))


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
    paper = result["paper"]
    paper_id = paper["paper_id"]
    papers = index_payload.setdefault("papers", [])
    existing = next((p for p in papers if (p.get("paper") or {}).get("paper_id") == paper_id), None)
    if existing is None:
        papers.append(
            {
                "paper_id": paper_id,
                "paper": paper,
                "query_keys": sorted({query_key}),
                "search_trace": result.get("search_trace", []),
                "sources": result.get("sources", []),
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
    existing["sources"] = _merge_sources(existing.get("sources") or [], result.get("sources") or [])
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
        cached = _find_cached_paper(index_payload, query_key, request_payload)
        if cached:
            cache_hit = True
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
            result = resolve_deterministic(request_payload, adapters)
    else:
        result = resolve_deterministic(request_payload, adapters)

    rdir = _retrieval_dir(pdir)
    write_yaml(rdir / "deterministic_request.yaml", request_payload)
    write_sources_tsv(rdir / "deterministic_sources.tsv", result.get("sources") or [])

    if result.get("status") == "resolved":
        _persist_resolved(result)
    _upsert_paper_entry(index_payload, query_key=query_key, result=result, policy=requested_policy)
    _append_query_event(index_payload, query=request_payload, policy=requested_policy, result=result, cache_hit=cache_hit)
    return _save_index(pdir, index_payload)


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
