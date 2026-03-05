from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from src.utils.yamlx import dump_to_path


def write_sections_yaml(out_path: Path, paper_id: str, sections: list[dict]) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "artifact_type": "sections",
        "schema_version": "0.1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "paper_id": paper_id,
        "step": 50,
        "sections": [f"{s['section_id']}|{s['title']}|{s['text']}" for s in sections],
    }
    dump_to_path(out_path, payload)
    return out_path
