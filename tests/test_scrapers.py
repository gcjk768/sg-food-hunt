from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from sgfoodhunt.models import SearchQuery
from sgfoodhunt.pipeline import CATALOGUE_QUERY
from sgfoodhunt.scrapers import REGISTRY
from sgfoodhunt.scrapers.google_places import anonymise_reviews, place_to_candidate
from sgfoodhunt.scrapers.michelin import distinction_from_text
from sgfoodhunt.scrapers.reddit import extract_venue_names
from tests.conftest import FakeSession, fixture_text

Q = SearchQuery("cafes_date", "dating", "romantic cafes Singapore", 2)
FAMILY_Q = SearchQuery("family_weekend", "family", "kid friendly restaurants Singapore", 4)


def test_registry_covers_every_configured_source(app_config) -> None:
    for src in app_config.sources.sources:
        assert src.scraper in REGISTRY, src.key


# --- blogs ------------------------------------------------------------------------------------
async def test_blog_scraper_extracts_venues(make_scraper, fake_session: FakeSession) -> None:
    scraper, _ = make_scraper(
        "sethlui",
        search_url="https://example-blog.test/?s={query}",
        base_url="https://example-blog.test",
    )
    fake_session.add(
        "https://example-blog.test/?s=romantic+cafes+Singapore", fixture_text("blog_search.html")
    )
    fake_session.add(
        "https://example-blog.test/best-romantic-cafes-singapore/",
        fixture_text("blog_article.html"),
    )
    fake_session.add(
        "https://example-blog.test/hawker-guide-to-tiong-bahru/",
        "<html><body><article><div class='entry-content'><p>nothing</p></div></article></body></html>",
    )
    result = await scraper.search(Q)
    assert result.skipped_reason is None
    assert [c.name for c in result.candidates] == ["Tiong Bahru Bakery", "Keng Eng Kee Seafood"]
    tbb = result.candidates[0]
    assert tbb.postal_code == "160056" and tbb.website == "https://www.tiongbahrubakery.com"
    assert tbb.page is not None and tbb.page.published_at == "2026-03-14"
    assert tbb.opening_hours == {
        "text": "7.30am to 8pm daily Tel: +65 6220 3430"[:120]
    } or "7.30am" in str(tbb.opening_hours)
    assert result.candidates[1].name_zh == "琼荣记海鲜"
    assert len(result.pages) == 3 and result.requests_made == 3
    assert not any("no article links" in w for w in result.warnings)


async def test_blog_scraper_respects_robots(make_scraper, fake_session: FakeSession) -> None:
    scraper, _ = make_scraper("sethlui", search_url="https://example-blog.test/private/?s={query}")
    result = await scraper.search(Q)
    assert result.skipped_reason and "robots" in result.skipped_reason
    assert result.candidates == [] and result.requests_made == 0


async def test_blog_scraper_dry_run_without_cache(make_scraper, fake_session: FakeSession) -> None:
    scraper, _ = make_scraper(
        "sethlui", dry_run=True, search_url="https://example-blog.test/?s={query}"
    )
    result = await scraper.search(Q)
    assert result.candidates == [] and any("dry run" in w for w in result.warnings)
    assert fake_session.calls == []


def test_sassymama_only_applies_to_family(make_scraper) -> None:
    scraper, _ = make_scraper("sassymama")
    assert scraper.applies_to(FAMILY_Q) is True
    assert scraper.applies_to(Q) is False


# --- booking / aggregators ----------------------------------------------------------------------
async def test_quandoo_uses_json_ld(make_scraper, fake_session: FakeSession) -> None:
    scraper, _ = make_scraper("quandoo", search_url="https://example-blog.test/quandoo?q={query}")
    fake_session.add(
        "https://example-blog.test/quandoo?q=romantic+cafes+Singapore",
        fixture_text("quandoo_search.html"),
    )
    result = await scraper.search(Q)
    names = [c.name for c in result.candidates]
    assert names == ["Luna Rooftop", "Ah Hua Zi Char"]
    luna = result.candidates[0]
    assert luna.booking_url == "https://www.quandoo.sg/en/place/luna-rooftop-12345"
    assert luna.postal_code == "049213" and luna.rating == 4.6 and luna.review_count == 812
    assert result.candidates[1].postal_code == "310123"


