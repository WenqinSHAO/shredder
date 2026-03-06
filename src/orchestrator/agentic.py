from __future__ import annotations

import csv
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.connectors.base import ConnectorConfig
from src.connectors.searxng import SearxngConnector
from src.retrieval.service import SOURCE_FIELDS, build_adapters, plan_queries, run_open_retrieval, write_yaml
from src.utils.paths import project_dir
from src.utils.yamlx import load

REQUEST_SCHEMA_VERSION = "0.1.0"
SESSION_SCHEMA_VERSION = "0.1.0"
RESULT_SCHEMA_VERSION = "0.1.0"
QUESTIONS_SCHEMA_VERSION = "0.1.0"

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
) -> list[dict]:
    if not bool(connector_cfg.enabled):
        return []
    connector = SearxngConnector(connector_cfg)
    if not connector.has_endpoint():
        return []
    try:
        rows = connector.search(
            theme=query,
            venues=[],
            year_min=1900,
            year_max=2100,
            limit=max(top_n * 4, 20),
        )
    except Exception:
        return []
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
    return out


def _route_retrieval(
    *,
    prompt: str,
    project_meta: dict,
    agentic_cfg: dict,
    top_n: int,
) -> dict:
    adapters = build_adapters(project_meta)
    scholarly = run_open_retrieval(prompt=prompt, adapters=adapters, top_n=top_n)
    raw_rows = list(scholarly.get("raw") or [])
    ranked_rows = list(scholarly.get("ranked") or [])
    tool_calls = [f"scholarly:search_open:{type(adapter).__name__}" for adapter in adapters]

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
            fallback_rows = _search_web_searxng(
                query=prompt,
                top_n=top_n,
                connector_cfg=fallback_cfg["searx_config"],
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
        "query_plan": scholarly.get("query_plan") or [],
        "tool_calls": tool_calls,
        "router_decision": router_decision,
        "fallback_triggered": fallback_triggered,
        "insufficiency_reason": insufficiency_reason,
    }


def _build_cycle_plan(prompt: str, workflow: str, cycle_index: int, previous_candidates: list[dict]) -> dict:
    if workflow == "theme_refine":
        if cycle_index == 1 or not previous_candidates:
            seed = prompt.strip()
            rationale = "theme_refine_bootstrap"
        else:
            anchor_title = str(previous_candidates[0].get("title") or "").strip()
            anchor = " ".join(anchor_title.split()[:6])
            seed = f"{prompt.strip()} {anchor}".strip()
            rationale = "theme_refine_iterative_narrowing"
        query_plan = plan_queries(seed)
        planned_query = query_plan[0]["query"] if query_plan else seed
        return {
            "workflow": workflow,
            "planned_query": planned_query,
            "retrieval_query": planned_query,
            "query_plan": query_plan,
            "rationale": rationale,
        }
    planned = prompt.strip()
    return {
        "workflow": workflow,
        "planned_query": planned,
        "retrieval_query": planned,
        "query_plan": [{"query": planned, "connector_scope": "all", "intent": "generic"}],
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
) -> tuple[str, str, str]:
    if not shortlisted:
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
            break

        cycle_index = current_cycle + 1
        previous_final = list(result_payload.get("final_candidates") or [])
        previous_keys = _final_candidate_keys(previous_final)

        session_payload["state"] = "plan"
        session_payload["updated_at"] = _utc_now()
        write_yaml(paths["session"], session_payload)

        plan = _build_cycle_plan(resolved_prompt, workflow_name, cycle_index, previous_final)

        session_payload["state"] = "retrieve"
        session_payload["updated_at"] = _utc_now()
        write_yaml(paths["session"], session_payload)

        retrieval_result = _route_retrieval(
            prompt=plan["retrieval_query"],
            project_meta=pmeta if isinstance(pmeta, dict) else {},
            agentic_cfg=agentic_cfg if isinstance(agentic_cfg, dict) else {},
            top_n=effective_top_n,
        )
        raw_rows = list(retrieval_result.get("raw") or [])
        ranked_rows = list(retrieval_result.get("ranked") or [])
        shortlisted = ranked_rows[:effective_top_n]

        session_payload["state"] = "rank"
        session_payload["updated_at"] = _utc_now()
        write_yaml(paths["session"], session_payload)

        candidate_rows, final_candidates, current_keys = _build_candidate_outputs(
            shortlisted=shortlisted,
            session_id=resolved_session_id,
            cycle_index=cycle_index,
        )
        _write_candidates_latest(paths["candidates"], candidate_rows)

        session_payload["state"] = "decide"
        decision, decision_reason, stop_reason = _decision_for_cycle(
            workflow=workflow_name,
            cycle_index=cycle_index,
            max_cycles=effective_max_cycles,
            shortlisted=shortlisted,
            previous_keys=previous_keys,
            current_keys=current_keys,
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

        if decision == "stop":
            result_payload["status"] = "completed"
            result_payload["stop_reason"] = stop_reason
            session_payload["status"] = "completed"
            session_payload["state"] = "completed"
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
