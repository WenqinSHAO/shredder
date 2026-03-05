from __future__ import annotations

from pathlib import Path
from src.utils.yamlx import load


def render_reports(project_path: Path) -> tuple[Path, Path]:
    extraction_dir = project_path / "artifacts" / "extraction"
    items = []
    for f in sorted(extraction_dir.glob("*.yaml")):
        payload = load(f)
        items.append((f.stem, payload.get("data", {})))

    report = project_path / "reports" / "report.md"
    slides = project_path / "reports" / "slides.md"
    report.parent.mkdir(parents=True, exist_ok=True)

    report_lines = ["# Research Report", "", "## Extracted Papers", ""]
    slide_lines = ["---", "marp: true", "---", "# Research Summary", ""]
    for pid, data in items:
        report_lines.append(f"### {pid}")
        for k, v in data.items():
            if isinstance(v, dict) and "value" in v:
                report_lines.append(f"- **{k}**: {v['value']}")
        report_lines.append("")
        slide_lines.append(f"## {pid}")
        slide_lines.append("- Stub extraction complete")

    report.write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    slides.write_text("\n".join(slide_lines) + "\n", encoding="utf-8")
    return report, slides
