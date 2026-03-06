from __future__ import annotations

import csv
import hashlib
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Callable

from src.connectors.base import ConnectorConfig
from src.connectors.http import normalize_arxiv_id, normalize_doi
from src.connectors.searxng import SearxngConnector
from src.retrieval.service import SOURCE_FIELDS, build_adapters, plan_queries, resolve_deterministic, write_yaml
from src.utils.paths import project_dir
from src.utils.yamlx import load

REQUEST_SCHEMA_VERSION = "0.1.0"
SESSION_SCHEMA_VERSION = "0.1.0"
RESULT_SCHEMA_VERSION = "0.1.0"
QUESTIONS_SCHEMA_VERSION = "0.1.0"
ProgressCallback = Callable[[dict], None]

CYCLE_FIELDS = [
    "timestamp",
    "session_id",
    "workflow",
    "cycle_index",
    "state_path",
    "planned_query",
    "retrieval_query",
    "tool_calls",
    "router_decision",
    "fallback_triggered",
    "insufficiency_reason",
    "raw_candidates",
    "ranked_candidates",
    "candidate_delta",
    "decision",
    "decision_reason",
    "stop_reason",
    "question_id",
    "plan_rationale",
]

CANDIDATE_LATEST_FIELDS = [
    "session_id",
    "cycle_index",
    "rank",
    "candidate_key",
    "query_used",
    *SOURCE_FIELDS,
    "selected",
]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _emit_progress(progress_callback: ProgressCallback | None, *, event: str, **payload) -> None:
    if progress_callback is None:
        return
    body = {"event": event}
    body.update(payload)
    progress_callback(body)


def _retrieval_dir(pdir: Path) -> Path:
    return pdir / "artifacts" / "retrieval"


def _agentic_paths(pdir: Path) -> dict[str, Path]:
    rdir = _retrieval_dir(pdir)
    return {
        "request": rdir / "agentic_request.yaml",
        "session": rdir / "agentic_session.yaml",
        "result": rdir / "agentic_result.yaml",
        "questions": rdir / "agentic_questions.yaml",
        "cycles": rdir / "agentic_cycles.tsv",
        "candidates": rdir / "agentic_candidates_latest.tsv",
    }


