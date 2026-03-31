# Implementation Progress Board

Last updated: 2026-03-31

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
| Agentic Meta Info Retrieval | `76%` (`███████░░░`) | Active (state consistency + extraction quality + trace readability) |
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
- `src/orchestrator/agentic.py` is now a small coordinator-sized module (`238` lines), and it is no longer the main debuggability problem
- `src/orchestrator/agentic_extract.py` is now a smaller runtime-focused module (`914` lines), and the remaining leverage is inside its runtime loop ownership rather than coordinator/request-shaping cleanup
- current decomposition still leaves too much behavior hidden behind compatibility wrappers and giant helper files, which risks recreating the previous un-debuggable implementation
- the extractor still lacks a first-class page result shape such as `papers[]`, `candidate_urls[]`, and `page_status`
- fallback extraction policy is still heavier than the naive goal and should only grow through narrow replay-backed corrections
- extract coverage / todo / planner memory are still not fully consistent; the latest `workspace/alibabanew` run shows completed-vs-in-progress drift, wasted retries on already completed URLs, and planner-visible todo state that does not match actual per-page coverage
- a few local heuristics still actively hurt extraction quality; removing or relaxing those is higher priority than adding new scoring logic
- the current candidate-URL proposal is still too broad on large venue pages; it should move toward a narrower "next page-ish" follow-up contract rather than wider harvesting
- user-facing trace readability is still insufficient for debugging agentic runs quickly; the raw trace is rich enough, but the human-facing projection is not yet carrying the right explanations
- PDF extraction should stay de-prioritized until we have real replay/live cases that force it; current leverage is on HTML venue pages and loop state consistency
- each refactor slice now needs to reduce file ownership and troubleshooting scope, not only move code across files
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
10. no single agentic module remains a mixed-purpose troubleshooting bottleneck; `agentic.py` is coordinator-only and extraction runtime semantics are no longer bundled into one giant file

### 3.2.4 Hardened Work Board

