# Implementation Progress Board

Last updated: 2026-03-23

## 0) Usage Contract

This board is the persistent external memory for implementation tracking.

Rules:
- Read this file before coding.
- Update this file after meaningful progress.
- Keep overall program tracking visible.
- Keep detailed, execution-ready plans primarily in active sections.
- For now, the active detailed section is Agentic Meta Info Retrieval.
- The old end-to-end agentic search spec has been removed because it drifted from the implementation; treat `src/orchestrator/agentic.py` plus focused module docs/tests as the current source of truth until a replacement minimal spec is written.

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
- iterative agent loop with `search_web`, `extract_content`
- raw fetch artifact persistence
- extractor LLM integration
- plan/progress trajectory and CLI progress events

Main issues:
- main loop still carries too much mixed state (`plan_state`, `plan_progress`, `timeline_moves`, `runtime_state`, `run_result`)
- tool config still leaks into loop attributes instead of being grouped by agent/search/extract tool
- final candidates still include noisy title variants in some runs (author/session leakage)
- extraction quality still overly heuristic in parts
- runtime dominated by LLM latency
- traces still need clearer latency split (`main_agent` vs `extractor`)
- extraction input/output contracts still need to be narrowed around a single URL-target path so debugging is not spread across `target_ids`/`urls`/coverage side paths

### 3.2.2 Big-Line Phases and Milestones

| Phase | Milestone | Significance | Goal | Status |
|---|---|---|---|---|
| P0 | `M-Agentic-P0` | Agent can plan and call web search iteratively. | Structured planning + actionable web-search loop works end-to-end. | Done |
| P1 | `M-Agentic-P1` | End-to-end `search -> fetch -> extract` flow exists. | Agent can produce paper candidates via fetch and extraction actions. | Done |
| P2 | `M-Agentic-Extract-QoS-v1` | Extraction quality and runtime become production-usable. | High precision/recall with stable normalization and operation-level timing observability. | Active |
| P3 | `M-Agentic-Cache-v1` | Cross-run raw fetch reuse and artifact simplification. | Global raw-fetch cache and slim final result artifact contract. | Planned |

Phase goals:
- `P2`: precision/recall quality hardening + LLM latency observability + extractor batching.
- `P3`: global fetch cache + stable minimal user-facing result contract.

Milestone status notes:
- `M-Agentic-P0`: achieved and stable.
- `M-Agentic-P1`: achieved; quality/perf still being hardened in `P2`.
- `M-Agentic-Extract-QoS-v1`: in progress; main blockers are consistent `papers by X` semantics, complementary official-page fetch coverage, and replay-backed validation.
- `M-Agentic-Cache-v1`: not started.

### 3.2.3 Active Phase (P2) Work Plan

