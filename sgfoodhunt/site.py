"""The website: one ``data/site/data.json`` for the static page in ``site/``.

Built after every run (and by ``sgfh site``). The page is plain HTML/CSS/JS served by an nginx
container that mounts ``site/`` and ``data/site/`` (see docker-compose.yml, ``food-site``), the same
shape as the property site. Only venues that hold a rank in some list are included, grouped the way
people actually ask the question: cafes, zi char, hawker, dinner for four, special occasions, new.
"""

from __future__ import annotations

import http.server
import logging
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sgfoodhunt.config import AppConfig
from sgfoodhunt.diff import _load_scores, _load_venues, michelin_award, verdict
from sgfoodhunt.http.cache import read_json, write_json
from sgfoodhunt.occasion import is_buffet
from sgfoodhunt.promos import PROMOS_FILE
from sgfoodhunt.storage.runs import RunStore

log = logging.getLogger(__name__)
SITE_DIR = "site"  # under data_dir
DATA_FILE = "data.json"
MAX_SOURCES = 6
NEW_DAYS = 14  # a venue first seen this recently is new, unless the tool itself was new then

# How the lists are grouped on the page: (key, label, Phosphor icon, category keys). A category
# may sit in two groups (new cafes are cafes and new). Groups with no scored category are dropped.
GROUPS: list[tuple[str, str, str, list[str]]] = [
    (
        "cafes",
        "Cafes & brunch",
        "coffee",
        ["cafes_date", "brunch_date", "new_cafes", "hightea_dessert_date"],
    ),
    ("zichar", "Zi char", "cooking-pot", ["zichar_family", "new_zichar"]),
    ("hawker", "Hawker", "bowl-food", ["hawker_family"]),
    (
        "dinner",
        "Dinner for 4",
        "users-four",
        [
            "restaurants_4pax",
            "family_weekend",
            "dimsum_family",
            "steamboat_family",
            "private_room_family",
            "family_occasion",
        ],
    ),
    ("date", "Date night", "heart", ["restaurants_date", "rooftop_waterfront_date"]),
    ("occasion", "Special occasions", "confetti", ["occasion_4pax"]),
    ("new", "New openings", "sparkle", ["new_cafes", "new_zichar", "new_restaurants"]),
]


def _run_time(run_id: str) -> datetime | None:
    m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z", run_id or "")
    if not m:
        return None
    y, mo, d, h, mi, s = (int(g) for g in m.groups())
    return datetime(y, mo, d, h, mi, s, tzinfo=UTC)


