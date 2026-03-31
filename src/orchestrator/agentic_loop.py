from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from src.orchestrator.agentic_result import (
    _cleanup_agentic_artifacts,
    _compact_result_payload,
)
from src.orchestrator.agentic_runtime import _ActionRuntime, _fetched_record_rows
from src.orchestrator.agentic_search import _peek_text
from src.orchestrator.agentic_state_apply import (
    _apply_extract_candidate_results,
    _apply_extract_coverage_update,
    _apply_search_action_result,
    _build_extract_coverage_summary,
    _paper_fallback_candidates,
    _paper_final_candidates,
    _set_paper_candidates,
)
from src.orchestrator.agentic_trace import _record_cycle_refs, _record_cycle_summary
from src.orchestrator.agentic_view import (
    _apply_plan_update,
    _build_agent_memory,
    _build_agent_working_state,
    _build_progress_snapshot,
    _sanitize_agent_action_params,
    _trace_action_input,
    _write_agentic_trajectory,
)
from src.retrieval.service import write_yaml

ProgressCallback = Callable[[dict], None]
ExecuteActionFn = Callable[..., dict[str, Any]]
AgentNextActionFn = Callable[..., dict[str, Any]]
EmitProgressFn = Callable[..., None]
ApplySearchActionResultFn = Callable[..., dict[str, Any]]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _unique_texts(items: list[str], *, limit: int) -> list[str]:
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


def _query_primary_venue_key(query: str) -> str:
    for token in re.findall(r"\b[A-Za-z][A-Za-z0-9\-]{2,}\b", str(query or "")):
        normalized = str(token or "").strip().upper()
        if len(normalized) < 4:
            continue
        if normalized.isdigit():
            continue
        if not re.fullmatch(r"[A-Z0-9\-]+", normalized):
            continue
        if normalized in {"PAPERS", "PROGRAM", "PROCEEDINGS", "ACCEPTED", "AUTHOR", "AUTHORS"}:
            continue
        return normalized
    return ""


