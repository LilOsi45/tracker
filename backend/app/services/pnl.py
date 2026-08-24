"""FIFO cost-basis engine.

Accounting currency is SOL, because that is what the chain actually records
and it is exact. USD is a presentation layer applied on top, using the daily
SOL close — never the other way round, which would bake a price lookup error
into the cost basis itself.

Lots and realized rows are fully derived: dropping them and calling
`rebuild_wallet` reproduces them from the swap table.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..config import WSOL_MINT

log = logging.getLogger(__name__)

# A sell of fewer than this many tokens beyond what we hold is treated as
# rounding, not as a missing buy.
DUST = 1e-9


@dataclass
class RebuildReport:
    swaps: int = 0
    lots_open: int = 0
    realized_rows: int = 0
    # Sells with no matching buy: the position was airdropped, bought before
    # the synced history, or arrived by transfer. Cost basis is unknown.
    orphan_sells: list[str] = field(default_factory=list)
    # Stablecoin-quoted swaps we could not convert to SOL.
    unconverted: list[str] = field(default_factory=list)


def day_of(block_time: int) -> str:
    return datetime.fromtimestamp(block_time, tz=timezone.utc).strftime("%Y-%m-%d")


def quote_to_sol(
    quote_mint: str,
    quote_amount: float,
    block_time: int,
    sol_prices: dict[str, float],
) -> float | None:
    """Convert a swap's quote leg into SOL. None when the rate is unknown."""
    if quote_mint == WSOL_MINT:
        return quote_amount
    price = sol_prices.get(day_of(block_time))
    if not price:
        return None
    return quote_amount / price


@dataclass
class _Lot:
    buy_signature: str
    acquired_at: int
    qty: float
    cost_per_token: float


def rebuild_wallet(conn: sqlite3.Connection, wallet: str) -> RebuildReport:
    """Recompute lots and realized PnL for one wallet from its swaps."""
    report = RebuildReport()

    sol_prices = {
        row["day"]: row["price_usd"]
        for row in conn.execute("SELECT day, price_usd FROM sol_price_daily")
    }

    conn.execute("DELETE FROM lots WHERE wallet = ?", (wallet,))
    conn.execute("DELETE FROM realized WHERE wallet = ?", (wallet,))

    rows = conn.execute(
        """
        SELECT signature, block_time, token_mint, side, token_amount, quote_mint, quote_amount
        FROM swaps
        WHERE wallet = ?
        ORDER BY block_time ASC, signature ASC, side DESC
        """,
        (wallet,),
    ).fetchall()

    open_lots: dict[str, list[_Lot]] = {}
    realized_rows: list[tuple] = []

    for row in rows:
        report.swaps += 1
        mint = row["token_mint"]
        qty = float(row["token_amount"])
        if qty <= 0:
            continue

        sol_value = quote_to_sol(
            row["quote_mint"], float(row["quote_amount"]), int(row["block_time"]), sol_prices
        )
        if sol_value is None:
            report.unconverted.append(row["signature"])
            continue

        if row["side"] == "buy":
            open_lots.setdefault(mint, []).append(
                _Lot(
                    buy_signature=row["signature"],
                    acquired_at=int(row["block_time"]),
                    qty=qty,
                    cost_per_token=sol_value / qty,
                )
            )
            continue

        # Sell: consume oldest lots first.
        proceeds_per_token = sol_value / qty
        remaining = qty
        lots = open_lots.get(mint, [])

        while remaining > DUST and lots:
            lot = lots[0]
            take = min(remaining, lot.qty)
            proceeds = take * proceeds_per_token
            cost = take * lot.cost_per_token
            realized_rows.append(
                (
                    wallet,
                    mint,
                    row["signature"],
                    lot.buy_signature,
                    lot.acquired_at,
                    int(row["block_time"]),
                    take,
                    proceeds,
                    cost,
                    proceeds - cost,
                )
            )
            lot.qty -= take
            remaining -= take
            if lot.qty <= DUST:
                lots.pop(0)

        if remaining > DUST:
            # No basis on record. Booking it at zero cost would invent a
            # profit, so we record it as a zero-cost disposal and flag it.
            report.orphan_sells.append(row["signature"])
            proceeds = remaining * proceeds_per_token
            realized_rows.append(
                (
                    wallet,
                    mint,
                    row["signature"],
                    "",
                    int(row["block_time"]),
                    int(row["block_time"]),
                    remaining,
                    proceeds,
                    0.0,
                    proceeds,
                )
            )

    conn.executemany(
        """
        INSERT INTO realized
            (wallet, token_mint, sell_signature, buy_signature, acquired_at,
             closed_at, qty, proceeds_sol, cost_sol, pnl_sol)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        realized_rows,
    )
    report.realized_rows = len(realized_rows)

    lot_rows = [
        (wallet, mint, lot.acquired_at, lot.buy_signature, lot.qty, lot.cost_per_token)
        for mint, lots in open_lots.items()
        for lot in lots
        if lot.qty > DUST
    ]
    conn.executemany(
        """
        INSERT INTO lots
            (wallet, token_mint, acquired_at, buy_signature, qty_remaining, cost_per_token)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        lot_rows,
    )
    report.lots_open = len(lot_rows)

    if report.orphan_sells:
        log.info(
            "%s: %d sells without cost basis (pre-history or transferred in)",
            wallet,
            len(report.orphan_sells),
        )
    return report
