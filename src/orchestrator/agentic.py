from __future__ import annotations

import csv
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from src.connectors.http import get_json, normalize_arxiv_id, normalize_doi
from src.retrieval.service import SOURCE_FIELDS, write_yaml
from src.utils.paths import project_dir
from src.utils.yamlx import load

REQUEST_SCHEMA_VERSION = "0.1.0"
SESSION_SCHEMA_VERSION = "0.1.0"
RESULT_SCHEMA_VERSION = "0.1.0"
QUESTIONS_SCHEMA_VERSION = "0.1.0"
LLM_PAYLOADS_SCHEMA_VERSION = "0.1.0"

CYCLE_FIELDS = [
    "timestamp",
    "session_id",
    "workflow",
    "cycle_index",
    "state_path",
    "planned_query",
    "retrieval_query",
    "tool_calls",
    "raw_candidates",
    "ranked_candidates",
    "candidate_delta",
    "decision",
    "decision_reason",
    "stop_reason",
    "question_id",
]

CANDIDATE_LATEST_FIELDS = [
    "session_id",
    "cycle_index",
    "rank",
    "candidate_key",
    "query_used",
    *SOURCE_FIELDS,
    "selected",
]

WEB_RESULT_FIELDS = [
    "timestamp",
    "session_id",
    "cycle_index",
    "query",
    "query_rank",
    "source",
    "source_id",
    "title",
    "url",
    "snippet",
    "venue",
    "year",
    "doi",
    "arxiv_id",
    "author_hint",
]

STOPWORDS = {
    "the",
    "and",
    "for",
    "from",
    "with",
    "this",
    "that",
    "into",
    "using",
    "toward",
    "towards",
    "based",
    "study",
    "analysis",
    "system",
    "paper",
    "research",
}

ProgressCallback = Callable[[dict], None]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _emit_progress(progress_callback: ProgressCallback | None, *, event: str, **payload) -> None:
    if progress_callback is None:
        return
    body = {"event": event}
    body.update(payload)
    progress_callback(body)


def _retrieval_dir(pdir: Path) -> Path:
    return pdir / "artifacts" / "retrieval"


def _agentic_paths(pdir: Path) -> dict[str, Path]:
    rdir = _retrieval_dir(pdir)
    return {
        "request": rdir / "agentic_request.yaml",
        "session": rdir / "agentic_session.yaml",
        "result": rdir / "agentic_result.yaml",
        "questions": rdir / "agentic_questions.yaml",
        "cycles": rdir / "agentic_cycles.tsv",
        "candidates": rdir / "agentic_candidates_latest.tsv",
        "web_results": rdir / "agentic_web_results.tsv",
        "llm_payloads": rdir / "agentic_llm_payloads.yaml",
    }