def _subject_hint_from_prompt(prompt: str) -> str:
    text = str(prompt or "").strip()
    if not text:
        return ""
    match = re.search(
        r"\bby\s+([A-Za-z][A-Za-z0-9 .,&\-]{1,80}?)(?:\s+at\s+|\s+in\s+|\s+since\s+|\s+from\s+|$)",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return ""
    return re.sub(r"\s+", " ", str(match.group(1) or "").strip(" ,.;:"))


def _search_query_from_todo_target(target: str, *, user_prompt: str) -> str:
    base = re.sub(r"\s+", " ", str(target or "").strip())
    if not base:
        return ""
    subject = _subject_hint_from_prompt(user_prompt)
    if subject and subject.lower() not in base.lower():
        base = f"{base} {subject}"
    return re.sub(r"\s+", " ", base).strip()


def _planned_search_queries_from_state_delta(
    state_delta: dict[str, Any],
    *,
    user_prompt: str,
    limit: int,
) -> list[str]:
    queries: list[str] = []
    for item in [*(state_delta.get("todo_updates") or []), *(state_delta.get("todo_append") or [])]:
        if not isinstance(item, dict):
            continue
        if str(item.get("action") or "").strip().lower() != "search_web":
            continue
        status = str(item.get("status") or "").strip().lower()
        if status in {"done", "blocked", "error"}:
            continue
        query = _search_query_from_todo_target(str(item.get("target") or ""), user_prompt=user_prompt)
        if query:
            queries.append(query)
    return _unique_texts(queries, limit=max(1, int(limit or 1)))


def _merge_search_queries(
    planned_queries: list[str],
    declared_queries: list[str],
    *,
    limit: int,
) -> list[str]:
    merged = _unique_texts([str(item) for item in planned_queries], limit=max(1, int(limit or 1)))
    covered_primary_venues: set[str] = set()
    for query in merged:
        venue_key = _query_primary_venue_key(query)
        if venue_key:
            covered_primary_venues.add(venue_key)
    for query in declared_queries:
        normalized = str(query or "").strip()
        if not normalized:
            continue
        primary_venue = _query_primary_venue_key(normalized)
        if primary_venue and primary_venue in covered_primary_venues:
            continue
        lowered = normalized.lower()
        if lowered in {item.lower() for item in merged}:
            continue
        merged.append(normalized)
        if primary_venue:
            covered_primary_venues.add(primary_venue)
        if len(merged) >= max(1, int(limit or 1)):
            break
    return merged[: max(1, int(limit or 1))]


def _reconcile_completed_search_todos(
    plan_state: dict[str, Any],
    *,
    executed_queries: list[str],
    user_prompt: str,
) -> dict[str, Any]:
    normalized_executed = {
        _search_query_from_todo_target(query, user_prompt=user_prompt).lower()
        for query in executed_queries
        if _search_query_from_todo_target(query, user_prompt=user_prompt)
    }
    if not normalized_executed:
        return dict(plan_state or {})

    todo_updates: list[dict[str, Any]] = []
    for item in [row for row in (plan_state.get("todo") or []) if isinstance(row, dict)]:
        if str(item.get("action") or "").strip().lower() != "search_web":
            continue
        status = str(item.get("status") or "").strip().lower()
        if status in {"done", "blocked", "error"}:
            continue
        normalized_target = _search_query_from_todo_target(str(item.get("target") or ""), user_prompt=user_prompt).lower()
        if normalized_target and normalized_target in normalized_executed:
            todo_updates.append({"todo_id": str(item.get("todo_id") or ""), "status": "done"})

    if not todo_updates:
        return dict(plan_state or {})
    return _apply_plan_update(dict(plan_state or {}), {"todo_updates": todo_updates})


def _normalize_extract_targets(action_params: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    shared_filters = dict(action_params.get("filters") or {}) if isinstance(action_params.get("filters"), dict) else {}
    for item in action_params.get("targets") or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        out.append(
            {
                "url": url,
                "filters": dict(shared_filters | dict(item.get("filters") or {})),
            }
        )
    for url in action_params.get("urls") or []:
        normalized = str(url or "").strip()
        if not normalized or normalized in seen_urls:
            continue
        seen_urls.add(normalized)
        out.append({"url": normalized, "filters": dict(shared_filters)})
    return out


def _extract_todo_matches_target(todo_target: str, target: dict[str, Any]) -> bool:
    todo_text = str(todo_target or "").strip().lower()
    if not todo_text:
        return False
    url = str(target.get("url") or "").strip().lower()
    if url and url in todo_text:
        return True
    venue = str((target.get("filters") or {}).get("venue") or "").strip().lower()
    if venue and venue in todo_text:
        return True
    path = urlparse(url).path.lower()
    phrase_map = {
        "accepted papers": "accepted-papers",
        "papers info": "papers-info",
        "technical sessions": "technical-sessions",
        "proceedings": "proceedings",
        "presentation": "presentation",
        "program": "program",
    }
    return any(phrase in todo_text and token in path for phrase, token in phrase_map.items())


def _reconcile_extract_todos_from_coverage(
    plan_state: dict[str, Any],
    *,
    action_params: dict[str, Any],
    extract_state_by_url: dict[str, dict[str, Any]],
    url_hits: list[dict[str, Any]],
) -> dict[str, Any]:
    targets = _normalize_extract_targets(action_params)
    if not targets:
        return dict(plan_state or {})

    todos = [dict(item) for item in (plan_state.get("todo") or []) if isinstance(item, dict)]
    extract_todos = [
        item
        for item in todos
        if str(item.get("action") or "").strip().lower() == "extract_content"
        and str(item.get("status") or "").strip().lower() not in {"blocked", "error"}
    ]
    if not extract_todos:
        return dict(plan_state or {})

    coverage_summary = _build_extract_coverage_summary(
        extract_state_by_url,
        url_hits=url_hits,
    )
    status_by_url = {
        str(row.get("url") or "").strip(): dict(row)
        for row in (coverage_summary.get("url_checks") or [])
        if isinstance(row, dict) and str(row.get("url") or "").strip()
    }

    matched_urls_by_todo: dict[str, list[str]] = {}
    assigned_urls: set[str] = set()
    unmatched_todos: list[str] = []
    for item in extract_todos:
        todo_id = str(item.get("todo_id") or "").strip()
        if not todo_id:
            continue
        matched_urls = [
            str(target.get("url") or "").strip()
            for target in targets
            if _extract_todo_matches_target(str(item.get("target") or ""), target)
        ]
        if matched_urls:
            matched_urls_by_todo[todo_id] = matched_urls
            assigned_urls.update(matched_urls)
        else:
            unmatched_todos.append(todo_id)

    remaining_urls = [
        str(target.get("url") or "").strip()
        for target in targets
        if str(target.get("url") or "").strip() and str(target.get("url") or "").strip() not in assigned_urls
    ]
    if len(unmatched_todos) == 1 and remaining_urls:
        matched_urls_by_todo[unmatched_todos[0]] = list(remaining_urls)

    todo_updates: list[dict[str, Any]] = []
    for item in extract_todos:
        todo_id = str(item.get("todo_id") or "").strip()
        matched_urls = matched_urls_by_todo.get(todo_id) or []
        if not todo_id or not matched_urls:
            continue
        matched_rows = [status_by_url.get(url, {"status": "new", "has_more_results": False}) for url in matched_urls]
        if matched_rows and all(
            str(row.get("status") or "").strip().lower() == "completed" and not bool(row.get("has_more_results"))
            for row in matched_rows
        ):
            next_status = "done"
        elif any(
            str(row.get("status") or "").strip().lower() in {"in_progress", "failed"}
            or bool(row.get("has_more_results"))
            for row in matched_rows
        ):
            next_status = "doing"
        elif any(str(row.get("status") or "").strip().lower() == "completed" for row in matched_rows):
            next_status = "doing"
        else:
            next_status = "todo"
        if str(item.get("status") or "").strip().lower() != next_status:
            todo_updates.append({"todo_id": todo_id, "status": next_status})

    if not todo_updates:
        return dict(plan_state or {})
    return _apply_plan_update(dict(plan_state or {}), {"todo_updates": todo_updates})


def _append_raw_event(
    *,
    path: Path,
    trace_state: "_TraceState",
    session_id: str,
    cycle_index: int,
    action_id: str,
    event_type: str,
    payload: Any,
    raw_refs: list[str] | None = None,
) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    seq = int(trace_state.raw_event_seq or 0) + 1
    trace_state.raw_event_seq = seq
    event_id = f"raw-{seq:06d}"
    row = {
        "event_id": event_id,
        "ts": _utc_now(),
        "run_id": session_id,
        "cycle_index": cycle_index,
        "action_id": action_id,
        "event_type": event_type,
        "raw_refs": list(raw_refs or []),
        "payload": payload,
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False))
        fh.write("\n")
    return event_id


def _next_op_id(trace_state: "_TraceState", prefix: str = "op") -> str:
    seq = int(trace_state.op_event_seq or 0) + 1
    trace_state.op_event_seq = seq
    token = re.sub(r"[^a-z0-9]+", "-", str(prefix or "op").strip().lower()).strip("-") or "op"
    return f"{token}-{seq:06d}"


def _new_result_state() -> dict[str, Any]:
    return {
        "status": "running",
        "stop_reason": "",
        "cycle_count": 0,
    }


def _default_plan_state() -> dict[str, Any]:
    return {
        "active_step": {},
        "todo": [],
    }


def _make_progress_record(*, source: str, step_id: str, status: str, note: str) -> dict[str, Any]:
    return {
        "source": str(source or ""),
        "step_id": str(step_id or ""),
        "status": str(status or ""),
        "note": _peek_text(str(note or ""), 120),
    }


@dataclass
class _PaperState:
    final_candidates: list[dict[str, Any]] = field(default_factory=list)
    fallback_candidates: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "final_candidates": [dict(row) for row in self.final_candidates if isinstance(row, dict)],
            "fallback_candidates": [dict(row) for row in self.fallback_candidates if isinstance(row, dict)],
        }


