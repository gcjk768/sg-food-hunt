"""Lexicon based review analysis. Deliberately simple and dependency free; swap ``aspect_scores``
for a small transformer model later if you want finer sentiment."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from typing import Any

from sgfoodhunt.config import AppConfig, Category
from sgfoodhunt.dedup.venue import Venue

ASPECTS: dict[str, dict[str, tuple[str, ...]]] = {
    "food": {
        "cue": (
            "food",
            "dish",
            "dishes",
            "taste",
            "flavour",
            "flavor",
            "portion",
            "menu",
            "cooked",
            "fresh",
            "prawn",
            "crab",
            "noodle",
            "rice",
            "coffee",
            "cake",
            "dessert",
            "pastry",
            "croissant",
            "hor fun",
            "fried",
        ),
        "pos": (
            "delicious",
            "tasty",
            "yummy",
            "flavourful",
            "flavorful",
            "fresh",
            "generous",
            "must try",
            "must-try",
            "excellent",
            "amazing",
            "best",
            "perfect",
            "superb",
            "wok hei",
            "shiok",
            "good",
        ),
        "neg": (
            "bland",
            "soggy",
            "overcooked",
            "undercooked",
            "stale",
            "greasy",
            "oily",
            "salty",
            "cold food",
            "tasteless",
            "small portion",
            "disappointing",
            "mediocre",
            "average",
        ),
    },
    "service": {
        "cue": (
            "service",
            "staff",
            "waiter",
            "waitress",
            "server",
            "wait",
            "waiting",
            "queue",
            "reservation",
            "attentive",
            "rude",
        ),
        "pos": (
            "attentive",
            "friendly",
            "prompt",
            "helpful",
            "warm",
            "efficient",
            "welcoming",
            "polite",
            "quick",
            "fast",
        ),
        "neg": (
            "rude",
            "slow",
            "ignored",
            "unfriendly",
            "long wait",
            "waited",
            "forgot",
            "inattentive",
            "pushy",
            "chaotic",
        ),
    },
    "ambience": {
        "cue": (
            "ambience",
            "ambiance",
            "atmosphere",
            "vibe",
            "decor",
            "interior",
            "view",
            "music",
            "seating",
            "lighting",
            "setting",
            "space",
        ),
        "pos": (
            "cosy",
            "cozy",
            "romantic",
            "beautiful",
            "stunning",
            "charming",
            "relaxing",
            "lovely",
            "chill",
            "instagrammable",
            "gorgeous",
            "quiet",
            "spacious",
            "comfortable",
        ),
        "neg": (
            "noisy",
            "loud",
            "cramped",
            "stuffy",
            "hot",
            "dirty",
            "dated",
            "crowded",
            "smoky",
            "uncomfortable",
        ),
    },
    "value": {
        "cue": (
            "price",
            "prices",
            "priced",
            "value",
            "worth",
            "expensive",
            "cheap",
            "affordable",
            "bill",
            "cost",
            "$",
        ),
        "pos": (
            "worth",
            "affordable",
            "reasonable",
            "value for money",
            "value-for-money",
            "cheap",
            "good value",
            "wallet friendly",
            "budget",
        ),
        "neg": (
            "expensive",
            "overpriced",
            "pricey",
            "not worth",
            "rip off",
            "rip-off",
            "steep",
            "costly",
        ),
    },
}
QUIET_WORDS = ("quiet", "peaceful", "serene", "tranquil", "calm", "intimate")
LOUD_WORDS = ("noisy", "loud", "crowded", "bustling", "rowdy", "packed")


def _texts(venue: Venue) -> list[str]:
    out = [r.get("snippet") or "" for r in venue.review_snippets]
    out += [e.snippet or "" for e in venue.evidence if e.snippet]
    out += [e.title or "" for e in venue.evidence if e.title]
    return [t.lower() for t in out if t]


def _count(blob: str, phrase: str) -> int:
    return len(re.findall(rf"(?<!\w){re.escape(phrase)}(?!\w)", blob))


def keyword_counts(texts: list[str], keywords: list[str]) -> dict[str, int]:
    blob = " ".join(texts)
    counts = {k: _count(blob, k.lower()) for k in keywords}
    return {k: v for k, v in counts.items() if v}


def aspect_scores(texts: list[str]) -> dict[str, float]:
    """0..1 per aspect (0.5 neutral); an aspect is omitted when nothing mentions it."""
    scores: dict[str, float] = {}
    sentences = [s for t in texts for s in re.split(r"(?<=[.!?])\s+|\n", t) if s.strip()]
    for aspect, lex in ASPECTS.items():
        pos = neg = 0
        for sent in sentences:
            if not any(_count(sent, c) for c in lex["cue"]) and not any(
                _count(sent, p) for p in lex["pos"] + lex["neg"]
            ):
                continue
            negated = bool(re.search(r"\b(not|no|never|isn't|wasn't|aren't|weren't|too)\b", sent))
            p = sum(_count(sent, w) for w in lex["pos"])
            n = sum(_count(sent, w) for w in lex["neg"])
            if negated and p and not n:
                p, n = 0, p
            pos += p
            neg += n
        if pos + neg:
            scores[aspect] = round((pos + 0.5) / (pos + neg + 1.0), 3)  # Laplace smoothed
    return scores


def noise_level(texts: list[str], ambience_tags: list[str]) -> str | None:
    blob = " ".join(texts)
    quiet = sum(_count(blob, w) for w in QUIET_WORDS) + ("quiet" in ambience_tags)
    loud = sum(_count(blob, w) for w in LOUD_WORDS) + ("lively" in ambience_tags)
    if quiet == loud == 0:
        return None
    if quiet > loud * 1.5:
        return "quiet"
    if loud > quiet * 1.5:
        return "loud"
    return "moderate"


def rating_trend(venue: Venue, today: date | None = None) -> str:
    """improving | stable | declining | unknown.

    Prefers the tool's own rating history (Google rating at each run, at least 30 days apart);
    otherwise compares the last 12 months of stored review snippets with the overall rating.
    """
    today = today or datetime.now(UTC).date()
    history = [
        h
        for h in venue.rating_history
        if h.get("source") == "google_places" and h.get("rating") is not None
    ]
    if len(history) >= 2:
        history.sort(key=lambda h: h["date"])
        first, last = history[0], history[-1]
        span = (date.fromisoformat(last["date"]) - date.fromisoformat(first["date"])).days
        if span >= 30:
            delta = float(last["rating"]) - float(first["rating"])
            if delta >= 0.1:
                return "improving"
            if delta <= -0.1:
                return "declining"
            return "stable"
    overall = venue.google_rating
    recent = [
        float(r["rating"])
        for r in venue.review_snippets
        if r.get("rating") is not None
        and r.get("published_at")
        and (today - date.fromisoformat(str(r["published_at"])[:10])).days <= 365
    ]
    if overall is not None and len(recent) >= 3:
        delta = sum(recent) / len(recent) - overall
        if delta >= 0.3:
            return "improving"
        if delta <= -0.3:
            return "declining"
        return "stable"
    return "unknown"


def _join(items: list[str]) -> str:
    items = [i for i in items if i]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


SPECIFIC_CUISINES = (
    "Zi Char",
    "Dim Sum",
    "Hotpot",
    "High Tea",
    "Dessert",
    "Hawker",
    "Buffet",
    "Fine Dining",
    "Cafe",
    "Bar",
)


def primary_cuisine(venue: Venue) -> str | None:
    """The most specific cuisine label (Zi Char beats Chinese, Dim Sum beats Chinese...)."""
    for label in SPECIFIC_CUISINES:
        if label in venue.cuisine:
            return label
    return venue.cuisine[0] if venue.cuisine else None


def _cuisine_affinity(venue: Venue, category: Category) -> bool:
    labels = {c.lower() for c in venue.cuisine}
    return any(k.lower() in labels for k in category.keywords)


def summarise(venue: Venue, config: AppConfig) -> str:
    """One or two sentences in the tool's own words, built only from structured fields."""
    primary = primary_cuisine(venue)
    kind = primary.lower() if primary else "restaurant"
    if kind in ("cafe", "dessert", "high tea"):
        kind_phrase = "cafe" if kind == "cafe" else f"{kind} spot"
    elif kind == "restaurant":
        kind_phrase = "restaurant"
    else:
        kind_phrase = f"{primary} place"
    where = (
        f" in {venue.district.split(' ', 1)[1]}"
        if venue.district and " " in venue.district
        else (f" in the {venue.region}" if venue.region else "")
    )
    price = {1: "budget friendly", 2: "mid priced", 3: "upmarket", 4: "splurge"}.get(
        venue.price_level or 0
    )
    first = f"{venue.name} is a {price + ' ' if price else ''}{kind_phrase}{where}"
    if venue.nearest_mrt and venue.mrt_walk_min:
        first += f", about {venue.mrt_walk_min} min on foot from {venue.nearest_mrt} MRT"
    first += "."
    strengths = [a for a, s in venue.aspects.items() if s >= 0.7]
    weaknesses = [a for a, s in venue.aspects.items() if s <= 0.35]
    bits: list[str] = []
    n_src = len(venue.independent_sources)
    if n_src >= 3:
        bits.append(f"recommended by {n_src} independent sources")
    elif n_src:
        bits.append(
            f"picked by {_join(sorted({config.sources.get(k).name for k in venue.independent_sources if k in {s.key for s in config.sources.sources}}))}"
        )
    if venue.michelin:
        bits.append(f"holds a Michelin {venue.michelin}")
    if venue.google_rating and venue.google_reviews:
        bits.append(
            f"rated {venue.google_rating:.1f} on Google over {venue.google_reviews:,} reviews"
        )
    if strengths:
        bits.append(f"reviewers praise the {_join(strengths)}")
    if weaknesses:
        bits.append(f"but complaints mention {_join(weaknesses)}")
    if venue.rating_trend == "declining":
        bits.append("recent ratings are slipping")
    elif venue.rating_trend == "improving":
        bits.append("recent ratings are climbing")
    second = ("It is " + _join(bits[:3]) + ".") if bits else ""
    if second.startswith("It is but"):
        second = second.replace("It is but", "Reviews do note", 1)
    return f"{first} {second}".strip()


