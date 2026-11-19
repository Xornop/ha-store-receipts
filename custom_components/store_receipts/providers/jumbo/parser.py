"""Jumbo: turn the print-layout receipt into items and totals."""
from __future__ import annotations

import json
import re
from typing import Any

_KEY_RE = re.compile(
    r"^(.+)-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$", re.I
)
_MULT_RE = re.compile(
    r"^\s*(\d+(?:,\d+)?)\s*(?:kg)?\s*[xX]\s+(\d+,\d{2})(?:\s+P)?\s+(-?\d+,\d{2})$", re.I
)
_SINGLE_RE = re.compile(r"^(.*?)(?:\s+P)?\s+(-?\d+,\d{2})$")
_TOTAL_RE = re.compile(r"Totaal\s+([\d,]+)")
_DISCOUNT_RE = re.compile(r"Totaal korting:\s*-?(\d+,\d{2})", re.I)


def _num(value: str) -> float:
    return float(value.replace(",", "."))


def receipt_id_candidates(raw: str) -> list[str]:
    """Forms of a receipt key that the detail query may accept."""
    raw = (raw or "").strip()
    stem = re.sub(r"\.json$", "", raw, flags=re.I)
    candidates = [raw, stem]
    match = _KEY_RE.match(stem)
    if match:
        candidates += [match.group(2), match.group(1)]
    seen: list[str] = []
    for item in candidates:
        if item and item not in seen:
            seen.append(item)
    return seen


def parse_receipt_document(raw: str) -> dict[str, Any]:
    """Return items, total and total discount from Jumbo's receipt document."""
    try:
        doc = json.loads(raw)["documents"][0]["documents"][0]
    except (ValueError, KeyError, IndexError, TypeError):
        return {"items": [], "total": None, "total_discount": None}

    lines: list[str] = []
    for section in doc.get("printSections") or []:
        for obj in section.get("textObjects") or []:
            for text_line in obj.get("textLines") or []:
                text = "".join((seg.get("text") or "") for seg in text_line.get("texts") or [])
                if text.strip():
                    lines.append(text)

    items: list[dict[str, Any]] = []
    total: float | None = None
    in_items = False
    pending: str | None = None

    for line in lines:
        if "OMSCHRIJVING" in line:
            in_items = True
            continue
        if not in_items:
            continue
        if line.startswith("Totaal"):
            match = _TOTAL_RE.match(line)
            if match:
                try:
                    total = _num(match.group(1))
                except ValueError:
                    total = None
            in_items = False
            pending = None
            continue
        if re.match(r"^[=-]+$", line.strip()):
            continue

        promo = " P " in line or line.endswith(" P") or "  P  " in line
        mult = _MULT_RE.match(line)
        if mult and pending:
            items.append(
                {
                    "name": pending,
                    "quantity": _num(mult.group(1)),
                    "price": _num(mult.group(2)),
                    "total": _num(mult.group(3)),
                    "promotion": promo,
                }
            )
            pending = None
            continue

        single = _SINGLE_RE.match(line)
        if single:
            name = f"{pending} {single.group(1).strip()}".strip() if pending else single.group(1).strip()
            price = _num(single.group(2))
            items.append(
                {"name": name, "quantity": 1.0, "price": price, "total": price, "promotion": promo}
            )
            pending = None
        elif line.strip():
            pending = line.strip()

    total_discount: float | None = None
    for line in lines:
        match = _DISCOUNT_RE.search(line)
        if match:
            total_discount = _num(match.group(1))
            break

    return {"items": items, "total": total, "total_discount": total_discount}
