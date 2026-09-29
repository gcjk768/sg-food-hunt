from __future__ import annotations

import json
import shutil
from datetime import date
from pathlib import Path

import httpx

from sgfoodhunt.config import AppConfig, SocialSettings
from sgfoodhunt.dedup import Evidence, Venue
from sgfoodhunt.http.cache import ResponseCache
from sgfoodhunt.http.client import AsyncApiClient
from sgfoodhunt.social import (
    SocialMention,
    SocialStore,
    VenueMatcher,
    buzz_scores,
    parse_instagram_export,
    parse_tiktok_export,
    run_social,
    secondhand_mentions,
)
from sgfoodhunt.social.hashtags import InstagramHashtagClient
from sgfoodhunt.social.oembed import TikTokOEmbed
from sgfoodhunt.social.serp import SerpClient
from tests.conftest import FIXTURES, fixture_text

VENUES = {
    "v1": Venue(id="v1", name="Tiong Bahru Bakery", aliases=["TBB"]),
    "v2": Venue(id="v2", name="Keng Eng Kee Seafood", aliases=["KEK Seafood"], name_zh="琼荣记"),
    "v3": Venue(id="v3", name="Luna Rooftop"),
    "v4": Venue(id="v4", name="Odette"),
}


def test_parse_instagram_export() -> None:
    mentions = parse_instagram_export(FIXTURES / "instagram_export")
    urls = sorted((m.url, m.creator_handle, m.query) for m in mentions)
    assert ("https://www.instagram.com/p/C9abcDEF123/", "tiongbahrubakery", "saved") in urls
    assert (
        "https://www.instagram.com/reel/C8xyzREEL/",
        "kengengkeeseafood",
        "saved",
    ) in urls  # query string stripped
    assert ("https://www.instagram.com/p/C6liked1/", "lunarooftopsg", "liked") in urls
    assert len(mentions) == 5 and all(
        m.platform == "instagram" and m.caption is None for m in mentions
    )
    saved = next(m for m in mentions if m.creator_handle == "tiongbahrubakery")
    assert saved.post_date == "2026-09-01"
    assert parse_instagram_export(FIXTURES / "nope") == []


def test_parse_tiktok_export() -> None:
    mentions = parse_tiktok_export(FIXTURES / "tiktok_export")
    assert [(m.url, m.post_date, m.query) for m in mentions] == [
        ("https://www.tiktokv.com/share/video/7400000000000000001/", "2026-08-15", "favourites"),
        ("https://www.tiktok.com/@foodiesg/video/7400000000000000002", "2026-06-01", "favourites"),
        ("https://www.tiktok.com/@someone/video/7400000000000000003", "2026-08-20", "liked"),
    ]  # browsing history is ignored


def test_matcher_by_handle_and_caption() -> None:
    matcher = VenueMatcher(VENUES)
    hit = matcher.by_handle("tiongbahrubakery")
    assert hit and hit.venue_id == "v1" and hit.method == "handle"
    hit = matcher.by_handle("kengengkeeseafood_official")
    assert hit and hit.venue_id == "v2"
    assert matcher.by_handle("randomtravelblog") is None
    hit = matcher.by_caption(
        "Best zi char?? Keng Eng Kee moonlight hor fun is unreal #sgfood #kengengkee"
    )
    assert hit and hit.venue_id == "v2" and hit.method == "caption"
    hit = matcher.by_caption("dinner at luna rooftop tonight")
    assert hit and hit.venue_id == "v3"
    assert matcher.by_caption("random selfie #sgcafe") is None
    assert matcher.match(None, None) is None
    cands = matcher.candidates("lunar rooftops are nice", None)
    assert cands and cands[0]["venue_id"] == "v3"


