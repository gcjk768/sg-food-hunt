"""Venue and category notes in the Obsidian vault, plus the personal layer read back from them."""

from __future__ import annotations

import logging
from typing import Any

from sgfoodhunt.config import AppConfig, Category
from sgfoodhunt.dedup.venue import Venue
from sgfoodhunt.reporting.memory import (
    history_entry,
    history_lines,
    log_activity,
    rank_events,
    with_history,
)
from sgfoodhunt.scoring import PersonalNote, ScoredVenue
from sgfoodhunt.storage.frontmatter import Note
from sgfoodhunt.storage.vault import CATEGORIES, VENUES, Vault

log = logging.getLogger(__name__)


def venue_note_name(venue: Venue) -> str:
    return venue.name if not venue.outlet else f"{venue.brand} ({venue.outlet})"


def read_personal_notes(vault: Vault, venues: list[Venue]) -> dict[str, PersonalNote]:
    """Collect status / my_rating from existing venue notes (the personal layer)."""
    notes: dict[str, PersonalNote] = {}
    for v in venues:
        note = vault.read(VENUES, venue_note_name(v))
        if note is None:
            continue
        fm = note.frontmatter
        status = fm.get("status")
        rating = fm.get("my_rating")
        if fm.get("excluded") is True:
            status = "excluded"
        try:
            my_rating = float(rating) if rating not in (None, "") else None
        except (TypeError, ValueError):
            my_rating = None
        if status or my_rating is not None:
            notes[v.id] = PersonalNote(status=str(status) if status else None, my_rating=my_rating)
    return notes


def _md_table(headers: list[str], rows: list[list[Any]]) -> str:
    def cell(v: Any) -> str:
        return str(v if v is not None else "").replace("|", "\\|").replace("\n", " ")

    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(" --- " for _ in headers) + "|"]
    lines.extend("| " + " | ".join(cell(v) for v in row) + " |" for row in rows)
    return "\n".join(lines)


def _hours_lines(venue: Venue) -> str:
    if not venue.hours:
        return "_unknown_"
    days = ("mon", "tue", "wed", "thu", "fri", "sat", "sun", "ph")
    out = []
    for d in days:
        if d not in venue.hours:
            continue
        ranges = venue.hours[d]
        out.append(
            f"- {d.title()}: " + (", ".join(f"{a}-{b}" for a, b in ranges) if ranges else "closed")
        )
    return "\n".join(out)


def _bill_estimates(venue: Venue, config: AppConfig, category_keys: list[str]) -> dict[str, float]:
    per_pax = config.settings.scoring.price_per_pax_sgd.get(venue.price_level or 0)
    if not per_pax:
        return {}
    return {
        k: per_pax * config.categories.get(k).party_size
        for k in category_keys
        if k in {c.key for c in config.categories.categories}
    }


