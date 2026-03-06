# Implementation Progress Board

Last updated: 2026-03-06

## 1) Program Overview

This board is the single source of truth for delivery progress.  
Detailed task tables are maintained only for modules currently in active implementation.

## 2) Module Progress Bars

| Module | Progress | Status |
|---|---:|---|
| Meta Info Retrieval (deterministic) | `58%` (`██████░░░░`) | In progress |
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
- Merge quality aligned with DB canonical model.
- Debuggable CLI and artifact outputs.

| Task | Status | Priority | Notes | Exit Criteria |
|---|---|---|---|---|
| Title/arXiv resolution reliability | In progress | P0 | arXiv URL normalization hardening landed (`http/https`, `abs/pdf`, version stripping); arXiv DOI alias (`10.48550/arXiv.*`) now resolves through arXiv identity path. Need broader benchmark verification. | Known DOI/arXiv/title benchmark set passes with expected `resolved`/`ambiguous_requires_selection`/`not_found`. |
| CLI debug visibility | Done | P0 | CLI now prints step-by-step search path, adapter timings/errors, cache hit/miss, and reconcile/persist events. | Retrieval path is inspectable from terminal without opening artifacts. |
| Deterministic policy behavior (`consensus`, `fast`, `cache_first`) | In progress | P0 | `cache_first` now prefers DB cache first; on miss it runs `fast` and only falls back to `consensus` when fast returns resolved-but-incomplete paper metadata for DB hardening. Needs broader benchmark verification and docs alignment. | Policy-specific acceptance tests pass and behavior is deterministic across reruns. |
| Cache-first index correctness (`one paper once`) | In progress | P0 | Query history append, request/source cumulative artifacts, alias-aware key matching (`arXiv` <-> `10.48550/arXiv.*`), and equivalent-entry reconciliation are in place; mixed-query edge cases still need focused validation. | Repeated mixed queries append `queries[]`, keep one canonical paper entry, and resolve cache hits reliably across identifier forms. |
| Identifier consistency guardrails for deterministic candidates | In progress | P0 | DOI/arXiv lookup now drops candidates whose returned identifiers do not match the requested identifier, reducing cross-index false positives. | Off-target adapter rows do not generate resolved papers or canonical ID drift for deterministic DOI/arXiv queries. |
| Metadata richness in deterministic output | In progress | P0 | Adapters now propagate `abstract`/`keywords`/`categories`; merge and DB persistence support added. Need coverage checks across connectors/corpus. | Resolved papers include non-empty informative metadata when upstream sources provide it; DB rows persist and return these fields. |
| Deterministic YAML readability/size control | In progress | P1 | Human-facing index now keeps paper summary + trace + source counts only (no per-paper `diagnostics`/`sources` payload); detailed sources stay in `deterministic_sources.tsv`. | `deterministic_result.yaml` remains human-inspectable under repeated runs while detailed provenance remains available via TSV artifacts. |
| Cross-source author canonicalization | Todo | P1 | Duplicate people still possible across heterogeneous IDs/surface names. | Canonical author merge rules reduce duplicate authors for a regression sample set. |
| Provenance and search-trace quality | In progress | P1 | Trace exists; needs cleaner wording and stable schema for downstream tooling. | Trace/provenance schema finalized and documented with fixture examples. |
| Fresh-session deterministic retrieval smoke/regression | Todo | P0 | Need cold-start validation in a clean session/workspace to catch state-carryover assumptions. | Fresh-session checklist executed and recorded (`cache_first`, title/doi/arXiv permutations, expected cache behavior). |
| Backfill existing index entries with richer metadata | Todo | P1 | Compaction migrates structure but does not invent missing abstracts/keywords; old entries need re-resolution pass. | One scripted/project run backfills existing papers where connectors can provide richer metadata. |
| Regression fixture suite for deterministic retrieval | Todo | P1 | Need a pinned corpus for reproducible checks across policy modes and metadata richness expectations. | Fixture pack added and used in CI/unit tests. |

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
- CLI shows full deterministic search/reconcile path directly.
- `cache_first` mode reuses index safely and avoids duplicates across identifier variants.
- Deterministic artifacts are compact/readable and include rich metadata when available.
- Deterministic retrieval tests are stable and reproducible (including fresh-session runs).

## 6) Next Fresh Session Task Queue

Use this queue at the start of the next session:

1. Run cold-start deterministic checks for: DOI, arXiv URL (`http`, `https`, `vN`), arXiv DOI alias (`10.48550/arXiv.*`), title exact, title ambiguous.
2. Validate cache behavior by repeating each query under `cache_first` and confirming `cache_hit=true` in `queries[]`.
3. Confirm summary index shape (`source_count`, `sources_truncated`, compact authors, no `sources`/`diagnostics`) remains stable after multiple runs.
4. Verify metadata backfill quality (`abstract`, `keywords`, `categories`) on a small pinned paper set and record connector gaps.
5. Add/update regression fixtures from the above runs and lock expected statuses/reasons.