@dataclass
class _RunState:
    cycle_index: int = 0
    status: str = "running"
    stop_reason: str = ""


@dataclass
class _TraceState:
    raw_event_seq: int = 0
    op_event_seq: int = 0


@dataclass
class _UrlState:
    hits: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class _ExtractState:
    by_url: dict[str, dict[str, Any]] = field(default_factory=dict)
    fetched_record_index: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class _SearchConfig:
    shortlist_size: int = 5
    results_per_query: int = 8
    timeout_s: float = 8.0
    categories: str = "general"


@dataclass
class _AgentConfig:
    max_queries_per_turn: int = 4
    llm_model: str = "deepseek/deepseek-chat"
    api_key_env: str = "DS_API_KEY"


@dataclass
class _ExtractConfig:
    llm_model: str = "deepseek/deepseek-chat"
    api_key_env: str = "DS_API_KEY"
    use_llm_extractor: bool = True


@dataclass
class _ResultConfig:
    display_limit: int = 0


@dataclass
class _AgentDecision:
    mode: str
    reason: str


@dataclass
class _PlanTurn:
    action_id: str
    raw_event_ids: list[str]
    selected_action: str
    action_params: dict[str, Any]
    agent_decision: "_AgentDecision"


@dataclass
class _ActionRun:
    raw_event_ids: list[str]
    action_result: dict[str, Any]


@dataclass
class _AgenticSearchLoop:
    paths: dict[str, Path]
    prompt: str
    session_id: str
    max_cycles: int
    searxng_url: str
    debug_retrieval: bool
    execute_action_fn: ExecuteActionFn = field(repr=False)
    agent_next_action_fn: AgentNextActionFn = field(repr=False)
    emit_progress_fn: EmitProgressFn = field(repr=False)
    apply_search_action_result_fn: ApplySearchActionResultFn = field(repr=False, default=_apply_search_action_result)
    progress_callback: ProgressCallback | None = None
    run_result: dict[str, Any] = field(default_factory=_new_result_state)
    paper_state: "_PaperState" = field(default_factory=lambda: _PaperState())
    agent_memory: dict[str, Any] = field(default_factory=dict)
    agent_plan: dict[str, Any] = field(default_factory=_default_plan_state)
    cycle_trace: list[dict[str, Any]] = field(default_factory=list)
    url_state: "_UrlState" = field(default_factory=lambda: _UrlState())
    extract_state: "_ExtractState" = field(default_factory=lambda: _ExtractState())
    run_state: "_RunState" = field(default_factory=lambda: _RunState())
    trace_state: "_TraceState" = field(default_factory=lambda: _TraceState())
    agent_config: "_AgentConfig" = field(default_factory=lambda: _AgentConfig())
    extract_config: "_ExtractConfig" = field(default_factory=lambda: _ExtractConfig())
    search_config: "_SearchConfig" = field(default_factory=lambda: _SearchConfig())
    result_config: "_ResultConfig" = field(default_factory=lambda: _ResultConfig())

    def _emit(self, **payload: Any) -> None:
        self.emit_progress_fn(self.progress_callback, **payload)

    def _append_raw(
        self,
        *,
        action_id: str,
        event_type: str,
        payload: dict[str, Any],
        refs: list[str] | None = None,
    ) -> str:
        return _append_raw_event(
            path=self.paths["raw"],
            trace_state=self.trace_state,
            session_id=self.session_id,
            cycle_index=int(self.run_state.cycle_index or 0),
            action_id=action_id,
            event_type=event_type,
            payload=payload,
            raw_refs=refs,
        )

    def _write_trajectory(self, *, status: str | None = None, stop_reason: str | None = None) -> None:
        agent_model = str(self.agent_config.llm_model or "deepseek/deepseek-chat")
        shortlist_size = max(1, int(self.search_config.shortlist_size or 1))
        _write_agentic_trajectory(
            self.paths["trajectory"],
            session_id=self.session_id,
            prompt=self.prompt,
            moves=self.cycle_trace,
            status=str(status if status is not None else self.run_state.status or "running"),
            stop_reason=str(stop_reason if stop_reason is not None else self.run_state.stop_reason or ""),
            llm_model=agent_model,
            max_cycles=self.max_cycles,
            top_n=shortlist_size,
        )

    def _write_result(self) -> None:
        agent_model = str(self.agent_config.llm_model or "deepseek/deepseek-chat")
        display_limit = max(0, int(self.result_config.display_limit or 0))
        compact_result = _compact_result_payload(
            run_result=self.run_result,
            paper_state=self.paper_state.as_dict(),
            coverage_summary=_build_extract_coverage_summary(
                self.extract_state.by_url,
                url_hits=[row for row in self.url_state.hits if isinstance(row, dict)],
            ),
            prompt=self.prompt,
            llm_model=agent_model,
            display_top_n=display_limit,
        )
        write_yaml(self.paths["result"], compact_result)

    def _execute_action(self, *, action: str, params: dict[str, Any]) -> dict[str, Any]:
        cycle_index = int(self.run_state.cycle_index or 0)
        runtime_payload = _ActionRuntime.from_loop(self).to_payload()
        result = self.execute_action_fn(
            action=action,
            session_id=self.session_id,
            cycle_index=cycle_index,
            params=params,
            paths=self.paths,
            user_prompt=self.prompt,
            searxng_url=self.searxng_url,
            search_categories=str(self.search_config.categories or "general"),
            web_results_per_query=max(1, int(self.search_config.results_per_query or 1)),
            timeout_s=float(self.search_config.timeout_s or 8.0),
            llm_model=str(self.agent_config.llm_model or "deepseek/deepseek-chat"),
            llm_extractor_model=str(self.extract_config.llm_model or "deepseek/deepseek-chat"),
            llm_api_key_env=str(self.extract_config.api_key_env or "DS_API_KEY"),
            extract_use_llm_extractor=bool(self.extract_config.use_llm_extractor),
            progress_callback=self.progress_callback,
            runtime_state=runtime_payload,
            next_op_id_fn=lambda _state, prefix="op": _next_op_id(self.trace_state, prefix),
            raw_event_fn=lambda event_type, payload, refs=None: self._append_raw(
                action_id=f"c{cycle_index:02d}",
                event_type=event_type,
                payload=payload,
                refs=refs,
            ),
        )
        _ActionRuntime.from_payload(runtime_payload).apply_to_loop(self)
        return result

    def run(self) -> Path:
        return _run_agentic_search_loop(self)