| ID | Task | Status | Notes |
|---|---|---|---|
| H1 | Shrink `agentic.py` into a coordinator-only loop | In progress | Keep only planner turn, action execution, and cycle finalize in the loop surface. |
| H2 | Separate semantic planner state from operational runtime state | In progress | `agent_memory` remains planner-facing and derived; runtime state owns fetch/extract/search mutation. |
| H3 | Narrow the action/runtime contract | In progress | `_ActionRuntime` should carry only action-execution state that truly crosses the loop boundary. |
| H4 | Isolate fetch/cache into a dedicated mechanism boundary | Todo | Move fetched-record indexing/reuse and later global cache behavior behind a dedicated subsystem. |
| H11 | Keep loop state canonical and planner memory derived from it | In progress | Coverage, todo status, extract retries, and result status must reconcile from runtime state rather than drift apart. |
| H12 | Prefer fewer heuristics and narrower LLM-scoped helpers | In progress | Remove local rules that lower extraction quality; only keep cheap deterministic cleanup at exchange boundaries. |
| H13 | Make human-facing trajectory/debug output easy to consume | In progress | Raw trace can stay verbose; CLI and projected trajectory should explain what happened and why. |
| H5 | Make extraction pipeline deterministic with agent-supplied hints only | In progress | Agent chooses URLs/anchor terms/filters; app owns batching, coverage, retries, and extraction mechanics. |
| H6 | Collapse duplicate normalization/projection helpers into contract-focused modules | Todo | Centralize action-param normalization, state-apply paths, and projection builders. |
| H7 | Keep `cycle_trace` as the single cycle debug ledger | In progress | Trajectory should project from `cycle_trace`, not parallel history structures. |
| H8 | Keep user-facing artifacts minimal and stable | In progress | `agentic_result.yaml` / `agentic_trajectory.yaml` stay projection-only. |
| H9 | Prefer replayable deterministic validation before more planner complexity | In progress | Continue using saved fetch HTML / targeted tests as the quality gate. |
| H10 | Keep module ownership debuggable and bounded | In progress | Refactors must reduce giant-file ownership and wrapper bloat, not just relocate code. |

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
- 2026-03-25: Re-reviewed the refactor after the `E6` rework and explicitly raised debuggability as the next gate: `src/orchestrator/agentic.py` and `src/orchestrator/agentic_extract.py` are still both >2k lines, so the next stage must shrink giant-module ownership rather than adding more features or compatibility wrappers.
- 2026-03-25: Completed `E7` by extracting the cycle state machine into `src/orchestrator/agentic_loop.py`, reducing `src/orchestrator/agentic.py` to entrypoint/action wiring plus compatibility helpers. Preserved patchable search-finalize behavior through an injected wrapper boundary and re-ran targeted plus full tests (`93 passed` in `tests/test_retrieval_agentic_i1.py`; `139 passed, 27 subtests passed` overall).
- 2026-03-25: Completed the first `E8` slice by moving the stateless extract prompt/schema, token-budget scaffolding, and LLM exchange helpers into `src/orchestrator/agentic_extract_llm.py`. `src/orchestrator/agentic_extract.py` is now below 2k lines (`1999`), and full tests still pass (`93 passed` in `tests/test_retrieval_agentic_i1.py`; `139 passed, 27 subtests passed` overall).
- 2026-03-25: Completed the second `E8` slice by moving pure candidate shaping / canonicalization into `src/orchestrator/agentic_extract_candidates.py` and retargeting the pure-function tests to that module instead of `agentic.py`. `src/orchestrator/agentic_extract.py` is now `1499` lines, and full tests still pass (`93 passed` in `tests/test_retrieval_agentic_i1.py`; `139 passed, 27 subtests passed` overall).
- 2026-03-28: Completed the first `E9` slice by removing the candidate-only forwarding helpers from `src/orchestrator/agentic.py`, wiring coordinator-local callers directly to the extracted implementation modules, and keeping tests focused on the real module boundaries. `src/orchestrator/agentic.py` is now `1355` lines, and full tests still pass (`93 passed` in `tests/test_retrieval_agentic_i1.py`; `139 passed, 27 subtests passed` overall).
- 2026-03-28: Completed the second `E9` slice by removing the target-normalization and extract-budget forwarding helpers from `src/orchestrator/agentic.py`, rewiring the extract action deps directly to implementation modules, and retargeting the remaining pure tests to `src/orchestrator/agentic_extract.py`. `src/orchestrator/agentic.py` is now `1288` lines, and full tests still pass (`93 passed` in `tests/test_retrieval_agentic_i1.py`; `139 passed, 27 subtests passed` overall).
- 2026-03-28: Completed the third `E9` slice by moving action execution into `src/orchestrator/agentic_actions.py`, deleting the remaining fetch/search/extract execution facade from `src/orchestrator/agentic.py`, and retargeting runtime tests to the real action/view/text ownership seams. `src/orchestrator/agentic.py` is now `920` lines, and full tests still pass (`93 passed` in `tests/test_retrieval_agentic_i1.py`; `139 passed, 27 subtests passed` overall).
- 2026-03-28: Completed the fourth `E9` slice by deleting the unused cycle-local candidate canonicalization subsystem from `src/orchestrator/agentic.py` and dropping the dead tests that only exercised that non-runtime path. `src/orchestrator/agentic.py` is now `559` lines, and full tests still pass (`88 passed` in `tests/test_retrieval_agentic_i1.py`; `134 passed, 27 subtests passed` overall).
- 2026-03-28: Completed the fifth `E9` slice by retargeting low-level LLM/fetch/extract helper tests to `src/orchestrator/agentic_llm.py`, `src/orchestrator/agentic_fetch.py`, and `src/orchestrator/agentic_extract.py`, then deleting those test-only wrappers from `src/orchestrator/agentic.py`. `src/orchestrator/agentic.py` is now `442` lines, and full tests still pass (`88 passed` in `tests/test_retrieval_agentic_i1.py`; `134 passed, 27 subtests passed` overall).
- 2026-03-28: Completed the sixth `E9` slice by moving `run_extract_agentic_local` into `src/orchestrator/agentic_local_extract.py`, so the coordinator no longer owns the saved-fetch local extraction workflow or its helper imports. `src/orchestrator/agentic.py` is now `238` lines, and full tests still pass (`88 passed` in `tests/test_retrieval_agentic_i1.py`; `134 passed, 27 subtests passed` overall).
- 2026-03-28: Completed the seventh `E9` slice by retargeting the remaining accidental helper tests to `agentic_text`, `agentic_search`, `agentic_view`, `agentic_result`, and `agentic_contracts`, then removing those accidental imports from `src/orchestrator/agentic.py`. The coordinator now mostly exposes intentional runtime entrypoints plus a small search-finalize bridge.
- 2026-03-28: Completed the eighth `E9` slice by deleting the pure candidate/LLM pass-through exports from `src/orchestrator/agentic_extract.py` that only mirrored `agentic_extract_candidates.py` and `agentic_extract_llm.py`. `src/orchestrator/agentic_extract.py` is now `1332` lines, and full tests still pass (`88 passed` in `tests/test_retrieval_agentic_i1.py`; `134 passed, 27 subtests passed` overall).
- 2026-03-28: Completed the ninth `E9` slice by moving extract request/intent/target preparation into `src/orchestrator/agentic_extract_prepare.py`, rewiring `agentic_actions.py`, `agentic_fetch.py`, and the direct tests to that boundary, and leaving `src/orchestrator/agentic_extract.py` focused on extract execution/runtime state. `src/orchestrator/agentic_extract.py` is now `914` lines, and full tests still pass (`88 passed` in `tests/test_retrieval_agentic_i1.py`; `134 passed, 27 subtests passed` overall).
- 2026-03-29: Completed the third `E8` slice and tenth `E9` slice by moving per-target extract execution and action-result assembly into `src/orchestrator/agentic_extract_runtime.py`, rewiring `src/orchestrator/agentic_actions.py` and a focused direct test to that boundary, and shrinking `src/orchestrator/agentic_extract.py` to a `123`-line LLM helper module. The remaining extract runtime owner is now `src/orchestrator/agentic_extract_runtime.py` (`831` lines), and full tests still pass (`89 passed` in `tests/test_retrieval_agentic_i1.py`; `135 passed, 27 subtests passed` overall).
- 2026-03-29: Refreshed `docs/DESIGN.md` so it reflects the current agentic retrieval architecture, live module ownership, main action/result contracts, and the recommended troubleshooting path through `agentic_raw.ndjson`, `agentic_trajectory.yaml`, and the refactored extract/search modules.
- 2026-03-30: Clarified `docs/DESIGN.md` further by replacing the misleading "local-first search" wording with a more precise workspace-local runtime description, and by explicitly documenting the `action result -> state_apply -> state -> view` design principle that now shapes the loop, state-apply, and view/result module split.
- 2026-03-30: Tightened `docs/DESIGN.md` again to make the opening state-centric, to explain that planner state is reconstructed explicitly each turn rather than accumulated as naive chat history, and to call out `_build_agent_memory(...)` / `_build_agent_working_state(...)` as the main domain-state interface for the planning LLM.
- 2026-03-30: Added a saved-artifact replay path in `src/orchestrator/agentic_replay_extract.py` plus the `replay-agentic-extract` CLI/runner entrypoint so current extraction can be exercised against existing `agentic_trajectory.yaml`, `agentic_raw.ndjson`, and `fetch_raw/` without depending on a fresh search run. This replay artifact is now the preferred way to compare page-level extract runtime behavior with narrower saved-segment probes on workspaces such as `google*` and `alibaba`.
- 2026-03-30: Ran the new replay harness live against saved venue-page cycles for `workspace/google` and `workspace/alibaba` using `DS_API_KEY`. The dominant failure mode is now explicit: page-level extract replays timed out on every LLM attempt, and the saved-segment probes timed out too, so the next leverage is backend/runtime timeout handling or smaller extract payloads, not more fallback heuristics.
- 2026-03-30: Tightened the replay/runtime path accordingly: replay probes now derive segments from the current cleaned page text plus current extract intent instead of reusing old `extract_llm_request` payloads, and `src/orchestrator/agentic_extract_runtime.py` now caps per-call extract batches to small sizes (`4` listing segments or `3` detail-page segments). Re-running the saved venue-page cycles for `workspace/google` and `workspace/alibaba` removed the timeout failures entirely and restored live extraction on both representative workspaces.
- 2026-03-30: Investigated the post-timeout precision issues on the live Google replay. The main false-positive seam is now concrete: `src/orchestrator/agentic_extract_candidates.py::_structured_match_text(...)` includes `llm_extract.decision_reason`, so uncertain rows such as "cannot verify Google affiliation" still satisfy institution phrase checks because the filter term is echoed in that reason text. A second, separate seam remains in title normalization: `canonicalize_candidate_title(...)` still does not collapse some paraphrased program-page titles (`Firefly ...`, `NIER ...`) to the accepted-list form, so near-duplicates survive dedupe.
- 2026-03-30: Completed the first `E11` precision slice by removing `llm_extract.decision_reason` from fallback evidence matching in `src/orchestrator/agentic_extract_candidates.py` and adding replay-backed tests for current Google-style false positives (`Discovering Millions...`, `NIER ... Solution`). This keeps uncertain rows from passing institution filters just because the reason text repeats the filter term, while leaving explicit `match_decision == match` behavior unchanged.
- 2026-03-30: Completed the narrow `E11b` title-family dedup slice by adding `src/orchestrator/agentic_extract_dedup.py`, routing a tiny LLM dedup pass over small near-duplicate clusters after paper-candidate shaping, and exposing `paper_dedup_clusters` / `paper_dedup_reduced` in the saved replay artifact. Direct saved-Google runtime replays now show the remaining `Firefly` duplicate collapsing with `paper_dedup_clusters=1` and `paper_dedup_reduced=1`; when the broader replay artifact still varies, treat that as extractor-output variability to recheck with another saved-page replay, not as a reason to grow more local dedup heuristics.
- 2026-03-30: A fresh end-to-end run on `workspace/googlenew` surfaced two integration gaps that replay alone did not show clearly. First, the user-facing CLI progress was too opaque: it hid the selected extract URLs, hid the candidate-URL proposal step entirely, and printed several raw `None` fields. That is now fixed in `src/cli.py`, `src/orchestrator/agentic_loop.py`, and `src/orchestrator/agentic_extract_runtime.py`, so extract action start shows target URLs and the runtime now emits readable `paper_dedup` / `candidate_url_proposal` / `candidate_url_done` stages. Second, the end-to-end search+shortlist path still missed the SIGCOMM companion paper pages (`papers-info` / `accepted-papers`) because the initial venue queries only surfaced `program-details` plus ACM proceedings and the current shortlist diversity logic kept just one same-kind SIGCOMM program URL. That companion-page discovery gap is now the next functional priority ahead of prompt-reuse tuning.
- 2026-03-30: A second end-to-end run on `workspace/alibabanew` exposed a more serious planner-side regression: cycle 1 explicitly decomposed the task into SIGCOMM + NSDI search todos, but the model emitted only one SIGCOMM query, and cycle 2 switched to SIGCOMM extraction before any NSDI venue search happened. Two fixes now land ahead of another live run: `src/orchestrator/agentic_view.py` exposes `memory.next_todos` to the planner so pending venue searches are visible in later cycles, and `src/orchestrator/agentic_loop.py` now aligns executed `search_web` queries with the planner's own declared `search_web` todo targets when the model under-specifies its query list. The CLI trace is also clearer: planner response events now show the selected action and planned queries, `search_web` action start shows the actual query list, and extract batches show segment ranges instead of opaque `0+4` style counters.
- 2026-03-30: A follow-up live rerun on `workspace/alibabanew` confirms that the planner regression is materially fixed: cycle 1 now issues both `SIGCOMM 2025 accepted papers Alibaba` and `NSDI 2025 accepted papers Alibaba`, and cycle 2's extract target list now includes the NSDI technical-sessions page instead of silently dropping that venue. The CLI is also materially more readable in the same run: the planner response prints the actual search queries, `extract_content` start prints the selected URL list, mixed-venue extract starts can now report `venue=SIGCOMM,NSDI`, and batch logs show `segments=9-12/39` style ranges. The remaining live issues are now downstream of that fix: `accepted-papers` and `papers-info` still look partly redundant in some venue runs, low-value third-party URLs can still enter the shortlist/extract set, and the long cycle still appears vulnerable to a later timeout after extraction work has already begun.
- 2026-03-30: Investigating the resulting `workspace/alibabanew` artifacts exposed a concrete mixed-target extraction bug: the NSDI page was fully extracted and produced `SimAI`, `Learning Production-Optimized...`, `Evolution of Aegis`, and `Mitigating Scalability Walls...` in cycle-2 trace data, but those rows were later dropped because the shared `extract_intent.must_match.venue_any` still carried `SIGCOMM` from the first target. The fix is now in `src/orchestrator/agentic_extract_prepare.py` and `src/orchestrator/agentic_extract_runtime.py`: shared request filters no longer collapse mixed target venues to the first target, and each prepared extract target now carries a scoped intent derived from its own filters before the LLM call. Regression coverage now asserts both neutral mixed-target request filters and per-target `venue_any` scoping.
- 2026-03-30: A fresh live rerun on `workspace/alibabanew` after the mixed-target fix now reaches `17` extracted rows in cycle 2 and `13` retained paper candidates there, confirming the missing NSDI rows were a real regression and are now materially restored. That same run also surfaced two follow-up issues: the persisted paper contract still flattens authors and affiliations into separate loose strings and still favors `abstract_snippet` over a full abstract field, and late cycles can still waste an LLM call on `candidate_url_proposal` even when an extract pass produced no paper candidates. Both are now addressed in the owning modules: result rows carry paired `author_affiliations` plus `authors_with_affiliations`, full `abstract` text is preserved when the extractor returns it, and `src/orchestrator/agentic_extract_runtime.py` skips candidate-URL proposal entirely when there are no newly retained paper candidates to ground that proposal.
- 2026-03-31: Rechecking the rerun in `workspace/alibabanew` exposed a separate planner-loop issue: even when cycle 1 actually executed both venue searches, the matching `search_web` todos remained open, so later cycles reissued exact same queries such as `NSDI 2025 program Alibaba`. This is now fixed in `src/orchestrator/agentic_loop.py`: after a `search_web` action, exact-match executed queries are reconciled back into the plan state and the corresponding `search_web` todos are marked `done`. This keeps the fix narrow and inspectable: only exact executed-query/todo matches are auto-closed, while broader replanning still stays with the planner.
- 2026-03-31: Completed the first `E14` state-consistency slice. `src/orchestrator/agentic_loop.py` now reconciles `extract_content` todo status from actual per-page coverage after each extract action, so planner-authored todo drift does not leave incomplete SIGCOMM work marked `done` while completed NSDI work stays `doing`. In parallel, `src/orchestrator/agentic_extract_runtime.py` now treats failed pages as retryable and resets stale completion state when the extraction scope changes, instead of skipping pages forever based on URL-only state. Retrieval-focused and full `pytest` both pass (`115` retrieval tests; `166 passed, 27 subtests passed` overall).
- 2026-03-31: A fresh live rerun on `workspace/alibabanew` after that first `E14` slice confirms the exact repeated-search bug is gone, but it also exposes the next planning-quality problems clearly. Cycle 1 now expands to four venue queries, which is already broader than needed and still lets obvious junk hosts into `raw_candidates`; cycle 2 then extracts the right two listing pages, but cycle 3 prematurely pivots to low-value detail pages while both high-value venue listings are still `in_progress`. The result regresses to `9` final papers with `max_cycles_reached`, while `venue`, `doi`, and `arxiv_url` remain empty for all final rows. The next queue therefore needs to prioritize (1) tighter query/search hygiene, (2) keeping unfinished high-value listing pages ahead of detail-page exploration, (3) narrowing candidate-URL suggestions, and (4) fixing the user-facing trajectory projection, which still leaves `user_view.action` / `extract_summary` too empty to explain the run.
- 2026-03-31: Completed the next planning-quality slice after that rerun. `src/orchestrator/agentic_loop.py` now merges planner-declared search todos with explicit `search_web` queries by primary venue, so one venue does not automatically expand into multiple near-duplicate search queries (`accepted papers`, then `accepted papers program proceedings ...`) in the same cycle. In parallel, `src/orchestrator/agentic_view.py` now exposes `memory.priority_extract_urls` for unfinished or failed high-value listing pages, and `src/orchestrator/agentic_contracts.py` now tells the planner to treat those as stronger obligations than detail-page exploration. Retrieval-focused and full `pytest` both still pass (`116` retrieval tests; `167 passed, 27 subtests passed` overall). The next live rerun should confirm that cycle 1 stays on the tighter two-query search plan and that cycle 3 no longer pivots to detail pages while official listing pages remain unfinished.
- 2026-03-31: The next live rerun on `workspace/alibabanew` confirms that those two planner fixes worked: cycle 1 stays on the tight two-query venue search plan, and cycles 3-4 stay focused on unfinished listing pages instead of immediately pivoting to low-value detail pages. But the run also exposes the next dominant failure mode: extraction-quality collapse from generic planner-supplied anchor terms. The planner is now sending broad schema-ish anchors like `paper`, `title`, `doi`, `arxiv`, `source_url`, `session`, and even `2025`, which blows the ranked listing scope out to `135-178` windows and leads to mostly off-target extraction on NSDI plus an early timeout on SIGCOMM `accepted-papers`. The result regresses further to only `4` final papers, with `venue`, `doi`, and `arxiv_url` still empty for all final rows. The next slice should therefore move to stricter anchor-term hygiene and extraction-scope narrowing, not more planner/query work.
- 2026-03-31: Completed the first anchor-hygiene slice for `E15`. `src/orchestrator/agentic_text.py` now strips generic schema-ish anchor terms such as `paper`, `title`, `doi`, `arxiv`, `source_url`, `session`, bare years, and listing-page boilerplate before extract prep, then falls back only to grep-friendly structured filters (`institution`, `author`) when the planner gives only generic terms. Semantic `topic` intent is still preserved for the LLM extractor, but it is no longer reused as a pseudo-grep anchor. That sanitization now runs both at the planner-action boundary in `src/orchestrator/agentic_view.py` and again during target prep in `src/orchestrator/agentic_extract_prepare.py`, so mixed-target venue labels do not leak back into per-page grep anchors. Retrieval-focused and full `pytest` both pass after this slice (`118 passed` in `tests/test_retrieval_agentic_i1.py`; `169 passed, 27 subtests passed` overall).
- 2026-03-31: Refined that boundary further to match the intended agentic contract. `extract_content` planner params now support explicit lexical `text_filters` (`literal_any`, `regex_any`) plus optional `semantic_focus`, with legacy `anchor_terms` accepted only as a compatibility alias. The app now treats grep/regex trimming and semantic extraction guidance as separate channels: lexical trimming stays deterministic in `src/orchestrator/agentic_text.py`, while semantic focus is carried into extract intent rather than being coerced into pseudo-grep anchors. Planner sanitization in `src/orchestrator/agentic_view.py`, request prep in `src/orchestrator/agentic_extract_prepare.py`, and runtime scope tracking in `src/orchestrator/agentic_extract_runtime.py` all understand that split now. Retrieval-focused `pytest` still passes after the contract change (`119 passed` in `tests/test_retrieval_agentic_i1.py`).
- 2026-03-31: The next live `workspace/alibabanew` rerun exposed three more concrete extraction/runtime seams. First, SIGCOMM `accepted-papers` restarted from segment `1/39` in cycle 3 even though the ranked windows themselves had not changed; the reset came from planner-side wording drift (`semantic_focus`, redundant regex filters) rather than a real change in the ranked window set. Second, SIGCOMM `papers-info` still times out on a rich pass-5 batch even though the actual prompt size is only about `1.8k` estimated input tokens; the runtime needs adaptive smaller-batch retry instead of treating that as an unrecoverable page failure. Third, candidate-URL proposal is still polluted by collection bias: the current collector fills the whole `32`-link budget from the first rich page in DOM order, so the LLM mostly sees NSDI site-navigation links instead of a balanced set of next-page complements across the extracted venue pages.
- 2026-03-31: Completed the next extraction/runtime slice against those seams. `src/orchestrator/agentic_extract_runtime.py` now keys extract continuation off the ranked window set itself instead of planner-side `semantic_focus` drift, so equivalent re-plans keep progressing instead of restarting from segment `1`. That same runtime now retries timeout batches with a smaller batch size before marking a page failed, which should reduce repeated `papers-info`-style timeouts without widening the general call budget. In parallel, `src/orchestrator/agentic_extract_candidates.py` now collects candidate URLs across all fetched pages, filters out generic navigation noise, and round-robins the capped payload across sources before the LLM proposal step, while `src/orchestrator/agentic_extract_llm.py` now explicitly tells the LLM to prefer direct next-page complements over home/schedule/login pages.
- 2026-03-31: Applied the next simplification slice to keep the app more agentic and less heuristic-heavy. `src/orchestrator/agentic_view.py` now exposes `page_role` and `page_family` in planner memory and builds `memory.priority_extract_urls` as one representative per venue family, which keeps home/proceedings pages from crowding out the real listing/page companions. In parallel, `src/orchestrator/agentic_actions.py` / `src/orchestrator/agentic_extract_runtime.py` now disable default `candidate_url_proposal` during the main shortlisted-page extraction loop, and `src/orchestrator/agentic_extract_prepare.py` now drops PDF URLs before extract prep so the active path stays HTML-first. Retrieval-focused and full `pytest` both pass after this slice (`10` targeted tests; `176 passed, 27 subtests passed` overall).
- 2026-03-31: The next live `workspace/alibabanew` rerun validated part of that simplification and exposed the next memory bug clearly. The good part: no `candidate_url_proposal` stage appears anymore, no PDFs are extracted, `accepted-papers` and `technical-sessions` stay the main active HTML sources, and adaptive batch retry still works on NSDI. The remaining problem is planner memory quality: `paper.lingyunyang.com/reading-notes/conference/nsdi-2025` was still surfaced as an independent `listing` family and then consumed two later extract cycles even though the official `technical-sessions` page was already in memory and later completed. The final result dropped to `15` rows, with an apparent duplicate/paraphrase seam around `Evolution of Aegis` / `Aegis: A Fault Diagnosis System for AI Model Training Cloud`, and all `15` rows still lack `venue`, `doi`, and `arxiv_url`.
- 2026-03-31: Tightened that memory seam directly in `src/orchestrator/agentic_view.py`. Generic `page_role=listing` mirrors are now suppressed from `memory.known_urls` and `memory.priority_extract_urls` when the same search query already surfaced an `accepted` or `program` page, so third-party listing mirrors do not compete with official venue pages for planner attention. Retrieval-focused and full `pytest` still pass after this change (`4` targeted tests; `178 passed, 27 subtests passed` overall).
- 2026-03-31: Reviewing the broader multi-year query `papers by alibaba at SIGCOMM from 2020 to 2022` exposed a more general planner-vs-memory split that should be fixed before changing budgets. Against the fetched `SIGCOMM 2020` program HTML, extraction is actually in decent shape: the page exposes five Alibaba-affiliated papers, and the final result recovers all five from that fetched page, with the remaining defects mostly in author/title fidelity rather than page-level recall. The run-level failure is elsewhere: the planner underused the cycle budget by only searching `2020`, then `2021`, and never reaching `2022` before `max_cycles_reached`; after the `2021` search, planner memory also let `HotNets 2021 accepted` masquerade as the next best `SIGCOMM 2021` extract target because it was a same-host `accepted` page. Record this as the next general-quality checkpoint: fix multi-year coverage planning and same-host sibling-venue memory first, do not paper over the issue by simply raising `max_cycles`, and broaden validation with more diverse queries before more code changes.
- 2026-03-31: The `papers by Zhai Ennan at SIGCOMM 2023` run exposed a distinct author-query failure mode on accepted-list pages. The good part: search and shortlist behavior are fine here, and the correct official listing page is fetched immediately. The bad part is in extract semantics and loop follow-through. The fetched HTML contains `CellFusion` and `XRON` as the real Ennan Zhai papers, but the segmented extract batches split paper titles from their author lines, so the LLM mispaired the `CellFusion` author block with the neighboring `ChameleMon` title and the `XRON` author block with the neighboring `ZGaming` title. That leaves the final result with `ChameleMon` and `ZGaming` instead of the two ground-truth papers. After that first wrong extraction, cycles 3-5 are mostly no-op replays of the same completed page, because the planner keeps asking for more detail extraction even though runtime marks the page `completed` and performs `0` LLM calls. Record this as a general accepted-list issue: preserve local title+author block boundaries for author queries, and do not let the planner spend cycles on completed pages when no new extraction scope exists.
- 2026-03-31: The topic query `papers related to congestion control at SIGCOMM 2025` exposed a complementary topic-query failure mode. The official `accepted-papers` page was extracted three times, but each pass used a different lexical/semantic filter pack, so the runtime kept re-ranking and effectively restarting the same page while the CLI trace only showed repeated segment batches. The final result kept `CClinguist` and `LeoCC`, missed `Falcon`, and included `ByteDance Jakiro` after the planner fed already surfaced paper titles back as literal filters in a later pass. The fetched `papers-info` HTML clearly contains `Falcon` with abstract evidence about delay-based congestion control, so this should be treated as a general issue: for topic queries, avoid planner overfitting from previously surfaced titles, surface filter/continuation deltas in the trace, and keep repeated same-page extraction intelligible as continuation versus restart.
- 2026-03-31: The broader topic query `papers on AI infra for LLM training and inference in SIGCOMM 2025` confirms that the same-page topic-query problem is not limited to congestion-control wording. The fetched `accepted-papers` HTML already contains the user's hand-built ground truth (`MixNet`, `InfiniteHBD`, `DistTrain`, `MegaScale-Infer`, `Astral`, `ByteScale`, `HACK`) and also plausible borderline neighbors such as `SCX`, yet the loop never leaves that page family or converges on the later rows. Instead it re-extracts `accepted-papers` four times under different filter packs, with ranked-window counts drifting from `151` to `51` to `66` and back to `151`, and finishes with only `MixNet`, `InfiniteHBD`, and `DistTrain` plus false positives such as `Hummingbird` and `Revisiting RDMA Reliability for Lossy Fabrics`. Record this as a general topic-query failure mode: repeated same-page scope resets are burning cycle budget, broad AI/LLM lexical filters are over-selecting early windows, and planner-fed surfaced titles/regexes are self-poisoning later passes instead of letting the extractor continue deeper into the known-relevant official page.

