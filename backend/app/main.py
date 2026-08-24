from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import REPO_DIR, get_settings, load_yaml_config
from .db import init_db
from .routers import coins as coins_router
from .routers import wallet as wallet_router
from .services import coins as coins_service
from .services import portfolio, wallet_sync

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s"
)
log = logging.getLogger("tracker")

FRONTEND_DIR = REPO_DIR / "frontend"


def _tracked_wallets() -> list[str]:
    return list((load_yaml_config().get("app") or {}).get("wallets") or [])


async def _job_sync_wallets(app: FastAPI) -> None:
    for wallet in _tracked_wallets():
        try:
            await wallet_sync.sync_wallet(app.state.http, wallet)
        except Exception:
            log.exception("scheduled sync failed for %s", wallet)


async def _job_snapshot(app: FastAPI) -> None:
    for wallet in _tracked_wallets():
        try:
            await portfolio.snapshot_equity(app.state.http, wallet)
        except Exception:
            log.exception("equity snapshot failed for %s", wallet)


async def _job_refresh_coins(app: FastAPI) -> None:
    try:
        await coins_service.refresh_market_data(app.state.http)
        await coins_service.enrich_safety(app.state.http, limit=25)
    except Exception:
        log.exception("coin refresh failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    app.state.http = httpx.AsyncClient(
        timeout=httpx.Timeout(20.0, connect=10.0),
        headers={"User-Agent": "solana-tracker/0.1"},
        follow_redirects=True,
    )

    config = load_yaml_config().get("app") or {}
    scheduler = AsyncIOScheduler(timezone=config.get("timezone") or "Europe/Berlin")

    sync_minutes = int(config.get("sync_interval_minutes") or 10)
    coin_minutes = int(config.get("coin_refresh_minutes") or 15)

    scheduler.add_job(
        _job_sync_wallets, IntervalTrigger(minutes=sync_minutes), args=[app], id="sync"
    )
    scheduler.add_job(
        _job_refresh_coins, IntervalTrigger(minutes=coin_minutes), args=[app], id="coins"
    )
    # Just before local midnight, so the day's closing unrealized value is
    # what lands in the chart.
    scheduler.add_job(
        _job_snapshot, CronTrigger(hour=23, minute=55), args=[app], id="snapshot"
    )
    scheduler.start()
    app.state.scheduler = scheduler
    log.info("started; tracking %d wallet(s)", len(_tracked_wallets()))

    try:
        yield
    finally:
        scheduler.shutdown(wait=False)
        await app.state.http.aclose()


app = FastAPI(title="Solana Tracker", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in get_settings().cors_origins.split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(wallet_router.router)
app.include_router(coins_router.router)


@app.get("/api/health")
async def health() -> dict:
    settings = get_settings()
    return {
        "status": "ok",
        "helius_configured": bool(settings.helius_api_key),
        "rugcheck_key_configured": bool(settings.rugcheck_api_key),
        "wallets": _tracked_wallets(),
    }


if FRONTEND_DIR.exists():
    # The PWA routes on the hash, so every deep link is still "/" as far as
    # the server is concerned and html=True is all the fallback needed.
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
