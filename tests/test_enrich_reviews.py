from __future__ import annotations

from datetime import date

import httpx

from sgfoodhunt.config import AppConfig
from sgfoodhunt.dedup import Evidence, Venue
from sgfoodhunt.enrich import (
    OneMapGeocoder,
    enrich_venues,
    load_stations,
    nearest_station,
)
from sgfoodhunt.http.cache import ResponseCache
from sgfoodhunt.http.client import AsyncApiClient
from sgfoodhunt.reviews import (
    aspect_scores,
    best_for_line,
    keyword_counts,
    noise_level,
    rating_trend,
    summarise,
)
from sgfoodhunt.reviews.analysis import analyse_venue, record_rating_history
from tests.conftest import fixture_text


def test_station_table_loads() -> None:
    stations = load_stations()
    assert len(stations) > 120
    names = {s.name for s in stations}
    assert {"Tiong Bahru", "Orchard", "Tampines", "Punggol", "Bayshore"} <= names
    assert all(1.2 < s.lat < 1.5 and 103.6 < s.lng < 104.1 for s in stations)


def test_nearest_station_and_walk() -> None:
    station, dist = nearest_station(
        1.2851, 103.8323
    )  # Tiong Bahru Bakery: Havelock (TEL) is closer
    assert station.name == "Havelock" and "Thomson-East Coast" in station.lines and dist < 500
    station, dist = nearest_station(1.2861, 103.8280)  # just east of Tiong Bahru station
    assert station.name == "Tiong Bahru" and "East West" in station.lines
    station, _ = nearest_station(1.2864, 103.8536)  # Fullerton
    assert station.name == "Raffles Place"


