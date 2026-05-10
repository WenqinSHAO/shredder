# Design: Agentic Search Architecture

## 1) Scope
This document describes the current design of the agentic retrieval path used by `retrieve-agentic`.
It is intentionally narrower than the older end-to-end pipeline notes: the active system is a workspace-local agentic retrieval loop with remote search/fetch connectors that:

1. maintains canonical run state for progress tracking and later planner decisions,
2. reconstructs planner context explicitly from that state instead of relying on naive chat accumulation,
3. chooses the next search or extraction action with an LLM,
4. executes search or page extraction and returns compact action results,
5. applies those results back into canonical state,
6. writes replayable local artifacts that expose both raw behavior and compact summaries.

Status note:
- use `docs/TODO.md` as the authoritative pending-work board
- this document describes the intended current architecture and ownership boundaries
- the active `E21` state-consistency slice means some extract-runtime paths are still being moved
  onto the stricter `state_apply -> canonical state -> projection` boundary

The design goal is debuggability first. Run control, runtime state, and replay artifacts are local to the workspace even though `search_web` and page fetches use remote services. Hard semantic choices should live in LLM prompts and compact contracts, while deterministic code should stay focused on normalization, filtering, dedupe, state updates, persistence, and projection.

## 2) Core Principles

### 2.1 Simple Page Contract
Extraction should stay centered on three outputs for a page:

- `papers[]`: extracted paper rows with evidence-oriented fields.
- `candidate_urls[]`: suggested follow-up URLs that may contain complementary information.
- `page_status`: coverage and failure state for that URL.

### 2.2 Planner Owns Adoption
`candidate_urls[]` are suggestions, not automatic new `url_hits`.
The planner sees them through agent memory and decides whether to search, fetch, or extract them later.

### 2.3 Fewer Heuristics
Hard semantic decisions, especially around complementary URLs, should prefer the LLM contract over growing local policy code.
Deterministic logic is still appropriate for:

- link collection from fetched HTML,
- URL normalization and dedupe,
- coverage bookkeeping,
- artifact writing,
- compact trajectory/debug projection.

### 2.4 Small Ownership Boundaries
Do not collapse extraction back into one giant mixed file.
Current ownership is split on purpose:

- planner contract,
- loop runtime,
- action dispatch,
- search execution,
- extract request preparation,
- extract runtime,
- extract LLM prompt/schema,
- candidate shaping,
- state application,
- trace/result projection.

### 2.5 State Transition Pipeline
The main loop is intentionally organized as:

`action result -> state_apply -> canonical state -> view/result/trace`

That means:

- action modules produce compact action results, not direct loop mutations
- `agentic_state_apply.py` owns deterministic state transitions from those results into canonical loop state
- loop state remains the single source of truth for URL progress, matched papers, and plan progress
- `agentic_view.py`, `agentic_trace.py`, and `agentic_result.py` project that canonical state outward for the planner, trajectory, and final artifact

This separation is a deliberate design decision.
It keeps runtime mutation logic away from presentation logic, makes replay/debugging easier, and prevents UI/trajectory formatting changes from also changing loop behavior.

### 2.6 Explicit Planner State Reconstruction
The planner is stateful at the application level, but not through vanilla chat history accumulation.

Instead, each turn:

- keeps canonical domain state in the loop,
- rebuilds compact planner memory from that state,
- wraps that memory into a bounded working-state payload,
- sends that reconstructed state to the planner LLM as the authoritative context for the next decision.

This is a core domain-specific design choice.
The important state is not "all prior messages"; it is the structured retrieval state built from known URLs, extract coverage, matched papers, blockers, last-step deltas, and outstanding plan/todo items.

## 3) Main Artifacts and Contracts

The agentic run writes four main retrieval artifacts under `workspace/<project>/artifacts/retrieval/`:

### 3.1 `agentic_result.yaml`
Stable user-facing result contract.
Written by `src/orchestrator/agentic_result.py`.

Contains:
- final `papers`
- compact `coverage`
- run `status`
- `stop_reason`
- refs to trajectory and raw trace

