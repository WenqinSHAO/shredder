# Detailed Implementation TODO Checklist

Last updated: 2026-03-05

## 0) Progress Bar

- Overall retrieval-first program: `65%` (`███████░░░`)
- Track A deterministic retrieval: `80%` (`████████░░`)
- Track B open-ended retrieval: `55%` (`██████░░░░`)
- Deferred backlog (parsing/extraction/rendering): `10%` (`█░░░░░░░░░`)

## 1) Replanned Priority

Primary objective is now retrieval reliability and completeness:

1. Deterministic retrieval first:
   - Given a paper title or arXiv URL, retrieve canonical paper metadata plus full author metadata.
   - Persist hardened records to shared KB with provenance.
2. Open-ended retrieval second:
   - Given a user prompt, generate and persist an intermediate candidate-paper list.
   - Then pass candidates through deterministic retrieval pipeline.

All other work (parsing/extraction/rendering) is temporarily lower priority.

## 2) Current State Baseline

- Discovery connectors exist: OpenAlex, Crossref, Semantic Scholar, SearxNG.
- Dedup + canonical provenance mapping is present for discovery rows.
- KB now hardens paper/author/org graph + provenance.
- Deterministic and open retrieval entrypoints are implemented with retrieval artifacts.
- Validation in current environment: `PYTHONPATH=. python3 -m unittest discover -s tests -q` passed (21 tests).

## 3) Reuse Strategy

| Component | Decision | Current integration status |
|---|---|---|
| `pyalex` | Adopt | Adapter added (`src/retrieval/adapters.py`) |
| `semanticscholar` | Adopt | Adapter added (`src/retrieval/adapters.py`) |
| `habanero` | Adopt | Adapter added (`src/retrieval/adapters.py`) |
| `arxiv.py` | Adopt | Adapter added (`src/retrieval/adapters.py`) |
| `openalex-official` | Adapt patterns only | deferred (checkpoint/resume tuning) |
| `paper-qa` | Adapt patterns only | deferred (planner sophistication) |

## 4) Track A - Deterministic Retrieval (Do First)

Goal: deterministic input -> deterministic paper+author KB records.

## A0 - Retrieval contract and CLI/API surface

- [x] Define deterministic retrieval input contract:
  - accepted inputs: exact/near-exact paper title, DOI, arXiv URL/arXiv id.
  - normalized internal key output: canonical `paper_id` (`doi:` > `arxiv:` > stable title-year key).
- [x] Add explicit command/API entrypoint:
  - CLI: `retrieve-paper <project> --title/--doi/--arxiv-url/--arxiv-id`
  - API: `POST /projects/{id}/retrieve/paper`
- [x] Artifact contract for deterministic runs:
  - `artifacts/retrieval/deterministic_request.yaml`
  - `artifacts/retrieval/deterministic_result.yaml`
  - `artifacts/retrieval/deterministic_sources.tsv`
- Acceptance:
  - same input resolves to same canonical paper ID across reruns.
  - failure modes are explicit and actionable.

## A1 - Deterministic paper resolution logic

- [x] Implement resolver strategy with strict precedence:
  1) DOI lookup
  2) arXiv lookup
  3) title-year fuzzy fallback
- [x] Add connector-specific fetch-by-id helpers:
  - `habanero`, `arxiv.py`, `pyalex`, `semanticscholar` adapters.
- [x] Add conflict resolution rules for paper fields:
  - deterministic source precedence and field-level confidence/provenance.
- [x] Persist source snapshots in deterministic source TSV artifact.
- Acceptance:
  - arXiv URL input returns one canonical paper record.
  - known title input returns one canonical paper record (or deterministic no-match).

## A2 - Author metadata retrieval and normalization

- [x] Extend connector normalization to capture author payloads per paper.
- [x] Define normalized author model:
  - `author_id` strategy (ORCID preferred; otherwise source-prefixed stable key)
  - `name`, `aliases`, `orcid`, `source_ids`, `affiliations`, optional `email/homepage` if available.
