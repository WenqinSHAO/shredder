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
| Agentic Meta Info Retrieval | `72%` (`███████░░░`) | Active (extraction quality + performance + trace contracts) |
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
- `src/orchestrator/agentic.py` is still too large and still mixes orchestration with runtime mutation details
- the action/runtime bridge is still broader than necessary, especially around fetched-record exchange
- fetch/cache ownership is still loop-adjacent rather than a clearly bounded mechanism subsystem
- projection/normalization logic is still spread across multiple helpers and modules
- extraction/runtime performance work is partially blocked by the remaining contract sprawl

### 3.2.2 Hardened Optimization Direction

Principle:
- keep agentic behavior minimally structured
- add structure only at exchange boundaries between:
  - planner and app runtime
  - search and shortlist mechanics
  - fetch/cache and extraction mechanics
  - runtime state and user-facing artifacts

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
3. action executors exchange narrow runtime contracts, not broad ad hoc shared payloads
4. fetch/cache handling is clearly separated from loop orchestration
5. `cycle_trace` is the only loop-owned per-cycle debug ledger
6. result and trajectory artifacts are pure projections from runtime state
7. replay/targeted tests validate extraction, merge, and coverage behavior

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

### 3.2.7 Next Big Stage

Next stage focus: harden canonical extract request resolution and continue shrinking coordinator ownership before reopening fetch-store/cache work.

Why this comes next:
- the broad extraction heuristics already have a large replay/unit test surface, but canonical URL-target resolution still has weak seams
- current extract request resolution still mixes requested URLs, hit ids, redirected URLs, and fetched-record aliases without one clearly enforced canonical path
- `agentic.py` is still too large because it still owns fetch/raw-trace/LLM transport helpers in addition to loop coordination
- fetch-store/cache work will be easier to design after the canonical request path and coordinator ownership boundaries are tighter

Stage goals:
1. enforce one canonical path from planner-selected targets to fetched records, including redirected and alias URLs
2. move remaining fetch and LLM mechanism helpers out of `agentic.py` so the coordinator surface keeps shrinking
3. unify per-URL extract progress / coverage projection so agent memory, trajectory, and result artifacts read from the same compact state shape
4. only after those seams stabilize, run a narrower replay audit for the remaining author/institution/venue extraction edge cases
5. keep Q4 fetch-store/cache design deferred until the above contracts are small enough to design against confidently

Next session checklist:
- start with `E5`, because the remaining leverage is now in replay-backed extraction correctness rather than coordinator/mechanism ownership
- treat `E5` as a sequence of narrow replay-backed corrections; avoid broad heuristic rewrites
- next narrow `E5` target is institution evidence overmatch, not a broader extract-policy rewrite
- prefer slices that reduce coordinator ownership or remove duplicated canonicalization/projection paths
- keep `Q4` deferred unless the request-resolution and fetch/extract boundaries become stable enough to justify cache design work
- when adding tests, strengthen them to assert positive extracted outputs and artifact state, not just the absence of one stop reason
- update this board after each meaningful boundary change or replay-backed correction

Immediate queue for the next stage:
- `E5 -> H5, H9`: replay-backed extraction audit after the above seams settle
  - done: require venue evidence on the fallback candidate-filter path when venue intent exists and `match_decision` is not explicit
  - done: require full multi-token author-name evidence on the fallback candidate-filter path when `match_decision` is absent
  - next: audit remaining institution edge cases where structured row-local evidence may still be under- or over-enforced
  - avoid reopening broader fetch-store/cache design until these replay-backed contracts are stable

## 4) Non-Active Modules (Summary Only)

| Module | Next Gate To Open Detailed Board |
|---|---|
| Data Backend and RAG | Open after agentic extraction artifacts and metadata schema stabilize. |
| Paper Context Retrieval | Open after agentic final candidates are consistently stable. |
| Paper Context Formatted Extraction | Open after context retrieval schema is stable. |
| Analysis Skill | Open after extraction and context artifacts are versioned and stable. |
| Output Skills | Open after analysis outputs are stable for rendering. |
| UI/TUI Design | Open after trajectory and result contracts are stable. |