def build_venue_note(
    venue: Venue,
    config: AppConfig,
    vault: Vault,
    scores: dict[str, ScoredVenue],
    run_id: str,
) -> Note:
    per_pax = config.settings.scoring.price_per_pax_sgd.get(venue.price_level or 0)
    ranked = {k: s for k, s in scores.items() if not s.excluded_reason}
    fm: dict[str, Any] = {
        "type": "venue",
        "venue_id": venue.id,
        "name": venue.name,
        "name_zh": venue.name_zh,
        "brand": venue.brand,
        "outlet": venue.outlet,
        "aliases": venue.aliases or None,
        "address": venue.address,
        "postal_code": venue.postal_code,
        "district": venue.district,
        "region": venue.region,
        "lat": venue.lat,
        "lng": venue.lng,
        "location": f"{venue.lat},{venue.lng}" if venue.lat and venue.lng else None,
        "nearest_mrt": venue.nearest_mrt,
        "mrt_lines": venue.mrt_lines or None,
        "cuisine": venue.cuisine or None,
        "price_level": "$" * venue.price_level if venue.price_level else None,
        "price_per_pax_sgd": per_pax,
        "bill_estimate": _bill_estimates(venue, config, list(ranked)) or None,
        "open_weekends": venue.open_weekends,
        "late_night": venue.late_night,
        "ph_closed": venue.ph_closed,
        "booking_url": venue.booking_url,
        "website": venue.website,
        "phone": venue.phone,
        "walk_in_only": (not venue.booking_url and venue.flags.get("reservable") is False) or None,
        "halal": venue.flags.get("halal"),
        "vegetarian_options": venue.flags.get("vegetarian_options"),
        "kid_friendly": venue.flags.get("kid_friendly"),
        "pet_friendly": venue.flags.get("pet_friendly"),
        "private_room": venue.flags.get("private_room"),
        "outdoor_seating": venue.flags.get("outdoor_seating"),
        "wheelchair": venue.flags.get("wheelchair"),
        "large_group": venue.flags.get("good_for_groups"),
        "ambience": venue.ambience_tags or None,
        "google_rating": venue.google_rating,
        "google_reviews": venue.google_reviews,
        "ratings": {
            k: v.get("rating")
            for k, v in venue.ratings.items()
            if k != "google_places" and v.get("rating") is not None
        }
        or None,
        "hygiene_grade": venue.hygiene_grade,
        "michelin": venue.michelin,
        "rating_trend": venue.rating_trend,
        "aspect_food": venue.aspects.get("food"),
        "aspect_service": venue.aspects.get("service"),
        "aspect_ambience": venue.aspects.get("ambience"),
        "aspect_value": venue.aspects.get("value"),
        "noise_level": venue.noise_level,
        "keyword_counts": venue.keyword_counts or None,
        "best_for": venue.best_for,
        "buzz_score": venue.buzz_score or None,
        "trending_social": venue.trending_social or None,
        "social_mentions_6m": venue.social_mentions_window or None,
        "social_secondhand": venue.social_secondhand or None,
        "business_status": venue.business_status,
        "source_count": len(venue.source_keys),
        "independent_sources": len(venue.independent_sources),
        "sources": venue.source_keys,
        "scores": {k: round(s.score, 3) for k, s in ranked.items()} or None,
        "ranks": {k: s.rank for k, s in ranked.items() if s.rank} or None,
        "categories": sorted(ranked) or None,
        "best_rank": min((s.rank for s in ranked.values() if s.rank), default=None),
        "earliest_evidence": venue.earliest_evidence,
        "first_seen": venue.first_seen_run,
        "last_seen": venue.last_seen_run,
        "updated_run": run_id,
        "status": None,  # user owned: visited | wishlist | excluded
        "my_rating": None,  # user owned
        "my_comment": None,  # user owned
        "tags": ["sgfoodhunt/venue"],
    }
    cat_lines = []
    for key, s in sorted(ranked.items(), key=lambda kv: kv[1].rank or 999):
        cat = config.categories.get(key)
        link = vault.link_target(CATEGORIES, cat.display_name)
        cat_lines.append(
            f"- [[{link}|{cat.display_name}]]: rank {s.rank or 'hidden'}, score {s.score:.2f}"
        )
    excluded_lines = [
        f"- {config.categories.get(k).display_name}: {s.excluded_reason}"
        for k, s in scores.items()
        if s.excluded_reason
    ]
    evidence_rows = []
    for ev in sorted(
        venue.evidence, key=lambda e: (e.published_at or "", e.source_key), reverse=True
    ):
        src = (
            config.sources.get(ev.source_key).name
            if ev.source_key in {s.key for s in config.sources.sources}
            else ev.source_key
        )
        title = ev.title or (ev.url or "")[:60]
        evidence_rows.append(
            [
                src,
                f"[{title}]({ev.url})" if ev.url else title,
                ev.published_at or "",
                ", ".join(ev.category_keys),
            ]
        )
    mrt_line = (
        f"Nearest MRT: **{venue.nearest_mrt}** ({', '.join(venue.mrt_lines)})."
        if venue.nearest_mrt
        else "_no coordinates yet_"
    )
    dishes = (
        "\n".join(f"- {d}" for d in venue.ai_dishes) if venue.ai_dishes else "_none identified yet_"
    )
    social_lines = [
        f"- {m.get('date') or 'undated'} · {m.get('platform')} · {('@' + m['creator']) if m.get('creator') else m.get('source')} · [post]({m['url']})"
        for m in venue.recent_social
        if m.get("url")
    ]
    social_block = "\n".join(social_lines) if social_lines else "_none_"
    if venue.social_secondhand:
        social_block += f"\n\nReviews and articles mention TikTok / Instagram / viral {venue.social_secondhand} time(s)."
    excluded_block = (
        ("\n**Excluded from:**\n" + "\n".join(excluded_lines)) if excluded_lines else ""
    )
    review_rows = [
        [
            r.get("rating") or "",
            r.get("published_at", "")[:10] if r.get("published_at") else "",
            r["snippet"],
        ]
        for r in venue.review_snippets
    ]
    body = f"""# {venue.name}{f" ({venue.name_zh})" if venue.name_zh else ""}

{venue.address or "_address unknown_"}{f" · {venue.region}" if venue.region else ""}{f" · {'$' * venue.price_level}" if venue.price_level else ""}{f" · {venue.michelin}" if venue.michelin else ""}

{("[Book](" + venue.booking_url + ")") if venue.booking_url else ""}{(" · [Website](" + venue.website + ")") if venue.website else ""}{(" · " + venue.phone) if venue.phone else ""}

## Summary

{venue.summary or "_no data yet_"}

## Best for

{venue.best_for or "_not ranked yet_"}

## Signature dishes

{dishes}

## Getting there

{mrt_line}

## Rankings

{chr(10).join(cat_lines) if cat_lines else "_not ranked in any category_"}
{excluded_block}

## Opening hours

{_hours_lines(venue)}

## Evidence

{_md_table(["Source", "Page", "Date", "Categories"], evidence_rows) if evidence_rows else "_none_"}

## Recent social mentions

{social_block}

## Review snippets

{_md_table(["Rating", "Date", "Snippet"], review_rows) if review_rows else "_none stored_"}

## My notes

"""
    return Note(frontmatter=fm, body=body)


