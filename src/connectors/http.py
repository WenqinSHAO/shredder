from __future__ import annotations

import json
import time
from urllib.parse import urlencode
from urllib.request import urlopen


def get_json(url: str, params: dict[str, str], timeout_s: float, min_interval_s: float) -> dict:
    query = urlencode({k: v for k, v in params.items() if v is not None and v != ""})
    full_url = f"{url}?{query}" if query else url
    with urlopen(full_url, timeout=timeout_s) as response:
        payload = response.read().decode("utf-8")
    if min_interval_s > 0:
        time.sleep(min_interval_s)
    return json.loads(payload)


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
    for prefix in ("arxiv:", "https://arxiv.org/abs/"):
        if value.startswith(prefix):
            value = value[len(prefix):]
    return value
