from __future__ import annotations

import re
from dataclasses import dataclass
from html import unescape
from importlib import import_module
from importlib.util import find_spec
from typing import Any

from src.connectors.http import normalize_arxiv_id, normalize_doi


def _as_dict(payload: Any) -> dict:
    if isinstance(payload, dict):
        return payload
    raw = getattr(payload, "raw_data", None)
    if isinstance(raw, dict):
        return raw
    data = getattr(payload, "__dict__", None)
    if isinstance(data, dict):
        return data
    return {}


def _normalize_affiliations(items: list[dict]) -> list[dict]:
    out: list[dict] = []
    for item in items:
        name = str(item.get("name") or "").strip()
        ror = str(item.get("ror") or "").strip()
        country = str(item.get("country") or "").strip()
        if not (name or ror or country):
            continue
        out.append({"name": name, "ror": ror, "country": country})
    return out


def _normalize_author(author: dict) -> dict:
    orcid = str(author.get("orcid") or "").strip().lower()
    for prefix in ("https://orcid.org/", "http://orcid.org/", "orcid:"):
        if orcid.startswith(prefix):
            orcid = orcid[len(prefix):]

    return {
        "name": str(author.get("name") or "").strip(),
        "orcid": orcid,
        "source_id": str(author.get("source_id") or "").strip(),
        "affiliations": _normalize_affiliations(author.get("affiliations") or []),
    }


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    text = unescape(text)
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(text.split())


def _normalize_terms(values: Any, max_terms: int = 12) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        raw_items = [values]
    elif isinstance(values, (list, tuple, set)):
        raw_items = list(values)
    else:
        raw_items = [values]

    out: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        term = _clean_text(item)
        if not term:
            continue
        lowered = term.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        out.append(term)
        if len(out) >= max_terms:
            break
    return out


def _decode_abstract_inverted_index(payload: dict) -> str:
    if not isinstance(payload, dict):
        return ""
    tokens: list[tuple[int, str]] = []
    for token, positions in payload.items():
        if not isinstance(positions, list):
            continue
        for pos in positions:
            if isinstance(pos, int):
                tokens.append((pos, str(token)))
    if not tokens:
        return ""
    tokens.sort(key=lambda item: item[0])
    return " ".join(tok for _, tok in tokens)


def _normalize_paper_row(row: dict, source: str, reason: str, score: float = 1.0) -> dict:
    authors = [_normalize_author(a) for a in (row.get("authors") or [])]
    return {
        "source": source,
        "source_id": str(row.get("source_id") or "").strip(),
        "title": str(row.get("title") or "").strip(),
        "venue": str(row.get("venue") or "").strip(),
        "year": str(row.get("year") or "").strip(),
        "doi": normalize_doi(str(row.get("doi") or "")),
        "arxiv_id": normalize_arxiv_id(str(row.get("arxiv_id") or "")),
        "url": str(row.get("url") or "").strip(),
        "abstract": _clean_text(row.get("abstract")),
        "keywords": _normalize_terms(row.get("keywords") or []),
        "categories": _normalize_terms(row.get("categories") or []),
        "authors": authors,
        "score": float(row.get("score", score)),
        "reason": reason,
    }


@dataclass
class AdapterConfig:
    enabled: bool = True


class RetrievalAdapter:
    name = "base"
    dependency_modules: tuple[str, ...] = ()

    def __init__(self, config: AdapterConfig | None = None):
        self.config = config or AdapterConfig()
        self.last_action: str = ""
        self.last_error: str = ""
        self.last_dependency: str = ""

    def lookup_doi(self, doi: str) -> list[dict]:
        return []

    def lookup_arxiv(self, arxiv_id: str) -> list[dict]:
        return []

    def search_title(self, title: str, limit: int = 5) -> list[dict]:
        return []

    def search_open(self, query: str, limit: int = 20) -> list[dict]:
        return self.search_title(query, limit=limit)

    def _has_module(self, module_name: str) -> bool:
        return find_spec(module_name) is not None

    def _start_call(self, action: str) -> bool:
        self.last_action = action
        self.last_error = ""
        self.last_dependency = ""
        if not self.config.enabled:
            self.last_error = "adapter_disabled"
            return False
        for module_name in self.dependency_modules:
            if not self._has_module(module_name):
                self.last_dependency = module_name
                self.last_error = f"module_not_installed:{module_name}"
                return False
        return True

    def _mark_error(self, exc: Exception | str) -> None:
        if isinstance(exc, Exception):
            self.last_error = f"{type(exc).__name__}: {exc}"
        else:
            self.last_error = str(exc)

    def diagnostics(self) -> dict:
        deps = list(self.dependency_modules)
        deps_available = {name: self._has_module(name) for name in deps}
        return {
            "adapter": self.name,
            "enabled": bool(self.config.enabled),
            "action": self.last_action,
            "dependency_modules": deps,
            "dependency_available": deps_available,
            "missing_dependency": self.last_dependency,
            "error": self.last_error,
        }


