"""Optional Playwright fetcher for JavaScript heavy pages.

Only used when a source has ``fetch: browser``. Playwright is an optional dependency
(``pip install -e .[browser]`` then ``playwright install chromium``). The fetcher shares the
robots.txt check, polite delay and cache of the ``PoliteClient`` it is given.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from urllib.parse import urlsplit

from sgfoodhunt.http.cache import CachedResponse, CacheMiss, cache_key
from sgfoodhunt.http.client import PoliteClient, RobotsDisallowed

log = logging.getLogger(__name__)


class BrowserUnavailable(RuntimeError):
    pass


class BrowserFetcher:
    def __init__(self, client: PoliteClient, wait_selector: str | None = None) -> None:
        self.client = client
        self.wait_selector = wait_selector

    def get(self, url: str, ttl: timedelta | None = None) -> CachedResponse:
        key = cache_key("GET", url, "browser")
        cached = self.client.cache.get(key)
        if cached is not None:
            self.client.stats.cache_hits += 1
            return cached
        if self.client.dry_run:
            raise CacheMiss(url)
        if not self.client.robots.allowed(url):
            self.client.stats.robots_denied += 1
            raise RobotsDisallowed(url)
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - depends on optional install
            raise BrowserUnavailable(
                "playwright is not installed; "
                "pip install -e .[browser] && playwright install chromium"
            ) from exc
        self.client.limiter.wait(urlsplit(url).netloc)
        ttl = ttl or timedelta(hours=self.client.settings.default_cache_ttl_hours)
        with sync_playwright() as pw:  # pragma: no cover - requires a browser
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(user_agent=self.client.settings.user_agent)
            page.goto(
                url, wait_until="networkidle", timeout=self.client.settings.timeout_seconds * 1000
            )
            if self.wait_selector:
                page.wait_for_selector(self.wait_selector, timeout=15000)
            html = page.content()
            browser.close()
        self.client.stats.requests += 1
        body = html.encode("utf-8")
        self.client.cache.put(key, url, "GET", 200, body, "text/html", ttl)
        from datetime import UTC, datetime

        return CachedResponse(url, 200, body, "text/html", datetime.now(UTC), from_cache=False)