def test_store_upsert_and_manual(tmp_path: Path) -> None:
    store = SocialStore(tmp_path)
    m1 = SocialMention("ig_export", "instagram", "https://ig/p/1", creator_handle="tbb")
    m2 = SocialMention("tiktok_export", "tiktok", "https://tt/v/2")
    assert store.upsert([m1, m2]) == (2, 0)
    m2b = SocialMention(
        "tiktok_export",
        "tiktok",
        "https://tt/v/2",
        caption="now with caption",
        venue_id="v2",
        matched_by="caption",
    )
    assert store.upsert([m2b, m1]) == (0, 1)
    loaded = {m.url: m for m in store.load()}
    assert (
        loaded["https://tt/v/2"].caption == "now with caption"
        and loaded["https://tt/v/2"].venue_id == "v2"
    )
    store.log_unmatched("r1", m1, [{"venue_id": "v1", "score": 70}])
    assert store.manual_resolutions() == {}
    row = json.loads(store.unmatched_path.read_text().splitlines()[0])
    row["resolved_venue_id"] = "v1"
    store.unmatched_path.write_text(json.dumps(row) + "\n")
    assert store.manual_resolutions() == {"https://ig/p/1": "v1"}


def test_buzz_scores() -> None:
    s = SocialSettings(
        enabled=True, buzz_window_months=6, buzz_half_life_days=60, trending_threshold=3
    )
    today = date(2026, 9, 15)
    mentions = [
        SocialMention("ig_export", "instagram", "u1", venue_id="v1", post_date="2026-09-10"),
        SocialMention("serp", "instagram", "u2", venue_id="v1", post_date="2026-08-01"),
        SocialMention("tiktok_export", "tiktok", "u3", venue_id="v1", post_date="2026-07-01"),
        SocialMention(
            "tiktok_export", "tiktok", "u3", venue_id="v1", post_date="2026-07-01"
        ),  # duplicate url
        SocialMention(
            "ig_export", "instagram", "u4", venue_id="v2", post_date="2025-01-01"
        ),  # outside window
        SocialMention(
            "ig_export", "instagram", "u5", venue_id="v2", post_date=None
        ),  # undated counts a little
        SocialMention("ig_export", "instagram", "u6", venue_id=None),
    ]
    buzz = buzz_scores(mentions, s, today)
    assert buzz["v1"].mentions_window == 3 and buzz["v1"].trending and buzz["v1"].score == 1.0
    assert buzz["v1"].latest == "2026-09-10"
    assert (
        buzz["v2"].mentions_window == 1 and not buzz["v2"].trending and 0 < buzz["v2"].score < 0.2
    )
    assert "v3" not in buzz


def test_secondhand_mentions() -> None:
    v = Venue(
        id="v",
        name="X",
        review_snippets=[{"snippet": "Saw it on TikTok, very insta-worthy"}],
        evidence=[Evidence("sethlui", "u", title="viral cafes", snippet="went viral on Instagram")],
    )
    assert secondhand_mentions(v) == 5


def _social_config(app_config: AppConfig, tmp_path: Path, **secrets: str) -> AppConfig:
    app_config.settings.social.enabled = True
    app_config.settings.social.exports_dir = tmp_path / "social_exports"
    app_config.settings.social.hashtags = ["sgcafe"]
    shutil.copytree(FIXTURES / "instagram_export", tmp_path / "social_exports" / "instagram")
    shutil.copytree(FIXTURES / "tiktok_export", tmp_path / "social_exports" / "tiktok")
    for k, v in secrets.items():
        setattr(app_config.secrets, k, v)
    return app_config