def best_for_line(venue: Venue, config: AppConfig, ranks: dict[str, int]) -> str:
    """A short 'best for' phrase from the venue's strongest category and its flags."""
    if not ranks:
        return "not ranked yet"
    best_rank = min(ranks.values())
    # Prefer a category whose keywords match the venue's cuisine when it ranks nearly as well
    # (a zi char place ranked 1st for cafes and 2nd for zi char is still "best for" zi char).
    affine = [
        k
        for k, r in ranks.items()
        if r <= best_rank + 2 and _cuisine_affinity(venue, config.categories.get(k))
    ]
    best_key = min(affine, key=lambda k: ranks[k]) if affine else min(ranks, key=lambda k: ranks[k])
    cat: Category = config.categories.get(best_key)
    tags = set(venue.ambience_tags)
    quiet = venue.noise_level == "quiet" or "quiet" in tags
    if cat.group == "dating":
        if "rooftop" in tags or "waterfront" in tags or "view" in tags:
            return "a date with a view"
        if quiet:
            return (
                "a quiet anniversary dinner" if "restaurants" in best_key else "a quiet coffee date"
            )
        if "brunch" in best_key:
            return "a lazy weekend brunch date"
        if "hightea" in best_key:
            return "an afternoon tea date"
        return "a relaxed date night" if "restaurants" in best_key else "a casual cafe date"
    if cat.group == "family":
        big = venue.flags.get("good_for_groups") or venue.flags.get("private_room")
        kids = venue.flags.get("kid_friendly")
        if "private_room" in best_key or venue.flags.get("private_room"):
            return "a big family celebration with a private room"
        if "steamboat" in best_key:
            return "a long hotpot night with the whole family"
        if "dimsum" in best_key:
            return "a weekend dim sum brunch with grandparents"
        if "zichar" in best_key:
            return "a no-fuss zi char dinner with the family"
        if "hawker" in best_key:
            return "a cheap and cheerful family meal"
        if "occasion" in best_key:
            return "a birthday or reunion dinner" + (
                " with elderly relatives" if venue.flags.get("wheelchair") else ""
            )
        if big and kids:
            return "a big family lunch with kids in tow"
        if kids:
            return "a weekend meal with young kids"
        return "a family weekend meal"
    return f"trying a new {(primary_cuisine(venue) or 'place').lower()} this month"


