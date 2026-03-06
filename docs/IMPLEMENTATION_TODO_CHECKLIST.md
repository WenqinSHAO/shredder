# Implementation Progress Board

Last updated: 2026-03-07

## 1) Program Overview

This board is the single source of truth for delivery progress.
Detailed task tables are maintained only for modules currently in active implementation.

## 2) Module Progress Bars

| Module | Progress | Status |
|---|---:|---|
| Meta Info Retrieval (deterministic) | `85%` (`████████░░`) | Stabilized |
| Agentic Meta Info Retrieval | `40%` (`████░░░░░░`) | Active (I1 rework needed) |
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
| A1 | Session Contracts | Define `agentic_request.yaml`, `agentic_session.yaml`, `agentic_result.yaml`, `agentic_questions.yaml`, `agentic_cycles.tsv`, `agentic_candidates_latest.tsv` schemas/contracts. | Done | Artifacts are versioned, deterministic-fielded, and loadable across resume cycles. | None |
| A2 | Orchestrator Core | Build single-loop orchestrator state machine: `plan -> retrieve -> rank -> decide(ask/continue/stop)`. | Done | One full cycle runs with no user interrupt path and writes all session artifacts. | A1 |
| A3 | Progress Memory | Persist per-cycle memory: planned query, tool calls, candidate deltas, rationale, stop-check signals. | In progress | Resume from checkpoint reproduces same next step given same inputs and memory is decision-grade. | A1,A2 |
| A4 | Tool Router | Implement scholarly-first tool routing (KB + OpenAlex/Crossref/S2/arXiv), web fallback trigger policy scaffold. | In progress | Routing policy reflects retrieval quality/cost realities and avoids redundant connector fan-out. | A2 |
| A5 | Workflow: Theme Refinement | Implement `theme_refine` workflow with iterative narrowing and candidate shortlist updates. | In progress | Workflow follows candidate-centric iterative loop (discover -> extract ids -> deterministic verify -> keep/ignore -> decide). | A2,A4 |
| A6 | CLI/API Session UX | Add checkpoint-resume interfaces: start session, fetch status, submit answers, finalize. | In progress | UX clearly exposes planning/reasoning/tool IO and required user feedback at each decision point. | A2,A3 |
| A7 | Tests I1 | Unit + integration coverage for state machine, artifact writing, resume idempotence, theme workflow. | In progress | Tests cover revised loop semantics and hardened memory behavior. | A1-A6 |

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

Progress snapshot (`2026-03-07`, rework pass-1 landed):
- `A3` cycle memory now persists decision-grade records in `agentic_result.yaml`:
  - planner input/output, LLM planner payload trace, per-action tool IO, per-search purpose/fulfillment/next-hop decisions, per-candidate keep/ignore decisions, controller guardrail state.
- `A4` router policy now supports adaptive template routing and connector pruning:
  - template-aware planning (`conference_program_first`, `scholar_graph`, `bibliography_index`, `identifier_targeted`)
  - reduced scholarly open-search fan-out via adapter prioritization
  - configurable web page fetch/parse path (`retrieval.agentic.web_fetch`) for high-value pages.
- `A5` theme workflow now runs strict candidate-centric verification on extracted identifiers/titles from discovery rows and fetched page snippets (no mixed free-text deterministic lookups).
- `A6` CLI debug surfaces now expose planning template details, exact LLM planner payload, web search/fetch payloads, and search decision traces.