async def test_chope_cards(make_scraper, fake_session: FakeSession) -> None:
    scraper, _ = make_scraper(
        "chope", fetch="static", search_url="https://example-blog.test/chope?q={query}"
    )
    fake_session.add(
        "https://example-blog.test/chope?q=romantic+cafes+Singapore",
        fixture_text("chope_search.html"),
    )
    result = await scraper.search(Q)
    assert [c.name for c in result.candidates] == ["Sky Garden Grill", "Mum's Kitchen 妈妈厨房"]
    sky = result.candidates[0]
    assert sky.booking_url == "https://www.chope.co/singapore-restaurants/sky-garden-grill"
    assert (
        sky.postal_code == "018972"
        and sky.price_text == "$$$$"
        and sky.cuisine == ["Western", "Grill"]
    )
    assert result.candidates[1].postal_code == "460123"


async def test_card_scraper_warns_when_nothing_parsed(
    make_scraper, fake_session: FakeSession
) -> None:
    scraper, _ = make_scraper("tablecheck", search_url="https://example-blog.test/tc?q={query}")
    fake_session.add(
        "https://example-blog.test/tc?q=romantic+cafes+Singapore",
        "<html><body><p>js app</p></body></html>",
    )
    result = await scraper.search(Q)
    assert result.candidates == [] and any("selectors may be stale" in w for w in result.warnings)


async def test_burpple_cards(make_scraper, fake_session: FakeSession) -> None:
    scraper, _ = make_scraper("burpple", search_url="https://example-blog.test/burpple?q={query}")
    fake_session.add(
        "https://example-blog.test/burpple?q=romantic+cafes+Singapore",
        fixture_text("burpple_search.html"),
    )
    result = await scraper.search(Q)
    assert [c.name for c in result.candidates] == [
        "Coexist Coffee Co.",
        "KEK Seafood (Keng Eng Kee)",
    ]
    assert result.candidates[0].website == "https://www.burpple.com/coexist-coffee-co"
    assert result.candidates[0].booking_url is None
    assert result.candidates[0].postal_code == "669592"


async def test_hungrygowhere_cards(make_scraper, fake_session: FakeSession) -> None:
    scraper, _ = make_scraper("hungrygowhere", search_url="https://example-blog.test/hgw?q={query}")
    fake_session.add(
        "https://example-blog.test/hgw?q=romantic+cafes+Singapore", fixture_text("hgw_search.html")
    )
    result = await scraper.search(Q)
    assert len(result.candidates) == 1
    c = result.candidates[0]
    assert (
        c.name == "Imperial Treasure Super Peking Duck"
        and c.postal_code == "238801"
        and c.rating == 4.2
    )
    assert c.cuisine == ["Chinese", "Dim Sum"]


async def test_tripadvisor_parser(make_scraper, fake_session: FakeSession) -> None:
    scraper, _ = make_scraper(
        "tripadvisor",
        enabled=True,
        tos_status="verified_ok",
        search_url="https://example-blog.test/ta?q={query}",
    )
    fake_session.add(
        "https://example-blog.test/ta?q=romantic+cafes+Singapore",
        fixture_text("tripadvisor_search.html"),
    )
    result = await scraper.search(Q)
    assert [c.name for c in result.candidates] == ["Jumbo Seafood"]
    assert (
        result.candidates[0].review_count == 5432 and result.candidates[0].postal_code == "058416"
    )


# --- michelin ----------------------------------------------------------------------------------
def test_distinction_from_text() -> None:
    assert distinction_from_text("One MICHELIN Star: High quality cooking") == "1 Star"
    assert distinction_from_text("Three MICHELIN Stars") == "3 Stars"
    assert distinction_from_text("Bib Gourmand: good quality") == "Bib Gourmand"
    assert distinction_from_text("2 stars") == "2 Stars"
    assert distinction_from_text("MICHELIN Guide selected") == "Selected"


