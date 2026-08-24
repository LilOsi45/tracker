"""Shared dependencies: the HTTP client, auth, and address validation."""

from __future__ import annotations

import re

import httpx
from fastapi import Header, HTTPException, Request

from .config import get_settings

BASE58 = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


def get_http(request: Request) -> httpx.AsyncClient:
    return request.app.state.http


def valid_wallet(address: str) -> str:
    if not BASE58.match(address):
        raise HTTPException(status_code=400, detail="Not a valid Solana address")
    return address


async def require_token(authorization: str | None = Header(default=None)) -> None:
    """Single-user auth. With no token configured the API is open (local use)."""
    expected = get_settings().access_token
    if not expected:
        return
    if authorization != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="Unauthorized")
