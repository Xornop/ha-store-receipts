"""Lidl: turn a ticket into our common item format."""
from __future__ import annotations

from html.parser import HTMLParser
from typing import Any


def parse_amount(value: Any) -> float | None:
    """Parse '2,19', 2.19 or None into a float."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


class _ArticleLines(HTMLParser):
    """Collects the item lines of Lidl's printed receipt (htmlPrintedReceipt).

    Item lines are tags with data-art-* attributes (data-art-id, data-art-description,
    data-unit-price, data-art-quantity). Not verified against a real Dutch receipt yet.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lines: list[dict[str, str | None]] = []
        self._seen: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if "data-art-description" not in values:
            return
        line_id = values.get("id")
        if line_id:
            if line_id in self._seen:
                return
            self._seen.add(line_id)
        self.lines.append(values)


def parse_html_items(html: str) -> list[dict[str, Any]]:
    """Item lines from the printed receipt HTML; empty if the format is not recognised."""
    parser = _ArticleLines()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001 - broken HTML must never break the sensor
        return []
    items: list[dict[str, Any]] = []
    for line in parser.lines:
        quantity = parse_amount(line.get("data-art-quantity")) or 1.0
        unit = parse_amount(line.get("data-unit-price"))
        if unit is None:
            continue
        item: dict[str, Any] = {
            "name": (line.get("data-art-description") or "?").strip(),
            "quantity": quantity,
            "price": unit,
            "total": round(unit * quantity, 2),
        }
        if line.get("data-art-id"):
            item["code"] = str(line["data-art-id"]).strip()
        items.append(item)
    return items


def parse_ticket_items(ticket: dict[str, Any]) -> list[dict[str, Any]]:
    """Item lines of a ticket: JSON lines (v2) or the printed receipt HTML (v3)."""
    json_lines = ticket.get("itemsLine") or ticket.get("items")
    if not json_lines:
        html = ticket.get("htmlPrintedReceipt")
        return parse_html_items(html) if isinstance(html, str) else []
    items: list[dict[str, Any]] = []
    for line in json_lines:
        quantity = parse_amount(line.get("quantity")) or 1.0
        unit = parse_amount(line.get("currentUnitPrice"))
        gross = parse_amount(line.get("originalAmount"))
        if gross is None:
            gross = round((unit or 0) * quantity, 2)
        discount = round(
            sum(parse_amount(d.get("amount")) or 0 for d in line.get("discounts") or []), 2
        )
        item = {
            "name": line.get("name") or line.get("description") or "?",
            "quantity": quantity,
            "price": unit if unit is not None else gross,
            "total": round(gross - discount, 2),
        }
        if discount:
            item["discount"] = discount
        items.append(item)
    return items