def _refresh_agent_memory(loop: _AgenticSearchLoop) -> None:
    loop.agent_memory = _build_agent_memory(
        user_prompt=loop.prompt,
        plan_state=loop.agent_plan,
        url_hits=[row for row in loop.url_state.hits if isinstance(row, dict)],
        extract_state_by_url={
            str(url): dict(row)
            for url, row in dict(loop.extract_state.by_url or {}).items()
            if str(url).strip() and isinstance(row, dict)
        },
        papers=_paper_final_candidates(loop.paper_state),
        cycle_trace=[row for row in loop.cycle_trace if isinstance(row, dict)],
        stop_reason=str(loop.run_state.stop_reason or ""),
    )


def _run_agentic_cycle(loop: _AgenticSearchLoop, cycle_index: int) -> bool:
    plan_result = _run_agent_turn(loop, cycle_index)
    if plan_result is None:
        return True

    action_ctx = _run_cycle_action(loop, cycle_index, plan_result)
    return _finalize_cycle(loop, cycle_index, plan_result, action_ctx)


def _finalize_cycle(
    loop: _AgenticSearchLoop,
    cycle_index: int,
    plan_result: _PlanTurn,
    action_ctx: _ActionRun,
) -> bool:
    action_id = str(plan_result.action_id)
    selected_action = str(plan_result.selected_action)
    planned_queries = list((plan_result.action_params or {}).get("queries") or [])
    agent_decision = plan_result.agent_decision
    raw_event_ids = list(action_ctx.raw_event_ids)
    action_result = dict(action_ctx.action_result)
    raw_candidates = list(action_result.get("raw_candidates") or [])
    extracted_paper_candidates = list(action_result.get("paper_candidates") or [])

    if selected_action == "search_web":
        loop.agent_plan = _reconcile_completed_search_todos(
            loop.agent_plan,
            executed_queries=planned_queries,
            user_prompt=loop.prompt,
        )
        url_shortlisted = _finalize_search_action(
            loop=loop,
            cycle_index=cycle_index,
            raw_candidates=raw_candidates,
            shortlist_hints=action_result.get("shortlist_hints"),
        )
    else:
        url_shortlisted = _finalize_extract_action(
            loop=loop,
            action_params=dict(plan_result.action_params or {}),
            extracted_paper_candidates=extracted_paper_candidates,
            action_result=action_result,
        )
    cycle_delta = _record_cycle_summary(
        loop.cycle_trace,
        selected_action=selected_action,
        action_result=action_result,
        raw_candidates=raw_candidates,
        extracted_paper_candidates=extracted_paper_candidates,
        url_shortlisted=url_shortlisted,
    )

    decision_info = _decide_cycle_outcome(
        loop=loop,
        cycle_index=cycle_index,
        selected_action=selected_action,
        planned_queries=planned_queries,
        agent_decision=agent_decision,
        action_result=action_result,
        url_shortlisted=url_shortlisted,
        extracted_paper_candidates=extracted_paper_candidates,
    )
    decision = str(decision_info["decision"])
    decision_reason = str(decision_info["decision_reason"])
    cycle_stop_reason = str(decision_info["stop_reason"])

    before_final_count = len(_paper_final_candidates(loop.paper_state))
    if selected_action == "extract_content":
        extract_finalize = _finalize_extract_candidates(
            loop=loop,
            action_id=action_id,
            action_result=action_result,
            extracted_paper_candidates=extracted_paper_candidates,
            raw_event_ids=raw_event_ids,
        )
        extracted_paper_candidates = list(extract_finalize["paper_candidates"])
        raw_event_ids = list(extract_finalize["raw_event_ids"])

    after_final_count = len(_paper_final_candidates(loop.paper_state))
    cycle_delta["final_candidate_delta"] = after_final_count - before_final_count
    shortlisted_count = len(url_shortlisted) if selected_action == "search_web" else len(extracted_paper_candidates)
    latest_tool_progress = _make_progress_record(
        source="tool",
        step_id=str((loop.agent_plan.get("active_step") or {}).get("step_id") or ""),
        status=str(action_result.get("status") or ""),
        note=str(action_result.get("notes") or ""),
    )
    _record_cycle_outcome(
        loop=loop,
        cycle_index=cycle_index,
        action_id=action_id,
        selected_action=selected_action,
        decision=decision,
        decision_reason=decision_reason,
        cycle_stop_reason=cycle_stop_reason,
        raw_candidate_count=len(raw_candidates),
        shortlisted_count=shortlisted_count,
        action_result=action_result,
        cycle_delta=cycle_delta,
        raw_event_ids=raw_event_ids,
        latest_progress=latest_tool_progress,
    )
    return _commit_cycle_state(
        loop=loop,
        decision=decision,
        cycle_stop_reason=cycle_stop_reason,
    )


