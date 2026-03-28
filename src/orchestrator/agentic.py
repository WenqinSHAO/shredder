from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from src.orchestrator import agentic_actions as actions_mod
from src.orchestrator.agentic_contracts import (
    _build_agent_messages,
    _parse_agent_action_response,
)
from src.orchestrator.agentic_llm import (
    estimate_messages_metrics as _estimate_messages_metrics_impl,
    openai_complete_json as _openai_complete_json_impl,
)
from src.orchestrator.agentic_loop import (
    _AgentConfig,
    _AgenticSearchLoop,
    _ExtractConfig,
    _ResultConfig,
    _SearchConfig,
)
from src.orchestrator.agentic_search import (
    _host_from_url,
    _make_hit_id,
    _peek_text,
    _to_url_hits as _to_url_hits_impl,
)
from src.orchestrator.agentic_state_apply import (
    _apply_search_action_result as _apply_search_action_result_impl,
)
from src.utils.paths import project_dir
from src.utils.yamlx import load

AGENT_SUPPORTED_ACTIONS = {
    "search_web",
    "extract_content",
}

ProgressCallback = Callable[[dict], None]


def _emit_progress(progress_callback: ProgressCallback | None, *, event: str, **payload) -> None:
    if progress_callback is None:
        return
    body = {"event": event}
    body.update(payload)
    progress_callback(body)


def _to_url_hits(session_id: str, cycle_index: int, shortlisted: list[dict], top_n: int) -> list[dict]:
    return _to_url_hits_impl(
        session_id,
        cycle_index,
        shortlisted,
        top_n,
        make_hit_id_fn=_make_hit_id,
        peek_text_fn=_peek_text,
        host_from_url_fn=_host_from_url,
    )


def _apply_search_action_result(
    *,
    session_id: str,
    cycle_index: int,
    raw_candidates: list[dict[str, Any]],
    shortlist_hints: Any,
    existing_url_hits: list[dict[str, Any]],
    shortlist_size: int,
    max_cycles: int,
) -> dict[str, Any]:
    return _apply_search_action_result_impl(
        session_id=session_id,
        cycle_index=cycle_index,
        raw_candidates=raw_candidates,
        shortlist_hints=shortlist_hints,
        existing_url_hits=existing_url_hits,
        shortlist_size=shortlist_size,
        max_cycles=max_cycles,
        to_url_hits_fn=_to_url_hits,
    )


def _retrieval_dir(pdir: Path) -> Path:
    return pdir / "artifacts" / "retrieval"


def _agentic_paths(pdir: Path) -> dict[str, Path]:
    rdir = _retrieval_dir(pdir)
    return {
        "result": rdir / "agentic_result.yaml",
        "trajectory": rdir / "agentic_trajectory.yaml",
        "raw": rdir / "agentic_raw.ndjson",
    }


