# Implementation Progress Board

Last updated: 2026-03-15

## 1) Program Overview

This board is the single source of truth for delivery progress.
Detailed task tables are maintained only for modules currently in active implementation.

## 2) Module Progress Bars

| Module | Progress | Status |
|---|---:|---|
| Meta Info Retrieval (deterministic) | `85%` (`████████░░`) | Stabilized |
| Agentic Meta Info Retrieval | `30%` (`███░░░░░░░`) | Active (I2 LLM+SearxNG loop) |
| Data Backend and RAG | `20%` (`██░░░░░░░░`) | Planned |
| Paper Context Retrieval | `5%` (`░░░░░░░░░░`) | Not started |
| Paper Context Formatted Extraction | `10%` (`█░░░░░░░░░`) | Not started |
| Analysis Skill | `10%` (`█░░░░░░░░░`) | Not started |
| Output Skills (report/slides/render) | `15%` (`█░░░░░░░░░`) | Early scaffold |
| Overall UI Design | `5%` (`░░░░░░░░░░`) | Not started |
| **Overall Program** | **`25%` (`███░░░░░░░`)** | **In progress** |

## 3) Active Section Task Board

## Meta Info Retrieval (Deterministic) - Stabilized (Collapsed)

Scope (frozen for next-stage handoff):
- Stable deterministic resolution for DOI/arXiv/title.
- Canonical paper/author metadata persistence in DB.
- Predictable cache semantics with inspectable artifacts.

Decision:
- Collapse detailed task tables for deterministic paper+author metadata retrieval and DB hardening.
- Keep only deferred non-critical backlog and wish-list items.
- Move active implementation focus to next-stage modules.
- Active agentic implementation tracking is maintained in `Agentic Meta Info Retrieval - Active` below.

### 3.1 Stabilization Summary

| Area | Status | Evidence |
|---|---|---|
| Paper metadata determinism + guardrails | Done | `tests/test_retrieval_deterministic.py`, `tests/test_retrieval_rc1_matrix.py`, `tests/fixtures/deterministic_benchmark_cases.json` |
| Author metadata persistence + cache-hit fidelity | Done | `tests/test_retrieval_index.py` (`author_reconcile`, `cache_first` author-fidelity tests) |
| DB hardening + cache/index behavior | Done | `tests/test_retrieval_index.py` (`cache_first` full-path, index uniqueness, legacy compat, artifact size-bounds) |
| Observability + fresh-session smoke | Done | `tests/test_retrieval_rc1_matrix.py::test_cold_start_matrix_and_cache_replay` |

### 3.2 Deferred Backlog (Post-RC1, Non-Blocking)

| Item | Status | Priority | Note |
|---|---|---|---|
| Cross-source author canonicalization | Todo | P1 | Duplicate people may still occur with partial/weak IDs; acceptable for most current flows. |
| Metadata backfill for legacy sparse index/DB rows | Todo | P1 | Existing rows are compatible but not auto-enriched retroactively. |
| Deterministic fixture pack CI rollout/expansion | In progress | P1 | Fixture pack exists and is tested locally; CI wiring + larger corpus pending. |

### 3.3 Wish List (Non-Blocking)

- Add fallback to web-search-based paper/author retrieval using existing OSS integration points.
- Add `homepage_url` (or equivalent) to author schema and retrieval pipeline.
- Add optional richer author profile enrichment fields after next-stage stabilization.

## Agentic Meta Info Retrieval - Active (I2 LLM + SearxNG Loop)

Scope:
- Keep existing `retrieve-agentic` entry points and core artifacts.
- Implement iterative LLM-powered query understanding for web retrieval through SearxNG.
- Keep scope narrow: no resume, no human-gated stop, no multi-workflow policy engine.

### 3.4 I2 Sprint Board

| ID | Item | Status | Note |
|---|---|---|---|
| A1 | LLM planner step (`plan_queries_llm`) outputs structured query set for SearxNG | Todo | Use DeepSeek via `litellm`; validate strict JSON output. |
| A2 | Multi-cycle orchestrator (`plan -> search_web -> condense -> decide`) | Todo | Stop on LLM convergence or `max_cycles` cap. |
| A3 | SearxNG-only retrieval in this workflow | Todo | Query `SEARXNG_URL`; no adapter merge in I2. |
| A4 | Intermediate artifacts for web results and LLM payload summaries | Todo | Persist compact cycle evidence for observability. |
| A5 | I2 tests (planner parse, convergence, empty results, artifact writes) | Todo | Keep existing I1 artifact contract compatibility. |

### 3.5 Defaults and Limits

