"""Score venues per category.

score = Σ_c w_c · component_c  (weights renormalised over the components that have data)
      + michelin_bonus + multi_source_bonus + min(buzz_bonus_max, buzz)
      - declining_trend_penalty - poor_hygiene_penalty - temporarily_closed_penalty
personal blend: (1 - p) · score + p · my_rating / 5
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sgfoodhunt.config import Category, ScoringSettings
from sgfoodhunt.dedup.venue import Venue

NOISE_QUIET = ("quiet", "peaceful", "serene", "intimate", "tranquil")
NOISE_LOUD = ("loud", "noisy", "crowded", "bustling", "rowdy")
PARKING_SCORES = {"own": 1.0, "public_nearby": 0.7, "street": 0.4, "none": 0.0}


@dataclass(slots=True)
class PersonalNote:
    status: str | None = None  # visited | wishlist | excluded
    my_rating: float | None = None


@dataclass(slots=True)
class ScoredVenue:
    venue_id: str
    name: str
    score: float
    rank: int
    components: dict[str, float] = field(default_factory=dict)
    adjustments: dict[str, float] = field(default_factory=dict)
    excluded_reason: str | None = None
    hidden: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "venue_id": self.venue_id,
            "name": self.name,
            "score": round(self.score, 4),
            "rank": self.rank,
            "components": {k: round(v, 4) for k, v in self.components.items()},
            "adjustments": {k: round(v, 4) for k, v in self.adjustments.items()},
            "excluded_reason": self.excluded_reason,
            "hidden": self.hidden,
        }


# --- components -------------------------------------------------------------------------------
def bayesian_rating(
    rating: float | None, count: int | None, prior_rating: float, prior_count: int
) -> float | None:
    if rating is None:
        return None
    n = max(0, count or 0)
    value = (prior_count * prior_rating + n * rating) / (prior_count + n)
    return max(0.0, min(1.0, (value - 1) / 4))  # 1..5 stars -> 0..1


def recency_weight(
    published: str | None, today: date, half_life_months: float, stale_years: float
) -> float:
    if not published:
        return 0.5  # undated evidence counts at half
    try:
        when = date.fromisoformat(published[:10])
    except ValueError:
        return 0.5
    age_months = max(0.0, (today - when).days / 30.4)
    if age_months > stale_years * 12:
        return 0.05
    return float(0.5 ** (age_months / half_life_months))


def recommendation_score(venue: Venue, today: date, s: ScoringSettings) -> float:
    """Sum of recency weighted independent recommendations (raw, normalised across the category later)."""
    total = 0.0
    seen: set[str] = set()
    for ev in venue.evidence:
        if not ev.independent or ev.confidence < 0.5:
            continue
        key = f"{ev.source_key}|{ev.url}"
        if key in seen:
            continue
        seen.add(key)
        total += (
            recency_weight(
                ev.published_at,
                today,
                s.recommendation_half_life_months,
                s.recommendation_stale_after_years,
            )
            * ev.confidence
        )
    return total


def keyword_hits(venue: Venue, keywords: list[str]) -> int:
    texts = [e.snippet or "" for e in venue.evidence] + [e.title or "" for e in venue.evidence]
    texts += [r.get("snippet") or "" for r in venue.review_snippets]
    blob = " ".join(texts).lower()
    return sum(len(re.findall(rf"(?<!\w){re.escape(k.lower())}(?!\w)", blob)) for k in keywords)


def noise_component(venue: Venue) -> float | None:
    blob = " ".join(
        [e.snippet or "" for e in venue.evidence]
        + [r.get("snippet") or "" for r in venue.review_snippets]
    ).lower()
    quiet = sum(blob.count(w) for w in NOISE_QUIET) + ("quiet" in venue.ambience_tags)
    loud = sum(blob.count(w) for w in NOISE_LOUD) + ("lively" in venue.ambience_tags)
    if quiet == loud == 0:
        return None
    return quiet / (quiet + loud)


def components_for(
    venue: Venue,
    category: Category,
    s: ScoringSettings,
    today: date,
    aspects: dict[str, float] | None,
) -> dict[str, float | None]:
    """Every weight component; None means "no data" and the weight is redistributed."""
    rating: float | None = None
    best: tuple[float, int] | None = None
    for src, r in venue.ratings.items():
        rv = r.get("rating")
        if rv is None:
            continue
        pair = (float(rv), int(r.get("review_count") or 0))
        if src == "google_places" or best is None or pair[1] > best[1]:
            best = pair
        if src == "google_places":
            break
    if best is not None:
        rating = bayesian_rating(
            best[0], best[1], s.bayesian_prior_rating, s.bayesian_prior_reviews
        )
    hits = keyword_hits(venue, category.keywords)
    flags = venue.flags
    space: float | None = None
    if flags.get("good_for_groups") is not None or flags.get("private_room"):
        space = 1.0 if flags.get("good_for_groups") or flags.get("private_room") else 0.3
    kid: float | None = None
    if flags.get("kid_friendly") is not None:
        kid = 1.0 if flags["kid_friendly"] else 0.0
    weekend: float | None = None
    if venue.open_weekends is not None:
        weekend = 1.0 if venue.open_weekends else 0.0
    aspects = aspects or {}
    return {
        "rating": rating,
        "recommendations": recommendation_score(venue, today, s),  # normalised by caller
        "keyword_match": math.log1p(hits) / math.log1p(20) if hits else 0.0,
        "food": aspects.get("food"),
        "service": aspects.get("service"),
        "ambience": aspects.get("ambience")
        if "ambience" in aspects
        else (0.7 if venue.ambience_tags else None),
        "value": aspects.get("value"),
        "space": space,
        "kid_friendly": kid,
        "parking": PARKING_SCORES.get(str(flags.get("parking"))) if flags.get("parking") else None,
        "weekend_open": weekend,
        "quiet": noise_component(venue),
    }


# --- filters ----------------------------------------------------------------------------------
def hard_filter_reason(
    venue: Venue, category: Category, s: ScoringSettings, today: date
) -> str | None:
    hf = category.hard_filters
    if hf.exclude_closed and venue.business_status == "CLOSED_PERMANENTLY":
        return "closed permanently"
    if hf.open_weekends and venue.open_weekends is False:
        return "not open on weekends"
    if hf.requires_private_room and not venue.flags.get("private_room"):
        return "no private room evidence"
    if hf.halal_only and not venue.flags.get("halal"):
        return "not halal"
    if hf.max_price_level and venue.price_level and venue.price_level > hf.max_price_level:
        return f"price level {venue.price_level} above {hf.max_price_level}"
    if hf.price_required and not venue.price_level:
        from sgfoodhunt.occasion import is_buffet, star_count

        if not (star_count(venue.michelin) or venue.michelin == "Bib Gourmand" or is_buffet(venue)):
            return "no price level"
    if hf.bill_range_sgd and venue.price_level:
        per_pax = s.price_per_pax_sgd.get(venue.price_level)
        if per_pax is not None:
            bill, (low, high) = per_pax * category.party_size, hf.bill_range_sgd
            if not low <= bill <= high:
                return f"estimated bill S${bill:.0f} outside S${low:.0f} to S${high:.0f}"
    if hf.opened_within_months:
        if not venue.earliest_evidence:
            return "no dated evidence for opening"
        days = (
            s.new_within_days * hf.opened_within_months
            if hf.opened_within_months == 1
            else int(hf.opened_within_months * 30.4)
        )
        try:
            earliest = date.fromisoformat(venue.earliest_evidence[:10])
        except ValueError:
            return "undated opening evidence"
        if earliest < today - timedelta(days=days):
            return f"earliest evidence {earliest.isoformat()} is older than {days} days"
    return None


# --- main -------------------------------------------------------------------------------------
def score_category(
    category: Category,
    venues: list[Venue],
    settings: ScoringSettings,
    personal: dict[str, PersonalNote] | None = None,
    aspects: dict[str, dict[str, float]] | None = None,
    buzz: dict[str, float] | None = None,
    trends: dict[str, str] | None = None,
    hide_visited: bool = False,
    today: date | None = None,
) -> list[ScoredVenue]:
    """Rank ``venues`` for one category. Excluded venues come last with ``excluded_reason`` set."""
    today = today or datetime.now(UTC).date()
    personal = personal or {}
    aspects = aspects or {}
    buzz = buzz or {}
    trends = trends or {}
    weights = category.normalised_weights()

    prepared: list[tuple[Venue, dict[str, float | None], str | None]] = []
    for v in venues:
        note = personal.get(v.id)
        if note and note.status == "excluded":
            prepared.append((v, {}, "excluded by you"))
            continue
        reason = hard_filter_reason(v, category, settings, today)
        prepared.append(
            (
                v,
                components_for(v, category, settings, today, aspects.get(v.id))
                if not reason
                else {},
                reason,
            )
        )

    max_rec = max((c.get("recommendations") or 0.0 for _, c, r in prepared if not r), default=0.0)
    results: list[ScoredVenue] = []
    for v, comps, reason in prepared:
        if reason:
            results.append(ScoredVenue(v.id, v.name, 0.0, 0, excluded_reason=reason))
            continue
        if max_rec > 0:
            comps["recommendations"] = (comps.get("recommendations") or 0.0) / max_rec
        available = {k: w for k, w in weights.items() if comps.get(k) is not None and w > 0}
        total_w = sum(available.values())
        weighted = (
            {k: (comps[k] or 0.0) * (w / total_w) for k, w in available.items()} if total_w else {}
        )
        score = sum(weighted.values())
        adj: dict[str, float] = {}
        if v.michelin:
            adj["michelin"] = settings.michelin_bonus
        if len(v.independent_sources) >= settings.multi_source_threshold:
            adj["multi_source"] = settings.multi_source_bonus
        if buzz.get(v.id):
            adj["buzz"] = min(settings.buzz_bonus_max, buzz[v.id])
        if trends.get(v.id) == "declining":
            adj["declining_trend"] = -settings.declining_trend_penalty
        if v.hygiene_grade and v.hygiene_grade.upper() in ("C", "D"):
            adj["poor_hygiene"] = -settings.poor_hygiene_penalty
        if v.business_status == "CLOSED_TEMPORARILY":
            adj["temporarily_closed"] = -settings.temporarily_closed_penalty
        score += sum(adj.values())
        note = personal.get(v.id)
        if note and note.my_rating is not None:
            p = settings.personal_rating_weight
            score = (1 - p) * score + p * (max(0.0, min(5.0, note.my_rating)) / 5)
            adj["personal"] = p
        score = max(0.0, min(1.0, score))
        hidden = bool(hide_visited and note and note.status == "visited")
        results.append(
            ScoredVenue(v.id, v.name, score, 0, components=weighted, adjustments=adj, hidden=hidden)
        )

    ranked = sorted((r for r in results if not r.excluded_reason), key=lambda r: (-r.score, r.name))
    rank = 0
    for r in ranked:
        if not r.hidden:
            rank += 1
            r.rank = rank
    excluded = sorted((r for r in results if r.excluded_reason), key=lambda r: r.name)
    return ranked + excluded
