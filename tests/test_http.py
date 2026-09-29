from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import httpx
import pytest

from sgfoodhunt.config import HttpSettings
from sgfoodhunt.http.cache import CacheMiss, ResponseCache, cache_key
from sgfoodhunt.http.client import AsyncApiClient, FetchError, PoliteClient, RobotsDisallowed
from sgfoodhunt.http.ratelimit import DomainRateLimiter
from sgfoodhunt.http.robots import RobotsChecker
from tests.conftest import FakeResponse, FakeSession


def test_cache_roundtrip_and_expiry(cache: ResponseCache) -> None:
    key = cache_key("GET", "https://a.test/x")
    assert cache.get(key) is None
    cache.put(key, "https://a.test/x", "GET", 200, b"<html>", "text/html", timedelta(hours=1))
    got = cache.get(key)
    assert got is not None and got.text == "<html>" and got.from_cache
    cache.put(key, "https://a.test/x", "GET", 200, b"old", None, timedelta(seconds=-1))
    assert cache.get(key) is None
    assert cache.purge_expired() == 1
    assert cache.stats() == {"entries": 0, "live": 0, "robots": 0}


def test_cache_key_includes_body() -> None:
    assert cache_key("POST", "u", "a") != cache_key("POST", "u", "b")
    assert cache_key("GET", "u") == cache_key("get", "u")


def test_cache_is_plain_files(cache: ResponseCache) -> None:
    key = cache_key("GET", "https://a.test/x")
    cache.put(key, "https://a.test/x", "GET", 200, b"body", None, timedelta(hours=1))
    files = list(cache.dir.rglob("*"))
    assert any(f.suffix == ".body" for f in files) and any(
        f.name.endswith(".meta.json") for f in files
    )


def test_rate_limiter_waits_between_same_host_calls() -> None:
    slept: list[float] = []
    limiter = DomainRateLimiter(2.0, 2.0, sleep=slept.append)
    limiter.wait("a.test")
    limiter.wait("b.test")  # different host: no wait
    limiter.wait("a.test")  # same host: must wait close to 2s
    assert len(slept) == 1 and 1.9 < slept[0] <= 2.0


def test_robots_checker_uses_cache_and_rules(cache: ResponseCache) -> None:
    calls: list[str] = []

    def fetcher(url: str) -> tuple[int, str]:
        calls.append(url)
        return 200, "User-agent: *\nDisallow: /private/\nCrawl-delay: 4\n"

    rc = RobotsChecker(cache, fetcher, "sgfoodhunt/0.1 (+x)", timedelta(hours=1))
    assert rc.allowed("https://a.test/public") is True
    assert rc.allowed("https://a.test/private/x") is False
    assert rc.crawl_delay("https://a.test/") == 4.0
    rc2 = RobotsChecker(cache, fetcher, "sgfoodhunt/0.1 (+x)", timedelta(hours=1))
    assert rc2.allowed("https://a.test/private/y") is False
    assert calls == ["https://a.test/robots.txt"]  # second checker served from cache


def test_robots_403_means_disallow_all(cache: ResponseCache) -> None:
    rc = RobotsChecker(cache, lambda _u: (403, ""), "ua", timedelta(hours=1))
    assert rc.allowed("https://b.test/anything") is False


def test_robots_404_means_allow(cache: ResponseCache) -> None:
    rc = RobotsChecker(cache, lambda _u: (404, ""), "ua", timedelta(hours=1))
    assert rc.allowed("https://c.test/anything") is True


def test_robots_disabled(cache: ResponseCache) -> None:
    rc = RobotsChecker(
        cache,
        lambda _u: (200, "User-agent: *\nDisallow: /"),
        "ua",
        timedelta(hours=1),
        enabled=False,
    )
    assert rc.allowed("https://d.test/") is True


