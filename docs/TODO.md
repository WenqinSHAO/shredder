# Implementation Progress Board

Last updated: 2026-03-15

## 1) Program Overview

This board is the single source of truth for delivery progress.
Detailed task tables are maintained only for modules currently in active implementation.

## 2) Module Progress Bars

| Module | Progress | Status |
|---|---:|---|
| Meta Info Retrieval (deterministic) | `85%` (`████████░░`) | Stabilized |
| Agentic Meta Info Retrieval | `58%` (`██████░░░░`) | Active (planning + web-search + URL shortlist baseline running) |
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

## Agentic Meta Info Retrieval - Active (I2 LLM + SearxNG Loop)

Scope:
- Keep existing `retrieve-agentic` entry points and core artifacts.
- Implement iterative LLM-powered query understanding for web retrieval through SearxNG.
- Keep scope narrow: no resume, no human-gated stop, no multi-workflow policy engine.

### 3.4 I2 Sprint Board

| ID | Item | Status | Note |
|---|---|---|---|
| P0-A1 | Prompt-engineered single agent emits structured next action JSON (`action`, `params`, `plan_update`, `progress_update`) | Done | Compound cue understanding + multi-hop plan updates wired. |
| P0-A2 | Implement `search_web` action only (SearxNG) end-to-end | Done | Query execution, ranking, filtering, and condensed URL cards running. |
| P0-A3 | Define non-web actions as schema-valid stubs (`fetch_content`, `extract_content`, `ask_user`, `memory_read/write`) | Done | Router returns `not_implemented` with structured payload; loop remains inspectable. |
| P0-A4 | Add action trace artifact for inspectability (`agentic_actions.tsv`) | Done | Per-cycle action and status persisted. |
| P0-A5 | Tests for action timing and stub safety | Done | `tests/test_retrieval_agentic_i1.py` covers single-cycle success, empty, and stub stop. |

### 3.4.1 Next Sprint Board (P1: Fetch + Extract)

| ID | Item | Status | Note |
|---|---|---|---|
| P1-B1 | Implement `fetch_content` action with bounded URL fetch and cache | Todo | Consume `agentic_fetch_queue.yaml`; fetch top targets only with timeout/retry limits. |
| P1-B2 | Implement stateless extraction pass (`extract_content`) with filter-aware one-shot output | Todo | One call per target to extract relevant lines/spans by filters (institution/author/topic/year). |
| P1-B3 | Define extracted record schema and artifacts (`agentic_extracted_facts.yaml/tsv`) | Todo | Keep main agent context lean by passing compact summaries + refs only. |
| P1-B4 | Promote paper-level candidates after extraction, not from raw web hits | Todo | `final_candidates` becomes extraction-derived handoff list only. |
| P1-B5 | Add end-to-end tests (`search_web -> fetch_content -> extract_content`) with noise pages | Todo | Validate anti-noise filtering, queue execution order, and compact payload round-trip. |
| P1-R1 | Refactor schemas/types for action params + status contracts | Todo | Stabilize Pydantic/typed contract layer before adding more tools. |
| P1-R2 | Payload budget + context hygiene review | Todo | Ensure per-cycle request payload stays bounded and delta-based. |

### 3.5 Defaults and Limits

- Active workflow name for I2: `searxng_meta_refine_v1`.
- Retrieval source scope for I2: SearxNG only.
- Loop stop rule: LLM returns `stop=true` OR no meaningful new narrowed signals, always bounded by `max_cycles`.
- Existing user knobs remain `prompt` and `top_n`; cycle and model controls come from project config.

### 3.6 I2 Contracts and Config

Cycle-level behavior contract:
1. Agent LLM receives prompt + trajectory summary and emits JSON action envelope:
   - `action: str`
   - `params: dict` (action-specific)
   - `plan_update: dict`
   - `progress_update: dict`
   - `rationale: str`
   - `stop: bool`
   - `stop_reason: str`
2. App executes action through action router:
   - v0 implementation: only `search_web`
   - other actions return schema-valid `not_implemented`.
3. For `search_web`, system executes each query against `SEARXNG_URL/search` using `format=json` and configured categories (`retrieval.agentic.searxng_categories`, default `general`).
4. System condenses raw web results, updates candidate list, and continues loop until convergence, stub-stop, or `max_cycles`.

