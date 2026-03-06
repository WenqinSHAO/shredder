from __future__ import annotations

import socket
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from src.connectors.http import RetryPolicy, get_json, normalize_arxiv_id


class _Response:
    def __init__(self, payload: bytes):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None

    def read(self) -> bytes:
        return self.payload


class TestHttpRetries(unittest.TestCase):
    def test_retries_and_succeeds_on_second_attempt(self):
        attempts = {"n": 0}

        def fake_urlopen(url, timeout):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise socket.timeout("timeout")
            return _Response(b'{"ok": true}')

        with patch("src.connectors.http.urlopen", side_effect=fake_urlopen), patch("src.connectors.http.time.sleep"):
            payload = get_json(
                "https://example.org/api",
                {},
                timeout_s=1.0,
                min_interval_s=0.0,
                retry_policy=RetryPolicy(max_attempts=3, base_backoff_s=0.0, max_backoff_s=0.0, jitter_s=0.0),
            )

        self.assertEqual(attempts["n"], 2)
        self.assertTrue(payload["ok"])

    def test_does_not_retry_http_400(self):
        attempts = {"n": 0}

        def fake_urlopen(url, timeout):
            attempts["n"] += 1
            raise HTTPError(url, 400, "bad request", hdrs=None, fp=None)

        with patch("src.connectors.http.urlopen", side_effect=fake_urlopen), patch("src.connectors.http.time.sleep"):
            with self.assertRaises(HTTPError):
                get_json(
                    "https://example.org/api",
                    {},
                    timeout_s=1.0,
                    min_interval_s=0.0,
                    retry_policy=RetryPolicy(max_attempts=3),
                )

        self.assertEqual(attempts["n"], 1)

    def test_stops_at_max_attempts(self):
        attempts = {"n": 0}

        def fake_urlopen(url, timeout):
            attempts["n"] += 1
            raise HTTPError(url, 503, "unavailable", hdrs=None, fp=None)

        with patch("src.connectors.http.urlopen", side_effect=fake_urlopen), patch("src.connectors.http.time.sleep"):
            with self.assertRaises(HTTPError):
                get_json(
                    "https://example.org/api",
                    {},
                    timeout_s=1.0,
                    min_interval_s=0.0,
                    retry_policy=RetryPolicy(max_attempts=3, base_backoff_s=0.0, max_backoff_s=0.0, jitter_s=0.0),
                )

        self.assertEqual(attempts["n"], 3)


class TestNormalization(unittest.TestCase):
    def test_normalize_arxiv_http_abs_with_version(self):
        self.assertEqual(normalize_arxiv_id("http://arxiv.org/abs/1806.11202v1"), "1806.11202")

    def test_normalize_arxiv_https_pdf_with_suffix(self):
        self.assertEqual(normalize_arxiv_id("https://arxiv.org/pdf/2401.12345v2.pdf"), "2401.12345")

    def test_normalize_arxiv_prefix_form(self):
        self.assertEqual(normalize_arxiv_id("arXiv:hep-th/9901001v3"), "hep-th/9901001")


if __name__ == "__main__":
    unittest.main()
