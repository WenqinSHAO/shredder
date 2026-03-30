from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from typing import Any, Callable

from src.orchestrator.agentic_search import _peek_text

GENERIC_TITLE_HEADS = {
    "a",
    "an",
    "the",
    "towards",
    "toward",
    "understanding",
    "discovering",
    "preventing",
    "learning",
    "efficient",
    "new",
    "evolution",
}


def _normalized_title(title: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(title or "").lower()))


def _title_tokens(title: str) -> list[str]:
    return [token for token in re.findall(r"[a-z0-9]{3,}", _normalized_title(title)) if token]


def _title_head(title: str) -> str:
    text = str(title or "").strip()
    if not text:
        return ""
    if ":" in text:
        head = _normalized_title(text.split(":", 1)[0])
        if head and head not in GENERIC_TITLE_HEADS:
            return head
    for token in _title_tokens(text):
        if token not in GENERIC_TITLE_HEADS:
            return token
    return ""


def _title_overlap_ratio(left: str, right: str) -> float:
    left_tokens = set(_title_tokens(left))
    right_tokens = set(_title_tokens(right))
    if not left_tokens or not right_tokens:
        return 0.0
    return float(len(left_tokens & right_tokens)) / float(min(len(left_tokens), len(right_tokens)))


def _likely_same_title_family(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_year = str(left.get("year") or "").strip()
    right_year = str(right.get("year") or "").strip()
    if left_year and right_year and left_year != right_year:
        return False
    left_title = str(left.get("title") or "").strip()
    right_title = str(right.get("title") or "").strip()
    if not left_title or not right_title:
        return False
    left_head = _title_head(left_title)
    right_head = _title_head(right_title)
    if not left_head or left_head != right_head:
        return False
    if ":" in left_title and ":" in right_title:
        return True
    overlap = _title_overlap_ratio(left_title, right_title)
    seq_ratio = SequenceMatcher(None, _normalized_title(left_title), _normalized_title(right_title)).ratio()
    return overlap >= 0.30 or seq_ratio >= 0.62


def build_candidate_dedup_clusters(
    paper_candidates: list[dict[str, Any]],
    *,
    max_cluster_size: int = 4,
) -> list[list[int]]:
    clusters: list[list[int]] = []
    seen: set[int] = set()
    for idx, row in enumerate(paper_candidates):
        if idx in seen or not isinstance(row, dict):
            continue
        cluster = [idx]
        for other_idx in range(idx + 1, len(paper_candidates)):
            if other_idx in seen:
                continue
            other = paper_candidates[other_idx]
            if not isinstance(other, dict):
                continue
            if _likely_same_title_family(row, other):
                cluster.append(other_idx)
        if len(cluster) < 2:
            continue
        if len(cluster) > max(2, int(max_cluster_size or 0)):
            continue
        seen.update(cluster)
        clusters.append(cluster)
    return clusters


def dedup_paper_candidates_system_prompt() -> str:
    return (
        "You deduplicate small clusters of extracted academic paper candidates. "
        "These clusters were already pre-grouped because they share the same distinctive title head "
        "and look like near-duplicates from the same venue/year. "
        "Return JSON only with key `groups` as a list of objects with keys: "
        "candidate_ids, canonical_candidate_id, canonical_title, reason. "
        "Only group candidates when they clearly refer to the same underlying paper. "
        "Conference accepted-paper pages and program/session pages often paraphrase the same paper title "
        "by dropping adjectives, rewriting the subtitle, or using singular/plural variants. "
        "If two candidates have the same distinctive title head and clearly overlapping technical title, "
        "treat them as the same paper even when the wording is not identical. "
        "Compatible authors or affiliations strengthen a merge, but missing author detail on one source "
        "does not by itself mean the papers are different. "
        "Do not merge distinct papers that only share venue, topic, or a broad prefix. "
        "Prefer the most specific official-looking title when choosing the canonical title."
    )


def dedup_paper_candidates_user_payload(
    *,
    user_prompt: str,
    cluster: list[dict[str, Any]],
) -> dict[str, Any]:
    shared_title_head = ""
    if cluster:
        shared_title_head = _title_head(str(cluster[0].get("title") or ""))
    return {
        "task": "deduplicate_paper_candidates",
        "user_prompt": user_prompt,
        "cluster_hint": {
            "shared_title_head": shared_title_head,
            "note": (
                "This cluster was pre-grouped as likely near-duplicates from the same title family. "
                "Conference accepted-paper pages and program/session pages may paraphrase the same paper title."
            ),
        },
        "candidates": [
            {
                "candidate_id": str(row.get("candidate_id") or ""),
                "title": str(row.get("title") or ""),
                "title_head": _title_head(str(row.get("title") or "")),
                "year": str(row.get("year") or ""),
                "authors": str(row.get("authors") or ""),
                "affiliations": str(row.get("affiliations") or ""),
                "source_url": str(row.get("url") or ""),
                "abstract_snippet": _peek_text(
                    str(row.get("abstract_snippet") or row.get("abstract") or ""),
                    240,
                ),
            }
            for row in cluster
            if isinstance(row, dict)
        ],
    }


def dedup_paper_candidates_messages(
    *,
    user_prompt: str,
    cluster: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    user_payload = dedup_paper_candidates_user_payload(user_prompt=user_prompt, cluster=cluster)
    messages = [
        {"role": "system", "content": dedup_paper_candidates_system_prompt()},
        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=True)},
    ]
    return messages, user_payload


