"""Apply geocoding and nearest-MRT lookups to venues."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass

from sgfoodhunt.dedup.venue import Venue
from sgfoodhunt.enrich.geocode import OneMapGeocoder
from sgfoodhunt.enrich.mrt import nearest_station

log = logging.getLogger(__name__)


@dataclass(slots=True)
class EnrichStats:
    geocoded: int = 0
    geocode_failed: int = 0
    mrt_assigned: int = 0
    onemap_requests: int = 0
    onemap_cache_hits: int = 0


async def enrich_venues(venues: Iterable[Venue], geocoder: OneMapGeocoder | None) -> EnrichStats:
    stats = EnrichStats()
    for v in venues:
        if (v.lat is None or v.lng is None) and v.postal_code and geocoder is not None:
            coords = await geocoder.postal(v.postal_code)
            if coords:
                v.lat, v.lng = coords
                stats.geocoded += 1
            else:
                stats.geocode_failed += 1
        if v.lat is not None and v.lng is not None and v.nearest_mrt is None:
            station, _dist = nearest_station(v.lat, v.lng)
            v.nearest_mrt = station.name
            v.mrt_lines = list(station.lines)
            stats.mrt_assigned += 1
    if geocoder is not None:
        stats.onemap_requests = geocoder.lookups
        stats.onemap_cache_hits = geocoder.cache_hits
    log.info(
        "enrich: %d geocoded (%d failed), %d nearest MRT assigned",
        stats.geocoded,
        stats.geocode_failed,
        stats.mrt_assigned,
    )
    return stats
