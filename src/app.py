from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from src.orchestrator.runner import run_step
from src.workspace.manager import list_artifacts

app = FastAPI(title="Shredder MVP")


class CreateProjectRequest(BaseModel):
    project_id: str
    theme: str | None = None


class RunStepRequest(BaseModel):
    paper_id: str | None = None
    pdf_path: str | None = None


class RetrievePaperRequest(BaseModel):
    title: str | None = None
    doi: str | None = None
    arxiv_url: str | None = None
    arxiv_id: str | None = None
    policy: str | None = None


class RetrieveOpenRequest(BaseModel):
    prompt: str
    top_n: int = 5


class RetrieveAgenticRequest(BaseModel):
    prompt: str
    workflow: str = "theme_refine"
    top_n: int = 5
    max_cycles: int = 1
    session_id: str = ""


class AgenticAnswerRequest(BaseModel):
    session_id: str
    answers: dict[str, str] = Field(default_factory=dict)


class AgenticFinalizeRequest(BaseModel):
    session_id: str


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.post("/projects")
def create_project(body: CreateProjectRequest):
    path = run_step(body.project_id, "init", theme=body.theme)
    return {"project": body.project_id, "path": str(path)}


@app.post("/projects/{project_id}/steps/{step}")
def run_project_step(project_id: str, step: str, body: RunStepRequest):
    try:
        result = run_step(project_id, step, paper_id=body.paper_id, pdf_path=body.pdf_path)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"project": project_id, "step": step, "result": str(result)}


@app.get("/projects/{project_id}/artifacts")
def get_artifacts(project_id: str):
    return {"project": project_id, "artifacts": list_artifacts(project_id)}


@app.post("/projects/{project_id}/retrieve/paper")
def retrieve_paper(project_id: str, body: RetrievePaperRequest):
    try:
        result = run_step(
            project_id,
            "retrieve-paper",
            title=body.title or "",
            doi=body.doi or "",
            arxiv_url=body.arxiv_url or "",
            arxiv_id=body.arxiv_id or "",
            policy=body.policy or "",
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"project": project_id, "result": str(result)}


@app.post("/projects/{project_id}/retrieve/open")
def retrieve_open(project_id: str, body: RetrieveOpenRequest):
    try:
        result = run_step(project_id, "retrieve-open", prompt=body.prompt, top_n=body.top_n)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"project": project_id, "result": str(result)}


@app.post("/projects/{project_id}/retrieve/agentic")
def retrieve_agentic(project_id: str, body: RetrieveAgenticRequest):
    return retrieve_agentic_start(project_id, body)


@app.post("/projects/{project_id}/retrieve/agentic/start")
def retrieve_agentic_start(project_id: str, body: RetrieveAgenticRequest):
    try:
        result = run_step(
            project_id,
            "retrieve-agentic-start",
            prompt=body.prompt,
            workflow=body.workflow,
            top_n=body.top_n,
            max_cycles=body.max_cycles,
            session_id=body.session_id,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"project": project_id, "result": str(result)}


@app.get("/projects/{project_id}/retrieve/agentic/status")
def retrieve_agentic_status(project_id: str, session_id: str = ""):
    try:
        result = run_step(
            project_id,
            "retrieve-agentic-status",
            session_id=session_id,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"project": project_id, "status": result}


@app.post("/projects/{project_id}/retrieve/agentic/answer")
def retrieve_agentic_answer(project_id: str, body: AgenticAnswerRequest):
    try:
        result = run_step(
            project_id,
            "retrieve-agentic-answer",
            session_id=body.session_id,
            answers=body.answers or {},
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"project": project_id, "status": result}


@app.post("/projects/{project_id}/retrieve/agentic/finalize")
def retrieve_agentic_finalize(project_id: str, body: AgenticFinalizeRequest):
    try:
        result = run_step(
            project_id,
            "retrieve-agentic-finalize",
            session_id=body.session_id,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"project": project_id, "result": str(result)}
