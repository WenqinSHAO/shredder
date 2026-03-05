from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = ROOT / "workspace"
KB_DIR = ROOT / "kb"
KB_PATH = KB_DIR / "kb.sqlite"


def project_dir(project_id: str) -> Path:
    return WORKSPACE_ROOT / project_id
