from __future__ import annotations

from pathlib import Path
import csv

from src.kb import init_kb, upsert_paper
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


def run_discovery(project_id: str) -> Path:
    pdir = project_dir(project_id)
    pmeta = load(pdir / "project.yaml")
    theme = pmeta.get("theme", "unknown")
    raw_path = pdir / "artifacts" / "discovery" / "raw.tsv"
    dedup_path = pdir / "artifacts" / "discovery" / "deduped.tsv"
    rows = [
        {"paper_id": "doi:10.0000/example1", "title": f"{theme} Systems Paper", "venue": "NSDI", "year": "2024", "doi": "10.0000/example1"},
        {"paper_id": "doi:10.0000/example1", "title": f"{theme} Systems Paper", "venue": "NSDI", "year": "2024", "doi": "10.0000/example1"},
        {"paper_id": "doi:10.0000/example2", "title": f"{theme} ML Systems Paper", "venue": "MLSys", "year": "2023", "doi": "10.0000/example2"},
    ]
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    with raw_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()), delimiter="\t")
        w.writeheader()
        w.writerows(rows)

    dedup = {r["paper_id"]: r for r in rows}
    with dedup_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()), delimiter="\t")
        w.writeheader()
        w.writerows(dedup.values())

    init_kb()
    for row in dedup.values():
        upsert_paper({
            "id": row["paper_id"],
            "title": row["title"],
            "venue": row["venue"],
            "year": int(row["year"]),
            "doi": row["doi"],
        }, source="mock_discovery")
    return dedup_path


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
