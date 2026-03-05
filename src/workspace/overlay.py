from __future__ import annotations


def preserve_user_overlay(generated: dict, existing: dict | None) -> dict:
    if not existing:
        return generated
    merged = dict(generated)
    merged.setdefault("user_notes", existing.get("user_notes"))
    return merged