def _finalize_search_action(
    *,
    loop: _AgenticSearchLoop,
    cycle_index: int,
    raw_candidates: list[dict[str, Any]],
    shortlist_hints: Any,
) -> list[dict[str, Any]]:
    shortlist_size = max(1, int(loop.search_config.shortlist_size or 1))
    finalized = loop.apply_search_action_result_fn(
        session_id=loop.session_id,
        cycle_index=cycle_index,
        raw_candidates=raw_candidates,
        shortlist_hints=shortlist_hints,
        existing_url_hits=[row for row in loop.url_state.hits if isinstance(row, dict)],
        shortlist_size=shortlist_size,
        max_cycles=loop.max_cycles,
    )
    loop.url_state.hits = [dict(row) for row in finalized["url_hits"] if isinstance(row, dict)]
    return [dict(row) for row in finalized["url_shortlisted"] if isinstance(row, dict)]


def _finalize_extract_action(
    *,
    loop: _AgenticSearchLoop,
    action_params: dict[str, Any],
    extracted_paper_candidates: list[dict[str, Any]],
    action_result: dict[str, Any],
) -> list[dict[str, Any]]:
    _ = extracted_paper_candidates
    _apply_extract_coverage_update(loop.extract_state.by_url, action_result=action_result)
    loop.agent_plan = _reconcile_extract_todos_from_coverage(
        loop.agent_plan,
        action_params=action_params,
        extract_state_by_url=loop.extract_state.by_url,
        url_hits=[row for row in loop.url_state.hits if isinstance(row, dict)],
    )
    return []


def _finalize_extract_candidates(
    *,
    loop: _AgenticSearchLoop,
    action_id: str,
    action_result: dict[str, Any],
    extracted_paper_candidates: list[dict[str, Any]],
    raw_event_ids: list[str],
) -> dict[str, Any]:
    _ = action_id
    _ = action_result
    _ = raw_event_ids
    finalized = _apply_extract_candidate_results(
        prompt=loop.prompt,
        agent_plan=loop.agent_plan,
        existing_final_candidates=_paper_final_candidates(loop.paper_state),
        existing_fallback_candidates=_paper_fallback_candidates(loop.paper_state),
        extracted_paper_candidates=extracted_paper_candidates,
    )
    _set_paper_candidates(
        loop.paper_state,
        final_candidates=[dict(row) for row in finalized["final_candidates"] if isinstance(row, dict)],
        fallback_candidates=[dict(row) for row in finalized["fallback_candidates"] if isinstance(row, dict)],
    )
    return {
        "paper_candidates": [dict(row) for row in finalized["paper_candidates"] if isinstance(row, dict)],
        "raw_event_ids": list(raw_event_ids),
    }


def _decide_cycle_outcome(
    *,
    loop: _AgenticSearchLoop,
    cycle_index: int,
    selected_action: str,
    planned_queries: list[str],
    agent_decision: _AgentDecision,
    action_result: dict[str, Any],
    url_shortlisted: list[dict[str, Any]],
    extracted_paper_candidates: list[dict[str, Any]],
) -> dict[str, str]:
    agent_decision_mode = str(agent_decision.mode or "").strip().lower()
    agent_decision_reason = str(agent_decision.reason or "").strip()
    agent_should_stop = agent_decision_mode == "stop"
    if bool(action_result.get("stop", False)):
        return {
            "decision": "stop",
            "decision_reason": "action_stop",
            "stop_reason": str(action_result.get("stop_reason") or "action_stop"),
        }
    if selected_action == "search_web" and not url_shortlisted:
        return {
            "decision": "stop",
            "decision_reason": "no_candidates",
            "stop_reason": "no_candidates",
        }
    if agent_should_stop:
        return {
            "decision": "stop",
            "decision_reason": "llm_converged",
            "stop_reason": agent_decision_reason or "llm_converged",
        }
    if cycle_index >= loop.max_cycles:
        return {
            "decision": "stop",
            "decision_reason": "max_cycles_reached",
            "stop_reason": "max_cycles_reached",
        }
    if selected_action == "search_web" and not planned_queries:
        return {
            "decision": "stop",
            "decision_reason": "agent_no_queries",
            "stop_reason": "agent_no_queries",
        }
    return {
        "decision": "continue",
        "decision_reason": "llm_continue",
        "stop_reason": "",
    }


def _record_cycle_outcome(
    *,
    loop: _AgenticSearchLoop,
    cycle_index: int,
    action_id: str,
    selected_action: str,
    decision: str,
    decision_reason: str,
    cycle_stop_reason: str,
    raw_candidate_count: int,
    shortlisted_count: int,
    action_result: dict[str, Any],
    cycle_delta: dict[str, Any],
    raw_event_ids: list[str],
    latest_progress: dict[str, Any] | None = None,
) -> None:
    if loop.cycle_trace:
        loop.cycle_trace[-1]["delta"] = cycle_delta
    _record_cycle_refs(
        loop.cycle_trace,
        fetched_records=_fetched_record_rows(loop.extract_state),
        raw_event_ids=raw_event_ids,
    )
    loop._emit(
        event="agentic_cycle_decision",
        cycle_index=cycle_index,
        action_id=action_id,
        decision=decision,
        decision_reason=decision_reason,
        stop_reason=cycle_stop_reason,
        raw_candidates=raw_candidate_count,
        shortlisted=shortlisted_count,
        final_candidates=len(_paper_final_candidates(loop.paper_state)),
    )
    loop.run_result["cycle_count"] = cycle_index
    progress_snapshot = _build_progress_snapshot(
        cycle_index=cycle_index,
        selected_action=selected_action,
        plan_state=loop.agent_plan,
        latest_progress=latest_progress,
        decision=decision,
        decision_reason=decision_reason,
        final_candidates=len(_paper_final_candidates(loop.paper_state)),
        fallback_candidates=len(_paper_fallback_candidates(loop.paper_state)),
    )
    if loop.cycle_trace:
        loop.cycle_trace[-1]["decision"] = {
            "decision": decision,
            "decision_reason": decision_reason,
            "stop_reason": cycle_stop_reason,
        }
        loop.cycle_trace[-1]["progress"] = progress_snapshot
    loop._emit(
        event="agentic_progress_snapshot",
        cycle_index=cycle_index,
        action_id=action_id,
        progress=progress_snapshot,
    )


