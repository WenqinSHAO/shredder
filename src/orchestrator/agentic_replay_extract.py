from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.orchestrator import agentic_actions as actions_mod
from src.orchestrator import agentic_fetch as fetch_mod
from src.orchestrator import agentic_text as text_mod
from src.orchestrator.agentic_extract_prepare import resolve_extract_intent
from src.retrieval.service import write_yaml
from src.utils.paths import project_dir
from src.utils.yamlx import load


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


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


def _retrieval_dir(project_path: Path) -> Path:
    return project_path / "artifacts" / "retrieval"


def _agentic_paths(project_path: Path) -> dict[str, Path]:
    retrieval_dir = _retrieval_dir(project_path)
    return {
        "result": retrieval_dir / "agentic_result.yaml",
        "trajectory": retrieval_dir / "agentic_trajectory.yaml",
        "raw": retrieval_dir / "agentic_raw.ndjson",
        "replay": retrieval_dir / "agentic_extract_replay.yaml",
    }
def _step_action_name(step: dict[str, Any]) -> str:
    status_snapshot = step.get("status_snapshot")
    if isinstance(status_snapshot, dict):
        action = str(status_snapshot.get("action") or "").strip()
        if action:
            return action
    selected_action = step.get("selected_action")
    if isinstance(selected_action, dict):
        action = str(selected_action.get("action") or "").strip()
        if action:
            return action
    action_input = step.get("action_input")
    if isinstance(action_input, dict):
        action = str(action_input.get("action") or "").strip()
        if action:
            return action
    return ""


def _extract_steps(trajectory: dict[str, Any], *, cycle_index: int = 0) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for step in trajectory.get("steps") or []:
        if not isinstance(step, dict):
            continue
        if _step_action_name(step) != "extract_content":
            continue
        if cycle_index > 0 and _read_int(step.get("cycle_index"), 0) != cycle_index:
            continue
        out.append(step)
    return out


def _infer_content_type(path: Path) -> str:
    name = path.name.lower()
    if name.endswith(".pdf") or ".pdf." in name:
        return "application/pdf"
    if name.endswith(".html") or name.endswith(".htm"):
        return "text/html"
    if name.endswith(".json"):
        return "application/json"
    return "application/octet-stream"


def _load_saved_raw_text(path: Path, *, url: str, title: str, filters: dict[str, Any]) -> tuple[str, list[str], str]:
    raw = path.read_bytes()
    content_type = _infer_content_type(path)
    if "pdf" in content_type or str(url or "").lower().endswith(".pdf"):
        text = fetch_mod.extract_text_from_pdf_bytes(raw, max_chars=8_000_000)
        segments = text_mod._extract_text_segments(text, max_chars=1800)
        if not segments:
            segments = fetch_mod.build_extraction_windows(
                text,
                filters,
                max_windows=30,
                radius=2,
                max_chars=1200,
            )
        return text_mod._clean_text(text, limit_chars=8_000_000), segments, content_type

    raw_html = fetch_mod.decode_bytes(raw)
    if text_mod._is_listing_page(title=title, url=url):
        text = text_mod._extract_listing_text_with_fallback(raw_html, max_chars=8_000_000)
    else:
        text = text_mod._extract_main_text_from_html(raw_html, max_chars=8_000_000)
    segments = text_mod._extract_text_segments(text, max_chars=1800)
    if not segments:
        segments = fetch_mod.build_extraction_windows(
            text,
            filters,
            max_windows=30,
            radius=2,
            max_chars=1200,
        )
    return text_mod._clean_text(text, limit_chars=8_000_000), segments, content_type


def _target_lookup(step: dict[str, Any]) -> list[dict[str, Any]]:
    action_debug = step.get("action_debug")
    if not isinstance(action_debug, dict):
        return []
    targets = action_debug.get("targets")
    if not isinstance(targets, list):
        return []
    out: list[dict[str, Any]] = []
    for item in targets:
        if isinstance(item, dict):
            out.append(item)
    return out


def _replay_params_from_step(step: dict[str, Any]) -> dict[str, Any]:
    action_input = step.get("action_input") if isinstance(step.get("action_input"), dict) else {}
    params = dict(action_input)
    urls = [str(url) for url in (params.get("urls") or []) if str(url).strip()]
    targets = params.get("targets") if isinstance(params.get("targets"), list) else []
    if urls or targets:
        return params
    replay_targets: list[dict[str, Any]] = []
    for item in _target_lookup(step):
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        replay_targets.append(
            {
                "target_id": str(item.get("target_id") or ""),
                "url": url,
                "title": str(item.get("title") or item.get("url_title") or ""),
                "why": str(item.get("why") or ""),
            }
        )
    if replay_targets:
        params["targets"] = replay_targets
        params["urls"] = [str(item.get("url") or "") for item in replay_targets if str(item.get("url") or "").strip()]
    return params