### 3.2 `agentic_trajectory.yaml`
Compact cycle-by-cycle view of what the planner did.
Written by `src/orchestrator/agentic_view.py`.

Contains:
- full `steps`
- simplified `user_view`
- action summaries
- extract debug summary
- refs to raw events and fetched raw files

### 3.3 `agentic_raw.ndjson`
Append-only raw event log.
Written by `src/orchestrator/agentic_loop.py`.

This is the first file to inspect when behavior is unclear. It contains:
- planner input/output payloads
- action input/output payloads
- `op_start` / `op_end` events for search, fetch, and extract sub-operations

### 3.4 `fetch_raw/`
Saved raw fetched pages and PDFs.
Referenced by `raw_path` in fetched records and by `fetch_raw_paths` in trajectory refs.

## 4) Runtime State

The loop owns typed state in `src/orchestrator/agentic_loop.py`:

- `paper_state`: final and fallback paper candidates
- `url_state`: current shortlisted / known URLs
- `extract_state`: per-URL coverage state plus fetched-record index
- `agent_plan`: planner-owned active step and todo list
- `agent_memory`: compact memory view built for the planner
- `cycle_trace`: append-only per-cycle trace used for trajectory writing

Action executors do not mutate those dataclasses directly.
`src/orchestrator/agentic_runtime.py` bridges them through a small mutable payload:

- `url_hits`
- `fetched_records`
- `extract_state_by_url`

That bridge is the shared in-memory contract between the loop and the action layer.
After an action completes, the loop applies deterministic state transitions before rebuilding outward-facing projections.

### 4.1 Planner Memory as the Domain State Interface
The most important state-to-LLM boundary is in `src/orchestrator/agentic_view.py`:

- `_build_agent_memory(...)` builds the compact domain memory used by the planner
- `_build_agent_working_state(...)` wraps that memory with the current task and cycle metadata

This is where domain knowledge is intentionally distilled for planning.
Today that memory includes:

- current plan state (`active_step`, todo counts)
- known URLs with projected progress/status
- matched papers already found
- suggested URLs from the latest extract action
- blockers, including failed pages and stop reasons
- last step summary and last change delta

This layer matters because it decides what the planner is allowed to "remember" and reason over.
It is not just serialization glue; it is the explicit state interface between the domain runtime and the planning LLM.

The loop refreshes that memory before each planner turn in `src/orchestrator/agentic_loop.py::_refresh_agent_memory(...)`.

## 5) Code Map

Use this map when navigating the refactored codebase.

| Module | Responsibility |
|---|---|
| `src/orchestrator/agentic.py` | Public entrypoint and config wiring for `run_retrieve_agentic`. |
| `src/orchestrator/agentic_loop.py` | Main cycle runtime, raw event writing, action execution, cycle finalization, result/trajectory persistence. |
| `src/orchestrator/agentic_contracts.py` | Planner prompt and JSON response contract. |
| `src/orchestrator/agentic_view.py` | Planner working state, compact memory, trajectory writing, parameter sanitization. |
| `src/orchestrator/agentic_actions.py` | Action dispatch and dependency injection for search/extract actions. |
| `src/orchestrator/agentic_search.py` | Search execution, query normalization, filtering, ranking, shortlist shaping. |
| `src/orchestrator/agentic_extract_prepare.py` | Extract request resolution, target normalization, scoped filters, target preparation. |
| `src/orchestrator/agentic_extract_runtime.py` | Extract action runtime, per-target LLM batching, coverage trace, candidate URL proposal wiring, final action-result assembly. |
| `src/orchestrator/agentic_extract_llm.py` | Stateless extraction prompts, schemas, token-budget helpers, OpenAI-compatible exchange. |
| `src/orchestrator/agentic_extract.py` | Thin helper surface that exposes extract LLM utilities. Do not grow runtime here again. |
| `src/orchestrator/agentic_extract_candidates.py` | Candidate shaping, canonicalization, candidate-URL input collection. |
| `src/orchestrator/agentic_state_apply.py` | Apply action results back into loop state and compact coverage projections. |
| `src/orchestrator/agentic_trace.py` | Compact action debug summaries attached to cycle trace. |
| `src/orchestrator/agentic_result.py` | Final result shaping and artifact cleanup. |
| `src/orchestrator/agentic_projection.py` | Shared per-URL coverage/status projection used by result, view, and trace. |

