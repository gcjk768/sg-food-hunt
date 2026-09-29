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
from sgfoodhunt.http.cache import write_json
from sgfoodhunt.reporting.exports import export_category_csvs, export_merged_json
from sgfoodhunt.reporting.venue_notes import (
    read_personal_notes,
    write_category_notes,
    write_venue_notes,
)
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
    personal_count: int = 0
    notes_written: int = 0
    exports: dict[str, str] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        return {
            "venues_total": len(self.venues),
            "venues_new": self.match_stats.created,
            "sightings": self.match_stats.sightings,
            "fuzzy_merges": len(self.match_stats.merges),
            "personal_notes": self.personal_count,
            "venue_notes_written": self.notes_written,
            "ranked_per_category": {
                k: sum(1 for s in v if not s.excluded_reason and not s.hidden)
                for k, v in self.scores.items()
            },
        }


def rank_run(
    config: AppConfig,
    store: RunStore,
    vault: Vault,
    run_id: str,
    hide_visited: bool = False,
    category_keys: list[str] | None = None,
) -> RankResult:
    run_dir = store.run_dir(run_id)
    rows = list(store.iter_candidates(run_id))
    registry, match_stats = build_venues(config, rows, run_id, run_dir)
    venues = dict(registry.venues)
    vault.ensure()
    personal = read_personal_notes(vault, list(venues.values()))

    wanted = category_keys or [c.key for c in config.categories.categories]
    scores: dict[str, list[ScoredVenue]] = {}
    for key in wanted:
        cat = config.categories.get(key)
        pool = [v for v in venues.values() if key in v.category_keys]
        if not pool:
            continue
        scores[key] = score_category(
            cat, pool, config.settings.scoring, personal=personal, hide_visited=hide_visited
        )
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
