from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from src.utils.yamlx import dump_to_path


def write_extraction(out_path: Path, paper_id: str, data: dict) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "artifact_type": "extraction",
        "schema_version": "0.1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "paper_id": paper_id,
        "step": 60,
        "data": data,
    }
    dump_to_path(out_path, payload)
    return out_path
