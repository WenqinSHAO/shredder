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
- planner memory and working-state reconstruction still under-represent important query intent dimensions such as `latest`, year ranges, and whether the query is mainly author-, institution-, venue-, or topic-led
- the broader query set now shows that the main system bottleneck is no longer module size alone; it is the quality of the planner-state interface and how canonical state is projected into planner-visible obligations
- the extractor still lacks a first-class page result shape such as `papers[]`, `candidate_urls[]`, and `page_status`
- fallback extraction policy is still heavier than the naive goal and should only grow through narrow replay-backed corrections
- extract coverage / todo / planner memory are still not fully consistent; the latest `workspace/alibabanew` run shows completed-vs-in-progress drift, wasted retries on already completed URLs, and planner-visible todo state that does not match actual per-page coverage
- a few local heuristics still actively hurt extraction quality; removing or relaxing those is higher priority than adding new scoring logic
- the current candidate-URL proposal is still too broad on large venue pages; it should move toward a narrower "next page-ish" follow-up contract rather than wider harvesting
- user-facing trace readability is still insufficient for debugging agentic runs quickly; the raw trace is rich enough, but the human-facing projection is not yet carrying the right explanations
- PDF extraction should stay de-prioritized until we have real replay/live cases that force it; current leverage is on HTML venue pages and loop state consistency
- validation is still biased toward venue+institution retrieval; author queries, topic-led queries, multi-year range queries, and `latest` cross-venue semantic queries now expose distinct failure classes that need first-class regression coverage
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
- `E*` items in `3.2.10` are the current next-stage execution slices after the older `Q*` queue

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

### 3.2.6 Historical Progress Log

Use this section as implementation history only.
If you are picking up active work, start with `3.2.7 Current Focus`, then `3.2.8 Active Priority Stack`, then `3.2.10 Active Next Slices`.

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
- 2026-03-31: The cross-venue semantic query `latest paper on agentic memory from top AI conferences such as ICLR, ICML, AAAI, etc.` exposed a different but related failure class. Search did surface stronger candidates early (`A-MEM`, `Agent Workflow Memory`, `MemoryAgentBench`, ICLR 2026 workshop context), but the planner still decomposed the task into only `ICLR accepted papers agentic memory`, `ICML accepted papers agentic memory`, and `AAAI accepted papers agentic memory`, with no explicit recency-aware search, no `latest`/`2026` planning, and no broader semantic variants. The loop then over-invested in the wrong page types: a giant ICLR 2025 listing page, DBLP AAAI 2025, an arXiv PDF, and an OpenReview PDF, while never converging on the strongest paper-detail hits already visible in search. The final retained row was the unrelated AAAI 2025 paper `HiCM²`, which shows that broad lexical pairs such as `agentic` + `memory` on giant bibliographies are still far too weak for semantic-topic queries. Record this as a general semantic-search issue: planner search/query generation must treat `latest` and cross-venue topical queries as recency-aware semantic tasks, planner memory must distinguish high-value paper-detail pages from broad listing/bibliography/PDF pages, and extraction should not spend large budgets on bibliography-style pages when the lexical evidence is this weak.
- 2026-03-31: Completed a small planner-memory slice against that `workspace/mem` failure class. `src/orchestrator/agentic_view.py` now exposes `memory.query_profile` (including `latest` / year-range cues) plus `memory.priority_direct_hits` for strong paper-detail hits already surfaced in search, and `src/orchestrator/agentic_contracts.py` now tells the planner to treat those direct hits as first-class options on latest/semantic queries instead of defaulting immediately to giant listings, bibliographies, or PDFs. As part of the same boundary, `src/orchestrator/agentic_text.py` now recognizes common poster/forum paper-detail URLs as detail pages for planner memory. Retrieval-focused tests now cover the new memory shape and pass (`8` focused tests; `128 passed` in `tests/test_retrieval_agentic_i1.py`).

### 3.2.7 Current Focus

Current stage focus: make the system robust across query archetypes by improving planner memory, query decomposition, and query-mode-aware extraction before spending more effort on budgets or new heuristics.

Why this is the active stage:
- the code boundaries are now mostly serviceable; the main remaining failures come from planner-state quality, not one giant mixed-purpose file
- the broader query set now exposes distinct failure classes: venue+institution, venue+author, venue+topic, multi-year/range, and `latest` cross-venue semantic search
- raising `max_cycles`, widening search fanout, or adding local heuristics would mostly hide those state-interface problems instead of fixing them
- the highest-leverage work now is in planner memory, canonical state, query-mode-aware extraction, and trajectory readability

