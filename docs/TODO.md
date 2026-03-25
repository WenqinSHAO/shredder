# Implementation Progress Board

Last updated: 2026-03-23

## 0) Usage Contract

This board is the persistent external memory for implementation tracking and the repository's task board / progress bar.

Rules:
- Read this file before coding.
- Update this file after meaningful progress.
- Keep overall program tracking visible.
- Keep detailed, execution-ready plans primarily in active sections.
- For now, the active detailed section is Agentic Meta Info Retrieval.
- The old end-to-end agentic search spec was removed because it drifted from the implementation; the replacement minimal spec now lives at `docs/agentic-search-spec.md`, with `src/orchestrator/agentic.py` plus focused module docs/tests still serving as the implementation source of truth.

Roadmap principle:
- Define big-line phases and milestone significance first.
- Keep detailed execution planning only for the currently active action items.
- Avoid overplanning non-active items; keep them as short backlog notes.

Environment baseline:
- virtualenv: `/home/wenqin/.virtualenvs/shredder`
- tests: `/home/wenqin/.virtualenvs/shredder/bin/python -m pytest -q`
- retrieval-focused tests: `tests/test_retrieval_agentic_i1.py`

Hygiene reminders:
- avoid venue/institution hardcoded logic
- prefer generic filter contracts and extractor schema outputs
- do not commit generated runtime artifacts (`kb/kb.sqlite`, egg-info)

## 1) Program Overview

This document tracks:
- overall module status
- active implementation details
- next executable queue for fresh sessions

## 2) Module Progress Bars

| Module | Progress | Status |
|---|---:|---|
| Meta Info Retrieval (deterministic) | `86%` (`████████░░`) | Stabilized |
| Agentic Meta Info Retrieval | `72%` (`███████░░░`) | Active (extraction simplification + quality + follow-up URL contract) |
| Data Backend and RAG | `22%` (`██░░░░░░░░`) | Planned |
| Paper Context Retrieval | `8%` (`░░░░░░░░░░`) | Not started |
| Paper Context Formatted Extraction | `12%` (`█░░░░░░░░░`) | Not started |
| Analysis Skill | `12%` (`█░░░░░░░░░`) | Not started |
| Output Skills (report/slides/render) | `16%` (`█░░░░░░░░░`) | Early scaffold |
| Overall UI/TUI Design | `8%` (`░░░░░░░░░░`) | Not started |
| **Overall Program** | **`31%` (`███░░░░░░░`)** | **In progress** |

## 3) Active Section Task Board

## 3.1 Deterministic Retrieval - Stabilized (Summary)

Scope (frozen for now):
- deterministic DOI/arXiv/title resolution
- canonical metadata persistence
- cache behavior and regression protection

Status:
- stable enough for handoff
- only maintenance-level work pending

Deferred backlog:
- cross-source author normalization improvements
- metadata backfill for older sparse rows
- broader CI fixture pack expansion

## 3.2 Agentic Meta Info Retrieval - Active

### 3.2.1 Current Baseline

Working:
- iterative loop with `search_web` and `extract_content`
- compact planner contract (`decision`, `state_delta`, `progress`, `action`)
- raw fetch artifact persistence and replay-friendly fixtures
- typed loop records for `run_state`, config groups, URL/extract/paper state
- user-facing result and trajectory artifacts generated from compact projections

Main problems to finish:
- `src/orchestrator/agentic.py` is smaller than before, but extraction is still too fact-filter-heavy instead of being centered on one per-page result contract
- the extractor still lacks a first-class page result shape such as `papers[]`, `candidate_urls[]`, and `page_status`
- complementary URL discovery is still partial and indirect, mostly coming from pagination/search side effects rather than page extraction output
- fallback extraction policy is still heavier than the naive goal and should only grow through narrow replay-backed corrections
- fetch-store/cache work is still deferred until the page-result contract is smaller and clearer

### 3.2.2 Hardened Optimization Direction

Principle:
- keep agentic behavior minimally structured
- add structure only at exchange boundaries between:
  - planner and app runtime
  - search and shortlist mechanics
  - fetch/cache and extraction mechanics
  - runtime state and user-facing artifacts
