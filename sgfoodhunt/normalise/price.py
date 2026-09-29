"""Price level normalisation."""

from __future__ import annotations

import re

DOLLAR_RE = re.compile(r"^\s*(\${1,4})\s*$")
RANGE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:-|–|to)\s*(\d+(?:\.\d+)?)")
SINGLE_RE = re.compile(r"\$?\s*(\d+(?:\.\d+)?)")


def price_level_from_text(text: str | None) -> int | None:
    """``"$$"`` -> 2, ``"20-40 SGD"`` -> 2 (by mid point), ``"~$120 per pax"`` -> 3."""
    if not text:
        return None
    match = DOLLAR_RE.match(text)
    if match:
        return len(match.group(1))
    match = RANGE_RE.search(text)
    amount: float | None = None
    if match:
        lo, hi = float(match.group(1)), float(match.group(2))
        amount = (lo + hi) / 2
    else:
        match = SINGLE_RE.search(text)
        if match:
            amount = float(match.group(1))
    if amount is None:
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
