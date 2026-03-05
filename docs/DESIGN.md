# Design: Local-First YAML-Centric Research Pipeline

## 1) Goals
- Build an agentic pipeline that discovers, enriches, parses, extracts, analyzes, and renders research outputs for user-defined themes.
- Keep user-facing artifacts editable and durable as YAML/Markdown/TSV.
- Support local-first execution with a project workspace + shared cross-project KB.
- Provide pluggable LLM backends via OpenAI-compatible interfaces.
- Make every step promptable with free-text `StepSpec` markdown and validated structured outputs.

## 2) Non-goals (Iteration 1)
- Perfect citation graph completeness across all venues.
- Full-text deep semantic extraction quality parity with human experts.
- Distributed execution and multi-user permissioning.
- Hard real-time sync between many concurrent editors.

## 3) Storage Model (Local-First)

### 3.1 Project Workspace (Plane A)
Per-project, versionable directory (e.g., `workspace/<project>/`) containing:
- `project.yaml` (project metadata, constraints, selected schemas)
- `specs/*.md` (StepSpec prompts for each pipeline stage)
- `artifacts/**` hardened step outputs
- `inputs/` user-provided PDFs and references
- `reports/` generated report/slide/pdf outputs

All files are editable by user. Reruns should preserve user edits via overlay semantics.

### 3.2 Shared KB (Plane B)
Cross-project shared data in host-visible path:
- `kb/kb.sqlite` as system of record for paper/author/org/provenance
- Optional `kb/cache/` for fetched metadata snapshots and normalized copies

### 3.3 Cache
`cache/` for ephemeral downloads or parse intermediates (HTML, API responses, parsed XML). Cache may be dropped without losing canonical artifacts.

## 4) Data Model

### 4.1 YAML Representation

#### Paper (`paper.yaml` fragment)
```yaml
paper_id: "doi:10.1145/1234567"
title: "Example Paper"
venue: "NSDI"
year: 2024
doi: "10.1145/1234567"
abstract: "..."
authors:
  - author_id: "orcid:0000-0001-..."
    name: "Jane Doe"
    affiliations:
      - org_id: "ror:05xyz"
        name: "Example University"
urls:
  pdf: "https://.../paper.pdf"
  html: "https://..."
provenance:
  sources: ["openalex", "crossref"]
  fetched_at: "2026-01-01T00:00:00Z"
```

#### Author
```yaml
author_id: "orcid:..."
name: "Jane Doe"
aliases: ["J. Doe"]
affiliations:
  - org_id: "ror:05xyz"
    role: "Professor"
```

#### Org
```yaml
org_id: "ror:05xyz"
name: "Example University"
country: "US"
aliases: ["Example U"]
```

### 4.2 SQLite Tables (Initial)
- `papers(id PRIMARY KEY, title, venue, year, doi UNIQUE, abstract, pdf_url, html_url, created_at, updated_at)`
- `authors(id PRIMARY KEY, name, orcid UNIQUE, created_at, updated_at)`
- `orgs(id PRIMARY KEY, name, ror UNIQUE, country, created_at, updated_at)`
- `paper_authors(paper_id, author_id, position, PRIMARY KEY(paper_id, author_id))`
- `author_orgs(author_id, org_id, role, PRIMARY KEY(author_id, org_id))`
- `provenance(id PRIMARY KEY, entity_type, entity_id, source, source_key, confidence, fetched_at, raw_ref)`

## 5) Pipeline Steps and Contracts
Suggested step numbers (10..70) with hardened outputs:

- **10-init**: Create project scaffold.  
  Output: `project.yaml`, `specs/*.md`, baseline directories.
- **20-discovery**: Query OpenAlex/Crossref/S2/SearxNG + venue filters.  
  Output: `artifacts/discovery/raw.tsv`, `artifacts/discovery/deduped.tsv`.
- **30-enrichment**: Merge metadata, normalize ids/authors/orgs, upsert KB.  
  Output: `artifacts/enrichment/papers.yaml`, `authors.yaml`, `orgs.yaml`.
- **40-fetch**: Download PDFs/HTML into workspace cache/inputs.  
  Output: `artifacts/fetch/files.tsv`.
- **50-parse**: Parse PDF/HTML to normalized sections.  
  Output: `artifacts/parsing/<paper_id>/sections.yaml`.
- **60-extract**: Apply user schema to sections using low-cost + verifier passes.  
  Output: `artifacts/extraction/<paper_id>.yaml` + evidence pointers.
- **65-analyze**: Run pluggable analysis skills.  
  Output: `artifacts/analysis/*.yaml` and optional plot files.
- **70-render**: Generate report/slides/pdf.  
  Output: `reports/report.md`, `reports/slides.md`, optional pdf.

## 6) Schema and Versioning
- Every artifact carries:
```yaml
meta:
  artifact_type: "sections"
  schema_version: "0.1.0"
  generated_at: "..."
  step: 50
```
- Schema definitions live in `schemas/*.yaml`.
- Backward compatibility via migrators (`from_version -> to_version`) when schema evolves.
- Strict validation using pydantic models and/or `jsonschema` generated from YAML schema definitions.

## 7) Provenance and Dedup Rules
- Dedup priority: DOI > arXiv id > title+year fuzzy match.
- Canonical paper ID strategy: `doi:<doi>` if DOI exists else provider-prefixed IDs.
- Maintain provenance rows for each imported field with source and confidence.
- Preserve source conflicts instead of destructive overwrite; store selected canonical value + alternatives.

## 8) Cost-Control Extraction Strategy
1. **Section selection**: rank sections relevant to schema fields (title/abstract/method/results/conclusion first).
2. **Budget-aware pass**: low-cost model extracts structured candidate YAML.
3. **Verifier pass**: smaller follow-up prompt checks evidence pointers + schema completeness.
4. **Evidence requirements**:
   - each nontrivial field includes `evidence` with section id and quote snippet.
   - unresolved fields explicitly marked `unknown` with reason.
5. **Fallback**: if confidence below threshold, escalate to higher-quality model only for missing fields.

## 9) Skills Plugin System
- Skills in `src/analysis_skills/` with manifest and typed I/O.

Example manifest (`skills/trends.yaml`):
```yaml
name: trends_over_time
version: 0.1.0
entrypoint: src.analysis_skills.trends:run
input_schema: schemas/skills/trends_input.yaml
output_schema: schemas/skills/trends_output.yaml
```

Execution flow:
1. Resolve skill by name in registry.
2. Validate input YAML against schema.
3. Run Python entrypoint.
4. Validate + persist output artifact YAML.

## 10) Backend API Sketch (FastAPI)
- `POST /projects` -> create project
- `POST /projects/{project_id}/steps/{step}` -> run a step
- `GET /projects/{project_id}/artifacts` -> list artifacts
- `GET /healthz` -> readiness

## 11) CLI/TUI Entrypoints
CLI (`typer`) examples:
- `python -m src.cli init <project_name>`
- `python -m src.cli run-step <project_name> parsing --paper-id sample --pdf path/to.pdf`
- `python -m src.cli run-step <project_name> extraction --paper-id sample`
- `python -m src.cli render <project_name>`

Future TUI can wrap the same orchestration service with step controls and artifact previews.
