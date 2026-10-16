"""Price level normalisation."""

from __future__ import annotations

import re

DOLLAR_RE = re.compile(r"^\s*(\${1,4})\s*$")
RANGE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:-|–|to)\s*(\d+(?:\.\d+)?)\s*(?:sgd|dollars)", re.I)
# a dollar amount, optionally a range: "$15", "S$18-S$28", "$20-40", "SGD 30"
MONEY_RE = re.compile(
    r"(?:S\$|SGD\s*|\$)\s*(\d+(?:\.\d+)?)(?:\s*(?:-|–|to)\s*(?:S\$|\$)?\s*(\d+(?:\.\d+)?))?", re.I
)
# a bare number only counts with a per-person or currency word after it ("30 per pax", "40 SGD")
BARE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:sgd|dollars|per (?:pax|person|head)|/pax|pp\b)", re.I)
# other currencies (Johor Bahru, Bangkok, Tokyo guides): no Singapore price to read
FOREIGN_RE = re.compile(
    r"(?:\bRM\s*\d|\bMYR\b|\bTHB\b|฿|\bIDR\b|\bRp\s*\d|US\$|\bUSD\b|HK\$|NT\$|\bJPY\b|¥|€|£)"
)


def price_level_from_text(text: str | None) -> int | None:
    """``"$$"`` -> 2, ``"20-40 SGD"`` -> 2 (mid point), ``"~$120 per pax"`` -> 4,
    ``"4-course lunch S$108"`` -> 3 (the dollar amount, not the 4), ``"RM20 – RM40"`` -> None."""
    if not text:
        return None
    match = DOLLAR_RE.match(text)
    if match:
        return len(match.group(1))
    if FOREIGN_RE.search(text) and not re.search(r"S\$|SGD", text, re.I):
        return None
    amount: float | None = None
    money = MONEY_RE.search(text)
    if money:
        lo = float(money.group(1))
        amount = (lo + float(money.group(2))) / 2 if money.group(2) else lo
    else:
        rng = RANGE_RE.search(text)
        bare = BARE_RE.search(text)
        if rng:
            amount = (float(rng.group(1)) + float(rng.group(2))) / 2
        elif bare:
            amount = float(bare.group(1))
    if amount is None or amount <= 0:
        return None
    if amount < 25:
        return 1
    if amount < 60:
        return 2
    if amount < 120:
        return 3
    return 4


def price_symbol(level: int | None) -> str | None:
    return "$" * level if level else None
