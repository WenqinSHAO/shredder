from __future__ import annotations

from importlib import import_module
from importlib.util import find_spec
from pathlib import Path
from typing import Any


class YamlDependencyError(RuntimeError):
    """Raised when PyYAML is not available."""


def _require_yaml_module():
    if find_spec("yaml") is None:
        raise YamlDependencyError(
            "PyYAML is required for YAML read/write operations. "
            "Install dependencies with `pip install -e .` or `pip install pyyaml`."
        )

    try:
        return import_module("yaml")
    except ModuleNotFoundError as exc:
        raise YamlDependencyError(
            "PyYAML is required for YAML read/write operations. "
            "Install dependencies with `pip install -e .` or `pip install pyyaml`."
        ) from exc


def loads(text: str) -> Any:
    yaml = _require_yaml_module()
    data = yaml.safe_load(text)
    return {} if data is None else data


def load(path: Path) -> Any:
    return loads(path.read_text(encoding="utf-8"))


def dumps(data: Any) -> str:
    yaml = _require_yaml_module()
    return yaml.safe_dump(
        data,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
    ).rstrip()


def dump_to_path(path: Path, data: Any) -> None:
    path.write_text(dumps(data) + "\n", encoding="utf-8")
