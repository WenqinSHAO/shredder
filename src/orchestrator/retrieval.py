from __future__ import annotations

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
    resolve_deterministic,
    run_open_retrieval,
    write_candidate_tsv,
    write_handoff_tsv,
    write_sources_tsv,
    write_yaml,
)
from src.utils.paths import project_dir
from src.utils.yamlx import load


def _retrieval_dir(pdir: Path) -> Path:
    return pdir / "artifacts" / "retrieval"


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
) -> Path:
    if not (title or doi or arxiv_url or arxiv_id):
        raise ValueError("Provide at least one of: title, doi, arxiv_url, arxiv_id")

    pdir = project_dir(project_id)
    pmeta = load(pdir / "project.yaml")
    retrieval_cfg = pmeta.get("retrieval", {})
    deterministic_cfg = retrieval_cfg.get("deterministic", {})
    adapters = build_adapters(pmeta)
    request_payload = {
        "title": title,
        "doi": doi,
        "arxiv_url": arxiv_url,
        "arxiv_id": arxiv_id,
        "ambiguity_delta": float(deterministic_cfg.get("ambiguity_delta", 0.05)),
    }
    result = resolve_deterministic(request_payload, adapters)

    rdir = _retrieval_dir(pdir)
    write_yaml(rdir / "deterministic_request.yaml", request_payload)
    write_yaml(rdir / "deterministic_result.yaml", result)
    write_sources_tsv(rdir / "deterministic_sources.tsv", result.get("sources") or [])

    if result.get("status") == "resolved":
        _persist_resolved(result)
    return rdir / "deterministic_result.yaml"


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
