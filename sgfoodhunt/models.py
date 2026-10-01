"""Data models shared between scrapers, storage and later pipeline stages."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

BusinessStatus = Literal["OPERATIONAL", "CLOSED_TEMPORARILY", "CLOSED_PERMANENTLY"]

POSTAL_RE = re.compile(r"(?<!\d)(?:Singapore\s*|S\(?)?(\d{6})\)?(?!\d)", re.IGNORECASE)
PHONE_RE = re.compile(r"(?<!\d)(?:\+65[\s-]?)?([689]\d{3})[\s-]?(\d{4})(?!\d)")
SNIPPET_MAX = 300


def utcnow_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


_MONTHS = frozenset(
    [
        "january",
        "february",
        "march",
        "april",
        "may",
        "june",
        "july",
        "august",
        "september",
        "october",
        "november",
        "december",
        "jan",
        "feb",
        "mar",
        "apr",
        "jun",
        "jul",
        "aug",
        "sep",
        "sept",
        "oct",
        "nov",
        "dec",
    ]
)


def is_date_only(name: str) -> bool:
    """Month headings of monthly round-ups ("July 2026", "12 Feb", "2026") are not venues."""
    words = re.findall(r"[a-z]+|\d+", name.lower())
    if not words or not all(w.isdigit() or w in _MONTHS for w in words):
        return False
    return any(w in _MONTHS for w in words) or bool(re.fullmatch(r"(?:19|20)\d\d", "".join(words)))


def extract_postal_code(text: str | None) -> str | None:
    """Return the first Singapore six digit postal code in ``text``."""
    if not text:
        return None
    match = POSTAL_RE.search(text)
    return match.group(1) if match else None


def extract_phone(text: str | None) -> str | None:
    if not text:
        return None
    match = PHONE_RE.search(text)
    if not match:
        return None
    return f"+65 {match.group(1)} {match.group(2)}"


def clean_snippet(text: str | None, limit: int = SNIPPET_MAX) -> str | None:
    """Collapse whitespace and truncate to a short, anonymised snippet."""
    if not text:
        return None
    collapsed = re.sub(r"\s+", " ", text).strip()
    if not collapsed:
        return None
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1].rstrip() + "…"


@dataclass(slots=True)
class SearchQuery:
    category_key: str
    category_group: str
    text: str
    party_size: int = 2


@dataclass(slots=True)
class SourcePage:
    """A page or API response that produced evidence."""

    source_key: str
    url: str
    title: str | None = None
    published_at: str | None = None
    content_hash: str | None = None


@dataclass(slots=True)
class VenueCandidate:
    """A single sighting of a venue in one source for one query. Not yet deduplicated."""

    source_key: str
    name: str
    source_ref: str | None = None
    name_zh: str | None = None
    brand: str | None = None
    address: str | None = None
    postal_code: str | None = None
    lat: float | None = None
    lng: float | None = None
    phone: str | None = None
    website: str | None = None
    booking_url: str | None = None
    rating: float | None = None
    review_count: int | None = None
    price_level: int | None = None
    price_text: str | None = None
    cuisine: list[str] = field(default_factory=list)
    opening_hours: dict[str, Any] | None = None
    business_status: BusinessStatus | None = None
    michelin: str | None = None
    hygiene_grade: str | None = None
    snippet: str | None = None
    confidence: float = 1.0
    extra: dict[str, Any] = field(default_factory=dict)
    page: SourcePage | None = None

    def __post_init__(self) -> None:
        self.name = re.sub(r"\s+", " ", self.name).strip()
        if not self.name:
            raise ValueError("candidate name must not be empty")
        if is_date_only(self.name):
            raise ValueError(f"not a venue name: {self.name!r}")
        if self.postal_code is None:
            self.postal_code = extract_postal_code(self.address)
        if self.phone is None:
            self.phone = extract_phone(self.address)
        self.snippet = clean_snippet(self.snippet)
        self.confidence = max(0.0, min(1.0, self.confidence))

    def to_row(self, run_id: str, query: SearchQuery, page_url: str | None) -> dict[str, Any]:
        """Flatten into a JSON serialisable row for the run's JSONL files."""
        row = asdict(self)
        row.pop("page")
        row.update(
            run_id=run_id,
            category_key=query.category_key,
            category_group=query.category_group,
            party_size=query.party_size,
            query=query.text,
            page_url=page_url,
            captured_at=utcnow_iso(),
        )
        return row


@dataclass(slots=True)
class ScrapeResult:
    source_key: str
    query: SearchQuery
    candidates: list[VenueCandidate] = field(default_factory=list)
    pages: list[SourcePage] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    skipped_reason: str | None = None
    requests_made: int = 0
    cache_hits: int = 0