Artifacts:
- Keep existing artifacts:
  - `agentic_request.yaml`
  - `agentic_session.yaml`
  - `agentic_result.yaml`
  - `agentic_questions.yaml`
  - `agentic_cycles.tsv`
  - `agentic_candidates_latest.tsv`
- Add I2 intermediate artifacts:
  - `agentic_web_results.tsv` (append-only across cycles, includes `cycle_index`, `query`, rank, compact metadata)
  - `agentic_llm_payloads.yaml` (per-cycle planner/decider input summaries and outputs; do not store full raw web payloads)
  - `agentic_actions.tsv` (per-cycle selected action and execution status; used for phased action-space rollout)
  - `agentic_url_hits_latest.yaml` (URL-card shortlist for current cycle: title, url, host, peek, score, rejection stats)
  - `agentic_fetch_queue.yaml` (iterative todo queue for pending fetch/extract targets)

Config defaults:
- `retrieval.agentic.max_cycles`: `3`
- `retrieval.agentic.queries_per_cycle`: `4`
- `retrieval.agentic.web_results_per_query`: `8`
- `retrieval.agentic.searxng_categories`: `general`
- `retrieval.agentic.llm.model`: `deepseek/deepseek-chat`
- `retrieval.agentic.llm.api_key_env`: `DS_API_KEY`

### 3.7 Next Step (Domain-Knowledge Assisted Academic Search) - Incremental Hardening

#### 3.7.1 Objective and Guardrails

- Agentic retrieval does not replace deterministic metadata retrieval.
- Agentic retrieval output is an editable candidate list (paper title + DOI/arXiv/url + provenance hints) for deterministic handoff.
- Domain focus is academic paper search; implementation must stay general (no hardcoded venue names/lists).

#### 3.7.2 Domain Knowledge for Prompt Engineering (v0)

The agent prompt must encode the following strategy patterns:

- Cue-aware planning:
  - Venue cues: prioritize searches for conference `program`, `accepted papers`, `proceedings`, optionally with year.
  - Author cues: prioritize author-centric pages and paper lists.
  - Topic cues: run open search, then refine by semantic relevance from titles/snippets.
- Multi-hop behavior:
  - `search_web` to discover promising URLs.
  - Later-phase tools (stubbed in v0) will fetch URL content and extract/filter lines by author, institution, and topic relevance.
- Ambiguity policy:
  - If tie persists (same-name authors, near-duplicate titles, conflicting identifiers), choose `ask_user` action.
- Non-hardcoding policy:
  - Use cue types and page patterns, not fixed conference name rules.

#### 3.7.3 Action Space v0 (Grow Gradually)

Common action envelope (agent -> app):
- `action`: one of `search_web`, `fetch_content`, `extract_content`, `ask_user`, `memory_read`, `memory_write` (`finalize` is agent-internal)
- `params`: action-specific payload validated by the selected action contract
- `stop`: bool
- `stop_reason`: str
- `rationale`: str

Action parameter contract notes:
- `search_web` uses `params.queries` (list of query strings) and retrieval-related knobs.
- `fetch_content` uses `params.targets[]` with `url`, `title`, `why`, and `filters`.
- Non-web actions do not require `queries`; they define their own inputs (for example URLs, memory refs, user question payloads, or finalize options).
- The app validates `params` against the selected action before execution.

Implementation status in this phase:
- Implemented: `search_web` (SearxNG only).
- Stub-only (schema-valid, returns `not_implemented`): `fetch_content`, `extract_content`, `ask_user`, `memory_read`, `memory_write`.

#### 3.7.4 Prompt Contract v0

Agent prompt responsibilities:
- Keep concise search trajectory summary and select next action.
- Use venue/author/topic cue-aware planning and multi-hop intent.
- Prefer conference-program discovery when venue signal is present.
- If multiple candidate venues are involved, enumerate them explicitly in plan state and search them one by one.
- Anticipate later filters (`grep`-like author/institution matching and semantic topic matching) when selecting URLs.
- Trigger `ask_user` only for ambiguity dead-ends.
- Return strict JSON only (action envelope above).

#### 3.7.5 Artifacts and Traceability (v0)