def test_polite_client_caches_and_sets_user_agent(
    polite_client: PoliteClient, fake_session: FakeSession
) -> None:
    fake_session.add("https://example-blog.test/page", "<p>hi</p>")
    r1 = polite_client.get("https://example-blog.test/page")
    r2 = polite_client.get("https://example-blog.test/page")
    assert r1.text == "<p>hi</p>" and not r1.from_cache and r2.from_cache
    assert fake_session.calls.count("https://example-blog.test/page") == 1
    assert str(fake_session.headers["User-Agent"]).startswith("sgfoodhunt-test")
    assert polite_client.stats.requests == 2  # robots + page
    assert polite_client.stats.cache_hits == 1


def test_polite_client_respects_robots(
    polite_client: PoliteClient, fake_session: FakeSession
) -> None:
    fake_session.add("https://example-blog.test/private/x", "secret")
    with pytest.raises(RobotsDisallowed):
        polite_client.get("https://example-blog.test/private/x")
    assert "https://example-blog.test/private/x" not in fake_session.calls
    assert polite_client.stats.robots_denied == 1


def test_polite_client_retries_then_succeeds(
    polite_client: PoliteClient, fake_session: FakeSession
) -> None:
    fake_session.add_sequence(
        "https://example-blog.test/flaky", [FakeResponse(503, b"x"), FakeResponse(200, b"ok")]
    )
    assert polite_client.get("https://example-blog.test/flaky").text == "ok"
    assert polite_client.stats.retries == 1


def test_polite_client_gives_up(polite_client: PoliteClient, fake_session: FakeSession) -> None:
    fake_session.add("https://example-blog.test/down", "x", status=502)
    with pytest.raises(FetchError):
        polite_client.get("https://example-blog.test/down")
    assert fake_session.calls.count("https://example-blog.test/down") == 3  # 1 + 2 retries


def test_polite_client_404_is_not_retried(
    polite_client: PoliteClient, fake_session: FakeSession
) -> None:
    fake_session.add("https://example-blog.test/missing", "x", status=404)
    with pytest.raises(FetchError):
        polite_client.get("https://example-blog.test/missing")
    assert fake_session.calls.count("https://example-blog.test/missing") == 1


def test_dry_run_serves_cache_only(
    http_settings: HttpSettings, cache: ResponseCache, fake_session: FakeSession
) -> None:
    fake_session.add("https://example-blog.test/page", "cached")
    live = PoliteClient(http_settings, cache, session=fake_session, sleep=lambda _s: None)
    live.get("https://example-blog.test/page")
    dry = PoliteClient(
        http_settings, cache, dry_run=True, session=fake_session, sleep=lambda _s: None
    )
    assert dry.get("https://example-blog.test/page").text == "cached"
    with pytest.raises(CacheMiss):
        dry.get("https://example-blog.test/other")
    assert "https://example-blog.test/other" not in fake_session.calls


async def test_async_api_client_caches_and_retries(
    http_settings: HttpSettings, cache: ResponseCache
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, json={"err": "slow down"})
        return httpx.Response(200, json={"ok": calls, "body": request.content.decode()})

    client = AsyncApiClient(
        http_settings,
        cache,
        rpm=1000,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    payload, cached = await client.request_json("POST", "https://api.test/x", json_body={"q": 1})
    assert payload["ok"] == 2 and not cached and client.stats.retries == 1
    payload2, cached2 = await client.request_json("POST", "https://api.test/x", json_body={"q": 1})
    assert cached2 and payload2 == payload
    payload3, cached3 = await client.request_json("POST", "https://api.test/x", json_body={"q": 2})
    assert not cached3 and payload3["ok"] == 3
    await client.aclose()


async def test_async_api_client_dry_run(http_settings: HttpSettings, cache: ResponseCache) -> None:
    client = AsyncApiClient(
        http_settings,
        cache,
        dry_run=True,
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json={}))
        ),
    )
    with pytest.raises(CacheMiss):
        await client.request_json("GET", "https://api.test/never")


async def test_async_api_client_non_retryable_error(
    http_settings: HttpSettings, cache: ResponseCache, tmp_path: Path
) -> None:
    client = AsyncApiClient(
        http_settings,
        cache,
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(403, text="nope"))
        ),
    )
    with pytest.raises(FetchError) as exc:
        await client.request_json("GET", "https://api.test/forbidden")
    assert exc.value.status == 403