def write_venue_notes(
    vault: Vault,
    config: AppConfig,
    venues: list[Venue],
    scores_by_category: dict[str, list[ScoredVenue]],
    run_id: str,
) -> int:
    by_venue: dict[str, dict[str, ScoredVenue]] = {}
    for cat_key, scored in scores_by_category.items():
        for s in scored:
            by_venue.setdefault(s.venue_id, {})[cat_key] = s
    top_n = config.settings.scoring.top_n
    labels = {c.key: c.display_name for c in config.categories.categories}
    count = 0
    for venue in venues:
        name = venue_note_name(venue)
        note = build_venue_note(venue, config, vault, by_venue.get(venue.id, {}), run_id)
        try:
            existing = vault.read(VENUES, name)
            old_ranks = (
                _top_ranks(existing.frontmatter.get("ranks"), top_n, scores_by_category)
                if existing
                else {}
            )
            new_ranks = _top_ranks(note.frontmatter.get("ranks"), top_n, scores_by_category)
            events = rank_events(old_ranks, new_ranks, labels, top_n)
            entries = [] if existing else [history_entry("🆕", "first seen", f"run {run_id}")]
            entries += [history_entry(e, what, detail) for e, what, detail in events]
            note.body = with_history(note.body, history_lines(existing) + entries)
            vault.write(VENUES, name, note)
        except Exception as exc:  # one bad note must not sink the run (vault is best-effort)
            log.warning("venue note %s not written: %s", name, exc)
            continue
        link = f"{vault.link_target(VENUES, name)}|{venue.name}"
        for e, what, detail in events:
            log_activity(vault, e, what, f"{venue.name} · {detail}", link)
        count += 1
    return count


