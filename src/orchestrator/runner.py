from __future__ import annotations

from src.workspace.manager import init_project
from .steps import run_discovery, run_parsing, run_extraction, run_render
from .agentic import finalize_agentic_session, get_agentic_status, run_retrieve_agentic, submit_agentic_answers
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
            progress_callback=kwargs.get("progress_callback"),
        )
    if step == "retrieve-open":
        return run_retrieve_open(
            project_id,
            prompt=kwargs.get("prompt", ""),
            top_n=int(kwargs.get("top_n", 5)),
        )
    if step == "retrieve-agentic":
        return run_retrieve_agentic(
            project_id,
            prompt=kwargs.get("prompt", ""),
            workflow=kwargs.get("workflow", "theme_refine"),
            top_n=int(kwargs.get("top_n", 5)),
            max_cycles=int(kwargs.get("max_cycles", 1)),
            session_id=kwargs.get("session_id", ""),
        )
    if step == "retrieve-agentic-start":
        return run_retrieve_agentic(
            project_id,
            prompt=kwargs.get("prompt", ""),
            workflow=kwargs.get("workflow", "theme_refine"),
            top_n=int(kwargs.get("top_n", 5)),
            max_cycles=int(kwargs.get("max_cycles", 1)),
            session_id=kwargs.get("session_id", ""),
        )
    if step == "retrieve-agentic-status":
        return get_agentic_status(
            project_id,
            session_id=kwargs.get("session_id", ""),
        )
    if step == "retrieve-agentic-answer":
        return submit_agentic_answers(
            project_id,
            session_id=kwargs.get("session_id", ""),
            answers=kwargs.get("answers", {}),
        )
    if step == "retrieve-agentic-finalize":
        return finalize_agentic_session(
            project_id,
            session_id=kwargs.get("session_id", ""),
        )
    raise ValueError(f"Unknown step: {step}")