### 3.2.7 Next Big Stage

Next stage focus: restore debuggability by shrinking the remaining giant agentic modules before reopening fetch-store/cache work or adding more planner behavior.

Why this comes next:
- the loop engine now lives in `src/orchestrator/agentic_loop.py`, and `src/orchestrator/agentic.py` is finally small enough to reason about, so the next leverage is no longer line-count triage there
- `src/orchestrator/agentic_extract.py` is now a small LLM-helper seam, and `src/orchestrator/agentic_extract_runtime.py` owns the remaining extract runtime; future refactors should target that runtime module only if replay/debugging shows its ownership is still too broad
- if the next slices only add behavior, or only move code behind compatibility shims, troubleshooting will drift back toward the previous terrible state
- the remaining replay work should be used to trim or justify policy, not to keep expanding fallback heuristics
- fetch-store/cache work will be easier to design after the page-result contract and follow-up URL flow are smaller and clearer

Stage goals:
1. keep extraction centered on a per-page contract: `papers[]`, `candidate_urls[]`, and `page_status`
2. keep candidate URL discovery lightweight: deterministic code should collect/normalize candidate links, while the LLM chooses which ones are complementary
3. keep planner ownership clear: discovered URLs should remain suggestions until the main agent decides whether to fetch/extract them
4. make `agentic.py` a true coordinator shell by burning down the remaining compatibility-heavy helper surface now that the loop engine lives elsewhere
5. keep extraction-contract, request-prep, runtime, and candidate-shaping ownership separate; only split the runtime module further if it remains a real troubleshooting bottleneck
6. only reopen replay-backed extraction corrections if new evidence shows a remaining precision gap
7. keep Q4 fetch-store/cache design deferred until the page-result contract and follow-up URL flow are stable enough to design against confidently

