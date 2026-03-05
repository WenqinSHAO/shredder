# Workplan: MVP -> v1

Detailed execution tracking lives in `docs/IMPLEMENTATION_TODO_CHECKLIST.md`.
Current implementation progress bars are maintained in section `0) Progress Bar` of that checklist.

## Milestones

### M0 (Current MVP Skeleton)
- Core project scaffolding + filesystem contracts.
- SQLite KB bootstrap with paper/author/provenance core tables.
- Discovery/parsing/extraction/render stubs runnable from CLI.
- FastAPI skeleton exposing create project/run step/list artifacts.

### M1 (Functional Metadata + Parsing)
- Real connectors for OpenAlex/Crossref/Semantic Scholar with retries + rate limits.
- Dedup and normalization pipeline.
- PDF parsing through GROBID wrapper with section normalization improvements.

### M2 (Schema-Driven Extraction)
- Robust schema loader + validator.
- Section ranking, low-cost extraction, verifier pass with evidence linking.
- Quality scoring and selective re-extraction.

### M3 (Analysis + Rendering)
- Skill registry expansion (taxonomy, methods/outcomes, author graph).
- Plot generation and markdown embedding.
- Quarto/Marp/Pandoc rendering pipeline.

### M4 (v1 Stabilization)
- Better caching/provenance/audit logs.
- Migrations and schema version support.
- Regression test suite, packaging, docs hardening.

## Task Breakdown + Acceptance Criteria

### 1. Project/Workspace bootstrap
- Implement `init project` command.
- Create `project.yaml`, default `specs/*.md`, artifact directories.
- **Acceptance**: running init twice does not destroy user-edited files.

### 2. KB layer
- Create sqlite schema + upsert/search primitives.
- Add provenance writes for insert/update actions.
- **Acceptance**: `kb.sqlite` is auto-created and queryable from CLI/API.

### 3. Discovery layer
- Stub connector interfaces and merged discovery output.
- Produce `raw.tsv` and `deduped.tsv`.
- **Acceptance**: step runs offline with mock fallback and writes both TSV files.

### 4. Parsing
- Add GROBID wrapper interface and local parser fallback.
- Emit normalized `sections.yaml` with minimal section list.
- **Acceptance**: can parse sample PDF path and create valid sections artifact.

### 5. Extraction
- Load user schema (`schema.yaml`), map from sections, write output YAML.
- Add evidence pointers.
- **Acceptance**: `<paper_id>.yaml` exists and validates against extraction schema.

### 6. Render
- Template report and slide outputs from extracted artifacts.
- Optional PDF command hooks.
- **Acceptance**: `reports/report.md` and `reports/slides.md` generated.

### 7. API + Orchestration
- FastAPI endpoints for create/run/list.
- Orchestrator to route step execution.
- **Acceptance**: endpoint-driven run equals CLI run output.

### 8. Skills system
- Manifest-based registry.
- Example skill: trends over time.
- **Acceptance**: skill produces YAML output from extraction results.

## Recommended Libraries / Tools
- **APIs/Metadata**: OpenAlex, Crossref, Semantic Scholar APIs.
- **Search fallback**: SearxNG endpoint connector.
- **Parsing**: GROBID (`docker/compose` optional), `pypdf` fallback.
- **LLM backends**: OpenAI-compatible clients (`openai` SDK), pluggable base class.
- **Validation**: pydantic + PyYAML.
- **Storage**: SQLite (`sqlite3` builtin), optional `sqlite-utils`.
- **Analysis**: pandas, NetworkX, matplotlib/plotnine.
- **Rendering**: Jinja2 templates + Pandoc/Quarto/Marp.
- **CLI/API**: Typer + FastAPI + uvicorn.

## Risks and Mitigations
- API rate limits -> implement caching and exponential backoff.
- PDF parse quality variability -> keep raw parse snapshot + manual edit loop.
- Extraction hallucinations -> mandatory evidence pointers + verifier pass.
- Schema drift -> explicit schema_version fields + migration utilities.