def _commit_cycle_state(
    *,
    loop: _AgenticSearchLoop,
    decision: str,
    cycle_stop_reason: str,
) -> bool:
    loop._write_trajectory(status="running", stop_reason="")
    _refresh_agent_memory(loop)

    if decision == "stop":
        loop.run_state.stop_reason = str(cycle_stop_reason or "")
        _refresh_agent_memory(loop)
        return True
    return False


def _run_cycle_action(
    loop: _AgenticSearchLoop,
    cycle_index: int,
    plan_result: _PlanTurn,
) -> _ActionRun:
    action_id = str(plan_result.action_id)
    raw_event_ids = list(plan_result.raw_event_ids)
    selected_action = str(plan_result.selected_action)
    action_params = dict(plan_result.action_params)

    _start_cycle_action(
        loop=loop,
        cycle_index=cycle_index,
        action_id=action_id,
        selected_action=selected_action,
        action_params=action_params,
        raw_event_ids=raw_event_ids,
    )

    action_result = loop._execute_action(action=selected_action, params=action_params)
    _finish_cycle_action(
        loop=loop,
        cycle_index=cycle_index,
        action_id=action_id,
        selected_action=selected_action,
        action_result=action_result,
        raw_event_ids=raw_event_ids,
    )

    return _ActionRun(raw_event_ids=raw_event_ids, action_result=action_result)


def _start_cycle_action(
    *,
    loop: _AgenticSearchLoop,
    cycle_index: int,
    action_id: str,
    selected_action: str,
    action_params: dict[str, Any],
    raw_event_ids: list[str],
) -> None:
    action_params["debug_retrieval"] = loop.debug_retrieval
    target_urls: list[str] = []
    for url in action_params.get("urls") or []:
        value = str(url or "").strip()
        if value:
            target_urls.append(value)
    for item in action_params.get("targets") or []:
        if not isinstance(item, dict):
            continue
        value = str(item.get("url") or "").strip()
        if value and value not in target_urls:
            target_urls.append(value)
    queries = [str(value).strip() for value in (action_params.get("queries") or []) if str(value).strip()]
    action_input_raw_id = loop._append_raw(
        action_id=action_id,
        event_type="action_input",
        payload={"action": selected_action, "params": action_params},
    )
    raw_event_ids.append(action_input_raw_id)
    loop._emit(
        event="agentic_action_start",
        cycle_index=cycle_index,
        action_id=action_id,
        action=selected_action,
        active_step_id=str((loop.agent_plan.get("active_step") or {}).get("step_id") or ""),
        raw_event_id=action_input_raw_id,
        query_count=len(queries),
        queries=queries[:6],
        target_count=len(target_urls),
        target_urls=target_urls[:6],
    )
    loop._write_trajectory(status="running", stop_reason="")


def _finish_cycle_action(
    *,
    loop: _AgenticSearchLoop,
    cycle_index: int,
    action_id: str,
    selected_action: str,
    action_result: dict[str, Any],
    raw_event_ids: list[str],
) -> None:
    action_output_raw_id = loop._append_raw(
        action_id=action_id,
        event_type="action_output",
        payload=action_result,
    )
    raw_event_ids.append(action_output_raw_id)
    loop._emit(
        event="agentic_action_done",
        cycle_index=cycle_index,
        action_id=action_id,
        action=selected_action,
        status=str(action_result.get("status") or ""),
        notes=_peek_text(str(action_result.get("notes") or ""), 160),
        raw_event_id=action_output_raw_id,
        requested_url_count=len(action_result.get("requested_urls") or []),
        extracted_count=len(action_result.get("paper_candidates") or []),
        candidate_url_count=len(action_result.get("candidate_urls") or []),
        paper_dedup_clusters=int(action_result.get("paper_dedup_clusters") or 0),
        paper_dedup_reduced=int(action_result.get("paper_dedup_reduced") or 0),
    )


