# Commit Review: Alignment to planning (through data connectors + dedup)

Scope reviewed:
- Merge #1 (`8c13785`): initial design/workplan baseline.
- Merge #2 (`dfe283a`): PyYAML adapter refactor.
- Merge #3 (`252a32c`): connector-based discovery + dedup.

## Verdict

Partially aligned. The repository has real connector integrations and writes `raw.tsv` / `deduped.tsv`, but key acceptance/quality requirements from `docs/WORKPLAN.md` and `docs/DESIGN.md` are incomplete.

## Findings

### 1) Connector integration exists, but required retries/backoff are missing
- Plan expects real connectors with retries + rate limits.
- Current HTTP adapter performs one request with no retry/backoff behavior.

Impact:
- transient API/network errors directly degrade discovery quality and increase fallback usage.

### 2) SearxNG fallback connector is planned but not wired into aggregation
- Design/workplan mention SearxNG search fallback.
- A `SearxngConnector` file exists but returns an empty list and is not included in discovery aggregation connector list.

Impact:
- reduced resilience when primary scholarly APIs are unavailable or rate-limited.

### 3) Dedup logic is order-dependent for DOI upgrade cases
- Goal says dedup priority should be DOI > arXiv > title+year.
- Current dedup matching only checks DOI equality when the *incoming* row has DOI. If an existing row (without DOI) should be merged with a later row that adds DOI, they may not merge in all orderings.

Impact:
- duplicate records can survive dedup depending on connector row order.

### 4) Provenance can be written to non-canonical paper IDs
- Provenance is written from `raw_rows` IDs after pre-dedup stable ID assignment.
- In merge/upgrade cases, canonical deduped `paper_id` can differ from some raw row IDs.

Impact:
- provenance rows can point to IDs absent from `papers`, weakening traceability guarantees.

### 5) Tests are too narrow for critical dedup edge cases
- Existing tests cover DOI match, arXiv match, and fuzzy title-year match.
- Missing tests for DOI-late-arrival merge, connector-order invariance, and canonical provenance mapping.

Impact:
- regressions in dedup correctness are likely.

---

## Proposed fixes (implementation-ready)

## A) HTTP resiliency and polite rate handling

### Changes
- Extend `src/connectors/http.py` with:
  - `RetryPolicy` dataclass (`max_attempts`, `base_backoff_s`, `max_backoff_s`, `jitter_s`, `retry_http_statuses`).
  - retry loop around `urlopen` for retryable failures (`URLError`, timeout, HTTP 429/5xx).
  - per-attempt delay: `min_interval_s` + bounded exponential backoff + jitter.
- Keep `get_json(...)` signature backward-compatible by adding optional `retry_policy: RetryPolicy | None = None`.

### Acceptance criteria
- transient 429/503 responses are retried and eventually succeed without surfacing an exception when later attempt succeeds.
- non-retryable failures (e.g., malformed URL / 4xx non-429) fail fast.

### Suggested tests
- `tests/test_connectors_http.py`
  - success on second attempt after first timeout.
  - no retry for HTTP 400.
  - retry capped at configured `max_attempts`.

## B) Implement and wire SearxNG fallback

### Changes
- Implement `src/connectors/searxng.py`:
  - endpoint from config/env (`SEARXNG_URL`), query with JSON format.
  - normalize result fields into discovery row contract.
  - optional lightweight title/year filtering.
- Wire in `src/orchestrator/discovery.py`:
  - include `("searxng", SearxngConnector)` in `connector_defs`.
  - enable only when configured (`enabled: true` + endpoint present).
- Add default config stanza in `src/workspace/manager.py` project template.

### Acceptance criteria
- when primary scholarly connectors fail and SearxNG is configured, discovery still emits non-empty `raw.tsv` without using mock rows.
- when SearxNG is disabled/unconfigured, behavior stays unchanged.

### Suggested tests
- mocked connector integration test validating SearxNG invocation path.

## C) Make dedup deterministic and order-invariant

### Changes
- Refactor dedup into two-stage process in `src/orchestrator/discovery.py`:
  1. Build candidate keys per row: doi_key, arxiv_key, title_year_key.
  2. Union/merge rows if any key overlaps existing cluster.
- Determine canonical ID per merged cluster with strict precedence:
  1) DOI, 2) arXiv, 3) title+year stable id.
- Normalize IDs (`lower()`, strip prefixes) before keying.

### Acceptance criteria
- dedup output identical regardless of input row order.
- a row set containing both title-only and DOI variants of same paper yields one DOI canonical record.

### Suggested tests
- permutation-based unit test for same rows in multiple orders.
- DOI-late-arrival merge test (title-only first, DOI second).

## D) Canonical provenance mapping

### Changes
- Update dedup API to return mapping from raw record key to canonical paper ID:
  - e.g., `deduplicate_candidates(rows) -> tuple[deduped_rows, raw_to_canonical]`.
- In `src/orchestrator/steps.py`, write provenance using canonical mapped ID rather than raw pre-dedup `paper_id`.
- Use deterministic raw key: `(source, source_id)` with fallback hash when source_id is empty.

### Acceptance criteria
- every provenance `entity_id` inserted during discovery exists in `papers.id` after same run.

### Suggested tests
- integration test using synthetic rows that force ID upgrade from title-id to DOI-id; assert provenance points to DOI canonical ID.

## E) Expand automated validation

### Add tests
- `tests/test_discovery_dedup_ordering.py` for order invariance + canonical precedence.
- `tests/test_discovery_provenance.py` for provenance-to-paper referential integrity.
- optional: `tests/test_discovery_connectors_fallback.py` for fallback ordering logic.

### CI behavior
- Ensure tests run with package import path configured (e.g., `PYTHONPATH=.` in CI command or editable install before test).

---

## Execution plan (small, safe PR sequence)

1. **PR-1: HTTP retry policy + tests** (low blast radius).
2. **PR-2: Dedup refactor + order/provenance tests** (core correctness).
3. **PR-3: SearxNG implementation + config wiring + tests** (resilience).
4. **PR-4: metrics/logging hardening** (non-functional improvements).

This sequence minimizes risk by locking correctness first (dedup/provenance), then expanding connector fallback behavior.
