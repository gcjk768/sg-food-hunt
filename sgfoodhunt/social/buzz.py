"""Buzz score: distinct social mentions in the last N months with recency decay."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sgfoodhunt.config import SocialSettings
from sgfoodhunt.social.models import SocialMention


@dataclass(slots=True)
class BuzzInfo:
    mentions_window: int = 0
    raw: float = 0.0
    score: float = 0.0  # 0..1, normalised across venues
    trending: bool = False
    latest: str | None = None


def buzz_scores(
    mentions: list[SocialMention], settings: SocialSettings, today: date | None = None
) -> dict[str, BuzzInfo]:
    today = today or datetime.now(UTC).date()
    window_start = today - timedelta(days=int(settings.buzz_window_months * 30.4))
    info: dict[str, BuzzInfo] = {}
    seen: set[tuple[str, str]] = set()
    for m in mentions:
        if not m.venue_id or not m.url:
            continue
        key = (m.venue_id, m.url)
        if key in seen:
            continue
        seen.add(key)
        when: date | None = None
        if m.post_date:
            try:
                when = date.fromisoformat(m.post_date[:10])
            except ValueError:
                when = None
        if when is None:
            weight, in_window = 0.25, True  # undated: counts a little
        else:
            if when < window_start:
                continue
            age = (today - when).days
            weight, in_window = 0.5 ** (age / settings.buzz_half_life_days), True
        b = info.setdefault(m.venue_id, BuzzInfo())
        if in_window:
            b.mentions_window += 1
            b.raw += weight
            if when and (b.latest is None or when.isoformat() > b.latest):
                b.latest = when.isoformat()
    max_raw = max((b.raw for b in info.values()), default=0.0)
    for b in info.values():
        b.score = b.raw / max_raw if max_raw > 0 else 0.0
        b.trending = b.mentions_window >= settings.trending_threshold
    return info