def _run_agent_turn(loop: _AgenticSearchLoop, cycle_index: int) -> _PlanTurn | None:
    loop.run_state.cycle_index = int(cycle_index)
    _refresh_agent_memory(loop)
    loop._emit(
        event="agentic_cycle_start",
        cycle_index=cycle_index,
        max_cycles=loop.max_cycles,
    )
    agent_input = _build_agent_working_state(
        user_prompt=loop.prompt,
        cycle_index=cycle_index,
        max_cycles=loop.max_cycles,
        agent_memory=loop.agent_memory,
    )
    action_id = f"c{cycle_index:02d}"
    raw_event_ids, agent_op_id = _start_agent_turn(
        loop=loop,
        cycle_index=cycle_index,
        action_id=action_id,
        agent_input=agent_input,
    )
    try:
        agent_output = loop.agent_next_action_fn(
            working_state=agent_input,
            queries_per_cycle=max(1, int(loop.agent_config.max_queries_per_turn or 1)),
            model=str(loop.agent_config.llm_model or "deepseek/deepseek-chat"),
            api_key_env=str(loop.agent_config.api_key_env or "DS_API_KEY"),
        )
    except Exception as exc:
        _fail_agent_turn(
            loop=loop,
            action_id=action_id,
            agent_op_id=agent_op_id,
            raw_event_ids=raw_event_ids,
            exc=exc,
        )
        raise

    normalized_output = _normalize_agent_turn_output(agent_output)
    selected_action = str(normalized_output["selected_action"])
    planned_queries = list(normalized_output["planned_queries"])
    action_params = dict(normalized_output["action_params"])
    state_delta = dict(normalized_output["state_delta"])
    latest_progress = dict(normalized_output["latest_progress"])
    agent_decision = normalized_output["agent_decision"]
    agent_debug = dict(normalized_output["agent_debug"])
    if selected_action == "search_web":
        declared_queries = _planned_search_queries_from_state_delta(
            state_delta,
            user_prompt=loop.prompt,
            limit=max(1, int(loop.agent_config.max_queries_per_turn or 1)),
        )
        if declared_queries:
            merged_queries = _merge_search_queries(
                list(action_params.get("queries") or []),
                declared_queries,
                limit=max(1, int(loop.agent_config.max_queries_per_turn or 1)),
            )
            action_params["queries"] = merged_queries
            planned_queries = list(merged_queries)
    _finish_agent_turn(
        loop=loop,
        cycle_index=cycle_index,
        action_id=action_id,
        agent_output=agent_output,
        raw_event_ids=raw_event_ids,
        agent_op_id=agent_op_id,
        agent_debug=agent_debug,
        selected_action=selected_action,
        planned_queries=planned_queries,
    )

    if state_delta:
        loop.agent_plan = _apply_plan_update(loop.agent_plan, state_delta)
    _refresh_agent_memory(loop)
    if _should_stop_after_agent_turn(agent_decision, selected_action, planned_queries):
        loop.run_state.stop_reason = str(agent_decision.reason or "").strip() or "llm_converged"
        loop._emit(
            event="agentic_cycle_decision",
            cycle_index=cycle_index,
            action_id=action_id,
            decision="stop",
            decision_reason="agent_stop",
            stop_reason=str(loop.run_state.stop_reason or ""),
        )
        return None

    _seed_cycle_trace(
        loop=loop,
        cycle_index=cycle_index,
        action_id=action_id,
        selected_action=selected_action,
        action_params=action_params,
        state_delta=state_delta,
        latest_progress=latest_progress,
    )
    return _PlanTurn(
        action_id=action_id,
        raw_event_ids=raw_event_ids,
        selected_action=selected_action,
        action_params=action_params,
        agent_decision=agent_decision,
    )


def _normalize_agent_turn_output(agent_output: dict[str, Any]) -> dict[str, Any]:
    selected_action = str(agent_output.get("action") or "search_web")
    planned_queries = list(agent_output.get("queries") or [])
    action_params = _sanitize_agent_action_params(selected_action, dict(agent_output.get("params") or {}))
    if selected_action == "search_web" and not list(action_params.get("queries") or []):
        action_params["queries"] = planned_queries
    state_delta = dict(agent_output.get("state_delta") or {})
    progress_update = dict(agent_output.get("progress") or {})
    agent_decision = agent_output.get("decision") if isinstance(agent_output.get("decision"), dict) else {}
    latest_progress: dict[str, Any] = {}
    if progress_update:
        latest_progress = _make_progress_record(
            source="agent",
            step_id=str(progress_update.get("step_id") or ""),
            status=str(progress_update.get("status") or ""),
            note=str(progress_update.get("note") or ""),
        )
    return {
        "selected_action": selected_action,
        "planned_queries": planned_queries,
        "action_params": action_params,
        "state_delta": state_delta,
        "latest_progress": latest_progress,
        "agent_decision": _AgentDecision(
            mode=str(agent_decision.get("mode") or "").strip().lower(),
            reason=str(agent_decision.get("reason") or "").strip(),
        ),
        "agent_debug": agent_output.get("_debug") if isinstance(agent_output.get("_debug"), dict) else {},
    }


def _seed_cycle_trace(
    *,
    loop: _AgenticSearchLoop,
    cycle_index: int,
    action_id: str,
    selected_action: str,
    action_params: dict[str, Any],
    state_delta: dict[str, Any],
    latest_progress: dict[str, Any],
) -> None:
    loop.cycle_trace.append(
        {
            "action_id": action_id,
            "cycle_index": cycle_index,
            "timestamp": _utc_now(),
            "state_delta": state_delta,
            "action_input": _trace_action_input(selected_action, action_params),
            "action_result": {
                "status": "running",
                "notes": "action_started",
            },
            "progress": _build_progress_snapshot(
                cycle_index=cycle_index,
                selected_action=selected_action,
                plan_state=loop.agent_plan,
                latest_progress=latest_progress,
            ),
            "delta": {},
        }
    )


def _should_stop_after_agent_turn(
    agent_decision: _AgentDecision,
    selected_action: str,
    planned_queries: list[str],
) -> bool:
    return (
        str(agent_decision.mode or "").strip().lower() == "stop"
        and selected_action == "search_web"
        and not planned_queries
    )