- keep extraction page-centric: every fetched page should ideally project to `papers[]`, `candidate_urls[]`, and `page_status`

Target layering:
1. Agent semantic layer
   - chooses next action, target URLs, anchor terms, minimal filters, stop/continue
   - only sees compact `agent_memory`
2. Runtime mechanism layer
   - owns search, shortlist, fetch, extract, merge, dedup, coverage, cache
   - owns mutable operational state
3. Artifact/projection layer
   - projects runtime state into stable result/trajectory artifacts
   - does not feed extra operational detail back into the planner

### 3.2.3 Minimum-Viable Finish Definition

Agentic search is considered minimum-viable finished when:
1. `agentic.py` is primarily a small coordinator for:
   - planner turn
   - action execution
   - cycle finalize
2. planner-facing memory is only `agent_memory`, kept compact and derived
3. page extraction returns a small, explicit contract:
   - `papers[]`
   - `candidate_urls[]`
   - `page_status`
4. candidate URL discovery is a first-class extractor output, not only a pagination/search side effect
5. action executors exchange narrow runtime contracts, not broad ad hoc shared payloads
6. fetch/cache handling is clearly separated from loop orchestration
7. `cycle_trace` is the only loop-owned per-cycle debug ledger
8. result and trajectory artifacts are pure projections from runtime state
9. replay/targeted tests validate extraction, merge, coverage, and follow-up URL discovery behavior

### 3.2.4 Hardened Work Board

| ID | Task | Status | Notes |
|---|---|---|---|
| H1 | Shrink `agentic.py` into a coordinator-only loop | In progress | Keep only planner turn, action execution, and cycle finalize in the loop surface. |
| H2 | Separate semantic planner state from operational runtime state | In progress | `agent_memory` remains planner-facing and derived; runtime state owns fetch/extract/search mutation. |
| H3 | Narrow the action/runtime contract | In progress | `_ActionRuntime` should carry only action-execution state that truly crosses the loop boundary. |
| H4 | Isolate fetch/cache into a dedicated mechanism boundary | Todo | Move fetched-record indexing/reuse and later global cache behavior behind a dedicated subsystem. |
| H5 | Make extraction pipeline deterministic with agent-supplied hints only | In progress | Agent chooses URLs/anchor terms/filters; app owns batching, coverage, retries, and extraction mechanics. |
| H6 | Collapse duplicate normalization/projection helpers into contract-focused modules | Todo | Centralize action-param normalization, state-apply paths, and projection builders. |
| H7 | Keep `cycle_trace` as the single cycle debug ledger | In progress | Trajectory should project from `cycle_trace`, not parallel history structures. |
| H8 | Keep user-facing artifacts minimal and stable | In progress | `agentic_result.yaml` / `agentic_trajectory.yaml` stay projection-only. |
| H9 | Prefer replayable deterministic validation before more planner complexity | In progress | Continue using saved fetch HTML / targeted tests as the quality gate. |

### 3.2.4.1 How to read `H*`, `Q*`, and `E*`

Interpretation rules:
- `H*` items in `3.2.4` are the higher-level outcome workstreams
- `Q*` items in `3.2.5` are concrete execution slices that advance one or more `H*` items
- completing a `Q*` item does **not** mean its related `H*` workstream is complete
- `E*` items in `3.2.7` are the next-stage execution slices after the current `Q*` queue

Current mapping from completed/deferred queue slices to workstreams:
- `Q1 -> H1, H2, H3`
- `Q2 -> H1, H2, H5, H6`
- `Q3 -> H1, H7, H8`
- `Q4 -> H4` (currently deferred until extraction behavior is more stable)
- `Q5 -> H9`

This means the queue is a delivery sequence for the workstreams rather than a second independent status board.

### 3.2.5 Current Execution Queue