def _new_session_id(prompt: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    digest = hashlib.sha1(prompt.strip().lower().encode("utf-8")).hexdigest()[:10]
    return f"agentic-{stamp}-{digest}"


def _merge_defaults(default: dict, payload: dict) -> dict:
    merged = dict(default)
    for key, value in payload.items():
        if key not in merged:
            merged[key] = value
            continue
        if isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _merge_defaults(merged[key], value)
            continue
        merged[key] = value
    return merged


def _load_contract(path: Path, default_payload: dict) -> dict:
    if not path.exists():
        return default_payload
    payload = load(path)
    if not isinstance(payload, dict):
        return default_payload
    return _merge_defaults(default_payload, payload)


def _request_contract(
    *,
    session_id: str,
    request_id: str,
    project_id: str,
    prompt: str,
    workflow: str,
    top_n: int,
    max_cycles: int,
) -> dict:
    return {
        "artifact_type": "agentic_request",
        "schema_version": REQUEST_SCHEMA_VERSION,
        "request_id": request_id,
        "session_id": session_id,
        "project_id": project_id,
        "workflow": workflow,
        "prompt": prompt,
        "policy": {
            "retrieval_order": ["scholarly", "web"],
            "web_fallback_mode": "conditional_scaffold",
        },
        "limits": {
            "top_n": top_n,
            "max_cycles": max_cycles,
            "max_questions_per_cycle": 0,
        },
        "created_at": _utc_now(),
        "updated_at": _utc_now(),
    }


def _session_contract(
    *,
    session_id: str,
    request_id: str,
    project_id: str,
    workflow: str,
    max_cycles: int,
) -> dict:
    return {
        "artifact_type": "agentic_session",
        "schema_version": SESSION_SCHEMA_VERSION,
        "session_id": session_id,
        "request_id": request_id,
        "project_id": project_id,
        "workflow": workflow,
        "status": "running",
        "state": "plan",
        "current_cycle": 0,
        "max_cycles": max_cycles,
        "last_decision": "",
        "last_decision_reason": "",
        "stop_reason": "",
        "created_at": _utc_now(),
        "updated_at": _utc_now(),
    }


def _result_contract(
    *,
    session_id: str,
    request_id: str,
    workflow: str,
    top_n: int,
) -> dict:
    return {
        "artifact_type": "agentic_result",
        "schema_version": RESULT_SCHEMA_VERSION,
        "session_id": session_id,
        "request_id": request_id,
        "workflow": workflow,
        "status": "running",
        "stop_reason": "",
        "cycle_count": 0,
        "top_n": top_n,
        "final_candidates": [],
        "decision_history": [],
        "created_at": _utc_now(),
        "updated_at": _utc_now(),
    }


def _questions_contract(*, session_id: str) -> dict:
    return {
        "artifact_type": "agentic_questions",
        "schema_version": QUESTIONS_SCHEMA_VERSION,
        "session_id": session_id,
        "pending": [],
        "history": [],
        "created_at": _utc_now(),
        "updated_at": _utc_now(),
    }


def _candidate_key(row: dict) -> str:
    doi = str(row.get("doi") or "").strip().lower()
    arxiv = str(row.get("arxiv_id") or "").strip().lower()
    title = str(row.get("title") or "").strip().lower()
    year = str(row.get("year") or "").strip()
    if doi:
        return f"doi:{doi}"
    if arxiv:
        return f"arxiv:{arxiv}"
    return f"title:{title}:{year}"


def _append_cycle_row(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists()
    with path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CYCLE_FIELDS, delimiter="\t")
        if not exists:
            writer.writeheader()
        writer.writerow({k: row.get(k, "") for k in CYCLE_FIELDS})


def _write_candidates_latest(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CANDIDATE_LATEST_FIELDS, delimiter="\t")
        writer.writeheader()
        for row in rows:
            payload: dict[str, str] = {}
            for key in CANDIDATE_LATEST_FIELDS:
                value = row.get(key, "")
                if isinstance(value, list):
                    payload[key] = "|".join(str(v) for v in value if str(v).strip())
                else:
                    payload[key] = str(value) if value is not None else ""
            writer.writerow(payload)


def _state_path() -> str:
    return "plan>retrieve>rank>decide"


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _llm_runtime(agentic_cfg: dict) -> dict:
    llm_cfg = agentic_cfg.get("llm") if isinstance(agentic_cfg.get("llm"), dict) else {}
    backend = str(
        llm_cfg.get("backend")
        or agentic_cfg.get("llm_backend")
        or "deepseek"
    ).strip().lower()
    model = str(llm_cfg.get("model") or "").strip()
    api_key_env = str(llm_cfg.get("api_key_env") or "").strip()
    if backend == "deepseek":
        model = model or "deepseek-chat"
        api_key_env = api_key_env or "DS_API_KEY"
    api_key_present = bool(os.environ.get(api_key_env)) if api_key_env else False
    return {
        "backend": backend,
        "model": model,
        "api_key_env": api_key_env,
        "api_key_present": api_key_present,
    }


def _load_cycle_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def _final_candidate_keys(rows: list[dict]) -> list[str]:
    out: list[str] = []
    for row in rows:
        key = str(row.get("candidate_key") or "").strip()
        if key:
            out.append(key)
    return out


def _rank_rows(rows: list[dict]) -> list[dict]:
    best_by_key: dict[str, dict] = {}
    for row in rows:
        key = _candidate_key(row)
        previous = best_by_key.get(key)
        if previous is None or float(row.get("score", 0.0) or 0.0) > float(previous.get("score", 0.0) or 0.0):
            best_by_key[key] = row
    ranked = sorted(
        best_by_key.values(),
        key=lambda row: (
            float(row.get("score", 0.0) or 0.0),
            1 if row.get("doi") else 0,
            1 if row.get("arxiv_id") else 0,
            len(str(row.get("title") or "")),
        ),
        reverse=True,
    )
    return ranked


def _extract_prompt_signals(prompt: str) -> dict:
    text = str(prompt or "").strip()
    doi_matches = re.findall(r"(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)", text, flags=re.IGNORECASE)
    dois: list[str] = []
    for doi in doi_matches:
        cleaned = normalize_doi(doi.rstrip(".,);]"))
        if cleaned and cleaned not in dois:
            dois.append(cleaned)

    arxiv_urls = re.findall(r"(https?://arxiv\.org/(?:abs|pdf)/[^\s]+)", text, flags=re.IGNORECASE)
    arxiv_ids: list[str] = []
    for url in arxiv_urls:
        aid = normalize_arxiv_id(url)
        if aid and aid not in arxiv_ids:
            arxiv_ids.append(aid)

    direct_arxiv = re.findall(r"\b(\d{4}\.\d{4,5}(?:v\d+)?)\b", text)
    for raw in direct_arxiv:
        aid = normalize_arxiv_id(raw)
        if aid and aid not in arxiv_ids:
            arxiv_ids.append(aid)

    quoted_titles = re.findall(r"\"([^\"]{8,220})\"", text)
    title_hint = quoted_titles[0].strip() if quoted_titles else ""
    return {
        "dois": dois,
        "arxiv_ids": arxiv_ids,
        "title_hint": title_hint,
        "has_identifier": bool(dois or arxiv_ids),
    }


def _derive_query_seed(prompt: str, signals: dict) -> str:
    seed = str(prompt or "").strip()
    for doi in list(signals.get("dois") or []):
        seed = re.sub(re.escape(doi), " ", seed, flags=re.IGNORECASE)
    for arxiv_id in list(signals.get("arxiv_ids") or []):
        seed = re.sub(re.escape(arxiv_id), " ", seed, flags=re.IGNORECASE)
    seed = re.sub(r"https?://arxiv\.org/(?:abs|pdf)/[^\s]+", " ", seed, flags=re.IGNORECASE)
    seed = re.sub(r"https?://doi\.org/[^\s]+", " ", seed, flags=re.IGNORECASE)
    seed = re.sub(r"\bdoi\b", " ", seed, flags=re.IGNORECASE)
    seed = re.sub(r"\barxiv\b", " ", seed, flags=re.IGNORECASE)
    lowered = seed.lower()
    for prefix in (
        "find details for ",
        "details for ",
        "find ",
        "show me ",
        "give me ",
    ):
        if lowered.startswith(prefix):
            seed = seed[len(prefix) :].strip()
            lowered = seed.lower()
            break
    for prefix in (
        "latest paper on ",
        "latest papers on ",
        "paper on ",
        "papers on ",
        "find papers on ",
        "search papers on ",
    ):
        if lowered.startswith(prefix):
            seed = seed[len(prefix) :].strip()
            break
    for phrase in ("from top ai conferences", "top ai conferences", "latest ", "recent "):
        seed = re.sub(re.escape(phrase), " ", seed, flags=re.IGNORECASE)
    seed = re.sub(r"\bfor and\b", " ", seed, flags=re.IGNORECASE)
    seed = re.sub(r"\band related work\b", " related work", seed, flags=re.IGNORECASE)
    seed = " ".join(seed.split())
    tokens = [token for token in re.findall(r"[a-z0-9]+", seed.lower()) if len(token) >= 3]
    if len(tokens) <= 1 and str(signals.get("title_hint") or "").strip():
        seed = str(signals.get("title_hint") or "").strip()
    if not seed:
        seed = str(signals.get("title_hint") or "").strip()
    return seed or str(prompt or "").strip()


def _build_deterministic_queries(signals: dict) -> list[dict]:
    queries: list[dict] = []
    for doi in list(signals.get("dois") or []):
        queries.append({"doi": doi, "policy": "fast", "limit": 5})
    for arxiv_id in list(signals.get("arxiv_ids") or []):
        queries.append({"arxiv_id": arxiv_id, "policy": "fast", "limit": 5})
    title_hint = str(signals.get("title_hint") or "").strip()
    if title_hint and not queries:
        queries.append({"title": title_hint, "policy": "consensus", "limit": 5})
    return queries


def _deterministic_result_to_row(result: dict, query_label: str) -> dict:
    paper = dict(result.get("paper") or {})
    return {
        "source": "deterministic",
        "source_id": str(paper.get("paper_id") or ""),
        "title": str(paper.get("title") or ""),
        "venue": str(paper.get("venue") or ""),
        "year": str(paper.get("year") or ""),
        "doi": str(paper.get("doi") or ""),
        "arxiv_id": str(paper.get("arxiv_id") or ""),
        "url": str(paper.get("url") or ""),
        "abstract": str(paper.get("abstract") or ""),
        "keywords": list(paper.get("keywords") or []),
        "categories": list(paper.get("categories") or []),
        "authors": list(paper.get("authors") or []),
        "score": 1.2,
        "reason": "deterministic_resolved",
        "query_used": query_label,
    }


def _candidate_seed_queries(row: dict) -> list[tuple[str, dict]]:
    seeds: list[tuple[str, dict]] = []
    doi = normalize_doi(str(row.get("doi") or ""))
    if doi:
        seeds.append((f"doi:{doi}", {"doi": doi, "policy": "fast", "limit": 5}))
    arxiv = normalize_arxiv_id(str(row.get("arxiv_id") or ""))
    if arxiv:
        seeds.append((f"arxiv:{arxiv}", {"arxiv_id": arxiv, "policy": "fast", "limit": 5}))
    title = str(row.get("title") or "").strip()
    if title:
        seeds.append((f"title:{title}", {"title": title, "policy": "consensus", "limit": 3}))
    return seeds


def _fit_score(prompt: str, row: dict) -> tuple[float, str]:
    stop_words = {
        "the", "and", "for", "with", "from", "that", "this", "into", "about", "latest", "paper", "papers",
        "research", "top", "conference", "conferences", "find", "details", "related", "work", "on",
    }
    prompt_tokens = {
        token
        for token in re.findall(r"[a-z0-9]+", str(prompt or "").lower())
        if len(token) >= 3 and token not in stop_words
    }
    target_text = " ".join(
        [
            str(row.get("title") or ""),
            str(row.get("abstract") or ""),
            str(row.get("venue") or ""),
            " ".join(str(k) for k in (row.get("keywords") or [])),
            " ".join(str(c) for c in (row.get("categories") or [])),
        ]
    ).lower()
    target_tokens = {
        token for token in re.findall(r"[a-z0-9]+", target_text) if len(token) >= 3
    }
    if not prompt_tokens:
        base = 0.0
        overlap = set()
    else:
        overlap = prompt_tokens & target_tokens
        base = float(len(overlap)) / float(len(prompt_tokens))
    id_bonus = 0.15 if (row.get("doi") or row.get("arxiv_id")) else 0.0
    score = min(1.0, base + id_bonus)
    if overlap:
        return score, f"overlap={','.join(sorted(list(overlap))[:6])}"
    return score, "no_token_overlap"


def _web_fallback_config(project_meta: dict, agentic_cfg: dict) -> dict:
    agentic_fallback = agentic_cfg.get("web_fallback") if isinstance(agentic_cfg.get("web_fallback"), dict) else {}
    discovery_cfg = project_meta.get("discovery") if isinstance(project_meta, dict) else {}
    connectors_cfg = discovery_cfg.get("connectors") if isinstance(discovery_cfg, dict) else {}
    searx_cfg = connectors_cfg.get("searxng") if isinstance(connectors_cfg, dict) else {}
    if not isinstance(searx_cfg, dict):
        searx_cfg = {}
    rates = discovery_cfg.get("rate_limits") if isinstance(discovery_cfg, dict) else {}
    searx_rate = rates.get("searxng", 1.0) if isinstance(rates, dict) else 1.0
    return {
        "enabled": bool(agentic_fallback.get("enabled", True)),
        "provider": str(agentic_fallback.get("provider", "searxng") or "searxng").strip().lower(),
        "min_ranked_candidates": max(
            1,
            _safe_int(
                agentic_fallback.get("min_ranked_candidates"),
                default=2,
            ),
        ),
        "searx_config": ConnectorConfig(
            enabled=bool(searx_cfg.get("enabled", True)),
            timeout_s=float(searx_cfg.get("timeout_s", 8.0) or 8.0),
            rate_limit_per_sec=float(searx_rate or 1.0),
            base_url=str(
                agentic_fallback.get("searxng_base_url")
                or searx_cfg.get("base_url")
                or ""
            ),
        ),
    }


def _search_web_searxng(
    *,
    query: str,
    top_n: int,
    connector_cfg: ConnectorConfig,
) -> tuple[list[dict], str]:
    if not bool(connector_cfg.enabled):
        return ([], "connector_disabled")
    connector = SearxngConnector(connector_cfg)
    if not connector.has_endpoint():
        return ([], "missing_endpoint")
    try:
        rows = connector.search(
            theme=query,
            venues=[],
            year_min=1900,
            year_max=2100,
            limit=max(top_n * 4, 20),
        )
    except Exception as exc:
        return ([], f"{type(exc).__name__}: {exc}")
    out: list[dict] = []
    for row in rows:
        payload = dict(row)
        payload.setdefault("source", "searxng")
        payload.setdefault("source_id", str(payload.get("url") or payload.get("title") or ""))
        payload.setdefault("title", "")
        payload.setdefault("venue", "")
        payload.setdefault("year", "")
        payload.setdefault("doi", "")
        payload.setdefault("arxiv_id", "")
        payload.setdefault("url", "")
        payload.setdefault("abstract", "")
        payload.setdefault("keywords", [])
        payload.setdefault("categories", [])
        payload.setdefault("authors", [])
        payload["score"] = float(payload.get("score", 0.25) or 0.25)
        payload["reason"] = str(payload.get("reason") or "web_fallback")
        payload["query_used"] = query
        out.append(payload)
    return (out, "")


def _route_retrieval(
    *,
    prompt: str,
    plan: dict,
    project_meta: dict,
    agentic_cfg: dict,
    top_n: int,
    progress_callback: ProgressCallback | None = None,
    session_id: str = "",
    cycle_index: int = 0,
) -> dict:
    adapters = build_adapters(project_meta)
    deterministic_queries = list(plan.get("deterministic_queries") or [])
    queries = list(plan.get("query_plan") or [])
    if not queries:
        queries = plan_queries(str(plan.get("retrieval_query") or prompt))
    raw_rows: list[dict] = []
    deterministic_resolved = 0
    deterministic_titles: list[str] = []

    for det_index, det_query in enumerate(deterministic_queries, start=1):
        label_parts = []
        if det_query.get("doi"):
            label_parts.append(f"doi:{det_query.get('doi')}")
        if det_query.get("arxiv_id"):
            label_parts.append(f"arxiv:{det_query.get('arxiv_id')}")
        if det_query.get("title"):
            label_parts.append(f"title:{det_query.get('title')}")
        query_label = ",".join(label_parts) or "deterministic_query"
        tool_call = "deterministic:resolve"
        _emit_progress(
            progress_callback,
            event="agentic_tool_start",
            session_id=session_id,
            cycle_index=cycle_index,
            query_index=det_index,
            query=query_label,
            adapter_index=1,
            adapter_total=1,
            tool_call=tool_call,
        )
        started = perf_counter()
        det_error = ""
        det_result: dict = {}
        try:
            det_result = resolve_deterministic(det_query, adapters, progress_callback=None)
        except Exception as exc:
            det_error = f"{type(exc).__name__}: {exc}"
        resolved = str(det_result.get("status") or "") == "resolved" and bool((det_result.get("paper") or {}).get("paper_id"))
        if resolved:
            raw_rows.append(_deterministic_result_to_row(det_result, query_label))
            deterministic_resolved += 1
            resolved_title = str((det_result.get("paper") or {}).get("title") or "").strip()
            if resolved_title:
                deterministic_titles.append(resolved_title)
        elapsed_ms = int((perf_counter() - started) * 1000)
        detail = f"status={det_result.get('status','')} reason={det_result.get('reason','')}".strip()
        _emit_progress(
            progress_callback,
            event="agentic_tool_done",
            session_id=session_id,
            cycle_index=cycle_index,
            query_index=det_index,
            query=query_label,
            tool_call=tool_call,
            rows_returned=1 if resolved else 0,
            elapsed_ms=elapsed_ms,
            error=det_error,
            detail=detail,
        )

    if deterministic_titles:
        replanned_seed = f"{deterministic_titles[0]} related work".strip()
        queries = plan_queries(replanned_seed)
        _emit_progress(
            progress_callback,
            event="agentic_query_replanned",
            session_id=session_id,
            cycle_index=cycle_index,
            reason="deterministic_title_seed",
            replanned_seed=replanned_seed,
            query_count=len(queries),
        )

    for query_index, plan_item in enumerate(queries, start=1):
        query = str(plan_item.get("query") or "").strip()
        _emit_progress(
            progress_callback,
            event="agentic_query_start",
            session_id=session_id,
            cycle_index=cycle_index,
            query_index=query_index,
            query_total=len(queries),
            query=query,
        )
        for adapter_index, adapter in enumerate(adapters, start=1):
            adapter_name = type(adapter).__name__
            tool_call = f"scholarly:search_open:{adapter_name}"
            _emit_progress(
                progress_callback,
                event="agentic_tool_start",
                session_id=session_id,
                cycle_index=cycle_index,
                query_index=query_index,
                query=query,
                adapter_index=adapter_index,
                adapter_total=len(adapters),
                tool_call=tool_call,
            )
            started = perf_counter()
            error = ""
            rows: list[dict] = []
            try:
                rows = adapter.search_open(query, limit=max(top_n * 4, 20))
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
            elapsed_ms = int((perf_counter() - started) * 1000)
            _emit_progress(
                progress_callback,
                event="agentic_tool_done",
                session_id=session_id,
                cycle_index=cycle_index,
                query_index=query_index,
                query=query,
                tool_call=tool_call,
                rows_returned=len(rows),
                elapsed_ms=elapsed_ms,
                error=error,
            )
            for row in rows:
                row_copy = dict(row)
                row_copy["query_used"] = query
                raw_rows.append(row_copy)

    discovery_ranked = _rank_rows(raw_rows)
    ranked_rows: list[dict] = []
    keep_count = 0
    ignore_count = 0
    deterministic_capable = all(
        hasattr(adapter, "lookup_doi") and hasattr(adapter, "lookup_arxiv") and hasattr(adapter, "search_title")
        for adapter in adapters
    )

    if deterministic_capable:
        seen_seed: set[str] = set()
        verified_rows: list[dict] = []
        fit_threshold = float((agentic_cfg.get("fit_filter") or {}).get("min_fit_score", 0.12)) if isinstance(agentic_cfg, dict) else 0.12
        max_seed_checks = max(top_n * 6, 24)
        for row in discovery_ranked:
            for seed_label, seed_query in _candidate_seed_queries(row):
                if seed_label in seen_seed:
                    continue
                seen_seed.add(seed_label)
                tool_call = "deterministic:resolve_candidate"
                _emit_progress(
                    progress_callback,
                    event="agentic_tool_start",
                    session_id=session_id,
                    cycle_index=cycle_index,
                    query_index=len(queries) + 1,
                    query=seed_label,
                    adapter_index=1,
                    adapter_total=1,
                    tool_call=tool_call,
                )
                started = perf_counter()
                resolved_result: dict = {}
                resolve_error = ""
                try:
                    resolved_result = resolve_deterministic(seed_query, adapters, progress_callback=None)
                except Exception as exc:
                    resolve_error = f"{type(exc).__name__}: {exc}"
                elapsed_ms = int((perf_counter() - started) * 1000)
                resolved = str(resolved_result.get("status") or "") == "resolved" and bool((resolved_result.get("paper") or {}).get("paper_id"))
                filter_decision = "ignore_unresolved"
                filter_reason = "deterministic_not_resolved"
                rows_returned = 0
                if resolved:
                    det_row = _deterministic_result_to_row(resolved_result, seed_label)
                    fit_score, fit_reason = _fit_score(prompt, det_row)
                    det_row["score"] = max(float(det_row.get("score", 0.0) or 0.0), fit_score)
                    rows_returned = 1
                    if fit_score >= fit_threshold:
                        verified_rows.append(det_row)
                        keep_count += 1
                        filter_decision = "keep"
                        filter_reason = f"fit={fit_score:.3f} {fit_reason}"
                    else:
                        ignore_count += 1
                        filter_decision = "ignore_low_fit"
                        filter_reason = f"fit={fit_score:.3f} {fit_reason}"
                else:
                    ignore_count += 1
                _emit_progress(
                    progress_callback,
                    event="agentic_tool_done",
                    session_id=session_id,
                    cycle_index=cycle_index,
                    query_index=len(queries) + 1,
                    query=seed_label,
                    tool_call=tool_call,
                    rows_returned=rows_returned,
                    elapsed_ms=elapsed_ms,
                    error=resolve_error,
                    detail=f"{filter_decision}:{filter_reason}",
                )
                _emit_progress(
                    progress_callback,
                    event="agentic_candidate_filter",
                    session_id=session_id,
                    cycle_index=cycle_index,
                    candidate_seed=seed_label,
                    decision=filter_decision,
                    reason=filter_reason,
                )
                if len(seen_seed) >= max_seed_checks:
                    break
            if len(seen_seed) >= max_seed_checks:
                break
        if verified_rows:
            ranked_rows = _rank_rows(verified_rows)
        else:
            raw_fit_rows: list[dict] = []
            raw_fit_threshold = max(0.06, fit_threshold * 0.75)
            for row in discovery_ranked:
                fit_score, fit_reason = _fit_score(prompt, row)
                if fit_score >= raw_fit_threshold:
                    row_copy = dict(row)
                    row_copy["score"] = max(float(row_copy.get("score", 0.0) or 0.0), fit_score)
                    raw_fit_rows.append(row_copy)
            if raw_fit_rows:
                ranked_rows = _rank_rows(raw_fit_rows)
                keep_count += len(raw_fit_rows)
            else:
                ranked_rows = []
    else:
        ranked_rows = discovery_ranked
        keep_count = len(ranked_rows)

    tool_calls: list[str] = []
    if deterministic_queries:
        tool_calls.append("deterministic:resolve")
    if deterministic_capable:
        tool_calls.append("deterministic:resolve_candidate")
    tool_calls.extend(f"scholarly:search_open:{type(adapter).__name__}" for adapter in adapters)

    fallback_cfg = _web_fallback_config(project_meta, agentic_cfg)
    min_ranked = max(1, min(int(top_n), int(fallback_cfg["min_ranked_candidates"])))
    insufficiency_reason = ""
    fallback_triggered = False
    router_decision = "scholarly_only"

    if len(ranked_rows) < min_ranked:
        insufficiency_reason = f"ranked_below_threshold:{len(ranked_rows)}<{min_ranked}"
        if not fallback_cfg["enabled"]:
            router_decision = "scholarly_only_web_disabled"
        elif fallback_cfg["provider"] != "searxng":
            router_decision = "scholarly_only_provider_unsupported"
        else:
            tool_calls.append("web:searxng:search")
            _emit_progress(
                progress_callback,
                event="agentic_tool_start",
                session_id=session_id,
                cycle_index=cycle_index,
                query_index=len(queries) + 1,
                query=prompt,
                adapter_index=1,
                adapter_total=1,
                tool_call="web:searxng:search",
            )
            started = perf_counter()
            fallback_rows, fallback_error = _search_web_searxng(
                query=prompt,
                top_n=top_n,
                connector_cfg=fallback_cfg["searx_config"],
            )
            elapsed_ms = int((perf_counter() - started) * 1000)
            _emit_progress(
                progress_callback,
                event="agentic_tool_done",
                session_id=session_id,
                cycle_index=cycle_index,
                query_index=len(queries) + 1,
                query=prompt,
                tool_call="web:searxng:search",
                rows_returned=len(fallback_rows),
                elapsed_ms=elapsed_ms,
                error=fallback_error,
            )
            if fallback_rows:
                fallback_triggered = True
                router_decision = "scholarly_plus_web"
                raw_rows.extend(fallback_rows)
                ranked_rows = _rank_rows(raw_rows)
            else:
                router_decision = "scholarly_only_web_unavailable"

    return {
        "raw": raw_rows,
        "ranked": ranked_rows,
        "query_plan": queries,
        "tool_calls": tool_calls,
        "router_decision": router_decision,
        "fallback_triggered": fallback_triggered,
        "insufficiency_reason": insufficiency_reason,
        "deterministic_resolved": deterministic_resolved,
        "deterministic_queries": deterministic_queries,
        "kept_candidates": keep_count,
        "ignored_candidates": ignore_count,
    }


def _build_cycle_plan(prompt: str, workflow: str, cycle_index: int, previous_candidates: list[dict]) -> dict:
    signals = _extract_prompt_signals(prompt)
    deterministic_queries = _build_deterministic_queries(signals)
    if workflow == "theme_refine":
        if cycle_index == 1 or not previous_candidates:
            seed = _derive_query_seed(prompt, signals)
            if deterministic_queries:
                rationale = "identifier_first_then_theme_expand"
            else:
                rationale = "theme_refine_bootstrap"
        else:
            anchor_title = str(previous_candidates[0].get("title") or "").strip()
            anchor = " ".join(anchor_title.split()[:6])
            seed = f"{_derive_query_seed(prompt, signals)} {anchor}".strip()
            rationale = "theme_refine_iterative_narrowing"
        query_plan = plan_queries(seed)
        planned_query = query_plan[0]["query"] if query_plan else seed
        return {
            "workflow": workflow,
            "planned_query": planned_query,
            "retrieval_query": planned_query,
            "query_plan": query_plan,
            "signals": signals,
            "deterministic_queries": deterministic_queries,
            "rationale": rationale,
        }
    planned = _derive_query_seed(prompt, signals)
    return {
        "workflow": workflow,
        "planned_query": planned,
        "retrieval_query": planned,
        "query_plan": [{"query": planned, "connector_scope": "all", "intent": "generic"}],
        "signals": signals,
        "deterministic_queries": deterministic_queries,
        "rationale": "generic_bootstrap",
    }


def _decision_for_cycle(
    *,
    workflow: str,
    cycle_index: int,
    max_cycles: int,
    shortlisted: list[dict],
    previous_keys: list[str],
    current_keys: list[str],
    ignored_candidates: int = 0,
) -> tuple[str, str, str]:
    if not shortlisted:
        if ignored_candidates > 0:
            return ("stop", "needs_user_feedback", "needs_feedback")
        return ("stop", "no_candidates", "no_candidates")
    if cycle_index >= max_cycles:
        return ("stop", "cycle_budget_reached", "max_cycles_reached")

    required_cycles = 2 if workflow == "theme_refine" and max_cycles >= 2 else 1
    if cycle_index < required_cycles:
        return ("continue", "minimum_refinement_cycles_not_met", "")

    if current_keys and current_keys == previous_keys:
        return ("stop", "converged_candidate_set", "converged")

    return ("continue", "refinement_budget_available", "")


def _build_candidate_outputs(
    *,
    shortlisted: list[dict],
    session_id: str,
    cycle_index: int,
) -> tuple[list[dict], list[dict], list[str]]:
    candidate_rows: list[dict] = []
    final_candidates: list[dict] = []
    current_keys: list[str] = []
    for idx, candidate in enumerate(shortlisted, start=1):
        row = dict(candidate)
        row["session_id"] = session_id
        row["cycle_index"] = cycle_index
        row["rank"] = idx
        row["candidate_key"] = _candidate_key(row)
        row["selected"] = "1"
        candidate_rows.append(row)
        current_keys.append(row["candidate_key"])
        final_candidates.append(
            {
                "rank": idx,
                "candidate_key": row["candidate_key"],
                "title": str(row.get("title") or ""),
                "venue": str(row.get("venue") or ""),
                "year": str(row.get("year") or ""),
                "doi": str(row.get("doi") or ""),
                "arxiv_id": str(row.get("arxiv_id") or ""),
                "url": str(row.get("url") or ""),
                "score": float(row.get("score", 0.0) or 0.0),
                "source": str(row.get("source") or ""),
            }
        )
    return candidate_rows, final_candidates, current_keys


def _preview_candidates(rows: list[dict], limit: int = 3) -> list[dict]:
    out: list[dict] = []
    for idx, row in enumerate(rows[:limit], start=1):
        key = str(row.get("candidate_key") or "").strip()
        if not key:
            key = _candidate_key(row)
        out.append(
            {
                "rank": idx,
                "candidate_key": key,
                "title": str(row.get("title") or ""),
                "source": str(row.get("source") or ""),
                "year": str(row.get("year") or ""),
                "score": float(row.get("score", 0.0) or 0.0),
            }
        )
    return out


def _feedback_template() -> dict:
    return {
        "keep": "candidate_key_1,candidate_key_2",
        "remove": "candidate_key_3",
        "why_missing": "brief description of expected missing papers",
    }


def _resolve_session_id(paths: dict[str, Path], requested_session_id: str) -> str:
    session_id = str(requested_session_id or "").strip()
    if session_id:
        return session_id
    request_path = paths["request"]
    if not request_path.exists():
        return ""
    payload = load(request_path)
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("session_id") or "").strip()


