"""Match raw sightings to canonical venues and keep venue ids stable across runs.

Matching order (every step is logged when it merges two different raw names):

1. Google place id (strongest anchor).
2. Normalised name + postal code, exact.
3. Phone number or booking link shared with an existing venue (renamed / relocated venues).
4. Fuzzy name match (rapidfuzz ``token_set_ratio`` >= ``fuzzy_match_threshold``) when at least
   one side has no postal code, or both share the same postal sector. Every fuzzy merge is written
   to ``merges.jsonl`` for review.

Low confidence sightings (Reddit heuristics, ``confidence <= 0.5``) and ``enrich_only`` rows
(SFA grades) never create a venue; they only attach to one that already exists.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz

from sgfoodhunt.config import AppConfig
from sgfoodhunt.dedup.venue import Evidence, Venue
from sgfoodhunt.http.cache import read_json, write_json
from sgfoodhunt.normalise import (
    name_key,
    normalise_cuisines,
    normalise_hours,
    price_level_from_text,
    region_for_postal,
    split_brand_outlet,
)
from sgfoodhunt.scrapers.html import strip_news_wording

log = logging.getLogger(__name__)

AMBIENCE_WORDS = {
    "romantic": ("romantic", "date night", "candlelit", "intimate"),
    "quiet": ("quiet", "peaceful", "serene", "tranquil"),
    "lively": ("lively", "buzzing", "bustling", "vibrant"),
    "rooftop": ("rooftop", "roof top", "sky bar"),
    "waterfront": (
        "waterfront",
        "riverside",
        "by the river",
        "seaside",
        "marina",
        "bayfront",
        "by the sea",
        "beachfront",
    ),
    "garden": ("garden", "greenery", "alfresco", "al fresco"),
    "heritage": ("heritage", "shophouse", "colonial", "conservation", "black and white bungalow"),
    "cosy": ("cosy", "cozy", "homely"),
    "view": ("view", "skyline", "sunset"),
}
PRIVATE_ROOM_RE = re.compile(
    r"private (?:dining )?rooms?|private dining|function room|包厢|包间", re.I
)
FLAG_KEYS = (
    "kid_friendly",
    "good_for_groups",
    "outdoor_seating",
    "wheelchair",
    "reservable",
    "vegetarian_options",
    "pet_friendly",
    "private_room",
    "halal",
)


@dataclass(slots=True)
class MergeEvent:
    run_id: str
    kept_venue_id: str
    kept_name: str
    merged_name: str
    merged_source: str
    method: str
    score: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "kept_venue_id": self.kept_venue_id,
            "kept_name": self.kept_name,
            "merged_name": self.merged_name,
            "merged_source": self.merged_source,
            "method": self.method,
            "score": self.score,
            "reviewed": False,
        }


@dataclass(slots=True)
class MatchStats:
    sightings: int = 0
    attached: int = 0
    created: int = 0
    skipped_low_confidence: int = 0
    by_method: dict[str, int] = field(default_factory=dict)
    merges: list[MergeEvent] = field(default_factory=list)


class VenueRegistry:
    """Canonical venues persisted at ``data/venues.json``."""

    def __init__(self, path: Path | str, fuzzy_threshold: int = 88) -> None:
        self.path = Path(path)
        self.fuzzy_threshold = fuzzy_threshold
        self.venues: dict[str, Venue] = {}
        self._next = 1
        self._by_place: dict[str, str] = {}
        self._by_key_postal: dict[tuple[str, str], str] = {}
        self._by_phone: dict[str, str] = {}
        self._by_booking: dict[str, str] = {}
        self._by_key: dict[str, list[str]] = {}
        if self.path.exists():
            self._load()

    # -- persistence ---------------------------------------------------------------------------
    def _load(self) -> None:
        data = read_json(self.path)
        for raw in data.get("venues", []):
            venue = Venue.from_dict(raw)
            clean = strip_news_wording(venue.name)  # names saved before the cleaner learned a rule
            if clean and clean != venue.name:
                venue.name = clean
                brand, outlet = split_brand_outlet(clean)
                venue.brand, venue.outlet = (brand if outlet else None), outlet
            self.venues[venue.id] = venue
            self._index(venue)
        self._next = int(data.get("next_id", len(self.venues) + 1))

    def save(self) -> None:
        write_json(
            self.path,
            {
                "saved_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "next_id": self._next,
                "venues": [v.to_dict() for v in self.venues.values()],
            },
        )

    def _index(self, venue: Venue) -> None:
        if venue.google_place_id:
            self._by_place[venue.google_place_id] = venue.id
        keys = {name_key(venue.name), *(name_key(a) for a in venue.aliases)}
        for key in keys:
            if not key:
                continue
            self._by_key.setdefault(key, [])
            if venue.id not in self._by_key[key]:
                self._by_key[key].append(venue.id)
            if venue.postal_code:
                self._by_key_postal[(key, venue.postal_code)] = venue.id
        if venue.phone:
            self._by_phone[venue.phone] = venue.id
        if venue.booking_url:
            self._by_booking[venue.booking_url] = venue.id

    # -- matching ------------------------------------------------------------------------------
    def find(self, row: dict[str, Any]) -> tuple[str | None, str, float | None]:
        """Return (venue_id, method, score) for a raw row, or (None, "none", None)."""
        key = name_key(row["name"])
        postal = row.get("postal_code")
        extra = row.get("extra") or {}
        place_id = (
            row.get("source_ref")
            if row["source_key"] in ("google_places", "google_reserve")
            else None
        )
        if place_id and place_id in self._by_place:
            return self._by_place[place_id], "place_id", None
        if postal and (key, postal) in self._by_key_postal:
            return self._by_key_postal[(key, postal)], "name_postal", None
        phone = row.get("phone")
        if phone and phone in self._by_phone:
            return self._by_phone[phone], "phone", None
        booking = row.get("booking_url")
        if booking and booking in self._by_booking:
            return self._by_booking[booking], "booking_link", None
        # exact key, postal missing on one side
        for vid in self._by_key.get(key, []):
            v = self.venues[vid]
            if not postal or not v.postal_code:
                return vid, "name_exact", None
        # fuzzy: compare against venues in the same postal sector or without postal
        best_id, best_score = None, 0.0
        for vid, v in self.venues.items():
            if postal and v.postal_code and postal != v.postal_code:
                continue
            candidates = [name_key(v.name), *(name_key(a) for a in v.aliases)]
            score = (
                max(fuzz.token_set_ratio(key, c) for c in candidates if c) if any(candidates) else 0
            )
            if score > best_score:
                best_id, best_score = vid, float(score)
        if best_id and best_score >= self.fuzzy_threshold:
            if extra.get("enrich_only") and best_score < 95:
                return None, "none", best_score  # catalogue rows need a very close name
            return best_id, "fuzzy_name", best_score
        return None, "none", None

    # -- building ------------------------------------------------------------------------------
    def _new_id(self) -> str:
        vid = f"v{self._next:05d}"
        self._next += 1
        return vid

    def create(self, row: dict[str, Any], run_id: str) -> Venue:
        name = strip_news_wording(row["name"])
        brand, outlet = split_brand_outlet(name)
        venue = Venue(
            id=self._new_id(),
            name=name,
            brand=brand if outlet else None,
            outlet=outlet,
            first_seen_run=run_id,
            last_seen_run=run_id,
        )
        self.venues[venue.id] = venue
        return venue

    def attach(
        self,
        venue: Venue,
        row: dict[str, Any],
        run_id: str,
        independent: bool,
        snippets_limit: int = 12,
    ) -> None:
        extra = row.get("extra") or {}
        src = row["source_key"]
        venue.last_seen_run = run_id
        # identity
        if row["name"] != venue.name and row["name"] not in venue.aliases:
            venue.aliases.append(row["name"])
        if row.get("name_zh") and not venue.name_zh:
            venue.name_zh = row["name_zh"]
        if src in ("google_places", "google_reserve") and row.get("source_ref"):
            venue.google_place_id = row["source_ref"]
        for attr in ("address", "postal_code", "lat", "lng", "phone", "website"):
            value = row.get(attr)
            if value and (not getattr(venue, attr) or src == "google_places"):
                setattr(venue, attr, value)
        if row.get("booking_url") and (not venue.booking_url or src != "google_reserve"):
            venue.booking_url = row["booking_url"]
        if venue.postal_code and not venue.region:
            venue.district, venue.region = region_for_postal(venue.postal_code)
        # dining details
        for label in normalise_cuisines(row.get("cuisine")):
            if label not in venue.cuisine:
                venue.cuisine.append(label)
        level = row.get("price_level") or price_level_from_text(row.get("price_text"))
        if level and (not venue.price_level or src == "google_places"):
            venue.price_level = level
        if row.get("price_text") and not venue.price_text:
            venue.price_text = row["price_text"]
        hours = normalise_hours(row.get("opening_hours"))
        if hours and (venue.hours is None or hours.source == "google"):
            venue.hours = hours.hours
            venue.open_weekends, venue.late_night, venue.ph_closed = (
                hours.open_weekends,
                hours.late_night,
                hours.ph_closed,
            )
        if row.get("business_status"):
            venue.business_status = row["business_status"]
        if row.get("michelin") and (not venue.michelin or row["michelin"] != "Selected"):
            venue.michelin = row["michelin"]
        if row.get("hygiene_grade"):
            venue.hygiene_grade = row["hygiene_grade"]
        # flags from Google extras and text
        mapping = {
            "kid_friendly": extra.get("good_for_children"),
            "good_for_groups": extra.get("good_for_groups"),
            "outdoor_seating": extra.get("outdoor_seating"),
            "wheelchair": extra.get("wheelchair_accessible_entrance"),
            "reservable": extra.get("reservable"),
            "vegetarian_options": extra.get("serves_vegetarian_food"),
            "pet_friendly": extra.get("allows_dogs"),
        }
        for k, val in mapping.items():
            if val is not None:
                venue.flags[k] = bool(val)
        text = " ".join(
            str(x) for x in (row.get("snippet"), extra.get("article_title"), row.get("query")) if x
        ).lower()
        if PRIVATE_ROOM_RE.search(text):
            venue.flags["private_room"] = True
        if "halal" in text or "Halal" in venue.cuisine:
            venue.flags["halal"] = True
        for tag, words in AMBIENCE_WORDS.items():
            if tag not in venue.ambience_tags and any(w in text for w in words):
                venue.ambience_tags.append(tag)
        # ratings, reviews, social words
        if row.get("rating") is not None and src != "google_reserve":
            venue.ratings[src] = {"rating": row["rating"], "review_count": row.get("review_count")}
        for rev in extra.get("reviews") or []:
            if (
                rev.get("snippet")
                and len(venue.review_snippets) < snippets_limit
                and not any(s["snippet"] == rev["snippet"] for s in venue.review_snippets)
            ):
                venue.review_snippets.append(
                    {
                        "source": src,
                        "rating": rev.get("rating"),
                        "published_at": rev.get("published_at"),
                        "snippet": rev["snippet"],
                    }
                )
            venue.social_words += int(rev.get("social_words") or 0)
        # evidence (one per source url; merge categories / queries)
        ref = row.get("source_ref") or ""
        url = (
            ref.split("#")[0]
            if ref.startswith("http")
            else (row.get("page_url") or row.get("website"))
        )
        # Only genuinely dated evidence counts (article date, Reddit thread date); the capture
        # date is not an opening date and would make every venue look "new" on first sight.
        published = extra.get("published_at")
        if not published and extra.get("thread_created_utc"):
            published = (
                datetime.fromtimestamp(float(extra["thread_created_utc"]), tz=UTC)
                .date()
                .isoformat()
            )
        for ev in venue.evidence:
            if ev.source_key == src and ev.url == url:
                for c in (row.get("category_key"),):
                    if c and c not in ev.category_keys:
                        ev.category_keys.append(c)
                if row.get("query") not in ev.queries:
                    ev.queries.append(row["query"])
                ev.run_id = run_id
                break
        else:
            venue.evidence.append(
                Evidence(
                    source_key=src,
                    url=url,
                    title=extra.get("article_title") or extra.get("thread_title"),
                    published_at=published or None,
                    category_keys=[row["category_key"]],
                    queries=[row["query"]],
                    snippet=row.get("snippet"),
                    rating=row.get("rating"),
                    review_count=row.get("review_count"),
                    confidence=float(row.get("confidence", 1.0)),
                    independent=independent,
                    captured_at=row.get("captured_at"),
                    run_id=run_id,
                )
            )
        dates = [e.published_at for e in venue.evidence if e.published_at]
        for rev in extra.get("reviews") or []:
            if rev.get("published_at"):
                dates.append(str(rev["published_at"])[:10])
        if dates:
            venue.earliest_evidence = min(dates)
        self._index(venue)

    def ingest(
        self, rows: Iterable[dict[str, Any]], run_id: str, independent_sources: set[str]
    ) -> MatchStats:
        stats = MatchStats()
        ordered = sorted(
            rows,
            key=lambda r: (-float(r.get("confidence", 1.0)), r["source_key"] != "google_places"),
        )
        for row in ordered:
            stats.sightings += 1
            extra = row.get("extra") or {}
            vid, method, score = self.find(row)
            if vid is None:
                if float(row.get("confidence", 1.0)) <= 0.5 or extra.get("enrich_only"):
                    stats.skipped_low_confidence += 1
                    continue
                venue = self.create(row, run_id)
                stats.created += 1
                method = "created"
            else:
                venue = self.venues[vid]
                stats.attached += 1
                if method in ("fuzzy_name", "phone", "booking_link") and name_key(
                    row["name"]
                ) != name_key(venue.name):
                    stats.merges.append(
                        MergeEvent(
                            run_id,
                            venue.id,
                            venue.name,
                            row["name"],
                            row["source_key"],
                            method,
                            score,
                        )
                    )
            stats.by_method[method] = stats.by_method.get(method, 0) + 1
            self.attach(venue, row, run_id, independent=row["source_key"] in independent_sources)
        return stats


def build_venues(
    config: AppConfig, rows: Iterable[dict[str, Any]], run_id: str, run_dir: Path
) -> tuple[VenueRegistry, MatchStats]:
    """Ingest a run's raw rows into the persistent registry and write the run's venues.json + merges.jsonl."""
    registry = VenueRegistry(
        config.resolve(config.settings.paths.data_dir) / "venues.json",
        fuzzy_threshold=config.settings.scoring.fuzzy_match_threshold,
    )
    independent = {s.key for s in config.sources.sources if s.independent}
    stats = registry.ingest(rows, run_id, independent)
    registry.save()
    seen = [v for v in registry.venues.values() if v.last_seen_run == run_id]
    write_json(run_dir / "venues.json", [v.to_dict() for v in seen])
    if stats.merges:
        with (run_dir / "merges.jsonl").open("a", encoding="utf-8") as fh:
            for m in stats.merges:
                fh.write(json.dumps(m.to_dict(), ensure_ascii=False) + "\n")
    log.info(
        "dedup: %d sightings -> %d venues seen this run (%d new, %d fuzzy merges)",
        stats.sightings,
        len(seen),
        stats.created,
        len(stats.merges),
    )
    return registry, stats