async def test_michelin_listing(make_scraper, fake_session: FakeSession) -> None:
    scraper, _ = make_scraper(
        "michelin", options={"listing_url": "https://example-blog.test/michelin", "max_pages": 5}
    )
    fake_session.add("https://example-blog.test/michelin", fixture_text("michelin_listing.html"))
    fake_session.add(
        "https://example-blog.test/michelin/page/2", fixture_text("michelin_empty.html")
    )
    assert scraper.query_driven is False
    result = await scraper.search(CATALOGUE_QUERY)
    got = {c.name: c.michelin for c in result.candidates}
    assert got == {
        "Burnt Ends": "1 Star",
        "Keng Eng Kee Seafood": "Bib Gourmand",
        "Odette": "3 Stars",
    }
    assert (
        result.candidates[0].website
        == "https://guide.michelin.com/sg/en/singapore-region/singapore/restaurant/burnt-ends"
    )
    assert (
        len(result.pages) == 2
        and fake_session.calls.count("https://example-blog.test/michelin/page/3") == 0
    )


# --- google places --------------------------------------------------------------------------------
def test_place_to_candidate_strips_reviewer_data() -> None:
    place = json.loads(fixture_text("google_places_page1.json"))["places"][0]
    cand = place_to_candidate("google_places", place)
    assert cand is not None
    assert (
        cand.name == "Tiong Bahru Bakery" and cand.postal_code == "160056" and cand.price_level == 2
    )
    assert (
        cand.lat == 1.2851
        and cand.business_status == "OPERATIONAL"
        and cand.cuisine == ["bakery", "cafe"]
    )
    assert cand.booking_url is None  # not reservable
    reviews = cand.extra["reviews"]
    assert len(reviews) == 2 and reviews[0]["rating"] == 5 and reviews[0]["social_words"] == 1
    dumped = json.dumps(cand.extra)
    assert (
        "Jane Doe" not in dumped and "contrib" not in dumped and "authorAttribution" not in dumped
    )
    assert cand.extra["good_for_children"] is True and cand.extra["photo_count"] == 2


def test_anonymise_reviews_handles_missing_text() -> None:
    assert anonymise_reviews([{"rating": 4}]) == [
        {"rating": 4, "published_at": None, "snippet": None, "social_words": 0}
    ]