def _new_session_id(prompt: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    digest = hashlib.sha1(prompt.strip().lower().encode("utf-8")).hexdigest()[:10]
    return f"agentic-{stamp}-{digest}"


def _request_contract(
    *,
    session_id: str,
    request_id: str,
    project_id: str,
    prompt: str,
    workflow: str,
    top_n: int,
    max_cycles: int,
) -> dict:
    return {
        "artifact_type": "agentic_request",
        "schema_version": REQUEST_SCHEMA_VERSION,
        "request_id": request_id,
        "session_id": session_id,
        "project_id": project_id,
        "workflow": workflow,
        "prompt": prompt,
        "policy": {
            "retrieval_order": ["web"],
            "search_backend": "searxng",
        },
        "limits": {
            "top_n": top_n,
            "max_cycles": max_cycles,
            "max_questions_per_cycle": 0,
        },
        "created_at": _utc_now(),
        "updated_at": _utc_now(),
    }


def _session_contract(
    *,
    session_id: str,
    request_id: str,
    project_id: str,
    workflow: str,
    max_cycles: int,
) -> dict:
    return {
        "artifact_type": "agentic_session",
        "schema_version": SESSION_SCHEMA_VERSION,
        "session_id": session_id,
        "request_id": request_id,
        "project_id": project_id,
        "workflow": workflow,
        "status": "running",
        "state": "plan",
        "current_cycle": 0,
        "max_cycles": max_cycles,
        "last_decision": "",
        "last_decision_reason": "",
        "stop_reason": "",
        "created_at": _utc_now(),
        "updated_at": _utc_now(),
    }


def _result_contract(
    *,
    session_id: str,
    request_id: str,
    workflow: str,
    top_n: int,
) -> dict:
    return {
        "artifact_type": "agentic_result",
        "schema_version": RESULT_SCHEMA_VERSION,
        "session_id": session_id,
        "request_id": request_id,
        "workflow": workflow,
        "status": "running",
        "stop_reason": "",
        "cycle_count": 0,
        "top_n": top_n,
        "final_candidates": [],
        "decision_history": [],
        "created_at": _utc_now(),
        "updated_at": _utc_now(),
    }


def _questions_contract(*, session_id: str) -> dict:
    return {
        "artifact_type": "agentic_questions",
        "schema_version": QUESTIONS_SCHEMA_VERSION,
        "session_id": session_id,
        "pending": [],
        "history": [],
        "created_at": _utc_now(),
        "updated_at": _utc_now(),
    }


def _llm_payloads_contract(*, session_id: str, request_id: str, workflow: str) -> dict:
    return {
        "artifact_type": "agentic_llm_payloads",
        "schema_version": LLM_PAYLOADS_SCHEMA_VERSION,
        "session_id": session_id,
        "request_id": request_id,
        "workflow": workflow,
        "cycles": [],
        "created_at": _utc_now(),
        "updated_at": _utc_now(),
    }


def _candidate_key(row: dict) -> str:
    doi = str(row.get("doi") or "").strip().lower()
    arxiv = str(row.get("arxiv_id") or "").strip().lower()
    title = str(row.get("title") or "").strip().lower()
    year = str(row.get("year") or "").strip()
    if doi:
        return f"doi:{doi}"
    if arxiv:
        return f"arxiv:{arxiv}"
    return f"title:{title}:{year}"


def _append_cycle_row(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists()
    with path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CYCLE_FIELDS, delimiter="\t")
        if not exists:
            writer.writeheader()
        writer.writerow({k: row.get(k, "") for k in CYCLE_FIELDS})


def _write_candidates_latest(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CANDIDATE_LATEST_FIELDS, delimiter="\t")
        writer.writeheader()
        for row in rows:
            payload: dict[str, str] = {}
            for key in CANDIDATE_LATEST_FIELDS:
                value = row.get(key, "")
                if isinstance(value, list):
                    payload[key] = "|".join(str(v) for v in value if str(v).strip())
                else:
                    payload[key] = str(value) if value is not None else ""
            writer.writerow(payload)


def _append_web_results(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists()
    with path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=WEB_RESULT_FIELDS, delimiter="\t")
        if not exists:
            writer.writeheader()
        for row in rows:
            writer.writerow({k: str(row.get(k, "") or "") for k in WEB_RESULT_FIELDS})


def _state_path() -> str:
    return "plan>search_web>condense>decide"


def _read_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _read_str(value: Any, default: str) -> str:
    text = str(value or "").strip()
    return text if text else default


def _extract_json_object(text: str) -> dict:
    stripped = text.strip()
    if not stripped:
        return {}
    if stripped.startswith("```"):
        lines = [line for line in stripped.splitlines() if not line.strip().startswith("```")]
        stripped = "\n".join(lines).strip()
    try:
        payload = json.loads(stripped)
        return payload if isinstance(payload, dict) else {}
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", stripped, flags=re.DOTALL)
    if not match:
        return {}
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _coerce_message_content_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if isinstance(item, dict):
                if isinstance(item.get("text"), str):
                    parts.append(item["text"])
                    continue
                if isinstance(item.get("content"), str):
                    parts.append(item["content"])
                    continue
        return "\n".join(part for part in parts if part).strip()
    return str(content)


def _response_message_content(response: Any) -> str:
    choices: Any = []
    if isinstance(response, dict):
        choices = response.get("choices") or []
    else:
        choices = getattr(response, "choices", []) or []
    if not choices:
        return ""

    first = choices[0]
    message: Any = {}
    if isinstance(first, dict):
        message = first.get("message") or {}
    else:
        message = getattr(first, "message", {}) or {}

    if isinstance(message, dict):
        return _coerce_message_content_text(message.get("content"))
    return _coerce_message_content_text(getattr(message, "content", ""))


def _completion_json_payload(
    *,
    completion_fn: Any,
    model: str,
    api_key: str,
    messages: list[dict],
    with_response_format: bool,
) -> dict:
    kwargs = {
        "model": model,
        "api_key": api_key,
        "messages": messages,
        "temperature": 0.1,
    }
    if with_response_format:
        kwargs["response_format"] = {"type": "json_object"}
    response = completion_fn(**kwargs)
    return _extract_json_object(_response_message_content(response))


def _unique_queries(items: Any, limit: int) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in (items or []):
        query = str(item or "").strip()
        if not query:
            continue
        lowered = query.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        out.append(query)
        if len(out) >= limit:
            break
    return out


def _litellm_complete_json(*, model: str, api_key_env: str, messages: list[dict]) -> dict:
    api_key = str(os.environ.get(api_key_env) or "").strip()
    if not api_key:
        raise RuntimeError(f"missing_api_key:{api_key_env}")

    try:
        from litellm import completion
    except ModuleNotFoundError as exc:
        raise RuntimeError("missing_dependency:litellm") from exc

    resolved_model = model.strip()
    if "/" not in resolved_model and api_key_env == "DS_API_KEY":
        resolved_model = f"deepseek/{resolved_model}"

    payload = _completion_json_payload(
        completion_fn=completion,
        model=resolved_model,
        api_key=api_key,
        messages=messages,
        with_response_format=True,
    )
    if payload:
        return payload

    retry_messages = list(messages) + [
        {
            "role": "system",
            "content": (
                "Your previous response was not parseable. "
                "Return ONLY a valid JSON object. No markdown, no prose."
            ),
        }
    ]
    payload = _completion_json_payload(
        completion_fn=completion,
        model=resolved_model,
        api_key=api_key,
        messages=retry_messages,
        with_response_format=False,
    )
    if not payload:
        raise RuntimeError("llm_invalid_json")
    return payload


def _plan_queries_llm(
    *,
    user_prompt: str,
    cycle_index: int,
    queries_per_cycle: int,
    previous_summary: dict,
    search_categories: str,
    model: str,
    api_key_env: str,
) -> dict:
    messages = [
        {
            "role": "system",
            "content": (
                "You generate precise web-search queries for academic metadata retrieval. "
                "Return strict JSON object only with keys: queries, rationale, stop, stop_reason."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "task": "plan_queries",
                    "cycle_index": cycle_index,
                    "user_prompt": user_prompt,
                    "previous_cycle_summary": previous_summary,
                    "constraints": {
                        "max_queries": queries_per_cycle,
                        "backend": "searxng",
                        "categories": search_categories,
                    },
                },
                ensure_ascii=True,
            ),
        },
    ]
    payload = _litellm_complete_json(model=model, api_key_env=api_key_env, messages=messages)
    return {
        "queries": _unique_queries(payload.get("queries"), queries_per_cycle),
        "rationale": str(payload.get("rationale") or ""),
        "stop": bool(payload.get("stop", False)),
        "stop_reason": str(payload.get("stop_reason") or ""),
    }