Next session checklist:
- before adding extraction logic, ask whether it improves `papers[]`, `candidate_urls[]`, or `page_status`
- do not add local URL classification/ranking heuristics unless replay evidence clearly justifies them
- if candidate URL discovery is hard, move more of the decision into the stateless LLM prompt instead of inventing new code rules
- keep discovered URLs planner-facing as suggestions, not auto-adopted known URLs
- each refactor slice must meaningfully shrink one giant file or remove one compatibility facade; avoid “module extraction” that leaves the same bulk mirrored in `agentic.py`
- prefer boundaries that can be replay-tested in isolation over broad churn across multiple modules at once
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
- `E7 -> H1, H3, H6, H10`: extract the loop engine out of `agentic.py`
  - done: move cycle-start/run/finalize/decision helpers into `src/orchestrator/agentic_loop.py`
  - done: keep `agentic.py` as entrypoint and wiring shell instead of the place where the state machine lives
  - next: reduce the remaining compatibility/wrapper surface now that the loop engine no longer lives there
- `E8 -> H1, H5, H6, H10`: split `agentic_extract.py` by ownership, not by convenience
  - done: separate the stateless extract contract/prompt/schema and LLM exchange helpers into `src/orchestrator/agentic_extract_llm.py`
  - done: separate candidate shaping / canonicalization into `src/orchestrator/agentic_extract_candidates.py`
  - done: extraction contract bugs, candidate-shaping bugs, and extraction runtime bugs can now be debugged in different modules
  - done: split extract request preparation / target preparation into `src/orchestrator/agentic_extract_prepare.py`
  - done: move per-target execution / result-shaping into `src/orchestrator/agentic_extract_runtime.py` so the action entrypoint no longer mixes LLM helper exports with runtime loop mechanics
  - next: only split `src/orchestrator/agentic_extract_runtime.py` further if replay/debugging shows a concrete mixed-ownership bottleneck inside that runtime module
