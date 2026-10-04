"""Promotions and festive offers for the places on your lists, found with Claude's web search.

Only deals the search actually returned, each with a source link, still running today. Used by the
weekly promo message and to add a 🎁 or 🎄 line to the special occasion list. Best effort: when the
AI layer is off or finds nothing, callers just skip it."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sgfoodhunt.ai.client import AiClient
from sgfoodhunt.config import AppConfig
from sgfoodhunt.diff import _link, _load_scores, _load_venues
from sgfoodhunt.http.cache import read_json, write_json
from sgfoodhunt.notify import TELEGRAM_MAX, esc, html_to_plain, notify
from sgfoodhunt.storage.runs import RunStore

log = logging.getLogger(__name__)
PROMO_EVERY_DAYS = 7
BATCH = 12  # venues per search call
MAX_VENUES = 24
STATE_FILE = "promos_state.json"
FESTIVE = (
    "christmas",
    "festive",
    "new year",
    "cny",
    "chinese new year",
    "deepavali",
    "hari raya",
    "mooncake",
)

SCHEMA = {
    "type": "object",
    "properties": {
        "promos": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "deal": {"type": "string"},
                    "ends": {"type": ["string", "null"]},
                    "url": {"type": "string"},
                },
                "required": ["name", "deal", "ends", "url"],
            },
        }
    },
    "required": ["promos"],
}
SYSTEM = (
    "You find current food and drink promotions in Singapore using web search. Report a promotion "
    "only if a search result you read states it for that exact venue (1-for-1, discount, set menu, "
    "festive or Christmas buffet early bird, new menu offer) and it is still running today or "
    "starts soon. Give a source URL for each. Never guess or invent. If a venue has no current "
    "promotion, leave it out. `ends` is the last valid day as YYYY-MM-DD, or null if unknown. "
    "`deal` is one short line with the price or discount. No dashes in text."
)


def _valid(item: Any, wanted: dict[str, str], today: date) -> dict[str, Any] | None:
    """Keep a promo only when it names a requested venue, has an https source and has not ended."""
    if not isinstance(item, dict):
        return None
    name = wanted.get(str(item.get("name", "")).strip().lower())
    url, deal = str(item.get("url") or ""), str(item.get("deal") or "").strip()
    if not (name and deal and url.startswith("https://")):
        return None
    ends = item.get("ends")
    try:
        if ends and date.fromisoformat(str(ends)[:10]) < today:
            return None
    except ValueError:
        ends = None
    return {
        "name": name,
        "deal": deal,
        "ends": str(ends)[:10] if ends else None,
        "url": url,
        "festive": any(w in deal.lower() for w in FESTIVE),
    }


def find_promos(
    ai: AiClient | None, names: list[str], today: date | None = None
) -> dict[str, dict[str, Any]]:
    """name -> promo for the venues that have one. One search call per BATCH venues, cached a day."""
    today = today or datetime.now(UTC).date()
    out: dict[str, dict[str, Any]] = {}
    if not ai or not ai.task_enabled("promo_search"):
        return out
    names = list(dict.fromkeys(names))[:MAX_VENUES]
    for i in range(0, len(names), BATCH):
        chunk = names[i : i + BATCH]
        prompt = (
            f"Today is {today.isoformat()}. Singapore venues:\n"
            + "\n".join(f"- {n}" for n in chunk)
            + "\nFind current promotions for these venues."
        )
        answer = ai.ask("promo_search", SYSTEM, prompt, SCHEMA, web=True, ttl_days=1)
        wanted = {n.lower(): n for n in chunk}
        for item in (answer or {}).get("promos", []):
            promo = _valid(item, wanted, today)
            if promo and promo["name"] not in out:
                out[promo["name"]] = promo
    return out


def promo_line(p: dict[str, Any]) -> str:
    until = f" · until {esc(p['ends'])}" if p.get("ends") else ""
    return f"{'🎄' if p.get('festive') else '🎁'} {esc(p['deal'])}{until} · {_link(p['url'], 'source')}"


def tracked_names(
    config: AppConfig, store: RunStore, run_id: str, per_category: int = 8
) -> list[str]:
    """Top venues of every enabled list, plus the special occasion list, best first, no repeats."""
    run_dir = store.run_dir(run_id)
    scores, venues = _load_scores(run_dir), _load_venues(run_dir)
    names: list[str] = []
    for key, scored in scores.items():
        if not config.categories.has(key):
            continue
        limit = 12 if config.categories.get(key).digest_only else per_category
        for s in [s for s in scored if s.get("rank") and not s.get("excluded_reason")][:limit]:
            if s["venue_id"] in venues:
                names.append(venues[s["venue_id"]]["name"])
    return list(dict.fromkeys(names))


def promo_message(promos: dict[str, dict[str, Any]]) -> str | None:
    if not promos:
        return None
    day = datetime.now(UTC).strftime("%a %d %b %Y")
    blocks = [f"🍽 <b>{esc(n)}</b>\n{promo_line(p)}" for n, p in promos.items()]
    note = (
        "<blockquote expandable>Found by web search for places on your lists. Each deal has its "
        "source link; check it before you go. Updated weekly.</blockquote>"
    )
    for n in range(len(blocks), 0, -1):  # one message: drop the tail until it fits
        text = (
            f"🎁 <b>PROMOS</b> · your lists · {day}\n\n" + "\n\n".join(blocks[:n]) + "\n\n" + note
        )
        if len(text) <= TELEGRAM_MAX:
            return text
    return None


def due(config: AppConfig, now: datetime | None = None) -> bool:
    path = config.resolve(config.settings.paths.data_dir) / STATE_FILE
    try:
        last = datetime.fromisoformat(read_json(path)["last_sent"])
    except Exception:
        return True
    return (now or datetime.now(UTC)) - last >= timedelta(days=PROMO_EVERY_DAYS)


def send_promos(
    config: AppConfig, store: RunStore, ai: AiClient | None, force: bool = False
) -> str:
    if not force and not due(config):
        return "not due yet"
    latest = store.latest_run(status=None)
    if latest is None:
        return "no runs yet"
    promos = find_promos(ai, tracked_names(config, store, latest.run_id))
    text = promo_message(promos)
    if not text:
        return "no current promos found" if ai else "AI layer off"
    result = notify(config, html_to_plain(text), subject="SG Food Hunt promos", messages=[text])
    if result.telegram == "sent" or result.email == "sent":
        write_json(
            config.resolve(config.settings.paths.data_dir) / STATE_FILE,
            {"last_sent": datetime.now(UTC).isoformat()},
        )
        return f"sent {len(promos)} promos"
    return f"not sent (telegram {result.telegram}, email {result.email})"
