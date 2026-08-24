"""Coin overview: merge DexScreener market data with RugCheck safety data.

Which field comes from where matters, because the two have very different
rate limits. DexScreener is cheap and batched (30 mints per call); RugCheck
is one call per mint at roughly 1 req/s. So market data is refreshed for the
whole universe, and RugCheck enrichment is applied to the subset that passes
the cheap gates first, oldest data first.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from ..clients.dexscreener import DexScreenerClient
from ..clients.rugcheck import RugCheckClient
from ..config import load_yaml_config
from ..db import session

log = logging.getLogger(__name__)

SORTABLE = {
    "market_cap",
    "liquidity_usd",
    "volume_24h",
    "volume_5m",
    "holder_count",
    "top10_pct",
    "pair_created_at",
    "price_change_5m",
    "price_change_1h",
    "price_change_24h",
    "rugcheck_score",
    "txns_24h",
}


async def discover_mints(http: httpx.AsyncClient) -> list[str]:
    """Candidate mints for the overview.

    DexScreener has no new-pairs endpoint, so this is a watchlist plus the
    paid-placement feeds plus configured search terms. It is explicitly not
    a complete view of new launches — see docs/data-sources.md.
    """
    config = (load_yaml_config().get("coins") or {})
    mints: list[str] = list(config.get("watchlist") or [])

    client = DexScreenerClient(http)
    sources = config.get("discovery") or {}

    if sources.get("token_profiles", True):
        try:
            mints += await client.token_profiles()
        except Exception as exc:
            log.warning("token profiles feed failed: %s", exc)

    if sources.get("token_boosts", True):
        try:
            mints += await client.token_boosts()
        except Exception as exc:
            log.warning("token boosts feed failed: %s", exc)

    for term in sources.get("search_terms") or []:
        try:
            for pair in await client.search(term):
                if pair.get("mint"):
                    mints.append(pair["mint"])
        except Exception as exc:
            log.warning("search %r failed: %s", term, exc)

    return list(dict.fromkeys(m for m in mints if m))


def _upsert_market(conn, snapshots: dict[str, dict[str, Any]]) -> None:
    now = int(time.time())
    conn.executemany(
        """
        INSERT INTO token_snapshot
            (mint, symbol, name, pair_address, dex, price_usd, market_cap, fdv,
             liquidity_usd, volume_24h, volume_5m, price_change_5m, price_change_1h,
             price_change_24h, txns_24h, pair_created_at, updated_at)
        VALUES (:mint, :symbol, :name, :pair_address, :dex, :price_usd, :market_cap, :fdv,
                :liquidity_usd, :volume_24h, :volume_5m, :price_change_5m, :price_change_1h,
                :price_change_24h, :txns_24h, :pair_created_at, :updated_at)
        ON CONFLICT(mint) DO UPDATE SET
            symbol = excluded.symbol, name = excluded.name,
            pair_address = excluded.pair_address, dex = excluded.dex,
            price_usd = excluded.price_usd, market_cap = excluded.market_cap,
            fdv = excluded.fdv, liquidity_usd = excluded.liquidity_usd,
            volume_24h = excluded.volume_24h, volume_5m = excluded.volume_5m,
            price_change_5m = excluded.price_change_5m,
            price_change_1h = excluded.price_change_1h,
            price_change_24h = excluded.price_change_24h,
            txns_24h = excluded.txns_24h,
            pair_created_at = excluded.pair_created_at,
            updated_at = excluded.updated_at
        """,
        [{**data, "updated_at": now} for data in snapshots.values()],
    )


async def refresh_market_data(http: httpx.AsyncClient, mints: list[str] | None = None) -> int:
    if mints is None:
        mints = await discover_mints(http)
    if not mints:
        return 0

    snapshots = await DexScreenerClient(http).tokens(mints)
    if not snapshots:
        return 0

    with session() as conn:
        _upsert_market(conn, snapshots)
    return len(snapshots)


async def enrich_safety(http: httpx.AsyncClient, limit: int = 25) -> int:
    """Add RugCheck data to the mints whose safety data is oldest or missing."""
    stale_after = int(time.time()) - 3600

    with session() as conn:
        rows = conn.execute(
            """
            SELECT mint FROM token_snapshot
             WHERE rugcheck_at IS NULL OR rugcheck_at < ?
             ORDER BY COALESCE(rugcheck_at, 0) ASC, liquidity_usd DESC
             LIMIT ?
            """,
            (stale_after, limit),
        ).fetchall()

    client = RugCheckClient(http)
    updated = 0
    now = int(time.time())

    for row in rows:
        report = await client.report(row["mint"])
        if report is None:
            # Mark the attempt so one unknown mint does not block the queue.
            with session() as conn:
                conn.execute(
                    "UPDATE token_snapshot SET rugcheck_at = ? WHERE mint = ?", (now, row["mint"])
                )
            continue

        with session() as conn:
            conn.execute(
                """
                UPDATE token_snapshot
                   SET holder_count = ?, top10_pct = ?, mint_authority = ?,
                       freeze_authority = ?, lp_locked_pct = ?, rugcheck_score = ?,
                       rugcheck_at = ?
                 WHERE mint = ?
                """,
                (
                    report["holder_count"],
                    report["top10_pct"],
                    report["mint_authority"],
                    report["freeze_authority"],
                    report["lp_locked_pct"],
                    report["rugcheck_score"],
                    now,
                    row["mint"],
                ),
            )
        updated += 1

    return updated


def list_tokens(
    *,
    sort: str = "liquidity_usd",
    direction: str = "desc",
    limit: int = 100,
    offset: int = 0,
    min_liquidity: float | None = None,
    max_top10: float | None = None,
    min_holders: int | None = None,
    min_age_minutes: int | None = None,
    max_age_minutes: int | None = None,
    authorities_revoked: bool = False,
    search: str | None = None,
) -> dict[str, Any]:
    if sort not in SORTABLE:
        sort = "liquidity_usd"
    order = "DESC" if direction.lower() != "asc" else "ASC"

    where: list[str] = []
    params: list[Any] = []
    now = int(time.time())

    if min_liquidity is not None:
        where.append("liquidity_usd >= ?")
        params.append(min_liquidity)
    if max_top10 is not None:
        where.append("top10_pct IS NOT NULL AND top10_pct <= ?")
        params.append(max_top10)
    if min_holders is not None:
        where.append("holder_count IS NOT NULL AND holder_count >= ?")
        params.append(min_holders)
    if min_age_minutes is not None:
        where.append("pair_created_at IS NOT NULL AND pair_created_at <= ?")
        params.append(now - min_age_minutes * 60)
    if max_age_minutes is not None:
        where.append("pair_created_at IS NOT NULL AND pair_created_at >= ?")
        params.append(now - max_age_minutes * 60)
    if authorities_revoked:
        where.append("mint_authority = 0 AND freeze_authority = 0")
    if search:
        where.append("(symbol LIKE ? OR name LIKE ? OR mint LIKE ?)")
        params += [f"%{search}%"] * 3

    clause = f"WHERE {' AND '.join(where)}" if where else ""

    with session() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) AS c FROM token_snapshot {clause}", params
        ).fetchone()["c"]
        rows = conn.execute(
            f"""
            SELECT * FROM token_snapshot {clause}
             ORDER BY {sort} IS NULL, {sort} {order}
             LIMIT ? OFFSET ?
            """,
            [*params, limit, offset],
        ).fetchall()

    tokens = []
    for row in rows:
        data = dict(row)
        created = data.get("pair_created_at")
        data["age_minutes"] = round((now - created) / 60) if created else None
        liquidity = data.get("liquidity_usd") or 0
        volume = data.get("volume_24h") or 0
        data["volume_liquidity_ratio"] = round(volume / liquidity, 2) if liquidity else None
        data["links"] = token_links(data["mint"])
        tokens.append(data)

    return {"tokens": tokens, "total": total, "limit": limit, "offset": offset}


def token_links(mint: str) -> dict[str, str]:
    return {
        "axiom": f"https://axiom.trade/t/{mint}",
        "rugcheck": f"https://rugcheck.xyz/tokens/{mint}",
        "bubblemaps": f"https://app.bubblemaps.io/sol/token/{mint}",
        "dexscreener": f"https://dexscreener.com/solana/{mint}",
        "solscan": f"https://solscan.io/token/{mint}",
    }