- Active workflow name for I2: `searxng_meta_refine_v1`.
- Retrieval source scope for I2: SearxNG only.
- Loop stop rule: LLM returns `stop=true` OR no meaningful new narrowed signals, always bounded by `max_cycles`.
- Existing user knobs remain `prompt` and `top_n`; cycle and model controls come from project config.

### 3.6 I2 Contracts and Config

Cycle-level behavior contract:
1. LLM planning receives user prompt + prior cycle condensed summary and emits JSON:
   - `queries: list[str]`
   - `rationale: str`
   - `stop: bool`
   - `stop_reason: str`
2. System executes each query against `SEARXNG_URL/search` using `format=json` and `categories=science`.
3. System condenses raw web results locally before sending back to LLM.
4. LLM decides next query set or stop; repeat until convergence or `max_cycles`.

Artifacts:
- Keep existing artifacts:
  - `agentic_request.yaml`
  - `agentic_session.yaml`
  - `agentic_result.yaml`
  - `agentic_questions.yaml`
  - `agentic_cycles.tsv`
  - `agentic_candidates_latest.tsv`
- Add I2 intermediate artifacts:
  - `agentic_web_results.tsv` (append-only across cycles, includes `cycle_index`, `query`, rank, compact metadata)
  - `agentic_llm_payloads.yaml` (per-cycle planner/decider input summaries and outputs; do not store full raw web payloads)

Config defaults:
- `retrieval.agentic.max_cycles`: `3`
- `retrieval.agentic.queries_per_cycle`: `4`
- `retrieval.agentic.web_results_per_query`: `8`
- `retrieval.agentic.llm.model`: `deepseek-chat`
- `retrieval.agentic.llm.api_key_env`: `DS_API_KEY`

## 4) Non-Active Modules (Summary Only)

| Module | Next Gate To Open Detailed Board |
|---|---|
| Data Backend and RAG | Open now: deterministic metadata schema and cache policy are stable enough for integration. |
| Paper Context Retrieval | Metadata layer can reliably resolve and cache canonical papers. |
| Paper Context Formatted Extraction | Context retrieval contract is stable. |
| Analysis Skill | Extraction artifacts have stable schema + quality controls. |
| Output Skills | Analysis outputs are stable and versioned. |
| Overall UI Design | Core retrieval/extraction APIs reach stable semantics. |

## 5) Milestone Status

Achieved:

`M-Deterministic-RC1` (2026-03-06): deterministic retrieval stabilized for handoff.

Evidence snapshot:
- DOI/arXiv/title deterministic behavior is benchmark-fixture covered across policies.
- arXiv queries hit DB cache correctly regardless of canonical paper ID prefix.
- Author and author-org links remain consistent after metadata updates.
- Cache-hit paper/author metadata fidelity is preserved.
- Deterministic artifacts remain compact/readable with provenance in TSV.
- Fresh-session deterministic regression coverage is stable.

Active next milestone:

`M-Agentic-I2`: establish convergent LLM-guided SearxNG loop with inspectable intermediate artifacts.

Done when:
- LLM planner emits valid query plans and decisions with robust parse/validation.
- Agentic orchestrator runs multi-cycle retrieval and stops with convergence + safety cap semantics.
- Intermediate web and LLM summary artifacts are written per cycle.
- I2 tests are added and passing without breaking I1 artifact compatibility.
- Detailed active task board is opened for Data Backend/RAG with deterministic artifact integration contracts.
- Deferred deterministic backlog and wishlist remain explicitly non-blocking unless they become concrete blockers.

## 6) Next Fresh Session Task Queue

Use this queue at the start of the next session:

1. Session handoff snapshot (cleaned baseline):
   - Agentic orchestrator target is I2 iterative LLM+SearxNG loop.
   - Preserve current artifact compatibility while adding intermediate cycle artifacts.
   - Entry points remain:
     - Runner step: `retrieve-agentic`
     - CLI command: `python -m src.cli retrieve-agentic <project_id> --prompt ... --top-n N`
     - API endpoint: `POST /projects/{project_id}/retrieve/agentic`

2. Environment + validation baseline:
   - Use virtual environment: `/home/wenqin/.virtualenvs/shredder`.
   - Baseline command:
     - `/home/wenqin/.virtualenvs/shredder/bin/python -m pytest -q`
   - Retrieval-specific focus:
     - Existing: `tests/test_retrieval_agentic_i1.py`
     - Add for I2: planner parse, convergence, empty-search path, artifact integration.

3. Next session entry criteria:
   - `DS_API_KEY` is available in env.
   - `SEARXNG_URL` points to reachable backend.
   - `litellm` dependency is installed in runtime.

4. Workspace hygiene reminder before commit:
   - Do not commit generated runtime files such as `kb/kb.sqlite` and `src/shredder.egg-info/`.