| Order | Slice | Status | Notes |
|---|---:|---|---|
| Q1 | Extract runtime bridge helpers from `agentic.py` into a dedicated module | Done | `src/orchestrator/agentic_runtime.py` now owns fetched-record indexing and the `_ActionRuntime` adapter so the loop no longer carries that mechanism code inline. |
| Q2 | Extract state-apply helpers for search/extract finalize paths | Done | `src/orchestrator/agentic_state_apply.py` now owns coverage summary/update logic plus search/extract state-apply helpers used by the loop finalize path. |
| Q3 | Extract artifact/projection helpers that do not belong in the loop | Done | `src/orchestrator/agentic_trace.py` now owns compact cycle-trace summary/ref helpers so loop finalization no longer formats those projection rows inline. |
| Q4 | Tighten fetch-store ownership and prepare global raw-fetch cache path | Deferred | Revisit only after extraction behavior/contracts are stable enough that cache work is worth the extra surface area. |
| Q5 | Re-run targeted replay/contract tests after each slice | Done | Full `tests/test_retrieval_agentic_i1.py` now passes after the runtime/state-apply/trace extraction slices. |

### 3.2.6 Progress Log

- 2026-03-23: Replaced the older mixed active board with this hardened minimum-structure plan so the remaining work is organized around contract boundaries instead of more planner complexity.
- 2026-03-23: Started Q1 to move runtime bridge responsibilities out of `agentic.py` before deeper fetch/cache or extraction changes.
- 2026-03-23: Completed Q1 by extracting fetched-record indexing and the `_ActionRuntime` bridge into `src/orchestrator/agentic_runtime.py`; next safe slice is state-apply extraction (Q2).
- 2026-03-23: Completed Q2 by extracting search/extract state-apply helpers into `src/orchestrator/agentic_state_apply.py` and adding targeted tests for the new boundary.
- 2026-03-23: Completed Q3 by extracting compact cycle-trace projection helpers into `src/orchestrator/agentic_trace.py` and adding focused trace-helper tests.
- 2026-03-23: Completed Q5 by running the full `tests/test_retrieval_agentic_i1.py` suite (78 passing tests) and deferred Q4 until extraction behavior stabilizes further.
- 2026-03-23: Re-reviewed the agentic search code against this board and refined the next-stage queue: canonical URL-target hardening and mechanism-boundary extraction should come before a broad replay audit, because the current leverage is still in ownership cleanup and alias/canonicalization correctness.
- 2026-03-23: Completed the first `E1` slice by making extract request resolution reuse fetched records through alias-aware URL matching instead of exact-URL-only filtering, and by strengthening redirected-URL / hit-id remap tests to assert positive extracted outputs. `tests/test_retrieval_agentic_i1.py` now passes with 79 tests.
- 2026-03-23: Completed the main `E2` slice by moving raw fetch / retry / save / pagination-fetch implementation into `src/orchestrator/agentic_fetch.py` while keeping patch-compatible wrappers in `agentic.py`. Full `pytest` now passes (`125 passed, 27 subtests passed`).
- 2026-03-23: Completed `E3` by moving OpenAI-compatible JSON transport, response-content parsing, and message-metric helpers into `src/orchestrator/agentic_llm.py` while keeping compatibility wrappers in `agentic.py`. Added focused transport-boundary tests and re-ran full `pytest` (`128 passed, 27 subtests passed`).
- 2026-03-23: Completed the remaining `E1` request-shape cleanup by keeping planner-facing `extract_content` params target-scoped when `targets` are present, so app-side resolution owns the shared filter/anchor reconstruction. Full `pytest` still passes (`128 passed, 27 subtests passed`).
- 2026-03-23: Completed `E4` by introducing a shared per-URL extract projection in `src/orchestrator/agentic_projection.py`, preserving `coverage_has_more` / failure / completion state through extract trace and state-apply, and switching planner memory, trajectory user-view, and result coverage to that shared shape. Full `pytest` now passes (`130 passed, 27 subtests passed`).
- 2026-03-23: Completed the first `E5` replay-backed correction by enforcing venue evidence on the local/structured candidate-filter path when `match_decision` is absent, while still allowing explicit `match` decisions to pass. Added focused venue-edge-case tests and re-ran full `pytest` (`132 passed, 27 subtests passed`).
- 2026-03-23: Completed the second `E5` replay-backed correction by requiring full multi-token author-name evidence on the fallback candidate-filter path when `match_decision` is absent, reducing same-name-author overmatches while keeping structured full-name rows valid. Added focused author-edge-case tests and re-ran full `pytest` (`134 passed, 27 subtests passed`).
- 2026-03-25: Reframed the next-stage guidance around a simpler page-result contract after reviewing extraction complexity against the actual goal. The next developer should optimize for `papers[]`, `candidate_urls[]`, and `page_status`, and avoid growing fallback policy unless replay evidence clearly demands it.
- 2026-03-25: Completed the third `E5` replay-backed correction by requiring full institution-phrase evidence on the fallback candidate-filter path when `match_decision` is absent, so multi-token organization filters no longer overmatch on one token alone. Added focused institution edge-case tests and re-ran full `pytest`.
- 2026-03-25: Reworked the first `E6` slice to keep complementary-URL discovery simple: the app now only collects page-local link candidates and asks the LLM to propose which URLs look complementary and still relevant to the query. Those proposals stay planner-facing as suggested URLs instead of being auto-merged into `url_hits`.