def _sources(config: AppConfig, v: dict[str, Any]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out = []
    for e in v.get("evidence") or []:
        url = e.get("url")
        if not url or url in seen:
            continue
        seen.add(url)
        key = e.get("source_key") or ""
        label = config.sources.get(key).name if _has_source(config, key) else key
        out.append(
            {
                "source": label,
                "title": e.get("title") or label,
                "url": url,
                "date": e.get("published_at"),
            }
        )
    out.sort(key=lambda s: s.get("date") or "", reverse=True)
    return out[:MAX_SOURCES]


def _has_source(config: AppConfig, key: str) -> bool:
    try:
        config.sources.get(key)
    except KeyError:
        return False
    return True


def _venue_row(
    config: AppConfig,
    v: dict[str, Any],
    tops: list[tuple[int, str]],
    new: bool,
    promo: dict[str, Any] | None,
) -> dict[str, Any]:
    g = (v.get("ratings") or {}).get("google_places") or {}
    worth, why = verdict(v, tops)
    per_pax = config.settings.scoring.price_per_pax_sgd.get(v.get("price_level") or 0)
    flags = v.get("flags") or {}
    return {
        "id": v["id"],
        "name": v["name"].strip(" .·-"),  # a scraper sometimes keeps a list bullet
        "name_zh": v.get("name_zh"),
        "address": v.get("address"),
        "postal_code": v.get("postal_code"),
        "district": v.get("district"),
        "region": v.get("region"),
        "lat": v.get("lat"),
        "lng": v.get("lng"),
        "mrt": v.get("nearest_mrt"),
        "mrt_lines": v.get("mrt_lines") or [],
        "cuisine": v.get("cuisine") or [],
        "price_level": v.get("price_level"),
        "price_text": (v.get("price_text") or "").strip(" ·|") or None,
        "per_pax": per_pax,
        "rating": g.get("rating"),
        "reviews": g.get("review_count"),
        "michelin": michelin_award(v),
        "hygiene": v.get("hygiene_grade"),
        "status": v.get("business_status"),
        "halal": bool(flags.get("halal")),
        "kids": bool(flags.get("kid_friendly")),
        "private_room": bool(flags.get("private_room")),
        "buffet": is_buffet(v),
        "tags": v.get("ambience_tags") or [],
        "hours": v.get("hours"),
        "open_weekends": v.get("open_weekends"),
        "booking_url": v.get("booking_url"),
        "website": v.get("website"),
        "phone": v.get("phone"),
        "summary": v.get("summary"),
        "best_for": v.get("best_for"),
        "dishes": v.get("ai_dishes") or [],
        "aspects": v.get("aspects") or {},
        "noise": v.get("noise_level"),
        "trend": v.get("rating_trend"),
        "first_seen": v.get("first_seen_run"),
        "new": new,
        "worth": worth,
        "why": why,
        "sources": _sources(config, v),
        "promo": promo,
    }


def build_site_data(
    config: AppConfig, store: RunStore, run_id: str | None = None
) -> dict[str, Any]:
    """Everything the page needs, for the latest scored run (or ``run_id``)."""
    if run_id is None:
        runs = [r for r in store.list_runs() if (store.run_dir(r.run_id) / "scores.json").exists()]
        if not runs:
            raise FileNotFoundError("no scored run yet")
        run_id = runs[-1].run_id
    run_dir = store.run_dir(run_id)
    scores, venues = _load_scores(run_dir), _load_venues(run_dir)
    record = store.load_run(run_id)
    labels = {c.key: c for c in config.categories.categories}
    data_dir = config.resolve(config.settings.paths.data_dir)
    promos_raw = read_json(data_dir / PROMOS_FILE) if (data_dir / PROMOS_FILE).exists() else {}
    promos: dict[str, dict[str, Any]] = promos_raw.get("promos") or {}
    run_at = _run_time(run_id)
    all_runs = store.list_runs()
    bootstrap = all_runs[0].run_id if all_runs else None  # first run saw everything "new"

    categories: dict[str, Any] = {}
    tops: dict[str, list[tuple[int, str]]] = {}
    new_ids: set[str] = set()
    for key, scored in scores.items():
        cat = labels.get(key)
        if cat is None:
            continue  # switched off since this run was scored
        ranked = []
        for s in scored:
            if (
                not s.get("rank")
                or s.get("excluded_reason")
                or s.get("hidden")
                or s["venue_id"] not in venues
            ):
                continue
            ranked.append(
                {
                    "id": s["venue_id"],
                    "rank": s["rank"],
                    "score": round(s["score"], 3),
                    "components": s.get("components") or {},
                    "adjustments": s.get("adjustments") or {},
                }
            )
            tops.setdefault(s["venue_id"], []).append((s["rank"], cat.display_name))
            if key.startswith("new_"):
                new_ids.add(s["venue_id"])
        ranked.sort(key=lambda r: r["rank"])
        categories[key] = {
            "key": key,
            "label": cat.display_name,
            "group": cat.group,
            "party_size": cat.party_size,
            "new": key.startswith("new_"),
            "digest_only": cat.digest_only,
            "ranked": ranked,
        }

    groups = []
    for gkey, glabel, icon, keys in GROUPS:
        present = [k for k in keys if k in categories]
        if present:
            groups.append({"key": gkey, "label": glabel, "icon": icon, "categories": present})

    out_venues: dict[str, Any] = {}
    for vid, pairs in tops.items():
        v = venues[vid]
        seen = _run_time(v.get("first_seen_run") or "")
        recent = bool(run_at and seen and run_at - seen <= timedelta(days=NEW_DAYS))
        new = vid in new_ids or (recent and v.get("first_seen_run") != bootstrap)
        out_venues[vid] = _venue_row(config, v, pairs, new, promos.get(v["name"]))

    diff_path = run_dir / "diff.json"
    diff = read_json(diff_path) if diff_path.exists() else {}
    entered = [
        {"category": c["key"], "label": c["display_name"], "id": e["venue_id"], "rank": e["rank"]}
        for c in diff.get("categories") or []
        for e in c.get("entered") or []
        if e["venue_id"] in out_venues and c["key"] in categories
    ]
    return {
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "run_id": run_id,
        "run_finished_at": record.finished_at,
        "prev_run_id": diff.get("prev_run_id"),
        "groups": groups,
        "categories": categories,
        "venues": out_venues,
        "changes": {
            "entered": entered,
            "new_venues": [
                n["venue_id"] for n in diff.get("new_venues") or [] if n["venue_id"] in out_venues
            ],
        },
        "promos": {"found_at": promos_raw.get("found_at"), "count": len(promos)},
        "sources": sorted({s["source"] for v in out_venues.values() for s in v["sources"]}),
    }


def build_site(config: AppConfig, store: RunStore, run_id: str | None = None) -> Path:
    """Write ``data/site/data.json`` and return its path."""
    data = build_site_data(config, store, run_id)
    path = config.resolve(config.settings.paths.data_dir) / SITE_DIR / DATA_FILE
    write_json(path, data)
    log.info(
        "site data: %d venues in %d lists -> %s", len(data["venues"]), len(data["categories"]), path
    )
    return path


def serve_site(site_dir: Path, data_dir: Path, port: int) -> None:
    """Local preview with the standard library: ``site/`` at ``/`` and ``data/site/`` at ``/live/``,
    the same paths nginx serves on the NAS."""
    live = data_dir / SITE_DIR

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, directory=str(site_dir), **kwargs)

        def translate_path(self, path: str) -> str:
            if path.startswith("/live/"):
                return str(live / path[len("/live/") :].split("?")[0])
            return super().translate_path(path)

        def end_headers(self) -> None:
            self.send_header("Cache-Control", "no-cache")
            super().end_headers()

        def log_message(self, format: str, *args: Any) -> None:
            log.debug(format, *args)

    with http.server.ThreadingHTTPServer(("0.0.0.0", port), Handler) as httpd:
        print(f"SG Food Hunt site: http://localhost:{port}/  (Ctrl+C to stop)")
        httpd.serve_forever()
