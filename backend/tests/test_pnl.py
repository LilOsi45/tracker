"""Tests for the FIFO engine, against a temporary database."""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

WALLET = "3Fk9x1sTPUn8pVQe1oGxG3rn7HmkTxx9ttcSJ4hSJ8xk"
TOKEN = "9BB6NFEcjBCtnNLFko2FqVQBq8HHM13kCyYcdQbgpump"
WSOL = "So11111111111111111111111111111111111111112"
DAY = 1_700_000_000  # 2023-11-14 UTC


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))

    from app import config

    config.get_settings.cache_clear()

    from app.db import SCHEMA

    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA)
    yield connection
    connection.close()


def add_swap(conn, *, side, token_amount, sol_amount, block_time, signature):
    conn.execute(
        """
        INSERT INTO swaps (signature, wallet, block_time, source, token_mint, side,
                           token_amount, quote_mint, quote_amount, fee_sol)
        VALUES (?, ?, ?, 'TEST', ?, ?, ?, ?, ?, 0)
        """,
        (signature, WALLET, block_time, TOKEN, side, token_amount, WSOL, sol_amount),
    )


def test_single_round_trip(conn):
    from app.services.pnl import rebuild_wallet

    add_swap(conn, side="buy", token_amount=1000, sol_amount=1.0, block_time=DAY, signature="b1")
    add_swap(
        conn, side="sell", token_amount=1000, sol_amount=3.0, block_time=DAY + 60, signature="s1"
    )

    report = rebuild_wallet(conn, WALLET)

    assert report.swaps == 2
    assert report.lots_open == 0
    rows = conn.execute("SELECT * FROM realized WHERE wallet = ?", (WALLET,)).fetchall()
    assert len(rows) == 1
    assert rows[0]["pnl_sol"] == pytest.approx(2.0)


def test_fifo_order_matters(conn):
    """Two buys at different prices, one partial sell: the cheap lot goes first."""
    from app.services.pnl import rebuild_wallet

    add_swap(conn, side="buy", token_amount=1000, sol_amount=1.0, block_time=DAY, signature="b1")
    add_swap(
        conn, side="buy", token_amount=1000, sol_amount=5.0, block_time=DAY + 10, signature="b2"
    )
    add_swap(
        conn, side="sell", token_amount=1000, sol_amount=4.0, block_time=DAY + 20, signature="s1"
    )

    rebuild_wallet(conn, WALLET)

    realized = conn.execute("SELECT * FROM realized WHERE wallet = ?", (WALLET,)).fetchall()
    assert len(realized) == 1
    # Cost basis is the first lot (1.0 SOL), not the average (3.0).
    assert realized[0]["cost_sol"] == pytest.approx(1.0)
    assert realized[0]["pnl_sol"] == pytest.approx(3.0)

    lots = conn.execute("SELECT * FROM lots WHERE wallet = ?", (WALLET,)).fetchall()
    assert len(lots) == 1
    assert lots[0]["qty_remaining"] == pytest.approx(1000)
    assert lots[0]["cost_per_token"] == pytest.approx(0.005)


def test_sell_spanning_two_lots(conn):
    from app.services.pnl import rebuild_wallet

    add_swap(conn, side="buy", token_amount=500, sol_amount=1.0, block_time=DAY, signature="b1")
    add_swap(
        conn, side="buy", token_amount=500, sol_amount=2.0, block_time=DAY + 10, signature="b2"
    )
    add_swap(
        conn, side="sell", token_amount=1000, sol_amount=6.0, block_time=DAY + 20, signature="s1"
    )

    rebuild_wallet(conn, WALLET)

    realized = conn.execute(
        "SELECT * FROM realized WHERE wallet = ? ORDER BY id", (WALLET,)
    ).fetchall()
    assert len(realized) == 2
    assert sum(r["pnl_sol"] for r in realized) == pytest.approx(3.0)
    assert realized[0]["buy_signature"] == "b1"
    assert realized[1]["buy_signature"] == "b2"


def test_sell_without_basis_is_flagged_not_invented(conn):
    from app.services.pnl import rebuild_wallet

    add_swap(
        conn, side="sell", token_amount=1000, sol_amount=2.0, block_time=DAY, signature="s1"
    )

    report = rebuild_wallet(conn, WALLET)

    assert report.orphan_sells == ["s1"]
    row = conn.execute("SELECT * FROM realized WHERE wallet = ?", (WALLET,)).fetchone()
    assert row["cost_sol"] == 0.0
    assert row["buy_signature"] == ""


def test_rebuild_is_idempotent(conn):
    from app.services.pnl import rebuild_wallet

    add_swap(conn, side="buy", token_amount=1000, sol_amount=1.0, block_time=DAY, signature="b1")
    add_swap(
        conn, side="sell", token_amount=400, sol_amount=1.0, block_time=DAY + 60, signature="s1"
    )

    first = rebuild_wallet(conn, WALLET)
    second = rebuild_wallet(conn, WALLET)

    assert first.lots_open == second.lots_open
    assert first.realized_rows == second.realized_rows
    assert (
        conn.execute("SELECT COUNT(*) FROM realized WHERE wallet = ?", (WALLET,)).fetchone()[0] == 1
    )


def test_stablecoin_leg_without_rate_is_skipped(conn):
    """A USDC-quoted swap with no SOL rate on file must not be valued at zero."""
    from app.services.pnl import rebuild_wallet

    usdc = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    conn.execute(
        """
        INSERT INTO swaps (signature, wallet, block_time, source, token_mint, side,
                           token_amount, quote_mint, quote_amount, fee_sol)
        VALUES ('u1', ?, ?, 'TEST', ?, 'buy', 1000, ?, 150, 0)
        """,
        (WALLET, DAY, TOKEN, usdc),
    )

    report = rebuild_wallet(conn, WALLET)

    assert report.unconverted == ["u1"]
    assert report.lots_open == 0


def test_stablecoin_leg_converts_with_daily_rate(conn):
    from app.services.pnl import rebuild_wallet

    usdc = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    conn.execute("INSERT INTO sol_price_daily (day, price_usd) VALUES ('2023-11-14', 50.0)")
    conn.execute(
        """
        INSERT INTO swaps (signature, wallet, block_time, source, token_mint, side,
                           token_amount, quote_mint, quote_amount, fee_sol)
        VALUES ('u1', ?, ?, 'TEST', ?, 'buy', 1000, ?, 150, 0)
        """,
        (WALLET, DAY, TOKEN, usdc),
    )

    report = rebuild_wallet(conn, WALLET)

    assert report.unconverted == []
    lot = conn.execute("SELECT * FROM lots WHERE wallet = ?", (WALLET,)).fetchone()
    # 150 USDC / 50 USD per SOL = 3 SOL over 1000 tokens.
    assert lot["cost_per_token"] == pytest.approx(0.003)
