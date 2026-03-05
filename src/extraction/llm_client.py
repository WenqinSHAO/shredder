from __future__ import annotations


def extract_stub(fields: list[str], sections: list[dict]) -> dict:
    first = sections[0] if sections else {"section_id": "unknown", "text": ""}
    result = {}
    for field in fields:
        result[field] = {
            "value": f"stub_{field}",
            "evidence": {
                "section_id": first.get("section_id", "unknown"),
                "quote": first.get("text", "")[:120],
            },
        }
    return result