def _decide_next_llm(
    *,
    user_prompt: str,
    cycle_index: int,
    max_cycles: int,
    current_queries: list[str],
    condensed_summary: dict,
    queries_per_cycle: int,
    model: str,
    api_key_env: str,
) -> dict:
    messages = [
        {
            "role": "system",
            "content": (
                "You decide whether retrieval is exhaustive. Return strict JSON object only with keys: "
                "stop, stop_reason, queries, rationale."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "task": "decide_or_continue",
                    "cycle_index": cycle_index,
                    "max_cycles": max_cycles,
                    "user_prompt": user_prompt,
                    "current_queries": current_queries,
                    "condensed_summary": condensed_summary,
                },
                ensure_ascii=True,
            ),
        },
    ]
    payload = _litellm_complete_json(model=model, api_key_env=api_key_env, messages=messages)
    return {
        "stop": bool(payload.get("stop", False)),
        "stop_reason": str(payload.get("stop_reason") or ""),
        "queries": _unique_queries(payload.get("queries"), queries_per_cycle),
        "rationale": str(payload.get("rationale") or ""),
    }


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        parts = [part.strip() for part in re.split(r"[;,|]", value) if part.strip()]
        return parts
    return [str(value).strip()]


def _normalize_searx_row(*, session_id: str, cycle_index: int, query: str, query_rank: int, item: dict) -> tuple[dict, dict]:
    metadata = item.get("metadata") or {}
    title = str(item.get("title") or metadata.get("title") or "").strip()
    url = str(item.get("url") or metadata.get("url") or "").strip()
    source_id = str(item.get("id") or url or title).strip()
    snippet = str(item.get("content") or item.get("snippet") or "").strip()
    source = str(item.get("engine") or item.get("source") or "searxng").strip()
    venue = str(metadata.get("journal") or metadata.get("venue") or metadata.get("source") or "").strip()
    year = str(metadata.get("year") or "").strip()
    doi = normalize_doi(str(metadata.get("doi") or ""))
    arxiv_raw = str(metadata.get("arxiv") or "").strip()
    if not arxiv_raw and "arxiv.org/" in url.lower():
        arxiv_raw = url
    arxiv_id = normalize_arxiv_id(arxiv_raw)

    author_candidates = []
    author_candidates.extend(_as_list(metadata.get("authors")))
    author_candidates.extend(_as_list(metadata.get("author")))
    author_hint = "|".join(dict.fromkeys(author_candidates))

    web_row = {
        "timestamp": _utc_now(),
        "session_id": session_id,
        "cycle_index": cycle_index,
        "query": query,
        "query_rank": query_rank,
        "source": source,
        "source_id": source_id,
        "title": title,
        "url": url,
        "snippet": snippet,
        "venue": venue,
        "year": year,
        "doi": doi,
        "arxiv_id": arxiv_id,
        "author_hint": author_hint,
    }

    quality_score = 0.0
    quality_score += max(0.0, 1.0 - (query_rank * 0.03))
    quality_score += 0.2 if doi else 0.0
    quality_score += 0.15 if arxiv_id else 0.0
    quality_score += 0.05 if venue else 0.0

    candidate_row = {
        "source": source,
        "source_id": source_id,
        "title": title,
        "venue": venue,
        "year": year,
        "doi": doi,
        "arxiv_id": arxiv_id,
        "url": url,
        "abstract": snippet,
        "keywords": [],
        "categories": [],
        "score": round(quality_score, 4),
        "reason": "searxng_search",
        "query_used": query,
        "_snippet": snippet,
        "_author_hint": author_hint,
    }
    return web_row, candidate_row