def _agentic_status_payload(project_id: str, session_id: str = "") -> dict:
    pdir = project_dir(project_id)
    paths = _agentic_paths(pdir)
    resolved_session_id = _resolve_session_id(paths, session_id)
    if not resolved_session_id:
        return {"project_id": project_id, "session_id": "", "session": {}, "result": {}, "questions": {}, "request": {}}
    request_payload = _load_contract(paths["request"], _request_contract(
        session_id=resolved_session_id,
        request_id=f"req-{resolved_session_id}",
        project_id=project_id,
        prompt="",
        workflow="theme_refine",
        top_n=5,
        max_cycles=1,
    ))
    session_payload = _load_contract(paths["session"], _session_contract(
        session_id=resolved_session_id,
        request_id=f"req-{resolved_session_id}",
        project_id=project_id,
        workflow=str(request_payload.get("workflow") or "theme_refine"),
        max_cycles=_safe_int((request_payload.get("limits") or {}).get("max_cycles"), 1),
    ))
    result_payload = _load_contract(paths["result"], _result_contract(
        session_id=resolved_session_id,
        request_id=f"req-{resolved_session_id}",
        workflow=str(request_payload.get("workflow") or "theme_refine"),
        top_n=_safe_int((request_payload.get("limits") or {}).get("top_n"), 5),
    ))
    questions_payload = _load_contract(paths["questions"], _questions_contract(session_id=resolved_session_id))
    return {
        "project_id": project_id,
        "session_id": resolved_session_id,
        "request": request_payload,
        "session": session_payload,
        "result": result_payload,
        "questions": questions_payload,
    }


