from __future__ import annotations

from typing import Protocol


class DiscoveryConnector(Protocol):
    name: str

    def search(self, theme: str, venues: list[str], year_min: int, year_max: int) -> list[dict]:
        ...
