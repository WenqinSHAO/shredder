# Implementation Progress Board

Last updated: 2026-03-06

## 1) Program Overview

This board is the single source of truth for delivery progress.
Detailed task tables are maintained only for modules currently in active implementation.

## 2) Module Progress Bars

| Module | Progress | Status |
|---|---:|---|
| Meta Info Retrieval (deterministic) | `70%` (`███████░░░`) | In progress |
| Agentic Meta Info Retrieval | `15%` (`█░░░░░░░░░`) | Not started |
| Data Backend and RAG | `20%` (`██░░░░░░░░`) | Planned |
| Paper Context Retrieval | `5%` (`░░░░░░░░░░`) | Not started |
| Paper Context Formatted Extraction | `10%` (`█░░░░░░░░░`) | Not started |
| Analysis Skill | `10%` (`█░░░░░░░░░`) | Not started |
| Output Skills (report/slides/render) | `15%` (`█░░░░░░░░░`) | Early scaffold |
| Overall UI Design | `5%` (`░░░░░░░░░░`) | Not started |
| **Overall Program** | **`22%` (`██░░░░░░░░`)** | **In progress** |

## 3) Active Section Task Board

## Meta Info Retrieval (Deterministic) - Active

Scope:
- Stable deterministic resolution for DOI/arXiv/title.
- Canonical paper/author metadata persistence in DB.
- Predictable cache semantics with inspectable artifacts.
- Resolved review issues are tracked inline in task tables (`DET-META-001..004` in 3.1/3.2/3.3).

### 3.1 Paper Metadata

| Task | Status | Priority | Notes | Exit Criteria |
|---|---|---|---|---|
| Identifier determinism (DOI/arXiv/title) | In progress | P0 | Core normalization + alias behavior is implemented and RC1 matrix-tested (`tests/test_retrieval_rc1_matrix.py::test_cold_start_matrix_and_cache_replay`); remaining gap is broader benchmark/corpus sweep. | Pinned benchmark set passes expected `resolved`/`ambiguous_requires_selection`/`not_found` outcomes across DOI/arXiv/title inputs. |
| Candidate identifier guardrails | Done | P0 | DOI/arXiv lookup now drops returned rows that do not match requested identifiers. | Off-target adapter rows cannot resolve canonical papers for deterministic identifier lookups. |
| Metadata richness merge (`abstract`, `keywords`, `categories`) | In progress | P0 | Merge + DB persistence are implemented with unit + index coverage (`tests/test_retrieval_deterministic.py::test_merge_includes_abstract_keywords_and_categories`, `tests/test_retrieval_index.py::test_cache_first_appends_query_history_and_keeps_unique_paper`); remaining work is representative multi-connector corpus validation. | For known papers with upstream metadata, resolved output and DB rows preserve informative values. |
| Title ambiguity behavior | Done | P1 | Core ambiguity signaling is implemented and covered (`tests/test_retrieval_deterministic.py::test_title_resolution_ambiguous_returns_no_write_signal`, `tests/test_retrieval_rc1_matrix.py::test_cold_start_matrix_and_cache_replay`); remaining breadth moved to regression fixture-pack work. | Ambiguous title cases reliably return `ambiguous_requires_selection` with ranked candidates. |

### 3.2 Author Metadata

| Task | Status | Priority | Notes | Exit Criteria |
|---|---|---|---|---|
| Cross-source author canonicalization | Todo | P1 | Duplicate people remain possible across adapters when IDs/ORCID are partial or inconsistent. Explicitly deferred as post-RC1 hardening. | Regression sample shows reduced duplicate-author incidence with documented merge rules. |
| Author relation reconciliation on updates | Done | P0 | Issue `DET-META-002` implemented: persistence now reconciles `paper_authors` per paper run and cleans stale `author_orgs` for orphaned/solo-paper authors, with regression coverage for author-list shrink. | Re-running retrieval with changed author roster yields DB links exactly matching latest canonical paper metadata. |
| Cache-hit author fidelity (`source_ids`, affiliations) | Done | P0 | Issue `DET-META-003` implemented: added per-paper author metadata snapshots (`source_ids` + `affiliations`) and cache-hit hydration now restores these fields instead of empty defaults, with regression coverage. | Cache-hit results preserve author/source affiliation fidelity equivalent to latest resolved canonical paper representation. |

### 3.3 DB Schema Hardening and Persistence

| Task | Status | Priority | Notes | Exit Criteria |
|---|---|---|---|---|
| Cache addressability for arXiv queries when canonical paper ID is DOI | Done | P0 | Issue `DET-META-001` implemented: persisted `papers.arxiv_id`, added DB lookup by arXiv ID, and added regression coverage for DOI-canonical/arXiv-query cache hits. | Repeated arXiv query under `cache_first` yields `cache_hit=true` even when canonical paper ID is DOI-based. |
| Canonical paper equivalence guardrails | Done | P1 | Issue `DET-META-004` implemented: title-based equivalence is now conservative (requires both years and rejects venue conflicts), with regression coverage for same-title/missing-year non-merge. | Mixed-title/year fixture set shows no incorrect merges while true duplicates still reconcile. |
| Metadata backfill for legacy index/DB entries | Todo | P1 | Compaction/migration keeps structure but does not enrich old sparse rows automatically. Explicitly deferred as post-RC1 hardening. | One scripted backfill pass enriches existing canonical rows where source metadata is available. |

