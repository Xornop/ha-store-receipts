"""Action: session blob and receipt parsing.

No Home Assistant imports, so it can be tested on its own.

The session (stored in the config entry field ``refresh_token``) is a compact
JSON string made by the Store Login add-on:
    {"v": 1, "user_agent": "...", "cookies": {"accessToken": "...", ...},
     "client_version": "1.345"}
client_version is optional (read by the add-on from the website).
"""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

SESSION_VERSION = 1
REQUIRED_COOKIES = ("accessToken", "tokensId")
# Cookies whose change is worth saving (the rest, e.g. __cf_bm, only lives in memory)
IMPORTANT_COOKIES = ("accessToken", "tokensId", "refreshToken", "cf_clearance")


# --- session -------------------------------------------------------------------------


def parse_session(blob: str) -> dict[str, Any]:
    """Parse the session blob; raises ValueError if it is not usable."""
    try:
        data = json.loads(blob)
    except (TypeError, ValueError) as err:
        raise ValueError("Session is not valid JSON") from err
    if not isinstance(data, dict):
        raise ValueError("Session has the wrong shape")
    cookies = data.get("cookies")
    user_agent = data.get("user_agent")
    if not isinstance(cookies, dict) or not isinstance(user_agent, str) or not user_agent:
        raise ValueError("Session misses cookies or user_agent")
    cookies = {str(k): str(v) for k, v in cookies.items() if v is not None}
    missing = [name for name in REQUIRED_COOKIES if not cookies.get(name)]
    if missing:
        raise ValueError(f"Session misses cookies: {', '.join(missing)}")
    version = data.get("client_version")
    return {
        "cookies": cookies,
        "user_agent": user_agent,
        "client_version": version if isinstance(version, str) and version else None,
    }


def dump_session(creds: dict[str, Any]) -> str:
    """Turn the session back into the blob we store."""
    data: dict[str, Any] = {
        "v": SESSION_VERSION,
        "user_agent": creds["user_agent"],
        "cookies": creds["cookies"],
    }
    if creds.get("client_version"):
        data["client_version"] = creds["client_version"]
    return json.dumps(data, separators=(",", ":"))


def cookie_header(cookies: dict[str, str]) -> str:
    """Build the Cookie request header (values with control characters are skipped)."""
    return "; ".join(
        f"{name}={value}"
        for name, value in cookies.items()
        if not re.search(r"[\x00-\x1f\x7f]", f"{name}{value}")
    )


def apply_set_cookies(cookies: dict[str, str], set_cookie_headers: list[str]) -> bool:
    """Apply Set-Cookie response headers to the jar; True if an important cookie changed."""
    changed = False
    for header in set_cookie_headers:
        first, *attrs = [part.strip() for part in header.split(";")]
        name, sep, value = first.partition("=")
        name = name.strip()
        if not sep or not name:
            continue
        expired = value == ""
        for attr in attrs:
            key, _, val = attr.partition("=")
            key = key.strip().lower()
            if key == "max-age":
                try:
                    expired = expired or int(val) <= 0
                except ValueError:
                    pass
            elif key == "expires":
                try:
                    expired = expired or parsedate_to_datetime(val) <= datetime.now(UTC)
                except (TypeError, ValueError):
                    pass
        if expired:
            if cookies.pop(name, None) is not None and name in IMPORTANT_COOKIES:
                changed = True
        elif cookies.get(name) != value:
            cookies[name] = value
            changed = changed or name in IMPORTANT_COOKIES
    return changed


# --- receipts ------------------------------------------------------------------------


def parse_amount(value: Any) -> float | None:
    """Parse 15.95, '15,95' or '€ 1.234,56' into a float."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = re.sub(r"[^\d,.\-]", "", str(value))
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def parse_receipt_datetime(value: str | None) -> datetime:
    """Action's dateTime has no timezone but is UTC (19:42 in the data = 21:42 on the receipt)."""
    if not value:
        raise ValueError("Missing receipt date")
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


def parse_items(detail: dict[str, Any]) -> list[dict[str, Any]]:
    """Item lines; total comes from the printed line total."""
    items: list[dict[str, Any]] = []
    for line in detail.get("products") or []:
        quantity = parse_amount(line.get("quantity")) or 1.0
        prices = line.get("price") or {}
        adjusted = parse_amount(prices.get("adjustedSalesPrice"))
        regular = parse_amount(prices.get("regularSalesPrice"))
        total = parse_amount(line.get("totalPriceFormat"))
        if total is None:
            total = round((adjusted if adjusted is not None else regular or 0) * quantity, 2)
        item: dict[str, Any] = {
            "name": line.get("description") or line.get("code") or "?",
            "quantity": quantity,
            "price": round(total / quantity, 2),
            "total": total,
        }
        if line.get("code"):
            item["code"] = str(line["code"])
        # Only for single items, where the unit/line price is not ambiguous
        if quantity == 1 and regular is not None and regular - total > 0.005:
            item["discount"] = round(regular - total, 2)
        items.append(item)
    return items


def parse_receipt(detail: dict[str, Any], listing: dict[str, Any] | None = None) -> dict[str, Any]:
    """Turn ReceiptDetails (+ the ReceiptList row) into the common format (datetime in UTC)."""
    listing = listing or {}
    items = parse_items(detail)
    prices = detail.get("price") or {}

    total = parse_amount(prices.get("total"))
    if total is None:
        total = parse_amount((listing.get("price") or {}).get("total"))
    if total is None:
        total = round(sum(item["total"] for item in items), 2)

    discount = round(
        (parse_amount(prices.get("otherDiscounts")) or 0)
        + (parse_amount(prices.get("employeeDiscount")) or 0),
        2,
    )
    if not discount:
        discount = round(sum(item.get("discount", 0) for item in items), 2)

    store = (detail.get("store") or {}).get("name") or (listing.get("store") or {}).get("name")
    return {
        "id": str(detail.get("id") or listing.get("id")),
        "datetime": parse_receipt_datetime(detail.get("dateTime") or listing.get("dateTime")),
        "total": total,
        "total_discount": discount or None,
        "store": store if isinstance(store, str) else None,
        "items": items,
    }
