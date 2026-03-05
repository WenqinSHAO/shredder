from __future__ import annotations

import csv
import hashlib
import json
from difflib import SequenceMatcher
from pathlib import Path

from src.connectors.base import ConnectorConfig
from src.connectors.crossref import CrossrefConnector
from src.connectors.openalex import OpenAlexConnector
from src.connectors.searxng import SearxngConnector
from src.connectors.semantic_scholar import SemanticScholarConnector
from src.connectors.http import normalize_arxiv_id, normalize_doi

FIELDS = ["source", "source_id", "paper_id", "title", "venue", "year", "doi", "arxiv_id", "url"]


def _norm_title(value: str) -> str:
    return " ".join("".join(ch.lower() if ch.isalnum() else " " for ch in (value or "")).split())


def raw_row_key(row: dict) -> str:
    source = (row.get("source") or "unknown").strip().lower()
    source_id = (row.get("source_id") or "").strip()
    if source_id:
        return f"{source}:{source_id}"
    fallback_payload = {
        "title": row.get("title", ""),
        "year": str(row.get("year", "")),
        "doi": normalize_doi(row.get("doi", "")),
        "arxiv_id": normalize_arxiv_id(row.get("arxiv_id", "")),
        "url": row.get("url", ""),
    }
    digest = hashlib.sha1(json.dumps(fallback_payload, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    return f"{source}:fallback:{digest}"


def _stable_id(doi: str, arxiv_id: str, title: str, year: str) -> str:
    if doi:
        return f"doi:{doi}"
    if arxiv_id:
        return f"arxiv:{arxiv_id}"
    return f"title:{title}:{year}"


def deduplicate_candidates(rows: list[dict]) -> tuple[list[dict], dict[str, str]]:
    prepared = []
    for idx, row in enumerate(rows):
        title = _norm_title(row.get("title", ""))
        year = str(row.get("year", ""))
        prepared.append(
            {
                "idx": idx,
                "row": dict(row),
                "doi": normalize_doi(row.get("doi", "")),
                "arxiv": normalize_arxiv_id(row.get("arxiv_id", "")),
                "title": title,
                "year": year,
            }
        )

    parent = list(range(len(prepared)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra == rb:
            return
        if ra < rb:
            parent[rb] = ra
        else:
            parent[ra] = rb

    doi_owner: dict[str, int] = {}
    arxiv_owner: dict[str, int] = {}
    for item in prepared:
        idx = item["idx"]
        if item["doi"]:
            prev = doi_owner.get(item["doi"])
            if prev is not None:
                union(idx, prev)
            doi_owner[item["doi"]] = idx
        if item["arxiv"]:
            prev = arxiv_owner.get(item["arxiv"])
            if prev is not None:
                union(idx, prev)
            arxiv_owner[item["arxiv"]] = idx

    for i in range(len(prepared)):
        for j in range(i + 1, len(prepared)):
            a = prepared[i]
            b = prepared[j]
            if not a["title"] or not b["title"]:
                continue
            if not a["year"] or a["year"] != b["year"]:
                continue
            if SequenceMatcher(None, a["title"], b["title"]).ratio() >= 0.94:
                union(a["idx"], b["idx"])

    clusters: dict[int, list[dict]] = {}
    for item in prepared:
        clusters.setdefault(find(item["idx"]), []).append(item)

    deduped: list[dict] = []
    raw_to_canonical: dict[str, str] = {}

    for root in sorted(clusters):
        members = sorted(clusters[root], key=lambda m: raw_row_key(m["row"]))
        dois = sorted({m["doi"] for m in members if m["doi"]})
        arxivs = sorted({m["arxiv"] for m in members if m["arxiv"]})
        canonical_doi = dois[0] if dois else ""
        canonical_arxiv = arxivs[0] if arxivs else ""

        best_title = ""
        for m in members:
            candidate = m["row"].get("title", "")
            if len(candidate) > len(best_title) or (len(candidate) == len(best_title) and candidate < best_title):
                best_title = candidate
        canonical_year = ""
        for m in members:
            year = m["year"]
            if year and (not canonical_year or year < canonical_year):
                canonical_year = year

        canonical_id = _stable_id(canonical_doi, canonical_arxiv, _norm_title(best_title), canonical_year)

        merged = {k: "" for k in FIELDS}
        merged.update({
            "paper_id": canonical_id,
            "source": members[0]["row"].get("source", ""),
            "source_id": members[0]["row"].get("source_id", ""),
            "title": best_title,
            "year": canonical_year,
            "doi": canonical_doi,
            "arxiv_id": canonical_arxiv,
        })
        for field in ("venue", "url"):
            values = sorted({(m["row"].get(field) or "") for m in members if m["row"].get(field)})
            merged[field] = values[0] if values else ""

        deduped.append(merged)
        for m in members:
            raw_to_canonical[raw_row_key(m["row"])] = canonical_id

    deduped.sort(key=lambda r: (r.get("paper_id", ""), r.get("source", "")))
    return deduped, raw_to_canonical


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


def run_discovery_aggregation(pdir: Path, pmeta: dict) -> tuple[list[dict], list[dict], dict[str, str]]:
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
        ("searxng", SearxngConnector),
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
            base_url=str(settings.get("base_url", "")),
        )
        connector = klass(cfg)
        if name == "searxng" and not connector.has_endpoint():
            continue
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
        row["paper_id"] = _stable_id(
            normalize_doi(row.get("doi", "")),
            normalize_arxiv_id(row.get("arxiv_id", "")),
            _norm_title(row.get("title", "")),
            str(row.get("year", "")),
        )

    merged_path = pdir / "artifacts" / "discovery" / "raw.tsv"
    _write_tsv(merged_path, all_rows)

    deduped, raw_to_canonical = deduplicate_candidates(all_rows)
    dedup_path = pdir / "artifacts" / "discovery" / "deduped.tsv"
    _write_tsv(dedup_path, deduped)
    return all_rows, deduped, raw_to_canonical
