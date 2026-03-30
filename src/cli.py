from __future__ import annotations

import argparse
import json

from src.orchestrator.runner import run_step
from src.utils.yamlx import YamlDependencyError


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


def _print_retrieve_agentic_progress(event: dict) -> None:
    name = str(event.get("event") or "")
    if not name:
        return
    prefix = "[retrieve-agentic]"

    if name == "agentic_start":
        print(
            f"{prefix} start workflow={event.get('workflow')} top_n={event.get('top_n')} "
            f"max_cycles={event.get('max_cycles')} queries_per_cycle={event.get('queries_per_cycle')} "
            f"results_per_query={event.get('web_results_per_query')} categories={event.get('searxng_categories')} "
            f"llm_model={event.get('llm_model')}",
            flush=True,
        )
        return
    if name == "agentic_cycle_start":
        print(
            f"{prefix} cycle {event.get('cycle_index')}/{event.get('max_cycles')} start",
            flush=True,
        )
        return
    if name == "agentic_llm_planner_request" or name == "agentic_llm_agent_request":
        payload = event.get("payload") or {}
        print(
            f"{prefix} agent request cycle={event.get('cycle_index')} action_id={event.get('action_id')} "
            f"candidates={len(payload.get('current_candidates') or [])} raw_ref={event.get('raw_event_id')}",
            flush=True,
        )
        return
    if name == "agentic_llm_planner_response" or name == "agentic_llm_agent_response":
        payload = event.get("payload") or {}
        planned = event.get("planned_queries") or []
        selected_action = event.get("selected_action")
        print(
            f"{prefix} agent response cycle={event.get('cycle_index')} action_id={event.get('action_id')} "
            f"selected_action={selected_action} query_count={len(planned)} raw_ref={event.get('raw_event_id')}",
            flush=True,
        )
        if planned:
            print(f"{prefix} queries={planned}", flush=True)
        return
    if name == "agentic_action_start":
        print(
            f"{prefix} action start cycle={event.get('cycle_index')} action_id={event.get('action_id')} "
            f"step={event.get('active_step_id')} action={event.get('action')} raw_ref={event.get('raw_event_id')}",
            flush=True,
        )
        return
    if name == "agentic_action_done":
        print(
            f"{prefix} action done cycle={event.get('cycle_index')} action_id={event.get('action_id')} "
            f"action={event.get('action')} status={event.get('status')} note={event.get('notes')} "
            f"raw_ref={event.get('raw_event_id')}",
            flush=True,
        )
        return
    if name == "agentic_web_search_query_start":
        print(
            f"{prefix} search start query={event.get('query')} categories={event.get('categories')} "
            f"limit={event.get('limit')}",
            flush=True,
        )
        return
    if name == "agentic_web_search_query_done":
        error = str(event.get("error") or "")
        extra = f" error={error}" if error else ""
        print(
            f"{prefix} search done query={event.get('query')} raw_results={event.get('raw_results')} "
            f"used_results={event.get('used_results')}{extra}",
            flush=True,
        )
        return
    if name == "agentic_extract_target_start":
        print(
            f"{prefix} extract start cycle={event.get('cycle_index')} target={event.get('target_id')} url={event.get('url')}",
            flush=True,
        )
        return
    if name == "agentic_extract_batch_start":
        print(
            f"{prefix} llm extract batch start cycle={event.get('cycle_index')} target={event.get('target_id')} "
            f"pass={event.get('pass_index')} batch={event.get('batch_start')}+{event.get('batch_size')}",
            flush=True,
        )
        return
    if name == "agentic_extract_batch_done":
        err = str(event.get("error") or "")
        extra = f" error={err}" if err else ""
        print(
            f"{prefix} llm extract batch done cycle={event.get('cycle_index')} target={event.get('target_id')} "
            f"extracted={event.get('extracted_count')} pass={event.get('pass_index')}{extra}",
            flush=True,
        )
        return
    if name == "agentic_extract_stage":
        stage = str(event.get("stage") or "")
        print(
            f"{prefix} extract stage cycle={event.get('cycle_index')} target={event.get('target_id') or '-'} "
            f"stage={stage} total={event.get('segments_total')} ranked={event.get('segments_ranked')} "
            f"det={event.get('deterministic_listing_count')} llm={event.get('llm_count')} merged={event.get('merged_count')}",
            flush=True,
        )
        return
    if name == "agentic_extract_target_done":
        done = event.get("segments_done")
        total = event.get("segments_total")
        coverage = f" segments={done}/{total}" if done is not None and total is not None else ""
        print(
            f"{prefix} extract done cycle={event.get('cycle_index')} target={event.get('target_id')} "
            f"extracted={event.get('extracted_count')} coverage_has_more={event.get('coverage_has_more')}{coverage}",
            flush=True,
        )
        return
    if name == "agentic_condensed_summary":
        summary = event.get("summary") or {}
        top_hits = summary.get("top_hits") if isinstance(summary.get("top_hits"), list) else []
        top_preview = [str((row or {}).get("title") or "") for row in top_hits[:3] if isinstance(row, dict)]
        extract_cov = summary.get("extract_coverage") if isinstance(summary.get("extract_coverage"), dict) else {}
        cov_note = ""
        if extract_cov:
            cov_note = f" coverage={extract_cov}"
        print(
            f"{prefix} condensed summary cycle={event.get('cycle_index')} "
            f"result_count={summary.get('result_count')} venues={summary.get('venues')} "
            f"top_hits={top_preview}{cov_note}",
            flush=True,
        )
        return
    if name == "agentic_progress_snapshot":
        progress = event.get("progress") or {}
        todo = progress.get("todo") if isinstance(progress.get("todo"), dict) else {}
        print(
            f"{prefix} progress cycle={event.get('cycle_index')} action={progress.get('action')} "
            f"step={progress.get('active_step_id')} todo={todo.get('done', 0)}/{todo.get('total', 0)} "
            f"doing={todo.get('doing', 0)} final_matches={progress.get('final_candidates', 0)} "
            f"decision={progress.get('decision')}",
            flush=True,
        )
        return
    if name == "agentic_llm_decider_request":
        payload = event.get("payload") or {}
        print(f"{prefix} decider request:", flush=True)
        print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
        return
    if name == "agentic_llm_decider_response":
        payload = event.get("payload") or {}
        print(f"{prefix} decider response:", flush=True)
        print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
        return
    if name == "agentic_cycle_decision":
        print(
            f"{prefix} cycle={event.get('cycle_index')} action_id={event.get('action_id')} decision={event.get('decision')} "
            f"reason={event.get('decision_reason')} stop_reason={event.get('stop_reason')} "
            f"raw={event.get('raw_candidates')} shortlisted={event.get('shortlisted')} "
            f"final_matches={event.get('final_candidates')}",
            flush=True,
        )
        return
    if name == "agentic_complete":
        print(
            f"{prefix} complete status={event.get('status')} cycle_count={event.get('cycle_count')} "
            f"stop_reason={event.get('stop_reason')} final_candidates={event.get('final_candidates')}",
            flush=True,
        )
        return
    if name == "agentic_failed":
        print(f"{prefix} failed reason={event.get('reason')}", flush=True)
        return


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
    p_retrieve_agentic.add_argument("--top-n", type=int, default=5)
    p_retrieve_agentic.add_argument("--final-limit", type=int, default=0)
    p_retrieve_agentic.add_argument("--debug-retrieval", action="store_true")

    p_extract_local = sub.add_parser("extract-agentic-local")
    p_extract_local.add_argument("project_id")
    p_extract_local.add_argument("--institution", default="")
    p_extract_local.add_argument("--year-gte", type=int, default=0)
    p_extract_local.add_argument("--url-contains", default="")

    p_replay_extract = sub.add_parser("replay-agentic-extract")
    p_replay_extract.add_argument("project_id")
    p_replay_extract.add_argument("--cycle-index", type=int, default=0)
    p_replay_extract.add_argument("--timeout-s", type=float, default=45.0)
    p_replay_extract.add_argument("--probe-segments", type=int, default=0)
    p_replay_extract.add_argument("--llm-extractor-model", default="")
    p_replay_extract.add_argument("--llm-api-key-env", default="")
    p_replay_extract.add_argument(
        "--use-llm-extractor",
        choices=("auto", "true", "false"),
        default="auto",
    )

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
                top_n=args.top_n,
                final_limit=args.final_limit,
                debug_retrieval=bool(args.debug_retrieval),
                progress_callback=_print_retrieve_agentic_progress,
            )
            print(f"Agentic retrieval complete: {result}")
        elif args.cmd == "extract-agentic-local":
            result = run_step(
                args.project_id,
                "extract-agentic-local",
                institution=args.institution,
                year_gte=int(args.year_gte or 0),
                url_contains=args.url_contains,
            )
            print(f"Local extraction complete: {result}")
        elif args.cmd == "replay-agentic-extract":
            use_llm_extractor = None
            if args.use_llm_extractor == "true":
                use_llm_extractor = True
            elif args.use_llm_extractor == "false":
                use_llm_extractor = False
            result = run_step(
                args.project_id,
                "replay-agentic-extract",
                cycle_index=int(args.cycle_index or 0),
                timeout_s=float(args.timeout_s or 45.0),
                probe_segments=int(args.probe_segments or 0),
                use_llm_extractor=use_llm_extractor,
                llm_extractor_model=args.llm_extractor_model,
                llm_api_key_env=args.llm_api_key_env,
            )
            print(f"Replay extraction complete: {result}")
    except YamlDependencyError as exc:
        raise SystemExit(f"YAML dependency error: {exc}") from exc


if __name__ == "__main__":
    main()
