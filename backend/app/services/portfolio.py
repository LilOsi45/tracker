"""Read models for the wallet screens: summary, open positions, history, chart."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from ..clients.dexscreener import DexScreenerClient
from ..clients.prices import PriceClient
from ..config import WSOL_MINT, load_yaml_config
from ..db import session

log = logging.getLogger(__name__)


def _tz() -> ZoneInfo:
    name = (load_yaml_config().get("app") or {}).get("timezone") or "Europe/Berlin"
    try:
        return ZoneInfo(name)
    except Exception:
        log.warning("unknown timezone %r, falling back to UTC", name)
        return ZoneInfo("UTC")


def _day_bounds(days_ago: int = 0) -> tuple[int, int]:
    """Unix bounds of a local calendar day."""
    tz = _tz()
    now = datetime.now(tz)
    start_local = (now - timedelta(days=days_ago)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    end_local = start_local + timedelta(days=1)
    return int(start_local.timestamp()), int(end_local.timestamp())


def _meta_map(conn, mints: list[str]) -> dict[str, dict[str, Any]]:
    if not mints:
        return {}
    placeholders = ",".join("?" * len(mints))
    return {
        row["mint"]: dict(row)
        for row in conn.execute(
            f"SELECT mint, symbol, name FROM token_meta WHERE mint IN ({placeholders})", mints
        )
    }


async def _current_prices(
    http: httpx.AsyncClient, mints: list[str]
) -> tuple[dict[str, float], float | None]:
    """USD price per mint plus the current SOL/USD rate.

    Jupiter is the primary source. Memecoins that have already died are not
    routable and simply have no price; we fall back to DexScreener's last
    traded price for those rather than showing a stale entry price as current.
    """
    price_client = PriceClient(http)
    prices = await price_client.spot_usd(mints + [WSOL_MINT])
    sol_usd = prices.pop(WSOL_MINT, None)

    unpriced = [m for m in mints if m not in prices]
    if unpriced:
        try:
            fallback = await DexScreenerClient(http).tokens(unpriced)
        except Exception as exc:
            log.warning("DexScreener price fallback failed: %s", exc)
            fallback = {}
        for mint, pair in fallback.items():
            if pair.get("price_usd"):
                prices[mint] = pair["price_usd"]

    return prices, sol_usd


async def open_positions(http: httpx.AsyncClient, wallet: str) -> dict[str, Any]:
    with session() as conn:
        rows = conn.execute(
            """
            SELECT token_mint,
                   SUM(qty_remaining)                     AS qty,
                   SUM(qty_remaining * cost_per_token)    AS cost_sol,
                   MIN(acquired_at)                       AS first_buy
              FROM lots
             WHERE wallet = ?
             GROUP BY token_mint
             HAVING qty > 0
            """,
            (wallet,),
        ).fetchall()
        mints = [r["token_mint"] for r in rows]
        meta = _meta_map(conn, mints)

    prices, sol_usd = await _current_prices(http, mints)

    positions: list[dict[str, Any]] = []
    total_cost = 0.0
    total_value = 0.0
    unpriced: list[str] = []

    for row in rows:
        mint = row["token_mint"]
        qty = float(row["qty"])
        cost_sol = float(row["cost_sol"])
        entry_sol_per_token = cost_sol / qty if qty else 0.0

        price_usd = prices.get(mint)
        if price_usd is None or not sol_usd:
            unpriced.append(mint)
            value_sol = None
            pnl_sol = None
            pnl_pct = None
        else:
            value_sol = qty * price_usd / sol_usd
            pnl_sol = value_sol - cost_sol
            pnl_pct = (pnl_sol / cost_sol * 100) if cost_sol else None
            total_value += value_sol

        total_cost += cost_sol

        positions.append(
            {
                "mint": mint,
                "symbol": (meta.get(mint) or {}).get("symbol"),
                "name": (meta.get(mint) or {}).get("name"),
                "qty": qty,
                "cost_sol": cost_sol,
                "entry_price_sol": entry_sol_per_token,
                "entry_price_usd": entry_sol_per_token * sol_usd if sol_usd else None,
                "current_price_usd": price_usd,
                "value_sol": value_sol,
                "value_usd": value_sol * sol_usd if value_sol is not None and sol_usd else None,
                "unrealized_sol": pnl_sol,
                "unrealized_usd": pnl_sol * sol_usd if pnl_sol is not None and sol_usd else None,
                "unrealized_pct": pnl_pct,
                "first_buy": row["first_buy"],
            }
        )

    positions.sort(key=lambda p: p["value_sol"] if p["value_sol"] is not None else -1, reverse=True)

    return {
        "positions": positions,
        "totals": {
            "cost_sol": total_cost,
            "value_sol": total_value,
            "unrealized_sol": total_value - total_cost,
            "unrealized_usd": (total_value - total_cost) * sol_usd if sol_usd else None,
        },
        "sol_usd": sol_usd,
        # Surfaced in the UI rather than silently shown as a zero.
        "unpriced_mints": unpriced,
    }


async def summary(http: httpx.AsyncClient, wallet: str) -> dict[str, Any]:
    """Today's realized and unrealized PnL, kept strictly separate."""
    start, end = _day_bounds(0)

    with session() as conn:
        today = conn.execute(
            """
            SELECT COALESCE(SUM(pnl_sol), 0)      AS pnl_sol,
                   COALESCE(SUM(proceeds_sol), 0) AS proceeds_sol,
                   COUNT(*)                       AS closes
              FROM realized
             WHERE wallet = ? AND closed_at >= ? AND closed_at < ?
            """,
            (wallet, start, end),
        ).fetchone()
        lifetime = conn.execute(
            "SELECT COALESCE(SUM(pnl_sol), 0) AS pnl_sol FROM realized WHERE wallet = ?",
            (wallet,),
        ).fetchone()
        state = conn.execute(
            "SELECT * FROM sync_state WHERE wallet = ?", (wallet,)
        ).fetchone()

    holdings = await open_positions(http, wallet)
    sol_usd = holdings["sol_usd"]

    return {
        "wallet": wallet,
        "sol_usd": sol_usd,
        "realized_today_sol": today["pnl_sol"],
        "realized_today_usd": today["pnl_sol"] * sol_usd if sol_usd else None,
        "closes_today": today["closes"],
        "unrealized_sol": holdings["totals"]["unrealized_sol"],
        "unrealized_usd": holdings["totals"]["unrealized_usd"],
        "open_value_sol": holdings["totals"]["value_sol"],
        "open_positions": len(holdings["positions"]),
        "realized_lifetime_sol": lifetime["pnl_sol"],
        "sync": {
            "last_synced_at": state["last_synced_at"] if state else None,
            "backfill_complete": bool(state["backfill_complete"]) if state else False,
            "swaps_seen": state["swaps_seen"] if state else 0,
        },
    }


