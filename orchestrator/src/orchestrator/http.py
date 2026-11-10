"""A small HTTP client for the tracker's and the code host's APIs, with waits on rate limits."""

from __future__ import annotations

import logging
import time

import httpx

log = logging.getLogger(__name__)


class Api:
    def __init__(self, base_url: str, headers: dict, transport: httpx.BaseTransport | None = None,
                 sleep=time.sleep, attempts: int = 6):
        self.client = httpx.Client(base_url=base_url, headers=headers, timeout=20, transport=transport)
        self.sleep, self.attempts = sleep, attempts

    def call(self, method: str, path: str, ok=(200, 201, 204), **kwargs) -> httpx.Response:
        for _ in range(self.attempts):
            response = self.client.request(method, path, **kwargs)
            if response.status_code != 429:
                break
            # Plane allows a key 60 calls a minute and says when the window resets.
            reset = response.headers.get("X-RateLimit-Reset") or response.headers.get("Retry-After")
            wait = max(1.0, min(65.0, float(reset) - time.time())) if reset and float(reset) > 1e9 else float(reset or 10)
            log.warning("%s %s: rate limited, waiting %.0f s", method, path, wait)
            self.sleep(wait)
        if response.status_code not in ok:
            raise ApiError(method, path, response.status_code, response.text[:300])
        return response

    def json(self, method: str, path: str, **kwargs):
        response = self.call(method, path, **kwargs)
        return response.json() if response.content else None


class ApiError(Exception):
    def __init__(self, method: str, path: str, status: int, text: str):
        super().__init__(f"{method} {path}: {status} {text}")
        self.status = status


def marker_html(marker: str) -> str:
    """A hidden note in a comment that says which event wrote it, so a retry does not write it twice."""
    return f"<!-- agent:{marker} -->"
