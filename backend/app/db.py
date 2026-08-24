"""SQLite access.

Raw swaps are the source of truth; lots and realized events are derived and
can always be rebuilt from the swap table with a full recompute.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator

from .config import ensure_data_dir, get_settings

SCHEMA = """
PRAGMA journal_mode=WAL;

-- Source of truth: one row per swap leg the wallet took part in.
CREATE TABLE IF NOT EXISTS swaps (
    signature      TEXT NOT NULL,
    wallet         TEXT NOT NULL,
    block_time     INTEGER NOT NULL,
    source         TEXT,
    token_mint     TEXT NOT NULL,
    side           TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    token_amount   REAL NOT NULL,
    quote_mint     TEXT NOT NULL,
    quote_amount   REAL NOT NULL,
    fee_sol        REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (signature, token_mint, side)
);
CREATE INDEX IF NOT EXISTS idx_swaps_wallet_time ON swaps (wallet, block_time);
CREATE INDEX IF NOT EXISTS idx_swaps_wallet_mint ON swaps (wallet, token_mint);

-- Derived: open FIFO lots.
CREATE TABLE IF NOT EXISTS lots (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    wallet         TEXT NOT NULL,
    token_mint     TEXT NOT NULL,
    acquired_at    INTEGER NOT NULL,
    buy_signature  TEXT NOT NULL,
    qty_remaining  REAL NOT NULL,
    cost_per_token REAL NOT NULL          -- in SOL
);
CREATE INDEX IF NOT EXISTS idx_lots_wallet_mint ON lots (wallet, token_mint, acquired_at);

-- Derived: one row per closed quantity (a sell may close several lots).
CREATE TABLE IF NOT EXISTS realized (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    wallet         TEXT NOT NULL,
    token_mint     TEXT NOT NULL,
    sell_signature TEXT NOT NULL,
    buy_signature  TEXT NOT NULL,
    acquired_at    INTEGER NOT NULL,
    closed_at      INTEGER NOT NULL,
    qty            REAL NOT NULL,
    proceeds_sol   REAL NOT NULL,
    cost_sol       REAL NOT NULL,
    pnl_sol        REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_realized_wallet_time ON realized (wallet, closed_at);

CREATE TABLE IF NOT EXISTS sync_state (
    wallet             TEXT PRIMARY KEY,
    newest_signature   TEXT,
    oldest_signature   TEXT,
    backfill_complete  INTEGER NOT NULL DEFAULT 0,
    last_synced_at     INTEGER,
    swaps_seen         INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS token_meta (
    mint       TEXT PRIMARY KEY,
    symbol     TEXT,
    name       TEXT,
    decimals   INTEGER,
    updated_at INTEGER
);

-- Spot price cache (Jupiter), refreshed on demand.
CREATE TABLE IF NOT EXISTS price_cache (
    mint       TEXT PRIMARY KEY,
    price_usd  REAL,
    updated_at INTEGER
);

-- Daily SOL/USD close, used to value SOL-denominated PnL in fiat.
CREATE TABLE IF NOT EXISTS sol_price_daily (
    day       TEXT PRIMARY KEY,          -- YYYY-MM-DD (UTC)
    price_usd REAL NOT NULL
);

-- Daily equity snapshot for the 30-day chart.
CREATE TABLE IF NOT EXISTS equity_daily (
    wallet         TEXT NOT NULL,
    day            TEXT NOT NULL,
    realized_sol   REAL NOT NULL DEFAULT 0,
    unrealized_sol REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (wallet, day)
);

-- Merged DexScreener + RugCheck snapshot for the coin overview.
CREATE TABLE IF NOT EXISTS token_snapshot (
    mint                TEXT PRIMARY KEY,
    symbol              TEXT,
    name                TEXT,
    pair_address        TEXT,
    dex                 TEXT,
    price_usd           REAL,
    market_cap          REAL,
    fdv                 REAL,
    liquidity_usd       REAL,
    volume_24h          REAL,
    volume_5m           REAL,
    price_change_5m     REAL,
    price_change_1h     REAL,
    price_change_24h    REAL,
    txns_24h            INTEGER,
    pair_created_at     INTEGER,
    holder_count        INTEGER,        -- NULL when RugCheck did not report it
    top10_pct           REAL,           -- NULL when unknown
    mint_authority      INTEGER,        -- 1 = still set (bad), 0 = revoked, NULL = unknown
    freeze_authority    INTEGER,
    lp_locked_pct       REAL,
    rugcheck_score      INTEGER,
    rugcheck_at         INTEGER,
    updated_at          INTEGER
);
"""


def connect() -> sqlite3.Connection:
    ensure_data_dir()
    conn = sqlite3.connect(get_settings().database_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def session() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with session() as conn:
        conn.executescript(SCHEMA)
