"""CSV and JSON exports of run data (stage one: raw candidates)."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from sgfoodhunt.storage.runs import RunStore

RAW_COLUMNS = [
    "source_key",
    "category_key",
    "query",
    "name",
    "name_zh",
    "address",
    "postal_code",
    "lat",
    "lng",
    "phone",
    "website",
    "booking_url",
    "rating",
    "review_count",
    "price_level",
    "price_text",
    "cuisine",
    "business_status",
    "michelin",
    "hygiene_grade",
    "confidence",
    "page_url",
    "source_ref",
    "captured_at",
]


def export_raw_candidates(store: RunStore, run_id: str, exports_dir: Path) -> tuple[Path, Path]:
    out_dir = exports_dir / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "raw_candidates.csv"
    json_path = out_dir / "raw_candidates.json"
    rows = list(store.iter_candidates(run_id))
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=RAW_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            flat = dict(row)
            if isinstance(flat.get("cuisine"), list):
                flat["cuisine"] = "; ".join(flat["cuisine"])
            writer.writerow(flat)
    with json_path.open("w", encoding="utf-8") as fh:
        json.dump(rows, fh, ensure_ascii=False, indent=1)
    return csv_path, json_path


# --- stage 2: ranked outputs -------------------------------------------------------------------
CATEGORY_COLUMNS = [
    "rank",
    "score",
    "venue_id",
    "name",
    "name_zh",
    "brand",
    "outlet",
    "address",
    "postal_code",
    "district",
    "region",
    "lat",
    "lng",
    "cuisine",
    "price_level",
    "bill_estimate_sgd",
    "google_rating",
    "google_reviews",
    "independent_sources",
    "sources",
    "michelin",
    "hygiene_grade",
    "open_weekends",
    "booking_url",
    "website",
    "phone",
    "kid_friendly",
    "private_room",
    "halal",
    "earliest_evidence",
    "nearest_mrt",
    "mrt_lines",
    "rating_trend",
    "aspect_food",
    "aspect_service",
    "aspect_ambience",
    "aspect_value",
    "noise_level",
    "summary",
    "best_for",
    "buzz_score",
    "trending_social",
    "social_mentions_6m",
    "hidden",
    "excluded_reason",
]


def _venue_row(config: Any, venue: Any, s: Any, party_size: int) -> dict[str, Any]:
    per_pax = config.settings.scoring.price_per_pax_sgd.get(venue.price_level or 0)
    return {
        "rank": s.rank or "",
        "score": f"{s.score:.4f}" if not s.excluded_reason else "",
        "venue_id": venue.id,
        "name": venue.name,
        "name_zh": venue.name_zh or "",
        "brand": venue.brand or "",
        "outlet": venue.outlet or "",
        "address": venue.address or "",
        "postal_code": venue.postal_code or "",
        "district": venue.district or "",
        "region": venue.region or "",
        "lat": venue.lat or "",
        "lng": venue.lng or "",
        "cuisine": "; ".join(venue.cuisine),
        "price_level": "$" * venue.price_level if venue.price_level else "",
        "bill_estimate_sgd": f"{per_pax * party_size:.0f}" if per_pax else "",
        "google_rating": venue.google_rating or "",
        "google_reviews": venue.google_reviews or "",
        "independent_sources": len(venue.independent_sources),
        "sources": "; ".join(venue.source_keys),
        "michelin": venue.michelin or "",
        "hygiene_grade": venue.hygiene_grade or "",
        "open_weekends": venue.open_weekends if venue.open_weekends is not None else "",
        "booking_url": venue.booking_url or "",
        "website": venue.website or "",
        "phone": venue.phone or "",
        "kid_friendly": venue.flags.get("kid_friendly", ""),
        "private_room": venue.flags.get("private_room", ""),
        "halal": venue.flags.get("halal", ""),
        "earliest_evidence": venue.earliest_evidence or "",
        "nearest_mrt": venue.nearest_mrt or "",
        "mrt_lines": "; ".join(venue.mrt_lines),
        "rating_trend": venue.rating_trend or "",
        "aspect_food": venue.aspects.get("food", ""),
        "aspect_service": venue.aspects.get("service", ""),
        "aspect_ambience": venue.aspects.get("ambience", ""),
        "aspect_value": venue.aspects.get("value", ""),
        "noise_level": venue.noise_level or "",
        "summary": venue.summary or "",
        "best_for": venue.best_for or "",
        "buzz_score": venue.buzz_score or "",
        "trending_social": venue.trending_social,
        "social_mentions_6m": venue.social_mentions_window or "",
        "hidden": s.hidden,
        "excluded_reason": s.excluded_reason or "",
    }


def export_category_csvs(
    config: Any, scores: dict[str, list[Any]], venues: dict[str, Any], out_dir: Path
) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    for key, scored in scores.items():
        cat = config.categories.get(key)
        path = out_dir / f"{key}.csv"
        with path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=CATEGORY_COLUMNS)
            writer.writeheader()
            for s in scored:
                writer.writerow(_venue_row(config, venues[s.venue_id], s, cat.party_size))
        paths[key] = path
    return paths


def export_merged_json(
    config: Any, scores: dict[str, list[Any]], venues: dict[str, Any], out_dir: Path, run_id: str
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "venues_ranked.json"
    by_venue: dict[str, dict[str, Any]] = {}
    for key, scored in scores.items():
        for s in scored:
            by_venue.setdefault(s.venue_id, {})[key] = s.to_dict()
    payload = {
        "run_id": run_id,
        "categories": {
            key: {
                "display_name": config.categories.get(key).display_name,
                "top": [
                    s.venue_id for s in scored if s.rank and s.rank <= config.settings.scoring.top_n
                ],
            }
            for key, scored in scores.items()
        },
        "venues": [{**v.to_dict(), "scores": by_venue.get(v.id, {})} for v in venues.values()],
    }
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    return path
