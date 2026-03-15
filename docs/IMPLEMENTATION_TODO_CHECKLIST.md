# Implementation Progress Board

Last updated: 2026-03-06

## 1) Program Overview

This board is the single source of truth for delivery progress.
Detailed task tables are maintained only for modules currently in active implementation.

## 2) Module Progress Bars

| Module | Progress | Status |
|---|---:|---|
| Meta Info Retrieval (deterministic) | `85%` (`████████░░`) | Stabilized |
| Agentic Meta Info Retrieval | `20%` (`██░░░░░░░░`) | Active (bootstrap only) |
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

## Agentic Meta Info Retrieval - Active (Clean Baseline)

Scope:
- Keep only a clean bootstrap implementation for agentic retrieval.
- Preserve artifact contracts and one-cycle execution visibility.
- Defer advanced planning/routing/multi-cycle logic to the next dedicated design session.

### 3.4 Implemented Baseline

| ID | Item | Status | Note |
|---|---|---|---|
| A1 | Session artifacts (`agentic_request/session/result/questions`, cycle/candidate TSVs) | Done | Contracts are written on every run. |
| A2 | One-cycle orchestrator (`plan -> retrieve -> rank -> decide`) | Done | Current behavior is explicitly single-cycle bootstrap. |
| A3 | Basic tests for non-empty and empty candidate paths | Done | See `tests/test_retrieval_agentic_i1.py`. |
| A4 | Resume, multi-cycle control, lead-specific workflows | Deferred | Not part of current clean baseline. |
| A5 | Advanced policy engine (clarification/feedback/convergence) | Deferred | Not part of current clean baseline. |

### 3.5 Defaults and Limits

- Workflow is fixed to `theme_refine` in current code path.
- Agentic run is single-cycle by design in current baseline.
- User-facing knobs are only `prompt` and `top_n`.
- Advanced resume and multi-hop planning are intentionally deferred.

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

`M-Next-Stage-Launch`: begin Agentic Meta Info Retrieval + Data Backend/RAG with deterministic layer frozen except bugfixes.

Done when:
- Detailed active task board is opened for Agentic Meta Info Retrieval with first sprint acceptance criteria. (Done)
- Detailed active task board is opened for Data Backend/RAG with deterministic artifact integration contracts.
- Deferred deterministic backlog and wishlist remain explicitly non-blocking unless they become concrete blockers.

## 6) Next Fresh Session Task Queue

Use this queue at the start of the next session:

1. Session handoff snapshot (cleaned baseline):
   - Agentic orchestrator remains at one-cycle bootstrap level.
   - Artifacts and tests are kept, but deferred features are removed from active scope.
   - Entry points remain:
     - Runner step: `retrieve-agentic`
     - CLI command: `python -m src.cli retrieve-agentic <project_id> --prompt ... --top-n N`
     - API endpoint: `POST /projects/{project_id}/retrieve/agentic`

2. Environment + validation baseline:
   - Use virtual environment: `/home/wenqin/.virtualenvs/shredder`.
   - Verified command baseline:
     - Full test suite: `48 passed, 27 subtests passed`.
     - Command used: `/home/wenqin/.virtualenvs/shredder/bin/python -m pytest -q`
   - Focused I1 test file added: `tests/test_retrieval_agentic_i1.py`.

3. Next session entry criteria:
   - Decide one architecture direction (minimal deterministic loop vs LLM-planned exploration) before adding new features.
   - Re-open detailed planning only after that architecture decision is explicit.

4. Workspace hygiene reminder before commit:
   - Do not commit generated runtime files such as `kb/kb.sqlite` and `src/shredder.egg-info/`.
