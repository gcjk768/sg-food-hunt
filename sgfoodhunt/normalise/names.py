"""Venue name normalisation and brand / outlet splitting."""

from __future__ import annotations

import re
import unicodedata

NOISE_WORDS = {
    "restaurant",
    "restaurants",
    "cafe",
    "café",
    "coffee",
    "bar",
    "bistro",
    "kitchen",
    "eatery",
    "eating",
    "house",
    "place",
    "the",
    "pte",
    "ltd",
    "llp",
    "sg",
    "singapore",
    "sgp",
    "outlet",
    "branch",
    "seafood",
    "food",
    "stall",
    "@",
    "and",
    "&",
}
LEGAL_SUFFIXES = {"pte", "ltd", "llp", "llc", "inc", "sg", "singapore", "sgp"}
LEGAL_SUFFIXES = {"pte", "ltd", "llp", "llc", "inc", "sg", "singapore", "sgp"}
OUTLET_SEP_RE = re.compile(r"\s+(?:@|at|-|–|—|\||/)\s+|\s*[(（]([^)）]+)[)）]\s*$")
CJK_RE = re.compile(r"[一-鿿]+")


def _ascii_fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def name_key(name: str, keep_noise: bool = False) -> str:
    """Lowercase, accent folded, punctuation free key used for exact matching.

    ``"Keng Eng Kee Seafood (琼荣记)"`` -> ``"keng eng kee"``. Chinese characters are kept as a
    separate token so Chinese-only names still get a key.
    """
    text = _ascii_fold(name).lower()
    cjk = " ".join(CJK_RE.findall(text))
    text = CJK_RE.sub(" ", text)
    text = re.sub(r"[^a-z0-9\s]+", " ", text)
    words = [t for t in text.split() if t not in LEGAL_SUFFIXES]
    tokens = [t for t in words if keep_noise or t not in NOISE_WORDS]
    if not tokens:  # every word is generic ("The Coffee Bar"): keep all but articles
        tokens = [t for t in words if t not in ("the", "a", "an")] or words or text.split()
    key = " ".join(tokens)
    if cjk:
        key = f"{key} {cjk}".strip()
    return key


def split_brand_outlet(name: str) -> tuple[str, str | None]:
    """``"Din Tai Fung @ Paragon"`` -> ``("Din Tai Fung", "Paragon")``; plain names give None."""
    match = OUTLET_SEP_RE.search(name)
    if not match:
        return name.strip(), None
    brand = name[: match.start()].strip()
    outlet = (match.group(1) or name[match.end() :]).strip()
    if not brand or not outlet or CJK_RE.fullmatch(outlet):
        return name.strip(), None
    if len(outlet.split()) > 4:
        return name.strip(), None
    return brand, outlet