class HabaneroAdapter(RetrievalAdapter):
    name = "habanero"
    dependency_modules = ("habanero",)

    def lookup_doi(self, doi: str) -> list[dict]:
        if not self._start_call("lookup_doi"):
            return []
        Crossref = import_module("habanero").Crossref
        client = Crossref()
        try:
            payload = client.works(ids=doi)
        except Exception as exc:
            self._mark_error(exc)
            return []
        message = _as_dict(payload).get("message") or {}
        if not message:
            self._mark_error("empty_response")
            return []
        return [
            _normalize_paper_row(
                {
                    "source_id": message.get("DOI", doi),
                    "title": ((message.get("title") or [""])[0] if isinstance(message.get("title"), list) else ""),
                    "venue": ((message.get("container-title") or [""])[0] if isinstance(message.get("container-title"), list) else ""),
                    "year": str((((message.get("issued") or {}).get("date-parts") or [[0]])[0][0] if (message.get("issued") or {}).get("date-parts") else "")),
                    "doi": message.get("DOI", doi),
                    "arxiv_id": "",
                    "url": message.get("URL", ""),
                    "abstract": message.get("abstract", ""),
                    "keywords": message.get("subject") or [],
                    "categories": message.get("subject") or [],
                    "authors": [
                        {
                            "name": f"{(a.get('given') or '').strip()} {(a.get('family') or '').strip()}".strip(),
                            "orcid": str(a.get("ORCID") or ""),
                            "source_id": "",
                            "affiliations": [{"name": aff.get("name", ""), "ror": "", "country": ""} for aff in (a.get("affiliation") or [])],
                        }
                        for a in (message.get("author") or [])
                    ],
                },
                source=self.name,
                reason="lookup_doi",
                score=1.0,
            )
        ]

    def search_open(self, query: str, limit: int = 20) -> list[dict]:
        if not self._start_call("search_open"):
            return []
        Crossref = import_module("habanero").Crossref
        client = Crossref()
        try:
            payload = client.works(query=query, limit=limit)
        except Exception as exc:
            self._mark_error(exc)
            return []
        items = (_as_dict(payload).get("message") or {}).get("items") or []
        out = []
        for item in items:
            out.append(
                _normalize_paper_row(
                    {
                        "source_id": item.get("DOI", ""),
                        "title": ((item.get("title") or [""])[0] if isinstance(item.get("title"), list) else ""),
                        "venue": ((item.get("container-title") or [""])[0] if isinstance(item.get("container-title"), list) else ""),
                        "year": str((((item.get("issued") or {}).get("date-parts") or [[0]])[0][0] if (item.get("issued") or {}).get("date-parts") else "")),
                        "doi": item.get("DOI", ""),
                        "arxiv_id": "",
                        "url": item.get("URL", ""),
                        "abstract": item.get("abstract", ""),
                        "keywords": item.get("subject") or [],
                        "categories": item.get("subject") or [],
                        "authors": [],
                        "score": 0.4,
                    },
                    source=self.name,
                    reason="open_query",
                    score=0.4,
                )
            )
        return out


