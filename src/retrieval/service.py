from __future__ import annotations

import csv
import hashlib
from difflib import SequenceMatcher
from pathlib import Path
from time import perf_counter
from typing import Callable

from src.connectors.http import normalize_arxiv_id, normalize_doi
from src.retrieval.adapters import (
    AdapterConfig,
    ArxivAdapter,
    HabaneroAdapter,
    PyAlexAdapter,
    RetrievalAdapter,
    SemanticScholarAdapter,
)
from src.utils.yamlx import dump_to_path

SOURCE_FIELDS = [
    "source",
    "source_id",
    "title",
    "venue",
    "year",
    "doi",
    "arxiv_id",
    "url",
    "abstract",
    "keywords",
    "categories",
    "score",
    "reason",
]

ProgressCallback = Callable[[dict], None]


def _emit_progress(progress_callback: ProgressCallback | None, *, event: str, **payload) -> None:
    if progress_callback is None:
        return
    body = {"event": event}
    body.update(payload)
    progress_callback(body)


def normalize_title(value: str) -> str:
    return " ".join("".join(ch.lower() if ch.isalnum() else " " for ch in (value or "")).split())


def normalize_arxiv_input(arxiv_url: str = "", arxiv_id: str = "") -> str:
    if arxiv_id:
        return normalize_arxiv_id(arxiv_id)
    return normalize_arxiv_id(arxiv_url)


def canonical_query_key(normalized_query: dict) -> str:
    doi = normalize_doi(normalized_query.get("doi", ""))
    if doi:
        return f"doi:{doi}"
    arxiv_id = normalize_arxiv_input(normalized_query.get("arxiv_url", ""), normalized_query.get("arxiv_id", ""))
    if arxiv_id:
        return f"arxiv:{arxiv_id}"
    title = normalize_title(str(normalized_query.get("title") or ""))
    if title:
        return f"title:{title}"
    return "empty"


def stable_paper_id(doi: str, arxiv_id: str, title: str, year: str) -> str:
    if doi:
        return f"doi:{doi}"
    if arxiv_id:
        return f"arxiv:{arxiv_id}"
    return f"title:{normalize_title(title)}:{year}"


def stable_org_id(name: str, ror: str, source: str) -> str:
    if ror:
        return f"ror:{ror.lower()}"
    payload = f"{source}|{name.strip().lower()}"
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]
    return f"org:{source}:{digest}"


def stable_author_id(name: str, orcid: str, source_id: str, source: str, affiliations: list[dict]) -> str:
    if orcid:
        return f"orcid:{orcid.lower()}"
    if source_id:
        return f"{source}:{source_id}"
    aff_names = "|".join(sorted({(a.get('name') or '').strip().lower() for a in affiliations if a.get("name")}))
    payload = f"{source}|{name.strip().lower()}|{aff_names}"
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]
    return f"author:{source}:{digest}"


def build_adapters(project_meta: dict | None = None) -> list[RetrievalAdapter]:
    retrieval_cfg = (project_meta or {}).get("retrieval", {})
    adapter_cfg = retrieval_cfg.get("adapters", {})
    return [
        HabaneroAdapter(AdapterConfig(enabled=adapter_cfg.get("habanero", {}).get("enabled", True))),
        ArxivAdapter(AdapterConfig(enabled=adapter_cfg.get("arxiv", {}).get("enabled", True))),
        PyAlexAdapter(AdapterConfig(enabled=adapter_cfg.get("pyalex", {}).get("enabled", True))),
        SemanticScholarAdapter(AdapterConfig(enabled=adapter_cfg.get("semanticscholar", {}).get("enabled", True))),
    ]


def _source_key(row: dict) -> str:
    source = (row.get("source") or "unknown").strip().lower()
    source_id = (row.get("source_id") or "").strip()
    if source_id:
        return f"{source}:{source_id}"
    payload = f"{source}|{row.get('title','')}|{row.get('year','')}|{row.get('doi','')}|{row.get('arxiv_id','')}|{row.get('url','')}"
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]
    return f"{source}:fallback:{digest}"


