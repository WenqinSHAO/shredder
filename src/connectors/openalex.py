from __future__ import annotations

from src.connectors.base import DiscoveryConnector
from src.connectors.http import get_json, normalize_arxiv_id, normalize_doi


class OpenAlexConnector(DiscoveryConnector):
    name = "openalex"

    def search(self, theme: str, venues: list[str], year_min: int, year_max: int, limit: int) -> list[dict]:
        filters = [f"from_publication_date:{year_min}-01-01", f"to_publication_date:{year_max}-12-31"]
        params = {
            "search": theme,
            "filter": ",".join(filters),
            "per-page": str(limit),
        }
        payload = get_json(
            "https://api.openalex.org/works",
            params,
            timeout_s=self.config.timeout_s,
            min_interval_s=(1.0 / self.config.rate_limit_per_sec) if self.config.rate_limit_per_sec else 0.0,
        )
        out = []
        allowed = {v.lower() for v in venues}
        for work in payload.get("results", []):
            venue = (work.get("primary_location") or {}).get("source", {}).get("display_name", "")
            if allowed and venue and venue.lower() not in allowed:
                continue
            doi = normalize_doi(work.get("doi"))
            arxiv = ""
            for location in work.get("locations", []):
                arxiv = normalize_arxiv_id(location.get("landing_page_url") or "")
                if arxiv:
                    break
            year = work.get("publication_year") or 0
            out.append(
                {
                    "source": self.name,
                    "source_id": work.get("id", ""),
                    "title": work.get("title", ""),
                    "venue": venue,
                    "year": str(year),
                    "doi": doi,
                    "arxiv_id": arxiv,
                    "url": (work.get("primary_location") or {}).get("landing_page_url", ""),
                }
            )
        return out
