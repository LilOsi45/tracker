from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from ..deps import get_http, require_token, valid_wallet
from ..services import csv_export, portfolio, wallet_sync

router = APIRouter(prefix="/api/wallet", tags=["wallet"], dependencies=[Depends(require_token)])


@router.post("/{address}/sync")
async def sync(address: str, http: httpx.AsyncClient = Depends(get_http)) -> dict:
    wallet = valid_wallet(address)
    result = await wallet_sync.sync_wallet(http, wallet)
    return {
        "wallet": result.wallet,
        "new_swaps": result.new_swaps,
        "pages_fetched": result.pages_fetched,
        "backfill_complete": result.backfill_complete,
        "total_swaps": result.rebuild.swaps,
        "open_lots": result.rebuild.lots_open,
        "realized_rows": result.rebuild.realized_rows,
        "sells_without_basis": len(result.rebuild.orphan_sells),
        "unconverted_swaps": len(result.rebuild.unconverted),
    }


@router.get("/{address}/summary")
async def summary(address: str, http: httpx.AsyncClient = Depends(get_http)) -> dict:
    return await portfolio.summary(http, valid_wallet(address))


@router.get("/{address}/positions")
async def positions(address: str, http: httpx.AsyncClient = Depends(get_http)) -> dict:
    return await portfolio.open_positions(http, valid_wallet(address))


@router.get("/{address}/chart")
async def chart(address: str, days: int = Query(30, ge=1, le=365)) -> dict:
    return portfolio.daily_series(valid_wallet(address), days=days)


@router.get("/{address}/trades")
async def trades(
    address: str,
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> dict:
    return portfolio.closed_trades(valid_wallet(address), limit=limit, offset=offset)


@router.get("/{address}/export/blockpit.csv")
async def export_blockpit(address: str) -> Response:
    wallet = valid_wallet(address)
    return Response(
        content=csv_export.blockpit_csv(wallet),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="blockpit-{wallet[:6]}.csv"'
        },
    )


@router.get("/{address}/export/trades.csv")
async def export_trades(address: str) -> Response:
    wallet = valid_wallet(address)
    return Response(
        content=csv_export.trades_csv(wallet),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="trades-{wallet[:6]}.csv"'},
    )