def _find_raw_path_for_target(
    fetch_raw_paths: list[str],
    *,
    cycle_index: int,
    target_id: str,
) -> str:
    if not fetch_raw_paths:
        return ""
    prefix = f"cycle{cycle_index:02d}-{fetch_mod.safe_name(target_id)}-"
    for rel_path in fetch_raw_paths:
        name = Path(str(rel_path)).name
        if name.startswith(prefix):
            return str(rel_path)
    fallback_token = f"-{fetch_mod.safe_name(target_id)}-"
    for rel_path in fetch_raw_paths:
        name = Path(str(rel_path)).name
        if fallback_token in name:
            return str(rel_path)
    return ""


def _rebuild_saved_fetched_records(
    *,
    retrieval_dir: Path,
    step: dict[str, Any],
) -> list[dict[str, Any]]:
    cycle_index = _read_int(step.get("cycle_index"), 0)
    action_input = step.get("action_input") if isinstance(step.get("action_input"), dict) else {}
    shared_filters = dict(action_input.get("filters") or {})
    refs = step.get("refs") if isinstance(step.get("refs"), dict) else {}
    fetch_raw_paths = [str(v) for v in (refs.get("fetch_raw_paths") or []) if str(v).strip()]
    targets = _target_lookup(step)
    out: list[dict[str, Any]] = []
    for item in targets:
        target_id = str(item.get("target_id") or "").strip()
        url = str(item.get("url") or "").strip()
        if not target_id or not url:
            continue
        rel_path = _find_raw_path_for_target(fetch_raw_paths, cycle_index=cycle_index, target_id=target_id)
        if not rel_path:
            continue
        raw_path = retrieval_dir / rel_path
        if not raw_path.exists():
            continue
        text, segments, content_type = _load_saved_raw_text(
            raw_path,
            url=url,
            title=str(item.get("title") or item.get("url_title") or ""),
            filters=shared_filters,
        )
        out.append(
            {
                "session_id": str(step.get("session_id") or "replay"),
                "cycle_index": cycle_index,
                "target_id": target_id,
                "requested_url": url,
                "url": url,
                "url_aliases": [url],
                "url_title": str(item.get("title") or item.get("url_title") or ""),
                "why": str(item.get("why") or ""),
                "status": "ok",
                "content_type": content_type,
                "bytes_read": raw_path.stat().st_size,
                "fetch_hard_limit_bytes": 0,
                "text_chars": len(text),
                "text": text,
                "peek": text_mod._peek_text(text, 320),
                "page_count": 1,
                "page_urls": [url],
                "segments": [str(segment) for segment in segments if str(segment).strip()],
                "segment_count": len([segment for segment in segments if str(segment).strip()]),
                "raw_path": str(rel_path),
            }
        )
    return out


def _build_url_hits_from_step(step: dict[str, Any]) -> list[dict[str, Any]]:
    summary = (step.get("agent_state_before") or {}).get("summary")
    if not isinstance(summary, dict):
        return []
    top_hits = summary.get("top_hits")
    if not isinstance(top_hits, list):
        return []
    out: list[dict[str, Any]] = []
    for idx, item in enumerate(top_hits, start=1):
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        out.append(
            {
                "hit_id": str(item.get("hit_id") or f"replay-hit-{idx}"),
                "title": str(item.get("title") or ""),
                "url": url,
                "host": str(item.get("host") or text_mod._host_from_url(url)),
                "status": str(item.get("status") or "shortlisted"),
                "score": float(item.get("score") or 0.0),
            }
        )
    return out


def _current_probe_segments(
    *,
    record: dict[str, Any],
    params: dict[str, Any],
    user_prompt: str,
    limit: int,
) -> list[str]:
    filters = dict(params.get("filters") or {})
    intent = resolve_extract_intent(params=params, filters=filters, user_prompt=user_prompt)
    must_match = intent.get("must_match") if isinstance(intent.get("must_match"), dict) else {}
    active_filters = text_mod._resolve_active_extract_filters(filters, must_match, user_prompt)
    anchor_terms = text_mod._resolve_extract_anchor_terms(
        params=params,
        filters=filters,
        intent=intent,
        user_prompt=user_prompt,
    )
    ranked_segments, _batch_mode = text_mod._prepare_extract_segments(
        row=record,
        filters=active_filters,
        anchor_terms=anchor_terms,
    )
    cleaned = [
        text_mod._clean_text(str(segment or ""), limit_chars=1200)
        for segment in ranked_segments
        if str(segment or "").strip()
    ]
    return cleaned[: max(0, int(limit or 0))]


