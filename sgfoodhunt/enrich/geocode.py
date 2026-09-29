"""Postal code -> coordinates through OneMap's public search endpoint (no key needed, cached)."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from sgfoodhunt.http.cache import CacheMiss
from sgfoodhunt.http.client import AsyncApiClient, FetchError

log = logging.getLogger(__name__)

ONEMAP_SEARCH = "https://www.onemap.gov.sg/api/common/elastic/search"


class OneMapGeocoder:
    def __init__(self, client: AsyncApiClient, ttl_days: int = 365) -> None:
        self.client = client
        self.ttl = timedelta(days=ttl_days)
        self.lookups = 0
        self.cache_hits = 0

    async def postal(self, postal_code: str) -> tuple[float, float] | None:
        try:
            payload, cached = await self.client.request_json(
                "GET",
                ONEMAP_SEARCH,
                params={
                    "searchVal": postal_code,
                    "returnGeom": "Y",
                    "getAddrDetails": "Y",
                    "pageNum": 1,
                },
                ttl=self.ttl,
            )
        except CacheMiss:
            return None
        except FetchError as exc:
            log.warning("OneMap lookup failed for %s: %s", postal_code, exc)
            return None
        self.cache_hits += int(cached)
        self.lookups += int(not cached)
        return _first_match(payload, postal_code)


def _first_match(payload: Any, postal_code: str) -> tuple[float, float] | None:
    results = payload.get("results") if isinstance(payload, dict) else None
    if not results:
        return None
    for r in results:
        if str(r.get("POSTAL", "")).strip() == postal_code:
            try:
                return float(r["LATITUDE"]), float(r["LONGITUDE"])
            except (KeyError, TypeError, ValueError):
                continue
    r = results[0]
    try:
        return float(r["LATITUDE"]), float(r["LONGITUDE"])
    except (KeyError, TypeError, ValueError):
        return None
