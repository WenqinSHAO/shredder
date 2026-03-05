from __future__ import annotations

from pathlib import Path
from src.kb import add_provenance, init_kb, upsert_paper
from src.parsing.grobid_wrapper import parse_pdf_stub
from src.parsing.normalize import write_sections_yaml
from src.extraction.loader import load_schema
from src.extraction.selector import select_sections
from src.extraction.llm_client import extract_stub
from src.extraction.verifier import verify_stub
from src.extraction.writer import write_extraction
from src.render.generator import render_reports
from src.utils.paths import project_dir
from src.utils.yamlx import load
from src.orchestrator.discovery import run_discovery_aggregation


def run_discovery(project_id: str) -> Path:
    pdir = project_dir(project_id)
    pmeta = load(pdir / "project.yaml")
    raw_rows, dedup_rows = run_discovery_aggregation(pdir, pmeta)

    init_kb()
    for row in dedup_rows:
        upsert_paper({
            "id": row["paper_id"],
            "title": row["title"],
            "venue": row["venue"],
            "year": int(row["year"]) if str(row.get("year", "")).isdigit() else None,
            "doi": row.get("doi") or None,
            "html_url": row.get("url") or None,
        })

    for row in raw_rows:
        paper_id = row.get("paper_id")
        if not paper_id:
            continue
        add_provenance(
            entity_id=paper_id,
            source=row.get("source", "unknown"),
            source_key=row.get("source_id") or row.get("doi") or row.get("arxiv_id") or "",
            raw_ref={"title": row.get("title"), "year": row.get("year")},
        )

    return pdir / "artifacts" / "discovery" / "deduped.tsv"


def run_parsing(project_id: str, paper_id: str, pdf_path: str) -> Path:
    pdir = project_dir(project_id)
    sections = parse_pdf_stub(Path(pdf_path))
    out = pdir / "artifacts" / "parsing" / paper_id / "sections.yaml"
    return write_sections_yaml(out, paper_id, sections)


def run_extraction(project_id: str, paper_id: str) -> Path:
    pdir = project_dir(project_id)
    sections_path = pdir / "artifacts" / "parsing" / paper_id / "sections.yaml"
    schema_path = pdir / "schema.yaml"
    sections_payload = load(sections_path)
    schema = load_schema(schema_path)
    fields = schema.get("fields", [])
    parsed_sections = []
    for raw in sections_payload.get("sections", []):
        sid, title, text = (raw.split("|", 2) + ["", "", ""])[:3]
        parsed_sections.append({"section_id": sid, "title": title, "text": text})
    selected = select_sections(parsed_sections)
    extracted = extract_stub(fields, selected)
    verified = verify_stub(extracted)
    out = pdir / "artifacts" / "extraction" / f"{paper_id}.yaml"
    return write_extraction(out, paper_id, verified)


def run_render(project_id: str) -> tuple[Path, Path]:
    return render_reports(project_dir(project_id))
