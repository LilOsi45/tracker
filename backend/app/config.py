"""Runtime configuration.

Secrets come from the environment (.env on the Hetzner box), tunable
behaviour comes from config.yaml so it can be changed without a redeploy.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BASE_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(REPO_DIR / ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    # --- secrets -------------------------------------------------------
    helius_api_key: str = ""
    rugcheck_api_key: str = ""
    discord_webhook_url: str = ""

    # --- app -----------------------------------------------------------
    database_path: str = str(REPO_DIR / "data" / "tracker.db")
    config_path: str = str(REPO_DIR / "config.yaml")
    # Single-user tool: one shared token guards the whole API.
    access_token: str = ""
    cors_origins: str = "*"

    @property
    def helius_base(self) -> str:
        return "https://api.helius.xyz/v0"

    @property
    def helius_rpc(self) -> str:
        return f"https://mainnet.helius-rpc.com/?api-key={self.helius_api_key}"


@lru_cache
def get_settings() -> Settings:
    return Settings()


def load_yaml_config() -> dict[str, Any]:
    """Read config.yaml fresh on every call so edits take effect without a restart."""
    path = Path(get_settings().config_path)
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def ensure_data_dir() -> None:
    Path(get_settings().database_path).parent.mkdir(parents=True, exist_ok=True)


# Mints that are money, not a position.
WSOL_MINT = "So11111111111111111111111111111111111111112"
STABLE_MINTS = {
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
}
QUOTE_MINTS = {WSOL_MINT} | STABLE_MINTS