async def test_geocoder_and_enrich(http_settings, cache: ResponseCache) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.url.params["searchVal"] in ("150124", "000000")
        if request.url.params["searchVal"] == "000000":
            return httpx.Response(200, json={"found": 0, "results": []})
        return httpx.Response(
            200, json=__import__("json").loads(fixture_text("onemap_search.json"))
        )

    client = AsyncApiClient(
        http_settings,
        cache,
        rpm=1000,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    geocoder = OneMapGeocoder(client)
    kek = Venue(id="v1", name="KEK", postal_code="150124")
    nowhere = Venue(id="v2", name="Nowhere", postal_code="000000")
    has_coords = Venue(id="v3", name="Fullerton", lat=1.2864, lng=103.8536)
    no_postal = Venue(id="v4", name="Mystery")
    stats = await enrich_venues([kek, nowhere, has_coords, no_postal], geocoder)
    assert stats.geocoded == 1 and stats.geocode_failed == 1 and stats.mrt_assigned == 2
    assert kek.lat == 1.2865 and kek.nearest_mrt == "Redhill" and kek.mrt_lines == ["East West"]
    assert has_coords.nearest_mrt == "Raffles Place" and stats.onemap_requests == 2
    assert no_postal.nearest_mrt is None
    # second pass is served from cache and does not re-assign
    stats2 = await enrich_venues([Venue(id="v5", name="KEK again", postal_code="150124")], geocoder)
    assert stats2.geocoded == 1 and calls == 2 and stats2.onemap_cache_hits == 1
    stats3 = await enrich_venues([Venue(id="v6", name="Offline", postal_code="150124")], None)
    assert stats3.geocoded == 0 and stats3.mrt_assigned == 0


def test_aspect_scores_and_noise() -> None:
    texts = [
        "the food was delicious and the prawns were fresh. service was slow though, we waited ages.",
        "beautiful cosy ambience but very noisy on weekends. prices are reasonable, worth it.",
        "portion not generous. staff were friendly.",
    ]
    scores = aspect_scores(texts)
    assert scores["food"] > 0.6 and scores["service"] < 0.6 and scores["value"] > 0.6
    assert set(scores) == {"food", "service", "ambience", "value"}
    assert noise_level(texts, []) == "loud"
    assert noise_level(["quiet corner, peaceful"], []) == "quiet"
    assert noise_level(["great"], []) is None
    assert aspect_scores(["hello"]) == {}


def test_keyword_counts() -> None:
    assert keyword_counts(
        ["romantic date night, very romantic"], ["romantic", "date night", "kids"]
    ) == {"romantic": 2, "date night": 1}


def test_rating_trend_history_and_reviews() -> None:
    v = Venue(id="v", name="X", ratings={"google_places": {"rating": 4.3, "review_count": 100}})
    assert rating_trend(v, date(2026, 9, 1)) == "unknown"
    v.review_snippets = [
        {"rating": 5, "published_at": "2026-08-01"},
        {"rating": 5, "published_at": "2026-07-01"},
        {"rating": 4, "published_at": "2025-12-01"},
    ]
    assert rating_trend(v, date(2026, 9, 1)) == "improving"
    v.review_snippets = [
        {"rating": 3, "published_at": "2026-08-01"},
        {"rating": 3, "published_at": "2026-07-01"},
        {"rating": 4, "published_at": "2026-06-01"},
    ]
    assert rating_trend(v, date(2026, 9, 1)) == "declining"
    record_rating_history(v, "r1", date(2026, 1, 1))
    v.ratings["google_places"]["rating"] = 4.0
    record_rating_history(v, "r2", date(2026, 6, 1))
    record_rating_history(v, "r2", date(2026, 6, 1))  # idempotent
    assert len(v.rating_history) == 2
    assert rating_trend(v, date(2026, 9, 1)) == "declining"  # history wins over snippets
    v.rating_history[1]["rating"] = 4.35
    assert rating_trend(v, date(2026, 9, 1)) == "stable"


def test_summary_and_best_for(app_config: AppConfig) -> None:
    v = Venue(
        id="v",
        name="Keng Eng Kee Seafood",
        cuisine=["Chinese", "Seafood", "Zi Char"],
        district="D03 Queenstown / Tiong Bahru",
        region="Central",
        price_level=1,
        nearest_mrt="Redhill",
        michelin="Bib Gourmand",
        ratings={"google_places": {"rating": 4.3, "review_count": 2100}},
        evidence=[
            Evidence("sethlui", "u1", snippet="delicious wok hei, tasty prawns"),
            Evidence("eatbook", "u2"),
            Evidence("michelin", "u3"),
        ],
        flags={"good_for_groups": True, "kid_friendly": True},
    )
    analyse_venue(v, app_config, {"zichar_family": 1, "family_weekend": 4})
    assert v.summary is not None
    assert v.summary.startswith(
        "Keng Eng Kee Seafood is a budget friendly Zi Char place in Queenstown / Tiong Bahru, near Redhill MRT."
    )
    assert (
        "recommended by 3 independent sources" in v.summary and "Michelin Bib Gourmand" in v.summary
    )
    assert v.best_for == "a no-fuss zi char dinner with the family"
    # cuisine affinity: ranked 1st for cafes but 2nd for zi char -> still zi char
    assert (
        best_for_line(v, app_config, {"cafes_date": 1, "zichar_family": 2})
        == "a no-fuss zi char dinner with the family"
    )
    assert (
        best_for_line(v, app_config, {"cafes_date": 1, "zichar_family": 6}) == "a casual cafe date"
    )
    assert (
        v.aspects["food"] > 0.6 and v.keyword_counts.get("zi char", 0) == 0
    )  # no 'zi char' in the texts
    assert "wok hei" not in v.summary  # never copies review text
    cafe = Venue(
        id="c", name="Quiet Cafe", cuisine=["Cafe"], region="East", ambience_tags=["quiet", "cosy"]
    )
    assert best_for_line(cafe, app_config, {"cafes_date": 2}) == "a quiet coffee date"
    assert best_for_line(cafe, app_config, {}) == "not ranked yet"
    assert best_for_line(cafe, app_config, {"new_cafes": 1}) == "trying a new cafe this month"
    view = Venue(id="r", name="Sky", ambience_tags=["rooftop"])
    assert (
        best_for_line(view, app_config, {"restaurants_date": 3, "rooftop_waterfront_date": 1})
        == "a date with a view"
    )
    assert summarise(Venue(id="p", name="Plain"), app_config) == "Plain is a restaurant."