| ID | Task | Status | Notes |
|---|---|---|---|
| A1 | Split latency accounting (`main_agent`, `extractor`, fetch, preprocess, postprocess) | Done | Implemented generic paired op events (`op_start`/`op_end`) with shared `op_id` across agent/search/fetch/extractor. |
| A2 | Stateless extractor schema-first row extraction (`paper_title_normalized`, authors, affiliations, abstract, evidence) | In progress | Schema extraction is live; row completeness is still being tracked for later enrichment and coverage reporting. |
| A3 | Large-blob extractor batching (fewer, larger calls with token budget) | In progress | Moving from small char-budget segment passes to page-scale token-budgeted batches with lower retry amplification and explicit token accounting. |
| A4 | Merge/dedup by normalized metadata (DOI/arXiv/title+author overlap) | In progress | Canonical merge exists; next tighten source-priority and author-aware merge without recall loss. |
| A8 | Post-extract cleanup simplification | Deferred | Canonicalizer-specific cleanup is being removed from the active loop so extract coverage and shortlist completeness are easier to debug. |
| A5 | Final artifact slimming: final result contains final matches only | In progress | Result artifact is slimmer; trajectory still needs better extract progress surfacing. |
| A6 | Global raw-fetch cache outside project scope | Todo | Cross-run URL reuse to avoid refetching. |
| A7 | Replay tests over saved fetch HTML to validate precision/recall | In progress | Expand fixtures and checks. |
| A9 | Canonicalizer scope fix: cycle-local cleanup before global merge | In progress | Prevent later venue-local extraction intent from pruning valid papers found in earlier cycles. |
| A10 | Match semantics alignment for `papers by X` | In progress | Default semantics should be at least one matching coauthor affiliation or author, not majority/first-author authorship. |
| A11 | Preserve complementary official listing pages in fetch shortlist | In progress | Keep official `accepted-papers`/`program`/`technical sessions` style pages together when they provide complementary structure. |
| A12 | Fast-path batch extraction prototype for authoritative pages | In progress | Defaulting authoritative/listing pages toward page-scale extraction batches to cut extractor latency. |
| A13 | User-facing trajectory surfacing for extract progress | In progress | Show extract coverage and accumulated final-match progress in trajectory. |
| A14 | External extractor spike (`langextract`-style grounding or adapter) | Todo | Evaluate whether external grounded extraction tooling helps long venue pages after the in-house page-batch path stabilizes. |
| A15 | Minimal loop-state refactor in `agentic.py` | In progress | `run_state` now owns `cycle_index/status/stop_reason`, is now a typed state record instead of a dict, `trace_state` is also now a typed record instead of a dict, `search_config` is now a typed tool-config record instead of a dict, `agent_config` and `extract_config` are now typed model/tool-config records instead of dicts, `result_config` is now a typed result-config record instead of a dict, `url_state` and `extract_state` are now typed loop-state records instead of dicts, `paper_state` is now a typed loop-state record that only owns final/fallback candidates, stale default helper functions for those typed state records are removed, extract coverage is now derived from `extract_state.by_url` at result-write time instead of being stored on `paper_state`, and `agent_plan` is now strict `active_step + todo` instead of cue summaries or step lists, `agent_memory` is the compact planner input, `plan_progress` is removed, the old compact-plan snapshot/delta side path is removed, dead previous-summary/candidate compaction helpers are removed from the view layer, the dead condensed-summary event path is removed, loop-side action views and cycle-commit calls are narrowed to only the fields the loop still consumes, `plan_result` / `action_ctx` no longer carry duplicate query/candidate payloads that can be read from normalized params or action results, `plan_result` now carries one normalized `agent_decision` record instead of split stop/reason fields, `plan_result` no longer carries `state_delta` or agent `latest_progress` just to seed the action trace, the loop boundary now uses small typed records instead of open dicts for the planner turn and action run, and the planner decision itself is now a typed record instead of a free-form dict, `run_retrieve_agentic(...)` now constructs typed loop state/config records directly, so the temporary dict-normalization bridge is removed, action context no longer carries a separate action-output raw id when that id is already the tail of `raw_event_ids`, action context no longer carries duplicate tool-progress state that can be derived from the action result, planner-call plumbing no longer threads duplicate prompt arguments, cycle summary/outcome helpers now take only the counts and metadata they actually consume, cycle action start/output trace plumbing is now localized behind `_start_cycle_action` / `_finish_cycle_action`, agent request/response raw-event plumbing is now localized behind `_start_agent_turn` / `_finish_agent_turn` / `_fail_agent_turn`, agent output normalization is now localized behind `_normalize_agent_turn_output`, cycle-trace initialization is now localized behind `_seed_cycle_trace`, the agent-response emit no longer repeats `selected_action` or `planned_queries` outside the payload itself, `cycle_trace` is the single append-only loop trace and now records per-cycle deltas, action inputs, minimal action outcomes, decisions, progress, and refs instead of full before/after state copies or duplicate counters, the trace no longer stores a duplicate `selected_action` field when the action is already present in `action_input`, cycle refs are finalized once at cycle end instead of being partially assembled in the summary path, the cycle-start trace row no longer seeds a partial `refs` snapshot, trajectory output now projects directly from that slimmer trace without a duplicate top-level `status_snapshot`, loop-side reads of `paper_state` are now localized behind small helpers for final/fallback candidates, and loop-side candidate writes are now localized behind helper functions; `runtime_state` no longer lives on the loop object and is only a local tool bridge during action execution, and the current follow-up simplification path is to narrow extraction to explicit URL targets only and keep post-extract cleanup deterministic while debugging. |
| A16 | Move tool config out of loop attributes | In progress | Search knobs now live behind `search_config`, result display limit lives behind `result_config`, and main-agent/extractor LLM knobs now live behind `agent_config` / `extract_config`; old loop-level config aliases are no longer used inside the main loop path, and `request_id` / `workflow_name` are removed from the loop surface. |
| A17 | Simplify agent-memory projection | In progress | Main agent now receives one compact `agent_memory` snapshot with `active_step`, `known_urls`, `matched_papers`, `blockers`, `last_step`, and `last_change`; old `previous_summary` / `previous_candidates` are no longer part of planner input, planner messages no longer duplicate `user_prompt` outside `working_state`, the loop now consumes only the new `decision/state_delta/progress/action` planner envelope, and `agent_plan` is now explicitly separated from the compact memory projection. |

Milestone mapping for active phase:
- `M-Agentic-Extract-QoS-v1` is achieved when `A1..A5`, `A8`, `A9`, `A10`, and `A11` are complete and validated on replay fixtures.
- `A6` is a bridge task to `P3` and may start in parallel after `A1/A2`.
- `A12` is a performance-track task and can proceed in parallel once correctness regressions are contained.
- `A15..A17` are enabling refactors for faster troubleshooting and safer future extraction/runtime simplification.

### 3.2.4 Detailed Plan for Current Action (`A15`) Only

Problem statement (`A15`):
- `agentic.py` still mixes loop orchestration, planner memory, tool runtime state, result assembly, and trace artifacts in one mutable surface.
- Several loop attributes are not true loop state:
  - `workflow_name`
  - `request_id`
  - loop-level `top_n`
  - loop-level `final_limit`
  - `web_results_per_query`
  - `searxng_categories`
