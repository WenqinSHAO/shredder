# Detailed Implementation TODO Checklist

Last updated: 2026-03-05

## 1) Current Progress Snapshot

- Validation: `PYTHONPATH=. python3 -m unittest discover -s tests -q` -> `Ran 16 tests ... OK`.
- M0 foundations are implemented and runnable:
  - CLI flow: `init -> discovery -> parsing -> extraction -> render`
  - FastAPI skeleton endpoints
  - Workspace scaffolding + shared SQLite KB bootstrap
- M1 discovery is mostly implemented:
  - OpenAlex/Crossref/Semantic Scholar connectors
  - SearxNG connector wiring + config toggle
  - Dedup with canonical precedence and order-invariance tests
  - Canonical provenance mapping tests
- M2+ remains mostly stub-level:
  - Parsing/extraction outputs are still minimal and not strongly schema-validated
  - Analysis skills/rendering are basic
  - KB schema/migrations are incomplete versus design

## 2) Milestone Status

### M0 (MVP skeleton)
- DONE: Project/workspace bootstrap (`src/workspace/manager.py`)
- DONE: KB bootstrap and paper/provenance writes (`src/kb/store.py`, `src/orchestrator/steps.py`)
- DONE: CLI + API skeleton (`src/cli.py`, `src/app.py`)

### M1 (metadata + parsing baseline)
- DONE: Real discovery connectors (`src/connectors/*.py`)
- DONE: Discovery aggregation + per-source/raw/dedup TSV outputs
- DONE: Deterministic dedup + canonical provenance mapping tests
- PARTIAL: Retry/backoff is implemented in HTTP adapter, but not yet configured/passed by connectors
- TODO: Parsing is still stub-only (no real GROBID integration path yet)

### M2 (schema-driven extraction)
- TODO: Formal schema validation for sections/extraction artifacts
- TODO: Structured sections model (section objects, page spans, offsets) instead of pipe-delimited strings
- TODO: Field-level extraction confidence/status and stronger verifier behavior

### M3 (analysis + rendering)
- PARTIAL: One skill exists (`trends_over_time`) with minimal registry
- TODO: Integrate skills into pipeline step execution and artifact logging
- TODO: Rich report/slides templates with evidence tables and charts

### M4 (stabilization)
- TODO: DB migrations + schema versioning/migrators
- TODO: Caching/provenance audit hardening
- TODO: Packaging/release/test hardening (pytest in dev env, CI command parity)

## 3) Detailed Follow-up TODO (Prioritized)

## P0 - Correctness / reliability

- [ ] Wire retry policy into all network connectors.
  - Add discovery config for retry policy (`max_attempts`, backoff, jitter, retry statuses).
  - Pass `retry_policy` from connector calls to `get_json(...)`.
  - Acceptance: transient failures are retried during real connector runs.

- [ ] Add connector integration tests for retry behavior.
  - Acceptance: tests prove retry + fail-fast behavior through connector layer, not only `http.py` unit tests.

- [ ] Tighten provenance consistency checks in discovery runs.
  - Acceptance: integration test validates every discovery provenance `entity_id` exists in `papers` for that run.

## P1 - Close M1/M2 contract gaps

- [ ] Replace pipe-delimited sections representation with structured YAML.
  - Target fields: `section_id`, `title`, `text`, `page_span`, `source_offsets`.
  - Acceptance: extraction reads structured sections without manual split logic.

- [ ] Implement schema validation against `schemas/artifact_sections.yaml` and `schemas/artifact_extraction.yaml`.
  - Add explicit validation errors to `artifacts/errors/*.md`.
  - Acceptance: invalid artifacts fail with clear user-visible error files.

- [ ] Improve extraction output contract.
  - Per-field: `value`, `confidence`, `evidence[]`, `status`.
  - Acceptance: every non-empty extracted field includes evidence pointers.

## P2 - Pipeline completeness

- [ ] Add missing orchestrator steps from design: enrichment (30), fetch (40), analyze (65).
  - Acceptance: CLI/API can run each step and write expected artifact directories.

- [ ] Expand KB schema to include org tables and join tables from design.
  - Add `orgs`, `paper_authors`, `author_orgs`.
  - Acceptance: idempotent upserts + relation tests.

- [ ] Add CLI KB subcommands (`search-papers`, `show-paper`).
  - Acceptance: commands return useful outputs from shared KB.

## P3 - Developer workflow and release readiness

- [ ] Add `pytest` to dev dependencies and CI-friendly test command.
  - Acceptance: documented command works on clean environment.

- [ ] Add Makefile targets (`init`, `test`, `lint`, `demo`).
  - Acceptance: one-command local bootstrap and smoke flow.

- [ ] Document production-ready vs stub components in release notes.
  - Acceptance: users can quickly see what is stable vs planned.

## 4) Suggested Next Execution Order

1. Wire connector retry policy + tests (P0).
2. Restructure parsing/extraction artifacts + schema validation (P1).
3. Add missing pipeline steps and KB relation tables (P2).
4. Harden dev/release workflow docs and commands (P3).
