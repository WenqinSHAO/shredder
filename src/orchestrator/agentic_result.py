from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.connectors.http import normalize_arxiv_id, normalize_doi
from src.orchestrator.agentic_search import (
    _host_from_url,
    _normalize_title_for_key,
    _peek_text,
    _rank_candidates,
    _strip_listing_author_tail,
)
from src.retrieval.service import write_yaml


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _requires_venue_evidence(user_prompt: str, plan_state: dict) -> bool:
    text = str(user_prompt or "").lower()
    if "conference" in text or "symposium" in text or "venue" in text:
        return True
    for cue in list(plan_state.get("cue_breakdown") or []):
        cue_text = str(cue or "").lower()
        if cue_text.strip() == "venue":
            return True
        if cue_text.startswith("venue:") and "none" not in cue_text and "null" not in cue_text:
            return True
    return False


def _has_venue_evidence(candidate: dict) -> bool:
    url = str(candidate.get("url") or "").lower()
    title = str(candidate.get("title") or "").lower()
    abstract = str(candidate.get("abstract") or "").lower()
    host = _host_from_url(url)
    if any(token in url for token in ("/conference/", "/proceedings/", "/rec/conf/", "/technical-sessions", "/accepted", "/program")):
        return True
    if any(token in title for token in ("proceedings", "conference", "symposium", "technical sessions", "accepted papers", "program")):
        return True
    if any(token in abstract for token in ("proceedings", "conference", "symposium", "technical sessions", "accepted papers")):
        return True
    if any(token in host for token in ("usenix.org", "acm.org", "ieee.org", "openreview.net", "dblp.org")) and "conf" in url:
        return True
    return False


def _merge_paper_candidates(existing: list[dict], incoming: list[dict], *, top_n: int | None = None) -> list[dict]:
    merged = _rank_candidates([*(existing or []), *(incoming or [])])
    if top_n is None:
        return merged
    return merged[: max(1, top_n)]


def _persist_result(paths: dict[str, Path], result_payload: dict) -> None:
    write_yaml(paths["result"], result_payload)


def _compact_result_payload(
    *,
    run_result: dict,
    paper_state: dict,
    coverage_summary: dict | None,
    prompt: str,
    llm_model: str,
    display_top_n: int,
) -> dict:
    final_candidates = [row for row in (paper_state.get("final_candidates") or []) if isinstance(row, dict)]
    final_unique_title_keys = {
        _normalize_title_for_key(_strip_listing_author_tail(str(row.get("title") or "")))
        for row in final_candidates
        if str(row.get("title") or "").strip()
    }
    final_unique_title_keys.discard("")
    support_scores = [float(row.get("score") or 0.0) for row in final_candidates]
    papers: list[dict[str, Any]] = []
    for row in final_candidates:
        arxiv_id = normalize_arxiv_id(str(row.get("arxiv_id") or ""))
        papers.append(
            {
                "title": str(row.get("title") or "").strip(),
                "doi": normalize_doi(str(row.get("doi") or "")),
                "arxiv_url": f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else "",
                "source_url": str(row.get("url") or ""),
                "venue": str(row.get("venue") or ""),
                "year": str(row.get("year") or ""),
                "authors": str(row.get("authors") or ""),
                "affiliations": str(row.get("affiliations") or ""),
                "author_affiliations": [dict(item) for item in (row.get("author_affiliations") or []) if isinstance(item, dict)],
                "authors_with_affiliations": str(row.get("authors_with_affiliations") or ""),
                "abstract": str(row.get("abstract") or row.get("abstract_snippet") or ""),
                "abstract_snippet": _peek_text(str(row.get("abstract_snippet") or row.get("abstract") or ""), 320),
                "confidence": float(row.get("score") or 0.0),
                "evidence_ref": str(row.get("source_id") or ""),
            }
        )
    if int(display_top_n or 0) > 0:
        papers = papers[: int(display_top_n)]
    coverage = coverage_summary if isinstance(coverage_summary, dict) else {}
    return {
        "artifact_type": "agentic_result",
        "schema_version": "0.2.0",
        "query": prompt,
        "status": str(run_result.get("status") or ""),
        "stop_reason": str(run_result.get("stop_reason") or ""),
        "cycle_count": int(run_result.get("cycle_count") or 0),
        "model": llm_model,
        "trajectory_ref": "agentic_trajectory.yaml",
        "raw_trace_ref": "agentic_raw.ndjson",
        "coverage": {
            "shortlisted_urls_total": int(coverage.get("shortlisted_urls_total") or 0),
            "shortlisted_urls_complete": int(coverage.get("shortlisted_urls_complete") or 0),
            "shortlisted_urls_with_more_results": int(coverage.get("shortlisted_urls_with_more_results") or 0),
            "url_checks": [dict(row) for row in (coverage.get("url_checks") or []) if isinstance(row, dict)],
            "final_match_count": len(final_candidates),
            "final_unique_title_count": len(final_unique_title_keys),
            "support_score_avg": round(sum(support_scores) / len(support_scores), 4) if support_scores else 0.0,
            "support_score_min": round(min(support_scores), 4) if support_scores else 0.0,
            "support_score_max": round(max(support_scores), 4) if support_scores else 0.0,
        },
        "papers": papers,
        "updated_at": _utc_now(),
    }


def _cleanup_agentic_artifacts(paths: dict[str, Path]) -> None:
    parent = paths["result"].parent
    keep = {paths["result"].name, paths["trajectory"].name, paths["raw"].name, "fetch_raw"}
    for p in parent.glob("agentic_*"):
        if p.name in keep:
            continue
        try:
            if p.is_file():
                p.unlink()
        except OSError:
            continue