def _merge_authors(rows: list[dict]) -> list[dict]:
    by_id: dict[str, dict] = {}
    for row in rows:
        source = row.get("source", "unknown")
        for author in row.get("authors") or []:
            affiliations = author.get("affiliations") or []
            author_id = stable_author_id(
                name=str(author.get("name") or ""),
                orcid=str(author.get("orcid") or ""),
                source_id=str(author.get("source_id") or ""),
                source=source,
                affiliations=affiliations,
            )
            record = by_id.setdefault(
                author_id,
                {
                    "author_id": author_id,
                    "name": str(author.get("name") or ""),
                    "orcid": str(author.get("orcid") or ""),
                    "source_ids": [],
                    "affiliations": [],
                },
            )
            source_id = str(author.get("source_id") or "")
            if source_id and source_id not in record["source_ids"]:
                record["source_ids"].append(source_id)
            if not record["name"] and author.get("name"):
                record["name"] = str(author.get("name"))
            if not record["orcid"] and author.get("orcid"):
                record["orcid"] = str(author.get("orcid"))
            for aff in affiliations:
                org_id = stable_org_id(str(aff.get("name") or ""), str(aff.get("ror") or ""), source)
                aff_row = {
                    "org_id": org_id,
                    "name": str(aff.get("name") or ""),
                    "ror": str(aff.get("ror") or ""),
                    "country": str(aff.get("country") or ""),
                }
                if aff_row not in record["affiliations"]:
                    record["affiliations"].append(aff_row)
    return sorted(by_id.values(), key=lambda x: x["author_id"])


def _best_row(rows: list[dict]) -> dict:
    ranked = sorted(
        rows,
        key=lambda r: (
            1 if r.get("doi") else 0,
            1 if r.get("arxiv_id") else 0,
            float(r.get("score", 0.0)),
            len(r.get("title", "")),
        ),
        reverse=True,
    )
    return ranked[0]


def _merge_terms(rows: list[dict], field: str, max_terms: int = 12) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for row in rows:
        values = row.get(field) or []
        if isinstance(values, str):
            values = [values]
        for value in values:
            term = str(value or "").strip()
            if not term:
                continue
            lowered = term.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            out.append(term)
            if len(out) >= max_terms:
                return out
    return out


def _best_abstract(rows: list[dict]) -> str:
    candidates = [str((row.get("abstract") or "")).strip() for row in rows]
    candidates = [text for text in candidates if text]
    if not candidates:
        return ""
    return sorted(candidates, key=len, reverse=True)[0]


def merge_paper_rows(rows: list[dict]) -> dict:
    if not rows:
        return {}
    best = _best_row(rows)
    doi_values = sorted({normalize_doi(r.get("doi", "")) for r in rows if normalize_doi(r.get("doi", ""))})
    arxiv_values = sorted({normalize_arxiv_id(r.get("arxiv_id", "")) for r in rows if normalize_arxiv_id(r.get("arxiv_id", ""))})
    doi = doi_values[0] if doi_values else ""
    arxiv_id = arxiv_values[0] if arxiv_values else ""
    year_values = sorted({str(r.get("year", "")).strip() for r in rows if str(r.get("year", "")).strip()})
    year = year_values[0] if year_values else str(best.get("year") or "")
    title = best.get("title") or ""
    venue = best.get("venue") or ""
    url = best.get("url") or ""
    abstract = _best_abstract(rows)
    keywords = _merge_terms(rows, "keywords", max_terms=12)
    categories = _merge_terms(rows, "categories", max_terms=12)
    paper_id = stable_paper_id(doi=doi, arxiv_id=arxiv_id, title=title, year=year)
    return {
        "paper_id": paper_id,
        "title": title,
        "venue": venue,
        "year": year,
        "doi": doi,
        "arxiv_id": arxiv_id,
        "url": url,
        "abstract": abstract,
        "keywords": keywords,
        "categories": categories,
        "authors": _merge_authors(rows),
    }


def _lookup_mode(query: dict) -> str:
    doi = normalize_doi(query.get("doi", ""))
    arxiv_id = normalize_arxiv_input(query.get("arxiv_url", ""), query.get("arxiv_id", ""))
    title = str(query.get("title") or "")
    if doi:
        return "doi"
    if arxiv_id:
        return "arxiv"
    if title:
        return "title"
    return "none"


def _adapter_diag(adapter: RetrievalAdapter, mode: str, rows_returned: int) -> dict:
    if hasattr(adapter, "diagnostics"):
        diag = adapter.diagnostics()
    else:
        diag = {
            "adapter": getattr(adapter, "name", adapter.__class__.__name__),
            "enabled": True,
            "action": mode,
            "dependency_modules": [],
            "dependency_available": {},
            "missing_dependency": "",
            "error": "",
        }
    if not diag.get("action"):
        diag["action"] = "not_attempted"
    diag.update({"lookup_mode": mode, "rows_returned": rows_returned})
    return diag


