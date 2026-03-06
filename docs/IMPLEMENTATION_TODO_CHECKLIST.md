# Implementation Progress Board

Last updated: 2026-03-06

## 1) Program Overview

This board is the single source of truth for delivery progress.  
Detailed task tables are maintained only for modules currently in active implementation.

## 2) Module Progress Bars

| Module | Progress | Status |
|---|---:|---|
| Meta Info Retrieval (deterministic) | `35%` (`███░░░░░░░`) | In progress |
| Agentic Meta Info Retrieval | `15%` (`█░░░░░░░░░`) | Not started |
| Data Backend and RAG | `20%` (`██░░░░░░░░`) | Planned |
| Paper Context Retrieval | `5%` (`░░░░░░░░░░`) | Not started |
| Paper Context Formatted Extraction | `10%` (`█░░░░░░░░░`) | Not started |
| Analysis Skill | `10%` (`█░░░░░░░░░`) | Not started |
| Output Skills (report/slides/render) | `15%` (`█░░░░░░░░░`) | Early scaffold |
| Overall UI Design | `5%` (`░░░░░░░░░░`) | Not started |
| **Overall Program** | **`18%` (`██░░░░░░░░`)** | **In progress** |

## 3) Active Section Task Board

## Meta Info Retrieval (Deterministic) - Active

Scope:
- Stable deterministic resolution for DOI/arXiv/title.
- Merge quality aligned with DB canonical model.
- Debuggable CLI and artifact outputs.

| Task | Status | Priority | Notes | Exit Criteria |
|---|---|---|---|---|
| Title search resolution reliability | In progress | P0 | Current quality is inconsistent; ambiguous/no-match behavior needs tightening. | Known title benchmark set passes with expected `resolved`/`ambiguous_requires_selection`/`not_found`. |
| CLI debug visibility | In progress | P0 | Diagnostics are in YAML, but terminal feedback still weak for common misuse. | CLI prints concise warnings (e.g., DOI looks like arXiv URL) without requiring YAML inspection. |
| Deterministic policy behavior (`consensus`, `fast`, `cache_first`) | In progress | P0 | Implemented but needs stricter behavior checks and docs alignment. | Policy-specific acceptance tests pass and behavior is deterministic across reruns. |
| Cache-first index correctness (`one paper once`) | In progress | P0 | Single YAML index implemented; needs edge-case validation under repeated mixed queries. | Repeated project queries append `queries[]` and never duplicate canonical paper entries. |
| Cross-source author canonicalization | Todo | P1 | Duplicate people still possible across heterogeneous IDs/surface names. | Canonical author merge rules reduce duplicate authors for a regression sample set. |
| Provenance and search-trace quality | In progress | P1 | Trace exists; needs cleaner wording and stable schema for downstream tooling. | Trace/provenance schema finalized and documented with fixture examples. |
| Regression fixture suite for deterministic retrieval | Todo | P1 | Need a pinned corpus for reproducible checks across policy modes. | Fixture pack added and used in CI/unit tests. |

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
- Title search behavior is predictable on benchmark queries.
- CLI shows useful debug hints directly.
- `cache_first` mode reuses index safely and avoids duplicates.
- Deterministic retrieval tests are stable and reproducible.