- [x] Define normalized org model for affiliations:
  - `org_id` strategy (ROR preferred; fallback stable source key), `name`, `country`.
- [x] Add deterministic merge policy for author identities across connectors.
- Acceptance:
  - deterministic retrieval of one paper stores paper + all available authors + affiliations.
  - rerun is idempotent (no duplicate author/org rows).

## A3 - KB hardening for paper-author graph

- [x] Add/confirm KB tables:
  - `authors`, `orgs`, `paper_authors`, `author_orgs`, `provenance`.
- [x] Implement upserts:
  - `upsert_author`, `upsert_org`, `upsert_paper_author`, `upsert_author_org`.
- [x] Ensure referential integrity via resolver/persistence path + tests.
  - every `paper_authors.paper_id` exists in `papers`
  - every `paper_authors.author_id` exists in `authors`
  - provenance `entity_id` always references existing entities.
- [x] Add query helpers:
  - `get_paper_with_authors(paper_id)`
  - `get_author_profile(author_id)`
- Acceptance:
  - one deterministic retrieval call can fully populate and query paper+author graph from KB.

## A4 - Deterministic reliability and tests

- [ ] Wire configurable retry policy through all connectors.
- [x] Add deterministic integration tests:
  - title input -> canonical paper + authors persisted.
  - arXiv URL input -> canonical paper + authors persisted.
  - rerun idempotency and stable IDs.
  - provenance integrity across papers/authors/orgs.
- [x] Add fixtures/mocks to keep tests offline and reproducible.
- Acceptance:
  - deterministic test suite passes reliably without network.

## 5) Track B - Open-Ended Retrieval (After Track A)

Goal: prompt -> candidate list artifact -> deterministic ingestion.

## B0 - Intermediate candidate-list artifact

- [x] Add open-ended retrieval step that produces:
  - `artifacts/retrieval/candidates_raw.tsv`
  - `artifacts/retrieval/candidates_ranked.tsv`
  - `artifacts/retrieval/handoff.tsv`
  - `artifacts/retrieval/candidates_summary.yaml`
- [x] Define candidate fields:
  - `query_used`, `source`, `source_id`, `title`, `year`, `doi`, `arxiv_id`, `url`, `score`, `reason`.
- Acceptance:
  - open-ended run always outputs explicit candidate artifacts, even when empty.

## B1 - Query planning heuristics (LLM-assisted, deterministic envelope)

- [x] Add baseline query planner (heuristic templates) that converts prompt into query set.
- [x] Planner outputs structured plan:
  - connector target, query string, year/venue constraints, expected recall intent.
- [ ] Keep planner bounded by deterministic guardrails:
  - max query count, explicit cost/time limits, strict output schema.
- Acceptance:
  - same prompt + same config yields reproducible query plan.

## B2 - Multi-source retrieval and ranking

- [ ] Execute planned queries across scholarly APIs and optional web search fallback.
- [ ] Dedup and rank candidates with transparent scoring policy.
- [ ] Keep all raw evidence for explainability.
- Acceptance:
  - ranked candidate list is reproducible and inspectable.

## B3 - Handoff to deterministic ingestion

- [x] Select top-N candidates (configurable) for deterministic retrieval ingestion.
- [x] Persist handoff map:
  - candidate row -> canonical paper_id / no-match reason.
- [x] Enforce strict rule: open-ended path does not directly write uncertain entities to KB.

## 6) Deferred Backlog (After Retrieval Tracks)

- [ ] Parsing contract upgrade (structured sections, validators).
- [ ] Extraction contract hardening (confidence/evidence/status verifier).
- [ ] Analysis skills expansion + richer render outputs.
- [ ] Dev UX hardening (`pytest` tooling, Makefile, release docs).

## 7) Recommended Next Execution Order

1. Wire retrieval retry policy and adapter-level backoff config.
2. Improve title disambiguation and confidence scoring.
3. Add richer query planner (LLM optional) with deterministic schema validation.
4. Add checkpoint/resume ingestion patterns inspired by `openalex-official`.
5. Expand coverage and docs for productionization.
