"""Tests for the balance-delta swap parser.

These use the shape Helius actually returns, including the awkward cases:
wrapped SOL, an ATA rent charge on the first buy, and transactions that are
not trades at all.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.clients.helius import parse_transaction  # noqa: E402
from app.config import WSOL_MINT  # noqa: E402

WALLET = "3Fk9x1sTPUn8pVQe1oGxG3rn7HmkTxx9ttcSJ4hSJ8xk"
TOKEN = "9BB6NFEcjBCtnNLFko2FqVQBq8HHM13kCyYcdQbgpump"
LAMPORTS = 1_000_000_000


def _tx(native_change: int, token_changes: list[dict], **overrides) -> dict:
    base = {
        "signature": "sig1",
        "timestamp": 1_700_000_000,
        "fee": 5_000,
        "feePayer": WALLET,
        "source": "PUMP_FUN",
        "transactionError": None,
        "accountData": [
            {"account": WALLET, "nativeBalanceChange": native_change, "tokenBalanceChanges": []},
            {"account": "pool", "nativeBalanceChange": -native_change, "tokenBalanceChanges": token_changes},
        ],
    }
    base.update(overrides)
    return base


def _token_change(mint: str, amount: int, decimals: int) -> dict:
    return {
        "userAccount": WALLET,
        "tokenAccount": "ata",
        "mint": mint,
        "rawTokenAmount": {"tokenAmount": str(amount), "decimals": decimals},
    }


def test_buy_with_sol():
    tx = _tx(-1 * LAMPORTS - 5_000, [_token_change(TOKEN, 1_000_000_000, 6)])
    swaps = parse_transaction(tx, WALLET)

    assert len(swaps) == 1
    swap = swaps[0]
    assert swap.side == "buy"
    assert swap.token_mint == TOKEN
    assert swap.token_amount == 1000.0
    assert swap.quote_mint == WSOL_MINT
    # The 5000 lamport fee must not inflate the cost basis.
    assert abs(swap.quote_amount - 1.0) < 1e-9
    assert abs(swap.fee_sol - 0.000005) < 1e-12


def test_sell_for_sol():
    tx = _tx(2 * LAMPORTS - 5_000, [_token_change(TOKEN, -1_000_000_000, 6)])
    swaps = parse_transaction(tx, WALLET)

    assert len(swaps) == 1
    swap = swaps[0]
    assert swap.side == "sell"
    assert swap.token_amount == 1000.0
    assert abs(swap.quote_amount - 2.0) < 1e-9


def test_wrapped_sol_counts_as_sol():
    """A route that wraps SOL first must not look like a WSOL position."""
    tx = _tx(
        -1 * LAMPORTS - 5_000,
        [
            _token_change(TOKEN, 1_000_000_000, 6),
            # WSOL briefly appears and is fully consumed.
            _token_change(WSOL_MINT, 0, 9),
        ],
    )
    swaps = parse_transaction(tx, WALLET)

    assert len(swaps) == 1
    assert swaps[0].token_mint == TOKEN


def test_plain_transfer_is_not_a_trade():
    """Receiving tokens with no SOL leg is an airdrop, not a buy."""
    tx = _tx(0, [_token_change(TOKEN, 5_000_000_000, 6)])
    assert parse_transaction(tx, WALLET) == []


def test_failed_transaction_ignored():
    tx = _tx(-1 * LAMPORTS, [_token_change(TOKEN, 1_000_000_000, 6)])
    tx["transactionError"] = {"InstructionError": [3, {"Custom": 6001}]}
    assert parse_transaction(tx, WALLET) == []


def test_other_wallets_changes_ignored():
    tx = _tx(-1 * LAMPORTS - 5_000, [_token_change(TOKEN, 1_000_000_000, 6)])
    tx["accountData"].append(
        {
            "account": "someone_else",
            "nativeBalanceChange": -50 * LAMPORTS,
            "tokenBalanceChanges": [
                {
                    "userAccount": "someone_else",
                    "mint": TOKEN,
                    "rawTokenAmount": {"tokenAmount": "999999999", "decimals": 6},
                }
            ],
        }
    )
    swaps = parse_transaction(tx, WALLET)

    assert len(swaps) == 1
    assert swaps[0].token_amount == 1000.0


def test_token_to_token_swap_is_skipped_not_guessed():
    other = "So11111111111111111111111111111111111111113"
    tx = _tx(
        -5_000,
        [_token_change(TOKEN, 1_000_000_000, 6), _token_change(other, -2_000_000, 6)],
    )
    assert parse_transaction(tx, WALLET) == []