- `E9 -> H1, H6, H10`: burn down temporary compatibility wrappers after each boundary move
  - done: remove the candidate-only forwarding helpers from `src/orchestrator/agentic.py` once callers/tests moved to the extracted modules
  - done: remove the target-normalization and extract-budget forwarding helpers from `src/orchestrator/agentic.py`
  - done: move search/fetch/extract action execution into `src/orchestrator/agentic_actions.py` and retarget runtime tests to that boundary
  - done: delete the dead cycle-local candidate canonicalization path that no longer ran in the live runtime
  - done: move low-level LLM/fetch/extract helper tests to their owning modules and remove the corresponding test-only wrappers from `src/orchestrator/agentic.py`
  - done: move `run_extract_agentic_local` into its own module and stop using `src/orchestrator/agentic.py` as the saved-fetch local extract entrypoint
  - done: retarget the remaining accidental helper tests to owning modules and remove those imports from `src/orchestrator/agentic.py`
  - done: remove the pure candidate/LLM pass-through exports from `src/orchestrator/agentic_extract.py` once tests and callers were already on the owning modules
  - done: move extract request/intent/target preparation out of `src/orchestrator/agentic_extract.py`
  - done: move extract action runtime ownership from `src/orchestrator/agentic_extract.py` into `src/orchestrator/agentic_extract_runtime.py`, and point the action layer at that owner directly
  - next: if more wrapper cleanup is needed, keep it inside `src/orchestrator/agentic_extract_runtime.py` or `src/orchestrator/agentic_actions.py` rather than rebuilding facade layers in `src/orchestrator/agentic_extract.py`
  - success check: wrapper count and line count both decrease, not just file count
