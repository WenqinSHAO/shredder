from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.orchestrator.agentic_search import _merge_fetched_records


# Bridge loop-owned runtime state into the mutable payload used by action executors and back.
@dataclass
class _ActionRuntime:
    url_hits: list[dict[str, Any]] = field(default_factory=list)
    extract_state_by_url: dict[str, dict[str, Any]] = field(default_factory=dict)
    fetched_records: list[dict[str, Any]] = field(default_factory=list)

    # Snapshot the loop runtime state before an action runs.
    @classmethod
    def from_loop(cls, loop: Any) -> "_ActionRuntime":
        return cls(
            url_hits=[dict(row) for row in loop.url_state.hits if isinstance(row, dict)],
            extract_state_by_url={
                str(url): dict(row)
                for url, row in dict(loop.extract_state.by_url or {}).items()
                if str(url).strip() and isinstance(row, dict)
            },
            fetched_records=_fetched_record_rows(loop.extract_state),
        )

    # Rebuild the runtime bridge from the action-mutated payload.
    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "_ActionRuntime":
        return cls(
            url_hits=[dict(row) for row in (payload.get("url_hits") or []) if isinstance(row, dict)],
            extract_state_by_url={
                str(url): dict(row)
                for url, row in dict(payload.get("extract_state_by_url") or {}).items()
                if str(url).strip() and isinstance(row, dict)
            },
            fetched_records=[dict(row) for row in (payload.get("fetched_records") or []) if isinstance(row, dict)],
        )

    # Serialize the runtime bridge into the shared action payload contract.
    def to_payload(self) -> dict[str, Any]:
        return {
            "url_hits": [dict(row) for row in self.url_hits if isinstance(row, dict)],
            "extract_state_by_url": {
                str(url): dict(row)
                for url, row in dict(self.extract_state_by_url or {}).items()
                if str(url).strip() and isinstance(row, dict)
            },
            "fetched_records": [dict(row) for row in self.fetched_records if isinstance(row, dict)],
        }

    # Apply the action-mutated runtime payload back onto typed loop state.
    def apply_to_loop(self, loop: Any) -> None:
        loop.url_state.hits = [dict(row) for row in self.url_hits if isinstance(row, dict)]
        loop.extract_state.by_url = {
            str(url): dict(row)
            for url, row in dict(self.extract_state_by_url or {}).items()
            if str(url).strip() and isinstance(row, dict)
        }
        loop.extract_state.fetched_record_index = _index_fetched_records(self.fetched_records)


# Choose the canonical lookup key used for fetched-record indexing and reuse.
def _fetched_record_index_key(row: dict[str, Any]) -> str:
    for value in (
        str(row.get("url") or "").strip(),
        str(row.get("requested_url") or "").strip(),
    ):
        if value:
            return value
    for value in (row.get("url_aliases") or []):
        alias = str(value).strip()
        if alias:
            return alias
    return ""


# Build the fetched-record index used by extract auto-fetch and reuse paths.
def _index_fetched_records(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    merged_records = _merge_fetched_records([], [dict(item) for item in records if isinstance(item, dict)])
    for row in merged_records:
        key = _fetched_record_index_key(row)
        if key:
            index[key] = dict(row)
    return index


# Project fetched-record rows back out of typed extract state for action payload exchange.
def _fetched_record_rows(extract_state: Any) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in dict(extract_state.fetched_record_index or {}).values()
        if isinstance(row, dict)
    ]
