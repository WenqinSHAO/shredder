# Implementation Progress Board

Last updated: 2026-03-06

## 1) Program Overview

This board is the single source of truth for delivery progress.
Detailed task tables are maintained only for modules currently in active implementation.

## 2) Module Progress Bars

| Module | Progress | Status |
|---|---:|---|
| Meta Info Retrieval (deterministic) | `60%` (`██████░░░░`) | In progress |
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

### 3.0 Review Issues Handoff (Actionable Fix Briefs)

The following issues were confirmed during code review and local repro runs.  
Use these as implementation tickets for a separate fixing agent.

| Issue ID | Severity | Observed Behavior | Likely Root Cause | Primary Touchpoints | Required Fix Direction | Regression Test To Add/Update |
|---|---|---|---|---|---|---|
| `DET-META-001` | P0 | Repeating the same arXiv query under `cache_first` can stay `cache_hit=false` when the resolved canonical `paper_id` is `doi:*`. | DB cache lookup resolves arXiv queries via `arxiv:{id}` and arXiv DOI alias only; canonical DOI IDs are not discoverable from arXiv query path unless DOI is arXiv alias. | `src/orchestrator/retrieval.py` (`_load_cached_paper_from_db`, `run_retrieve_paper`), `src/retrieval/service.py` (`stable_paper_id`), `src/kb/store.py` (papers schema/query helpers). | Add an arXiv-addressable lookup path for canonical DOI papers. Options: add a dedicated `arxiv_id` column/index in `papers`, or maintain a deterministic identifier alias mapping table. Ensure DB-first cache resolution checks this path before declaring miss. | New test: first retrieval by arXiv ID returns DOI canonical `paper_id`; second retrieval by same arXiv ID under `cache_first` must produce `cache_hit=true` and zero adapter calls. |
| `DET-META-002` | P0 | Re-resolving a paper after author list shrinks leaves stale `paper_authors` rows in DB. | Persistence layer only upserts (`INSERT OR REPLACE`) links and never removes links absent from latest canonical author set. | `src/orchestrator/retrieval.py` (`_persist_resolved`), `src/kb/store.py` (`upsert_paper_author`, `upsert_author_org`). | Implement relation reconciliation for `paper_authors` and `author_orgs`: compute current canonical edges, delete stale edges, then upsert current edges. Keep this operation idempotent per run. | New test: retrieval #1 writes 2 authors; retrieval #2 writes 1 author for same paper; DB must contain exactly 1 `paper_authors` row afterward. |
| `DET-META-003` | P0 | Cache-hit responses lose author richness (`source_ids`, affiliation details/count fidelity), compared with fresh resolve path. | DB hydration path rebuilds authors from `papers + paper_authors + authors` only and hardcodes empty `source_ids`/affiliations. | `src/orchestrator/retrieval.py` (`_db_payload_to_paper`, `_load_cached_paper_from_db`), `src/kb/store.py` (`get_paper_with_authors`, schema). | Persist and hydrate minimum author metadata needed for deterministic fidelity on cache hits. Options: store canonical author payload JSON per paper snapshot, or extend relational model to include source IDs and affiliation links with deterministic retrieval provenance. | New test: first run writes author `source_ids`/affiliations; second `cache_first` hit must return equivalent author metadata fields (or explicit, documented canonical subset with no silent loss). |
| `DET-META-004` | P1 | Equivalent paper merge may over-merge records by normalized title when one side has missing year. | `_papers_equivalent` allows title-only equivalence if years are not both present/non-equal. | `src/orchestrator/retrieval.py` (`_papers_equivalent`, `_find_equivalent_paper_entry`). | Tighten equivalence guardrails: require stronger evidence (shared DOI/arXiv/alias) or add conservative constraints for title-based fallback (e.g., venue/year confidence checks). | New fixture test: same normalized title but different true papers should not collapse into one canonical entry. |

Suggested implementation order:
1. `DET-META-001` (cache correctness blocker for deterministic policy).
2. `DET-META-002` (DB integrity blocker).
3. `DET-META-003` (cache-hit metadata quality blocker).
4. `DET-META-004` (quality hardening).

### 3.1 Paper Metadata