def run_retrieve_agentic(
    project_id: str,
    *,
    prompt: str = "",
    workflow: str = "theme_refine",
    top_n: int = 5,
    max_cycles: int = 1,
    session_id: str = "",
    progress_callback: ProgressCallback | None = None,
) -> Path:
    pdir = project_dir(project_id)
    paths = _agentic_paths(pdir)
    pmeta = load(pdir / "project.yaml")
    retrieval_cfg = (pmeta.get("retrieval") or {}) if isinstance(pmeta, dict) else {}
    agentic_cfg = (retrieval_cfg.get("agentic") or {}) if isinstance(retrieval_cfg, dict) else {}

    requested_top_n = _safe_int(top_n or agentic_cfg.get("top_n", 5) or 5, default=5)
    effective_top_n = max(1, requested_top_n)
    requested_cycles = _safe_int(max_cycles or agentic_cfg.get("max_cycles", 1) or 1, default=1)
    effective_max_cycles = max(1, requested_cycles)
    workflow_name = str(workflow or agentic_cfg.get("workflow", "theme_refine") or "theme_refine").strip() or "theme_refine"
    llm_runtime = _llm_runtime(agentic_cfg if isinstance(agentic_cfg, dict) else {})

    resolved_prompt = str(prompt or "").strip()
    resolved_session_id = str(session_id or "").strip() or _new_session_id(resolved_prompt or workflow_name)
    request_id = f"req-{resolved_session_id}"

    default_request = _request_contract(
        session_id=resolved_session_id,
        request_id=request_id,
        project_id=project_id,
        prompt=resolved_prompt,
        workflow=workflow_name,
        top_n=effective_top_n,
        max_cycles=effective_max_cycles,
    )
    request_payload = _load_contract(paths["request"], default_request)
    existing_request_session = str(request_payload.get("session_id") or "")
    if existing_request_session and existing_request_session != resolved_session_id:
        request_payload = default_request
    elif existing_request_session == resolved_session_id:
        saved_prompt = str(request_payload.get("prompt") or "").strip()
        if resolved_prompt and saved_prompt and resolved_prompt != saved_prompt:
            raise ValueError("Prompt mismatch for existing session_id")
        resolved_prompt = saved_prompt or resolved_prompt
        workflow_name = str(request_payload.get("workflow") or workflow_name).strip() or workflow_name
        limits = request_payload.get("limits") if isinstance(request_payload.get("limits"), dict) else {}
        effective_top_n = max(1, _safe_int(limits.get("top_n"), effective_top_n))
        effective_max_cycles = max(1, _safe_int(limits.get("max_cycles"), effective_max_cycles))
    if resolved_prompt:
        request_payload["prompt"] = resolved_prompt
    resolved_prompt = str(request_payload.get("prompt") or "").strip()
    if not resolved_prompt:
        raise ValueError("Prompt is required")
    request_payload["workflow"] = workflow_name
    request_payload["session_id"] = resolved_session_id
    request_payload["request_id"] = request_id
    request_payload["project_id"] = project_id
    request_payload.setdefault("limits", {})
    request_payload["limits"]["top_n"] = effective_top_n
    request_payload["limits"]["max_cycles"] = effective_max_cycles
    request_payload["llm"] = {
        "backend": llm_runtime["backend"],
        "model": llm_runtime["model"],
        "api_key_env": llm_runtime["api_key_env"],
        "api_key_present": bool(llm_runtime["api_key_present"]),
    }
    request_payload["updated_at"] = _utc_now()

    default_session = _session_contract(
        session_id=resolved_session_id,
        request_id=request_id,
        project_id=project_id,
        workflow=workflow_name,
        max_cycles=effective_max_cycles,
    )
    session_payload = _load_contract(paths["session"], default_session)
    if str(session_payload.get("session_id") or "") not in {"", resolved_session_id}:
        session_payload = default_session
    session_payload["session_id"] = resolved_session_id
    session_payload["request_id"] = request_id
    session_payload["workflow"] = workflow_name
    session_payload["project_id"] = project_id
    session_payload["max_cycles"] = effective_max_cycles

    default_result = _result_contract(
        session_id=resolved_session_id,
        request_id=request_id,
        workflow=workflow_name,
        top_n=effective_top_n,
    )
    result_payload = _load_contract(paths["result"], default_result)
    if str(result_payload.get("session_id") or "") not in {"", resolved_session_id}:
        result_payload = default_result
    result_payload["session_id"] = resolved_session_id
    result_payload["request_id"] = request_id
    result_payload["workflow"] = workflow_name
    result_payload["top_n"] = effective_top_n

    default_questions = _questions_contract(session_id=resolved_session_id)
    questions_payload = _load_contract(paths["questions"], default_questions)
    if str(questions_payload.get("session_id") or "") not in {"", resolved_session_id}:
        questions_payload = default_questions
    questions_payload["session_id"] = resolved_session_id
    questions_payload["updated_at"] = _utc_now()

    if str(session_payload.get("status") or "").strip().lower() == "completed":
        _emit_progress(
            progress_callback,
            event="agentic_session_already_completed",
            session_id=resolved_session_id,
            current_cycle=_safe_int(session_payload.get("current_cycle"), 0),
            stop_reason=str(session_payload.get("stop_reason") or ""),
        )
        write_yaml(paths["request"], request_payload)
        write_yaml(paths["session"], session_payload)
        write_yaml(paths["result"], result_payload)
        write_yaml(paths["questions"], questions_payload)
        return paths["result"]

    cycle_rows = _load_cycle_rows(paths["cycles"])
    recorded_cycles = [
        _safe_int(row.get("cycle_index"), 0)
        for row in cycle_rows
        if str(row.get("session_id") or "") == resolved_session_id
    ]
    if recorded_cycles:
        session_payload["current_cycle"] = max(_safe_int(session_payload.get("current_cycle"), 0), max(recorded_cycles))

    session_payload["status"] = "running"
    session_payload["state"] = "plan"
    session_payload["updated_at"] = _utc_now()
    _emit_progress(
        progress_callback,
        event="agentic_start",
        project_id=project_id,
        session_id=resolved_session_id,
        workflow=workflow_name,
        top_n=effective_top_n,
        max_cycles=effective_max_cycles,
        llm_backend=llm_runtime["backend"],
        llm_model=llm_runtime["model"],
        llm_api_key_present=bool(llm_runtime["api_key_present"]),
    )
    write_yaml(paths["session"], session_payload)
    write_yaml(paths["request"], request_payload)
    write_yaml(paths["questions"], questions_payload)

    while True:
        current_cycle = _safe_int(session_payload.get("current_cycle"), 0)
        if current_cycle >= effective_max_cycles:
            result_payload["status"] = "completed"
            result_payload["stop_reason"] = str(result_payload.get("stop_reason") or "max_cycles_reached")
            result_payload["cycle_count"] = current_cycle
            result_payload["updated_at"] = _utc_now()
            session_payload["status"] = "completed"
            session_payload["state"] = "completed"
            session_payload["stop_reason"] = str(session_payload.get("stop_reason") or result_payload["stop_reason"])
            session_payload["updated_at"] = _utc_now()
            _emit_progress(
                progress_callback,
                event="agentic_complete",
                session_id=resolved_session_id,
                status="completed",
                stop_reason=str(result_payload.get("stop_reason") or ""),
                cycle_count=current_cycle,
                final_candidates=len(list(result_payload.get("final_candidates") or [])),
            )
            break

        cycle_index = current_cycle + 1
        previous_final = list(result_payload.get("final_candidates") or [])
        previous_keys = _final_candidate_keys(previous_final)
        _emit_progress(
            progress_callback,
            event="agentic_cycle_context",
            session_id=resolved_session_id,
            cycle_index=cycle_index,
            previous_candidate_count=len(previous_final),
            previous_preview=_preview_candidates(previous_final, limit=3),
        )

        session_payload["state"] = "plan"
        session_payload["updated_at"] = _utc_now()
        _emit_progress(
            progress_callback,
            event="agentic_state",
            session_id=resolved_session_id,
            cycle_index=cycle_index,
            state="plan",
        )
        write_yaml(paths["session"], session_payload)

        plan = _build_cycle_plan(resolved_prompt, workflow_name, cycle_index, previous_final)
        _emit_progress(
            progress_callback,
            event="agentic_plan_ready",
            session_id=resolved_session_id,
            cycle_index=cycle_index,
            planned_query=plan["planned_query"],
            retrieval_query=plan["retrieval_query"],
            plan_rationale=plan["rationale"],
            extracted_signals=plan.get("signals") or {},
            deterministic_queries=plan.get("deterministic_queries") or [],
            open_query_plan=plan.get("query_plan") or [],
        )

        session_payload["state"] = "retrieve"
        session_payload["updated_at"] = _utc_now()
        _emit_progress(
            progress_callback,
            event="agentic_state",
            session_id=resolved_session_id,
            cycle_index=cycle_index,
            state="retrieve",
        )
        write_yaml(paths["session"], session_payload)

        retrieval_result = _route_retrieval(
            prompt=plan["retrieval_query"],
            plan=plan,
            project_meta=pmeta if isinstance(pmeta, dict) else {},
            agentic_cfg=agentic_cfg if isinstance(agentic_cfg, dict) else {},
            top_n=effective_top_n,
            progress_callback=progress_callback,
            session_id=resolved_session_id,
            cycle_index=cycle_index,
        )
        raw_rows = list(retrieval_result.get("raw") or [])
        ranked_rows = list(retrieval_result.get("ranked") or [])
        shortlisted = ranked_rows[:effective_top_n]
        _emit_progress(
            progress_callback,
            event="agentic_retrieve_done",
            session_id=resolved_session_id,
            cycle_index=cycle_index,
            raw_candidates=len(raw_rows),
            ranked_candidates=len(shortlisted),
            router_decision=str(retrieval_result.get("router_decision") or ""),
            fallback_triggered=bool(retrieval_result.get("fallback_triggered")),
            insufficiency_reason=str(retrieval_result.get("insufficiency_reason") or ""),
            tool_calls=list(retrieval_result.get("tool_calls") or []),
            deterministic_resolved=int(retrieval_result.get("deterministic_resolved") or 0),
            kept_candidates=int(retrieval_result.get("kept_candidates") or 0),
            ignored_candidates=int(retrieval_result.get("ignored_candidates") or 0),
        )

        session_payload["state"] = "rank"
        session_payload["updated_at"] = _utc_now()
        _emit_progress(
            progress_callback,
            event="agentic_state",
            session_id=resolved_session_id,
            cycle_index=cycle_index,
            state="rank",
        )
        write_yaml(paths["session"], session_payload)

        candidate_rows, final_candidates, current_keys = _build_candidate_outputs(
            shortlisted=shortlisted,
            session_id=resolved_session_id,
            cycle_index=cycle_index,
        )
        _emit_progress(
            progress_callback,
            event="agentic_ranked_preview",
            session_id=resolved_session_id,
            cycle_index=cycle_index,
            shortlisted_count=len(final_candidates),
            shortlisted_preview=_preview_candidates(final_candidates, limit=3),
        )
        _write_candidates_latest(paths["candidates"], candidate_rows)

        session_payload["state"] = "decide"
        _emit_progress(
            progress_callback,
            event="agentic_state",
            session_id=resolved_session_id,
            cycle_index=cycle_index,
            state="decide",
        )
        decision, decision_reason, stop_reason = _decision_for_cycle(
            workflow=workflow_name,
            cycle_index=cycle_index,
            max_cycles=effective_max_cycles,
            shortlisted=shortlisted,
            previous_keys=previous_keys,
            current_keys=current_keys,
            ignored_candidates=int(retrieval_result.get("ignored_candidates") or 0),
        )
        cycle_row = {
            "timestamp": _utc_now(),
            "session_id": resolved_session_id,
            "workflow": workflow_name,
            "cycle_index": cycle_index,
            "state_path": _state_path(),
            "planned_query": plan["planned_query"],
            "retrieval_query": plan["retrieval_query"],
            "tool_calls": ",".join(retrieval_result.get("tool_calls") or []),
            "router_decision": retrieval_result.get("router_decision", ""),
            "fallback_triggered": "1" if retrieval_result.get("fallback_triggered") else "0",
            "insufficiency_reason": retrieval_result.get("insufficiency_reason", ""),
            "raw_candidates": len(raw_rows),
            "ranked_candidates": len(shortlisted),
            "candidate_delta": len(set(current_keys) - set(previous_keys)),
            "decision": decision,
            "decision_reason": decision_reason,
            "stop_reason": stop_reason,
            "question_id": "",
            "plan_rationale": plan["rationale"],
        }
        _append_cycle_row(paths["cycles"], cycle_row)

        result_payload["cycle_count"] = cycle_index
        result_payload["final_candidates"] = final_candidates
        result_payload.setdefault("cycle_memory", []).append(
            {
                "cycle_index": cycle_index,
                "planned_query": plan["planned_query"],
                "retrieval_query": plan["retrieval_query"],
                "plan_rationale": plan["rationale"],
                "tool_calls": retrieval_result.get("tool_calls") or [],
                "router_decision": retrieval_result.get("router_decision", ""),
                "fallback_triggered": bool(retrieval_result.get("fallback_triggered")),
                "insufficiency_reason": retrieval_result.get("insufficiency_reason", ""),
                "raw_candidates": len(raw_rows),
                "ranked_candidates": len(shortlisted),
                "candidate_delta": len(set(current_keys) - set(previous_keys)),
            }
        )
        result_payload.setdefault("decision_history", []).append(
            {
                "timestamp": _utc_now(),
                "cycle_index": cycle_index,
                "decision": decision,
                "decision_reason": decision_reason,
                "stop_reason": stop_reason,
                "planned_query": plan["planned_query"],
                "retrieved_count": len(raw_rows),
                "ranked_count": len(shortlisted),
                "plan_rationale": plan["rationale"],
            }
        )
        result_payload["updated_at"] = _utc_now()

        session_payload["current_cycle"] = cycle_index
        session_payload["last_decision"] = decision
        session_payload["last_decision_reason"] = decision_reason
        session_payload["stop_reason"] = stop_reason
        session_payload["updated_at"] = _utc_now()
        _emit_progress(
            progress_callback,
            event="agentic_decision",
            session_id=resolved_session_id,
            cycle_index=cycle_index,
            decision=decision,
            decision_reason=decision_reason,
            stop_reason=stop_reason,
            candidate_delta=len(set(current_keys) - set(previous_keys)),
        )
        _emit_progress(
            progress_callback,
            event="agentic_feedback_expected",
            session_id=resolved_session_id,
            cycle_index=cycle_index,
            optional=not (decision_reason == "needs_user_feedback"),
            pending_questions=1 if decision_reason == "needs_user_feedback" else 0,
            expected_answers=_feedback_template(),
            command_hint=(
                "python -m src.cli retrieve-agentic-answer "
                f"{project_id} --session-id {resolved_session_id} "
                "--answer keep=<candidate_keys_csv> --answer remove=<candidate_keys_csv> "
                "--answer why_missing=<text>"
            ),
        )

        if decision == "stop":
            result_payload["status"] = "completed"
            result_payload["stop_reason"] = stop_reason
            session_payload["status"] = "completed"
            session_payload["state"] = "completed"
            _emit_progress(
                progress_callback,
                event="agentic_complete",
                session_id=resolved_session_id,
                status="completed",
                stop_reason=stop_reason,
                cycle_count=cycle_index,
                final_candidates=len(final_candidates),
            )
            break

        result_payload["status"] = "running"
        result_payload["stop_reason"] = ""
        session_payload["status"] = "running"
        session_payload["state"] = "plan"

        write_yaml(paths["request"], request_payload)
        write_yaml(paths["session"], session_payload)
        write_yaml(paths["result"], result_payload)
        write_yaml(paths["questions"], questions_payload)

    write_yaml(paths["request"], request_payload)
    write_yaml(paths["session"], session_payload)
    write_yaml(paths["result"], result_payload)
    write_yaml(paths["questions"], questions_payload)
    return paths["result"]