def _places_handler(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    assert request.headers["X-Goog-Api-Key"] == "test-key"
    assert "places.reviews" in request.headers["X-Goog-FieldMask"]
    assert body["regionCode"] == "SG" and body["locationBias"]["circle"]["radius"] == 30000
    page = (
        "google_places_page2.json"
        if body.get("pageToken") == "TOKEN2"
        else "google_places_page1.json"
    )
    return httpx.Response(200, json=json.loads(fixture_text(page)))


async def test_google_places_paginates_and_caches(
    make_scraper, monkeypatch: pytest.MonkeyPatch
) -> None:
    scraper, factory = make_scraper("google_places", handler=_places_handler)
    assert scraper.missing_credentials() is not None
    scraper.ctx.config.secrets.google_places_api_key = "test-key"
    result = await scraper.search(Q)
    assert [c.name for c in result.candidates] == [
        "Tiong Bahru Bakery",
        "Luna Rooftop",
        "Old Cafe",
        "Keng Eng Kee Seafood",
    ]
    assert result.requests_made == 2 and result.cache_hits == 0 and len(factory.requests) == 2
    closed = result.candidates[2]
    assert closed.business_status == "CLOSED_PERMANENTLY"
    again = await scraper.search(Q)
    assert again.cache_hits == 2 and again.requests_made == 0 and len(factory.requests) == 2


async def test_google_places_skips_without_key(make_scraper) -> None:
    scraper, factory = make_scraper("google_places")
    result = await scraper.search(Q)
    assert result.skipped_reason == "GOOGLE_PLACES_API_KEY is not set" and factory.requests == []


async def test_google_reserve_keeps_reservable_only(make_scraper) -> None:
    scraper, _ = make_scraper("google_reserve", handler=_places_handler)
    scraper.ctx.config.secrets.google_places_api_key = "test-key"
    result = await scraper.search(Q)
    assert [c.name for c in result.candidates] == ["Luna Rooftop", "Keng Eng Kee Seafood"]
    assert result.candidates[0].booking_url == "https://maps.google.com/?cid=2"
    assert (
        result.candidates[0].source_key == "google_reserve"
        and "reviews" not in result.candidates[0].extra
    )


# --- reddit ----------------------------------------------------------------------------------------
def test_extract_venue_names() -> None:
    text = "Go to **Two Chefs Eating Place** at Commonwealth. Also try New Ubin Seafood.\n- Kok Sen Restaurant - big prawn\n- JB Ah Meng: crab\nI think The best"
    found = dict(extract_venue_names(text))
    assert found["Two Chefs Eating Place"] == 0.6
    assert found["Kok Sen Restaurant"] == 0.5 and found["JB Ah Meng"] == 0.5
    assert found["New Ubin Seafood"] == 0.35
    assert "The" not in found and "I" not in found


def _reddit_handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if url.startswith("https://www.reddit.com/api/v1/access_token"):
        assert request.headers.get("Authorization", "").startswith("Basic ")
        return httpx.Response(200, json=json.loads(fixture_text("reddit_token.json")))
    assert request.headers["Authorization"] == "bearer abc"
    if "/search" in url:
        if "r/singapore/" in url:
            return httpx.Response(200, json={"data": {"children": []}})
        return httpx.Response(200, json=json.loads(fixture_text("reddit_search.json")))
    if "/comments/t1abc" in url:
        return httpx.Response(200, json=json.loads(fixture_text("reddit_comments.json")))
    return httpx.Response(404)


async def test_reddit_threads_and_comments(make_scraper) -> None:
    scraper, _factory = make_scraper("reddit", handler=_reddit_handler)
    assert scraper.missing_credentials()
    scraper.ctx.config.secrets.reddit_client_id = "id"
    scraper.ctx.config.secrets.reddit_client_secret = "secret"
    result = await scraper.search(
        SearchQuery("zichar_family", "family", "best zi char Singapore", 5)
    )
    names = sorted(c.name for c in result.candidates)
    assert names == [
        "JB Ah Meng",
        "Keng Eng Kee",
        "Kok Sen Restaurant",
        "New Ubin Seafood",
        "Two Chefs Eating Place",
    ]
    assert all(c.confidence < 1 for c in result.candidates)
    assert (
        result.pages[0].url == "https://www.reddit.com/r/singaporeeats/comments/t1abc/best_zi_char/"
    )
    dumped = json.dumps([c.extra for c in result.candidates]) + json.dumps(
        [c.snippet for c in result.candidates]
    )
    assert "u1" not in dumped.split() and "someuser" not in dumped
    assert (
        result.requests_made == 3
    )  # two subreddit searches + one thread; token is not cached/counted


# --- sfa ---------------------------------------------------------------------------------------------
async def test_sfa_dataset(make_scraper) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["resource_id"] == "d_test"
        return httpx.Response(200, json=json.loads(fixture_text("sfa_datastore.json")))

    scraper, _ = make_scraper("sfa", handler=handler, options={"dataset_id": "d_test"})
    result = await scraper.search(CATALOGUE_QUERY)
    grades = {c.name: c.hygiene_grade for c in result.candidates}
    assert grades == {"Keng Eng Kee Seafood": "A", "Tiong Bahru Bakery": "B"}
    assert all(c.confidence == 0.0 and c.extra["enrich_only"] for c in result.candidates)
    assert result.candidates[0].postal_code == "150124"


async def test_sfa_skips_when_unconfigured(make_scraper) -> None:
    scraper, factory = make_scraper("sfa")
    result = await scraper.search(CATALOGUE_QUERY)
    assert (
        result.skipped_reason and "dataset_id" in result.skipped_reason and factory.requests == []
    )


def test_every_scraper_instantiates(make_scraper, app_config) -> None:
    for src in app_config.sources.sources:
        scraper, _ = make_scraper(src.key)
        assert scraper.source.key == src.key
        assert isinstance(scraper.query_driven, bool)


@pytest.mark.parametrize(
    "key", ["chope", "quandoo", "tablecheck", "burpple", "hungrygowhere", "sethlui", "timeout"]
)
def test_search_url_encodes_query(make_scraper, key: str) -> None:
    scraper, _ = make_scraper(key)
    url = scraper.search_url(SearchQuery("c", "dating", "新加坡 约会 咖啡馆 & more", 2))
    assert " " not in url and "&amp;" not in url and "%E6%96%B0" in url


def _unused(*_: Any) -> None:  # keep linters quiet about the Any import in some environments
    return None
