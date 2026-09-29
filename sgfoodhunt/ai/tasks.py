"""The four AI tasks. Prompts only ever receive public page text, anonymised review snippets and
social captions; never reviewer names, handles of commenters, or anything from your exports
beyond the caption you already saved."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sgfoodhunt.ai.client import AiClient

SYSTEM_EXTRACT = (
    "You extract dining venues (restaurants, cafes, hawker stalls, bars, dessert places) in "
    "Singapore from text. Return only real venue names as written, never people, dishes, areas, "
    "or MRT stations on their own. Keep Chinese names when given. If nothing is a venue, return "
    "an empty list."
)
SYSTEM_REVIEWS = (
    "You analyse short anonymised review and article snippets about one Singapore dining venue. "
    "Score aspects from 0 (very negative) to 1 (very positive), 0.5 when neutral or unmentioned. "
    "Write the summary in your own words, one or two plain sentences, and never quote or copy "
    "the snippets. Do not mention reviewers."
)
SYSTEM_MATCH = (
    "You match a social media caption to the dining venue it is about, choosing only from the "
    "candidate list. Answer with the candidate id, or 'none' when no candidate clearly matches."
)

VENUES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "venues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "name_zh": {"type": "string"},
                    "area": {"type": "string"},
                    "address": {"type": "string"},
                    "hours": {"type": "string"},
                    "price": {"type": "string"},
                    "note": {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "required": ["name"],
            },
        }
    },
    "required": ["venues"],
}
REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "food": {"type": "number"},
        "service": {"type": "number"},
        "ambience": {"type": "number"},
        "value": {"type": "number"},
        "noise_level": {"type": "string", "enum": ["quiet", "moderate", "loud", "unknown"]},
        "summary": {"type": "string"},
        "signature_dishes": {"type": "array", "items": {"type": "string"}},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["food", "service", "ambience", "value", "noise_level", "summary"],
}
MATCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"venue_id": {"type": "string"}, "confidence": {"type": "number"}},
    "required": ["venue_id"],
}
MAX_TEXT = 12000


@dataclass(slots=True)
class ExtractedVenue:
    name: str
    name_zh: str | None = None
    area: str | None = None
    address: str | None = None
    hours: str | None = None
    price: str | None = None
    note: str | None = None
    confidence: float = 0.8


@dataclass(slots=True)
class ReviewAnalysis:
    aspects: dict[str, float]
    noise_level: str | None
    summary: str
    signature_dishes: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


def _clip(text: str, limit: int = MAX_TEXT) -> str:
    return text if len(text) <= limit else text[:limit] + " …"


def _venues(payload: dict[str, Any] | None, default_conf: float) -> list[ExtractedVenue]:
    out: list[ExtractedVenue] = []
    for item in (payload or {}).get("venues") or []:
        if not isinstance(item, dict) or not str(item.get("name", "")).strip():
            continue
        conf = item.get("confidence")
        out.append(
            ExtractedVenue(
                name=str(item["name"]).strip(),
                name_zh=(str(item["name_zh"]).strip() or None) if item.get("name_zh") else None,
                area=item.get("area") or None,
                address=item.get("address") or None,
                hours=item.get("hours") or None,
                price=item.get("price") or None,
                note=item.get("note") or None,
                confidence=float(conf) if isinstance(conf, (int, float)) else default_conf,
            )
        )
    return out


def extract_venues(ai: AiClient, text: str, context: str = "") -> list[ExtractedVenue] | None:
    """Venue names recommended in community text (Reddit). None when the AI did not answer."""
    prompt = f"{context}\n\nText:\n{_clip(text)}".strip()
    payload = ai.ask("reddit_extraction", SYSTEM_EXTRACT, prompt, VENUES_SCHEMA)
    return None if payload is None else _venues(payload, 0.8)


def extract_listicle(ai: AiClient, title: str | None, text: str) -> list[ExtractedVenue] | None:
    """Venue entries (with address, hours, price when present) from an article's text."""
    prompt = (
        f"Article title: {title or 'unknown'}\n"
        "List every venue the article recommends, with its address, opening hours and price "
        "level if the article states them.\n\n"
        f"Article text:\n{_clip(text)}"
    )
    payload = ai.ask("article_extraction", SYSTEM_EXTRACT, prompt, VENUES_SCHEMA)
    return None if payload is None else _venues(payload, 0.9)


def analyse_reviews(ai: AiClient, venue_name: str, texts: list[str]) -> ReviewAnalysis | None:
    snippets = "\n".join(f"- {t.strip()}" for t in texts if t.strip())
    if not snippets:
        return None
    prompt = f"Venue: {venue_name}\n\nSnippets:\n{_clip(snippets)}"
    payload = ai.ask("review_analysis", SYSTEM_REVIEWS, prompt, REVIEW_SCHEMA)
    if payload is None:
        return None
    aspects: dict[str, float] = {}
    for k in ("food", "service", "ambience", "value"):
        v = payload.get(k)
        if isinstance(v, (int, float)):
            aspects[k] = max(0.0, min(1.0, float(v)))
    noise = payload.get("noise_level")
    summary = str(payload.get("summary") or "").strip()
    if not summary:
        return None
    return ReviewAnalysis(
        aspects=aspects,
        noise_level=noise if noise in ("quiet", "moderate", "loud") else None,
        summary=summary,
        signature_dishes=[str(d) for d in payload.get("signature_dishes") or []][:6],
        tags=[str(t).lower() for t in payload.get("tags") or []][:8],
    )


def match_caption(
    ai: AiClient, caption: str, candidates: list[tuple[str, str]]
) -> tuple[str, float] | None:
    """Pick the venue a caption is about from ``[(venue_id, name), ...]``; None for no match."""
    if not caption.strip() or not candidates:
        return None
    listing = "\n".join(f"- {vid}: {name}" for vid, name in candidates)
    prompt = f"Caption:\n{_clip(caption, 2000)}\n\nCandidates:\n{listing}\n\nAnswer with one candidate id or 'none'."
    payload = ai.ask("social_matching", SYSTEM_MATCH, prompt, MATCH_SCHEMA)
    if payload is None:
        return None
    vid = str(payload.get("venue_id") or "").strip()
    if vid.lower() == "none" or vid not in {c[0] for c in candidates}:
        return None
    conf = payload.get("confidence")
    return vid, float(conf) if isinstance(conf, (int, float)) else 0.8
