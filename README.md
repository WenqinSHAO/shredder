# Shredder: Local-First YAML-Centric Research Pipeline

This repository provides an MVP scaffold for a local-first, agentic research pipeline.

## What works in this iteration
- Initialize a project workspace with editable YAML/Markdown specs.
- Run connector-based discovery and produce per-source/raw/dedup TSV artifacts with offline mock fallback.
- Initialize shared KB (`kb/kb.sqlite`) with paper/author/provenance tables.
- Run deterministic retrieval (`title`/`doi`/`arxiv`) and persist paper+author+org graph into KB.
- Run open-ended retrieval to produce candidate lists and deterministic handoff artifacts.
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

Optional retrieval integrations:

```bash
pip install -e ".[retrieval]"
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
python -m src.cli retrieve-paper demo --doi "10.1145/3366423.3380296"
python -m src.cli retrieve-open demo --prompt "memory disaggregation datacenter systems" --top-n 5
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
- `POST /projects/{project_id}/retrieve/paper`
- `POST /projects/{project_id}/retrieve/open`
- `GET /projects/{project_id}/artifacts`
- `GET /healthz`


See `docs/CLI_SMOKE_TEST.md` for a full smoke-test command sequence.