- `E10 -> H9, H10`: require replay/targeted validation after each refactor slice that moved ownership
  - each slice should leave behind focused tests for the new boundary before the next move
  - do not batch multiple ownership moves together if it makes regressions harder to localize
  - done: add `replay-agentic-extract` so saved trajectory + `fetch_raw/` can be replayed through the current extract action and, when needed, narrowed to saved LLM request segments
  - done: use the replay artifact on representative `google` and `alibaba` saved workspaces; both showed timeout-dominated LLM extraction behavior rather than new precision bugs
  - done: reduce replay/extract payload size by using current cleaned segments for probes and hard-capping per-call extract batches in the runtime
  - next: use the now-stable replay artifacts to trim duplicate/over-broad matches before adding any new extraction policy
  - next: split this into two separate follow-ups:
    - `E11`: precision cleanup on extracted papers only
      - done: remove `decision_reason` from fallback evidence matching so uncertain rows do not pass institution/author filters just because the reason text repeats the filter term
      - keep `llm_extract.match_decision == match` as the only case where the LLM's explicit semantic judgment short-circuits fallback matching
      - done: add replay-backed tests for current Google false positives such as the `Discovering Millions...` and `NIER ... Solution` rows
      - done: add a narrow LLM dedup pass for small same-title-family clusters and use it to collapse replay-backed near-duplicates such as the `Firefly` variants
      - keep this LLM-owned; do not reopen broad local title heuristics unless new replay evidence forces it
    - `E12`: prompt reuse / batching efficiency after precision stabilizes
      - do not reopen large per-call batches blindly, but also do not keep arbitrary caps that clearly cut off useful venue-page coverage
      - revisit the current `6` LLM-call per-target limit and listing-page `4`-segment batch cap with replay/live evidence; if they are harming recall more than helping latency, relax or remove them
      - prefer simpler runtime controls such as scope-aware continuation, contiguous-hit grouping, or adaptive continuation over more hand-tuned heuristics
      - latest `workspace/alibabanew` run shows how harmful the current combination can be on large venue pages: NSDI `technical-sessions` widened to `143` ranked segments, yet extraction still stopped at `24/143`; fix the upstream ranking/anchor breadth before deciding whether to raise the call cap
      - latest rerun makes this even clearer: `accepted-papers` timed out by pass 2 and NSDI widened to `178` ranked segments under generic anchors; narrow the scope first, then revisit call-cap tuning
      - keep this separate from precision so bad matches are not hidden behind efficiency tuning
    - `E13`: end-to-end venue companion-page discovery before more efficiency tuning
      - done: make planner memory expose pending todo targets (`memory.next_todos`) and align `search_web` action queries with planner-declared search todos so explicit multi-venue search plans do not silently collapse to one venue
      - done: fix mixed-target extract intent scoping so cycle-level `extract_content` actions do not reuse one venue's strict `must_match` constraints for another venue page
      - next: rerun live end-to-end venue queries (`google*`, `alibaba*`) to confirm SIGCOMM + NSDI both get searched before extraction
      - done: tighten search query expansion so explicit `search_web` queries stay one-per-venue unless a later cycle truly needs a broader variant
      - done: rerunning `workspace/alibabanew` confirms cycle 1 stays on the tighter venue-query set rather than auto-expanding into four overlapping queries
      - search+shortlist should not strand key venue companion pages such as SIGCOMM `accepted-papers` / `papers-info` when one generic program page was already found
      - prefer a simple, inspectable path such as companion-page expansion from trusted venue program roots or small planner-visible venue-page suggestions; do not hide this behind more opaque ranking heuristics
      - next: trim obviously low-value third-party venue-adjacent URLs (for example LinkedIn promo posts) without collapsing official companion venue pages into one opaque representative
      - done: make the late candidate-URL proposal path best-effort instead of fatal; extracted papers and coverage should survive even if URL suggestion times out
      - done: shrink planner-visible `known_urls` memory and candidate-URL proposal payloads to more bounded, inspectable shapes (more URL slots for the planner, smaller candidate-URL payload caps for the LLM)
      - next: rerun `workspace/alibabanew` after the above changes and confirm (a) repeated `search_web` queries stay gone, (b) official SIGCOMM pages stay visible in planner memory, and (c) a late candidate-URL timeout can no longer erase successful extraction work
      - next: trim obviously low-value third-party venue-adjacent URLs (for example LinkedIn promo posts) without collapsing official companion venue pages into one opaque representative
      - next: simplify the final paper contract by dropping `authors_with_affiliations` and `abstract_snippet`; keep the structured `author_affiliations` field plus `abstract`
      - next: metadata enrichment is optional per field, but not having `doi`, `arxiv_url`, and `venue` for all rows is now a quality gap; add a narrow follow-up pass so at least some of those fields are recovered when the source page clearly exposes them
      - next: rerun `workspace/alibabanew` after exact-match search-todo reconciliation and confirm repeated queries such as `NSDI 2025 program Alibaba` no longer reappear in later cycles unless the planner emits a genuinely new variant
      - use the improved CLI progress plus `agentic_trajectory.yaml` to confirm the selected extract URL set is now intelligible during live runs
    - `E14`: canonical loop-state reconciliation before more extraction feature work
      - top priority: fix extract-state / todo-state drift so planner memory, todo status, result coverage, and retry decisions all reconcile from the same canonical per-page state
      - planner-vs-memory note: the latest failures are now more about app-side memory/status design than raw planner capability; when the planner sees low-quality mirrors or missing field-gap signals as first-class options, it will spend cycles there
      - done: reconcile `extract_content` todo status from actual per-page coverage after each extract action, so loop-owned state can reopen incomplete work and close completed work even when the planner's own todo update is wrong
      - done: make extract page state scope-aware and retryable: completed pages only skip when the extraction scope is unchanged, and previously failed pages can be retried instead of being skipped forever
      - latest `workspace/alibabanew` run is the reference failure: SIGCOMM pages remain incomplete/failed, but later cycles still drift onto already completed NSDI work
      - do not let planner-authored todo updates alone decide completion; loop/runtime state must be able to close or reopen extract todos from actual per-page coverage and failure state
      - fix URL-only completion assumptions; extraction state may need to account for scope changes that alter ranked segment sets for the same URL
      - done: rerunning `workspace/alibabanew` confirms cycle 3 no longer wastes turns on already completed NSDI listing extraction
      - next: unfinished high-value listing pages must stay ahead of lower-value detail pages; the latest run still pivoted to `dl.acm` / presentation detail pages while both official venue listings were `in_progress`
      - done: reflect listing-priority state directly in planner memory via `memory.priority_extract_urls`, instead of assuming the planner will infer it from compact `known_urls` alone
      - done: rerunning `workspace/alibabanew` confirms cycle 3 keeps unfinished official listing pages ahead of `dl.acm` / presentation detail-page pivots
      - next: avoid gratuitous scope-reset churn; the latest SIGCOMM listing rerun restarted from segment `1/39` in cycle 4 because anchor-term drift changed the scope fingerprint, which is safer than stale completion reuse but still too expensive for minor planner wording changes
      - done: make extract continuation depend on the ranked window set rather than planner-side wording drift, so semantically equivalent re-plans keep their progress instead of restarting from segment `1`
      - done: the latest `workspace/alibabanew` run validates that fix directly; `accepted-papers` resumes at `25-39/39` in cycle 3 instead of restarting from `1-24`
      - next: tighten page-priority memory so low-yield venue homepages such as `https://www.usenix.org/conference/nsdi25` do not become `priority_extract_urls` once the real listing page (`technical-sessions`) has already been completed
      - next: keep obviously dead pages such as `dl.acm` proceedings URLs with `segment_filter 0/0` out of later extract plans instead of re-presenting them as live extraction options
      - done: handle venue page families earlier in planner memory: `memory.known_urls` now exposes `page_role` / `page_family`, and `memory.priority_extract_urls` now selects one high-value listing representative per family instead of surfacing home/proceedings pages alongside the real companion listing
      - done: suppress generic third-party listing mirrors when the same search query already has an official `accepted` or `program` page, so planner memory does not present both as equal candidates
      - next: add a stronger source-quality / officialness signal to planner memory so official venue pages outrank mirrors and social/news/blog pages even when they look structurally similar
      - next: make multi-year/range coverage explicit in planner memory and todo state, so uncovered years remain first-class obligations instead of being starved by repeated extraction or same-year refinement
      - next: keep same-host sibling venues/families from satisfying the wrong venue goal in memory (for example `HotNets 2021 accepted` should not become the best `SIGCOMM 2021` follow-up just because it is an `accepted` page on `conferences.sigcomm.org`)
      - next: stop planner no-op loops on completed listing pages; if runtime says a page is already `completed` for the current scope and there are no priority URLs or open search obligations, planner memory should not keep re-proposing the same extract action
      - next: for topic-led queries on the same listing page, make planner memory expose whether a repeated extract is true continuation versus a new narrowed scope; the latest `workspace/congestion` run re-extracted `accepted-papers` three times under drifting filter packs that the CLI trace did not make visible
      - next: for topic-led queries where one official accepted/program page already contains most or all plausible answer rows, keep extraction on that page as true continuation until coverage is materially exhausted; the latest `workspace/aiinfra` run spent all five cycles on repeated same-page scope resets (`151 -> 51 -> 66 -> 151` ranked windows) instead of converging on later relevant rows already present on the fetched accepted page
      - defer: expose missing-field pressure per page family (for example `papers found but venue/doi/abstract still missing`) until after the higher-value coverage and venue-family fixes; title/author recovery is already good enough to postpone this
      - stop wasting cycles on URLs that runtime will immediately skip, and make the user-facing trajectory say explicitly why a page was skipped or retried
    - `E15`: remove or relax heuristics that are harming extraction quality
      - top priority: trim false-negative title filters in `src/orchestrator/agentic_extract_candidates.py` (for example the current short-title / `cloud`-ish rejection path that can drop valid papers such as the Alibaba congestion-control row)
      - prefer explicit LLM judgment or simple evidence-preserving cleanup over bespoke reject rules when the local rule is causing recall loss
      - audit remaining candidate-shaping heuristics with replay artifacts and remove any that lower recall without clearly improving precision
      - done: move anchor-term hygiene into the app boundary; planner-supplied schema words such as `paper`, `title`, `doi`, `arxiv`, `source_url`, `session`, and bare years are now dropped before extract prep
      - done: keep listing-page lexical filters concrete and query-grounded by falling back only to grep-friendly structured filters (`institution`, `author`) when the planner only supplied generic extraction-field names
      - done: expose the extractor boundary more explicitly to the planner: use `text_filters.literal_any` / `text_filters.regex_any` for lexical trimming and `semantic_focus` for purely semantic extraction guidance; keep `anchor_terms` only as a compatibility alias
      - done: rerunning `workspace/alibabanew` confirms the broad ranking regression is gone for the first extraction pass; cycle 2 is back to `39/176` for `accepted-papers` and `16/319` for `technical-sessions` instead of the earlier `135-178` window blow-up
      - next: trim overly broad lexical filters / ranking hints that are still coming from page boilerplate or residual venue labels on listing pages
      - next: preserve local paper-block structure on accepted/program listing pages for author queries, so a title line is paired with its own adjacent author line before the LLM call instead of being misattached to the neighboring paper title
      - next: on topic queries, avoid planner overfitting by feeding already surfaced paper titles back as literal filters unless they have been explicitly validated as in-scope; the latest `workspace/congestion` run promoted `ByteDance Jakiro` into a later literal filter pack and then kept it while still missing `Falcon`, which only became clearly in-scope through abstract-level evidence on `papers-info`
      - next: on broader topic-led venue queries, stop letting generic domain words plus a few surfaced paper titles explode ranking scope or self-poison later passes; the latest `workspace/aiinfra` run selected `151/176` accepted-page windows on the first pass, then later filter packs still surfaced `Hummingbird` and `Revisiting RDMA` while missing ground-truth rows already present lower on the same accepted page (`MegaScale-Infer`, `Astral`, `ByteScale`, `HACK`)
      - next: revisit the fixed `DEFAULT_EXTRACT_MAX_CALLS = 6` only after the ranked-window count is back under control; do not hide scope problems by simply raising the call cap first
      - done: make timeout handling adaptive before raising the global call cap; a timeout on one rich batch now retries with a smaller batch instead of failing the whole page immediately
      - done: the latest `workspace/alibabanew` run validates the adaptive retry path directly on `papers-info`; the timed-out `17-20/44` batch was retried as `17-18/44` and extraction continued
      - do not add new hardcoded venue/institution rules while fixing this
    - `E16`: narrow candidate-URL proposal to true next-page complements
      - make candidate-URL proposal page-local and narrow by default: detail pages, proceedings/program subpages, PDFs, and clearly relevant author pages
      - avoid broad page-wide harvesting from large venue pages; if needed, let the LLM help prune a small prefiltered link set instead of encoding more local ranking heuristics
      - keep candidate URLs as planner suggestions only; planner adoption stays explicit
      - low-value third-party and boilerplate links should fall out from the narrower contract, not from a large growing denylist
      - latest `workspace/alibabanew` suggestions are still too broad: conference homepages, schedule pages, BibTeX exports, and unrelated presentation pages are crowding out higher-value companion pages
      - done: stop filling the capped candidate-link bucket from the first page in DOM order; candidate-link collection now balances across source pages and filters generic site navigation before the LLM proposal step
      - done: the latest `workspace/alibabanew` cycle-2 proposal is materially narrower (`3` URLs: one SimAI presentation page plus the two NSDI proceedings PDFs) instead of the earlier broad homepage/schedule-heavy set
      - done: drop candidate-URL proposal during the main shortlisted-page extraction loop for now; it is adding complexity, late-cycle failures, and redundant-page churn without enough yield on the current HTML-first venue tasks
      - if candidate-URL proposal comes back later, make it an explicit fallback for metadata recovery or no-good-HTML cases, not a default step on every listing-page extraction pass
      - next: bias the proposal contract toward same-paper or same-session complements first, and only suggest generic conference pages when they expose metadata the current page clearly lacks
      - next: second-stage candidate-URL proposals are still too broad on weaker pages; cycle 4 and cycle 5 still hit `candidate_url_failed`, so the proposal input should exclude venue homepages and low-signal policy/contact/accessibility links before the LLM step
    - `E17`: improve human-facing trajectory readability without losing raw trace detail
      - keep raw trace rich and machine-oriented, but make CLI / trajectory projection explain actions in human terms: why this page, why skipped, why retry, what changed
      - next: when the same URL is extracted again, surface the effective filter / semantic-focus delta and whether the pass is continuation or restart; the current CLI makes repeated same-page extraction look like opaque loops
      - consider using the planner LLM to phrase concise human-readable progress notes, but keep the underlying raw event data complete and authoritative
      - make extracted-vs-dropped rows visible in the user-facing trajectory when candidate shaping removes a real extracted row
      - result status should not read as fully completed when the stop reason is `max_cycles_reached` and important pages are still failed/in-progress
      - fix the current projection gaps: `user_view.action` is still empty and `user_view.extract_summary` is often missing even when the raw trace contains `action_debug.targets`, `candidate_urls`, and `url_checks`
    - `E18`: metadata enrichment and result-contract cleanup after state consistency is fixed
      - planner-vs-memory note: `venue` / `doi` / `arxiv_url` being empty is not mainly a planner failure; the planner currently lacks a canonical memory signal that says which missing fields justify another companion-page extraction turn
      - remove `authors_with_affiliations` and `abstract_snippet` from the final result contract after dependent readers/tests are updated
      - keep `author_affiliations` as the primary author/affiliation export field
      - prefer full abstract text when available; on listing pages, do not pretend title+author lines are full abstracts
      - add a narrow enrichment step so venue/DOI/arXiv are recovered when clearly present on companion/detail pages
      - latest `workspace/alibabanew` now reaches `17` final rows, but `venue`, `doi`, and `arxiv_url` are still empty for all `17`; abstract presence is `17/17`, yet several rows are still title/author listing text rather than a real abstract
      - latest rerun after the HTML-first simplification still shows the same metadata gap at smaller scale: `15/15` final rows have empty `venue`, `doi`, and `arxiv_url`, and several retained rows still carry listing-text pseudo-abstracts rather than true abstracts
    - `E19`: de-scope PDF extraction unless a concrete replay case demands it
      - done: drop PDF URLs from extract target normalization so the active agentic path stays HTML-first
      - do not spend active refactor effort on PDF extraction right now; the current failures and duplication are centered on HTML venue pages and planner page choice
      - keep PDF extraction as a guarded fallback only for cases where there is no good HTML venue/listing page or when a concrete saved artifact shows it is the only source of needed metadata
      - latest `workspace/alibabanew` run makes this more urgent: final recall regressed further to `4` papers and all `venue` / `doi` / `arxiv_url` fields are still empty
    - `E19`: keep PDF extraction out of the active queue unless new evidence justifies it
      - do not spend significant time on PDF extraction or PDF-specific heuristics right now
      - only reopen PDF work once we have replay/live cases where HTML venue pages are insufficient and the missing value is concrete

## 4) Non-Active Modules (Summary Only)

| Module | Next Gate To Open Detailed Board |
|---|---|
| Data Backend and RAG | Open after agentic extraction artifacts and metadata schema stabilize. |
| Paper Context Retrieval | Open after agentic final candidates are consistently stable. |
| Paper Context Formatted Extraction | Open after context retrieval schema is stable. |
| Analysis Skill | Open after extraction and context artifacts are versioned and stable. |
| Output Skills | Open after analysis outputs are stable for rendering. |
| UI/TUI Design | Open after trajectory and result contracts are stable. |