class ArxivAdapter(RetrievalAdapter):
    name = "arxiv"
    dependency_modules = ("arxiv",)

    def lookup_arxiv(self, arxiv_id: str) -> list[dict]:
        if not self._start_call("lookup_arxiv"):
            return []
        arxiv = import_module("arxiv")
        try:
            search = arxiv.Search(id_list=[arxiv_id], max_results=1)
            client = getattr(arxiv, "Client", None)
            if client:
                results = list(client().results(search))
            else:
                results = list(search.results())
        except Exception as exc:
            self._mark_error(exc)
            return []

        out: list[dict] = []
        for item in results:
            data = _as_dict(item)
            title = str(getattr(item, "title", "") or data.get("title") or "")
            year = ""
            published = getattr(item, "published", None) or data.get("published")
            if published is not None and getattr(published, "year", None):
                year = str(published.year)
            authors = []
            for author in (getattr(item, "authors", None) or data.get("authors") or []):
                name = str(getattr(author, "name", "") or _as_dict(author).get("name") or "")
                authors.append({"name": name, "orcid": "", "source_id": "", "affiliations": []})
            out.append(
                _normalize_paper_row(
                    {
                        "source_id": arxiv_id,
                        "title": title,
                        "venue": "arXiv",
                        "year": year,
                        "doi": "",
                        "arxiv_id": arxiv_id,
                        "url": str(getattr(item, "entry_id", "") or data.get("entry_id") or ""),
                        "abstract": str(getattr(item, "summary", "") or data.get("summary") or ""),
                        "keywords": list(getattr(item, "categories", None) or data.get("categories") or []),
                        "categories": list(getattr(item, "categories", None) or data.get("categories") or []),
                        "authors": authors,
                    },
                    source=self.name,
                    reason="lookup_arxiv",
                    score=1.0,
                )
            )
        return out

    def search_title(self, title: str, limit: int = 5) -> list[dict]:
        if not self._start_call("search_title"):
            return []
        arxiv = import_module("arxiv")
        try:
            search = arxiv.Search(query=title, max_results=limit)
            client = getattr(arxiv, "Client", None)
            if client:
                results = list(client().results(search))
            else:
                results = list(search.results())
        except Exception as exc:
            self._mark_error(exc)
            return []

        out: list[dict] = []
        for item in results:
            data = _as_dict(item)
            arxiv_id = normalize_arxiv_id(str(getattr(item, "entry_id", "") or data.get("entry_id") or ""))
            out.append(
                _normalize_paper_row(
                    {
                        "source_id": arxiv_id,
                        "title": str(getattr(item, "title", "") or data.get("title") or ""),
                        "venue": "arXiv",
                        "year": str((getattr(item, "published", None) or data.get("published") or "").year if getattr((getattr(item, "published", None) or data.get("published") or ""), "year", None) else ""),
                        "doi": "",
                        "arxiv_id": arxiv_id,
                        "url": str(getattr(item, "entry_id", "") or data.get("entry_id") or ""),
                        "abstract": str(getattr(item, "summary", "") or data.get("summary") or ""),
                        "keywords": list(getattr(item, "categories", None) or data.get("categories") or []),
                        "categories": list(getattr(item, "categories", None) or data.get("categories") or []),
                        "authors": [],
                        "score": 0.6,
                    },
                    source=self.name,
                    reason="search_title",
                    score=0.6,
                )
            )
        return out


class PyAlexAdapter(RetrievalAdapter):
    name = "pyalex"
    dependency_modules = ("pyalex",)

    def _map_work(self, work: dict, reason: str, score: float) -> dict:
        authors = []
        for authorship in work.get("authorships", []):
            author_obj = authorship.get("author") or {}
            affiliations = []
            for inst in authorship.get("institutions", []):
                affiliations.append(
                    {
                        "name": str(inst.get("display_name") or ""),
                        "ror": str(inst.get("ror") or ""),
                        "country": str(inst.get("country_code") or ""),
                    }
                )
            authors.append(
                {
                    "name": str(author_obj.get("display_name") or ""),
                    "orcid": str(author_obj.get("orcid") or ""),
                    "source_id": str(author_obj.get("id") or ""),
                    "affiliations": affiliations,
                }
            )

        primary_source = (work.get("primary_location") or {}).get("source") or {}
        concepts = [str((c or {}).get("display_name") or "") for c in (work.get("concepts") or [])]
        categories: list[str] = []
        primary_topic = work.get("primary_topic") or {}
        for key in ("subfield", "field", "domain"):
            name = str((primary_topic.get(key) or {}).get("display_name") or "")
            if name:
                categories.append(name)
        return _normalize_paper_row(
            {
                "source_id": str(work.get("id") or ""),
                "title": str(work.get("title") or ""),
                "venue": str(primary_source.get("display_name") or ""),
                "year": str(work.get("publication_year") or ""),
                "doi": str(work.get("doi") or ""),
                "arxiv_id": normalize_arxiv_id(str((work.get("ids") or {}).get("arxiv") or "")),
                "url": str((work.get("primary_location") or {}).get("landing_page_url") or ""),
                "abstract": _decode_abstract_inverted_index(work.get("abstract_inverted_index") or {}),
                "keywords": concepts,
                "categories": categories,
                "authors": authors,
                "score": score,
            },
            source=self.name,
            reason=reason,
            score=score,
        )

    def lookup_doi(self, doi: str) -> list[dict]:
        if not self._start_call("lookup_doi"):
            return []
        Works = import_module("pyalex").Works
        try:
            works = Works().filter(doi=doi).get(per_page=5)
        except Exception as exc:
            self._mark_error(exc)
            return []
        return [self._map_work(w, reason="lookup_doi", score=1.0) for w in works or []]

    def lookup_arxiv(self, arxiv_id: str) -> list[dict]:
        if not self._start_call("lookup_arxiv"):
            return []
        Works = import_module("pyalex").Works
        try:
            works = Works().filter(from_publication_date="1900-01-01").search(arxiv_id).get(per_page=5)
        except Exception as exc:
            self._mark_error(exc)
            return []
        out = [self._map_work(w, reason="lookup_arxiv", score=0.9) for w in works or []]
        return [w for w in out if w.get("arxiv_id") == arxiv_id]

    def search_title(self, title: str, limit: int = 5) -> list[dict]:
        if not self._start_call("search_title"):
            return []
        Works = import_module("pyalex").Works
        try:
            works = Works().search(title).get(per_page=limit)
        except Exception as exc:
            self._mark_error(exc)
            return []
        return [self._map_work(w, reason="search_title", score=0.7) for w in works or []]


