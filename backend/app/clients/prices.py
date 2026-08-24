"""Price sources.

Jupiter Lite gives spot USD prices for arbitrary SPL mints without a key.
CoinGecko gives the daily SOL/USD close, which is what turns SOL-denominated
PnL into fiat for the chart and the tax export.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from ..config import WSOL_MINT
from .base import RateLimiter, request_json

log = logging.getLogger(__name__)

JUPITER_PRICE_URL = "https://lite-api.jup.ag/price/v3"
COINGECKO_URL = "https://api.coingecko.com/api/v3/coins/solana/market_chart"

# Lite tier is roughly 60 req/min on a shared bucket.
_jupiter_limiter = RateLimiter(rate=50, per=60.0)
_coingecko_limiter = RateLimiter(rate=8, per=60.0)


def _extract_price(entry: Any) -> float | None:
    if not isinstance(entry, dict):
        return None
    for key in ("usdPrice", "price"):
        value = entry.get(key)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


class PriceClient:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def spot_usd(
        self, mints: list[str], *, attempts: int = 2, max_delay: float = 2.0
    ) -> dict[str, float]:
        """Current USD price per mint. Mints Jupiter cannot route are omitted.

        Defaults are deliberately impatient: this sits on the request path of
        a screen someone is staring at, and a missing price is a state the UI
        renders honestly rather than an error worth waiting out.
        """
        unique = list(dict.fromkeys(m for m in mints if m))
        prices: dict[str, float] = {}

        for i in range(0, len(unique), 50):
            chunk = unique[i : i + 50]
            try:
                data = await request_json(
                    self._client,
                    "GET",
                    JUPITER_PRICE_URL,
                    params={"ids": ",".join(chunk)},
                    limiter=_jupiter_limiter,
                    max_attempts=attempts,
                    max_delay=max_delay,
                )
            except Exception as exc:  # a dead price feed must not kill the page
                log.warning("Jupiter price lookup failed for %d mints: %s", len(chunk), exc)
                continue

            # v3 returns a flat map; older shapes nest under "data".
            payload = data.get("data") if isinstance(data, dict) and "data" in data else data
            if not isinstance(payload, dict):
                continue
            for mint, entry in payload.items():
                price = _extract_price(entry)
                if price is not None:
                    prices[mint] = price

        return prices

    async def sol_usd(self) -> float | None:
        prices = await self.spot_usd([WSOL_MINT])
        return prices.get(WSOL_MINT)

    async def sol_daily_history(self, days: int = 30) -> dict[str, float]:
        """{'YYYY-MM-DD': close} for the last `days` days, UTC."""
        try:
            data = await request_json(
                self._client,
                "GET",
                COINGECKO_URL,
                params={"vs_currency": "usd", "days": days, "interval": "daily"},
                limiter=_coingecko_limiter,
            )
        except Exception as exc:
            log.warning("CoinGecko SOL history failed: %s", exc)
            return {}

        out: dict[str, float] = {}
        for point in data.get("prices") or []:
            if not isinstance(point, list) or len(point) != 2:
                continue
            ts_ms, price = point
            day = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
            out[day] = float(price)
        return out
