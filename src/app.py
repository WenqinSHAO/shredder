from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from src.orchestrator.runner import run_step
from src.workspace.manager import list_artifacts

app = FastAPI(title="Shredder MVP")


class CreateProjectRequest(BaseModel):
    project_id: str
    theme: str | None = None


class RunStepRequest(BaseModel):
    paper_id: str | None = None
    pdf_path: str | None = None


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
