from __future__ import annotations

import json
import os
import re
from typing import Any

from src.orchestrator.agentic_text import _estimate_text_tokens


def extract_json_object(text: str) -> dict:
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


def coerce_message_content_text(content: Any) -> str:
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


def response_message_content(response: Any) -> str:
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
        return coerce_message_content_text(message.get("content"))
    return coerce_message_content_text(getattr(message, "content", ""))


def resolve_openai_model_and_base_url(*, model: str, api_key_env: str) -> tuple[str, str]:
    resolved_model = str(model or "").strip()
    if not resolved_model:
        return "", ""
    base_url = ""
    if "/" in resolved_model:
        provider, bare_model = resolved_model.split("/", 1)
        provider_l = provider.strip().lower()
        if provider_l == "deepseek":
            resolved_model = bare_model.strip()
            base_url = str(os.environ.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com").strip()
        elif provider_l == "openai":
            resolved_model = bare_model.strip()
            base_url = str(os.environ.get("OPENAI_BASE_URL") or "").strip()
    else:
        model_l = resolved_model.lower()
        if api_key_env == "DS_API_KEY" or model_l.startswith("deepseek-"):
            base_url = str(os.environ.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com").strip()
        else:
            base_url = str(os.environ.get("OPENAI_BASE_URL") or "").strip()
    return resolved_model, base_url


def openai_completion_json_payload(
    *,
    client: Any,
    model: str,
    messages: list[dict],
    with_response_format: bool,
    max_tokens: int | None = None,
) -> dict:
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0.1,
    }
    if with_response_format:
        kwargs["response_format"] = {"type": "json_object"}
    if max_tokens and max_tokens > 0:
        kwargs["max_tokens"] = int(max_tokens)
    response = client.chat.completions.create(**kwargs)
    return extract_json_object(response_message_content(response))


def estimate_messages_metrics(messages: list[dict]) -> dict[str, int]:
    total_chars = 0
    total_tokens = 0
    for item in messages:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "")
        content = coerce_message_content_text(item.get("content"))
        total_chars += len(role) + len(content)
        total_tokens += _estimate_text_tokens(role) + _estimate_text_tokens(content)
    return {
        "input_chars": total_chars,
        "input_tokens_est": total_tokens,
    }


def openai_complete_json(
    *,
    model: str,
    api_key_env: str,
    messages: list[dict],
    timeout_s: float | None = None,
    max_tokens: int | None = None,
    max_retries: int | None = None,
) -> dict:
    api_key = str(os.environ.get(api_key_env) or "").strip()
    if not api_key:
        raise RuntimeError(f"missing_api_key:{api_key_env}")

    try:
        from openai import OpenAI
    except ModuleNotFoundError as exc:
        raise RuntimeError("missing_dependency:openai") from exc

    resolved_model, base_url = resolve_openai_model_and_base_url(model=model, api_key_env=api_key_env)
    client = OpenAI(
        api_key=api_key,
        base_url=base_url or None,
        max_retries=(int(max_retries) if max_retries is not None else 2),
    )
    option_kwargs: dict[str, Any] = {}
    if timeout_s and timeout_s > 0:
        option_kwargs["timeout"] = float(timeout_s)
    if max_retries is not None:
        option_kwargs["max_retries"] = int(max_retries)
    if option_kwargs:
        client = client.with_options(**option_kwargs)

    payload = openai_completion_json_payload(
        client=client,
        model=resolved_model,
        messages=messages,
        with_response_format=True,
        max_tokens=max_tokens,
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
    payload = openai_completion_json_payload(
        client=client,
        model=resolved_model,
        messages=retry_messages,
        with_response_format=False,
        max_tokens=max_tokens,
    )
    if not payload:
        raise RuntimeError("llm_invalid_json")
    return payload
