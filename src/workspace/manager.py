from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import shutil

from src.utils.paths import project_dir
from src.utils.yamlx import dump_to_path

DEFAULT_SPECS = {
    "10-init.md": "# Step 10 Init\nDescribe initialization behavior and overrides.\n",
    "20-discovery.md": "# Step 20 Discovery\nDescribe venue/time constraints and discovery strategy.\n",
    "50-parse.md": "# Step 50 Parse\nDescribe parsing preferences and section normalization.\n",
    "60-extraction.md": "# Step 60 Extraction\nDescribe extraction quality, cost limits, and evidence constraints.\n",
    "70-render.md": "# Step 70 Render\nDescribe reporting style and audience.\n",
}


def init_project(project_id: str, theme: str | None = None) -> Path:
    base = project_dir(project_id)
    dirs = [
        base / "specs",
        base / "artifacts" / "discovery",
        base / "artifacts" / "retrieval",
        base / "artifacts" / "parsing",
        base / "artifacts" / "extraction",
        base / "artifacts" / "analysis",
        base / "inputs",
        base / "reports",
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)

    project_yaml = base / "project.yaml"
    if not project_yaml.exists():
        dump_to_path(
            project_yaml,
            {
                "project_id": project_id,
                "theme": theme or "TODO define research theme",
                "schema": "examples/schema.yaml",
                "year_min": 2020,
                "year_max": datetime.now(timezone.utc).year,
                "venues": ["SIGCOMM", "NSDI", "HPCA", "OSDI", "MLSys", "ASPLOS"],
                "discovery": {
                    "limit": 25,
                    "connectors": {
                        "openalex": {"enabled": True, "timeout_s": 8.0},
                        "crossref": {"enabled": True, "timeout_s": 8.0},
                        "semantic_scholar": {"enabled": True, "timeout_s": 8.0},
                        "searxng": {"enabled": False, "timeout_s": 8.0, "base_url": ""},
                    },
                    "rate_limits": {
                        "openalex": 1.0,
                        "crossref": 1.0,
                        "semantic_scholar": 1.0,
                        "searxng": 1.0,
                    },
                },
                "retrieval": {
                    "open_enabled": False,
                    "open_top_n": 5,
                    "deterministic": {"ambiguity_delta": 0.05},
                    "adapters": {
                        "habanero": {"enabled": True},
                        "arxiv": {"enabled": True},
                        "pyalex": {"enabled": True},
                        "semanticscholar": {"enabled": True},
                    },
                },
            },
        )

    for name, content in DEFAULT_SPECS.items():
        spec_path = base / "specs" / name
        if not spec_path.exists():
            spec_path.write_text(content, encoding="utf-8")

    schema_target = base / "schema.yaml"
    if not schema_target.exists():
        shutil.copyfile(Path("examples/schema.yaml"), schema_target)

    return base


def list_artifacts(project_id: str) -> list[str]:
    base = project_dir(project_id) / "artifacts"
    if not base.exists():
        return []
    return sorted(str(p.relative_to(project_dir(project_id))) for p in base.rglob("*") if p.is_file())
