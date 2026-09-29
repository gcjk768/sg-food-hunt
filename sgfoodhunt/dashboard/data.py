"""Data loading for the dashboard (no Streamlit import so it is unit testable)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sgfoodhunt.config import AppConfig, load_config
from sgfoodhunt.http.cache import read_json
from sgfoodhunt.storage.runs import RunStore


def load_dashboard_data(
    config_dir: Path | str = "config",
) -> tuple[AppConfig, str | None, list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    """Return (config, latest run id, venues with their scores, per category ranked lists)."""
    config = load_config(config_dir)
    store = RunStore(config.resolve(config.settings.paths.data_dir))
    runs = [r for r in store.list_runs() if (store.run_dir(r.run_id) / "scores.json").exists()]
    if not runs:
        return config, None, [], {}
    run = runs[-1]
    run_dir = store.run_dir(run.run_id)
    scores: dict[str, list[dict[str, Any]]] = read_json(run_dir / "scores.json")
    registry_path = config.resolve(config.settings.paths.data_dir) / "venues.json"
    venues = read_json(registry_path).get("venues", []) if registry_path.exists() else []
    by_venue: dict[str, dict[str, dict[str, Any]]] = {}
    for key, scored in scores.items():
        for s in scored:
            by_venue.setdefault(s["venue_id"], {})[key] = s
    rows = []
    for v in venues:
        rows.append({**v, "scores": by_venue.get(v["id"], {})})
    return config, run.run_id, rows, scores


def venue_table(
    rows: list[dict[str, Any]], category_key: str | None, config: AppConfig
) -> list[dict[str, Any]]:
    """Flatten venues into the columns the dashboard shows, filtered to one category if given."""
    out = []
    for v in rows:
        s = v["scores"].get(category_key) if category_key else None
        if category_key and (s is None or s.get("excluded_reason") or s.get("hidden")):
            continue
        g = (v.get("ratings") or {}).get("google_places") or {}
        per_pax = config.settings.scoring.price_per_pax_sgd.get(v.get("price_level") or 0)
        out.append(
            {
                "rank": s["rank"] if s else None,
                "score": round(s["score"], 3) if s else None,
                "name": v["name"],
                "region": v.get("region"),
                "district": v.get("district"),
                "nearest_mrt": v.get("nearest_mrt"),
                "cuisine": ", ".join(v.get("cuisine") or []),
                "price": "$" * v["price_level"] if v.get("price_level") else "",
                "per_pax_sgd": per_pax,
                "google_rating": g.get("rating"),
                "google_reviews": g.get("review_count"),
                "halal": bool((v.get("flags") or {}).get("halal")),
                "kid_friendly": bool((v.get("flags") or {}).get("kid_friendly")),
                "michelin": v.get("michelin") or "",
                "sources": len({e["source_key"] for e in v.get("evidence", [])}),
                "social_6m": v.get("social_mentions_window") or 0,
                "trending": bool(v.get("trending_social")),
                "best_for": v.get("best_for") or "",
                "booking": v.get("booking_url") or v.get("website") or "",
                "lat": v.get("lat"),
                "lon": v.get("lng"),
                "status": v.get("business_status") or "",
                "id": v["id"],
            }
        )
    out.sort(key=lambda r: (r["rank"] is None, r["rank"] or 0, r["name"]))
    return out
