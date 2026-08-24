"""DexScreener client. No API key, no account.

Note on discovery: DexScreener has no "all new Solana pairs" endpoint.
token-profiles and token-boosts only list tokens whose teams paid for a
profile or a boost, which is a marketing feed, not a launch feed. See
docs/data-sources.md for what that means for the Phase 2 screener.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from .base import RateLimiter, request_json

log = logging.getLogger(__name__)

BASE = "https://api.dexscreener.com"

# Documented limits: 300 req/min for pair endpoints, 60 req/min for the
# profile/boost endpoints. We keep a margin on both.
_pairs_limiter = RateLimiter(rate=240, per=60.0)
_profiles_limiter = RateLimiter(rate=50, per=60.0)


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalise_pair(pair: dict[str, Any]) -> dict[str, Any]:
    """Flatten a DexScreener pair into the fields the overview needs."""
    base = pair.get("baseToken") or {}
    liquidity = pair.get("liquidity") or {}
    volume = pair.get("volume") or {}
    change = pair.get("priceChange") or {}
    txns = pair.get("txns") or {}
    day_txns = txns.get("h24") or {}

    return {
        "mint": base.get("address"),
        "symbol": base.get("symbol"),
        "name": base.get("name"),
        "pair_address": pair.get("pairAddress"),
        "dex": pair.get("dexId"),
        "price_usd": _num(pair.get("priceUsd")),
        "market_cap": _num(pair.get("marketCap")),
        "fdv": _num(pair.get("fdv")),
        "liquidity_usd": _num(liquidity.get("usd")),
        "volume_24h": _num(volume.get("h24")),
        "volume_5m": _num(volume.get("m5")),
        "price_change_5m": _num(change.get("m5")),
        "price_change_1h": _num(change.get("h1")),
        "price_change_24h": _num(change.get("h24")),
        "txns_24h": int((day_txns.get("buys") or 0) + (day_txns.get("sells") or 0)),
        # DexScreener reports this in milliseconds.
        "pair_created_at": int(pair["pairCreatedAt"] / 1000)
        if pair.get("pairCreatedAt")
        else None,
    }


def _best_pair(pairs: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Pick the deepest Solana pool as the canonical one for a mint."""
    solana = [p for p in pairs if p.get("chainId") == "solana"]
    if not solana:
        return None
    return max(solana, key=lambda p: _num((p.get("liquidity") or {}).get("usd")) or 0.0)


class DexScreenerClient:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def tokens(
        self, mints: list[str], *, attempts: int = 5, max_delay: float = 60.0
    ) -> dict[str, dict[str, Any]]:
        """Canonical pair per mint. Accepts up to 30 addresses per call.

        The scheduled refresh keeps the full retry ladder; callers on a
        request path pass a smaller budget.
        """
        unique = list(dict.fromkeys(m for m in mints if m))
        out: dict[str, dict[str, Any]] = {}

        for i in range(0, len(unique), 30):
            chunk = unique[i : i + 30]
            try:
                data = await request_json(
                    self._client,
                    "GET",
                    f"{BASE}/latest/dex/tokens/{','.join(chunk)}",
                    limiter=_pairs_limiter,
                    max_attempts=attempts,
                    max_delay=max_delay,
                )
            except Exception as exc:
                log.warning("DexScreener token lookup failed: %s", exc)
                continue

            by_mint: dict[str, list[dict[str, Any]]] = {}
            for pair in data.get("pairs") or []:
                address = (pair.get("baseToken") or {}).get("address")
                if address:
                    by_mint.setdefault(address, []).append(pair)

            for mint, pairs in by_mint.items():
                best = _best_pair(pairs)
                if best:
                    out[mint] = normalise_pair(best)

        return out

    async def search(self, query: str) -> list[dict[str, Any]]:
        data = await request_json(
            self._client,
            "GET",
            f"{BASE}/latest/dex/search",
            params={"q": query},
            limiter=_pairs_limiter,
        )
        pairs = [p for p in (data.get("pairs") or []) if p.get("chainId") == "solana"]
        return [normalise_pair(p) for p in pairs]

    async def token_profiles(self) -> list[str]:
        """Solana mints from the latest profiles feed. Marketing feed, not launches."""
        data = await request_json(
            self._client, "GET", f"{BASE}/token-profiles/latest/v1", limiter=_profiles_limiter
        )
        entries = data if isinstance(data, list) else []
        return [
            e["tokenAddress"]
            for e in entries
            if e.get("chainId") == "solana" and e.get("tokenAddress")
        ]

    async def token_boosts(self) -> list[str]:
        data = await request_json(
            self._client, "GET", f"{BASE}/token-boosts/latest/v1", limiter=_profiles_limiter
        )
        entries = data if isinstance(data, list) else []
        return [
            e["tokenAddress"]
            for e in entries
            if e.get("chainId") == "solana" and e.get("tokenAddress")
        ]