class SemanticScholarAdapter(RetrievalAdapter):
    name = "semanticscholar"
    dependency_modules = ("semanticscholar",)

    def _map_paper(self, paper: dict, reason: str, score: float) -> dict:
        ext = paper.get("externalIds") or {}
        authors = []
        for author in paper.get("authors") or []:
            affiliations = [{"name": str(aff), "ror": "", "country": ""} for aff in (author.get("affiliations") or [])]
            authors.append(
                {
                    "name": str(author.get("name") or ""),
                    "orcid": str((author.get("externalIds") or {}).get("ORCID") or ""),
                    "source_id": str(author.get("authorId") or ""),
                    "affiliations": affiliations,
                }
            )
        categories = [str((item or {}).get("category") or "") for item in (paper.get("s2FieldsOfStudy") or [])]
        return _normalize_paper_row(
            {
                "source_id": str(paper.get("paperId") or ""),
                "title": str(paper.get("title") or ""),
                "venue": str(paper.get("venue") or ""),
                "year": str(paper.get("year") or ""),
                "doi": str(ext.get("DOI") or ""),
                "arxiv_id": str(ext.get("ArXiv") or ""),
                "url": str(paper.get("url") or ""),
                "abstract": str(paper.get("abstract") or ""),
                "keywords": [str(v) for v in (paper.get("fieldsOfStudy") or [])],
                "categories": categories,
                "authors": authors,
                "score": score,
            },
            source=self.name,
            reason=reason,
            score=score,
        )

    def lookup_doi(self, doi: str) -> list[dict]:
        if not self._start_call("lookup_doi"):
            return []
        client = import_module("semanticscholar").SemanticScholar()
        try:
            paper = client.get_paper(f"DOI:{doi}")
        except Exception as exc:
            self._mark_error(exc)
            return []
        payload = _as_dict(paper)
        if not payload:
            self._mark_error("empty_response")
            return []
        return [self._map_paper(payload, reason="lookup_doi", score=1.0)]

    def lookup_arxiv(self, arxiv_id: str) -> list[dict]:
        if not self._start_call("lookup_arxiv"):
            return []
        client = import_module("semanticscholar").SemanticScholar()
        try:
            paper = client.get_paper(f"ARXIV:{arxiv_id}")
        except Exception as exc:
            self._mark_error(exc)
            return []
        payload = _as_dict(paper)
        if not payload:
            self._mark_error("empty_response")
            return []
        return [self._map_paper(payload, reason="lookup_arxiv", score=0.95)]

    def search_title(self, title: str, limit: int = 5) -> list[dict]:
        if not self._start_call("search_title"):
            return []
        client = import_module("semanticscholar").SemanticScholar()
        try:
            result = client.search_paper(title, limit=limit)
        except Exception as exc:
            self._mark_error(exc)
            return []
        payload = _as_dict(result)
        papers = payload.get("data") or payload.get("papers") or []
        return [self._map_paper(_as_dict(p), reason="search_title", score=0.75) for p in papers]