### 3.2.7 Next Big Stage

Next stage focus: finish simplifying extraction around a small page-result contract before reopening fetch-store/cache work.

Why this comes next:
- the page contract now emits both `papers[]` and a first `candidate_urls[]` path, but the follow-up URL side should stay LLM-proposed rather than growing local classification logic
- the remaining replay work should be used to trim or justify policy, not to keep expanding fallback heuristics
- fetch-store/cache work will be easier to design after the page-result contract and follow-up URL flow are smaller and clearer

Stage goals:
1. keep extraction centered on a per-page contract: `papers[]`, `candidate_urls[]`, and `page_status`
2. keep candidate URL discovery lightweight: deterministic code should collect/normalize candidate links, while the LLM chooses which ones are complementary
3. keep planner ownership clear: discovered URLs should remain suggestions until the main agent decides whether to fetch/extract them
4. only reopen replay-backed extraction corrections if new evidence shows a remaining precision gap
5. keep Q4 fetch-store/cache design deferred until the page-result contract and follow-up URL flow are stable enough to design against confidently

Next session checklist:
- before adding extraction logic, ask whether it improves `papers[]`, `candidate_urls[]`, or `page_status`
- do not add local URL classification/ranking heuristics unless replay evidence clearly justifies them
- if candidate URL discovery is hard, move more of the decision into the stateless LLM prompt instead of inventing new code rules
- keep discovered URLs planner-facing as suggestions, not auto-adopted known URLs
- prefer page-local evidence (anchors, titles, nearby text) over broader global policy
- keep `Q4` deferred unless the request-resolution and fetch/extract boundaries become stable enough to justify cache design work
- when adding tests, strengthen them to assert positive extracted outputs and artifact state, not just the absence of one stop reason
- update this board after each meaningful boundary change or replay-backed correction

Immediate queue for the next stage:
- `E5 -> H5, H9`: replay-backed extraction audit after the above seams settle
  - done: require venue evidence on the fallback candidate-filter path when venue intent exists and `match_decision` is not explicit
  - done: require full multi-token author-name evidence on the fallback candidate-filter path when `match_decision` is absent
  - done: require full institution-phrase evidence on the fallback candidate-filter path when `match_decision` is absent
  - avoid broader heuristic rewrites unless replay evidence clearly justifies them
- `E6 -> H5, H9`: make complementary page-local URL discovery a first-class extraction output
  - done: add a small page-local candidate URL proposal path alongside extracted papers
  - done: keep deterministic code limited to link collection / normalization / dedupe against already known URLs
  - done: keep discovered URLs planner-facing as suggestions rather than auto-merging them into `url_hits`
  - next: refine the complementary-URL LLM prompt/schema using replay fixtures if proposal quality is weak
  - avoid broad URL taxonomies or hard-coded ranking/classification rules

## 4) Non-Active Modules (Summary Only)

| Module | Next Gate To Open Detailed Board |
|---|---|
| Data Backend and RAG | Open after agentic extraction artifacts and metadata schema stabilize. |
| Paper Context Retrieval | Open after agentic final candidates are consistently stable. |
| Paper Context Formatted Extraction | Open after context retrieval schema is stable. |
| Analysis Skill | Open after extraction and context artifacts are versioned and stable. |
| Output Skills | Open after analysis outputs are stable for rendering. |
| UI/TUI Design | Open after trajectory and result contracts are stable. |
