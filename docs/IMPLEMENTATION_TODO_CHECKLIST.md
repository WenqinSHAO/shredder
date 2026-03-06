# Implementation Progress Board

Last updated: 2026-03-06

## 1) Program Overview

This board is the single source of truth for delivery progress.
Detailed task tables are maintained only for modules currently in active implementation.

## 2) Module Progress Bars

| Module | Progress | Status |
|---|---:|---|
| Meta Info Retrieval (deterministic) | `85%` (`████████░░`) | Stabilized |
| Agentic Meta Info Retrieval | `20%` (`██░░░░░░░░`) | Active (board opened) |
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

## Agentic Meta Info Retrieval - Active

Scope:
- Build checkpoint/resume agentic retrieval on top of deterministic DOI/arXiv/title resolution.
- Persist intermediate cycle artifacts as session memory and progress-control ledger.
- Split lead-based workflows into dedicated sub-modules (`lead_author_org_venue`, `lead_citation_graph`) with shared runtime/scoring.

Execution strategy:
- Deliver in four increments with explicit dependencies and acceptance criteria.
- Keep deterministic retrieval layer frozen except bugfixes and compatibility adaptations.

### 3.4 Incremental Task Board

#### Increment 1: Session Core + Theme Workflow (`M-Agentic-I1`)

| ID | Sub-module | Task | Status | Acceptance Criteria | Depends On |
|---|---|---|---|---|---|
| A1 | Session Contracts | Define `agentic_request.yaml`, `agentic_session.yaml`, `agentic_result.yaml`, `agentic_questions.yaml`, `agentic_cycles.tsv`, `agentic_candidates_latest.tsv` schemas/contracts. | Todo | Artifacts are versioned, deterministic-fielded, and loadable across resume cycles. | None |
| A2 | Orchestrator Core | Build single-loop orchestrator state machine: `plan -> retrieve -> rank -> decide(ask/continue/stop)`. | Todo | One full cycle runs with no user interrupt path and writes all session artifacts. | A1 |
| A3 | Progress Memory | Persist per-cycle memory: planned query, tool calls, candidate deltas, rationale, stop-check signals. | Todo | Resume from checkpoint reproduces same next step given same inputs. | A1,A2 |
| A4 | Tool Router | Implement scholarly-first tool routing (KB + OpenAlex/Crossref/S2/arXiv), web fallback trigger policy scaffold. | Todo | Router calls web only when scholarly retrieval is insufficient by policy. | A2 |
| A5 | Workflow: Theme Refinement | Implement `theme_refine` workflow with iterative narrowing and candidate shortlist updates. | Todo | Broad-theme prompt converges to shortlist with >=2 cycles and explicit rationale history. | A2,A4 |
| A6 | CLI/API Session UX | Add checkpoint-resume interfaces: start session, fetch status, submit answers, finalize. | Todo | CLI/API can pause on question and resume without losing cycle memory. | A2,A3 |
| A7 | Tests I1 | Unit + integration coverage for state machine, artifact writing, resume idempotence, theme workflow. | Todo | CI tests cover happy path + resume path + empty-result fallback path. | A1-A6 |

#### Increment 2: Fuzzy Reference + Feedback Learning (`M-Agentic-I2`)

| ID | Sub-module | Task | Status | Acceptance Criteria | Depends On |
|---|---|---|---|---|---|
| B1 | Workflow: Fuzzy Reference | Implement `fuzzy_reference` workflow for shorthand/inexact paper mentions using lexical candidate generation + LLM rerank + metadata checks. | Todo | Inexact paper queries recover likely canonical papers with explicit confidence + alternatives. | A4,A5 |
| B2 | User Feedback Loop | Add `keep/remove/why-missing` feedback capture and apply it to next-cycle scoring/query reformulation. | Todo | Feedback changes ranking/frontier in next cycle and is recorded in ledger. | A3,B1 |
| B3 | Clarification Policy | Interrupt-driven question policy (only ambiguity/confidence failures), with max questions per cycle. | Todo | Session asks only when thresholds fail; otherwise proceeds autonomously. | A2,B2 |
| B4 | Ranking Engine v1 | Balanced precision/recall scoring with transparent components: relevance, source quality, lead match, novelty, user-feedback alignment. | Todo | Candidate table includes per-component score breakdown persisted in artifacts. | B2 |
| B5 | Stop/Convergence Controller | Default stop = convergence + user confirm; guardrails: max hops=3, cycle/tool/token/time budgets. | Todo | Stop reason always explicit and reproducible in `agentic_result.yaml`. | B3,B4 |
| B6 | Tests I2 | Golden tests for fuzzy-reference scenarios and feedback-driven reranking behavior. | Todo | Fixture suite validates convergence and ambiguity-handling paths. | B1-B5 |