Keep existing artifacts and add:
- `agentic_actions.tsv`: per-cycle action trace (`action`, `status`, `planned_queries`, `stop_reason`, notes).
- `agentic_url_hits_latest.yaml`: shortlist cards for agent decision (`url_title`, `url`, `peek`, `host`, `source`, `score`) + rejection counts.
- `agentic_fetch_queue.yaml`: ordered todo list of URLs + filter actions for upcoming fetch/extract steps.

Current baseline status:
- Initial planning + iterative web search + URL shortlisting are functional.
- Main gap is content execution (`fetch_content` + `extract_content`) to produce paper-level final candidates.

## 4) Non-Active Modules (Summary Only)

| Module | Next Gate To Open Detailed Board |
|---|---|
| Data Backend and RAG | Open after P1 extraction artifacts are stable (`agentic_extracted_facts.*` + paper-level candidate handoff contract). |
| Paper Context Retrieval | Open after agentic output reliably yields canonical paper candidates (DOI/arXiv/title) from extracted content. |
| Paper Context Formatted Extraction | Open after fetch+extract contracts are stable and evidence references are consistent. |
| Analysis Skill | Open after extraction outputs have versioned schema and confidence/evidence controls. |
| Output Skills | Open after analysis outputs are stable and iteration-safe for rendering. |
| Overall UI Design | Open after agentic step semantics are stable enough to expose in UI workflows. |

Fresh-session prioritization note:
- Do not expand these modules before P1 (`fetch_content` + `extract_content`) is complete and validated.
- If capacity is limited, prioritize retrieval correctness and artifact contracts over parallel module kickoff.

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

`M-Agentic-P0` (2026-03-15): initial planning + web-search baseline established.

Evidence snapshot:
- Single-agent action planning is functional (`action`, `params`, `plan_update`, `progress_update`).
- `search_web` is implemented end-to-end with SearxNG.
- URL-centric shortlist and queue artifacts are produced (`agentic_url_hits_latest.yaml`, `agentic_fetch_queue.yaml`).
- Non-web actions are schema-valid stubs with explicit stop reasons.
- Action-level traceability is persisted (`agentic_actions.tsv`).

Active next milestone:

`M-Agentic-P1`: execute `fetch_content` + `extract_content` and promote paper-level final candidates.

Done when:
1. `fetch_content` consumes `agentic_fetch_queue.yaml`, fetches bounded targets, and persists fetched artifacts deterministically.
2. `extract_content` performs filter-aware one-shot extraction per target and writes `agentic_extracted_facts` artifacts.
3. `final_candidates` is paper-level only (derived from extraction), while URL shortlists remain separate artifacts.
4. End-to-end tests cover `search_web -> fetch_content -> extract_content` with noisy-page scenarios and pass.
5. Per-cycle LLM payload remains compact (delta-focused summaries, no raw page dumps).
6. Existing entry points (`retrieve-agentic` CLI/API/runner) remain backward compatible.

## 6) Next Fresh Session Task Queue

Use this queue at the start of the next session:

1. Session handoff snapshot (cleaned baseline):
   - I2 iterative action-driven LLM+SearxNG loop is running with URL shortlist + fetch queue artifacts.
   - Next step is `fetch_content` + `extract_content` execution and paper-level candidate promotion.
   - Entry points remain:
     - Runner step: `retrieve-agentic`
     - CLI command: `python -m src.cli retrieve-agentic <project_id> --prompt ... --top-n N`
     - API endpoint: `POST /projects/{project_id}/retrieve/agentic`

2. Environment + validation baseline:
   - Use virtual environment: `/home/wenqin/.virtualenvs/shredder`.
   - Baseline command:
     - `/home/wenqin/.virtualenvs/shredder/bin/python -m pytest -q`
   - Retrieval-specific focus:
     - Existing: `tests/test_retrieval_agentic_i1.py`
     - Add next: end-to-end fetch/extract tests with noisy web pages and filter-aware extraction checks.

3. Next session entry criteria:
   - `DS_API_KEY` is available in env.
   - `SEARXNG_URL` points to reachable backend.
   - `litellm` dependency is installed in runtime.
   - Domain-knowledge section (3.7) remains aligned with implemented action contracts and artifacts.

4. Workspace hygiene reminder before commit:
   - Do not commit generated runtime files such as `kb/kb.sqlite` and `src/shredder.egg-info/`.
