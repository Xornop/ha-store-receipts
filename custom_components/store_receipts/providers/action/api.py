"""Action (Mijn Action): API client.

Action has no OAuth. The web app logs in with e-mail + password (POST /api/auth/login)
and then uses cookies (accessToken, tokensId, refreshToken) behind Cloudflare
(cf_clearance) and a GraphQL API with persisted queries. All of it comes from HAR
captures of www.action.com; the Store Login add-on delivers the cookies and the
User-Agent of its browser as one JSON blob in ``refresh_token``, plus the client
version it saw. Queries are "automatic persisted queries": see queries.py.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from aiohttp import ClientError, ClientSession, ClientTimeout

from homeassistant.util import dt as dt_util

from ...errors import ReceiptApiError, ReceiptAuthError
from ..base import BaseApi, Tokens
from .queries import HASHES, QUERIES
from .parser import (
    apply_set_cookies,
    cookie_header,
    dump_session,
    important_cookies,
    parse_receipt,
    parse_receipt_datetime,
    parse_session,
)

_LOGGER = logging.getLogger(__name__)

ORIGIN = "https://www.action.com"
GRAPHQL_URL = f"{ORIGIN}/api/graphql"
REFRESH_URL = f"{ORIGIN}/api/auth/refresh"
REFERER = f"{ORIGIN}/nl-nl/mijn-action/mijn-kassabonnetjes/"
CLIENT_VERSION = "1.345"  # <meta name="release-id"> of www.action.com
FEATURE_FLAGS = (
    '{"enhancedProductSeo":true,"productReviews":true,"productReviewsLister":true,'
    '"productReviewsTranslations":true,"promotionWithLoyaltyCard":true,"webshopCmsThemes":true}'
)
LIST_LIMIT = 14
TIMEOUT = ClientTimeout(total=20, connect=10)

CLOUDFLARE_MSG = (
    "Cloudflare blocks the request: cf_clearance expired, or the IP address or "
    "User-Agent differs from the Store Login browser. Log in again via Store Login"
)


def _check_graphql_errors(payload: dict[str, Any]) -> None:
    errors = payload.get("errors")
    if not errors:
        return
    text = json.dumps(errors, ensure_ascii=False)[:300]
    low = text.lower()
    codes = {
        str(((err or {}).get("extensions") or {}).get("code", "")).upper()
        for err in errors
        if isinstance(err, dict)
    }
    if codes & {"USER_LOGGED_OUT", "UNAUTHENTICATED", "UNAUTHORIZED", "FORBIDDEN"} or (
        "invalid access or refresh token" in low
    ):
        raise ReceiptAuthError(f"Action session ended (logged out): {text}")
    if "persistedquerynotfound" in low or "persisted_query_not_found" in low:
        raise ReceiptApiError(f"Action does not accept the query (also not as full text): {text}")
    if any(word in low for word in ("unauthenticated", "unauthorized", "not authenticated", "forbidden")):
        raise ReceiptAuthError(f"Action rejected the session: {text}")
    raise ReceiptApiError(f"GraphQL error: {text}")


def _persisted_query_missing(payload: dict[str, Any]) -> bool:
    return "persistedquerynotfound" in json.dumps(payload.get("errors") or []).lower()


async def _send(
    session: ClientSession,
    creds: dict[str, Any],
    operation: str,
    variables: dict[str, Any],
    full_query: bool,
) -> tuple[dict[str, Any], bool]:
    """One request: GET with only the hash, or (full_query) POST with the whole query."""
    extensions = {
        "persistedQuery": {"sha256Hash": HASHES[operation], "version": 1},
        "headers": {"Accept-Language": "nl-NL"},
    }
    headers = {
        "User-Agent": creds["user_agent"],
        "Accept": "*/*",
        "Accept-Language": "nl-NL,nl;q=0.9",
        "Referer": REFERER,
        "apollographql-client-name": "web",
        "x-apollo-operation-name": operation,
        "Cache-Control": "no-cache",
        "x-client-version": creds.get("client_version") or CLIENT_VERSION,
        "x-feature-flags": FEATURE_FLAGS,
        "Cookie": cookie_header(creds["cookies"]),
    }
    try:
        if full_query:
            # Same body as the web app sends when the server does not know the hash
            headers["Content-Type"] = "application/json"
            body = json.dumps(
                {
                    "query": QUERIES[operation],
                    "operationName": operation,
                    "variables": variables,
                    "extensions": {
                        "headers": extensions["headers"],
                        "persistedQuery": extensions["persistedQuery"],
                    },
                },
                separators=(",", ":"),
            )
            request = session.post(
                GRAPHQL_URL, data=body, headers=headers, timeout=TIMEOUT, allow_redirects=False
            )
        else:
            params = {
                "operationName": operation,
                "variables": json.dumps(variables, separators=(",", ":")),
                "extensions": json.dumps(
                    {"persistedQuery": extensions["persistedQuery"], "headers": extensions["headers"]},
                    separators=(",", ":"),
                ),
            }
            request = session.get(
                GRAPHQL_URL, params=params, headers=headers, timeout=TIMEOUT, allow_redirects=False
            )
        async with request as resp:
            status = resp.status
            text = await resp.text()
            set_cookies = resp.headers.getall("Set-Cookie", [])
            mitigated = resp.headers.get("cf-mitigated", "")
    except TimeoutError as err:
        raise ReceiptApiError("Request timed out (www.action.com)") from err
    except ClientError as err:
        raise ReceiptApiError(f"Request failed: {type(err).__name__}: {err}") from err

    changed = apply_set_cookies(creds["cookies"], set_cookies)

    if mitigated == "challenge" or (
        status in (403, 429, 503) and ("Just a moment" in text or "/cdn-cgi/challenge-platform" in text)
    ):
        raise ReceiptAuthError(CLOUDFLARE_MSG)
    if 300 <= status < 400 or status in (401, 403):
        raise ReceiptAuthError(f"Action rejected the session (HTTP {status})")
    if status >= 400:
        raise ReceiptApiError(f"HTTP {status}: {text[:200]}")
    try:
        payload = json.loads(text)
    except ValueError as err:
        raise ReceiptApiError("Invalid JSON from Action") from err
    if not isinstance(payload, dict):
        raise ReceiptApiError("Unexpected response from Action")
    return payload, changed


def _logged_out(payload: dict[str, Any]) -> bool:
    """True if GraphQL says the access token is no longer valid (the website then refreshes)."""
    return "user_logged_out" in json.dumps(payload.get("errors") or []).lower()


async def _refresh_session(session: ClientSession, creds: dict[str, Any]) -> None:
    """Renew the login like the website: POST /api/auth/refresh with the login cookies.

    Seen in a HAR capture: empty body, header x-token-behaviour: default, cookies
    refreshToken + tokensId (+ accessToken); the answer sets new cookies. On failure the
    server only answers 401 and clears the cookies; those are NOT applied here, so the old
    values stay in creds.
    """
    headers = {
        "User-Agent": creds["user_agent"],
        "Accept": "*/*",
        "Accept-Language": "nl-NL,nl;q=0.9",
        "Origin": ORIGIN,
        "Referer": REFERER,
        "x-token-behaviour": "default",
        "x-feature-flags": FEATURE_FLAGS,
        "Cookie": cookie_header(creds["cookies"]),
    }
    try:
        async with session.post(
            REFRESH_URL, headers=headers, timeout=TIMEOUT, allow_redirects=False
        ) as resp:
            status = resp.status
            text = await resp.text()
            set_cookies = resp.headers.getall("Set-Cookie", [])
    except TimeoutError as err:
        raise ReceiptApiError("Token refresh timed out (www.action.com)") from err
    except ClientError as err:
        raise ReceiptApiError(f"Token refresh failed: {type(err).__name__}: {err}") from err

    if status in (401, 403) or 300 <= status < 400:
        if status == 403 and ("Just a moment" in text or "/cdn-cgi/challenge-platform" in text):
            raise ReceiptAuthError(CLOUDFLARE_MSG)
        raise ReceiptAuthError(f"Action session ended, refresh refused (HTTP {status}): {text[:150]}")
    if status >= 400:
        raise ReceiptApiError(f"Token refresh HTTP {status}: {text[:150]}")
    apply_set_cookies(creds["cookies"], set_cookies)
    _LOGGER.debug("Action session refreshed")


async def _graphql(
    session: ClientSession,
    creds: dict[str, Any],
    operation: str,
    variables: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    """Run a query. Returns (json, important cookies changed); updates creds in place."""
    refreshed = False
    while True:
        payload, changed = await _send(session, creds, operation, variables, full_query=False)
        if _persisted_query_missing(payload):
            # The server does not know the hash (yet): send the full query once, like the website
            payload, changed_again = await _send(
                session, creds, operation, variables, full_query=True
            )
            changed = changed or changed_again
        if _logged_out(payload) and not refreshed:
            # Access token expired: renew it once with the refresh token, then retry
            refreshed = True
            await _refresh_session(session, creds)
            changed = True
            continue
        _check_graphql_errors(payload)
        return payload, changed


def _receipt_list(payload: dict[str, Any]) -> list[dict[str, Any]]:
    block = (payload.get("data") or {}).get("receiptList")
    if block is None:
        # A logged-in user always gets an object here, even without receipts
        raise ReceiptAuthError("Action returned no receipt list: the session has probably expired")
    return block.get("receipts") or []


def _tokens(creds: dict[str, Any]) -> Tokens:
    """Cookie session in our token model: the whole session sits in refresh_token."""
    return {
        "access_token": creds["cookies"].get("accessToken"),
        "refresh_token": dump_session(creds),
        "expires_at": 0,  # the session has no known expiry; Action tells us by rejecting it
    }


async def async_refresh_tokens(session: ClientSession, refresh_token: str) -> Tokens:
    """Login check: parse the blob and test it with a real request. There is no refresh."""
    creds = parse_session(refresh_token)  # ValueError -> invalid_auth in the config flow
    payload, _ = await _graphql(session, creds, "ReceiptList", {"limit": LIST_LIMIT})
    _receipt_list(payload)
    return _tokens(creds)


class ActionApi(BaseApi):
    """Fetches the most recent Action receipt."""

    _creds: dict[str, Any] | None = None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # One fetch at a time: parallel use of a rotating login could invalidate it
        self._fetch_lock = asyncio.Lock()

    async def _ensure_token(self, force: bool = False) -> None:
        """Cookie session: nothing to refresh, just load the session once."""
        async with self._lock:
            if self._creds is None:
                try:
                    self._creds = parse_session(self._tokens.get("refresh_token") or "")
                except ValueError as err:
                    raise ReceiptAuthError(f"Invalid Action session: {err}") from err

    async def _do_refresh(self, refresh_token: str) -> Tokens:
        raise ReceiptAuthError("Action has no token refresh; log in again via Store Login")

    async def _query(self, operation: str, variables: dict[str, Any]) -> dict[str, Any]:
        await self._ensure_token()
        assert self._creds is not None
        before = important_cookies(self._creds["cookies"])
        try:
            payload, _ = await _graphql(self._session, self._creds, operation, variables)
        finally:
            # Save rotated login cookies at once, also when the request then fails:
            # the old ones may already be invalid and a retry would start from them
            after = important_cookies(self._creds["cookies"])
            if after != before:
                _LOGGER.debug(
                    "Action rotated cookies: %s",
                    ", ".join(sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))),
                )
                self._tokens = _tokens(self._creds)
                if self._token_callback:
                    self._token_callback(self._tokens)
        return payload

    async def async_get_latest_receipt(self) -> dict[str, Any] | None:
        async with self._fetch_lock:
            return await self._fetch_latest_receipt()

    async def _fetch_latest_receipt(self) -> dict[str, Any] | None:
        payload = await self._query("ReceiptList", {"limit": LIST_LIMIT})
        receipts = _receipt_list(payload)
        if not receipts:
            return None
        try:
            latest = max(receipts, key=lambda r: parse_receipt_datetime(r.get("dateTime")))
        except ValueError as err:
            raise ReceiptApiError(f"Invalid receipt date: {err}") from err

        detail_payload = await self._query("ReceiptDetails", {"receiptId": latest["id"]})
        detail = (detail_payload.get("data") or {}).get("receipt")
        if not detail:
            raise ReceiptApiError("Action returned no receipt details")

        receipt = parse_receipt(detail, latest)
        receipt["datetime"] = dt_util.as_local(receipt["datetime"])
        return receipt
