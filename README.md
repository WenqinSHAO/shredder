# Shredder: Local-First YAML-Centric Research Pipeline

This repository provides an MVP scaffold for a local-first, agentic research pipeline.

## What works in this iteration
- Initialize a project workspace with editable YAML/Markdown specs.
- Run discovery stub and produce TSV artifacts.
- Initialize shared KB (`kb/kb.sqlite`) with paper/author/provenance tables.
- Parse a local PDF into normalized `sections.yaml` (stub parser).
- Run extraction stub from `sections.yaml` + `schema.yaml` into per-paper YAML.
- Render report and slides markdown outputs.
- FastAPI app skeleton with project/step/artifact endpoints.

## Repository layout
- `docs/`: design and implementation workplan
- `src/`: application code
- `schemas/`: YAML schema definitions for artifacts
- `examples/`: sample project and extraction schema
- `docker/`: optional compose for GROBID with host bind mounts

## Bootstrap / dependencies

Create a virtual environment and install project dependencies (including **PyYAML**, used for all YAML artifact I/O):

```bash
python -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e .
```

If YAML commands fail with a PyYAML dependency error, install it explicitly:

```bash
pip install pyyaml
```

## Quickstart

```bash
python -m src.cli init demo
python -m src.cli run-step demo discovery
python -m src.cli run-step demo parsing --paper-id sample --pdf examples/sample.pdf
python -m src.cli run-step demo extraction --paper-id sample
python -m src.cli render demo
```

Generated artifacts live under `workspace/demo/`.
Shared KB is created at `kb/kb.sqlite`.

## API run
```bash
uvicorn src.app:app --reload
```

Endpoints:
- `POST /projects`
- `POST /projects/{project_id}/steps/{step}`
- `GET /projects/{project_id}/artifacts`
- `GET /healthz`