Working rules for the next slices:
- before adding heuristics, identify whether the issue belongs to planner query generation, planner memory, extract runtime, candidate shaping, or trace projection
- keep HTML-first and official-first behavior as the default, including for broad semantic queries
- when strong direct paper hits are already visible in search, do not let giant listings, bibliographies, or PDFs become the only first-class options
- do not raise `max_cycles` or broaden search fanout to hide decomposition or memory defects
- prefer replay/fixture-backed tests for new failure classes before another round of live tuning

### 3.2.8 Active Priority Stack

**Replanned 2026-04-02 based on third-party code review**

The review identified that state consistency is foundational and must be fixed before planner memory improvements will be effective. Regression tests should be added in parallel to prevent regressions.

Use this section first when deciding what to implement next.

| Priority | Theme | Main owner | What remains | Supporting observations |
|---|---|---|---|---|
| `P0` | **State consistency foundation** | `agentic_extract_runtime`, `agentic_state_apply`, `agentic_loop` | Fix direct state mutation bypass, simplify todo-URL matching, add state validators, stop no-op loops. Without this, all other fixes will be unreliable. | Coverage/todo drift in `workspace/alibabanew`, no-op cycles in `workspace/ennan`, wasted retries across all workspaces |
| `P0` (parallel) | **Regression test matrix** | `tests/` | Add archetype-specific replay tests NOW for already-identified failure modes, not after more fixes. Prevent regressions while fixing root causes. | All query archetypes: author, topic narrow, topic broad, multi-year, latest semantic |
| `P1` | **Query archetype detection** | `agentic_view`, `agentic_contracts`, `agentic_text` | Classify queries into archetypes (author/venue/topic/semantic/multi-year/latest) and use to adjust segmentation, memory priorities, and extraction budgets. | Wrong segmentation for `workspace/ennan`, missing latest signal for `workspace/mem`, multi-year planning failure |
| `P1` | **Planner memory completeness** | `agentic_view`, `agentic_contracts` | Add missing dimensions: is_latest, year_range_progress, archetype, page quality scores, page type distribution. Strengthen direct hit prioritization. | `workspace/mem` direct hits ignored, `workspace/alibabanew` year range not tracked |
| `P1` | **Extraction scope management** | `agentic_extract_runtime`, `agentic_view` | Fix same-page restart vs continuation by including filter pack in scope signature. Prevent surfaced-title self-poisoning. Track filter history. | `workspace/congestion` Falcon missed, `workspace/aiinfra` repeated scope resets |
| `P2` | **Extraction quality by archetype** | `agentic_text`, `agentic_extract_candidates` | Archetype-specific extraction: block-preserving segmentation for author queries, semantic similarity for topic queries, abstract quality validation. | `workspace/ennan` wrong papers, `workspace/aiinfra` broad filter over-matching |
| `P2` | **Page quality scoring and budget** | `agentic_view`, `agentic_extract_runtime` | Score pages by quality (official vs mirror, rich vs sparse). Add budget tiers per page type. Prioritize high-quality sources. | Third-party mirrors in `workspace/alibabanew`, giant pages consuming budget in `workspace/mem` |
| `P3` | **Human-facing trace** | `cli`, `agentic_trace`, `agentic_loop` | Make repeated same-URL extraction, skip/retry reasons, and extracted-vs-dropped transitions visible in CLI and trajectory output. | `workspace/congestion`, `workspace/aiinfra`, `workspace/mem` |
| `P4` | **Metadata/result cleanup** | `agentic_result`, enrichment path | Recover some `venue` / `doi` / `arxiv_url`, remove obsolete result fields, and stop listing-text pseudo-abstracts from looking complete. | `workspace/alibabanew` |

### 3.2.9 Supporting Evidence By Query Archetype

- `Venue + institution`
  - `workspace/alibabanew` is the main reference. Search/query duplication is better, but memory/state quality still determines whether the loop stays on official listing pages, avoids mirrors, and retains extracted results consistently.
- `Venue + author`
  - `workspace/ennan` shows the accepted-list segmentation issue clearly. Search/shortlist are fine, but title and author lines are split across neighboring paper blocks, leading to confident wrong matches.
- `Venue + topic` narrow
  - `workspace/congestion` shows the `Falcon` failure mode. The same official page is re-extracted under drifting filter packs, and abstract-rich evidence arrives too late or is outweighed by surfaced-title lexical feedback.
- `Venue + topic` broad
  - `workspace/aiinfra` shows that one accepted page can already contain most of the answer set, yet repeated scope resets and surfaced-title filters still stop the loop from reaching later relevant rows.
