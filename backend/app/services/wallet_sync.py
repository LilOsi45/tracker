"""Pull a wallet's trade history from Helius into SQLite, then recompute PnL."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import httpx

from ..clients.helius import HeliusClient, parse_transaction
from ..clients.prices import PriceClient
from ..db import session
from .pnl import RebuildReport, rebuild_wallet

log = logging.getLogger(__name__)

# One page is 100 transactions. A memecoin wallet can have tens of thousands,
# so the first sync walks back in bounded chunks and resumes on the next call.
MAX_PAGES_PER_RUN = 60


@dataclass
class SyncResult:
    wallet: str
    new_swaps: int
    pages_fetched: int
    backfill_complete: bool
    rebuild: RebuildReport


async def refresh_sol_prices(http: httpx.AsyncClient, days: int = 365) -> int:
    """Keep the daily SOL/USD table current. CoinGecko free tier caps history."""
    prices = await PriceClient(http).sol_daily_history(days=days)
    if not prices:
        return 0
    with session() as conn:
        conn.executemany(
            "INSERT INTO sol_price_daily (day, price_usd) VALUES (?, ?) "
            "ON CONFLICT(day) DO UPDATE SET price_usd = excluded.price_usd",
            list(prices.items()),
        )
    return len(prices)


def _store_swaps(wallet: str, swaps: list) -> int:
    if not swaps:
        return 0
    rows = [
        (
            s.signature,
            wallet,
            s.block_time,
            s.source,
            s.token_mint,
            s.side,
            s.token_amount,
            s.quote_mint,
            s.quote_amount,
            s.fee_sol,
        )
        for s in swaps
    ]
    with session() as conn:
        before = conn.execute(
            "SELECT COUNT(*) AS c FROM swaps WHERE wallet = ?", (wallet,)
        ).fetchone()["c"]
        conn.executemany(
            """
            INSERT INTO swaps
                (signature, wallet, block_time, source, token_mint, side,
                 token_amount, quote_mint, quote_amount, fee_sol)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(signature, token_mint, side) DO NOTHING
            """,
            rows,
        )
        after = conn.execute(
            "SELECT COUNT(*) AS c FROM swaps WHERE wallet = ?", (wallet,)
        ).fetchone()["c"]
    return after - before


def _sync_state(wallet: str) -> dict:
    with session() as conn:
        row = conn.execute("SELECT * FROM sync_state WHERE wallet = ?", (wallet,)).fetchone()
        if row is None:
            conn.execute("INSERT INTO sync_state (wallet) VALUES (?)", (wallet,))
            return {
                "newest_signature": None,
                "oldest_signature": None,
                "backfill_complete": 0,
            }
        return dict(row)


async def sync_wallet(http: httpx.AsyncClient, wallet: str) -> SyncResult:
    """Fetch anything new, continue the backfill, then rebuild the ledger."""
    helius = HeliusClient(http)
    state = _sync_state(wallet)

    await refresh_sol_prices(http)

    total_new = 0
    pages = 0
    newest_signature = state.get("newest_signature")
    oldest_signature = state.get("oldest_signature")
    backfill_complete = bool(state.get("backfill_complete"))
    first_signature_this_run: str | None = None

    # 1. Everything newer than the last sync.
    if newest_signature:
        before: str | None = None
        while pages < MAX_PAGES_PER_RUN:
            page = await helius.transactions_page(wallet, before=before, until=newest_signature)
            pages += 1
            if not page:
                break
            if first_signature_this_run is None:
                first_signature_this_run = page[0].get("signature")
            swaps = [s for tx in page for s in parse_transaction(tx, wallet)]
            total_new += _store_swaps(wallet, swaps)
            before = page[-1].get("signature")
            if len(page) < 100:
                break

    # 2. Walk backwards until we reach the beginning of the wallet's history.
    if not backfill_complete:
        before = oldest_signature
        while pages < MAX_PAGES_PER_RUN:
            page = await helius.transactions_page(wallet, before=before)
            pages += 1
            if not page:
                backfill_complete = True
                break
            if first_signature_this_run is None and before is None:
                first_signature_this_run = page[0].get("signature")
            swaps = [s for tx in page for s in parse_transaction(tx, wallet)]
            total_new += _store_swaps(wallet, swaps)
            before = page[-1].get("signature")
            oldest_signature = before
            if len(page) < 100:
                backfill_complete = True
                break

    with session() as conn:
        if first_signature_this_run:
            newest_signature = first_signature_this_run
        conn.execute(
            """
            UPDATE sync_state
               SET newest_signature = COALESCE(?, newest_signature),
                   oldest_signature = ?,
                   backfill_complete = ?,
                   last_synced_at = ?,
                   swaps_seen = (SELECT COUNT(*) FROM swaps WHERE wallet = ?)
             WHERE wallet = ?
            """,
            (
                newest_signature,
                oldest_signature,
                1 if backfill_complete else 0,
                int(time.time()),
                wallet,
                wallet,
            ),
        )
        rebuild = rebuild_wallet(conn, wallet)

    await _refresh_token_meta(http, wallet)

    log.info(
        "synced %s: %d new swaps over %d pages (backfill %s)",
        wallet,
        total_new,
        pages,
        "done" if backfill_complete else "in progress",
    )
    return SyncResult(wallet, total_new, pages, backfill_complete, rebuild)


async def _refresh_token_meta(http: httpx.AsyncClient, wallet: str) -> None:
    """Fill in symbol/name for mints we have not looked up yet."""
    with session() as conn:
        missing = [
            row["token_mint"]
            for row in conn.execute(
                """
                SELECT DISTINCT s.token_mint
                  FROM swaps s
                  LEFT JOIN token_meta m ON m.mint = s.token_mint
                 WHERE s.wallet = ? AND m.mint IS NULL
                 LIMIT 200
                """,
                (wallet,),
            )
        ]
    if not missing:
        return

    try:
        metadata = await HeliusClient(http).token_metadata(missing)
    except Exception as exc:
        log.warning("token metadata lookup failed: %s", exc)
        return

    now = int(time.time())
    with session() as conn:
        conn.executemany(
            "INSERT INTO token_meta (mint, symbol, name, decimals, updated_at) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(mint) DO UPDATE SET "
            "symbol = excluded.symbol, name = excluded.name, "
            "decimals = excluded.decimals, updated_at = excluded.updated_at",
            [
                (mint, meta.get("symbol"), meta.get("name"), meta.get("decimals"), now)
                for mint, meta in metadata.items()
            ],
        )