## 6) Planner Contract

The planner LLM contract lives in `src/orchestrator/agentic_contracts.py`.
It returns strict JSON with:

- `decision`: `continue` or `stop`
- `state_delta`: incremental plan/todo updates
- `progress`: compact user-facing progress note
- `action`: one of `search_web` or `extract_content`

Important design rule:
- the planner does not control fetch retries, batch sizes, token budgets, or low-level extraction mechanics
- the planner chooses intentful actions and compact parameters only
- the planner receives explicit reconstructed state, not a raw transcript of all prior turns

The planner side should be understood as:

- multi-turn at the application level,
- explicitly state-carried through `_build_agent_memory(...)` and `_build_agent_working_state(...)`,
- bounded and domain-shaped rather than chat-history-shaped.

For `extract_content`, planner params should stay high level:
- target `url`
- optional lexical `text_filters`
  `literal_any` for grep-like phrases or names
  `regex_any` for compact regex when lexical trimming is useful
- optional `semantic_focus` for short semantic guidance when lexical trimming is weak or absent
- minimal semantic `filters` or `match`

`anchor_terms` is now a legacy compatibility alias. The preferred contract is: planner decides whether a URL needs lexical trimming, semantic guidance, or both, and the app keeps batching/retry/ranking mechanics on its own side.

## 7) Action Contracts

### 7.1 Search Action
Owner:
- dispatcher: `src/orchestrator/agentic_actions.py`
- implementation: `src/orchestrator/agentic_search.py`

Primary result fields:
- `raw_candidates`
- `web_rows`
- `shortlist_hints`
- `status`
- `notes`

The loop then applies that output through `src/orchestrator/agentic_state_apply.py::_apply_search_action_result`, which updates `url_hits`.

### 7.2 Extract Action
Owners:
- dispatcher: `src/orchestrator/agentic_actions.py`
- request prep: `src/orchestrator/agentic_extract_prepare.py`
- runtime: `src/orchestrator/agentic_extract_runtime.py`

Primary result fields:
- `paper_candidates`
- `candidate_urls`
- `extracted_records`
- `extract_windows_trace`
- `coverage_has_more`
- `coverage_passes`
- `extract_timeout_errors`
- `extract_empty_semantic`
- `status`
- `notes`

The loop applies that output through:
- `src/orchestrator/agentic_state_apply.py::_apply_extract_coverage_update`
- `src/orchestrator/agentic_state_apply.py::_apply_extract_candidate_results`

## 8) Main Runtime Flow

### 8.1 Entry
`src/orchestrator/agentic.py::run_retrieve_agentic`

Responsibilities:
- load project config
- resolve environment-backed runtime settings
- build `_AgenticSearchLoop`
- start the run

### 8.2 Main Loop
`src/orchestrator/agentic_loop.py::_run_agentic_search_loop`

Each cycle:
1. refresh compact agent memory from canonical loop state
2. build planner working state from that memory
3. call planner LLM
4. sanitize and record the chosen action
5. execute the action through `agentic_actions`
6. apply state updates
7. write trajectory and result artifacts

### 8.3 Search Path
Follow this path when a search step behaves oddly:

1. `agentic_loop.py::_run_cycle_action`
2. `agentic_actions.py::execute_search_web_action`
3. `agentic_search.py::execute_search_web_action`
4. `agentic_state_apply.py::_apply_search_action_result`

If the bug is about shortlist quality, stay in `agentic_search.py`.
If the bug is about persisted `url_hits`, inspect `agentic_state_apply.py`.

### 8.4 Extract Path
Follow this path when extraction behaves oddly:

