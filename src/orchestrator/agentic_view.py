from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.orchestrator.agentic_text import _host_from_url, _normalize_anchor_terms, _peek_text
from src.retrieval.service import write_yaml

ALLOWED_SHORTLIST_HINTS = {
    "venue_program_pages",
    "author_sources",
    "doi_landing",
    "avoid_detail_pages",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _unique_nonempty(items: list[str], limit: int) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        value = str(item or "").strip()
        if not value:
            continue
        lowered = value.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        out.append(value)
        if len(out) >= limit:
            break
    return out


def _normalize_shortlist_hints(value: Any) -> dict:
    if not isinstance(value, dict):
        return {}
    prefer_raw = value.get("prefer")
    items: list[str] = []
    if isinstance(prefer_raw, list):
        for raw in prefer_raw:
            parts = [part.strip().lower() for part in re.split(r"[|,;]", str(raw or "")) if part.strip()]
            items.extend(parts)
    elif isinstance(prefer_raw, str):
        items.extend([part.strip().lower() for part in re.split(r"[|,;]", prefer_raw) if part.strip()])
    if not items:
        return {}
    prefer = [item for item in _unique_nonempty(items, limit=16) if item in ALLOWED_SHORTLIST_HINTS]
    return {"prefer": prefer} if prefer else {}


def _write_agentic_trajectory(
    path: Path,
    *,
    session_id: str,
    prompt: str,
    moves: list[dict],
    status: str,
    stop_reason: str,
    llm_model: str,
    max_cycles: int,
    top_n: int,
) -> None:
    user_view: list[dict[str, Any]] = []
    for move in moves:
        step = int(move.get("cycle_index") or 0)
        action_input = move.get("action_input") if isinstance(move.get("action_input"), dict) else {}
        action = str(action_input.get("action") or move.get("selected_action") or "")
        result = move.get("action_result") if isinstance(move.get("action_result"), dict) else {}
        decision = move.get("decision") if isinstance(move.get("decision"), dict) else {}
        refs = move.get("refs") if isinstance(move.get("refs"), dict) else {}
        progress = move.get("progress") if isinstance(move.get("progress"), dict) else {}
        action_debug = move.get("action_debug") if isinstance(move.get("action_debug"), dict) else {}
        targets = [item for item in (action_debug.get("targets") or []) if isinstance(item, dict)]
        user_view.append(
            {
                "cycle_index": step,
                "action_id": str(move.get("action_id") or ""),
                "status": str(result.get("status") or ""),
                "action": action,
                "summary": _peek_text(str(result.get("notes") or ""), 220),
                "decision": str(decision.get("decision") or ""),
                "decision_reason": str(decision.get("decision_reason") or ""),
                "progress": progress,
                "extract_summary": {
                    "targets": len(targets),
                    "segments_done": sum(int(item.get("segments_done") or 0) for item in targets),
                    "segments_pending": sum(int(item.get("segments_pending") or 0) for item in targets),
                    "llm_requests": sum(int(item.get("llm_requests") or 0) for item in targets),
                    "llm_errors": sum(int(item.get("llm_errors") or 0) for item in targets),
                }
                if action == "extract_content"
                else {},
                "raw_event_ids": [str(v) for v in (refs.get("raw_event_ids") or [])[:6]],
                "fetch_raw_paths": [str(v) for v in (refs.get("fetch_raw_paths") or [])[:6]],
            }
        )
    payload = {
        "artifact_type": "agentic_trajectory",
        "schema_version": "0.2.0",
        "session_id": session_id,
        "run_header": {
            "prompt": prompt,
            "llm_model": llm_model,
            "max_cycles": max_cycles,
            "top_n": top_n,
            "updated_at": _utc_now(),
        },
        "raw_trace_ref": "agentic_raw.ndjson",
        "status": status,
        "stop_reason": stop_reason,
        "steps": moves,
        "user_view": user_view,
        "updated_at": _utc_now(),
    }
    write_yaml(path, payload)


def _compact_plan_for_agent(plan_state: dict) -> dict:
    todo = [item for item in (plan_state.get("todo") or []) if isinstance(item, dict)]
    active_step = plan_state.get("active_step") if isinstance(plan_state.get("active_step"), dict) else {}
    next_todos = [item for item in todo if str(item.get("status") or "").lower() != "done"]
    return {
        "active_step": {
            "step_id": str(active_step.get("step_id") or ""),
            "action": str(active_step.get("action") or ""),
            "goal": _peek_text(str(active_step.get("goal") or ""), 120),
        }
        if active_step
        else {},
        "todo_counts": _todo_status_counts(plan_state),
        "next_todos": [
            {
                "todo_id": str(item.get("todo_id") or ""),
                "action": str(item.get("action") or ""),
                "status": str(item.get("status") or ""),
                "target": _peek_text(str(item.get("target") or ""), 80),
            }
            for item in next_todos[:4]
        ],
    }


def _apply_plan_update(plan_state: dict, plan_update: dict) -> dict:
    state = {
        "active_step": dict(plan_state.get("active_step") or {}) if isinstance(plan_state.get("active_step"), dict) else {},
        "todo": [item for item in (plan_state.get("todo") or []) if isinstance(item, dict)],
    }

    if isinstance(plan_update.get("active_step"), dict):
        next_active = dict(state["active_step"])
        next_active.update({k: v for k, v in dict(plan_update.get("active_step") or {}).items() if v not in (None, "")})
        state["active_step"] = next_active

    if isinstance(plan_update.get("todo"), list):
        state["todo"] = [item for item in plan_update.get("todo") if isinstance(item, dict)]
    todo_updates = [item for item in (plan_update.get("todo_updates") or []) if isinstance(item, dict)]
    todo_append = [item for item in (plan_update.get("todo_append") or []) if isinstance(item, dict)]
    if todo_updates or todo_append:
        ordered = [dict(item) for item in state["todo"]]
        index = {str(item.get("todo_id") or ""): idx for idx, item in enumerate(ordered) if str(item.get("todo_id") or "")}
        for item in todo_updates:
            todo_id = str(item.get("todo_id") or "")
            if not todo_id or todo_id not in index:
                ordered.append(dict(item))
                if todo_id:
                    index[todo_id] = len(ordered) - 1
                continue
            prev = dict(ordered[index[todo_id]])
            prev.update({k: v for k, v in item.items() if v not in (None, "")})
            ordered[index[todo_id]] = prev
        for item in todo_append:
            todo_id = str(item.get("todo_id") or "")
            if todo_id and todo_id in index:
                prev = dict(ordered[index[todo_id]])
                prev.update({k: v for k, v in item.items() if v not in (None, "")})
                ordered[index[todo_id]] = prev
                continue
            ordered.append(dict(item))
            if todo_id:
                index[todo_id] = len(ordered) - 1
        state["todo"] = ordered[:16]

    return state


def _todo_status_counts(plan_state: dict) -> dict:
    counts = {"total": 0, "todo": 0, "doing": 0, "done": 0, "blocked": 0, "error": 0}
    todos = [item for item in (plan_state.get("todo") or []) if isinstance(item, dict)]
    counts["total"] = len(todos)
    for item in todos:
        status = str(item.get("status") or "").strip().lower()
        if status in counts:
            counts[status] += 1
        elif status:
            counts["todo"] += 1
    return counts


def _build_progress_snapshot(
    *,
    cycle_index: int,
    selected_action: str,
    plan_state: dict,
    latest_progress: dict | None = None,
    decision: str = "",
    decision_reason: str = "",
    final_candidates: int = 0,
    fallback_candidates: int = 0,
) -> dict:
    latest = dict(latest_progress or {})
    active_step = plan_state.get("active_step") if isinstance(plan_state.get("active_step"), dict) else {}
    return {
        "cycle_index": int(cycle_index),
        "action": str(selected_action or ""),
        "active_step_id": str(active_step.get("step_id") or ""),
        "todo": _todo_status_counts(plan_state),
        "decision": str(decision or ""),
        "decision_reason": str(decision_reason or ""),
        "latest_progress": latest,
        "final_candidates": int(final_candidates or 0),
        "fallback_candidates": int(fallback_candidates or 0),
    }

def _compact_known_urls_for_agent(
    url_hits: list[dict],
    *,
    extract_state_by_url: dict[str, Any] | None = None,
    max_items: int = 6,
) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    extract_state = extract_state_by_url if isinstance(extract_state_by_url, dict) else {}
    for item in url_hits:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        row_state = extract_state.get(url) if isinstance(extract_state.get(url), dict) else {}
        status = "new"
        if bool(row_state.get("completed")):
            status = "completed"
        elif bool(row_state.get("failed")):
            status = "failed"
        elif bool(row_state.get("fetched")) or int(row_state.get("segments_done") or 0) > 0:
            status = "in_progress"
        out.append(
            {
                "url": url,
                "title": _peek_text(str(item.get("url_title") or item.get("title") or ""), 100),
                "host": str(item.get("host") or _host_from_url(url)),
                "peek": _peek_text(str(item.get("peek") or ""), 120),
                "status": status,
            }
        )
        if len(out) >= max_items:
            break
    return out


def _compact_matched_papers_for_agent(papers: list[dict], *, max_items: int = 6) -> list[dict]:
    out: list[dict] = []
    for item in papers:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        out.append(
            {
                "title": _peek_text(title, 120),
                "url": str(item.get("url") or item.get("source_url") or ""),
                "year": str(item.get("year") or ""),
            }
        )
        if len(out) >= max_items:
            break
    return out


def _build_agent_memory(
    *,
    user_prompt: str,
    plan_state: dict,
    url_hits: list[dict],
    extract_state_by_url: dict[str, Any],
    papers: list[dict],
    cycle_trace: list[dict],
    stop_reason: str = "",
) -> dict:
    plan = _compact_plan_for_agent(plan_state)
    blockers: list[str] = []
    for url, row in list((extract_state_by_url or {}).items())[:16]:
        if not isinstance(row, dict):
            continue
        if bool(row.get("failed")):
            message = str(row.get("last_error") or "extract_failed").strip()
            blockers.append(_peek_text(f"{url} :: {message}", 160))
    if stop_reason:
        blockers.append(_peek_text(stop_reason, 160))
    last_step: dict[str, Any] = {}
    last_change: dict[str, Any] = {}
    recent_trace = [item for item in cycle_trace if isinstance(item, dict)]
    if recent_trace:
        tail = recent_trace[-1]
        action_input = tail.get("action_input") if isinstance(tail.get("action_input"), dict) else {}
        action_result = tail.get("action_result") if isinstance(tail.get("action_result"), dict) else {}
        delta = tail.get("delta") if isinstance(tail.get("delta"), dict) else {}
        last_step = {
            "cycle_index": int(tail.get("cycle_index") or 0),
            "action": str(action_input.get("action") or tail.get("selected_action") or ""),
            "status": str(action_result.get("status") or ""),
            "note": _peek_text(str(action_result.get("notes") or ""), 140),
        }
        last_change = {
            "retrieved_count": int(delta.get("retrieved_count") or 0),
            "shortlisted_count": int(delta.get("shortlisted_count") or 0),
            "final_candidate_delta": int(delta.get("final_candidate_delta") or 0),
        }
    return {
        "goal": _peek_text(str(user_prompt or ""), 180),
        "active_step": dict(plan.get("active_step") or {}),
        "todo": dict(plan.get("todo_counts") or {}),
        "known_urls": _compact_known_urls_for_agent(
            [item for item in url_hits if isinstance(item, dict)],
            extract_state_by_url=extract_state_by_url,
            max_items=6,
        ),
        "matched_papers": _compact_matched_papers_for_agent(
            [item for item in papers if isinstance(item, dict)],
            max_items=6,
        ),
        "blockers": _unique_nonempty(blockers, limit=6),
        "last_step": last_step,
        "last_change": last_change,
    }


def _build_agent_working_state(
    *,
    user_prompt: str,
    cycle_index: int,
    max_cycles: int,
    agent_memory: dict,
) -> dict:
    return {
        "task": _peek_text(str(user_prompt or ""), 220),
        "cycle": {"index": int(cycle_index), "max": int(max_cycles)},
        "memory": dict(agent_memory or {}),
    }


def _trace_action_input(action: str, params: dict) -> dict:
    if action == "search_web":
        return {
            "queries": [str(v) for v in (params.get("queries") or [])[:12]],
        }
    if action == "extract_content":
        targets = [row for row in (params.get("targets") or []) if isinstance(row, dict)]
        if targets:
            return {
                "targets": [
                    {
                        "url": str(row.get("url") or ""),
                        "anchor_terms": _normalize_anchor_terms(row.get("anchor_terms")),
                        "match": dict(row.get("match") or row.get("filters") or {}),
                    }
                    for row in targets[:8]
                ]
            }
        return {
            "target_ids": [str(v) for v in (params.get("target_ids") or [])[:12]],
            "urls": [str(v) for v in (params.get("urls") or [])[:12]],
            "filters": dict(params.get("filters") or {}),
            "anchor_terms": _normalize_anchor_terms(params.get("anchor_terms")),
        }
    return {"params_keys": sorted(list(params.keys()))[:16]}


def _sanitize_agent_action_params(action: str, params: dict) -> dict:
    def _first_text(value: Any) -> str:
        if isinstance(value, list):
            for item in value:
                text = str(item or "").strip()
                if text:
                    return text
            return ""
        return str(value or "").strip()

    def _normalize_match(value: Any) -> dict:
        if not isinstance(value, dict):
            return {}
        out: dict[str, Any] = {}
        institution = _first_text(value.get("institution_any") or value.get("institution"))
        author = _first_text(value.get("author_any") or value.get("author"))
        venue = _first_text(value.get("venue_any") or value.get("venue"))
        topic = _first_text(value.get("topic_any") or value.get("topic"))
        year_gte = value.get("year_gte")
        if institution:
            out["institution"] = institution
        if author:
            out["author"] = author
        if venue:
            out["venue"] = venue
        if topic:
            out["topic"] = topic
        try:
            if year_gte not in (None, ""):
                out["year_gte"] = int(year_gte)
        except (TypeError, ValueError):
            pass
        return out

    raw = params if isinstance(params, dict) else {}
    if action == "search_web":
        return {
            "queries": [str(v) for v in (raw.get("queries") or []) if str(v).strip()][:12],
        }
    if action == "extract_content":
        targets = [row for row in (raw.get("targets") or []) if isinstance(row, dict)]
        normalized_targets = []
        merged_anchor_terms: list[str] = []
        merged_filters: dict[str, Any] = {}
        for row in targets[:12]:
            url = str(row.get("url") or "").strip()
            if not url:
                continue
            target_anchor_terms = _normalize_anchor_terms(row.get("anchor_terms"))
            target_filters = _normalize_match(row.get("filters") or row.get("match"))
            normalized = {
                "url": url,
                "title": str(row.get("title") or row.get("url_title") or "").strip(),
                "why": str(row.get("why") or "").strip(),
                "anchor_terms": target_anchor_terms,
                "filters": target_filters,
            }
            if isinstance(row.get("match"), dict):
                normalized["match"] = dict(row.get("match") or {})
            normalized_targets.append(normalized)
            merged_anchor_terms.extend(target_anchor_terms)
            for key, value in target_filters.items():
                if key not in merged_filters and value not in ("", None):
                    merged_filters[key] = value
        if normalized_targets:
            return {
                "targets": normalized_targets,
                "anchor_terms": _normalize_anchor_terms(merged_anchor_terms),
                "filters": merged_filters,
            }
        return {
            "target_ids": [str(v) for v in (raw.get("target_ids") or []) if str(v).strip()][:12],
            "urls": [str(v) for v in (raw.get("urls") or []) if str(v).strip()][:12],
            "filters": dict(raw.get("filters") or {}),
            "anchor_terms": _normalize_anchor_terms(raw.get("anchor_terms")),
        }
    return {}
