from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ConnectorConfig:
    enabled: bool = True
    rate_limit_per_sec: float = 1.0
    timeout_s: float = 8.0


class DiscoveryConnector:
    name = "base"

    def __init__(self, config: ConnectorConfig | None = None):
        self.config = config or ConnectorConfig()

    def search(self, theme: str, venues: list[str], year_min: int, year_max: int, limit: int) -> list[dict]:
        raise NotImplementedError