def _collect_candidates(
    query: dict,
    adapters: list[RetrievalAdapter],
    policy: str = "consensus",
    progress_callback: ProgressCallback | None = None,
) -> tuple[list[dict], list[dict]]:
    doi = normalize_doi(query.get("doi", ""))
    arxiv_id = normalize_arxiv_input(query.get("arxiv_url", ""), query.get("arxiv_id", ""))
    title = str(query.get("title") or "")
    mode = _lookup_mode(query)
    candidates: list[dict] = []
    diagnostics: list[dict] = []
    stop_early = policy == "fast"
    have_rows = False
    _emit_progress(
        progress_callback,
        event="candidate_collection_start",
        lookup_mode=mode,
        policy=policy,
        adapter_count=len(adapters),
    )
    for index, adapter in enumerate(adapters, start=1):
        adapter_name = getattr(adapter, "name", adapter.__class__.__name__)
        if stop_early and have_rows:
            diagnostics.append(
                {
                    "adapter": adapter_name,
                    "enabled": getattr(getattr(adapter, "config", None), "enabled", True),
                    "action": "skipped_due_fast_policy",
                    "dependency_modules": list(getattr(adapter, "dependency_modules", ()) or []),
                    "dependency_available": {
                        dep: adapter._has_module(dep) if hasattr(adapter, "_has_module") else True
                        for dep in (getattr(adapter, "dependency_modules", ()) or [])
                    },
                    "missing_dependency": "",
                    "error": "",
                    "lookup_mode": mode,
                    "rows_returned": 0,
                }
            )
            _emit_progress(
                progress_callback,
                event="adapter_query_skipped",
                adapter=adapter_name,
                lookup_mode=mode,
                adapter_index=index,
                adapter_total=len(adapters),
                reason="fast_policy_hit_already_found",
            )
            continue
        rows: list[dict] = []
        _emit_progress(
            progress_callback,
            event="adapter_query_start",
            adapter=adapter_name,
            lookup_mode=mode,
            adapter_index=index,
            adapter_total=len(adapters),
        )
        started = perf_counter()
        if doi:
            rows = adapter.lookup_doi(doi)
        elif arxiv_id:
            rows = adapter.lookup_arxiv(arxiv_id)
        elif title:
            rows = adapter.search_title(title, limit=int(query.get("limit", 5)))
        rows = rows or []
        elapsed_ms = int((perf_counter() - started) * 1000)
        candidates.extend(rows)
        adapter_diag = _adapter_diag(adapter, mode, len(rows))
        diagnostics.append(adapter_diag)
        _emit_progress(
            progress_callback,
            event="adapter_query_done",
            adapter=adapter_name,
            lookup_mode=mode,
            adapter_index=index,
            adapter_total=len(adapters),
            rows_returned=len(rows),
            elapsed_ms=elapsed_ms,
            enabled=adapter_diag.get("enabled", True),
            action=adapter_diag.get("action", ""),
            error=adapter_diag.get("error", ""),
            missing_dependency=adapter_diag.get("missing_dependency", ""),
        )
        if rows:
            have_rows = True
    _emit_progress(
        progress_callback,
        event="candidate_collection_done",
        lookup_mode=mode,
        policy=policy,
        candidate_count=len(candidates),
        adapter_calls=len(diagnostics),
    )
    return candidates, diagnostics


def _input_warnings(normalized_query: dict) -> list[str]:
    warnings: list[str] = []
    doi = str(normalized_query.get("doi") or "").lower()
    arxiv_url = str(normalized_query.get("arxiv_url") or "").lower()
    if doi and "arxiv.org/" in doi:
        warnings.append("doi_argument_looks_like_arxiv_url")
    if arxiv_url and "doi.org/" in arxiv_url:
        warnings.append("arxiv_url_argument_looks_like_doi_url")
    if doi and not doi.startswith("10."):
        warnings.append("doi_argument_not_normalized")
    return warnings