def _start_agent_turn(
    *,
    loop: _AgenticSearchLoop,
    cycle_index: int,
    action_id: str,
    agent_input: dict[str, Any],
) -> tuple[list[str], str]:
    raw_event_ids: list[str] = []
    request_raw_id = loop._append_raw(action_id=action_id, event_type="agent_request", payload=agent_input)
    raw_event_ids.append(request_raw_id)
    loop._emit(
        event="agentic_llm_agent_request",
        cycle_index=cycle_index,
        action_id=action_id,
        payload=agent_input,
        raw_event_id=request_raw_id,
    )
    agent_op_id = _next_op_id(loop.trace_state, "agent_llm")
    raw_event_ids.append(
        loop._append_raw(
            action_id=action_id,
            event_type="op_start",
            payload={
                "op_id": agent_op_id,
                "op_type": "agent_llm",
                "component": "agent",
                "model": str(loop.agent_config.llm_model or "deepseek/deepseek-chat"),
                "input_chars": 0,
                "input_tokens_est": 0,
                "request_ref": request_raw_id,
            },
            refs=[request_raw_id],
        )
    )
    return raw_event_ids, agent_op_id


def _fail_agent_turn(
    *,
    loop: _AgenticSearchLoop,
    action_id: str,
    agent_op_id: str,
    raw_event_ids: list[str],
    exc: Exception,
) -> None:
    raw_event_ids.append(
        loop._append_raw(
            action_id=action_id,
            event_type="op_end",
            payload={
                "op_id": agent_op_id,
                "op_type": "agent_llm",
                "component": "agent",
                "model": str(loop.agent_config.llm_model or "deepseek/deepseek-chat"),
                "status": "error",
                "error": f"{type(exc).__name__}:{exc}",
            },
        )
    )


def _finish_agent_turn(
    *,
    loop: _AgenticSearchLoop,
    cycle_index: int,
    action_id: str,
    agent_output: dict[str, Any],
    raw_event_ids: list[str],
    agent_op_id: str,
    agent_debug: dict[str, Any],
    selected_action: str,
    planned_queries: list[str],
) -> None:
    agent_response_raw_id = loop._append_raw(
        action_id=action_id,
        event_type="agent_response",
        payload={**agent_output, "_debug": agent_debug},
    )
    raw_event_ids.append(agent_response_raw_id)
    raw_event_ids.append(
        loop._append_raw(
            action_id=action_id,
            event_type="op_end",
            payload={
                "op_id": agent_op_id,
                "op_type": "agent_llm",
                "component": "agent",
                "model": str(loop.agent_config.llm_model or "deepseek/deepseek-chat"),
                "status": "ok",
                "input_chars": int(agent_debug.get("input_chars") or 0),
                "input_tokens_est": int(agent_debug.get("input_tokens_est") or 0),
                "response_ref": agent_response_raw_id,
            },
            refs=[agent_response_raw_id],
        )
    )
    loop._emit(
        event="agentic_llm_agent_response",
        cycle_index=cycle_index,
        action_id=action_id,
        payload=agent_output,
        raw_event_id=agent_response_raw_id,
        selected_action=selected_action,
        planned_queries=list(planned_queries),
    )


def _run_agentic_search_loop(loop: _AgenticSearchLoop) -> Path:
    if not loop.searxng_url:
        loop.run_state.status = "failed"
        loop.run_state.stop_reason = "missing_env:SEARXNG_URL"
        loop.run_result["status"] = "failed"
        loop.run_result["stop_reason"] = str(loop.run_state.stop_reason or "")
        loop._write_trajectory(
            status=str(loop.run_state.status or "failed"),
            stop_reason=str(loop.run_state.stop_reason or ""),
        )
        loop._write_result()
        _cleanup_agentic_artifacts(loop.paths)
        loop._emit(event="agentic_failed", reason=str(loop.run_state.stop_reason or ""))
        return loop.paths["result"]

    loop.paths["raw"].parent.mkdir(parents=True, exist_ok=True)
    loop.paths["raw"].write_text("", encoding="utf-8")
    loop._write_trajectory(status="running", stop_reason="")
    loop._write_result()

    try:
        for cycle_index in range(1, loop.max_cycles + 1):
            if _run_agentic_cycle(loop, cycle_index):
                break

        if not str(loop.run_state.stop_reason or ""):
            loop.run_state.stop_reason = "max_cycles_reached"

        loop.run_state.status = "completed"
        loop.run_result["status"] = "completed"
        loop.run_result["stop_reason"] = str(loop.run_state.stop_reason or "")
        loop.run_result["cycle_count"] = int(loop.run_state.cycle_index or 0)
        loop._emit(
            event="agentic_complete",
            status="completed",
            cycle_count=int(loop.run_state.cycle_index or 0),
            stop_reason=str(loop.run_state.stop_reason or ""),
            final_candidates=len(_paper_final_candidates(loop.paper_state)),
        )
    except KeyboardInterrupt:
        loop.run_state.stop_reason = "interrupted:keyboard"
        loop.run_state.status = "failed"
        loop.run_result["status"] = "failed"
        loop.run_result["stop_reason"] = str(loop.run_state.stop_reason or "")
        loop.run_result["cycle_count"] = int(loop.run_state.cycle_index or 0)
        loop._emit(event="agentic_failed", reason=str(loop.run_state.stop_reason or ""))
    except Exception as exc:
        loop.run_state.stop_reason = f"agentic_error:{type(exc).__name__}:{exc}"
        loop.run_state.status = "failed"
        loop.run_result["status"] = "failed"
        loop.run_result["stop_reason"] = str(loop.run_state.stop_reason or "")
        loop.run_result["cycle_count"] = int(loop.run_state.cycle_index or 0)
        loop._emit(event="agentic_failed", reason=str(loop.run_state.stop_reason or ""))

    loop._write_trajectory(
        status=str(loop.run_state.status or "running"),
        stop_reason=str(loop.run_result.get("stop_reason") or loop.run_state.stop_reason or ""),
    )
    loop._write_result()
    _cleanup_agentic_artifacts(loop.paths)
    return loop.paths["result"]
