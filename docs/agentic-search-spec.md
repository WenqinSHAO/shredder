# Agentic Search Minimal Spec

Last updated: 2026-05-09

## Purpose

This document is the lightweight contract spec for the current compact agentic search loop.

It is intentionally narrower than the older end-to-end spec that was removed. The source
of truth remains the implementation in:

- `src/orchestrator/agentic.py`
- `src/orchestrator/agentic_contracts.py`
- `src/orchestrator/agentic_extract.py`
- `src/orchestrator/agentic_result.py`
- `src/orchestrator/agentic_view.py`

For current pending issues and implementation priority, do **not** use this file as the main board.
Use `docs/TODO.md`, especially:

- `3.2.7 Current Focus`
- `3.2.8 Active Priority Stack`
- `3.2.10 Active Next Slices`

Important current caveat:
- the design is moving toward `action result -> state_apply -> canonical state -> projection`
- but the `E21` state consistency slice is still in progress, so some extract-runtime paths still
  mutate shared extract state directly rather than returning only state deltas
- treat this file as the compact contract reference, not the authoritative “what is left to fix” document

This spec focuses on the most important data contracts:

1. the main agent contract
2. the tool/runtime contract between the loop and action executors
3. the user-facing result and trajectory artifacts
4. how the loop updates those contracts each cycle

## 1) High-Level Loop Shape

The loop has three repeated phases per cycle:

1. planner turn
2. tool action
3. cycle finalize

The planner decides whether to:

- `search_web`
- `extract_content`
- stop

The loop keeps its persistent runtime state in typed records instead of open dicts where possible.

## 2) Persistent Loop State

The current persistent loop state is:

- `run_state`
  - `cycle_index`
  - `status`
  - `stop_reason`
- `agent_plan`
  - `active_step`
  - `todo`
- `agent_memory`
  - compact planner-facing memory snapshot
- `url_state`
  - shortlist/discovery state for candidate URLs
- `extract_state`
  - per-URL extract progress plus compact fetched-record index
- `paper_state`
  - final candidate state only:
    - `final_candidates`
    - `fallback_candidates`
- `cycle_trace`
  - append-only cycle summaries for trajectory/debug projection

Notably:

- extract coverage is **not** stored on `paper_state`
- result coverage is derived from `extract_state.by_url` at result-write time

## 3) Main Agent Contract

The planner input is built from:

- the user prompt
- the current cycle index / max cycles
- `agent_memory`

`agent_memory` is the only planner-facing persistent summary. It should stay compact and avoid
replaying the entire trajectory.

The planner output is normalized into:

- `selected_action`
- `planned_queries`
- `action_params`
- `state_delta`
- `latest_progress`
- `agent_decision`

Where `agent_decision` is:

- `mode`: usually `continue` or `stop`
- `reason`: compact explanation of why

The loop then applies:

- `state_delta` to `agent_plan`
- `latest_progress` to cycle progress projection
- `selected_action` / `action_params` to the tool phase

## 4) Shared Search Status / Action Runtime Contract

The loop exchanges shared search-status state through `_ActionRuntime`.

This is not a third tool. It is the loop-owned mutable status surface that action executors can
read from and write back to while a cycle is running.

For the current actions, this contract exists less because every action semantically needs every
field, and more because the app currently lets action-carrying functions operate against one shared
runtime/status payload instead of passing many separate mutable objects around.

Current runtime payload fields:

- `url_hits`
  - shortlisted URL rows the agent can refer to by `hit_id`
- `extract_state_by_url`
  - per-URL extract state such as:
    - `segments_done`
    - `segment_total`
    - `completed`
    - `failed`
    - `last_error`
    - `coverage_has_more`
- `fetched_records`
  - compact list projection of fetched page records used by extract auto-fetch/reuse paths

The loop owns the typed runtime adapter:

- `_ActionRuntime.from_loop(...)`
- `_ActionRuntime.to_payload()`
- `_ActionRuntime.from_payload(...)`
- `_ActionRuntime.apply_to_loop(...)`

Current producer/consumer split:

- producer before action execution: the loop
- consumer during action execution: the selected action executor
- producer after action execution: the selected action executor mutates the runtime payload
- consumer after action execution: the loop applies the mutated payload back onto typed loop state

This keeps the action executor boundary explicit and makes future shrinking of `fetched_records`
safer.

## 5) `search_web` Action Contract

The search action consumes normalized search params and returns a result containing at least:

- `status`
- `notes`
- `raw_candidates`
- optional shortlist hint metadata

The loop finalizes search output by:

1. filtering and ranking `raw_candidates`
2. applying shortlist hints
3. selecting a diverse shortlist
4. projecting that shortlist into `url_state.hits`

`url_state.hits` is the loop’s persistent URL shortlist/discovery surface.

## 6) `extract_content` Action Contract

The extract action consumes either:

- explicit URL targets
- target ids that map back to `url_state.hits`