- `Multi-year / range`
  - `papers by alibaba at SIGCOMM from 2020 to 2022` shows that the planner underuses cycle budget and that same-host sibling venues can wrongly satisfy the next year slot in memory.
- `Latest / cross-venue semantic`
  - `workspace/mem` shows that search can already surface strong direct paper hits (`A-MEM`, `Agent Workflow Memory`, `MemoryAgentBench`), but planner memory does not yet preserve or prioritize them strongly enough against giant listings, bibliographies, and PDFs.

### 3.2.10 Active Next Slices

**Replanned 2026-04-02 based on third-party code review (docs/third-party-review.md)**

The review identified that many issues share common root causes. The slices below are consolidated to address multiple issues with focused fixes, ordered by foundational dependency.

| Slice | Status | Priority | Issues Addressed | Concrete Next Moves |
|---|---|---|---|---|
| `E21` state consistency foundation | Active | **P0** | Direct mutation bypass, coverage/todo drift, no-op loops, wasted retries, missing validators | 1. Refactor `agentic_extract_runtime.py` to return state deltas instead of mutating `extract_state_by_url` directly (lines 273-297). 2. Route all state changes through `agentic_state_apply.py`. 3. Simplify `_reconcile_extract_todos_from_coverage` to use explicit URL/target_id references instead of text matching (lines 234-318). 4. Add state consistency validators that run after each cycle: assert coverage matches todos, no duplicate URLs, no orphaned state. 5. Add early-return with `skipped` status for completed pages to avoid no-op finalize. |
| `E22` regression test matrix | Active | **P0** (parallel) | No archetype-specific tests, no state consistency tests | 1. Add `tests/test_retrieval_agentic_author.py` with workspace/ennan replay asserting CellFusion and XRON are found. 2. Add `tests/test_retrieval_agentic_topic.py` with workspace/congestion replay asserting Falcon is found. 3. Add `tests/test_retrieval_agentic_multiyear.py` with SIGCOMM 2020-2022 replay asserting all three years are covered. 4. Add `tests/test_retrieval_agentic_semantic.py` with workspace/mem replay asserting direct hits are prioritized. 5. Add state consistency tests asserting coverage/todo alignment after each cycle. |
| `E23` query archetype detection | Active | **P1** | Missing archetype classification, wrong segmentation for author queries, missing latest signal, multi-year planning failure | 1. Add `query_profile.archetype` enum in `agentic_contracts.py`: `author`, `venue`, `topic`, `semantic`, `multi_year`, `latest`. 2. Implement archetype detection in `agentic_view.py::_build_agent_memory` based on query text analysis and filter presence. 3. Add `query_profile.is_latest` boolean derived from "latest" / "recent" keywords. 4. Add `query_profile.year_range_progress` dict for multi-year queries tracking which years have been searched/extracted. 5. Use archetype to adjust segmentation strategy in `agentic_text.py`: for `author` archetype on accepted pages, use block-preserving segmentation that keeps title+author pairs together. |
| `E24` planner memory completeness | Active | **P1** | Missing memory dimensions, direct hits not prioritized, year range not tracked, page quality not scored | 1. Extend `_build_agent_memory` to include: `query_profile.is_latest`, `query_profile.year_range_progress`, `query_profile.archetype`, `page_type_distribution` (counts of listing/detail/PDF), `extract_progress_by_venue_family`. 2. Add per-URL quality scoring in `agentic_view.py` based on: domain authority (official conference vs third-party), page_role (accepted/program high quality), content richness. 3. Strengthen `priority_direct_hits` weighting: for `latest` and `semantic` archetypes, add quality threshold that triggers auto-extraction consideration. 4. Add planner contract guidance: for `latest` queries, extract direct hits before searching listings. 5. Include venue acronym in `page_family` computation to prevent same-host sibling venue confusion (HotNets vs SIGCOMM on usenix.org). |
| `E25` extraction scope management | Active | **P1** | Same-page restart vs continuation, filter-pack drift, surfaced-title self-poisoning, repeated scope resets | 1. Include filter pack in scope signature computation in `agentic_extract_runtime.py` to detect when filters change. 2. Add `restart_reason` field to extract trace when `segments_done` resets to 0. 3. Track filter pack history per page to avoid repeating failed packs. 4. In `agentic_view.py`, sanitize `text_filters.literal_any` to remove already-matched paper titles from `matched_papers`. 5. Add planner contract guidance: do not use surfaced paper titles as literal filters in subsequent passes. 6. Prefer continuation over restart when page has partial progress (segments_done > 0). |
| `E26` extraction quality by archetype | Active | **P2** | Author block segmentation, broad lexical over-matching, abstract quality, missing semantic similarity | 1. For `author` archetype on accepted/program pages, implement block-preserving segmentation in `agentic_text.py` that keeps title+author+affiliation in same segment. 2. For `topic` archetype, add semantic similarity matching in `agentic_extract_candidates.py` instead of just substring matching. 3. Cap window count for broad filters (e.g., max 50 windows per page for topic queries). 4. For `semantic` archetype, weight abstract-level evidence higher than title-only overlap. 5. Add abstract quality validation: minimum length, not just author names, not just title repetition, contains content words. 6. Require multiple filter matches for broad filters to reduce false positives. |
| `E27` page quality scoring and budget | Active | **P2** | Third-party mirrors compete with official, low-quality pages consume budget, no budget tiers | 1. Implement quality scoring in `agentic_view.py`: score = domain_authority (0-1) * page_role_bonus (accepted=1.5, program=1.3, listing=1.0, detail=1.2, PDF=0.7) * content_richness (has_abstract=1.2). 2. Filter `priority_extract_urls` by quality threshold (e.g., score > 0.5). 3. Add budget tiers in `agentic_extract_runtime.py`: direct hits get full budget, official listings get medium budget (max 40 segments), third-party listings get low budget (max 20 segments), bibliographies get very low budget (max 10 segments). 4. Use quality scores to order extraction: high-quality pages first. 5. Add early termination for low-quality pages that show no candidates after first batch. |
| `E17` human-facing trace readability | Active | **P2** | Hidden extraction details, no continuation/restart distinction, no filter deltas, None field printing | 1. In `agentic_trace.py`, add `restart_reason` and `filter_delta` to extract debug summary. 2. In `agentic_loop.py` CLI progress, show: continuation vs restart label, effective filter deltas, skip/retry reasons. 3. Show when extracted rows were later dropped by candidate shaping. 4. Fix `None` field printing in CLI output. 5. Show selected extract URLs at action start. 6. Show candidate-URL proposal stage when it runs. |
| `E18` metadata/result cleanup | Later | **P3** | Result field redundancy, missing venue/doi/arxiv, abstract snippet vs full | Remove `authors_with_affiliations` and `abstract_snippet`; keep `author_affiliations` primary; add a narrow enrichment step for `venue` / `doi` / `arxiv_url` when clearly present; avoid listing text masquerading as full abstracts. |
| `E19` keep PDF extraction out of the active queue | Guardrail | - | PDF distraction | Stay HTML-first by default; only reopen PDF work when a concrete replay/live case shows HTML is insufficient. |

