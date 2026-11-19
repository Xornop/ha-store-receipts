"""Lidl Plus: API client."""
from __future__ import annotations

import base64
import json
import logging
import socket
from typing import Any

from aiohttp import ClientError, ClientSession, ClientTimeout, TCPConnector

from ...errors import ReceiptApiError, ReceiptAuthError
from ..base import BaseApi, Tokens, normalize_tokens, parse_datetime
from .parser import parse_amount, parse_ticket_items

_LOGGER = logging.getLogger(__name__)

TOKEN_URL = "https://accounts.lidl.com/connect/token"
CLIENT_ID = "LidlPlusNativeClient"
COUNTRY = "NL"
LANGUAGE = "nl"
TICKET_API = f"https://tickets.lidlplus.com/api/v2/{COUNTRY}/tickets"
# The v2 detail call rejects receipt ids (HTTP 400); the current app uses v3 for details
TICKET_DETAIL_API = f"https://tickets.lidlplus.com/api/v3/{COUNTRY}/tickets"
TIMEOUT = ClientTimeout(total=20, connect=10)

_BASIC = base64.b64encode(f"{CLIENT_ID}:secret".encode()).decode()
# Headers based on the reference Lidl Plus client (python-requests)
TOKEN_HEADERS = {
    "Authorization": f"Basic {_BASIC}",
    "User-Agent": "python-requests/2.32.3",
    "Accept": "*/*",
}
# Lidl's edge silently stalls requests with the old fake version "999.99.9" (no answer at
# all, so a timeout). A realistic app version fixes that; if timeouts return, raise it.
APP_VERSION = "16.7.0"
API_HEADERS = {
    "User-Agent": "python-requests/2.32.3",
    "App-Version": APP_VERSION,
    "Operating-System": "iOs",
    "App": "com.lidl.eci.lidl.plus",
    "Accept-Language": LANGUAGE,
    "Accept": "application/json",
}


async def async_refresh_tokens(session: ClientSession, refresh_token: str) -> Tokens:
    """Refresh tokens. Lidl may rotate the refresh token: always store the new one."""
    try:
        async with session.post(
            TOKEN_URL,
            data={"grant_type": "refresh_token", "refresh_token": refresh_token},
            headers=TOKEN_HEADERS,
            timeout=TIMEOUT,
        ) as resp:
            body = await resp.text()
            if resp.status in (400, 401, 403) and (
                "invalid_grant" in body or resp.status == 401
            ):
                raise ReceiptAuthError(f"HTTP {resp.status}: {body[:200]}")
            if resp.status >= 400:
                raise ReceiptApiError(f"HTTP {resp.status}: {body[:200]}")
            data = json.loads(body)
    except TimeoutError as err:
        raise ReceiptApiError("Token request timed out (accounts.lidl.com)") from err
    except (ClientError, ValueError) as err:
        raise ReceiptApiError(f"Token request failed: {type(err).__name__}: {err}") from err
    return normalize_tokens(data, refresh_token)


class LidlApi(BaseApi):
    """Fetches the most recent Lidl Plus receipt."""

    _plain_ipv4 = False  # switched on if Home Assistant's shared session gets no answer

    async def _request(
        self, url: str, params: dict[str, Any] | None, headers: dict[str, str]
    ) -> tuple[int, str]:
        """GET via Home Assistant's session; fall back to a plain IPv4 session."""
        if not self._plain_ipv4:
            try:
                async with self._session.get(
                    url, params=params, headers=headers, timeout=TIMEOUT
                ) as resp:
                    return resp.status, await resp.text()
            except (ClientError, TimeoutError) as err:
                _LOGGER.warning(
                    "Lidl request failed (%s), retrying over plain IPv4", type(err).__name__
                )
        try:
            async with ClientSession(connector=TCPConnector(family=socket.AF_INET)) as session:
                async with session.get(
                    url, params=params, headers=headers, timeout=TIMEOUT
                ) as resp:
                    result = resp.status, await resp.text()
        except TimeoutError as err:
            raise ReceiptApiError("Request timed out (tickets.lidlplus.com)") from err
        except ClientError as err:
            raise ReceiptApiError(f"Request failed: {type(err).__name__}: {err}") from err
        if not self._plain_ipv4:
            _LOGGER.warning("Lidl works over plain IPv4; using that from now on")
        self._plain_ipv4 = True
        return result

    async def _do_refresh(self, refresh_token: str) -> Tokens:
        return await async_refresh_tokens(self._session, refresh_token)

    async def _get(
        self, url: str, params: dict[str, Any] | None = None, retry: bool = True
    ) -> dict[str, Any]:
        await self._ensure_token()
        headers = {**API_HEADERS, "Authorization": f"Bearer {self._tokens['access_token']}"}
        status, text = await self._request(url, params, headers)

        if status in (401, 403):
            if retry:
                await self._ensure_token(force=True)
                return await self._get(url, params, retry=False)
            raise ReceiptAuthError("Lidl rejected the session")
        if status >= 400:
            raise ReceiptApiError(f"HTTP {status}: {text[:200]}")
        try:
            return json.loads(text)
        except ValueError as err:
            raise ReceiptApiError("Invalid JSON from Lidl") from err

    async def _get_detail(self, ticket_id: str) -> dict[str, Any]:
        """Receipt detail: v3 first, v2 as fallback (the list call must keep working)."""
        try:
            return await self._get(f"{TICKET_DETAIL_API}/{ticket_id}")
        except ReceiptAuthError:
            raise
        except ReceiptApiError as err:
            _LOGGER.warning("Lidl v3 receipt detail failed (%s), trying v2", err)
        return await self._get(f"{TICKET_API}/{ticket_id}")

    async def async_get_latest_receipt(self) -> dict[str, Any] | None:
        listing = await self._get(TICKET_API, {"pageNumber": 1, "onlyFavorite": "False"})
        tickets = listing.get("tickets") or []
        if not tickets:
            return None
        latest = max(tickets, key=lambda t: parse_datetime(t.get("date")))
        detail = await self._get_detail(latest["id"])

        items = parse_ticket_items(detail)
        if not items:
            _LOGGER.warning(
                "Lidl receipt %s: no item lines recognised (fields: %s, html: %s chars)",
                latest["id"],
                ", ".join(sorted(detail)),
                len(detail.get("htmlPrintedReceipt") or ""),
            )
        total = parse_amount(detail.get("totalAmount"))
        if total is None:
            total = parse_amount(latest.get("totalAmount"))
        if total is None:
            total = round(sum(item["total"] for item in items), 2)

        store = detail.get("store") or latest.get("store")
        if isinstance(store, dict):
            store = store.get("name")
        store = store or detail.get("storeName") or latest.get("storeName")

        return {
            "id": str(latest["id"]),
            "datetime": parse_datetime(detail.get("date") or latest.get("date")),
            "total": total,
            "total_discount": round(sum(item.get("discount", 0) for item in items), 2) or None,
            "store": store if isinstance(store, str) else None,
            "items": items,
        }
