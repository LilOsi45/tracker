"""Helius client.

We deliberately do NOT trust `events.swap` for trade reconstruction. Helius
parses the well-known programs, but memecoin routes go through whatever is
new that week and then arrive as type UNKNOWN with no swap event.

Instead we read `accountData`, which Helius fills in for every transaction
regardless of program, and compute the wallet's own net balance deltas. A
trade is then whatever moved: SOL out + one token in is a buy. That works for
Pump.fun, Raydium, Meteora, Jupiter and anything that ships next month.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from ..config import QUOTE_MINTS, WSOL_MINT, get_settings
from .base import ApiError, RateLimiter, request_json

log = logging.getLogger(__name__)

# Free tier is 10 RPC req/s; stay well under it so a backfill cannot starve
# the interactive endpoints.
_limiter = RateLimiter(rate=5, per=1.0)

LAMPORTS = 1_000_000_000


@dataclass(slots=True)
class ParsedSwap:
    signature: str
    block_time: int
    source: str
    token_mint: str
    side: str  # "buy" | "sell"
    token_amount: float
    quote_mint: str
    quote_amount: float  # always positive, in UI units of quote_mint
    fee_sol: float


def _wallet_deltas(tx: dict[str, Any], wallet: str) -> tuple[float, dict[str, float]]:
    """Net SOL delta (UI units) and per-mint token deltas for `wallet`."""
    lamports = 0
    tokens: dict[str, float] = {}

    for entry in tx.get("accountData") or []:
        if entry.get("account") == wallet:
            lamports += int(entry.get("nativeBalanceChange") or 0)

        for change in entry.get("tokenBalanceChanges") or []:
            if change.get("userAccount") != wallet:
                continue
            raw = change.get("rawTokenAmount") or {}
            try:
                amount = int(raw.get("tokenAmount", 0))
                decimals = int(raw.get("decimals", 0))
            except (TypeError, ValueError):
                continue
            mint = change.get("mint")
            if not mint:
                continue
            tokens[mint] = tokens.get(mint, 0.0) + amount / (10**decimals)

    sol = lamports / LAMPORTS
    # Wrapped SOL is the same money, just temporarily in an ATA.
    if WSOL_MINT in tokens:
        sol += tokens.pop(WSOL_MINT)
    return sol, tokens


def parse_transaction(tx: dict[str, Any], wallet: str) -> list[ParsedSwap]:
    """Turn one enhanced transaction into zero or more swap legs.

    Returns [] for anything that is not a two-sided trade — transfers, mints,
    airdrops, staking. Those are intentionally not positions.
    """
    if tx.get("transactionError"):
        return []

    signature = tx.get("signature")
    timestamp = tx.get("timestamp")
    if not signature or not timestamp:
        return []

    fee_sol = 0.0
    if tx.get("feePayer") == wallet:
        fee_sol = int(tx.get("fee") or 0) / LAMPORTS

    sol_delta, token_deltas = _wallet_deltas(tx, wallet)

    # The fee is a cost of doing business, not part of the traded amount.
    trade_sol = sol_delta + fee_sol if tx.get("feePayer") == wallet else sol_delta

    # Ignore dust: rent for a new ATA, priority fee rounding, etc.
    token_deltas = {m: d for m, d in token_deltas.items() if abs(d) > 1e-12}
    if not token_deltas:
        return []

    source = tx.get("source") or "UNKNOWN"

    gained = {m: d for m, d in token_deltas.items() if d > 0 and m not in QUOTE_MINTS}
    lost = {m: -d for m, d in token_deltas.items() if d < 0 and m not in QUOTE_MINTS}

    # Stablecoin legs count as the quote side, like SOL does.
    stable_delta = sum(d for m, d in token_deltas.items() if m in QUOTE_MINTS and m != WSOL_MINT)
    stable_mint = next(
        (m for m in token_deltas if m in QUOTE_MINTS and m != WSOL_MINT), None
    )

    swaps: list[ParsedSwap] = []

    def add(mint: str, side: str, amount: float, quote_mint: str, quote_amount: float) -> None:
        if amount <= 0 or quote_amount <= 0:
            return
        swaps.append(
            ParsedSwap(
                signature=signature,
                block_time=int(timestamp),
                source=source,
                token_mint=mint,
                side=side,
                token_amount=amount,
                quote_mint=quote_mint,
                quote_amount=quote_amount,
                # Attribute the fee once per transaction, to the first leg.
                fee_sol=fee_sol if not swaps else 0.0,
            )
        )

    # Buy: quote goes out, exactly one token comes in.
    if len(gained) == 1 and not lost:
        mint, amount = next(iter(gained.items()))
        if trade_sol < 0:
            add(mint, "buy", amount, WSOL_MINT, -trade_sol)
        elif stable_mint and stable_delta < 0:
            add(mint, "buy", amount, stable_mint, -stable_delta)
        return swaps

    # Sell: exactly one token goes out, quote comes in.
    if len(lost) == 1 and not gained:
        mint, amount = next(iter(lost.items()))
        if trade_sol > 0:
            add(mint, "sell", amount, WSOL_MINT, trade_sol)
        elif stable_mint and stable_delta > 0:
            add(mint, "sell", amount, stable_mint, stable_delta)
        return swaps

    # Token -> token swap: record both legs, priced through the SOL that would
    # have been involved. Without a reliable mid-price we cannot split the
    # value, so we skip rather than invent one.
    if len(gained) == 1 and len(lost) == 1:
        log.debug("token->token swap %s not priced, skipped", signature)
        return []

    return []


class HeliusClient:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client
        self._settings = get_settings()

    @property
    def configured(self) -> bool:
        return bool(self._settings.helius_api_key)

    async def transactions_page(
        self, address: str, *, before: str | None = None, until: str | None = None
    ) -> list[dict[str, Any]]:
        if not self.configured:
            raise ApiError("HELIUS_API_KEY is not set")
        params: dict[str, Any] = {"api-key": self._settings.helius_api_key, "limit": 100}
        if before:
            params["before"] = before
        if until:
            params["until"] = until
        url = f"{self._settings.helius_base}/addresses/{address}/transactions"
        data = await request_json(self._client, "GET", url, params=params, limiter=_limiter)
        return data if isinstance(data, list) else []

    async def token_metadata(self, mints: list[str]) -> dict[str, dict[str, Any]]:
        """Symbol/name/decimals via the DAS getAssetBatch RPC method."""
        if not mints or not self.configured:
            return {}
        out: dict[str, dict[str, Any]] = {}
        for i in range(0, len(mints), 100):
            chunk = mints[i : i + 100]
            payload = {
                "jsonrpc": "2.0",
                "id": "meta",
                "method": "getAssetBatch",
                "params": {"ids": chunk},
            }
            data = await request_json(
                self._client, "POST", self._settings.helius_rpc, json=payload, limiter=_limiter
            )
            for asset in data.get("result") or []:
                if not asset:
                    continue
                metadata = (asset.get("content") or {}).get("metadata") or {}
                info = asset.get("token_info") or {}
                out[asset["id"]] = {
                    "symbol": metadata.get("symbol") or info.get("symbol"),
                    "name": metadata.get("name"),
                    "decimals": info.get("decimals"),
                }
        return out

    async def sol_balance(self, address: str) -> float:
        payload = {
            "jsonrpc": "2.0",
            "id": "bal",
            "method": "getBalance",
            "params": [address],
        }
        data = await request_json(
            self._client, "POST", self._settings.helius_rpc, json=payload, limiter=_limiter
        )
        value = (data.get("result") or {}).get("value")
        return (value or 0) / LAMPORTS
