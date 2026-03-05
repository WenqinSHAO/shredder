# Codex Execution Prompt: Implement M1 Discovery/Dedup Fixes

Use this prompt in a fresh Codex session to implement the fixes described in `docs/COMMIT_REVIEW_M1_DISCOVERY.md`.

---

You are working in `/workspace/shredder`.

## Goal
Implement the discovery-layer corrective work identified in `docs/COMMIT_REVIEW_M1_DISCOVERY.md` so the code aligns with planning through connectors + dedup + provenance integrity.

## Source of truth
1. `docs/COMMIT_REVIEW_M1_DISCOVERY.md`
2. `docs/WORKPLAN.md` (M1 expectations)
3. `docs/DESIGN.md` (dedup/provenance rules)

## Constraints
- Keep behavior backward-compatible where practical.
- Prefer small, reviewable commits (or clearly separated sections in one PR).
- Add/adjust tests for every behavior change.
- Do not add try/catch around imports.
- Keep connector API response handling defensive but minimal.

## Required implementation scope

### A) Add HTTP retry/backoff policy
**Files**: `src/connectors/http.py` (+ new tests)

Implement:
- `RetryPolicy` dataclass with at least:
  - `max_attempts: int`
  - `base_backoff_s: float`
  - `max_backoff_s: float`
  - `jitter_s: float`
  - `retry_http_statuses: set[int]` (default includes 429, 500, 502, 503, 504)
- Extend `get_json(...)` to optionally accept `retry_policy`.
- Retry on transient failures (`URLError`, timeout, HTTP retry statuses).
- Fail fast on non-retryable HTTP 4xx (except 429).
- Keep `min_interval_s` behavior (rate limiting) intact.

Add tests:
- `tests/test_connectors_http.py`
  - retries and succeeds on 2nd attempt
  - does not retry HTTP 400
  - stops at `max_attempts`

### B) Implement + wire SearxNG connector
**Files**: `src/connectors/searxng.py`, `src/orchestrator/discovery.py`, `src/workspace/manager.py`

Implement:
- `SearxngConnector` consistent with `DiscoveryConnector` interface.
- Configurable base URL (from config, with optional env fallback `SEARXNG_URL`).
- JSON query and normalization into discovery fields:
  - `source, source_id, title, venue, year, doi, arxiv_id, url`
- Add SearxNG to `connector_defs` in discovery aggregation.
- Add default config stanza under `project.yaml` template for searxng.
- Only run it when enabled and URL is configured.

Add tests:
- connector normalization unit test
- orchestration test showing SearxNG participates when configured

### C) Refactor dedup for deterministic order-invariant behavior
**Files**: `src/orchestrator/discovery.py` (+ tests)

Implement:
- Dedup clustering by normalized keys:
  - DOI (highest priority)
  - arXiv
  - title+year fuzzy/similarity policy
- Ensure merge works regardless of input row order.
- Canonical `paper_id` precedence:
  1) `doi:<doi>`
  2) `arxiv:<arxiv_id>`
  3) stable title+year ID
- Normalize DOI/arXiv before keying.

Add tests:
- `tests/test_discovery_dedup_ordering.py`
  - same set of rows in different orders => identical dedup output
  - DOI-late-arrival case merges to single DOI canonical record

### D) Canonical provenance mapping
**Files**: `src/orchestrator/discovery.py`, `src/orchestrator/steps.py` (+ tests)

Implement:
- Dedup function should return both:
  - deduped rows
  - raw-row -> canonical paper_id mapping
- In `run_discovery`, write provenance using canonical paper IDs only.
- Raw row identity key should be deterministic (`source + source_id`, with fallback for missing source_id).

Add tests:
- `tests/test_discovery_provenance.py`
  - verify every inserted provenance `entity_id` exists in papers table for the same run

### E) Keep/extend existing tests
- Update existing tests (`tests/test_discovery_dedup.py`) if signatures change.
- Ensure full suite passes with repository’s expected invocation.

## Validation commands
Run at minimum:
1. `PYTHONPATH=. pytest -q`
2. (Optional) targeted tests for changed modules

If any command fails, fix or document clearly in PR notes.

## Expected deliverables
1. Code changes implementing A–D.
2. New/updated tests for all critical behaviors.
3. Brief update in PR description mapping each issue to concrete fix.

## PR checklist template
Use this in the PR body:

- [ ] HTTP retry/backoff implemented and tested
- [ ] SearxNG wired and config-supported
- [ ] Dedup deterministic and order-invariant
- [ ] Provenance uses canonical IDs only
- [ ] New tests added for retry, dedup ordering, provenance integrity
- [ ] `PYTHONPATH=. pytest -q` passes

