"""RugCheck client.

This is where authority status, LP lock and holder distribution come from —
none of those exist in the DexScreener response.

Top-10 concentration is computed with pool and LP accounts removed. A naive
top-10 counts the Raydium vault as a whale and reports 60% concentration for
a perfectly ordinary token, which makes the number useless as a filter.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from ..config import get_settings
from .base import RateLimiter, request_json

log = logging.getLogger(__name__)

BASE = "https://api.rugcheck.xyz/v1"

# Public API is about 1 req/s. Stay just under.
_limiter = RateLimiter(rate=1, per=1.2)


def _pool_accounts(report: dict[str, Any]) -> set[str]:
    """Addresses that hold supply on behalf of a market, not a person."""
    accounts: set[str] = set()
    for market in report.get("markets") or []:
        for key in ("liquidityA", "liquidityB", "pubkey", "marketType"):
            value = market.get(key)
            if isinstance(value, str) and len(value) > 30:
                accounts.add(value)
        lp = market.get("lp") or {}
        for key in ("lpMint", "quoteMint", "baseMint"):
            value = lp.get(key)
            if isinstance(value, str) and len(value) > 30:
                accounts.add(value)
    return accounts


def parse_report(report: dict[str, Any]) -> dict[str, Any]:
    token = report.get("token") or {}
    meta = report.get("tokenMeta") or {}

    excluded = _pool_accounts(report)
    holders = []
    for holder in report.get("topHolders") or []:
        address = holder.get("owner") or holder.get("address")
        if address in excluded:
            continue
        pct = holder.get("pct")
        if isinstance(pct, (int, float)):
            holders.append(float(pct))

    top10_pct = round(sum(sorted(holders, reverse=True)[:10]), 2) if holders else None

    # lpLockedPct lives per market; take the deepest market's value.
    lp_locked_pct = None
    markets = report.get("markets") or []
    if markets:
        best = max(
            markets,
            key=lambda m: (m.get("lp") or {}).get("lpLockedUSD") or 0,
        )
        value = (best.get("lp") or {}).get("lpLockedPct")
        if isinstance(value, (int, float)):
            lp_locked_pct = float(value)

    total_holders = report.get("totalHolders")

    return {
        "symbol": meta.get("symbol"),
        "name": meta.get("name"),
        # RugCheck returns null for a revoked authority.
        "mint_authority": 0 if token.get("mintAuthority") in (None, "") else 1,
        "freeze_authority": 0 if token.get("freezeAuthority") in (None, "") else 1,
        "top10_pct": top10_pct,
        "holder_count": int(total_holders) if isinstance(total_holders, int) else None,
        "lp_locked_pct": lp_locked_pct,
        "rugcheck_score": report.get("score_normalised") or report.get("score"),
        "creator": report.get("creator"),
        "rugged": bool(report.get("rugged")),
        "risks": [
            {"name": r.get("name"), "level": r.get("level"), "description": r.get("description")}
            for r in (report.get("risks") or [])
        ],
    }


class RugCheckClient:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client
        self._settings = get_settings()

    def _headers(self) -> dict[str, str]:
        key = self._settings.rugcheck_api_key
        return {"X-API-KEY": key} if key else {}

    async def report(self, mint: str) -> dict[str, Any] | None:
        """Parsed report, or None when RugCheck has no data for the mint."""
        try:
            data = await request_json(
                self._client,
                "GET",
                f"{BASE}/tokens/{mint}/report",
                headers=self._headers(),
                limiter=_limiter,
            )
        except Exception as exc:
            log.warning("RugCheck report failed for %s: %s", mint, exc)
            return None

        if not isinstance(data, dict) or not data:
            return None
        return parse_report(data)
