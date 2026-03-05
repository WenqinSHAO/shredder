# Codex Fresh-Session Prompts

Use these prompts directly in a new Codex session to continue implementation.

## Prompt 1 — Stabilize foundations and remove brittle YAML parser

```text
You are continuing work in /workspace/shredder.

Context:
- Project is a local-first, YAML-centric research pipeline.
- The current MVP has docs + scaffolding + stub pipeline flow.
- It currently uses a custom lightweight YAML parser (src/utils/yamlx.py) because package install was blocked earlier.

Your task:
1) Replace custom YAML parsing with robust PyYAML usage behind a narrow adapter interface.
2) Keep user-facing artifacts YAML/Markdown/TSV/SQLite only (no JSON outputs for users).
3) Add graceful fallback error messages if YAML dependency is unavailable.
4) Add tests for round-trip loading/writing for:
   - project.yaml
   - sections.yaml
   - extraction artifact YAML
5) Ensure current CLI flow still works end-to-end:
   init -> discovery -> parsing -> extraction -> render.

Constraints:
- Preserve local-first paths and existing artifact contracts.
- Do not break current command UX.
- Update README with dependency/bootstrap guidance.

Deliver:
- Code changes
- Tests
- Updated docs
- Commands run + results
```

## Prompt 2 — Implement real discovery connectors + dedup

```text
Continue in /workspace/shredder.

Goal:
Upgrade step 20 discovery from mock data to real connector-based retrieval with deterministic offline fallback.

Implement:
1) Connector clients for OpenAlex, Crossref, Semantic Scholar with shared interface:
   search(theme, venues, year_min, year_max, limit) -> normalized paper candidates.
2) A discovery aggregator that:
   - queries enabled connectors
   - stores raw per-source TSVs
   - produces merged raw.tsv
   - produces deduped.tsv via DOI > arXiv > title+year fuzzy rules.
3) Provenance persistence in kb.sqlite for each paper/source pair.
4) Configurable connector toggles and rate-limit settings in project.yaml.
5) Offline fallback mode (if network unavailable) that emits deterministic sample rows and clearly marks source=mock.

Validation:
- Add unit tests for dedup rules.
- Add a CLI smoke test command sequence in docs.

Do not add hidden Docker volumes; keep host-visible storage.
```

## Prompt 3 — Make parsing + extraction contract robust

```text
Continue in /workspace/shredder.

Goal:
Strengthen step 50 parsing and step 60 extraction so outputs are schema-driven and evidence-backed.

Implement:
1) Parsing:
   - Keep stub fallback, but structure sections.yaml as:
     meta + sections[] with section_id/title/text/page_span/source_offsets.
   - Optional GROBID integration with explicit endpoint config.
2) Extraction:
   - Load user schema.yaml fields and requiredness.
   - Section selection heuristic (title/abstract/method/results/conclusion priority).
   - Extraction output per field:
     value, confidence, evidence[{section_id, quote}], status.
   - Verifier pass that flags missing evidence/low confidence.
3) Validation:
   - Add schema checks for sections and extraction artifacts.
   - Write clear errors into artifacts/errors/*.md when validation fails.

Keep cost-aware architecture for pluggable OpenAI-compatible backend clients.
```

## Prompt 4 — Shared KB model upgrade

```text
Continue in /workspace/shredder.

Goal:
Upgrade shared KB into a reusable cross-project store for papers/authors/orgs + joins + provenance.

Implement:
1) SQLite migrations for:
   papers, authors, orgs, paper_authors, author_orgs, provenance.
2) Upsert functions for all entities and join tables.
3) Search APIs:
   - search_papers(query, venue, year_range)
   - get_paper_with_authors(paper_id)
   - get_author_collab_graph(seed_author, depth)
4) CLI subcommands:
   - kb search-papers
   - kb show-paper
5) Export helper:
   - write selected KB query results as TSV + YAML under workspace/<project>/artifacts/kb_exports/

Add tests for migrations and upsert idempotency.
```

## Prompt 5 — Rendering and analysis skill extensibility

```text
Continue in /workspace/shredder.

Goal:
Make analysis skills and rendering pipeline genuinely useful for literature synthesis.

Implement:
1) Skill runtime:
   - manifest-based loading
   - typed input/output validation
   - skill execution logs under artifacts/analysis/logs/
2) Add at least two skills:
   - trends_over_time
   - problem_taxonomy_from_extractions
3) Render improvements:
   - report.md with sections: scope, dataset summary, key findings, evidence table, open questions.
   - slides.md with Marp frontmatter and concise bullets.
   - optional PDF render command wrappers (pandoc/quarto) with graceful “tool not installed” warnings.
4) Plot outputs:
   - produce at least one chart artifact (e.g., year histogram) and embed in report markdown.

Ensure all generated outputs remain editable and rerun-safe.
```

## Prompt 6 — End-to-end hardening and release prep

```text
Continue in /workspace/shredder.

Goal:
Prepare a v0.2 hardened release from the current MVP.

Deliver:
1) Test suite:
   - unit tests for connectors/dedup/schema validation
   - integration test for full CLI flow
2) Dev UX:
   - Makefile targets (init, test, lint, demo)
   - clearer error messages and exit codes
3) Docs:
   - architecture quick diagram in markdown
   - "first 30 minutes" tutorial
4) Release notes:
   - what’s production-ready vs stub
   - migration notes for existing workspace data

Keep implementation local-first and avoid user-facing JSON artifacts.
```

## Short bootstrap prompt (if you only want one)

```text
Continue the /workspace/shredder project toward a robust v0.2.
Prioritize: (1) remove brittle YAML parser, (2) real discovery connectors + dedup + provenance, (3) structured sections/extraction artifacts with evidence + validation, (4) stronger KB schema + joins, (5) improved report/slides output.
Maintain local-first workspace+KB model and YAML/Markdown/TSV/SQLite artifact policy.
Run tests and provide a concise change summary with commands/results.
```