def _mock_client(http_settings, cache: ResponseCache, handler) -> AsyncApiClient:  # type: ignore[no-untyped-def]
    return AsyncApiClient(
        http_settings,
        cache,
        rpm=1000,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


async def test_run_social_all_routes(
    app_config: AppConfig, tmp_path: Path, http_settings, cache: ResponseCache
) -> None:
    config = _social_config(app_config, tmp_path)
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        calls.append(url)
        if "tiktok.com/oembed" in url:
            if "7400000000000000002" in url:
                return httpx.Response(200, json=json.loads(fixture_text("tiktok_oembed.json")))
            return httpx.Response(404, text="not found")
        if "serpapi" in url:
            assert (
                "site:instagram.com" in request.url.params["q"]
                or "site:tiktok.com" in request.url.params["q"]
            )
            return httpx.Response(
                200,
                json=json.loads(fixture_text("serp_instagram.json"))
                if "instagram" in request.url.params["q"]
                else {"organic_results": []},
            )
        if "ig_hashtag_search" in url:
            return httpx.Response(200, json=json.loads(fixture_text("ig_hashtag_search.json")))
        if "recent_media" in url:
            return httpx.Response(200, json=json.loads(fixture_text("ig_hashtag_media.json")))
        return httpx.Response(404)

    venues = {
        k: Venue(id=v.id, name=v.name, aliases=list(v.aliases), name_zh=v.name_zh)
        for k, v in VENUES.items()
    }
    venues["v1"].review_snippets = [{"snippet": "went viral on tiktok"}]
    stats = await run_social(
        config,
        cache,
        venues,
        "r1",
        serp_client=SerpClient(_mock_client(http_settings, cache, handler), "key"),
        oembed=TikTokOEmbed(_mock_client(http_settings, cache, handler)),
        hashtag_client=InstagramHashtagClient(
            _mock_client(http_settings, cache, handler), "tok", "123", tmp_path / "hashtag_log.json"
        ),
    )
    assert stats.enabled and stats.ig_export == 5 and stats.tiktok_export == 3
    assert stats.oembed_filled == 1 and stats.hashtag == 2
    assert stats.serp > 0
    store = SocialStore(config.settings.paths.data_dir)
    stored = store.load()
    by_url = {m.url: m for m in stored}
    assert (
        by_url["https://www.instagram.com/p/C9abcDEF123/"].venue_id == "v1"
        and by_url["https://www.instagram.com/p/C9abcDEF123/"].matched_by == "handle"
    )
    assert by_url["https://www.tiktok.com/@foodiesg/video/7400000000000000002"].venue_id == "v2"
    assert (
        by_url["https://www.tiktok.com/@foodiesg/video/7400000000000000002"].creator_handle
        == "foodiesg"
    )
    assert (
        by_url["https://www.instagram.com/p/HASH1/"].venue_id == "v3"
        and by_url["https://www.instagram.com/p/HASH1/"].hashtag == "sgcafe"
    )
    assert by_url["https://www.instagram.com/p/C7unknown/"].matched_by == "unmatched"
    unmatched = [json.loads(line) for line in store.unmatched_path.read_text().splitlines()]
    assert any(u["url"] == "https://www.instagram.com/p/HASH2/" for u in unmatched)
    assert (
        venues["v1"].social_secondhand == 2
        and venues["v1"].buzz_score > 0
        and venues["v1"].social_mentions_window >= 2
    )
    assert venues["v1"].recent_social and venues["v1"].recent_social[0]["url"]
    assert (tmp_path / "hashtag_log.json").exists()
    assert not any("instagram.com/p/" in c for c in calls)  # post pages are never fetched
    # commenter / viewer data never stored
    dumped = store.mentions_path.read_text()
    assert "comment" not in dumped.lower() and "viewer" not in dumped.lower()
    # second run: exports only, no network
    calls.clear()
    stats2 = await run_social(config, cache, venues, "r2", exports_only=True)
    assert calls == [] and stats2.matched >= 2
    assert (
        venues["v2"].recent_social[0]["url"].startswith("https://www.tiktok.com/@foodiesg")
    )  # caption reused from store


async def test_run_social_disabled(app_config: AppConfig, cache: ResponseCache) -> None:
    stats = await run_social(app_config, cache, {}, "r1")
    assert not stats.enabled and "social.enabled is false" in stats.skipped


async def test_hashtag_weekly_cap(tmp_path: Path, http_settings, cache: ResponseCache) -> None:
    log_path = tmp_path / "hashtag_log.json"
    import datetime as dt

    recent = {f"tag{i}": dt.datetime.now(dt.UTC).isoformat() for i in range(25)}
    log_path.write_text(json.dumps(recent))
    client = InstagramHashtagClient(
        _mock_client(http_settings, cache, lambda r: httpx.Response(200, json={"data": []})),
        "t",
        "b",
        log_path,
    )
    assert await client.recent_media("newtag") == [] and client.skipped_cap == 1
    assert (
        await client.recent_media("tag3") == [] and client.skipped_cap == 1
    )  # already counted this week
