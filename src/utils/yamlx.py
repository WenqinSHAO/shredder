from __future__ import annotations

from pathlib import Path


def _parse_scalar(value: str):
    value = value.strip()
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    if value.startswith("'") and value.endswith("'"):
        return value[1:-1]
    if value.isdigit():
        return int(value)
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    return value


def loads(text: str):
    lines = [ln.rstrip("\n") for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    root = {}
    stack = [(-1, root)]

    for ln in lines:
        indent = len(ln) - len(ln.lstrip(" "))
        stripped = ln.strip()

        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]

        if stripped.startswith("- "):
            item = stripped[2:]
            if isinstance(parent, list):
                parent.append(_parse_scalar(item))
            continue

        if ":" not in stripped:
            continue
        key, rest = stripped.split(":", 1)
        key, rest = key.strip(), rest.strip()

        if rest == "":
            # decide list vs dict by peeking next line
            container = {}
            # heuristic: keys ending with 's' often lists in our files, plus known key
            if key in {"fields", "sections", "venues", "authors", "affiliations", "sources", "skills"}:
                container = []
            if isinstance(parent, dict):
                parent[key] = container
            elif isinstance(parent, list):
                parent.append({key: container})
                container = parent[-1][key]
            stack.append((indent, container))
        else:
            val = _parse_scalar(rest)
            if isinstance(parent, dict):
                parent[key] = val
            elif isinstance(parent, list):
                parent.append({key: val})
    return root


def load(path: Path):
    return loads(path.read_text(encoding="utf-8"))


def _dump_scalar(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    if any(ch in s for ch in [":", "#", "[", "]"]):
        return f'"{s}"'
    return s


def dumps(data, indent: int = 0) -> str:
    lines = []
    sp = " " * indent
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, (dict, list)):
                lines.append(f"{sp}{k}:")
                lines.append(dumps(v, indent + 2))
            else:
                lines.append(f"{sp}{k}: {_dump_scalar(v)}")
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, (dict, list)):
                lines.append(f"{sp}-")
                lines.append(dumps(item, indent + 2))
            else:
                lines.append(f"{sp}- {_dump_scalar(item)}")
    return "\n".join(lines)


def dump_to_path(path: Path, data) -> None:
    path.write_text(dumps(data) + "\n", encoding="utf-8")