def _top_ranks(ranks: Any, top_n: int, scope: dict[str, Any]) -> dict[str, int]:
    """Top-list ranks of the categories this run scored (a ``-c`` run leaves the rest alone)."""
    if not isinstance(ranks, dict):
        return {}
    return {k: int(r) for k, r in ranks.items() if k in scope and isinstance(r, int) and r <= top_n}


def build_category_note(
    category: Category,
    scored: list[ScoredVenue],
    venues: dict[str, Venue],
    config: AppConfig,
    vault: Vault,
    run_id: str,
) -> Note:
    top_n = config.settings.scoring.top_n
    visible = [s for s in scored if not s.excluded_reason and not s.hidden]
    hidden = [s for s in scored if s.hidden]
    excluded = [s for s in scored if s.excluded_reason]

    def row(s: ScoredVenue) -> list[Any]:
        v = venues[s.venue_id]
        link = f"[[{vault.link_target(VENUES, venue_note_name(v))}|{v.name}]]"
        bill = config.settings.scoring.price_per_pax_sgd.get(v.price_level or 0)
        return [
            s.rank,
            link,
            v.region or "",
            v.nearest_mrt or "",
            "$" * v.price_level if v.price_level else "",
            f"{v.google_rating:.1f} ({v.google_reviews or 0})" if v.google_rating else "",
            len(v.independent_sources),
            v.michelin or "",
            f"~${bill * category.party_size:.0f}" if bill else "",
            f"[book]({v.booking_url})" if v.booking_url else "",
            f"{s.score:.2f}",
            v.best_for or "",
        ]

    headers = [
        "#",
        "Venue",
        "Region",
        "MRT",
        "Price",
        "Google",
        "Sources",
        "Michelin",
        f"Bill ({category.party_size} pax)",
        "Booking",
        "Score",
        "Best for",
    ]
    top_rows = [row(s) for s in visible[:top_n]]
    rest_rows = [row(s) for s in visible[top_n:]]
    weights = ", ".join(
        f"{k} {w:.2f}"
        for k, w in sorted(category.normalised_weights().items(), key=lambda kv: -kv[1])
        if w > 0
    )
    body = f"""# {category.display_name}

Party of {category.party_size}. Weights: {weights}. Run [[{vault.link_target("Runs", run_id)}|{run_id}]].

## Top {top_n}

{_md_table(headers, top_rows) if top_rows else "_no venues ranked yet_"}

## Full ranking

{_md_table(headers, rest_rows) if rest_rows else "_nothing beyond the top list_"}

## Hidden (visited)

{chr(10).join(f"- [[{vault.link_target(VENUES, venue_note_name(venues[s.venue_id]))}|{s.name}]] ({s.score:.2f})" for s in hidden) if hidden else "_none_"}

## Excluded

{chr(10).join(f"- {s.name}: {s.excluded_reason}" for s in excluded) if excluded else "_none_"}

## My notes

"""
    fm = {
        "type": "category",
        "category_key": category.key,
        "display_name": category.display_name,
        "group": category.group,
        "party_size": category.party_size,
        "run_id": run_id,
        "ranked": len(visible),
        "excluded": len(excluded),
        "tags": ["sgfoodhunt/category"],
    }
    return Note(frontmatter=fm, body=body)


def write_category_notes(
    vault: Vault,
    config: AppConfig,
    scores_by_category: dict[str, list[ScoredVenue]],
    venues: dict[str, Venue],
    run_id: str,
) -> None:
    for key, scored in scores_by_category.items():
        cat = config.categories.get(key)
        vault.write(
            CATEGORIES,
            cat.display_name,
            build_category_note(cat, scored, venues, config, vault, run_id),
        )
