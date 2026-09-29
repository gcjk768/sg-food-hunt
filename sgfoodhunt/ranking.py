"""Stage two driver: raw sightings of a run -> venues -> per category scores -> vault notes and exports.

Runs entirely offline from the run's stored JSONL, so ``sgfh rank`` can re-score after a weight
change without touching any source.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sgfoodhunt.config import AppConfig
from sgfoodhunt.dedup import Venue, build_venues
from sgfoodhunt.dedup.registry import MatchStats
from sgfoodhunt.enrich import EnrichStats, OneMapGeocoder, enrich_venues
from sgfoodhunt.http.cache import ResponseCache, write_json
from sgfoodhunt.http.client import AsyncApiClient
from sgfoodhunt.reporting.exports import export_category_csvs, export_merged_json
from sgfoodhunt.reporting.venue_notes import (
    read_personal_notes,
    write_category_notes,
    write_venue_notes,
)
from sgfoodhunt.reviews import analyse_venue
from sgfoodhunt.reviews.analysis import record_rating_history
from sgfoodhunt.scoring import ScoredVenue, score_category
from sgfoodhunt.storage.runs import RunStore
from sgfoodhunt.storage.vault import Vault

log = logging.getLogger(__name__)


@dataclass(slots=True)
class RankResult:
    run_id: str
    venues: dict[str, Venue]
    scores: dict[str, list[ScoredVenue]]
    match_stats: MatchStats
    enrich_stats: EnrichStats = field(default_factory=EnrichStats)
    personal_count: int = 0
    notes_written: int = 0
    exports: dict[str, str] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        return {
            "venues_total": len(self.venues),
            "venues_new": self.match_stats.created,
            "sightings": self.match_stats.sightings,
            "fuzzy_merges": len(self.match_stats.merges),
            "geocoded": self.enrich_stats.geocoded,
            "mrt_assigned": self.enrich_stats.mrt_assigned,
            "personal_notes": self.personal_count,
            "venue_notes_written": self.notes_written,
            "ranked_per_category": {
                k: sum(1 for s in v if not s.excluded_reason and not s.hidden)
                for k, v in self.scores.items()
            },
        }


def default_geocoder(
    config: AppConfig, cache: ResponseCache, dry_run: bool = False
) -> OneMapGeocoder:
    client = AsyncApiClient(
        config.settings.http,
        cache,
        rpm=config.settings.api_rate_limits.get("onemap", 60),
        dry_run=dry_run,
    )
    return OneMapGeocoder(client)


async def rank_run(
    config: AppConfig,
    store: RunStore,
    vault: Vault,
    run_id: str,
    hide_visited: bool = False,
    category_keys: list[str] | None = None,
    geocoder: OneMapGeocoder | None = None,
) -> RankResult:
    """Dedup -> enrich -> analyse reviews -> score -> notes and exports for a stored run.

    ``geocoder`` may be None to skip OneMap lookups (venues keep whatever coordinates a source
    gave them; nearest MRT is still computed for those).
    """
    run_dir = store.run_dir(run_id)
    rows = list(store.iter_candidates(run_id))
    registry, match_stats = build_venues(config, rows, run_id, run_dir)
    venues = dict(registry.venues)
    seen = [v for v in venues.values() if v.last_seen_run == run_id]
    for v in seen:
        record_rating_history(v, run_id)
    enrich_stats = await enrich_venues(seen, geocoder)
    for v in venues.values():
        analyse_venue(v, config)
    vault.ensure()
    personal = read_personal_notes(vault, list(venues.values()))

    wanted = category_keys or [c.key for c in config.categories.categories]
    scores: dict[str, list[ScoredVenue]] = {}
    aspects = {v.id: v.aspects for v in venues.values() if v.aspects}
    trends = {v.id: v.rating_trend or "unknown" for v in venues.values()}
    for key in wanted:
        cat = config.categories.get(key)
        pool = [v for v in venues.values() if key in v.category_keys]
        if not pool:
            continue
        scores[key] = score_category(
            cat,
            pool,
            config.settings.scoring,
            personal=personal,
            aspects=aspects,
            trends=trends,
            hide_visited=hide_visited,
        )
    ranks: dict[str, dict[str, int]] = {}
    for key, scored in scores.items():
        for sv in scored:
            if sv.rank:
                ranks.setdefault(sv.venue_id, {})[key] = sv.rank
    for v in venues.values():
        analyse_venue(v, config, ranks.get(v.id))
    registry.save()
    write_json(run_dir / "venues.json", [v.to_dict() for v in seen])
    write_json(
        run_dir / "scores.json",
        {k: [s.to_dict() for s in v] for k, v in scores.items()},
    )
    notes_written = write_venue_notes(vault, config, list(venues.values()), scores, run_id)
    write_category_notes(vault, config, scores, venues, run_id)
    exports_dir = config.resolve(config.settings.paths.exports_dir) / run_id
    csvs = export_category_csvs(config, scores, venues, exports_dir)
    merged = export_merged_json(config, scores, venues, exports_dir, run_id)
    result = RankResult(
        run_id=run_id,
        venues=venues,
        scores=scores,
        match_stats=match_stats,
        enrich_stats=enrich_stats,
        personal_count=len(personal),
        notes_written=notes_written,
        exports={**{k: str(p) for k, p in csvs.items()}, "merged_json": str(merged)},
    )
    record = store.load_run(run_id)
    record.stats["ranking"] = result.summary()
    store.save_run(record)
    log.info(
        "ranked %d categories over %d venues (%d new); %d venue notes written",
        len(scores),
        len(venues),
        match_stats.created,
        notes_written,
    )
    return result
