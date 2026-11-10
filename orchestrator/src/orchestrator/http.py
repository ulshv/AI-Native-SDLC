"""The HTTP client for the tracker's and the code host's APIs: a timeout on every call, and
bounded retries with backoff (tenacity) for what is safe to send again.

A request the server never took (429, a refused connection) is retried whatever its method. A
server error or a lost response is retried only for idempotent methods: a POST that may have
landed is not sent twice here; the step fails, and the queue runs it again, where the comment
markers keep it from repeating.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
from tenacity import RetryCallState, Retrying, retry_if_exception, stop_after_attempt, wait_exponential_jitter

log = logging.getLogger(__name__)

TIMEOUT = httpx.Timeout(20.0, connect=5.0)
IDEMPOTENT = frozenset({"GET", "HEAD", "PUT", "PATCH", "DELETE"})
NOT_TAKEN = frozenset({429})
SERVER_ERRORS = frozenset({500, 502, 503, 504})
BACKOFF = wait_exponential_jitter(initial=1, max=30)


class Retryable(Exception):
    """A response worth sending the request again for."""

    def __init__(self, response: httpx.Response):
        super().__init__(f"{response.request.method} {response.request.url.path}: {response.status_code}")
        self.response = response


class ApiError(Exception):
    def __init__(self, method: str, path: str, status: int, text: str):
        super().__init__(f"{method} {path}: {status} {text}")
        self.status = status


MAX_WAIT = 65.0  # a rate-limit window of Plane's; far below the queue's lease


def rate_limit_wait(response: httpx.Response) -> float:
    """How long a 429 asks to wait, at most MAX_WAIT. Plane says when its window resets (an
    epoch); Retry-After gives seconds or an HTTP date."""
    value = response.headers.get("X-RateLimit-Reset") or response.headers.get("Retry-After")
    return max(1.0, min(MAX_WAIT, seconds_until(value)))


def seconds_until(value: str | None) -> float:
    if not value:
        return 10.0
    try:
        number = float(value)
    except ValueError:
        try:
            return parsedate_to_datetime(value).timestamp() - time.time()
        except (TypeError, ValueError):
            return 10.0
    return number - time.time() if number > 1e9 else number


def wait(state: RetryCallState) -> float:
    error = state.outcome.exception() if state.outcome else None
    if isinstance(error, Retryable) and error.response.status_code in NOT_TAKEN:
        return rate_limit_wait(error.response)
    return BACKOFF(state)


def worth_retrying(idempotent: bool) -> Callable[[BaseException], bool]:
    """What is safe to send again: a request the server never took, whatever its method; one that
    may have landed only when sending it twice changes nothing."""

    def check(error: BaseException) -> bool:
        if isinstance(error, Retryable):
            status = error.response.status_code
            return status in NOT_TAKEN or (idempotent and status in SERVER_ERRORS)
        if isinstance(error, httpx.ConnectError | httpx.ConnectTimeout):
            return True
        return idempotent and isinstance(error, httpx.TransportError)

    return check


def before_sleep(state: RetryCallState) -> None:
    error = state.outcome.exception() if state.outcome else None
    method, path = state.args[:2]
    log.warning("%s %s: %s, try %d", method, path, error, state.attempt_number)


class Api:
    def __init__(
        self,
        base_url: str,
        headers: dict[str, str],
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        attempts: int = 6,
        timeout: httpx.Timeout = TIMEOUT,
    ):
        self.client = httpx.Client(base_url=base_url, headers=headers, timeout=timeout, transport=transport)
        self.retrying = {
            idempotent: Retrying(
                stop=stop_after_attempt(attempts),
                wait=wait,
                retry=retry_if_exception(worth_retrying(idempotent)),
                sleep=sleep,
                before_sleep=before_sleep,
                reraise=True,
            )
            for idempotent in (True, False)
        }

    def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        response = self.client.request(method, path, **kwargs)
        if response.status_code in NOT_TAKEN | SERVER_ERRORS:
            raise Retryable(response)
        return response

    def call(self, method: str, path: str, ok: tuple[int, ...] = (200, 201, 204), **kwargs: Any) -> httpx.Response:
        try:
            response = self.retrying[method in IDEMPOTENT](self._send, method, path, **kwargs)
        except Retryable as error:  # retried as far as it was safe to
            response = error.response
        if response.status_code not in ok:
            raise ApiError(method, path, response.status_code, response.text[:300])
        return response

    def json(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self.call(method, path, **kwargs)
        return response.json() if response.content else None


def marker_html(marker: str) -> str:
    """A hidden note in a comment that says which event wrote it, so a retry does not write it twice."""
    return f"<!-- agent:{marker} -->"
