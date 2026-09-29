"""Canonical venue record (one per outlet) and the evidence attached to it."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class Evidence:
    source_key: str
    url: str | None
    title: str | None = None
    published_at: str | None = None
    category_keys: list[str] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    snippet: str | None = None
    rating: float | None = None
    review_count: int | None = None
    confidence: float = 1.0
    independent: bool = True
    captured_at: str | None = None
    run_id: str | None = None


@dataclass(slots=True)
class Venue:
    id: str
    name: str
    name_zh: str | None = None
    brand: str | None = None
    outlet: str | None = None
    aliases: list[str] = field(default_factory=list)
    address: str | None = None
    postal_code: str | None = None
    district: str | None = None
    region: str | None = None
    lat: float | None = None
    lng: float | None = None
    phone: str | None = None
    website: str | None = None
    booking_url: str | None = None
    google_place_id: str | None = None
    cuisine: list[str] = field(default_factory=list)
    price_level: int | None = None
    price_text: str | None = None
    hours: dict[str, list[list[str]]] | None = None
    open_weekends: bool | None = None
    late_night: bool | None = None
    ph_closed: bool | None = None
    business_status: str | None = None
    michelin: str | None = None
    hygiene_grade: str | None = None
    flags: dict[str, bool | None] = field(default_factory=dict)
    ambience_tags: list[str] = field(default_factory=list)
    ratings: dict[str, dict[str, float | int | None]] = field(default_factory=dict)
    evidence: list[Evidence] = field(default_factory=list)
    review_snippets: list[dict[str, Any]] = field(default_factory=list)
    social_words: int = 0
    earliest_evidence: str | None = None
    first_seen_run: str | None = None
    last_seen_run: str | None = None

    # -- derived -----------------------------------------------------------------------------
    @property
    def source_keys(self) -> list[str]:
        return sorted({e.source_key for e in self.evidence})

    @property
    def independent_sources(self) -> list[str]:
        return sorted(
            {e.source_key for e in self.evidence if e.independent and e.confidence >= 0.5}
        )

    @property
    def category_keys(self) -> list[str]:
        return sorted({c for e in self.evidence for c in e.category_keys if c != "*"})

    @property
    def google_rating(self) -> float | None:
        g = self.ratings.get("google_places")
        value = g.get("rating") if g else None
        return float(value) if value is not None else None

    @property
    def google_reviews(self) -> int | None:
        g = self.ratings.get("google_places")
        value = g.get("review_count") if g else None
        return int(value) if value is not None else None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Venue:
        data = dict(data)
        data["evidence"] = [Evidence(**e) for e in data.get("evidence", [])]
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
