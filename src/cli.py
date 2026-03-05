from __future__ import annotations

import argparse

from src.orchestrator.runner import run_step


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

    args = parser.parse_args()
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


if __name__ == "__main__":
    main()
