"""Special occasion list: restaurants whose estimated bill for 4 is over S$200, as ONE Telegram
message to consider for a birthday or other celebration. Michelin places show their stars, good
picks say Worth, buffets say Buffet. Sent every ``OCCASION_EVERY_DAYS`` days, not on every run."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sgfoodhunt.config import AppConfig
from sgfoodhunt.diff import _link, _load_scores, _load_venues, michelin_award, verdict
from sgfoodhunt.http.cache import read_json, write_json
from sgfoodhunt.notify import TELEGRAM_MAX, esc, html_to_plain, notify
from sgfoodhunt.storage.runs import RunStore

log = logging.getLogger(__name__)
CATEGORY = "occasion_4pax"
OCCASION_EVERY_DAYS = 14
MAX_VENUES = 20
STATE_FILE = "occasion_state.json"


def is_buffet(v: Any) -> bool:
    """True when the name, cuisine or summary says buffet (works on a Venue or its dict)."""
    get = v.get if isinstance(v, dict) else lambda k, d=None: getattr(v, k, d)
    text = " ".join(
        [
            str(get("name", "") or ""),
            " ".join(get("cuisine", None) or []),
            str(get("summary", "") or ""),
        ]
    )
    return "buffet" in text.lower()


def star_count(michelin: str | None) -> int:
    """1 to 3 for 'N Star(s)' awards, 0 for Bib Gourmand, Selected, Green Star or none."""
    m = str(michelin or "")
    if m.endswith(("Star", "Stars")) and m != "Green Star" and m[0].isdigit():
        return int(m[0])
    return 1 if m == "Star" else 0


def _entry(config: AppConfig, v: dict[str, Any], rank: int) -> str:
    award = michelin_award(v)
    stars = star_count(v.get("michelin"))
    name = esc(v["name"] + (f" ({v['name_zh']})" if v.get("name_zh") else ""))
    if stars:
        head = (
            f"{'⭐' * stars} <b>{name}</b> · <b>Michelin</b> {stars} Star{'s' if stars > 1 else ''}"
        )
    elif award:
        head = f"🏅 <b>{name}</b> · <b>Michelin</b> {esc(award)}"
    else:
        head = f"🍽 <b>{name}</b>"
    if verdict(v, [(rank, "")])[0]:
        head += " ✅ <b>Worth</b>"
    if is_buffet(v):
        head += " 🍴 <b>Buffet</b>"
    per_pax = config.settings.scoring.price_per_pax_sgd.get(v.get("price_level") or 0)
    bits = [
        f"📍 <code>{esc(v.get('address') or v.get('district') or v.get('region'))}</code>"
        if (v.get("address") or v.get("district") or v.get("region"))
        else "",
        f"💰 about S${per_pax * 4:,.0f} for 4" if per_pax else "",
        _link(v["booking_url"], "Book") if v.get("booking_url") else "",
    ]
    return head + "\n" + "  ·  ".join(b for b in bits if b) if any(bits) else head


def occasion_message(config: AppConfig, store: RunStore, run_id: str | None = None) -> str | None:
    """One message for the latest scored run, or None when the category has no venues yet."""
    if not config.categories.has(CATEGORY):
        return None
    run_id = run_id or (
        store.latest_run(status=None).run_id if store.latest_run(status=None) else None
    )
    if not run_id:
        return None
    run_dir = store.run_dir(run_id)
    scored = [
        s
        for s in _load_scores(run_dir).get(CATEGORY, [])
        if s.get("rank") and not s.get("excluded_reason") and not s.get("hidden")
    ]
    venues = _load_venues(run_dir)
    picks = [(s["rank"], venues[s["venue_id"]]) for s in scored if s["venue_id"] in venues]
    # Michelin stars first (3 to 1), then the ranker's order
    picks.sort(key=lambda p: (-star_count(p[1].get("michelin")), p[0]))
    day = datetime.now(UTC).strftime("%a %d %b %Y")
    note = (
        "<blockquote expandable>Estimated bill for 4 is over S$200 (price level 3 or 4, or a "
        "Michelin star or buffet with no price yet). ✅ Worth = strong rating, top 10 place, "
        "two or more sources or a Michelin award. Check prices and book ahead for birthdays. "
        f"Updated every {OCCASION_EVERY_DAYS // 7} weeks.</blockquote>"
    )
    for n in range(min(MAX_VENUES, len(picks)), 0, -1):  # one message: drop the tail until it fits
        body = "\n\n".join(_entry(config, v, r) for r, v in picks[:n])
        head = f"🎉 <b>SPECIAL OCCASIONS</b> · S$200+ for 4 · {day}"
        text = f"{head}\n\n{body}\n\n{note}" if body else ""
        if len(text) <= TELEGRAM_MAX:
            return text or None
    return None


def due(config: AppConfig, now: datetime | None = None) -> bool:
    path = config.resolve(config.settings.paths.data_dir) / STATE_FILE
    try:
        last = datetime.fromisoformat(read_json(path)["last_sent"])
    except Exception:
        return True
    return (now or datetime.now(UTC)) - last >= timedelta(days=OCCASION_EVERY_DAYS)


def send_occasion(config: AppConfig, store: RunStore, force: bool = False) -> str:
    """Send the list when due (or forced). Returns what happened, for the console."""
    if not force and not due(config):
        return "not due yet"
    text = occasion_message(config, store)
    if not text:
        return "no special occasion venues yet"
    result = notify(
        config, html_to_plain(text), subject="SG Food Hunt special occasions", messages=[text]
    )
    if result.telegram == "sent" or result.email == "sent":
        path = config.resolve(config.settings.paths.data_dir) / STATE_FILE
        write_json(path, {"last_sent": datetime.now(UTC).isoformat()})
        return "sent"
    return f"not sent (telegram {result.telegram}, email {result.email})"
