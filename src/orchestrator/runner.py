from __future__ import annotations

from src.workspace.manager import init_project
from .steps import run_discovery, run_parsing, run_extraction, run_render
from .retrieval import run_retrieve_open, run_retrieve_paper


def run_step(project_id: str, step: str, **kwargs):
    if step == "init":
        return init_project(project_id, kwargs.get("theme"))
    if step == "discovery":
        return run_discovery(project_id)
    if step == "parsing":
        return run_parsing(project_id, kwargs["paper_id"], kwargs["pdf_path"])
    if step == "extraction":
        return run_extraction(project_id, kwargs["paper_id"])
    if step == "render":
        return run_render(project_id)
    if step == "retrieve-paper":
        return run_retrieve_paper(
            project_id,
            title=kwargs.get("title", ""),
            doi=kwargs.get("doi", ""),
            arxiv_url=kwargs.get("arxiv_url", ""),
            arxiv_id=kwargs.get("arxiv_id", ""),
            policy=kwargs.get("policy", ""),
        )
    if step == "retrieve-open":
        return run_retrieve_open(
            project_id,
            prompt=kwargs.get("prompt", ""),
            top_n=int(kwargs.get("top_n", 5)),
        )
    raise ValueError(f"Unknown step: {step}")