def _resolve_title(title: str, candidates: list[dict], ambiguity_delta: float = 0.05) -> tuple[str, str, list[dict]]:
    if not candidates:
        return "not_found", "no_candidates", []
    title_norm = normalize_title(title)
    scored = []
    for row in candidates:
        sim = SequenceMatcher(None, title_norm, normalize_title(row.get("title", ""))).ratio()
        total = float(row.get("score", 0.0)) + sim
        row_copy = dict(row)
        row_copy["_sim"] = sim
        row_copy["_total"] = total
        scored.append(row_copy)
    scored.sort(key=lambda r: (r["_total"], r["_sim"], len(r.get("title", ""))), reverse=True)
    top = scored[0]
    if len(scored) > 1 and abs(top["_total"] - scored[1]["_total"]) <= ambiguity_delta:
        return "ambiguous_requires_selection", "multiple_title_matches", scored
    return "resolved", "title_resolved", scored


def resolve_deterministic(
    query: dict,
    adapters: list[RetrievalAdapter],
    progress_callback: ProgressCallback | None = None,
) -> dict:
    normalized_query = {
        "title": str(query.get("title") or "").strip(),
        "doi": normalize_doi(str(query.get("doi") or "")),
        "arxiv_url": str(query.get("arxiv_url") or "").strip(),
        "arxiv_id": normalize_arxiv_input(str(query.get("arxiv_url") or ""), str(query.get("arxiv_id") or "")),
        "limit": int(query.get("limit", 5)),
    }
    policy = str(query.get("policy") or "consensus").strip().lower()
    if policy not in {"consensus", "fast", "cache_first"}:
        policy = "consensus"
    if policy == "cache_first":
        # Cache behavior is orchestrator-controlled; fallback to consensus retrieval when cache miss occurs.
        effective_policy = "consensus"
    else:
        effective_policy = policy

    lookup_mode = _lookup_mode(normalized_query)
    _emit_progress(
        progress_callback,
        event="resolve_start",
        lookup_mode=lookup_mode,
        policy=policy,
        effective_policy=effective_policy,
        query_key=canonical_query_key(normalized_query),
        has_title=bool(normalized_query["title"]),
        has_doi=bool(normalized_query["doi"]),
        has_arxiv_id=bool(normalized_query["arxiv_id"]),
    )

    input_warnings = _input_warnings(normalized_query)
    if input_warnings:
        _emit_progress(
            progress_callback,
            event="input_warnings",
            warnings=input_warnings,
        )

    candidates, adapter_calls = _collect_candidates(
        normalized_query,
        adapters,
        policy=effective_policy,
        progress_callback=progress_callback,
    )
    diagnostics = {
        "lookup_mode": lookup_mode,
        "candidate_count": len(candidates),
        "policy": policy,
        "effective_policy": effective_policy,
        "query_key": canonical_query_key(normalized_query),
        "input_warnings": input_warnings,
        "adapter_calls": adapter_calls,
    }
    search_trace = [
        f"query_classification={diagnostics['lookup_mode']}",
        (
            "normalized_input "
            f"doi={normalized_query['doi'] or 'n/a'} "
            f"arxiv_id={normalized_query['arxiv_id'] or 'n/a'} "
            f"title_present={bool(normalized_query['title'])}"
        ),
    ]
    if diagnostics["input_warnings"]:
        search_trace.append("input_warnings=" + ",".join(diagnostics["input_warnings"]))
    for call in adapter_calls:
        search_trace.append(
            "adapter="
            f"{call.get('adapter')} action={call.get('action')} enabled={call.get('enabled')} "
            f"rows={call.get('rows_returned')} error={call.get('error') or 'none'}"
        )

    if normalized_query["title"] and not normalized_query["doi"] and not normalized_query["arxiv_id"]:
        status, reason, ranked = _resolve_title(normalized_query["title"], candidates, ambiguity_delta=float(query.get("ambiguity_delta", 0.05)))
        _emit_progress(
            progress_callback,
            event="title_resolution",
            status=status,
            reason=reason,
            candidate_count=len(ranked),
        )
        if status != "resolved":
            search_trace.append(f"resolution_status={status} reason={reason}")
            _emit_progress(
                progress_callback,
                event="resolve_complete",
                status=status,
                reason=reason,
                candidate_count=len(candidates),
            )
            return {
                "status": status,
                "resolution_status": status,
                "query_classification": diagnostics["lookup_mode"],
                "reason": reason,
                "query": normalized_query,
                "paper": None,
                "sources": ranked,
                "diagnostics": diagnostics,
                "search_trace": search_trace,
            }
        merged = merge_paper_rows([ranked[0]])
        search_trace.append(f"resolution_status=resolved reason={reason}")
        _emit_progress(
            progress_callback,
            event="resolve_complete",
            status="resolved",
            reason=reason,
            candidate_count=len(candidates),
        )
        return {
            "status": "resolved",
            "resolution_status": "resolved",
            "query_classification": diagnostics["lookup_mode"],
            "reason": reason,
            "query": normalized_query,
            "paper": merged,
            "sources": ranked,
            "diagnostics": diagnostics,
            "search_trace": search_trace,
        }

    if not candidates:
        search_trace.append("resolution_status=not_found reason=no_candidates")
        _emit_progress(
            progress_callback,
            event="resolve_complete",
            status="not_found",
            reason="no_candidates",
            candidate_count=0,
        )
        return {
            "status": "not_found",
            "resolution_status": "not_found",
            "query_classification": diagnostics["lookup_mode"],
            "reason": "no_candidates",
            "query": normalized_query,
            "paper": None,
            "sources": [],
            "diagnostics": diagnostics,
            "search_trace": search_trace,
        }

    merged = merge_paper_rows(candidates)
    search_trace.append("resolution_status=resolved reason=resolved_by_identifier")
    _emit_progress(
        progress_callback,
        event="resolve_complete",
        status="resolved",
        reason="resolved_by_identifier",
        candidate_count=len(candidates),
    )
    return {
        "status": "resolved",
        "resolution_status": "resolved",
        "query_classification": diagnostics["lookup_mode"],
        "reason": "resolved_by_identifier",
        "query": normalized_query,
        "paper": merged,
        "sources": candidates,
        "diagnostics": diagnostics,
        "search_trace": search_trace,
    }