1. Critical carry-over issues to address first (blocking `A3-A6` completion):
   - Overall search flow is still flawed: loop must be strictly candidate-centric and iterative:
     - derive web searches from initial prompt
     - extract individual title/doi/arXiv from discovery results
     - run deterministic retrieval using individual identifiers/titles (never mixed free text)
     - evaluate fit against initial prompt
     - keep/ignore with explicit rationale
     - progress controller decides continue/ask/stop
   - Scholarly-first + broad connector fan-out is currently too expensive and noisy:
     - poor candidates and significant latency
     - redundant querying across all scholarly connectors
     - router policy needs quality/cost-aware sequencing and pruning
   - Increase reliance on web search + web page fetching:
     - conference program pages are high-value sources
     - retrieval should support webpage content fetch/parse before candidate verification
   - Debug transparency is insufficient:
     - explicitly log what is passed to LLM backend
     - explicitly log what is passed to web search provider and fetched page parser
   - Intermediate decisions/search records are not yet hardened as memory:
     - loop control must rely on persisted, replayable decision memory
     - artifacts should include durable per-action inputs/outputs, keep/ignore reasons, and controller state
   - Add typical search optimization paths and generalize them into workflow policies:
     - explicitly target conference program pages, Google Scholar result pages, and DBLP pages
     - digest fetched page content into structured search memory (entities, links, candidate ids, next-hop clues)
     - make “next search” decisions explicit and testable:
       - what next query is proposed from previous search output
       - what purpose is expected for that query
       - whether that purpose was fulfilled by returned results
       - if not fulfilled: how web query is tuned/reformulated
       - if fulfilled: whether to stop, deepen, or expand to adjacent aspects

2. Environment + validation baseline:
   - Use virtual environment: `/home/wenqin/.virtualenvs/shredder`.
   - Verified command baseline:
     - Full test suite: `56 passed, 27 subtests passed`.
     - Command used: `/home/wenqin/.virtualenvs/shredder/bin/python -m pytest -q`
   - Focused I1 test file status: `9 passed` for `tests/test_retrieval_agentic_i1.py`.

3. Rework Increment-1 `A3` first (memory hardening before new features):
   - Redesign cycle memory schema around decision-grade records:
     - planner inputs/outputs
     - tool call inputs/outputs
     - candidate keep/ignore decisions + reasons
     - controller guardrail state and stop rationale
   - Ensure replay can reconstruct same next action deterministically.

4. Rework Increment-1 `A4` next (router policy and tool economics):
   - Replace naive scholarly-first fan-out with adaptive routing:
     - web-first or mixed-first when prompt is broad/theme/program-oriented
     - scholarly targeted lookup for identifier-rich candidates
     - avoid redundant all-connector querying
   - Add configurable fetch of web page content for conference/program pages.

5. Rework Increment-1 `A5` next (workflow semantics):
   - Enforce loop semantics:
     - prompt -> discovery queries
     - discovered snippets/pages -> identifier/title extraction
     - deterministic verification per candidate
     - fit scoring and keep/ignore
     - continue/ask/stop
   - Do not pass mixed prompt strings to deterministic identifier lookups.
   - Generalize search optimization flow templates:
     - conference-program-first template
     - scholar-graph template (Google Scholar/related pages)
     - bibliography-index template (DBLP-first)
   - For each template, persist:
     - expected purpose
     - fulfillment check result
     - next-hop decision (tune/deepen/expand/stop)

6. Rework Increment-1 `A6` next (UX/debug surfaces):
   - CLI/API must expose:
     - planning input/output
     - exact payloads passed to LLM backend
     - exact payloads passed to web search + fetched page summaries
     - per-candidate keep/ignore decisions
     - per-search purpose/fulfillment/tuning decision trace
     - explicit expected user feedback fields at interrupt points

7. Keep these defaults during rework:
   - Agentic runtime remains an internal single-controller state machine (no external framework dependency in I1).
   - LLM backend target remains DeepSeek via `DS_API_KEY` (OpenAI-compatible adapter boundary).
   - Web fallback target remains SearXNG via `SEARXNG_URL`.

8. Workspace hygiene reminder before commit:
   - Do not commit generated runtime files such as `kb/kb.sqlite` and `src/shredder.egg-info/`.

9. Carry-over implementation references:
   - LangChain Open Deep Research: https://github.com/langchain-ai/open_deep_research
   - PaperQA2: https://github.com/Future-House/paper-qa
   - Haystack conditional fallback routing: https://haystack.deepset.ai/tutorials/36_building_fallbacks_with_conditional_routing
   - OpenAlex filter docs: https://docs.openalex.org/how-to-use-the-api/get-lists-of-entities/filter-entity-lists
   - Crossref REST API tips: https://www.crossref.org/documentation/retrieve-metadata/rest-api/tips-for-using-the-crossref-rest-api/
   - SearXNG Search API: https://docs.searxng.org/dev/search_api.html
   - arXiv API user manual + ToU: https://info.arxiv.org/help/api/user-manual.html , https://info.arxiv.org/help/api/tou.html
