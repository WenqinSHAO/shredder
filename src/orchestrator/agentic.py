from __future__ import annotations

import csv
import hashlib
from datetime import datetime, timezone
from pathlib import Path

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
    "raw_candidates",
    "ranked_candidates",
    "candidate_delta",
    "decision",
    "decision_reason",
    "stop_reason",
    "question_id",
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


def _build_cycle_plan(prompt: str, workflow: str) -> dict:
    if workflow == "theme_refine":
        query_plan = plan_queries(prompt)
        planned_query = query_plan[0]["query"] if query_plan else prompt.strip()
        return {
            "workflow": workflow,
            "planned_query": planned_query,
            "retrieval_query": planned_query,
            "query_plan": query_plan,
            "rationale": "theme_refine_bootstrap",
        }
    planned = prompt.strip()
    return {
        "workflow": workflow,
        "planned_query": planned,
        "retrieval_query": planned,
        "query_plan": [{"query": planned, "connector_scope": "all", "intent": "generic"}],
        "rationale": "generic_bootstrap",
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

    requested_top_n = int(top_n or agentic_cfg.get("top_n", 5) or 5)
    effective_top_n = max(1, requested_top_n)
    requested_cycles = int(max_cycles or agentic_cfg.get("max_cycles", 1) or 1)
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
    if str(request_payload.get("session_id") or "") not in {"", resolved_session_id}:
        request_payload = default_request
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

    adapters = build_adapters(pmeta)
    cycle_index = int(session_payload.get("current_cycle") or 0) + 1

    session_payload["status"] = "running"
    session_payload["state"] = "plan"
    session_payload["updated_at"] = _utc_now()
    write_yaml(paths["session"], session_payload)
    write_yaml(paths["request"], request_payload)
    write_yaml(paths["questions"], questions_payload)

    plan = _build_cycle_plan(resolved_prompt, workflow_name)
    session_payload["state"] = "retrieve"
    session_payload["updated_at"] = _utc_now()
    write_yaml(paths["session"], session_payload)

    retrieval_result = run_open_retrieval(prompt=plan["retrieval_query"], adapters=adapters, top_n=effective_top_n)
    raw_rows = list(retrieval_result.get("raw") or [])
    ranked_rows = list(retrieval_result.get("ranked") or [])
    shortlisted = ranked_rows[:effective_top_n]

    session_payload["state"] = "rank"
    session_payload["updated_at"] = _utc_now()
    write_yaml(paths["session"], session_payload)

    previous_count = len(list(result_payload.get("final_candidates") or []))
    candidate_rows: list[dict] = []
    final_candidates: list[dict] = []
    for idx, candidate in enumerate(shortlisted, start=1):
        row = dict(candidate)
        row["session_id"] = resolved_session_id
        row["cycle_index"] = cycle_index
        row["rank"] = idx
        row["candidate_key"] = _candidate_key(row)
        row["selected"] = "1"
        candidate_rows.append(row)
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
    _write_candidates_latest(paths["candidates"], candidate_rows)

    session_payload["state"] = "decide"
    if not shortlisted:
        decision = "stop"
        decision_reason = "no_candidates"
        stop_reason = "no_candidates"
    elif cycle_index >= effective_max_cycles:
        decision = "stop"
        decision_reason = "cycle_budget_reached"
        stop_reason = "max_cycles_reached"
    else:
        decision = "stop"
        decision_reason = "single_cycle_bootstrap"
        stop_reason = "initial_cycle_complete"

    cycle_row = {
        "timestamp": _utc_now(),
        "session_id": resolved_session_id,
        "workflow": workflow_name,
        "cycle_index": cycle_index,
        "state_path": _state_path(),
        "planned_query": plan["planned_query"],
        "retrieval_query": plan["retrieval_query"],
        "tool_calls": ",".join(f"search_open:{type(adapter).__name__}" for adapter in adapters),
        "raw_candidates": len(raw_rows),
        "ranked_candidates": len(shortlisted),
        "candidate_delta": len(shortlisted) - previous_count,
        "decision": decision,
        "decision_reason": decision_reason,
        "stop_reason": stop_reason,
        "question_id": "",
    }
    _append_cycle_row(paths["cycles"], cycle_row)

    result_payload["status"] = "completed"
    result_payload["stop_reason"] = stop_reason
    result_payload["cycle_count"] = cycle_index
    result_payload["final_candidates"] = final_candidates
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

    session_payload["status"] = "completed"
    session_payload["state"] = "completed"
    session_payload["current_cycle"] = cycle_index
    session_payload["last_decision"] = decision
    session_payload["last_decision_reason"] = decision_reason
    session_payload["stop_reason"] = stop_reason
    session_payload["updated_at"] = _utc_now()

    write_yaml(paths["request"], request_payload)
    write_yaml(paths["session"], session_payload)
    write_yaml(paths["result"], result_payload)
    write_yaml(paths["questions"], questions_payload)
    return paths["result"]
