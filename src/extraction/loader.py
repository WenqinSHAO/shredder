from __future__ import annotations

from pathlib import Path
from src.utils.yamlx import load


def load_schema(path):
    return load(Path(path))