def daily_series(wallet: str, days: int = 30) -> dict[str, Any]:
    """Realized PnL per day plus whatever unrealized snapshots we have.

    Realized is exact, computed from the ledger. Unrealized is only available
    from the day this tool started taking daily snapshots, because per-token
    historical prices are not reconstructible from the free APIs.
    """
    tz = _tz()
    today = datetime.now(tz).date()
    start_day = today - timedelta(days=days - 1)
    start_ts = int(datetime.combine(start_day, datetime.min.time(), tzinfo=tz).timestamp())

    with session() as conn:
        realized_rows = conn.execute(
            "SELECT closed_at, pnl_sol FROM realized WHERE wallet = ? AND closed_at >= ?",
            (wallet, start_ts),
        ).fetchall()
        snapshots = {
            row["day"]: row["unrealized_sol"]
            for row in conn.execute(
                "SELECT day, unrealized_sol FROM equity_daily WHERE wallet = ? AND day >= ?",
                (wallet, start_day.isoformat()),
            )
        }

    buckets: dict[str, float] = {}
    for row in realized_rows:
        day = datetime.fromtimestamp(row["closed_at"], tz=tz).strftime("%Y-%m-%d")
        buckets[day] = buckets.get(day, 0.0) + float(row["pnl_sol"])

    series = []
    cumulative = 0.0
    for offset in range(days):
        day = (start_day + timedelta(days=offset)).isoformat()
        realized = buckets.get(day, 0.0)
        cumulative += realized
        series.append(
            {
                "day": day,
                "realized_sol": realized,
                "cumulative_realized_sol": cumulative,
                "unrealized_sol": snapshots.get(day),
            }
        )

    return {
        "series": series,
        "has_unrealized_history": bool(snapshots),
    }


def closed_trades(wallet: str, limit: int = 200, offset: int = 0) -> dict[str, Any]:
    with session() as conn:
        rows = conn.execute(
            """
            SELECT r.*, m.symbol, m.name
              FROM realized r
              LEFT JOIN token_meta m ON m.mint = r.token_mint
             WHERE r.wallet = ?
             ORDER BY r.closed_at DESC
             LIMIT ? OFFSET ?
            """,
            (wallet, limit, offset),
        ).fetchall()
        total = conn.execute(
            "SELECT COUNT(*) AS c FROM realized WHERE wallet = ?", (wallet,)
        ).fetchone()["c"]
        sol_price = conn.execute(
            "SELECT price_usd FROM sol_price_daily ORDER BY day DESC LIMIT 1"
        ).fetchone()

    rate = sol_price["price_usd"] if sol_price else None
    trades = []
    for row in rows:
        qty = float(row["qty"])
        trades.append(
            {
                "mint": row["token_mint"],
                "symbol": row["symbol"],
                "name": row["name"],
                "opened_at": row["acquired_at"],
                "closed_at": row["closed_at"],
                "qty": qty,
                "entry_price_sol": (row["cost_sol"] / qty) if qty else 0.0,
                "exit_price_sol": (row["proceeds_sol"] / qty) if qty else 0.0,
                "cost_sol": row["cost_sol"],
                "proceeds_sol": row["proceeds_sol"],
                "pnl_sol": row["pnl_sol"],
                "pnl_pct": (row["pnl_sol"] / row["cost_sol"] * 100) if row["cost_sol"] else None,
                "pnl_usd": row["pnl_sol"] * rate if rate else None,
                "buy_signature": row["buy_signature"],
                "sell_signature": row["sell_signature"],
                # True when the buy predates the synced history.
                "basis_unknown": not row["buy_signature"],
            }
        )

    return {"trades": trades, "total": total, "limit": limit, "offset": offset}


async def snapshot_equity(http: httpx.AsyncClient, wallet: str) -> None:
    """Record today's unrealized total so the 30-day chart fills in over time."""
    holdings = await open_positions(http, wallet)
    start, _ = _day_bounds(0)
    day = datetime.fromtimestamp(start, tz=_tz()).strftime("%Y-%m-%d")

    with session() as conn:
        realized = conn.execute(
            "SELECT COALESCE(SUM(pnl_sol), 0) AS p FROM realized "
            "WHERE wallet = ? AND closed_at >= ?",
            (wallet, start),
        ).fetchone()["p"]
        conn.execute(
            """
            INSERT INTO equity_daily (wallet, day, realized_sol, unrealized_sol)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(wallet, day) DO UPDATE SET
                realized_sol = excluded.realized_sol,
                unrealized_sol = excluded.unrealized_sol
            """,
            (wallet, day, realized, holdings["totals"]["unrealized_sol"]),
        )
    log.info("equity snapshot for %s on %s", wallet, day)
