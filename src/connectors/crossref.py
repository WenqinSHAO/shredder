from __future__ import annotations

from src.connectors.base import DiscoveryConnector
from src.connectors.http import get_json, normalize_doi


class CrossrefConnector(DiscoveryConnector):
    name = "crossref"

    def search(self, theme: str, venues: list[str], year_min: int, year_max: int, limit: int) -> list[dict]:
        params = {
            "query.title": theme,
            "rows": str(limit),
            "filter": f"from-pub-date:{year_min},until-pub-date:{year_max}",
        }
        payload = get_json(
            "https://api.crossref.org/works",
            params,
            timeout_s=self.config.timeout_s,
            min_interval_s=(1.0 / self.config.rate_limit_per_sec) if self.config.rate_limit_per_sec else 0.0,
        )
        allowed = {v.lower() for v in venues}
        out = []
        for item in payload.get("message", {}).get("items", []):
            venue = ""
            container = item.get("container-title") or []
            if container:
                venue = container[0]
            if allowed and venue and venue.lower() not in allowed:
                continue
            year_parts = ((item.get("issued") or {}).get("date-parts") or [[0]])[0]
            year = int(year_parts[0]) if year_parts else 0
            out.append(
                {
                    "source": self.name,
                    "source_id": item.get("DOI", ""),
                    "title": (item.get("title") or [""])[0],
                    "venue": venue,
                    "year": str(year),
                    "doi": normalize_doi(item.get("DOI", "")),
                    "arxiv_id": "",
                    "url": (item.get("URL") or ""),
                }
            )
        return out