1. `agentic_loop.py::_run_cycle_action`
2. `agentic_actions.py::execute_extract_content_action`
3. `agentic_extract_runtime.py::execute_extract_content_action`
4. `agentic_extract_prepare.py::resolve_extract_request`
5. `agentic_extract_runtime.py::execute_resolved_extract_request`
6. `agentic_extract_prepare.py::prepare_extract_target`
7. `agentic_extract_runtime.py::_run_prepared_extract_target`
8. `agentic_extract_llm.py` via helpers in `agentic_extract.py`
9. `agentic_extract_candidates.py`

This split is intentional:
- request-selection bugs should be debugged in `agentic_extract_prepare.py`
- page-runtime bugs should be debugged in `agentic_extract_runtime.py`
- prompt/schema bugs should be debugged in `agentic_extract_llm.py`
- candidate canonicalization bugs should be debugged in `agentic_extract_candidates.py`

## 9) How Complementary URLs Work

Complementary URL discovery is deliberately simple:

1. deterministic code collects candidate links from fetched records
2. deterministic code normalizes and dedupes those links
3. the stateless LLM chooses which links look complementary to the current page and still relevant to the overall query
4. the extract action returns them as `candidate_urls`
5. planner memory surfaces them as suggested URLs
6. the planner decides whether to act on them later

Do not reintroduce local ranking/classification taxonomies here unless replay evidence proves the prompt contract is insufficient.

## 10) Troubleshooting Guide

Start from artifacts, then drill into code:

| Symptom | First artifact | Primary code path |
|---|---|---|
| Planner chose the wrong action or weird params | `agentic_raw.ndjson` planner input/output | `agentic_contracts.py`, `agentic_view.py`, `agentic_loop.py::_run_agent_turn` |
| Search found results but shortlist looks wrong | `agentic_raw.ndjson` action output | `agentic_search.py`, then `agentic_state_apply.py::_apply_search_action_result` |
| A target URL was not fetched or reused correctly | `agentic_raw.ndjson`, `fetch_raw/` | `agentic_extract_prepare.py::resolve_extract_request`, `agentic_runtime.py` |
| Extract missed obvious papers on a page | `agentic_trajectory.yaml` `action_debug.targets` | `agentic_extract_prepare.py`, `agentic_extract_runtime.py`, `agentic_extract_llm.py` |
| Candidate URLs are noisy or missing | `agentic_trajectory.yaml` `action_debug.candidate_urls` | `agentic_extract_candidates.py`, `agentic_extract_llm.py` |
| Coverage looks inconsistent between result and trajectory | `agentic_result.yaml` + `agentic_trajectory.yaml` | `agentic_projection.py`, `agentic_state_apply.py`, `agentic_trace.py`, `agentic_view.py` |
| Final papers look right in trace but wrong in result | `agentic_trajectory.yaml` + `agentic_result.yaml` | `agentic_state_apply.py`, `agentic_result.py` |

Suggested debug order for a bad run:

1. read `agentic_result.yaml` to see the final stop reason and compact coverage
2. read `agentic_trajectory.yaml` to find the cycle where behavior drifted
3. read matching rows in `agentic_raw.ndjson`
4. inspect referenced `fetch_raw/*` files if extraction quality is the issue
5. open the responsible module from the code map above

When deciding where a bug belongs, use this rule:

- if the problem is "the tool output was wrong", start in the action/search/extract module
- if the problem is "the loop state changed incorrectly", start in `agentic_state_apply.py`
- if the problem is "the state is right but the planner/trajectory/result view is misleading", start in `agentic_view.py`, `agentic_trace.py`, or `agentic_result.py`

## 11) Current Guardrails

When extending the system, keep these rules:

- do not put loop logic back into `agentic.py`
- do not put extract runtime back into `agentic_extract.py`
- keep planner-facing contracts compact and action-oriented
- keep discovered URLs as planner suggestions, not automatic known URLs
- prefer replay-backed prompt/schema improvements over new fallback heuristics
- update `docs/TODO.md` and this file when ownership boundaries move
