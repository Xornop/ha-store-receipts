"""Shared building blocks for store providers.

Every store lives in its own folder (providers/<store>/) and fetches the
most recent receipt, returned in this common format:

    {
        "id": str,
        "datetime": datetime,
        "total": float,
        "store": str | None,            # optional
        "total_discount": float | None, # optional
        "items": [{"name": str, "quantity": float, "price": float, "total": float}],
    }
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from aiohttp import ClientSession

from homeassistant.util import dt as dt_util

from ..const import TOKEN_KEYS
from ..errors import ReceiptApiError

Tokens = dict[str, Any]
TokenCallback = Callable[[Tokens], None]


def normalize_tokens(payload: dict[str, Any], fallback_refresh: str | None = None) -> Tokens:
    """Turn an OAuth token response into the dict we store."""
    try:
        return {
            "access_token": payload["access_token"],
            "refresh_token": payload.get("refresh_token") or fallback_refresh,
            "expires_at": time.time() + int(payload.get("expires_in") or 3600),
        }
    except (KeyError, TypeError, ValueError) as err:
        raise ReceiptApiError(f"Unexpected token response: {err}") from err


def parse_datetime(value: str | None) -> datetime:
    """Parse an ISO date into a timezone-aware local datetime."""
    parsed = dt_util.parse_datetime(value) if value else None
    if parsed is None:
        raise ReceiptApiError(f"Invalid receipt date: {value}")
    return dt_util.as_local(parsed)


class BaseApi:
    """Token handling shared by all stores. Stores add the actual API calls."""

    def __init__(
        self,
        session: ClientSession,
        tokens: Tokens,
        token_callback: TokenCallback | None = None,
    ) -> None:
        self._session = session
        self._tokens = {key: tokens.get(key) for key in TOKEN_KEYS}
        self._token_callback = token_callback
        self._lock = asyncio.Lock()

    @property
    def tokens(self) -> Tokens:
        """Return the current (possibly rotated) tokens."""
        return dict(self._tokens)

    async def _do_refresh(self, refresh_token: str) -> Tokens:
        """Store-specific token refresh."""
        raise NotImplementedError

    async def _ensure_token(self, force: bool = False) -> None:
        async with self._lock:
            if not force and float(self._tokens.get("expires_at") or 0) - time.time() > 60:
                return
            self._tokens = await self._do_refresh(self._tokens["refresh_token"])
            # Persist at once: stores may invalidate the old refresh token
            if self._token_callback:
                self._token_callback(self._tokens)

    async def async_get_latest_receipt(self) -> dict[str, Any] | None:
        """Return the most recent receipt in the common format, or None."""
        raise NotImplementedError


@dataclass(frozen=True)
class Provider:
    """Everything the integration needs to know about one store."""

    key: str
    name: str
    api_class: type[BaseApi]
    refresh_tokens: Callable[[ClientSession, str], Awaitable[Tokens]]
    # Optional: log in with a one-time code instead of a refresh token
    exchange_code: Callable[[ClientSession, str], Awaitable[Tokens]] | None = None
    extract_code: Callable[[str], str] | None = None