def analyse_venue(
    venue: Venue, config: AppConfig, ranks: dict[str, int] | None = None, today: date | None = None
) -> None:
    """Fill the stage 3 fields on ``venue`` in place (summary needs ranks, so it may run twice)."""
    texts = _texts(venue)
    all_keywords = sorted({k for c in config.categories.categories for k in c.keywords})
    venue.keyword_counts = keyword_counts(texts, all_keywords)
    venue.aspects = aspect_scores(texts)
    venue.noise_level = noise_level(texts, venue.ambience_tags)
    venue.rating_trend = rating_trend(venue, today)
    venue.summary = summarise(venue, config)
    venue.best_for = best_for_line(venue, config, ranks or {})


def category_keyword_hits(venue: Venue, category: Category) -> int:
    return sum(v for k, v in venue.keyword_counts.items() if k in category.keywords)


def record_rating_history(venue: Venue, run_id: str, today: date | None = None) -> None:
    today = today or datetime.now(UTC).date()
    for src, r in venue.ratings.items():
        if r.get("rating") is None:
            continue
        entry: dict[str, Any] = {
            "run_id": run_id,
            "date": today.isoformat(),
            "source": src,
            "rating": r["rating"],
            "review_count": r.get("review_count"),
        }
        if not any(
            h.get("run_id") == run_id and h.get("source") == src for h in venue.rating_history
        ):
            venue.rating_history.append(entry)
