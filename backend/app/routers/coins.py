from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query

from ..clients.rugcheck import RugCheckClient
from ..deps import get_http, require_token
from ..services import coins

router = APIRouter(prefix="/api/coins", tags=["coins"], dependencies=[Depends(require_token)])


@router.get("")
async def list_coins(
    sort: str = Query("liquidity_usd"),
    direction: str = Query("desc", pattern="^(asc|desc)$"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    min_liquidity: float | None = None,
    max_top10: float | None = None,
    min_holders: int | None = None,
    min_age_minutes: int | None = None,
    max_age_minutes: int | None = None,
    authorities_revoked: bool = False,
    search: str | None = None,
) -> dict:
    return coins.list_tokens(
        sort=sort,
        direction=direction,
        limit=limit,
        offset=offset,
        min_liquidity=min_liquidity,
        max_top10=max_top10,
        min_holders=min_holders,
        min_age_minutes=min_age_minutes,
        max_age_minutes=max_age_minutes,
        authorities_revoked=authorities_revoked,
        search=search,
    )


@router.post("/refresh")
async def refresh(
    enrich: int = Query(25, ge=0, le=200),
    http: httpx.AsyncClient = Depends(get_http),
) -> dict:
    market = await coins.refresh_market_data(http)
    safety = await coins.enrich_safety(http, limit=enrich) if enrich else 0
    return {"market_updated": market, "safety_updated": safety}


@router.get("/{mint}")
async def coin_detail(mint: str, http: httpx.AsyncClient = Depends(get_http)) -> dict:
    market = await coins.refresh_market_data(http, [mint])
    if not market:
        raise HTTPException(status_code=404, detail="No Solana pair found for this mint")

    report = await RugCheckClient(http).report(mint)
    listing = coins.list_tokens(limit=1, offset=0, search=mint)
    token = listing["tokens"][0] if listing["tokens"] else {"mint": mint}
    token["rugcheck"] = report
    token["links"] = coins.token_links(mint)
    return token
