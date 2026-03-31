from __future__ import annotations

import json
from typing import Any


def _agent_output_contract() -> dict[str, Any]:
    return {
        "decision": {
            "mode": "continue|stop",
            "reason": "str",
        },
        "state_delta": {
            "active_step": {"step_id": "str", "action": "str", "goal": "str"},
            "todo_updates": [{"todo_id": "str", "action": "search_web|extract_content", "status": "todo|doing|done|blocked|error", "target": "str"}],
            "todo_append": [{"todo_id": "str", "action": "search_web|extract_content", "status": "todo|doing|done|blocked|error", "target": "str"}],
        },
        "progress": {
            "phase": "planning|searching|extracting|done|blocked",
            "status": "planned|running|done|blocked",
            "note": "str",
        },
        "action": {
            "name": "search_web|extract_content",
            "params": "see action_contracts",
        },
        "action_contracts": {
            "search_web": {
                "queries": ["str"],
            },
            "extract_content": {
                "targets": [
                    {
                        "url": "str",
                        "text_filters": {
                            "literal_any": ["str"],
                            "regex_any": ["str"],
                        },
                        "semantic_focus": "str?",
                        "filters": {
                            "institution": "str?",
                            "author": "str?",
                            "venue": "str?",
                            "topic": "str?",
                            "year_gte": "int?",
                        },
                    }
                ]
            },
        },
    }


def _agent_system_prompt() -> str:
    return (
        "Role: academic paper agentic search planner. "
        "Goal: produce paper identifiers (title, doi, arxiv, source_url) with evidence. "
        "Methods: dynamic multi-hop search with explicit planning, shortlisting, and extraction. "
        "SOP: (1) decompose cues: topic/author/venue/institution/year; "
        "(2) set the current active_step and todo; "
        "(3) search and shortlist URLs; "
        "(4) extract paper facts from chosen URLs using explicit text filters and minimal match constraints; "
        "(5) update progress and continue until coverage is sufficient. "
        "Action Catalog: "
        "search_web(params.queries[]) => ranked URL hits with title/url/peek; "
        "extract_content(params.targets[{url, text_filters?, semantic_focus?, filters?}]) => fetch + extract paper facts for chosen URLs. "
        "Rules: one search query per venue when multiple venues are in scope. "
        "Do not combine many venue names in one query. "
        "Do not broaden one venue into multiple near-duplicate queries unless the first query clearly failed to land an official venue page. "
        "memory.next_todos lists the concrete pending tasks; treat those as real obligations, not just hints. "
        "memory.priority_extract_urls lists unfinished or failed high-value listing pages; treat those as stronger obligations than detail-page exploration. "
        "memory.known_urls entries include page_role and page_family; URLs in the same page_family are likely overlapping venue pages, so pick one representative first and only add a companion page when it clearly fills missing fields. "
        "If pending search_web tasks remain for named venues, keep covering those venue searches before switching to extract_content. "
        "If memory.priority_extract_urls is non-empty, continue those listing pages before switching to new detail pages, PDFs, or generic conference homepages. "
        "Prefer accepted/program listing pages over proceedings PDFs or generic conference homepages. "
        "Do not choose PDFs for extraction in the current HTML-first flow. "
        "If venue cues exist, prioritize conference accepted/program pages. "
        "If author cues dominate, prioritize DBLP/arXiv/OpenReview/author pages and then filter. "
        "If topic cue dominates, first land likely venues or influential authors/institutions, then expand. "
        "The app decides result limits, fetch behavior, batching, retries, and extraction mechanics. "
        "For extract_content, decide per URL whether lexical trimming is helpful. "
        "Use text_filters.literal_any for grep-like phrases or names, text_filters.regex_any for compact regex patterns when that is genuinely better, "
        "and semantic_focus for short semantic guidance when lexical trimming is weak or not enough. "
        "Do not force all three; only include the parts that help for that URL. "
        "Avoid generic schema words such as paper, title, doi, arxiv, source_url, session, or bare years as text filters. "
        "Use filters only for minimal semantic constraints such as institution/author/year; do not restate generic extraction schema or tool mechanics. "
        "Do not pass low-level extraction controls such as batch size, coverage windows, target_ids, or token budgets; the app decides those. "
        "state_delta is delta-based: only send fields that changed, do not restate the full active_step/todo state every cycle. "
        "state_delta is the authoritative internal update; progress should summarize that update for the user. "
        "For long venue listing pages, prefer exhaustive extraction coverage before unrelated new searches. "
        "Return strict JSON only with keys: decision, state_delta, progress, action. "
        "Do not emit separate rationale, stop, stop_reason, plan_update, or progress_update fields."
    )


def _build_agent_messages(
    *,
    working_state: dict,
    queries_per_cycle: int,
    supported_actions: list[str],
) -> list[dict[str, Any]]:
    return [
        {
            "role": "system",
            "content": _agent_system_prompt(),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "working_state": working_state,
                    "available_actions": supported_actions,
                    "max_queries": queries_per_cycle,
                    "output_contract": _agent_output_contract(),
                },
                ensure_ascii=True,
            ),
        },
    ]


def _parse_agent_action_response(
    *,
    payload: dict,
    queries_per_cycle: int,
    supported_actions: set[str],
    debug_metrics: dict[str, int] | None = None,
) -> dict[str, Any]:
    decision = payload.get("decision") if isinstance(payload.get("decision"), dict) else {}
    state_delta = payload.get("state_delta") if isinstance(payload.get("state_delta"), dict) else {}
    progress = payload.get("progress") if isinstance(payload.get("progress"), dict) else {}
    action_block = payload.get("action") if isinstance(payload.get("action"), dict) else {}

    action = str(action_block.get("name") or "").strip().lower()
    if action not in supported_actions:
        action = "search_web"
    params = action_block.get("params") if isinstance(action_block.get("params"), dict) else {}
    decision_mode = str(decision.get("mode") or "").strip().lower()
    decision_reason = str(decision.get("reason") or "").strip()
    if decision_mode not in {"continue", "stop"}:
        decision_mode = "continue"
    normalized_decision = {
        "mode": decision_mode,
        "reason": decision_reason,
    }
    normalized_state_delta = dict(state_delta)
    normalized_progress = dict(progress) if isinstance(progress, dict) else {}
    if "step_id" not in normalized_progress:
        active_step = normalized_state_delta.get("active_step") if isinstance(normalized_state_delta.get("active_step"), dict) else {}
        normalized_progress["step_id"] = str(active_step.get("step_id") or "")
    queries = []
    if isinstance(params.get("queries"), list):
        seen: set[str] = set()
        for item in params.get("queries") or []:
            query = str(item or "").strip()
            if not query:
                continue
            lowered = query.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            queries.append(query)
            if len(queries) >= queries_per_cycle:
                break
    return {
        "action": action,
        "decision": normalized_decision,
        "state_delta": normalized_state_delta,
        "progress": normalized_progress,
        "queries": queries,
        "params": params,
        "_debug": {
            "input_chars": int((debug_metrics or {}).get("input_chars") or 0),
            "input_tokens_est": int((debug_metrics or {}).get("input_tokens_est") or 0),
        },
    }