def _search_web_queries(
    *,
    session_id: str,
    cycle_index: int,
    queries: list[str],
    searxng_url: str,
    search_categories: str,
    web_results_per_query: int,
    timeout_s: float,
    progress_callback: ProgressCallback | None = None,
) -> tuple[list[dict], list[dict]]:
    endpoint = f"{searxng_url.rstrip('/')}/search"
    web_rows: list[dict] = []
    candidates: list[dict] = []
    for query in queries:
        _emit_progress(
            progress_callback,
            event="agentic_web_search_query_start",
            cycle_index=cycle_index,
            query=query,
            categories=search_categories,
            limit=web_results_per_query,
        )
        payload = get_json(
            endpoint,
            {"q": query, "format": "json", "categories": search_categories},
            timeout_s=timeout_s,
            min_interval_s=0.0,
        )
        scoped = (payload.get("results") or [])[:web_results_per_query]
        for rank, item in enumerate(scoped, start=1):
            if not isinstance(item, dict):
                continue
            web_row, candidate_row = _normalize_searx_row(
                session_id=session_id,
                cycle_index=cycle_index,
                query=query,
                query_rank=rank,
                item=item,
            )
            web_rows.append(web_row)
            candidates.append(candidate_row)
        _emit_progress(
            progress_callback,
            event="agentic_web_search_query_done",
            cycle_index=cycle_index,
            query=query,
            raw_results=len(payload.get("results") or []),
            used_results=len(scoped),
        )
    return web_rows, candidates


def _candidate_dedup_key(row: dict) -> str:
    doi = normalize_doi(str(row.get("doi") or ""))
    if doi:
        return f"doi:{doi}"
    arxiv = normalize_arxiv_id(str(row.get("arxiv_id") or ""))
    if arxiv:
        return f"arxiv:{arxiv}"
    url = str(row.get("url") or "").strip().lower()
    if url:
        return f"url:{url}"
    title = str(row.get("title") or "").strip().lower()
    year = str(row.get("year") or "").strip()
    return f"title:{title}:{year}"


def _rank_candidates(rows: list[dict]) -> list[dict]:
    best_by_key: dict[str, dict] = {}
    for row in rows:
        key = _candidate_dedup_key(row)
        prev = best_by_key.get(key)
        if prev is None or float(row.get("score", 0.0)) > float(prev.get("score", 0.0)):
            best_by_key[key] = row
    return sorted(
        best_by_key.values(),
        key=lambda r: (
            float(r.get("score", 0.0)),
            1 if r.get("doi") else 0,
            1 if r.get("arxiv_id") else 0,
            len(str(r.get("title") or "")),
        ),
        reverse=True,
    )


def _extract_keywords(rows: list[dict], max_terms: int = 12) -> list[str]:
    counter: dict[str, int] = {}
    for row in rows:
        title = str(row.get("title") or "")
        for token in re.findall(r"[A-Za-z][A-Za-z0-9\-]{2,}", title):
            lowered = token.lower()
            if lowered in STOPWORDS:
                continue
            counter[lowered] = counter.get(lowered, 0) + 1
    return [term for term, _ in sorted(counter.items(), key=lambda item: (-item[1], item[0]))[:max_terms]]