#### Increment 3: Lead-Based Workflow Pack (Author/Org/Venue) (`M-Agentic-I3`)

| ID | Sub-module | Task | Status | Acceptance Criteria | Depends On |
|---|---|---|---|---|---|
| C1 | Lead Normalization | Parse/normalize leads from prompt: author, institution, venue, paper seed, year bounds, domain tags. | Todo | Lead parsing emits typed lead objects with confidence and unresolved fields. | B4 |
| C2 | Workflow: Author/Org/Venue | Implement dedicated `lead_author_org_venue` workflow with facet-specific query planners and constraints. | Todo | Query like "all networking papers from Alibaba on SIGCOMM" yields constrained shortlist with traceable filters. | C1 |
| C3 | Planner Specialization | Add facet planner strategies (author-centric, org-centric, venue-centric) rather than one generic planner. | Todo | Planner strategy chosen by lead type and persisted in cycle ledger. | C2 |
| C4 | Constraint Refinement | Add interactive refinement prompts specific to lead facets (e.g., affiliation ambiguity, venue aliasing, year scope conflicts). | Todo | User can refine constraints mid-session without restarting session. | C2,C3 |
| C5 | Tests I3 | Scenario tests for author/org/venue lead retrieval and constraint refinement loops. | Todo | Each lead type has passing end-to-end scenario with resume path. | C1-C4 |

#### Increment 4: Citation Workflow + Hardening (`M-Agentic-I4 / RC1`)

| ID | Sub-module | Task | Status | Acceptance Criteria | Depends On |
|---|---|---|---|---|---|
| D1 | Workflow: Citation Graph | Implement dedicated `lead_citation_graph` workflow (forward citations, backward references, bounded expansion). | Todo | Citation-based exploration behaves differently from author/org/venue and is traceable in ledger. | C1,B5 |
| D2 | Multi-hop Expansion Policy | Enable adaptive expansion up to max 3 hops with per-hop frontier controls and pruning. | Todo | Hop transitions and pruning decisions are explicit and replayable. | D1 |
| D3 | Web Fallback Provider Layer | Implement pluggable provider interface; default SearXNG provider and optional extension hook for hosted providers. | Todo | If pluggable path is not viable in sprint scope, fallback to SearXNG-only without contract changes. | A4 |
| D4 | Observability + Evaluation | Add cycle-level metrics: latency, tool calls, fallback rate, clarification rate, convergence cycles, acceptance rate. | Todo | Metrics emitted to artifacts and used by regression tests. | D1-D3 |
| D5 | RC1 Hardening | Backward compatibility checks, failure-mode tests, docs updates, and launch checklist. | Todo | RC1 gate passes with deterministic handoff compatibility preserved. | D1-D4 |

### 3.5 Minimum Required Test Scenarios

1. Broad-theme interactive narrowing with at least one clarification interrupt.
2. Fuzzy shorthand paper query resolved to canonical candidate set.
3. Lead-based author/org/venue constrained search with user feedback refinement.
4. Citation-graph exploration with bounded hops and convergence stop.
5. Session interruption/resume across multiple cycles with exact artifact continuity.
6. Scholarly-first success path and web-fallback path both covered.
7. Deterministic handoff compatibility: resolved finalists pass through existing deterministic persistence path unchanged.

### 3.6 Assumptions and Defaults

- Orchestrator model: single-controller loop.
- Interaction mode: checkpoint-resume via CLI/API.
- Retrieval policy: scholarly-first, web fallback conditional.
- Lead workflows are intentionally split:
  - `theme_refine`
  - `fuzzy_reference`
  - `lead_author_org_venue`
  - `lead_citation_graph`
- Convergence defaults:
  - max hops = 3
  - stop on convergence + user confirmation
  - enforce cycle/tool/time/token guardrails
- Intermediate steps are mandatory persisted memory for both progress control and replay/debug.

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

1. Execute Increment-1 A1/A2: define session artifact contracts and implement single-loop orchestrator skeleton with full-cycle artifact writes.
2. Execute Increment-1 A3/A4: add resume-safe progress memory and scholarly-first router with conditional web fallback scaffold.
3. Execute Increment-1 A5/A6: ship `theme_refine` workflow and checkpoint-resume CLI/API interfaces.
4. Execute Increment-1 A7: land unit/integration coverage (happy path, resume idempotence, empty-result fallback), then open Data Backend/RAG detailed board contracts.