It returns fields including:

- `status`
- `notes`
- `paper_candidates`
- `extract_windows_trace`
- fetch/extract counters

The loop uses `extract_windows_trace` to update `extract_state.by_url` with compact per-URL
coverage/progress state. This includes:

- `target_id`
- `segments_done`
- `segment_total`
- `coverage_has_more`

The loop then merges `paper_candidates` into:

- `paper_state.final_candidates`
- `paper_state.fallback_candidates`

depending on venue-evidence filtering.

## 7) User-Facing Result Contract

The result artifact is `agentic_result.yaml`.

Important top-level fields:

- `artifact_type`
- `schema_version`
- `query`
- `status`
- `stop_reason`
- `cycle_count`
- `trajectory_ref`
- `raw_trace_ref`
- `coverage`
- `papers`

Notes:

- `trajectory_ref` and `raw_trace_ref` are artifact links for clients or operators that want to
  inspect the compact user-facing trajectory or the lower-level raw trace alongside the final
  result.
- `shortlisted_urls_total` is a count, not a list. The per-URL rows live in `url_checks`.

### Result coverage contract

Coverage is derived from `extract_state.by_url` and currently contains:

- `shortlisted_urls_total`
- `shortlisted_urls_complete`
- `shortlisted_urls_with_more_results`
- `url_checks`

Each `url_checks` row contains:

- `url`
- `target_id`
- `segments_done`
- `segment_total`
- `all_papers_extracted`
- `has_more_results`

### Result papers contract

Each final paper row is currently a compact user-facing projection of final candidates:

- `title`
- `doi`
- `arxiv_url`
- `source_url`
- `venue`
- `year`
- `authors`
- `affiliations`
- `abstract_snippet`
- `confidence`
- `evidence_ref`

Notes:

- The current implementation still emits `abstract_snippet` rather than full `abstract`.
- The current implementation still emits `confidence`, but its long-term necessity is under review.

## 8) User-Facing Trajectory Contract

The trajectory artifact is `agentic_trajectory.yaml`.

It can likely be simplified further once the final compact `cycle_trace` contract is settled.

Each cycle row projects:

- `cycle_index`
- `action_id`
- `status`
- `action`
- `summary`
- `decision`
- `decision_reason`
- `progress`
- `extract_summary`
- `raw_event_ids`
- `fetch_raw_paths`

The trajectory should expose user-meaningful progress, not full raw tool payloads.

## 9) Cycle Update Semantics

Here, "cycle" means one iteration of the main search loop and the loop-state updates attached to
that iteration.

Each cycle updates state in this order:

1. set `run_state.cycle_index`
2. refresh `agent_memory`
3. ask the main agent for the next action
4. apply the main-agent `state_delta` to `agent_plan`
5. append a new cycle trace row
6. execute the selected action through `_ActionRuntime`
   - concretely, the loop snapshots shared search status into a runtime payload, passes that
     payload plus action params to the selected action executor, and then re-applies the mutated
     runtime payload back onto `url_state` / `extract_state`
   - note: this is the intended exchange boundary, but `E21` is still tightening extract-state
     ownership so some runtime mutation still bypasses the final state-delta-only design
7. write action-side state back onto:
   - `url_state`
   - `extract_state`
8. finalize action outputs into:
   - `paper_state`
   - `cycle_trace`
   - `run_state.stop_reason` if needed
9. refresh `agent_memory` again
   - `agent_memory` is rebuilt from updated loop state, especially:
     - `agent_plan`
     - `url_state.hits`
     - `extract_state.by_url`
     - compact matched-paper summaries
     - compact recent cycle-trace summaries
   - today this rebuild is deterministic projection logic; a later design could introduce a
     dedicated LLM summarization call if that becomes necessary
10. write trajectory/result artifacts

## 10) Current Simplification Goals

The current compaction direction is:

1. typed loop/runtime state instead of open dicts
2. output-only `paper_state` derived from action outputs rather than owning extract coverage
3. result-time coverage derivation from `extract_state`
4. a narrow action-runtime adapter boundary
5. slimmer fetched-record storage and slimmer cycle trace as the next follow-up

## 11) Known Gaps / Follow-Up

This spec is intentionally minimal. It does **not** yet fully specify:

- the exact fetch-record minimization target for `fetched_records`
- the final intended compact shape of `cycle_trace`
- the long-term replay/fixture validation contract
- the future cache contract for cross-run raw fetch reuse
- the extractor refactor needed to better surface URLs that may contain more relevant search
  results in addition to extracting papers from current pages
- the future optimization/refactor plan for how `agent_memory` is built from loop state

Those should be filled in after the next compaction pass lands.

Current known active follow-up, per `docs/TODO.md`:

- finish `E21` state consistency foundation
- add `E22` replay/regression coverage across author, topic, multi-year, and semantic query archetypes
- only then move to richer planner-memory and archetype-specific extraction work
