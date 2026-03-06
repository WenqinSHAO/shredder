from __future__ import annotations

import json
import random
import re
import socket
import time
from dataclasses import dataclass, field
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import urlopen


@dataclass
class RetryPolicy:
    max_attempts: int = 3
    base_backoff_s: float = 0.25
    max_backoff_s: float = 2.0
    jitter_s: float = 0.1
    retry_http_statuses: set[int] = field(default_factory=lambda: {429, 500, 502, 503, 504})


def get_json(
    url: str,
    params: dict[str, str],
    timeout_s: float,
    min_interval_s: float,
    retry_policy: RetryPolicy | None = None,
) -> dict:
    query = urlencode({k: v for k, v in params.items() if v is not None and v != ""})
    full_url = f"{url}?{query}" if query else url

    policy = retry_policy or RetryPolicy(max_attempts=1, base_backoff_s=0.0, max_backoff_s=0.0, jitter_s=0.0, retry_http_statuses=set())
    last_error: Exception | None = None

    for attempt in range(1, policy.max_attempts + 1):
        try:
            with urlopen(full_url, timeout=timeout_s) as response:
                payload = response.read().decode("utf-8")
            if min_interval_s > 0:
                time.sleep(min_interval_s)
            return json.loads(payload)
        except HTTPError as exc:
            last_error = exc
            if exc.code not in policy.retry_http_statuses:
                raise
        except (URLError, TimeoutError, socket.timeout) as exc:
            last_error = exc

        if attempt >= policy.max_attempts:
            break

        backoff = min(policy.base_backoff_s * (2 ** (attempt - 1)), policy.max_backoff_s)
        jitter = random.uniform(0.0, policy.jitter_s) if policy.jitter_s > 0 else 0.0
        sleep_s = min_interval_s + backoff + jitter
        if sleep_s > 0:
            time.sleep(sleep_s)

    if last_error is not None:
        raise last_error
    raise RuntimeError("get_json exhausted retries without response")


def get_text(
    url: str,
    timeout_s: float,
    min_interval_s: float,
    retry_policy: RetryPolicy | None = None,
) -> str:
    policy = retry_policy or RetryPolicy(max_attempts=1, base_backoff_s=0.0, max_backoff_s=0.0, jitter_s=0.0, retry_http_statuses=set())
    last_error: Exception | None = None

    for attempt in range(1, policy.max_attempts + 1):
        try:
            with urlopen(url, timeout=timeout_s) as response:
                raw = response.read()
                charset = ""
                headers = getattr(response, "headers", None)
                if headers is not None and hasattr(headers, "get_content_charset"):
                    charset = str(headers.get_content_charset() or "").strip()
            encoding = charset or "utf-8"
            try:
                text = raw.decode(encoding, errors="replace")
            except LookupError:
                text = raw.decode("utf-8", errors="replace")
            if min_interval_s > 0:
                time.sleep(min_interval_s)
            return text
        except HTTPError as exc:
            last_error = exc
            if exc.code not in policy.retry_http_statuses:
                raise
        except (URLError, TimeoutError, socket.timeout) as exc:
            last_error = exc

        if attempt >= policy.max_attempts:
            break

        backoff = min(policy.base_backoff_s * (2 ** (attempt - 1)), policy.max_backoff_s)
        jitter = random.uniform(0.0, policy.jitter_s) if policy.jitter_s > 0 else 0.0
        sleep_s = min_interval_s + backoff + jitter
        if sleep_s > 0:
            time.sleep(sleep_s)

    if last_error is not None:
        raise last_error
    raise RuntimeError("get_text exhausted retries without response")


def normalize_doi(doi: str | None) -> str:
    if not doi:
        return ""
    doi = doi.strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if doi.startswith(prefix):
            return doi[len(prefix):]
    return doi


def normalize_arxiv_id(raw: str | None) -> str:
    if not raw:
        return ""
    value = raw.strip().lower()
    if value.startswith("arxiv:"):
        value = value[len("arxiv:"):]

    parsed = None
    if "arxiv.org/" in value:
        parsed = urlparse(value if "://" in value else f"https://{value}")
        path = (parsed.path or "").strip()
        if path.startswith("/abs/"):
            value = path[len("/abs/"):]
        elif path.startswith("/pdf/"):
            value = path[len("/pdf/"):]
        else:
            value = path.lstrip("/")

    if parsed is None:
        value = value.split("?", 1)[0].split("#", 1)[0]

    if value.endswith(".pdf"):
        value = value[: -len(".pdf")]
    value = re.sub(r"v\d+$", "", value)
    return value
