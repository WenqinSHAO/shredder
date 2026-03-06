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