### 3.4 Cache Strategy and Index Artifacts

| Task | Status | Priority | Notes | Exit Criteria |
|---|---|---|---|---|
| `cache_first` policy behavior (`db -> fast -> consensus fallback`) | In progress | P0 | DB-first + fast-first fallback is implemented and now includes regression coverage for miss-`not_found` and incomplete-fast fallback (`tests/test_retrieval_index.py::test_cache_first_not_found_keeps_index_empty_and_logs_query`, `tests/test_retrieval_index.py::test_cache_first_uses_fast_then_fallback_to_consensus_for_incomplete_paper`). | Policy behavior is deterministic and reproducible for cache hit, cache miss-fast-resolved, miss-fast-incomplete, and miss-not-found paths. |
| Index uniqueness and alias-aware query history | In progress | P0 | Query history append and alias-aware query keys are present, with new mixed-identifier permutation coverage (`tests/test_retrieval_index.py::test_mixed_identifier_permutations_keep_single_canonical_entry`). Remaining work is larger stress corpus. | Repeated mixed DOI/arXiv/title queries keep one canonical paper entry with accurate `queries[]` history and cache-hit flags. |
| Deterministic artifact readability and size bounds | In progress | P0 | Human-facing YAML remains compact and detailed provenance stays in TSV; added repeated-run compactness guard (`tests/test_retrieval_index.py::test_index_artifact_stays_compact_after_repeated_runs`). Longitudinal realistic-project checks still pending. | `deterministic_result.yaml` remains compact and readable after repeated runs with growth bounded by paper/query counts. |

### 3.5 Observability, Regression, and Ops

| Task | Status | Priority | Notes | Exit Criteria |
|---|---|---|---|---|
| CLI retrieval trace quality | Done | P0 | Progress events expose search path, adapter outcomes, cache events, and reconcile/persist milestones. | Retrieval path can be debugged from CLI output without opening artifact files. |
| Fresh-session deterministic smoke suite | In progress | P0 | Clean-workspace matrix coverage now exists via isolated temp-workspace suite (`tests/test_retrieval_rc1_matrix.py::test_cold_start_matrix_and_cache_replay`); expansion into fuller CI matrix is pending. | Cold-start suite recorded for DOI/arXiv alias/title + cache replay checks. |
| Deterministic regression fixture pack | In progress | P1 | Core deterministic regressions are covered (`tests/test_retrieval_deterministic.py`, `tests/test_retrieval_index.py`, `tests/test_retrieval_rc1_matrix.py`); fixture corpus for broader metadata drift remains pending. | Fixture pack added and run in CI for deterministic policy and metadata integrity regressions. |

## 4) Non-Active Modules (Summary Only)

| Module | Next Gate To Open Detailed Board |
|---|---|
| Agentic Meta Info Retrieval | Deterministic retrieval reaches release-candidate quality. |
| Data Backend and RAG | Deterministic metadata schema and cache policy frozen. |
| Paper Context Retrieval | Metadata layer can reliably resolve and cache canonical papers. |
| Paper Context Formatted Extraction | Context retrieval contract is stable. |
| Analysis Skill | Extraction artifacts have stable schema + quality controls. |
| Output Skills | Analysis outputs are stable and versioned. |
| Overall UI Design | Core retrieval/extraction APIs reach stable semantics. |

## 5) Immediate Next Milestone

`M-Deterministic-RC1`: deterministic retrieval is reliable enough for daily use.

Done when:
- DOI/arXiv/title behavior is predictable on benchmark queries.
- arXiv queries reliably hit DB cache regardless of canonical paper ID prefix strategy.
- Author and author-org links in DB remain exact after metadata updates.
- Cache-hit paper metadata does not degrade relative to canonical persisted state.
- Deterministic artifacts remain compact/readable with full provenance retained in TSV logs.
- Deterministic tests and fixture-based regressions are stable in fresh sessions.

## 6) Next Fresh Session Task Queue

Use this queue at the start of the next session:

1. Run cold-start matrix for DOI/arXiv URL/arXiv DOI alias/title exact/title ambiguous under `cache_first`.
2. Validate index/query history stability (`query_key`, `cache_hit`, canonical paper consistency) after recent DB/cache reconciliation changes.
3. Add/refresh fixture corpus to lock policy-mode behavior across `consensus`, `fast`, and `cache_first`.
4. Add explicit migration/compat checks for older DB files that predate `papers.arxiv_id` and `paper_author_metadata`.
5. Review policy docs/README to reflect stricter title-equivalence behavior and expected tradeoffs.
