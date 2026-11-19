"""Albert Heijn: API client."""
from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlparse

from aiohttp import ClientError, ClientSession, ClientTimeout

from ...errors import ReceiptApiError, ReceiptAuthError
from ..base import BaseApi, Tokens, normalize_tokens, parse_datetime

BASE_URL = "https://api.ah.nl"
GRAPHQL_URL = f"{BASE_URL}/graphql"
CLIENT_ID = "appie-ios"
HEADERS = {
    "User-Agent": "Appie/9.28 (iPhone17,3; iPhone; CPU OS 26_1 like Mac OS X)",
    "x-client-name": CLIENT_ID,
    "x-client-version": "9.28",
    "x-application": "AHWEBSHOP",
    "Accept": "application/json",
}
TIMEOUT = ClientTimeout(total=30)

LIST_QUERY = """
query FetchPosReceipts($offset: Int!, $limit: Int!) {
  posReceiptsPage(pagination: {offset: $offset, limit: $limit}) {
    posReceipts {
      id
      dateTime
      totalAmount { amount }
    }
  }
}
"""

DETAIL_QUERY = """
query FetchReceipt($id: String!) {
  posReceiptDetails(id: $id) {
    id
    products {
      id
      quantity
      name
      price { amount }
      amount { amount }
    }
  }
}
"""


def extract_code(value: str) -> str:
    """Return the auth code from a raw code or an appie://login-exit?code=... URL."""
    stripped = value.strip()
    if stripped and "://" not in stripped and "?" not in stripped and "=" not in stripped:
        return stripped
    code = parse_qs(urlparse(stripped).query).get("code", [None])[0]
    if not code:
        raise ValueError("No authorization code found")
    return code


async def _post_token(session: ClientSession, path: str, payload: dict[str, str]) -> dict[str, Any]:
    try:
        async with session.post(
            f"{BASE_URL}{path}", json=payload, headers=HEADERS, timeout=TIMEOUT
        ) as resp:
            if resp.status in (400, 401, 403):
                raise ReceiptAuthError(await resp.text())
            resp.raise_for_status()
            return await resp.json()
    except (ClientError, TimeoutError) as err:
        raise ReceiptApiError(f"Token request failed: {err}") from err


async def async_exchange_code(session: ClientSession, code: str) -> Tokens:
    """Exchange a one-time login code for tokens."""
    payload = await _post_token(
        session, "/mobile-auth/v1/auth/token", {"clientId": CLIENT_ID, "code": code}
    )
    return normalize_tokens(payload)


async def async_refresh_tokens(session: ClientSession, refresh_token: str) -> Tokens:
    """Get a fresh token set using a refresh token."""
    payload = await _post_token(
        session,
        "/mobile-auth/v1/auth/token/refresh",
        {"clientId": CLIENT_ID, "refreshToken": refresh_token},
    )
    return normalize_tokens(payload, fallback_refresh=refresh_token)


class AHApi(BaseApi):
    """Fetches the most recent in-store receipt."""

    async def _do_refresh(self, refresh_token: str) -> Tokens:
        return await async_refresh_tokens(self._session, refresh_token)

    async def _graphql(
        self, query: str, variables: dict[str, Any], retry: bool = True
    ) -> dict[str, Any]:
        await self._ensure_token()
        headers = {**HEADERS, "Authorization": f"Bearer {self._tokens['access_token']}"}
        payload: dict[str, Any] = {}
        status = 0
        try:
            async with self._session.post(
                GRAPHQL_URL,
                json={"query": query, "variables": variables},
                headers=headers,
                timeout=TIMEOUT,
            ) as resp:
                status = resp.status
                if status not in (401, 403):
                    resp.raise_for_status()
                    payload = await resp.json()
        except (ClientError, TimeoutError) as err:
            raise ReceiptApiError(f"GraphQL request failed: {err}") from err

        if status in (401, 403):
            if retry:
                await self._ensure_token(force=True)
                return await self._graphql(query, variables, retry=False)
            raise ReceiptAuthError("Access denied")
        if payload.get("errors"):
            raise ReceiptApiError(f"GraphQL errors: {payload['errors']}")
        return payload["data"]

    async def async_get_latest_receipt(self) -> dict[str, Any] | None:
        data = await self._graphql(LIST_QUERY, {"offset": 0, "limit": 10})
        receipts = (data.get("posReceiptsPage") or {}).get("posReceipts") or []
        if not receipts:
            return None

        latest = max(receipts, key=lambda r: parse_datetime(r["dateTime"]))
        detail = (
            await self._graphql(DETAIL_QUERY, {"id": str(latest["id"])})
        ).get("posReceiptDetails") or {}

        items = [
            {
                "name": line.get("name"),
                "quantity": float(line.get("quantity") or 1),
                "price": float((line.get("price") or {}).get("amount") or 0),
                "total": float((line.get("amount") or {}).get("amount") or 0),
            }
            for line in detail.get("products") or []
        ]
        return {
            "id": str(latest["id"]),
            "datetime": parse_datetime(latest["dateTime"]),
            "total": float(latest["totalAmount"]["amount"]),
            "items": items,
        }