def _unique_nonempty(items: list[str], limit: int) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        value = str(item or "").strip()
        if not value:
            continue
        lowered = value.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        out.append(value)
        if len(out) >= limit:
            break
    return out


def _condense_results(rows: list[dict], max_hits: int = 20) -> dict:
    scoped = rows[:max_hits]
    hits = [
        {
            "title": str(row.get("title") or ""),
            "url": str(row.get("url") or ""),
            "venue": str(row.get("venue") or ""),
            "year": str(row.get("year") or ""),
            "doi": str(row.get("doi") or ""),
            "arxiv_id": str(row.get("arxiv_id") or ""),
            "score": float(row.get("score", 0.0) or 0.0),
        }
        for row in scoped
    ]
    paper_titles = _unique_nonempty([str(row.get("title") or "") for row in scoped], limit=10)
    venues = _unique_nonempty([str(row.get("venue") or "") for row in scoped], limit=10)
    authors = _unique_nonempty(
        [part for row in scoped for part in str(row.get("_author_hint") or "").split("|") if part.strip()],
        limit=12,
    )
    keywords = _extract_keywords(scoped, max_terms=12)
    return {
        "result_count": len(rows),
        "top_hits": hits,
        "paper_titles": paper_titles,
        "venues": venues,
        "authors": authors,
        "keywords": keywords,
    }


def _to_final_candidates(session_id: str, cycle_index: int, shortlisted: list[dict], top_n: int) -> tuple[list[dict], list[dict]]:
    candidate_rows: list[dict] = []
    final_candidates: list[dict] = []
    for idx, candidate in enumerate(shortlisted[:top_n], start=1):
        row = dict(candidate)
        row["session_id"] = session_id
        row["cycle_index"] = cycle_index
        row["rank"] = idx
        row["candidate_key"] = _candidate_key(row)
        row["selected"] = "1"
        candidate_rows.append(row)
        final_candidates.append(
            {
                "rank": idx,
                "candidate_key": row["candidate_key"],
                "title": str(row.get("title") or ""),
                "venue": str(row.get("venue") or ""),
                "year": str(row.get("year") or ""),
                "doi": str(row.get("doi") or ""),
                "arxiv_id": str(row.get("arxiv_id") or ""),
                "url": str(row.get("url") or ""),
                "score": float(row.get("score", 0.0) or 0.0),
                "source": str(row.get("source") or ""),
            }
        )
    return candidate_rows, final_candidates


def _persist_all(paths: dict[str, Path], request_payload: dict, session_payload: dict, result_payload: dict, questions_payload: dict, llm_payloads: dict) -> None:
    write_yaml(paths["request"], request_payload)
    write_yaml(paths["session"], session_payload)
    write_yaml(paths["result"], result_payload)
    write_yaml(paths["questions"], questions_payload)
    write_yaml(paths["llm_payloads"], llm_payloads)