def _candidate_dedup_key(row: dict) -> str:
    doi = normalize_doi(row.get("doi", ""))
    if doi:
        return f"doi:{doi}"
    arxiv = normalize_arxiv_id(row.get("arxiv_id", ""))
    if arxiv:
        return f"arxiv:{arxiv}"
    return f"title:{normalize_title(row.get('title', ''))}:{row.get('year', '')}"


def _rank_candidates(rows: list[dict]) -> list[dict]:
    best_by_key: dict[str, dict] = {}
    for row in rows:
        key = _candidate_dedup_key(row)
        prev = best_by_key.get(key)
        if prev is None or float(row.get("score", 0.0)) > float(prev.get("score", 0.0)):
            best_by_key[key] = row
    ranked = sorted(
        best_by_key.values(),
        key=lambda r: (
            float(r.get("score", 0.0)),
            1 if r.get("doi") else 0,
            1 if r.get("arxiv_id") else 0,
            len(r.get("title", "")),
        ),
        reverse=True,
    )
    return ranked


def plan_queries(prompt: str) -> list[dict]:
    base = prompt.strip()
    items = [base, f"{base} research paper", f"{base} arxiv"]
    out: list[dict] = []
    seen = set()
    for query in items:
        normalized = query.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        out.append({"query": normalized, "connector_scope": "all", "intent": "maximize_recall"})
    return out


def run_open_retrieval(prompt: str, adapters: list[RetrievalAdapter], top_n: int = 5) -> dict:
    queries = plan_queries(prompt)
    raw: list[dict] = []
    for item in queries:
        q = item["query"]
        for adapter in adapters:
            for row in adapter.search_open(q, limit=max(top_n * 4, 20)):
                row_copy = dict(row)
                row_copy["query_used"] = q
                raw.append(row_copy)
    ranked = _rank_candidates(raw)
    return {"query_plan": queries, "raw": raw, "ranked": ranked, "top_n": top_n}


def _tsv_value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "|".join(str(v) for v in value if str(v).strip())
    return str(value)


def write_sources_tsv(path: Path, rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=SOURCE_FIELDS, delimiter="\t")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: _tsv_value(row.get(k, "")) for k in SOURCE_FIELDS})
    return path


def write_candidate_tsv(path: Path, rows: list[dict], include_query: bool = False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["query_used"] + SOURCE_FIELDS if include_query else SOURCE_FIELDS
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: _tsv_value(row.get(k, "")) for k in fields})
    return path


def write_yaml(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    dump_to_path(path, payload)
    return path


def write_handoff_tsv(path: Path, rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["candidate_key", "status", "canonical_paper_id", "reason"]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fields})
    return path