def get_agentic_status(project_id: str, *, session_id: str = "") -> dict:
    return _agentic_status_payload(project_id, session_id=session_id)


def submit_agentic_answers(project_id: str, *, session_id: str, answers: dict[str, str]) -> dict:
    status = _agentic_status_payload(project_id, session_id=session_id)
    resolved_session_id = str(status.get("session_id") or "").strip()
    if not resolved_session_id:
        raise ValueError("Unknown agentic session_id")
    pdir = project_dir(project_id)
    paths = _agentic_paths(pdir)
    questions_payload = status.get("questions") if isinstance(status.get("questions"), dict) else {}
    history = questions_payload.get("history") if isinstance(questions_payload.get("history"), list) else []
    history.append(
        {
            "timestamp": _utc_now(),
            "session_id": resolved_session_id,
            "answers": {str(k): str(v) for k, v in (answers or {}).items()},
        }
    )
    questions_payload["history"] = history
    questions_payload["pending"] = []
    questions_payload["updated_at"] = _utc_now()
    write_yaml(paths["questions"], questions_payload)

    request_payload = status.get("request") if isinstance(status.get("request"), dict) else {}
    limits = request_payload.get("limits") if isinstance(request_payload.get("limits"), dict) else {}
    run_retrieve_agentic(
        project_id,
        prompt=str(request_payload.get("prompt") or ""),
        workflow=str(request_payload.get("workflow") or "theme_refine"),
        top_n=max(1, _safe_int(limits.get("top_n"), 5)),
        max_cycles=max(1, _safe_int(limits.get("max_cycles"), 1)),
        session_id=resolved_session_id,
    )
    return _agentic_status_payload(project_id, session_id=resolved_session_id)


def finalize_agentic_session(project_id: str, *, session_id: str) -> Path:
    status = _agentic_status_payload(project_id, session_id=session_id)
    resolved_session_id = str(status.get("session_id") or "").strip()
    if not resolved_session_id:
        raise ValueError("Unknown agentic session_id")
    pdir = project_dir(project_id)
    paths = _agentic_paths(pdir)
    session_payload = status.get("session") if isinstance(status.get("session"), dict) else {}
    result_payload = status.get("result") if isinstance(status.get("result"), dict) else {}

    if str(session_payload.get("status") or "").strip().lower() != "completed":
        session_payload["status"] = "completed"
        session_payload["state"] = "completed"
        session_payload["stop_reason"] = str(session_payload.get("stop_reason") or "user_finalized")
        session_payload["updated_at"] = _utc_now()
    if str(result_payload.get("status") or "").strip().lower() != "completed":
        result_payload["status"] = "completed"
        result_payload["stop_reason"] = str(result_payload.get("stop_reason") or "user_finalized")
        result_payload["updated_at"] = _utc_now()

    write_yaml(paths["session"], session_payload)
    write_yaml(paths["result"], result_payload)
    return paths["result"]