def run_retrieve_agentic(
    project_id: str,
    *,
    prompt: str = "",
    top_n: int = 5,
    progress_callback: ProgressCallback | None = None,
) -> Path:
    pdir = project_dir(project_id)
    paths = _agentic_paths(pdir)
    pmeta = load(pdir / "project.yaml")
    retrieval_cfg = (pmeta.get("retrieval") or {}) if isinstance(pmeta, dict) else {}
    agentic_cfg = (retrieval_cfg.get("agentic") or {}) if isinstance(retrieval_cfg, dict) else {}
    llm_cfg = (agentic_cfg.get("llm") or {}) if isinstance(agentic_cfg, dict) else {}

    requested_top_n = _read_int(top_n or agentic_cfg.get("top_n", 5), 5)
    effective_top_n = max(1, requested_top_n)
    effective_max_cycles = max(1, _read_int(agentic_cfg.get("max_cycles", 3), 3))
    queries_per_cycle = max(1, _read_int(agentic_cfg.get("queries_per_cycle", 4), 4))
    web_results_per_query = max(1, _read_int(agentic_cfg.get("web_results_per_query", 8), 8))
    searxng_timeout_s = float(agentic_cfg.get("searxng_timeout_s", 8.0) or 8.0)
    searxng_categories = _read_str(agentic_cfg.get("searxng_categories"), "general")
    llm_model = _read_str(llm_cfg.get("model"), "deepseek-chat")
    llm_api_key_env = _read_str(llm_cfg.get("api_key_env"), "DS_API_KEY")
    workflow_name = "searxng_meta_refine_v1"
    _emit_progress(
        progress_callback,
        event="agentic_start",
        project_id=project_id,
        workflow=workflow_name,
        top_n=effective_top_n,
        max_cycles=effective_max_cycles,
        queries_per_cycle=queries_per_cycle,
        web_results_per_query=web_results_per_query,
        searxng_categories=searxng_categories,
        llm_model=llm_model,
        llm_api_key_env=llm_api_key_env,
    )

    resolved_prompt = str(prompt or "").strip()
    resolved_session_id = _new_session_id(resolved_prompt or workflow_name)
    request_id = f"req-{resolved_session_id}"

    request_payload = _request_contract(
        session_id=resolved_session_id,
        request_id=request_id,
        project_id=project_id,
        prompt=resolved_prompt,
        workflow=workflow_name,
        top_n=effective_top_n,
        max_cycles=effective_max_cycles,
    )
    resolved_prompt = str(request_payload.get("prompt") or "").strip()
    if not resolved_prompt:
        raise ValueError("Prompt is required")

    session_payload = _session_contract(
        session_id=resolved_session_id,
        request_id=request_id,
        project_id=project_id,
        workflow=workflow_name,
        max_cycles=effective_max_cycles,
    )
    result_payload = _result_contract(
        session_id=resolved_session_id,
        request_id=request_id,
        workflow=workflow_name,
        top_n=effective_top_n,
    )
    questions_payload = _questions_contract(session_id=resolved_session_id)
    llm_payloads = _llm_payloads_contract(session_id=resolved_session_id, request_id=request_id, workflow=workflow_name)

    searxng_url = str(os.environ.get("SEARXNG_URL") or "").strip()
    if not searxng_url:
        session_payload["status"] = "failed"
        session_payload["state"] = "failed"
        session_payload["stop_reason"] = "missing_env:SEARXNG_URL"
        session_payload["updated_at"] = _utc_now()
        result_payload["status"] = "failed"
        result_payload["stop_reason"] = "missing_env:SEARXNG_URL"
        result_payload["updated_at"] = _utc_now()
        _persist_all(paths, request_payload, session_payload, result_payload, questions_payload, llm_payloads)
        _write_candidates_latest(paths["candidates"], [])
        _emit_progress(
            progress_callback,
            event="agentic_failed",
            reason="missing_env:SEARXNG_URL",
        )
        return paths["result"]

    _write_candidates_latest(paths["candidates"], [])
    _persist_all(paths, request_payload, session_payload, result_payload, questions_payload, llm_payloads)

    previous_summary: dict[str, Any] = {}
    current_cycle = 0
    stop_reason = ""
    last_decision = "stop"
    last_decision_reason = ""

    try:
        for cycle_index in range(1, effective_max_cycles + 1):
            current_cycle = cycle_index
            _emit_progress(
                progress_callback,
                event="agentic_cycle_start",
                cycle_index=cycle_index,
                max_cycles=effective_max_cycles,
            )
            session_payload["current_cycle"] = cycle_index - 1
            session_payload["status"] = "running"
            session_payload["state"] = "plan"
            session_payload["updated_at"] = _utc_now()
            _persist_all(paths, request_payload, session_payload, result_payload, questions_payload, llm_payloads)

            planner_input = {
                "user_prompt": resolved_prompt,
                "cycle_index": cycle_index,
                "previous_summary": previous_summary,
            }
            _emit_progress(
                progress_callback,
                event="agentic_llm_planner_request",
                cycle_index=cycle_index,
                payload=planner_input,
            )
            planner_output = _plan_queries_llm(
                user_prompt=resolved_prompt,
                cycle_index=cycle_index,
                queries_per_cycle=queries_per_cycle,
                previous_summary=previous_summary,
                search_categories=searxng_categories,
                model=llm_model,
                api_key_env=llm_api_key_env,
            )
            planned_queries = planner_output["queries"]
            _emit_progress(
                progress_callback,
                event="agentic_llm_planner_response",
                cycle_index=cycle_index,
                payload=planner_output,
                planned_queries=planned_queries,
            )
            if planner_output["stop"] and not planned_queries:
                stop_reason = planner_output.get("stop_reason") or "llm_converged"
                last_decision = "stop"
                last_decision_reason = "planner_stop"
                llm_payloads["cycles"].append(
                    {
                        "cycle_index": cycle_index,
                        "timestamp": _utc_now(),
                        "planner_input_summary": planner_input,
                        "planner_output": planner_output,
                        "decider_input_summary": {},
                        "decider_output": {"stop": True, "stop_reason": stop_reason, "queries": [], "rationale": "planner_stop"},
                    }
                )
                _append_cycle_row(
                    paths["cycles"],
                    {
                        "timestamp": _utc_now(),
                        "session_id": resolved_session_id,
                        "workflow": workflow_name,
                        "cycle_index": cycle_index,
                        "state_path": _state_path(),
                        "planned_query": "",
                        "retrieval_query": "",
                        "tool_calls": "llm:planner",
                        "raw_candidates": 0,
                        "ranked_candidates": 0,
                        "candidate_delta": 0,
                        "decision": "stop",
                        "decision_reason": "planner_stop",
                        "stop_reason": stop_reason,
                        "question_id": "",
                    },
                )
                _emit_progress(
                    progress_callback,
                    event="agentic_cycle_decision",
                    cycle_index=cycle_index,
                    decision="stop",
                    decision_reason="planner_stop",
                    stop_reason=stop_reason,
                )
                break

            if not planned_queries:
                stop_reason = "planner_no_queries"
                last_decision = "stop"
                last_decision_reason = "planner_no_queries"
                _write_candidates_latest(paths["candidates"], [])
                _append_cycle_row(
                    paths["cycles"],
                    {
                        "timestamp": _utc_now(),
                        "session_id": resolved_session_id,
                        "workflow": workflow_name,
                        "cycle_index": cycle_index,
                        "state_path": _state_path(),
                        "planned_query": "",
                        "retrieval_query": "",
                        "tool_calls": "llm:planner",
                        "raw_candidates": 0,
                        "ranked_candidates": 0,
                        "candidate_delta": 0,
                        "decision": "stop",
                        "decision_reason": "planner_no_queries",
                        "stop_reason": stop_reason,
                        "question_id": "",
                    },
                )
                _emit_progress(
                    progress_callback,
                    event="agentic_cycle_decision",
                    cycle_index=cycle_index,
                    decision="stop",
                    decision_reason="planner_no_queries",
                    stop_reason=stop_reason,
                )
                break

            session_payload["state"] = "retrieve"
            session_payload["updated_at"] = _utc_now()
            _persist_all(paths, request_payload, session_payload, result_payload, questions_payload, llm_payloads)

            web_rows, raw_candidates = _search_web_queries(
                session_id=resolved_session_id,
                cycle_index=cycle_index,
                queries=planned_queries,
                searxng_url=searxng_url,
                search_categories=searxng_categories,
                web_results_per_query=web_results_per_query,
                timeout_s=searxng_timeout_s,
                progress_callback=progress_callback,
            )
            _append_web_results(paths["web_results"], web_rows)

            ranked_rows = _rank_candidates(raw_candidates)
            shortlisted = ranked_rows[:effective_top_n]
            previous_count = len(list(result_payload.get("final_candidates") or []))
            candidate_rows, final_candidates = _to_final_candidates(
                resolved_session_id,
                cycle_index,
                shortlisted,
                effective_top_n,
            )
            _write_candidates_latest(paths["candidates"], candidate_rows)

            session_payload["state"] = "rank"
            session_payload["updated_at"] = _utc_now()
            _persist_all(paths, request_payload, session_payload, result_payload, questions_payload, llm_payloads)

            condensed_summary = _condense_results(ranked_rows, max_hits=max(effective_top_n * 3, 12))
            _emit_progress(
                progress_callback,
                event="agentic_condensed_summary",
                cycle_index=cycle_index,
                summary=condensed_summary,
            )
            session_payload["state"] = "decide"
            session_payload["updated_at"] = _utc_now()
            _persist_all(paths, request_payload, session_payload, result_payload, questions_payload, llm_payloads)

            decider_input = {
                "cycle_index": cycle_index,
                "max_cycles": effective_max_cycles,
                "current_queries": planned_queries,
                "condensed_summary": condensed_summary,
            }
            _emit_progress(
                progress_callback,
                event="agentic_llm_decider_request",
                cycle_index=cycle_index,
                payload=decider_input,
            )
            decider_output = _decide_next_llm(
                user_prompt=resolved_prompt,
                cycle_index=cycle_index,
                max_cycles=effective_max_cycles,
                current_queries=planned_queries,
                condensed_summary=condensed_summary,
                queries_per_cycle=queries_per_cycle,
                model=llm_model,
                api_key_env=llm_api_key_env,
            )
            _emit_progress(
                progress_callback,
                event="agentic_llm_decider_response",
                cycle_index=cycle_index,
                payload=decider_output,
            )

            llm_payloads["cycles"].append(
                {
                    "cycle_index": cycle_index,
                    "timestamp": _utc_now(),
                    "planner_input_summary": planner_input,
                    "planner_output": planner_output,
                    "decider_input_summary": decider_input,
                    "decider_output": decider_output,
                }
            )
            llm_payloads["updated_at"] = _utc_now()

            if not shortlisted:
                decision = "stop"
                decision_reason = "no_candidates"
                stop_reason = "no_candidates"
            elif cycle_index >= effective_max_cycles:
                decision = "stop"
                decision_reason = "max_cycles_reached"
                stop_reason = "max_cycles_reached"
            elif decider_output["stop"]:
                decision = "stop"
                decision_reason = "llm_converged"
                stop_reason = decider_output.get("stop_reason") or "llm_converged"
            elif not decider_output["queries"]:
                decision = "stop"
                decision_reason = "llm_no_next_queries"
                stop_reason = "llm_no_next_queries"
            else:
                decision = "continue"
                decision_reason = "llm_continue"
                stop_reason = ""

            _append_cycle_row(
                paths["cycles"],
                {
                    "timestamp": _utc_now(),
                    "session_id": resolved_session_id,
                    "workflow": workflow_name,
                    "cycle_index": cycle_index,
                    "state_path": _state_path(),
                    "planned_query": " || ".join(planned_queries),
                    "retrieval_query": " || ".join(planned_queries),
                    "tool_calls": f"llm:planner,llm:decider,searxng:{len(planned_queries)}",
                    "raw_candidates": len(raw_candidates),
                    "ranked_candidates": len(shortlisted),
                    "candidate_delta": len(shortlisted) - previous_count,
                    "decision": decision,
                    "decision_reason": decision_reason,
                    "stop_reason": stop_reason,
                    "question_id": "",
                },
            )
            _emit_progress(
                progress_callback,
                event="agentic_cycle_decision",
                cycle_index=cycle_index,
                decision=decision,
                decision_reason=decision_reason,
                stop_reason=stop_reason,
                raw_candidates=len(raw_candidates),
                shortlisted=len(shortlisted),
            )

            result_payload["final_candidates"] = final_candidates
            result_payload["cycle_count"] = cycle_index
            result_payload.setdefault("decision_history", []).append(
                {
                    "timestamp": _utc_now(),
                    "cycle_index": cycle_index,
                    "decision": decision,
                    "decision_reason": decision_reason,
                    "stop_reason": stop_reason,
                    "planned_queries": planned_queries,
                    "retrieved_count": len(raw_candidates),
                    "ranked_count": len(shortlisted),
                    "planner_rationale": planner_output.get("rationale", ""),
                    "decider_rationale": decider_output.get("rationale", ""),
                }
            )
            result_payload["updated_at"] = _utc_now()
            previous_summary = condensed_summary
            last_decision = decision
            last_decision_reason = decision_reason

            if decision == "stop":
                break

        if not stop_reason:
            stop_reason = "max_cycles_reached"

        session_payload["status"] = "completed"
        session_payload["state"] = "completed"
        session_payload["current_cycle"] = current_cycle
        session_payload["last_decision"] = last_decision
        session_payload["last_decision_reason"] = last_decision_reason
        session_payload["stop_reason"] = stop_reason
        session_payload["updated_at"] = _utc_now()

        result_payload["status"] = "completed"
        result_payload["stop_reason"] = stop_reason
        result_payload["cycle_count"] = current_cycle
        result_payload["updated_at"] = _utc_now()
        _emit_progress(
            progress_callback,
            event="agentic_complete",
            status="completed",
            cycle_count=current_cycle,
            stop_reason=stop_reason,
            final_candidates=len(result_payload.get("final_candidates") or []),
        )
    except Exception as exc:
        reason = f"agentic_error:{type(exc).__name__}:{exc}"
        session_payload["status"] = "failed"
        session_payload["state"] = "failed"
        session_payload["current_cycle"] = current_cycle
        session_payload["last_decision"] = "fail"
        session_payload["last_decision_reason"] = "exception"
        session_payload["stop_reason"] = reason
        session_payload["updated_at"] = _utc_now()

        result_payload["status"] = "failed"
        result_payload["stop_reason"] = reason
        result_payload["cycle_count"] = current_cycle
        result_payload["updated_at"] = _utc_now()
        if not paths["candidates"].exists():
            _write_candidates_latest(paths["candidates"], [])
        _emit_progress(
            progress_callback,
            event="agentic_failed",
            reason=reason,
        )

    _persist_all(paths, request_payload, session_payload, result_payload, questions_payload, llm_payloads)
    return paths["result"]
