from __future__ import annotations


def verify_stub(extraction: dict) -> dict:
    extraction["verification"] = {"status": "stub_ok", "confidence": 0.5}
    return extraction
