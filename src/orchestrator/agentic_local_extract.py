from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.orchestrator.agentic_extract_candidates import (
    canonicalize_candidate_title,
    extract_listing_candidates_from_segments,
    extract_year_best,
    to_paper_candidates_from_facts,
)
from src.orchestrator.agentic_search import _peek_text
from src.orchestrator.agentic_text import (
    _extract_html_structural_segments,
    _extract_listing_text_with_fallback,
    _safe_int,
)
from src.retrieval.service import write_yaml
from src.utils.paths import project_dir


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _retrieval_dir(project_path: Path) -> Path:
    return project_path / "artifacts" / "retrieval"


def _agentic_paths(project_path: Path) -> dict[str, Path]:
    retrieval_dir = _retrieval_dir(project_path)
    return {
        "result": retrieval_dir / "agentic_result.yaml",
        "trajectory": retrieval_dir / "agentic_trajectory.yaml",
        "raw": retrieval_dir / "agentic_raw.ndjson",
    }


def run_extract_agentic_local(
    project_id: str,
    *,
    institution: str = "",
    year_gte: int = 0,
    url_contains: str = "",
) -> Path:
    project_path = project_dir(project_id)
    paths = _agentic_paths(project_path)
    fetch_dir = paths["result"].parent / "fetch_raw"
    out_path = paths["result"].parent / "agentic_extract_local.yaml"

    filters: dict[str, Any] = {}
    if str(institution or "").strip():
        filters["institution"] = str(institution).strip()
    if int(year_gte or 0) > 0:
        filters["year_gte"] = int(year_gte)

    url_hint = str(url_contains or "").strip().lower()
    facts: list[dict[str, Any]] = []
    inspected: list[dict[str, Any]] = []
    for idx, path in enumerate(sorted(fetch_dir.glob("*.html")), start=1):
        raw_html = path.read_text(encoding="utf-8", errors="ignore")
        text = _extract_listing_text_with_fallback(raw_html, max_chars=8_000_000)
        segments = _extract_html_structural_segments(raw_html, max_segments=240, max_chars=1800)
        pseudo_url = f"file:{path.name}"
        if url_hint and url_hint not in pseudo_url.lower() and url_hint not in text.lower():
            continue
        year = extract_year_best(text, year_gte=_safe_int(filters.get("year_gte"))) or str(year_gte or "")
        listing_facts = extract_listing_candidates_from_segments(
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

    candidates = to_paper_candidates_from_facts(
        facts,
        canonicalize_candidate_title_fn=canonicalize_candidate_title,
    )
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
