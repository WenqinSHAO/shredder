from __future__ import annotations

from pathlib import Path


def parse_pdf_stub(pdf_path: Path) -> list[dict]:
    text = f"Parsed content from {pdf_path.name}. This is a stub section for MVP extraction."
    return [
        {"section_id": "s1", "title": "Abstract", "text": text[:200]},
        {"section_id": "s2", "title": "Method", "text": "Method details unavailable in stub parser."},
    ]