| Task | Status | Priority | Notes | Exit Criteria |
|---|---|---|---|---|
| Identifier determinism (DOI/arXiv/title) | In progress | P0 | URL normalization + arXiv DOI alias handling is implemented. Need benchmark sweep for mixed identifier entrypoints. | Pinned benchmark set passes expected `resolved`/`ambiguous_requires_selection`/`not_found` outcomes across DOI/arXiv/title inputs. |
| Candidate identifier guardrails | Done | P0 | DOI/arXiv lookup now drops returned rows that do not match requested identifiers. | Off-target adapter rows cannot resolve canonical papers for deterministic identifier lookups. |
| Metadata richness merge (`abstract`, `keywords`, `categories`) | In progress | P0 | Merge + DB persistence exist; still need connector coverage checks on representative corpus. | For known papers with upstream metadata, resolved output and DB rows preserve informative values. |
| Title ambiguity behavior | In progress | P1 | Ambiguity signaling is in place; needs fixture coverage and downstream handling contract lock. | Ambiguous title cases reliably return `ambiguous_requires_selection` with ranked candidates. |

### 3.2 Author Metadata

| Task | Status | Priority | Notes | Exit Criteria |
|---|---|---|---|---|
| Cross-source author canonicalization | Todo | P1 | Duplicate people remain possible across adapters when IDs/ORCID are partial or inconsistent. | Regression sample shows reduced duplicate-author incidence with documented merge rules. |
| Author relation reconciliation on updates | Done | P0 | Issue `DET-META-002` implemented: persistence now reconciles `paper_authors` per paper run and cleans stale `author_orgs` for orphaned/solo-paper authors, with regression coverage for author-list shrink. | Re-running retrieval with changed author roster yields DB links exactly matching latest canonical paper metadata. |
| Cache-hit author fidelity (`source_ids`, affiliations) | Done | P0 | Issue `DET-META-003` implemented: added per-paper author metadata snapshots (`source_ids` + `affiliations`) and cache-hit hydration now restores these fields instead of empty defaults, with regression coverage. | Cache-hit results preserve author/source affiliation fidelity equivalent to latest resolved canonical paper representation. |

### 3.3 DB Schema Hardening and Persistence

| Task | Status | Priority | Notes | Exit Criteria |
|---|---|---|---|---|
| Cache addressability for arXiv queries when canonical paper ID is DOI | Done | P0 | Issue `DET-META-001` implemented: persisted `papers.arxiv_id`, added DB lookup by arXiv ID, and added regression coverage for DOI-canonical/arXiv-query cache hits. | Repeated arXiv query under `cache_first` yields `cache_hit=true` even when canonical paper ID is DOI-based. |
| Canonical paper equivalence guardrails | Done | P1 | Issue `DET-META-004` implemented: title-based equivalence is now conservative (requires both years and rejects venue conflicts), with regression coverage for same-title/missing-year non-merge. | Mixed-title/year fixture set shows no incorrect merges while true duplicates still reconcile. |
| Metadata backfill for legacy index/DB entries | Todo | P1 | Compaction/migration keeps structure but does not enrich old sparse rows automatically. | One scripted backfill pass enriches existing canonical rows where source metadata is available. |

### 3.4 Cache Strategy and Index Artifacts

| Task | Status | Priority | Notes | Exit Criteria |
|---|---|---|---|---|
| `cache_first` policy behavior (`db -> fast -> consensus fallback`) | In progress | P0 | DB-first + fast-first fallback is implemented; incomplete-fast fallback to consensus is implemented. Needs broader policy-mode acceptance suite. | Policy behavior is deterministic and reproducible for cache hit, cache miss-fast-resolved, miss-fast-incomplete, and miss-not-found paths. |
| Index uniqueness and alias-aware query history | In progress | P0 | Query history append and alias-aware query keys are present; mixed identifier permutations need broader stress tests. | Repeated mixed DOI/arXiv/title queries keep one canonical paper entry with accurate `queries[]` history and cache-hit flags. |
| Deterministic artifact readability and size bounds | In progress | P1 | Human-facing YAML is compact; detailed provenance is in TSV artifacts. Need longitudinal size checks on realistic projects. | `deterministic_result.yaml` remains compact and readable after repeated runs with growth bounded by paper/query counts. |

### 3.5 Observability, Regression, and Ops

| Task | Status | Priority | Notes | Exit Criteria |
|---|---|---|---|---|
| CLI retrieval trace quality | Done | P0 | Progress events expose search path, adapter outcomes, cache events, and reconcile/persist milestones. | Retrieval path can be debugged from CLI output without opening artifact files. |
| Fresh-session deterministic smoke suite | Todo | P0 | Need clean-workspace runs to validate no hidden state assumptions. | Cold-start suite recorded for DOI/arXiv alias/title + cache replay checks. |
| Deterministic regression fixture pack | Todo | P1 | Tests cover core path; fixture corpus for mixed identifiers/metadata drift still missing. | Fixture pack added and run in CI for deterministic policy and metadata integrity regressions. |

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
