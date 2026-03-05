from __future__ import annotations

import csv
from difflib import SequenceMatcher
from pathlib import Path

from src.connectors.base import ConnectorConfig
from src.connectors.crossref import CrossrefConnector
from src.connectors.openalex import OpenAlexConnector
from src.connectors.semantic_scholar import SemanticScholarConnector

FIELDS = ["source", "source_id", "paper_id", "title", "venue", "year", "doi", "arxiv_id", "url"]


def _norm_title(value: str) -> str:
    return " ".join("".join(ch.lower() if ch.isalnum() else " " for ch in (value or "")).split())


def _stable_id(row: dict) -> str:
    if row.get("doi"):
        return f"doi:{row['doi']}"
    if row.get("arxiv_id"):
        return f"arxiv:{row['arxiv_id']}"
    return f"title:{_norm_title(row.get('title', ''))}:{row.get('year', '')}"


def deduplicate_candidates(rows: list[dict]) -> list[dict]:
    deduped: list[dict] = []
    for row in rows:
        row = dict(row)
        doi = (row.get("doi") or "").strip().lower()
        arxiv = (row.get("arxiv_id") or "").strip().lower()
        title = _norm_title(row.get("title", ""))
        year = str(row.get("year", ""))

        found = None
        for idx, existing in enumerate(deduped):
            existing_title = _norm_title(existing.get("title", ""))
            if doi and doi == (existing.get("doi") or "").strip().lower():
                found = idx
                break
            if not doi and arxiv and arxiv == (existing.get("arxiv_id") or "").strip().lower():
                found = idx
                break
            if not doi and not arxiv:
                same_year = year and year == str(existing.get("year", ""))
                similar = SequenceMatcher(None, title, existing_title).ratio() >= 0.94
                if same_year and similar:
                    found = idx
                    break

        if found is None:
            row["paper_id"] = _stable_id(row)
            deduped.append(row)
            continue

        existing = deduped[found]
        for key in ("doi", "arxiv_id", "url", "venue"):
            if not existing.get(key) and row.get(key):
                existing[key] = row[key]
        if len(row.get("title", "")) > len(existing.get("title", "")):
            existing["title"] = row["title"]
        if row.get("doi"):
            existing["paper_id"] = f"doi:{row['doi']}"
        elif row.get("arxiv_id") and not existing.get("doi"):
            existing["paper_id"] = f"arxiv:{row['arxiv_id']}"

    deduped.sort(key=lambda r: (r.get("paper_id", ""), r.get("source", "")))
    return deduped


def _write_tsv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, delimiter="\t")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in FIELDS})


def mock_rows(theme: str) -> list[dict]:
    return [
        {
            "source": "mock",
            "source_id": "mock-1",
            "paper_id": "",
            "title": f"{theme} Systems Paper",
            "venue": "NSDI",
            "year": "2024",
            "doi": "10.0000/example1",
            "arxiv_id": "",
            "url": "https://example.org/mock1",
        },
        {
            "source": "mock",
            "source_id": "mock-2",
            "paper_id": "",
            "title": f"{theme} ML Systems Paper",
            "venue": "MLSys",
            "year": "2023",
            "doi": "",
            "arxiv_id": "2401.00001",
            "url": "https://arxiv.org/abs/2401.00001",
        },
    ]


def run_discovery_aggregation(pdir: Path, pmeta: dict) -> tuple[list[dict], list[dict]]:
    discovery_cfg = pmeta.get("discovery", {})
    connectors_cfg = discovery_cfg.get("connectors", {})
    rate_limits = discovery_cfg.get("rate_limits", {})

    theme = pmeta.get("theme", "unknown")
    venues = pmeta.get("venues", [])
    year_min = int(pmeta.get("year_min", 2020))
    year_max = int(pmeta.get("year_max", 2100))
    limit = int(discovery_cfg.get("limit", 25))

    connector_defs = [
        ("openalex", OpenAlexConnector),
        ("crossref", CrossrefConnector),
        ("semantic_scholar", SemanticScholarConnector),
    ]

    all_rows: list[dict] = []
    network_failed = False

    for name, klass in connector_defs:
        settings = connectors_cfg.get(name, {})
        if settings.get("enabled", True) is False:
            continue
        cfg = ConnectorConfig(
            enabled=True,
            rate_limit_per_sec=float(rate_limits.get(name, settings.get("rate_limit_per_sec", 1.0))),
            timeout_s=float(settings.get("timeout_s", 8.0)),
        )
        connector = klass(cfg)
        try:
            rows = connector.search(theme, venues, year_min, year_max, limit)
        except Exception:
            rows = []
            network_failed = True
        for row in rows:
            row["paper_id"] = ""
        _write_tsv(pdir / "artifacts" / "discovery" / f"raw_{name}.tsv", rows)
        all_rows.extend(rows)

    if not all_rows and network_failed:
        all_rows = mock_rows(theme)
        _write_tsv(pdir / "artifacts" / "discovery" / "raw_mock.tsv", all_rows)

    for row in all_rows:
        row["paper_id"] = _stable_id(row)

    merged_path = pdir / "artifacts" / "discovery" / "raw.tsv"
    _write_tsv(merged_path, all_rows)

    deduped = deduplicate_candidates(all_rows)
    dedup_path = pdir / "artifacts" / "discovery" / "deduped.tsv"
    _write_tsv(dedup_path, deduped)
    return all_rows, deduped
