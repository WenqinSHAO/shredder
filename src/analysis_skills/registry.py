from __future__ import annotations

from .trends_over_time import run as trends_over_time

SKILLS = {
    "trends_over_time": trends_over_time,
}


def run_skill(name: str, input_data: dict) -> dict:
    if name not in SKILLS:
        raise ValueError(f"Unknown skill: {name}")
    return SKILLS[name](input_data)
