"""Shared HTTP plumbing: token-bucket rate limiting and retry with backoff."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any

import httpx

log = logging.getLogger(__name__)


class RateLimiter:
    """Simple token bucket. `rate` calls per `per` seconds, shared across tasks."""

    def __init__(self, rate: int, per: float = 60.0) -> None:
        self.rate = rate
        self.per = per
        self._tokens = float(rate)
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                self._tokens = min(
                    self.rate, self._tokens + (now - self._updated) * self.rate / self.per
                )
                self._updated = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                await asyncio.sleep((1.0 - self._tokens) * self.per / self.rate)


class ApiError(RuntimeError):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


async def request_json(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    limiter: RateLimiter | None = None,
    max_attempts: int = 5,
    **kwargs: Any,
) -> Any:
    """Issue a request, honouring 429/Retry-After and retrying transient failures.

    Raises ApiError on 4xx that is not 429, since retrying those never helps.
    """
    delay = 1.0
    last_error: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        if limiter is not None:
            await limiter.acquire()
        try:
            response = await client.request(method, url, **kwargs)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_error = exc
            log.warning("%s %s failed (%s), attempt %d/%d", method, url, exc, attempt, max_attempts)
        else:
            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After")
                wait = float(retry_after) if retry_after and retry_after.isdigit() else delay
                log.warning("429 from %s, waiting %.1fs", url, wait)
                await asyncio.sleep(wait)
                delay = min(delay * 2, 60.0)
                continue
            if 400 <= response.status_code < 500:
                raise ApiError(
                    f"{method} {url} -> {response.status_code}: {response.text[:300]}",
                    response.status_code,
                )
            if response.status_code >= 500:
                last_error = ApiError(f"{url} -> {response.status_code}", response.status_code)
                log.warning("5xx from %s, attempt %d/%d", url, attempt, max_attempts)
            else:
                return response.json()

        await asyncio.sleep(delay + random.uniform(0, 0.3))
        delay = min(delay * 2, 60.0)

    raise ApiError(f"{method} {url} failed after {max_attempts} attempts: {last_error}")
