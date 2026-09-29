"""Polite HTTP clients.

``PoliteClient`` wraps ``requests`` for static pages: robots.txt, per domain delay, cache, retries.
``AsyncApiClient`` wraps ``httpx.AsyncClient`` for official APIs: RPM limiting, cache, retries.
Both honour dry run mode, where only cached responses are served.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Callable, MutableMapping
from datetime import timedelta
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx
import requests

from sgfoodhunt.config import HttpSettings
from sgfoodhunt.http.cache import CachedResponse, CacheMiss, ResponseCache, cache_key
from sgfoodhunt.http.ratelimit import AsyncRpmLimiter, DomainRateLimiter
from sgfoodhunt.http.robots import RobotsChecker

log = logging.getLogger(__name__)

RETRY_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})


class SessionLike(Protocol):
    """The subset of ``requests.Session`` the polite client uses (injectable in tests)."""

    @property
    def headers(self) -> MutableMapping[str, str | bytes]: ...

    def get(self, url: str | bytes, *, headers: Any = None, timeout: Any = None) -> Any: ...


class FetchError(RuntimeError):
    def __init__(self, url: str, status: int | None, message: str) -> None:
        super().__init__(f"{message} ({url}, status={status})")
        self.url = url
        self.status = status


class RobotsDisallowed(FetchError):
    def __init__(self, url: str) -> None:
        super().__init__(url, None, "disallowed by robots.txt")


class FetchStats:
    def __init__(self) -> None:
        self.requests = 0
        self.cache_hits = 0
        self.retries = 0
        self.robots_denied = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "requests": self.requests,
            "cache_hits": self.cache_hits,
            "retries": self.retries,
            "robots_denied": self.robots_denied,
        }


def backoff_seconds(attempt: int, base: float, cap: float) -> float:
    return float(min(cap, base * (2**attempt)) * random.uniform(0.8, 1.2))


class PoliteClient:
    """Synchronous client for static HTML pages."""

    def __init__(
        self,
        settings: HttpSettings,
        cache: ResponseCache,
        dry_run: bool = False,
        session: SessionLike | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.settings = settings
        self.cache = cache
        self.dry_run = dry_run
        self.session: SessionLike = session or requests.Session()
        self.session.headers.update(
            {"User-Agent": settings.user_agent, "Accept-Language": "en-SG,en;q=0.9,zh;q=0.8"}
        )
        self._sleep = sleep
        self.limiter = DomainRateLimiter(
            settings.min_delay_seconds, settings.max_delay_seconds, sleep=sleep
        )
        self.robots = RobotsChecker(
            cache,
            self._fetch_robots,
            settings.user_agent,
            timedelta(hours=settings.robots_cache_ttl_hours),
            enabled=settings.respect_robots,
        )
        self.stats = FetchStats()

    def _fetch_robots(self, url: str) -> tuple[int, str]:
        if self.dry_run:
            return 404, ""  # no restrictions can be evaluated; nothing is fetched anyway
        self.limiter.wait(urlsplit(url).netloc)
        resp = self.session.get(url, timeout=self.settings.timeout_seconds)
        self.stats.requests += 1
        return resp.status_code, resp.text if resp.status_code < 400 else ""

    def get(
        self,
        url: str,
        ttl: timedelta | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> CachedResponse:
        if params:
            url = requests.Request("GET", url, params=params).prepare().url or url
        key = cache_key("GET", url)
        cached = self.cache.get(key)
        if cached is not None:
            self.stats.cache_hits += 1
            return cached
        if self.dry_run:
            raise CacheMiss(url)
        if not self.robots.allowed(url):
            self.stats.robots_denied += 1
            raise RobotsDisallowed(url)
        ttl = ttl or timedelta(hours=self.settings.default_cache_ttl_hours)
        host = urlsplit(url).netloc
        last_exc: Exception | None = None
        for attempt in range(self.settings.max_retries + 1):
            self.limiter.wait(host)
            crawl_delay = self.robots.crawl_delay(url)
            if crawl_delay and crawl_delay > self.settings.min_delay_seconds:
                self._sleep(crawl_delay - self.settings.min_delay_seconds)
            try:
                resp = self.session.get(url, headers=headers, timeout=self.settings.timeout_seconds)
                self.stats.requests += 1
            except requests.RequestException as exc:
                last_exc = exc
                log.warning("GET %s failed (%s), attempt %d", url, exc, attempt + 1)
            else:
                if resp.status_code in RETRY_STATUSES:
                    last_exc = FetchError(url, resp.status_code, "retryable status")
                    log.warning("GET %s -> %s, attempt %d", url, resp.status_code, attempt + 1)
                else:
                    if resp.status_code >= 400:
                        raise FetchError(url, resp.status_code, "request failed")
                    body = resp.content
                    ctype = resp.headers.get("Content-Type")
                    self.cache.put(key, url, "GET", resp.status_code, body, ctype, ttl)
                    return CachedResponse(
                        url=url,
                        status=resp.status_code,
                        body=body,
                        content_type=ctype,
                        fetched_at=self.cache.get(key).fetched_at  # type: ignore[union-attr]
                        if self.cache.get(key)
                        else _now(),
                        from_cache=False,
                    )
            if attempt < self.settings.max_retries:
                self.stats.retries += 1
                self._sleep(
                    backoff_seconds(
                        attempt,
                        self.settings.backoff_base_seconds,
                        self.settings.backoff_max_seconds,
                    )
                )
        raise FetchError(url, getattr(last_exc, "status", None), f"gave up: {last_exc}")


def _now() -> Any:
    from datetime import UTC, datetime

    return datetime.now(UTC)


class AsyncApiClient:
    """httpx based client for JSON APIs with RPM limiting, caching and retries."""

    def __init__(
        self,
        settings: HttpSettings,
        cache: ResponseCache,
        rpm: int = 60,
        dry_run: bool = False,
        client: httpx.AsyncClient | None = None,
        default_headers: dict[str, str] | None = None,
    ) -> None:
        self.settings = settings
        self.cache = cache
        self.dry_run = dry_run
        headers = {"User-Agent": settings.user_agent}
        headers.update(default_headers or {})
        self._client = client or httpx.AsyncClient(
            headers=headers, timeout=settings.timeout_seconds
        )
        self._owns_client = client is None
        self._headers = headers
        self.limiter = AsyncRpmLimiter(rpm)
        self.stats = FetchStats()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def request_json(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        data: dict[str, Any] | None = None,
        auth: tuple[str, str] | None = None,
        ttl: timedelta | None = None,
        cacheable: bool = True,
        cache_salt: str = "",
    ) -> tuple[Any, bool]:
        """Return (parsed JSON, from_cache)."""
        import json as _json

        req = self._client.build_request(
            method, url, params=params, json=json_body, headers=headers, data=data
        )
        body_for_key = (
            _json.dumps(json_body, sort_keys=True)
            if json_body
            else (_json.dumps(data) if data else "")
        ) + cache_salt
        key = cache_key(method, str(req.url), body_for_key)
        if cacheable:
            cached = self.cache.get(key)
            if cached is not None:
                self.stats.cache_hits += 1
                return _json.loads(cached.text), True
        if self.dry_run:
            raise CacheMiss(str(req.url))
        ttl = ttl or timedelta(hours=self.settings.default_cache_ttl_hours)
        last_exc: Exception | None = None
        for attempt in range(self.settings.max_retries + 1):
            await self.limiter.acquire()
            try:
                resp = await self._client.send(req, auth=auth)
                self.stats.requests += 1
            except httpx.HTTPError as exc:
                last_exc = exc
                log.warning("%s %s failed (%s), attempt %d", method, url, exc, attempt + 1)
            else:
                if resp.status_code in RETRY_STATUSES:
                    last_exc = FetchError(url, resp.status_code, "retryable status")
                    log.warning(
                        "%s %s -> %s, attempt %d", method, url, resp.status_code, attempt + 1
                    )
                else:
                    if resp.status_code >= 400:
                        raise FetchError(url, resp.status_code, resp.text[:200])
                    payload = resp.json()
                    if cacheable:
                        self.cache.put(
                            key,
                            str(req.url),
                            method,
                            resp.status_code,
                            resp.content,
                            resp.headers.get("Content-Type"),
                            ttl,
                        )
                    return payload, False
            if attempt < self.settings.max_retries:
                self.stats.retries += 1
                await asyncio.sleep(
                    backoff_seconds(
                        attempt,
                        self.settings.backoff_base_seconds,
                        self.settings.backoff_max_seconds,
                    )
                )
            req = self._client.build_request(
                method, url, params=params, json=json_body, headers=headers, data=data
            )
        raise FetchError(url, getattr(last_exc, "status", None), f"gave up: {last_exc}")
