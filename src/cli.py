from __future__ import annotations

import argparse

from src.orchestrator.runner import run_step
from src.utils.yamlx import YamlDependencyError


def _parse_answers(items: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in items:
        part = str(raw or "").strip()
        if not part:
            continue
        if "=" not in part:
            raise SystemExit(f"Invalid --answer value '{part}'. Expected key=value.")
        key, value = part.split("=", 1)
        key = key.strip()
        if not key:
            raise SystemExit(f"Invalid --answer value '{part}'. Key cannot be empty.")
        out[key] = value.strip()
    return out


def _short_text(value: str, max_len: int = 96) -> str:
    text = str(value or "").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def _print_candidate_preview(prefix: str, preview_rows: list[dict]) -> None:
    for row in preview_rows:
        print(
            f"{prefix} rank={row.get('rank')} source={row.get('source')} year={row.get('year')} "
            f"score={float(row.get('score') or 0.0):.3f} key={row.get('candidate_key')} "
            f"title=\"{_short_text(str(row.get('title') or ''))}\"",
            flush=True,
        )


def _print_retrieve_agentic_progress(event: dict) -> None:
    name = str(event.get("event") or "")
    if not name:
        return
    prefix = "[retrieve-agentic]"

    if name == "agentic_start":
        llm_status = "ready" if event.get("llm_api_key_present") else "missing-key"
        print(
            f"{prefix} start session_id={event.get('session_id')} workflow={event.get('workflow')} "
            f"top_n={event.get('top_n')} max_cycles={event.get('max_cycles')} "
            f"llm={event.get('llm_backend')}:{event.get('llm_model')}({llm_status})",
            flush=True,
        )
        return
    if name == "agentic_session_already_completed":
        print(
            f"{prefix} resume no-op session_id={event.get('session_id')} "
            f"cycle={event.get('current_cycle')} stop_reason={event.get('stop_reason')}",
            flush=True,
        )
        return
    if name == "agentic_state":
        print(
            f"{prefix} cycle={event.get('cycle_index')} state={event.get('state')}",
            flush=True,
        )
        return
    if name == "agentic_cycle_context":
        prev_count = int(event.get("previous_candidate_count") or 0)
        print(
            f"{prefix} cycle={event.get('cycle_index')} context previous_finalists={prev_count}",
            flush=True,
        )
        preview = event.get("previous_preview") or []
        if preview:
            _print_candidate_preview(f"{prefix} previous", preview)
        return
    if name == "agentic_plan_ready":
        query = str(event.get("retrieval_query") or "")
        q_preview = (query[:117] + "...") if len(query) > 120 else query
        signals = event.get("extracted_signals") or {}
        det_queries = event.get("deterministic_queries") or []
        open_plan = event.get("open_query_plan") or []
        plan_template = str(event.get("plan_template") or "").strip()
        print(
            f"{prefix} cycle={event.get('cycle_index')} planning rationale={event.get('plan_rationale')} "
            f"template={plan_template or 'n/a'} query=\"{q_preview}\"",
            flush=True,
        )
        print(
            f"{prefix} planning signals doi={len(signals.get('dois') or [])} "
            f"arxiv={len(signals.get('arxiv_ids') or [])} title_hint={int(bool(signals.get('title_hint')))}",
            flush=True,
        )
        if det_queries:
            print(f"{prefix} planning deterministic_queries={len(det_queries)}", flush=True)
            for det in det_queries:
                ident = det.get("doi") or det.get("arxiv_id") or det.get("title") or ""
                print(f"{prefix} deterministic query={_short_text(str(ident), 120)}", flush=True)
        if open_plan:
            print(f"{prefix} planning open_queries={len(open_plan)}", flush=True)
            for idx, item in enumerate(open_plan[:3], start=1):
                print(
                    f"{prefix} open_query[{idx}]={_short_text(str(item.get('query') or ''), 120)} "
                    f"scope={item.get('connector_scope')} purpose={_short_text(str(item.get('purpose') or ''), 80)}",
                    flush=True,
                )
        return
    if name == "agentic_llm_payload":
        payload = event.get("payload") or {}
        task = str(payload.get("task") or "")
        model = str(payload.get("model") or "")
        planner = payload.get("output") if isinstance(payload.get("output"), dict) else {}
        print(
            f"{prefix} cycle={event.get('cycle_index')} llm_payload task={task} model={model} "
            f"planned_query=\"{_short_text(str(planner.get('planned_query') or ''), 100)}\"",
            flush=True,
        )
        return
    if name == "agentic_web_payload":
        provider = str(event.get("provider") or "")
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        if provider == "searxng":
            print(
                f"{prefix} cycle={event.get('cycle_index')} web_payload provider={provider} "
                f"q=\"{_short_text(str(payload.get('q') or ''), 120)}\"",
                flush=True,
            )
        elif provider == "page_fetch":
            print(
                f"{prefix} cycle={event.get('cycle_index')} web_payload provider={provider} "
                f"url=\"{_short_text(str(payload.get('url') or ''), 120)}\"",
                flush=True,
            )
        return
    if name == "agentic_query_start":
        print(
            f"{prefix} cycle={event.get('cycle_index')} query_step={event.get('query_index')}/{event.get('query_total')} "
            f"query=\"{_short_text(str(event.get('query') or ''), 140)}\"",
            flush=True,
        )
        return
    if name == "agentic_query_replanned":
        print(
            f"{prefix} cycle={event.get('cycle_index')} replanned_queries reason={event.get('reason')} "
            f"seed=\"{_short_text(str(event.get('replanned_seed') or ''), 120)}\" count={event.get('query_count')}",
            flush=True,
        )
        return
    if name == "agentic_tool_start":
        print(
            f"{prefix} cycle={event.get('cycle_index')} tool_start action={event.get('tool_call')} "
            f"query=\"{_short_text(str(event.get('query') or ''), 110)}\"",
            flush=True,
        )
        return
    if name == "agentic_tool_done":
        error = str(event.get("error") or "").strip()
        detail = str(event.get("detail") or "").strip()
        error_extra = f" error={error}" if error else ""
        detail_extra = f" detail={detail}" if detail and not error else ""
        print(
            f"{prefix} cycle={event.get('cycle_index')} tool_done action={event.get('tool_call')} "
            f"rows={event.get('rows_returned')} elapsed_ms={event.get('elapsed_ms')}{detail_extra}{error_extra}",
            flush=True,
        )
        return
    if name == "agentic_retrieve_done":
        print(
            f"{prefix} cycle={event.get('cycle_index')} tool_results raw={event.get('raw_candidates')} "
            f"ranked={event.get('ranked_candidates')} router={event.get('router_decision')} "
            f"fallback={int(bool(event.get('fallback_triggered')))} insufficiency={event.get('insufficiency_reason') or 'none'} "
            f"deterministic_resolved={event.get('deterministic_resolved')} "
            f"kept={event.get('kept_candidates')} ignored={event.get('ignored_candidates')}",
            flush=True,
        )
        tool_calls = event.get("tool_calls") or []
        for tool_call in tool_calls:
            print(f"{prefix} tool action={tool_call}", flush=True)
        return
    if name == "agentic_candidate_filter":
        print(
            f"{prefix} cycle={event.get('cycle_index')} candidate_filter seed={event.get('candidate_seed')} "
            f"decision={event.get('decision')} reason={event.get('reason')}",
            flush=True,
        )
        return
    if name == "agentic_search_decision":
        print(
            f"{prefix} cycle={event.get('cycle_index')} search_decision query=\"{_short_text(str(event.get('query') or ''), 90)}\" "
            f"purpose={_short_text(str(event.get('purpose') or ''), 64)} fulfilled={int(bool(event.get('fulfilled')))} "
            f"next={event.get('next_hop_decision')}",
            flush=True,
        )
        proposed = str(event.get("next_query_proposal") or "").strip()
        if proposed:
            print(f"{prefix} search_next_query \"{_short_text(proposed, 120)}\"", flush=True)
        return
    if name == "agentic_search_trace":
        decisions = event.get("decisions") or []
        print(
            f"{prefix} cycle={event.get('cycle_index')} search_trace_count={len(decisions)}",
            flush=True,
        )
        for item in decisions[:3]:
            print(
                f"{prefix} trace query=\"{_short_text(str(item.get('query') or ''), 88)}\" "
                f"fulfilled={int(bool(item.get('fulfilled')))} purpose={_short_text(str(item.get('purpose') or ''), 56)}",
                flush=True,
            )
        return
    if name == "agentic_ranked_preview":
        count = int(event.get("shortlisted_count") or 0)
        print(
            f"{prefix} cycle={event.get('cycle_index')} ranking shortlisted={count}",
            flush=True,
        )
        preview = event.get("shortlisted_preview") or []
        if preview:
            _print_candidate_preview(f"{prefix} result", preview)
        return
    if name == "agentic_decision":
        stop_reason = str(event.get("stop_reason") or "")
        stop_extra = f" stop_reason={stop_reason}" if stop_reason else ""
        print(
            f"{prefix} cycle={event.get('cycle_index')} reasoning decision={event.get('decision')} "
            f"reason={event.get('decision_reason')}{stop_extra}",
            flush=True,
        )
        return
    if name == "agentic_feedback_expected":
        optional = bool(event.get("optional"))
        mode = "optional" if optional else "required"
        print(
            f"{prefix} cycle={event.get('cycle_index')} feedback {mode} pending_questions={event.get('pending_questions')}",
            flush=True,
        )
        expected = event.get("expected_answers") or {}
        if expected:
            print(
                f"{prefix} feedback_keys keep/remove/why_missing "
                f"example_keep=\"{expected.get('keep', '')}\"",
                flush=True,
            )
        command_hint = str(event.get("command_hint") or "").strip()
        if command_hint:
            print(f"{prefix} feedback_cmd {command_hint}", flush=True)
        return
    if name == "agentic_complete":
        print(
            f"{prefix} complete status={event.get('status')} stop_reason={event.get('stop_reason')} "
            f"cycles={event.get('cycle_count')} finalists={event.get('final_candidates')}",
            flush=True,
        )
        return


def _print_retrieve_paper_progress(event: dict) -> None:
    name = str(event.get("event") or "")
    if not name:
        return
    prefix = "[retrieve-paper]"

    if name == "retrieve_paper_start":
        print(
            f"{prefix} start query_mode={event.get('query_mode')} policy={event.get('requested_policy')}",
            flush=True,
        )
        return
    if name == "cache_lookup_start":
        print(f"{prefix} cache lookup query_key={event.get('query_key')}", flush=True)
        return
    if name == "cache_lookup_hit":
        print(f"{prefix} cache hit paper_id={event.get('paper_id')}", flush=True)
        return
    if name == "cache_lookup_miss":
        print(f"{prefix} cache miss; falling back to adapters", flush=True)
        return
    if name == "resolve_start":
        print(
            f"{prefix} resolve lookup_mode={event.get('lookup_mode')} "
            f"policy={event.get('effective_policy')} query_key={event.get('query_key')}",
            flush=True,
        )
        return
    if name == "input_warnings":
        warnings = ",".join(event.get("warnings") or [])
        print(f"{prefix} warnings={warnings}", flush=True)
        return
    if name == "adapter_query_start":
        print(
            f"{prefix} adapter {event.get('adapter_index')}/{event.get('adapter_total')} "
            f"start {event.get('adapter')} mode={event.get('lookup_mode')}",
            flush=True,
        )
        return
    if name == "adapter_query_done":
        error = str(event.get("error") or "")
        extra = f" error={error}" if error else ""
        print(
            f"{prefix} adapter {event.get('adapter')} done rows={event.get('rows_returned')} "
            f"elapsed_ms={event.get('elapsed_ms')}{extra}",
            flush=True,
        )
        return
    if name == "adapter_query_skipped":
        print(
            f"{prefix} adapter {event.get('adapter')} skipped reason={event.get('reason')}",
            flush=True,
        )
        return
    if name == "candidate_collection_done":
        print(
            f"{prefix} collected candidates={event.get('candidate_count')} "
            f"adapter_calls={event.get('adapter_calls')}",
            flush=True,
        )
        return
    if name == "title_resolution":
        print(
            f"{prefix} title resolution status={event.get('status')} reason={event.get('reason')}",
            flush=True,
        )
        return
    if name == "resolve_complete":
        print(f"{prefix} resolve complete status={event.get('status')} reason={event.get('reason')}", flush=True)
        return
    if name == "reconcile_start":
        print(
            f"{prefix} reconcile start status={event.get('status')} paper_id={event.get('paper_id') or 'n/a'} "
            f"existing_entry={event.get('existing_entry')} source_count={event.get('source_count')}",
            flush=True,
        )
        return
    if name == "kb_persist_done":
        print(
            f"{prefix} persisted to KB paper_id={event.get('paper_id')} authors={event.get('author_count')}",
            flush=True,
        )
        return
    if name == "kb_persist_skipped":
        print(
            f"{prefix} KB persist skipped status={event.get('status')} reason={event.get('reason')}",
            flush=True,
        )
        return
    if name == "reconcile_done":
        print(
            f"{prefix} reconcile done paper_id={event.get('paper_id') or 'n/a'} "
            f"query_keys={event.get('query_keys')} merged_sources={event.get('merged_sources')} "
            f"total_papers={event.get('total_papers')}",
            flush=True,
        )
        return
    if name == "retrieve_paper_artifacts_written":
        print(
            f"{prefix} wrote request_log={event.get('request_path')} sources_log={event.get('sources_path')} "
            f"result={event.get('result_path')}",
            flush=True,
        )
        return
    if name == "retrieve_paper_complete":
        print(
            f"{prefix} complete status={event.get('status')} reason={event.get('reason')} paper_id={event.get('paper_id')}",
            flush=True,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Shredder local-first research pipeline CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init")
    p_init.add_argument("project_id")
    p_init.add_argument("--theme", default="")

    p_run = sub.add_parser("run-step")
    p_run.add_argument("project_id")
    p_run.add_argument("step")
    p_run.add_argument("--paper-id", default="")
    p_run.add_argument("--pdf", default="")

    p_render = sub.add_parser("render")
    p_render.add_argument("project_id")

    p_retrieve_paper = sub.add_parser("retrieve-paper")
    p_retrieve_paper.add_argument("project_id")
    p_retrieve_paper.add_argument("--title", default="")
    p_retrieve_paper.add_argument("--doi", default="")
    p_retrieve_paper.add_argument("--arxiv-url", default="")
    p_retrieve_paper.add_argument("--arxiv-id", default="")
    p_retrieve_paper.add_argument("--policy", default="")

    p_retrieve_open = sub.add_parser("retrieve-open")
    p_retrieve_open.add_argument("project_id")
    p_retrieve_open.add_argument("--prompt", required=True)
    p_retrieve_open.add_argument("--top-n", type=int, default=5)

    p_retrieve_agentic = sub.add_parser("retrieve-agentic")
    p_retrieve_agentic.add_argument("project_id")
    p_retrieve_agentic.add_argument("--prompt", required=True)
    p_retrieve_agentic.add_argument("--workflow", default="theme_refine")
    p_retrieve_agentic.add_argument("--top-n", type=int, default=5)
    p_retrieve_agentic.add_argument("--max-cycles", type=int, default=1)
    p_retrieve_agentic.add_argument("--session-id", default="")

    p_retrieve_agentic_start = sub.add_parser("retrieve-agentic-start")
    p_retrieve_agentic_start.add_argument("project_id")
    p_retrieve_agentic_start.add_argument("--prompt", required=True)
    p_retrieve_agentic_start.add_argument("--workflow", default="theme_refine")
    p_retrieve_agentic_start.add_argument("--top-n", type=int, default=5)
    p_retrieve_agentic_start.add_argument("--max-cycles", type=int, default=1)
    p_retrieve_agentic_start.add_argument("--session-id", default="")

    p_retrieve_agentic_status = sub.add_parser("retrieve-agentic-status")
    p_retrieve_agentic_status.add_argument("project_id")
    p_retrieve_agentic_status.add_argument("--session-id", default="")

    p_retrieve_agentic_answer = sub.add_parser("retrieve-agentic-answer")
    p_retrieve_agentic_answer.add_argument("project_id")
    p_retrieve_agentic_answer.add_argument("--session-id", required=True)
    p_retrieve_agentic_answer.add_argument("--answer", action="append", default=[])

    p_retrieve_agentic_finalize = sub.add_parser("retrieve-agentic-finalize")
    p_retrieve_agentic_finalize.add_argument("project_id")
    p_retrieve_agentic_finalize.add_argument("--session-id", required=True)

    args = parser.parse_args()
    try:
        if args.cmd == "init":
            result = run_step(args.project_id, "init", theme=args.theme or None)
            print(f"Initialized project at: {result}")
        elif args.cmd == "run-step":
            kwargs = {}
            if args.paper_id:
                kwargs["paper_id"] = args.paper_id
            if args.pdf:
                kwargs["pdf_path"] = args.pdf
            result = run_step(args.project_id, args.step, **kwargs)
            print(f"Step {args.step} complete: {result}")
        elif args.cmd == "render":
            result = run_step(args.project_id, "render")
            print(f"Rendered outputs: {result}")
        elif args.cmd == "retrieve-paper":
            result = run_step(
                args.project_id,
                "retrieve-paper",
                title=args.title,
                doi=args.doi,
                arxiv_url=args.arxiv_url,
                arxiv_id=args.arxiv_id,
                policy=args.policy,
                progress_callback=_print_retrieve_paper_progress,
            )
            print(f"Deterministic retrieval complete: {result}")
        elif args.cmd == "retrieve-open":
            result = run_step(args.project_id, "retrieve-open", prompt=args.prompt, top_n=args.top_n)
            print(f"Open retrieval complete: {result}")
        elif args.cmd == "retrieve-agentic":
            result = run_step(
                args.project_id,
                "retrieve-agentic",
                prompt=args.prompt,
                workflow=args.workflow,
                top_n=args.top_n,
                max_cycles=args.max_cycles,
                session_id=args.session_id,
                progress_callback=_print_retrieve_agentic_progress,
            )
            print(f"Agentic retrieval complete: {result}")
        elif args.cmd == "retrieve-agentic-start":
            result = run_step(
                args.project_id,
                "retrieve-agentic-start",
                prompt=args.prompt,
                workflow=args.workflow,
                top_n=args.top_n,
                max_cycles=args.max_cycles,
                session_id=args.session_id,
                progress_callback=_print_retrieve_agentic_progress,
            )
            print(f"Agentic retrieval start/continue complete: {result}")
        elif args.cmd == "retrieve-agentic-status":
            result = run_step(
                args.project_id,
                "retrieve-agentic-status",
                session_id=args.session_id,
            )
            session = result.get("session") or {}
            print(
                "Agentic session status: "
                f"session_id={result.get('session_id')} "
                f"status={session.get('status')} "
                f"state={session.get('state')} "
                f"current_cycle={session.get('current_cycle')}"
            )
        elif args.cmd == "retrieve-agentic-answer":
            result = run_step(
                args.project_id,
                "retrieve-agentic-answer",
                session_id=args.session_id,
                answers=_parse_answers(args.answer or []),
            )
            session = result.get("session") or {}
            print(
                "Agentic answers submitted: "
                f"session_id={result.get('session_id')} "
                f"status={session.get('status')} "
                f"state={session.get('state')}"
            )
        elif args.cmd == "retrieve-agentic-finalize":
            result = run_step(
                args.project_id,
                "retrieve-agentic-finalize",
                session_id=args.session_id,
            )
            print(f"Agentic session finalized: {result}")
    except YamlDependencyError as exc:
        raise SystemExit(f"YAML dependency error: {exc}") from exc


if __name__ == "__main__":
    main()
