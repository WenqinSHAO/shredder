from __future__ import annotations


def select_sections(sections: list[dict], max_sections: int = 3) -> list[dict]:
    return sections[:max_sections]
