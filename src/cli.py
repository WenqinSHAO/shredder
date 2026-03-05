from __future__ import annotations

import argparse

from src.orchestrator.runner import run_step
from src.utils.yamlx import YamlDependencyError


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

    p_retrieve_open = sub.add_parser("retrieve-open")
    p_retrieve_open.add_argument("project_id")
    p_retrieve_open.add_argument("--prompt", required=True)
    p_retrieve_open.add_argument("--top-n", type=int, default=5)

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
            )
            print(f"Deterministic retrieval complete: {result}")
        elif args.cmd == "retrieve-open":
            result = run_step(args.project_id, "retrieve-open", prompt=args.prompt, top_n=args.top_n)
            print(f"Open retrieval complete: {result}")
    except YamlDependencyError as exc:
        raise SystemExit(f"YAML dependency error: {exc}") from exc


if __name__ == "__main__":
    main()