def dedup_paper_candidates_with_llm(
    *,
    paper_candidates: list[dict[str, Any]],
    user_prompt: str,
    model: str,
    api_key_env: str,
    timeout_s: float = 45.0,
    max_retries: int = 0,
    raw_event_fn: Callable[[str, Any], str] | None = None,
    llm_op_id_prefix: str = "",
    deps: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    estimate_messages_metrics_fn = deps["estimate_messages_metrics_fn"]
    openai_complete_json_fn = deps["openai_complete_json_fn"]
    next_op_id_fn = deps.get("next_op_id_fn")
    clusters = build_candidate_dedup_clusters(paper_candidates)
    if not clusters:
        return list(paper_candidates), {"cluster_count": 0, "groups_applied": 0}

    indexed: list[dict[str, Any]] = []
    for idx, row in enumerate(paper_candidates):
        item = dict(row)
        item["candidate_id"] = f"cand-{idx + 1}"
        indexed.append(item)

    drop_ids: set[str] = set()
    canonical_updates: dict[str, str] = {}
    groups_applied = 0

    for cluster_indexes in clusters:
        cluster = [indexed[idx] for idx in cluster_indexes if 0 <= idx < len(indexed)]
        if len(cluster) < 2:
            continue
        messages, user_payload = dedup_paper_candidates_messages(user_prompt=user_prompt, cluster=cluster)
        message_metrics = estimate_messages_metrics_fn(messages)
        max_completion_tokens = min(1_200, max(300, int((message_metrics.get("input_tokens_est") or 0) * 0.25)))
        llm_op_id = next_op_id_fn(llm_op_id_prefix or "extract_paper_dedup") if next_op_id_fn is not None else ""
        if raw_event_fn is not None:
            raw_event_fn(
                "extract_paper_dedup_request",
                {
                    "op_id": llm_op_id,
                    "model": model,
                    "api_key_env": api_key_env,
                    "messages": messages,
                    "input_chars": int(message_metrics.get("input_chars") or 0),
                    "input_tokens_est": int(message_metrics.get("input_tokens_est") or 0),
                    "max_completion_tokens": max_completion_tokens,
                    "cluster_size": len(cluster),
                },
            )
        payload = openai_complete_json_fn(
            model=model,
            api_key_env=api_key_env,
            messages=messages,
            timeout_s=timeout_s,
            max_tokens=max_completion_tokens,
            max_retries=max_retries,
        )
        if raw_event_fn is not None:
            response_payload = dict(payload) if isinstance(payload, dict) else {"payload": payload}
            response_payload["op_id"] = llm_op_id
            raw_event_fn("extract_paper_dedup_response", response_payload)
        rows = payload.get("groups") if isinstance(payload, dict) else []
        cluster_ids = {str(row.get("candidate_id") or "") for row in cluster}
        for item in rows if isinstance(rows, list) else []:
            if not isinstance(item, dict):
                continue
            member_ids = [str(value) for value in (item.get("candidate_ids") or []) if str(value).strip()]
            canonical_id = str(item.get("canonical_candidate_id") or "").strip()
            canonical_title = str(item.get("canonical_title") or "").strip()
            valid_members = [value for value in member_ids if value in cluster_ids]
            if len(valid_members) < 2 or canonical_id not in valid_members:
                continue
            groups_applied += 1
            if canonical_title:
                canonical_updates[canonical_id] = canonical_title
            for member_id in valid_members:
                if member_id != canonical_id:
                    drop_ids.add(member_id)

    out: list[dict[str, Any]] = []
    for row in indexed:
        candidate_id = str(row.get("candidate_id") or "")
        if candidate_id in drop_ids:
            continue
        item = dict(row)
        item.pop("candidate_id", None)
        updated_title = canonical_updates.get(candidate_id)
        if updated_title:
            item["title"] = updated_title
        out.append(item)
    return out, {
        "cluster_count": len(clusters),
        "groups_applied": groups_applied,
        "reduced_count": max(0, len(paper_candidates) - len(out)),
    }
