"""Jumbo: API client (Auth0 login + GraphQL)."""
from __future__ import annotations

import json
import re
from typing import Any

from aiohttp import ClientError, ClientSession, ClientTimeout

from ...errors import ReceiptApiError, ReceiptAuthError
from ..base import BaseApi, Tokens, normalize_tokens, parse_datetime
from .parser import parse_receipt_document, receipt_id_candidates

TOKEN_URL = "https://auth.jumbo.com/oauth/token"
CLIENT_ID = "JxF3wi1b7MOTd9qXutaxqd9uHt4KmG3U"
AUDIENCE = "https://jumbo.com/web"
SCOPE = "openid profile email offline_access"
GRAPHQL_URL = "https://www.jumbo.com/api/graphql"
CLIENT_NAME = "JUMBO_WEB-orders"
TIMEOUT = ClientTimeout(total=30)

AUTH_HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)",
}
API_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    ),
    "Content-Type": "application/json",
    "Accept": "application/json",
    "apollographql-client-name": CLIENT_NAME,
    "apollographql-client-version": "master-v39.2.0-web",
    "x-source": CLIENT_NAME,
}

OVERVIEW_QUERY = """
query GetReceiptOverview {
  receiptOverview(page: 0, pageSize: 10) {
    receipts {
      transactionId
      purchaseEndOn
      store { storeId name }
    }
  }
}
"""

DETAIL_QUERY = """
query GetDigitalReceipt($transactionId: String) {
  receipt(transactionId: $transactionId) {
    receiptImage { image type }
    store { storeId name }
    purchaseEndOn
  }
}
"""


async def async_refresh_tokens(session: ClientSession, refresh_token: str) -> Tokens:
    """Refresh tokens. Jumbo rotates refresh tokens: the old one is dead afterwards."""
    payload = {
        "grant_type": "refresh_token",
        "client_id": CLIENT_ID,
        "refresh_token": refresh_token,
        "audience": AUDIENCE,
        "scope": SCOPE,
    }
    try:
        async with session.post(
            TOKEN_URL, json=payload, headers=AUTH_HEADERS, timeout=TIMEOUT
        ) as resp:
            body = await resp.text()
            if resp.status in (400, 401, 403) and "invalid_grant" in body:
                raise ReceiptAuthError(body)
            if resp.status >= 400:
                raise ReceiptApiError(f"HTTP {resp.status}: {body[:200]}")
            data = json.loads(body)
    except (ClientError, TimeoutError, ValueError) as err:
        raise ReceiptApiError(f"Token request failed: {err}") from err
    return normalize_tokens(data, refresh_token)


class JumboApi(BaseApi):
    """Fetches the most recent Jumbo receipt."""

    async def _do_refresh(self, refresh_token: str) -> Tokens:
        return await async_refresh_tokens(self._session, refresh_token)

    async def _graphql(
        self, query: str, variables: dict[str, Any] | None = None, retry: bool = True
    ) -> dict[str, Any]:
        await self._ensure_token()
        headers = {**API_HEADERS, "Authorization": f"Bearer {self._tokens['access_token']}"}
        body: dict[str, Any] = {"query": query}
        if variables:
            body["variables"] = variables
        try:
            async with self._session.post(
                GRAPHQL_URL, json=body, headers=headers, timeout=TIMEOUT
            ) as resp:
                status = resp.status
                text = await resp.text()
        except (ClientError, TimeoutError) as err:
            raise ReceiptApiError(f"GraphQL request failed: {err}") from err

        auth_failed = status in (401, 403)
        payload: dict[str, Any] = {}
        if not auth_failed:
            if status >= 400:
                raise ReceiptApiError(f"HTTP {status}: {text[:200]}")
            try:
                payload = json.loads(text)
            except ValueError as err:
                raise ReceiptApiError("Invalid JSON from Jumbo") from err
            errors = payload.get("errors") or []
            if errors:
                message = "; ".join(str(e.get("message", "")) for e in errors)
                if re.search(r"401 UNAUTHORIZED|Customer ID|unauthorized", message, re.I):
                    auth_failed = True
                else:
                    raise ReceiptApiError(f"GraphQL error: {message}")

        if auth_failed:
            if retry:
                await self._ensure_token(force=True)
                return await self._graphql(query, variables, retry=False)
            raise ReceiptAuthError("Jumbo rejected the session")
        return payload.get("data") or {}

    async def async_get_latest_receipt(self) -> dict[str, Any] | None:
        data = await self._graphql(OVERVIEW_QUERY)
        receipts = (data.get("receiptOverview") or {}).get("receipts") or []
        if not receipts:
            return None
        latest = max(receipts, key=lambda r: parse_datetime(r["purchaseEndOn"]))

        detail: dict[str, Any] | None = None
        for candidate in receipt_id_candidates(str(latest["transactionId"])):
            result = await self._graphql(DETAIL_QUERY, {"transactionId": candidate})
            if result.get("receipt"):
                detail = result["receipt"]
                break
        if detail is None:
            raise ReceiptApiError(f"Receipt {latest['transactionId']} not found")

        parsed = parse_receipt_document((detail.get("receiptImage") or {}).get("image") or "{}")
        items = parsed["items"]
        total = parsed["total"]
        if total is None:
            total = round(sum(item["total"] for item in items), 2)
        store = (detail.get("store") or latest.get("store") or {}).get("name")
        return {
            "id": str(latest["transactionId"]),
            "datetime": parse_datetime(detail.get("purchaseEndOn") or latest["purchaseEndOn"]),
            "total": total,
            "total_discount": parsed["total_discount"],
            "store": store,
            "items": items,
        }
