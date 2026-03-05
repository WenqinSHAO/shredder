from __future__ import annotations

import os

from src.connectors.base import DiscoveryConnector
from src.connectors.http import get_json, normalize_arxiv_id, normalize_doi


class SearxngConnector(DiscoveryConnector):
    name = "searxng"

    def __init__(self, config=None):
        super().__init__(config)
        self.base_url = (self.config.base_url or os.environ.get("SEARXNG_URL") or "").rstrip("/")

    def has_endpoint(self) -> bool:
        return bool(self.base_url)

    def search(self, theme: str, venues: list[str], year_min: int, year_max: int, limit: int) -> list[dict]:
        if not self.base_url:
            return []
        payload = get_json(
            f"{self.base_url}/search",
            {
                "q": f"{theme} {year_min}..{year_max}",
                "format": "json",
                "categories": "science",
            },
            timeout_s=self.config.timeout_s,
            min_interval_s=(1.0 / self.config.rate_limit_per_sec) if self.config.rate_limit_per_sec else 0.0,
        )

        allowed = {v.lower() for v in venues}
        out = []
        for item in payload.get("results", [])[:limit]:
            metadata = item.get("metadata") or {}
            title = item.get("title") or metadata.get("title") or ""
            content = item.get("content") or ""
            venue = metadata.get("journal") or metadata.get("venue") or ""
            if allowed and venue and venue.lower() not in allowed:
                continue
            year = str(metadata.get("year") or "")
            if year.isdigit() and not (year_min <= int(year) <= year_max):
                continue
            source_id = item.get("url") or item.get("id") or title
            doi = normalize_doi(metadata.get("doi") or "")
            arxiv = normalize_arxiv_id(metadata.get("arxiv") or item.get("url") or "")
            out.append(
                {
                    "source": self.name,
                    "source_id": source_id,
                    "title": title,
                    "venue": venue,
                    "year": year,
                    "doi": doi,
                    "arxiv_id": arxiv,
                    "url": item.get("url", ""),
                }
            )
        return out