- Planner context is still partly spread across:
  - `agent_plan`
  - derived `agent_memory`
- Operational state is also mixed:
  - `run_result`
  - `paper_state`
  - `cycle_trace`
  - loop-local tool bridge payloads during action execution
  - final-result bookkeeping that still sits in `run_result`

Minimal target state:
- `run_state`
  - `cycle_index`
  - `status`
  - `stop_reason`
- `agent_memory`
  - compact semantic state passed back to the main agent each cycle
  - minimum viable contents:
    - active goal / active step
    - compact todo summary
    - investigated URL summary (`url`, `title/peek`, `status`)
    - matched paper summary
    - current blocker summary
    - `last_step`
    - `last_change`
- `url_state`
  - known URLs and investigation status
  - minimum viable fields per URL:
    - `url`
    - `source`
    - `status`
    - `title`
    - `peek`
    - `discovered_in_cycle`
    - `last_error`
- `extract_state`
  - per-URL fetch/extract operational state
  - minimum viable fields per URL:
    - `fetched`
    - `completed`
    - `failed`
    - `segments_done`
    - `last_error`
- `papers`
  - derived final output only
  - emitted from `paper_state` during result compaction
- `paper_state`
  - runtime paper/candidate state before final result compaction
  - minimum viable contents:
    - `final_candidates`
    - `fallback_candidates`
- `cycle_trace`
  - append-only per-cycle record for trajectory/debug/user-facing projection

Design direction:
1. Keep the loop skeleton small first:
   - planner turn
   - tool action
   - cycle finalize
2. Replace loop attributes incrementally, not all at once:
   - first fold `current_cycle` + `run_status` + `stop_reason` into `run_state`
   - then remove `request_id` and `workflow_name`
   - then move search config fields off the loop object
3. Treat `agent_memory` as the only planner-facing persistent state:
   - compose it at cycle end from `agent_plan`/URL summary/paper summary
   - pass only `agent_memory` back to `_build_agent_messages(...)`
   - keep it explicitly small: enough for next-step reasoning and duplicate-avoidance, not full trajectory replay
   - current progress: `previous_summary` / `previous_candidates` are removed from planner input, `plan_progress` is removed, the old compact-plan snapshot/delta bookkeeping is removed, the stale cue-summary side path is removed from `agent_plan`, and planner state is now strict `active_step + todo`
4. Separate app/tool state from planner state:
   - `url_state` for discovery/investigation status
   - `extract_state` for per-URL fetch/extract progress
   - `paper_state` for runtime paper/candidate aggregation and coverage
5. Keep `cycle_trace` as the only append-only trace structure:
   - record per-cycle deltas, action inputs, minimal action outcomes, progress, and refs
   - do not mirror full loop state (`status_snapshot`, full before/after state blobs) into each cycle row
   - do not duplicate candidate counts inside both `decision` and `progress`
   - trajectory output should project from `steps` / `user_view`, not maintain a second top-level snapshot of the latest step
  - later user-facing trajectory can derive from it
  - avoid parallel history structures where possible
  - current progress: `plan_progress` is removed; progress snapshots now carry one explicit `latest_progress` record instead of a separate progress history list, and the loop now names this structure `cycle_trace`
6. Keep `run_result` minimal:
   - run/result metadata only
   - final/fallback candidates and coverage belong in `paper_state`
7. Defer deeper extraction/runtime behavior changes until the loop state model is simplified and stable

Acceptance criteria (`A15`):
1. `_AgenticSearchLoop` fields map clearly to:
   - run state
   - agent plan
   - agent memory
   - tool state
   - paper state
   - cycle trace
2. `workflow_name` and `request_id` are removed.
3. Search tool config no longer lives as ad hoc loop attributes.
4. `plan_progress` is removed or fully subsumed by `cycle_trace`.
5. `run_state` owns cycle index, status, and stop reason.
6. The next-cycle main-agent context can be explained directly from `agent_memory` alone.
7. `agent_memory` carries enough short-horizon continuity to avoid repeated work:
   - `active_step`
   - `last_step`
   - `last_change`

### 3.2.5 Active Focus

Current action focus:
1. Simplify `_AgenticSearchLoop` to minimum viable state.
2. Move search/extract/LLM config out of loop attributes into tool-specific config groups.
3. Collapse planner-facing memory into one `agent_memory` projection.
4. Keep changes incremental and structurally safe before touching deeper extraction behavior again.

## 4) Non-Active Modules (Summary Only)

| Module | Next Gate To Open Detailed Board |
|---|---|
| Data Backend and RAG | Open after agentic extraction artifacts and metadata schema stabilize. |
| Paper Context Retrieval | Open after agentic final candidates are consistently stable. |
| Paper Context Formatted Extraction | Open after context retrieval schema is stable. |
| Analysis Skill | Open after extraction and context artifacts are versioned and stable. |
| Output Skills | Open after analysis outputs are stable for rendering. |
| UI/TUI Design | Open after trajectory and result contracts are stable. |
