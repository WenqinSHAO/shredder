# Repository Guidelines

## Project Structure & Module Organization
Core code lives in `src/`. Use `src/orchestrator/` for pipeline control flow, `src/connectors/` for external data adapters, `src/parsing/` and `src/extraction/` for document processing, `src/render/` for report generation, and `src/analysis_skills/` for pluggable analysis steps. API and CLI entry points are `src/app.py` and `src/cli.py`.

Tests live in `tests/`, with reusable inputs under `tests/fixtures/`. Schemas and examples live in `schemas/` and `examples/`. Design notes are in `docs/`. Runtime state is written to `workspace/`, `kb/`, and `cache/`; treat those as generated data unless a task explicitly targets them.

## Workflow Notes
Treat `docs/TODO.md` as the repository task board and progress bar. Read it before substantial edits, and keep at least that file consistent with the latest code and document changes after each meaningful edit so the next session inherits an accurate state.

## Build, Test, and Development Commands
Create an environment and install the package:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e .
pip install -e ".[retrieval]"  # optional connector extras
```

Key workflows:

```bash
python -m pytest -q
python -m src.cli init demo
python -m src.cli run-step demo discovery
python -m src.cli render demo
uvicorn src.app:app --reload
docker compose -f docker/compose.grobid.yml up  # optional parser service
```

## Coding Style & Naming Conventions
Follow the existing Python style: 4-space indentation, type hints where practical, and `from __future__ import annotations` in new modules. Use `snake_case` for modules, functions, and file names; use `PascalCase` for classes. Keep YAML and schema file names descriptive, e.g. `artifact_sections.yaml`. No formatter or linter is configured in `pyproject.toml`, so match surrounding code closely and keep imports tidy. Short inline one-line comments are appreciated when they clarify non-obvious control flow or artifacts.

## Testing Guidelines
Use `pytest` for discovery, even though many suites are written with `unittest.TestCase`. Name new files `test_*.py` and keep test names behavior-focused, for example `test_open_retrieval_outputs_candidates`. Prefer fixture-driven, offline tests over live network calls, and cover both written artifacts and KB side effects when changing pipeline steps.

## Commit & Pull Request Guidelines
Recent history favors short, imperative commit subjects, optionally scoped: `docs: simplify planning docs`, `feat(agentic): run llm+searxng loop`, `Clarify agentic spec contracts`. Keep commits focused on one concern. PRs should summarize the affected pipeline step(s), list verification commands run, and include sample artifact paths or API/CLI output when behavior changes.