**Mapping to previous slices:**
- `E21` supersedes parts of `E14` (state consistency) and addresses the foundational gap not explicitly called out before
- `E22` replaces `E20` with concrete test file names and assertions
- `E23` addresses parts of `E13`, `E14`, `E15` with a unified archetype-based approach
- `E24` addresses parts of `E13`, `E14` with concrete memory field additions
- `E25` addresses parts of `E15` with concrete scope signature and filter tracking
- `E26` addresses parts of `E15` with archetype-specific extraction strategies
- `E27` addresses gaps not explicitly mentioned in previous slices
- `E17`, `E18`, `E19` remain as before

**Execution order:**
1. Start `E21` and `E22` in parallel (foundational fixes + test coverage)
2. After `E21` completes, start `E23` (depends on state consistency)
3. After `E23` completes, start `E24` and `E25` in parallel (both depend on archetype detection)
4. After `E24` and `E25` complete, start `E26` and `E27` in parallel (both depend on memory completeness)
5. `E17` can start anytime after `E21` (trace improvements benefit from state consistency)
6. `E18` and `E19` remain lower priority

### 3.2.11 Notes On Completed Historical Slices

- `E5-E12` are mostly historical/completed slices now.
- Keep using the `3.2.6 Historical Progress Log` above as the reference for what was already implemented and validated.
- Do not reopen those older slices unless a new replay/live failure clearly maps back to them.

## 4) Non-Active Modules (Summary Only)

| Module | Next Gate To Open Detailed Board |
|---|---|
| Data Backend and RAG | Open after agentic extraction artifacts and metadata schema stabilize. |
| Paper Context Retrieval | Open after agentic final candidates are consistently stable. |
| Paper Context Formatted Extraction | Open after context retrieval schema is stable. |
| Analysis Skill | Open after extraction and context artifacts are versioned and stable. |
| Output Skills | Open after analysis outputs are stable for rendering. |
| UI/TUI Design | Open after trajectory and result contracts are stable. |