def _probe_saved_target(
    *,
    record: dict[str, Any],
    params: dict[str, Any],
    user_prompt: str,
    model: str,
    api_key_env: str,
    timeout_s: float,
    probe_segments: list[str],
) -> dict[str, Any]:
    filters = dict(params.get("filters") or {})
    intent = resolve_extract_intent(params=params, filters=filters, user_prompt=user_prompt)
    try:
        facts, trace = actions_mod._extract_facts_with_llm(
            record=record,
            filters=filters,
            user_prompt=user_prompt,
            model=model,
            api_key_env=api_key_env,
            intent=intent,
            segments=probe_segments,
            timeout_s=timeout_s,
            max_retries=0,
            raw_event_fn=None,
            llm_op_id="",
            batch_mode="replay_probe",
        )
    except Exception as exc:
        return {
            "target_id": str(record.get("target_id") or ""),
            "url": str(record.get("url") or ""),
            "status": "error",
            "error": f"{type(exc).__name__}:{exc}",
            "probe_segment_count": len(probe_segments),
            "extracted_count": 0,
            "titles": [],
            "trace": {},
        }
    return {
        "target_id": str(record.get("target_id") or ""),
        "url": str(record.get("url") or ""),
        "status": "ok",
        "probe_segment_count": len(probe_segments),
        "extracted_count": len(facts),
        "titles": [str(row.get("paper_title") or row.get("paper_title_raw") or "") for row in facts if str(row.get("paper_title") or row.get("paper_title_raw") or "").strip()],
        "trace": {
            "timed_out": bool(trace.get("timed_out")),
            "empty_semantic": bool(trace.get("empty_semantic")),
            "input_chars": _read_int(trace.get("input_chars"), 0),
            "input_tokens_est": _read_int(trace.get("input_tokens_est"), 0),
            "output_chars": _read_int(trace.get("output_chars"), 0),
        },
    }


def _project_action_result(result: dict[str, Any]) -> dict[str, Any]:
    notes = str(result.get("notes") or "")

    def _note_int(name: str) -> int:
        match = re.search(rf"\b{re.escape(name)}=([0-9]+)\b", notes)
        if not match:
            return 0
        return _read_int(match.group(1), 0)

    return {
        "status": str(result.get("status") or ""),
        "notes": notes,
        "requested_url_count": _note_int("requested_urls"),
        "extracted_count": len(result.get("paper_candidates") or []),
        "candidate_url_count": len(result.get("candidate_urls") or []),
        "llm_extract_attempted": _note_int("llm_extract_attempted"),
        "llm_extract_applied": _note_int("llm_extract_applied"),
        "llm_timeout_errors": _note_int("llm_timeout_errors"),
        "coverage_has_more": bool(result.get("coverage_has_more")),
        "coverage_passes": _read_int(result.get("coverage_passes"), _note_int("coverage_passes")),
        "extract_timeout_errors": _read_int(result.get("extract_timeout_errors"), 0),
        "extract_empty_semantic": _read_int(result.get("extract_empty_semantic"), 0),
        "paper_titles": [str(row.get("title") or "") for row in (result.get("paper_candidates") or []) if str(row.get("title") or "").strip()],
        "candidate_urls": [
            {
                "url": str(row.get("url") or ""),
                "title": str(row.get("title") or ""),
                "why": str(row.get("why") or ""),
            }
            for row in (result.get("candidate_urls") or [])
            if isinstance(row, dict)
        ],
    }