def _new_session_id(prompt: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    digest = hashlib.sha1(prompt.strip().lower().encode("utf-8")).hexdigest()[:10]
    return f"agentic-{stamp}-{digest}"


def _read_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _read_str(value: Any, default: str) -> str:
    text = str(value or "").strip()
    return text if text else default


def _read_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _agent_next_action_llm(
    *,
    working_state: dict,
    queries_per_cycle: int,
    model: str,
    api_key_env: str,
) -> dict:
    messages = _build_agent_messages(
        working_state=working_state,
        queries_per_cycle=queries_per_cycle,
        supported_actions=sorted(AGENT_SUPPORTED_ACTIONS),
    )
    message_metrics = _estimate_messages_metrics_impl(messages)
    payload = _openai_complete_json_impl(model=model, api_key_env=api_key_env, messages=messages, max_retries=1)
    return _parse_agent_action_response(
        payload=payload,
        queries_per_cycle=queries_per_cycle,
        supported_actions=AGENT_SUPPORTED_ACTIONS,
        debug_metrics=message_metrics,
    )


def run_retrieve_agentic(
    project_id: str,
    *,
    prompt: str = "",
    top_n: int = 5,
    final_limit: int = 0,
    debug_retrieval: bool | None = None,
    progress_callback: ProgressCallback | None = None,
) -> Path:
    pdir = project_dir(project_id)
    paths = _agentic_paths(pdir)
    pmeta = load(pdir / "project.yaml")
    retrieval_cfg = (pmeta.get("retrieval") or {}) if isinstance(pmeta, dict) else {}
    agentic_cfg = (retrieval_cfg.get("agentic") or {}) if isinstance(retrieval_cfg, dict) else {}
    llm_cfg = (agentic_cfg.get("llm") or {}) if isinstance(agentic_cfg, dict) else {}

    requested_top_n = _read_int(top_n or agentic_cfg.get("top_n", 5), 5)
    effective_top_n = max(1, requested_top_n)
    final_limit_cfg = _read_int(agentic_cfg.get("final_result_limit", 0), 0)
    effective_final_limit = max(0, _read_int(final_limit if int(final_limit or 0) > 0 else final_limit_cfg, 0))
    effective_max_cycles = max(1, _read_int(agentic_cfg.get("max_cycles", 3), 3))
    queries_per_cycle = max(1, _read_int(agentic_cfg.get("queries_per_cycle", 4), 4))
    web_results_per_query = max(1, _read_int(agentic_cfg.get("web_results_per_query", 8), 8))
    searxng_timeout_s = float(agentic_cfg.get("searxng_timeout_s", 8.0) or 8.0)
    searxng_categories = _read_str(agentic_cfg.get("searxng_categories"), "general")
    llm_model = _read_str(llm_cfg.get("model"), "deepseek/deepseek-chat")
    llm_extractor_model = _read_str(llm_cfg.get("extractor_model"), "deepseek/deepseek-chat")
    llm_api_key_env = _read_str(llm_cfg.get("api_key_env"), "DS_API_KEY")
    extract_use_llm_extractor = _read_bool(agentic_cfg.get("extract_use_llm_extractor"), True)
    effective_debug_retrieval = bool(debug_retrieval) if debug_retrieval is not None else False
    _emit_progress(
        progress_callback,
        event="agentic_start",
        project_id=project_id,
        top_n=effective_top_n,
        final_result_limit=effective_final_limit,
        max_cycles=effective_max_cycles,
        queries_per_cycle=queries_per_cycle,
        web_results_per_query=web_results_per_query,
        searxng_categories=searxng_categories,
        llm_model=llm_model,
        llm_extractor_model=llm_extractor_model,
        llm_api_key_env=llm_api_key_env,
        extract_use_llm_extractor=extract_use_llm_extractor,
        debug_retrieval=effective_debug_retrieval,
    )

    resolved_prompt = str(prompt or "").strip()
    if not resolved_prompt:
        raise ValueError("Prompt is required")
    resolved_session_id = _new_session_id(resolved_prompt)

    searxng_url = str(os.environ.get("SEARXNG_URL") or "").strip()
    loop = _AgenticSearchLoop(
        paths=paths,
        prompt=resolved_prompt,
        session_id=resolved_session_id,
        max_cycles=effective_max_cycles,
        searxng_url=searxng_url,
        debug_retrieval=effective_debug_retrieval,
        execute_action_fn=actions_mod.execute_agent_action,
        agent_next_action_fn=_agent_next_action_llm,
        emit_progress_fn=_emit_progress,
        apply_search_action_result_fn=_apply_search_action_result,
        progress_callback=progress_callback,
        agent_config=_AgentConfig(
            max_queries_per_turn=queries_per_cycle,
            llm_model=llm_model,
            api_key_env=llm_api_key_env,
        ),
        extract_config=_ExtractConfig(
            llm_model=llm_extractor_model,
            api_key_env=llm_api_key_env,
            use_llm_extractor=extract_use_llm_extractor,
        ),
        search_config=_SearchConfig(
            shortlist_size=effective_top_n,
            results_per_query=web_results_per_query,
            timeout_s=searxng_timeout_s,
            categories=searxng_categories,
        ),
        result_config=_ResultConfig(
            display_limit=effective_final_limit,
        ),
    )
    return loop.run()
