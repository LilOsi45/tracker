"""The price lookups behind a screen refresh must be time-boxed.

Without this, one unreachable upstream turned a summary request into a
90-second hang: five attempts with doubling backoff, twice over.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.clients.base import ApiError, request_json  # noqa: E402
from app.services import portfolio  # noqa: E402


class _HangingClient:
    """Stands in for an upstream that never answers."""

    async def request(self, *args, **kwargs):
        await asyncio.sleep(3600)


def test_current_prices_gives_up_within_budget(monkeypatch):
    monkeypatch.setattr(portfolio, "PRICE_BUDGET_SECONDS", 0.3)

    async def never_returns(*args, **kwargs):
        await asyncio.sleep(60)

    monkeypatch.setattr(portfolio, "_fetch_prices", never_returns)

    started = time.monotonic()
    prices, sol_usd = asyncio.run(portfolio._current_prices(_HangingClient(), ["mint"]))
    elapsed = time.monotonic() - started

    assert prices == {}
    assert sol_usd is None
    assert elapsed < 2.0, f"took {elapsed:.1f}s, budget was not enforced"


def test_backoff_respects_max_delay():
    """A low max_delay must keep total wait small even across all attempts."""

    class AlwaysFails:
        async def request(self, *args, **kwargs):
            raise __import__("httpx").ConnectError("refused")

    started = time.monotonic()
    with pytest.raises(ApiError):
        asyncio.run(
            request_json(
                AlwaysFails(),
                "GET",
                "https://example.invalid/x",
                max_attempts=3,
                max_delay=0.05,
            )
        )
    elapsed = time.monotonic() - started

    # Three attempts at <=0.05s backoff plus jitter must not approach the
    # uncapped ladder's 1 + 2 + 4 seconds.
    assert elapsed < 2.0, f"took {elapsed:.1f}s, max_delay was not honoured"