def run_replay_agentic_extract(
    project_id: str,
    *,
    cycle_index: int = 0,
    timeout_s: float = 45.0,
    probe_segments: int = 0,
    use_llm_extractor: bool | None = None,
    llm_extractor_model: str = "",
    llm_api_key_env: str = "",
) -> Path:
    project_path = project_dir(project_id)
    paths = _agentic_paths(project_path)
    trajectory = load(paths["trajectory"])
    if not isinstance(trajectory, dict):
        raise ValueError("Invalid agentic trajectory artifact")
    project_meta = load(project_path / "project.yaml")
    retrieval_cfg = (project_meta.get("retrieval") or {}) if isinstance(project_meta, dict) else {}
    agentic_cfg = (retrieval_cfg.get("agentic") or {}) if isinstance(retrieval_cfg, dict) else {}
    llm_cfg = (agentic_cfg.get("llm") or {}) if isinstance(agentic_cfg, dict) else {}
    resolved_llm_model = _read_str(
        llm_extractor_model or llm_cfg.get("extractor_model") or llm_cfg.get("model"),
        "deepseek/deepseek-chat",
    )
    resolved_api_key_env = _read_str(llm_api_key_env or llm_cfg.get("api_key_env"), "DS_API_KEY")
    resolved_use_llm = (
        bool(use_llm_extractor)
        if use_llm_extractor is not None
        else _read_bool(agentic_cfg.get("extract_use_llm_extractor"), True)
    )
    user_prompt = str((trajectory.get("run_header") or {}).get("prompt") or "").strip()
    if not user_prompt:
        raise ValueError("Saved trajectory is missing run prompt")
    step_rows = _extract_steps(trajectory, cycle_index=cycle_index)
    if not step_rows:
        raise ValueError("No extract_content steps found in saved trajectory")

    steps_payload: list[dict[str, Any]] = []
    for step in step_rows:
        replay_params = _replay_params_from_step(step)
        replay_params["auto_fetch"] = False
        replay_params["target_ids"] = []

        saved_records = _rebuild_saved_fetched_records(
            retrieval_dir=_retrieval_dir(project_path),
            step=step,
        )
        runtime_state = {
            "url_hits": _build_url_hits_from_step(step),
            "fetched_records": list(saved_records),
            "extract_state_by_url": {},
        }
        result = actions_mod.execute_extract_content_action(
            session_id=str(trajectory.get("session_id") or f"replay-{project_id}"),
            cycle_index=_read_int(step.get("cycle_index"), 0),
            params=replay_params,
            paths=paths,
            user_prompt=user_prompt,
            timeout_s=float(timeout_s or 45.0),
            llm_extractor_model=resolved_llm_model,
            llm_api_key_env=resolved_api_key_env,
            extract_use_llm_extractor=resolved_use_llm,
            progress_callback=None,
            runtime_state=runtime_state,
            next_op_id_fn=None,
            raw_event_fn=None,
        )

        probes: list[dict[str, Any]] = []
        if probe_segments > 0 and resolved_use_llm:
            for record in saved_records:
                segments = _current_probe_segments(
                    record=record,
                    params=replay_params,
                    user_prompt=user_prompt,
                    limit=probe_segments,
                )
                if not segments:
                    continue
                probes.append(
                    _probe_saved_target(
                        record=record,
                        params=replay_params,
                        user_prompt=user_prompt,
                        model=resolved_llm_model,
                        api_key_env=resolved_api_key_env,
                        timeout_s=float(timeout_s or 45.0),
                        probe_segments=segments,
                    )
                )

        steps_payload.append(
            {
                "cycle_index": _read_int(step.get("cycle_index"), 0),
                "action_id": str(step.get("action_id") or ""),
                "requested_urls": [str(url) for url in (replay_params.get("urls") or []) if str(url).strip()],
                "saved_records": [
                    {
                        "target_id": str(row.get("target_id") or ""),
                        "url": str(row.get("url") or ""),
                        "content_type": str(row.get("content_type") or ""),
                        "segment_count": _read_int(row.get("segment_count"), 0),
                        "text_chars": _read_int(row.get("text_chars"), 0),
                        "raw_path": str(row.get("raw_path") or ""),
                    }
                    for row in saved_records
                ],
                "replay_result": _project_action_result(result),
                "segment_probes": probes,
            }
        )

    payload = {
        "artifact_type": "agentic_extract_replay",
        "schema_version": "0.1.0",
        "project_id": project_id,
        "prompt": user_prompt,
        "source_trajectory_ref": str(paths["trajectory"].name),
        "source_raw_ref": str(paths["raw"].name),
        "cycle_filter": _read_int(cycle_index, 0),
        "extract_use_llm_extractor": resolved_use_llm,
        "llm_extractor_model": resolved_llm_model,
        "llm_api_key_env": resolved_api_key_env,
        "probe_segments": max(0, _read_int(probe_segments, 0)),
        "step_count": len(steps_payload),
        "steps": steps_payload,
        "updated_at": _utc_now(),
    }
    write_yaml(paths["replay"], payload)
    return paths["replay"]
