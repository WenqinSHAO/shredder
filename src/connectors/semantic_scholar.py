from __future__ import annotations

from src.connectors.base import DiscoveryConnector
from src.connectors.http import get_json, normalize_arxiv_id, normalize_doi


class SemanticScholarConnector(DiscoveryConnector):
    name = "semantic_scholar"

    def search(self, theme: str, venues: list[str], year_min: int, year_max: int, limit: int) -> list[dict]:
        params = {
            "query": theme,
            "year": f"{year_min}-{year_max}",
            "limit": str(limit),
            "fields": "paperId,title,year,venue,externalIds,url",
        }
        payload = get_json(
            "https://api.semanticscholar.org/graph/v1/paper/search",
            params,
            timeout_s=self.config.timeout_s,
            min_interval_s=(1.0 / self.config.rate_limit_per_sec) if self.config.rate_limit_per_sec else 0.0,
        )
        allowed = {v.lower() for v in venues}
        out = []
        for paper in payload.get("data", []):
            venue = paper.get("venue", "")
            if allowed and venue and venue.lower() not in allowed:
                continue
            ext = paper.get("externalIds") or {}
            out.append(
                {
                    "source": self.name,
                    "source_id": paper.get("paperId", ""),
                    "title": paper.get("title", ""),
                    "venue": venue,
                    "year": str(paper.get("year") or 0),
                    "doi": normalize_doi(ext.get("DOI", "")),
                    "arxiv_id": normalize_arxiv_id(ext.get("ArXiv", "")),
                    "url": paper.get("url", ""),
                }
            )
        return out
