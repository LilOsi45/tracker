"""CSV exports.

Two shapes, because they answer different questions:

* `blockpit` — one row per swap leg, matching Blockpit's generic import
  template. Tax law treats every token/SOL swap as a disposal, so the tax
  export has to be transaction-level; a list of closed positions would not
  be importable.
* `trades` — one row per closed quantity with entry, exit and PnL. This is
  for reading, not for filing.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone

from ..config import WSOL_MINT
from ..db import session

# Column order from Blockpit's generic Excel/CSV template.
BLOCKPIT_COLUMNS = [
    "Date (UTC)",
    "Integration Name",
    "Label",
    "Outgoing Asset",
    "Outgoing Amount",
    "Incoming Asset",
    "Incoming Amount",
    "Fee Asset",
    "Fee Amount",
]

TRADE_COLUMNS = [
    "Opened (UTC)",
    "Closed (UTC)",
    "Symbol",
    "Mint",
    "Quantity",
    "Entry Price (SOL)",
    "Exit Price (SOL)",
    "Cost (SOL)",
    "Proceeds (SOL)",
    "PnL (SOL)",
    "PnL (%)",
    "Buy Signature",
    "Sell Signature",
    "Basis Known",
]


def _utc(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%d.%m.%Y %H:%M:%S")


def _fmt(value: float | None, places: int = 9) -> str:
    if value is None:
        return ""
    return f"{value:.{places}f}".rstrip("0").rstrip(".") or "0"


def _asset_label(mint: str, symbol: str | None) -> str:
    """What to put in the asset column.

    Memecoin tickers collide constantly, so we qualify the symbol with a
    short mint prefix. Blockpit will not auto-resolve these either way and
    they have to be mapped once on import; an ambiguous bare ticker would
    silently map to the wrong asset, which is worse.
    """
    if mint == WSOL_MINT:
        return "SOL"
    if symbol:
        cleaned = "".join(c for c in symbol if c.isalnum())[:10]
        if cleaned:
            return f"{cleaned}-{mint[:4]}"
    return mint[:10]


def blockpit_csv(wallet: str, integration_name: str = "Solana Wallet") -> str:
    with session() as conn:
        rows = conn.execute(
            """
            SELECT s.*, m.symbol
              FROM swaps s
              LEFT JOIN token_meta m ON m.mint = s.token_mint
             WHERE s.wallet = ?
             ORDER BY s.block_time ASC
            """,
            (wallet,),
        ).fetchall()

    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\n")
    writer.writerow(BLOCKPIT_COLUMNS)

    for row in rows:
        token = _asset_label(row["token_mint"], row["symbol"])
        quote = _asset_label(row["quote_mint"], None)

        if row["side"] == "buy":
            outgoing_asset, outgoing_amount = quote, row["quote_amount"]
            incoming_asset, incoming_amount = token, row["token_amount"]
        else:
            outgoing_asset, outgoing_amount = token, row["token_amount"]
            incoming_asset, incoming_amount = quote, row["quote_amount"]

        fee = float(row["fee_sol"] or 0)
        writer.writerow(
            [
                _utc(row["block_time"]),
                integration_name,
                "Trade",
                outgoing_asset,
                _fmt(outgoing_amount),
                incoming_asset,
                _fmt(incoming_amount),
                "SOL" if fee else "",
                _fmt(fee) if fee else "",
            ]
        )

    return buffer.getvalue()


def trades_csv(wallet: str) -> str:
    with session() as conn:
        rows = conn.execute(
            """
            SELECT r.*, m.symbol
              FROM realized r
              LEFT JOIN token_meta m ON m.mint = r.token_mint
             WHERE r.wallet = ?
             ORDER BY r.closed_at ASC
            """,
            (wallet,),
        ).fetchall()

    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\n")
    writer.writerow(TRADE_COLUMNS)

    for row in rows:
        qty = float(row["qty"])
        cost = float(row["cost_sol"])
        writer.writerow(
            [
                _utc(row["acquired_at"]),
                _utc(row["closed_at"]),
                row["symbol"] or "",
                row["token_mint"],
                _fmt(qty, 6),
                _fmt(cost / qty if qty else 0),
                _fmt(float(row["proceeds_sol"]) / qty if qty else 0),
                _fmt(cost),
                _fmt(float(row["proceeds_sol"])),
                _fmt(float(row["pnl_sol"])),
                _fmt(float(row["pnl_sol"]) / cost * 100, 2) if cost else "",
                row["buy_signature"] or "",
                row["sell_signature"],
                "no" if not row["buy_signature"] else "yes",
            ]
        )

    return buffer.getvalue()
