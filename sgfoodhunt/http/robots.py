"""robots.txt lookup with caching."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import timedelta
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

from sgfoodhunt.http.cache import ResponseCache

log = logging.getLogger(__name__)

RobotsFetcher = Callable[[str], tuple[int, str]]
"""Given a robots.txt URL, returns (status, body). Injected so tests never touch the network."""


class RobotsChecker:
    def __init__(
        self,
        cache: ResponseCache,
        fetcher: RobotsFetcher,
        user_agent: str,
        ttl: timedelta,
        enabled: bool = True,
    ) -> None:
        self._cache = cache
        self._fetch = fetcher
        self._ua = user_agent
        self._ttl = ttl
        self._enabled = enabled
        self._parsers: dict[str, RobotFileParser | None] = {}

    @staticmethod
    def host_of(url: str) -> str:
        parts = urlsplit(url)
        return f"{parts.scheme}://{parts.netloc}"

    def _parser_for(self, host: str) -> RobotFileParser | None:
        if host in self._parsers:
            return self._parsers[host]
        cached = self._cache.get_robots(host)
        if cached is None:
            robots_url = f"{host}/robots.txt"
            try:
                status, body = self._fetch(robots_url)
            except Exception as exc:  # network failure: treat as allow-all but log
                log.warning("robots.txt fetch failed for %s: %s", host, exc)
                status, body = 599, ""
            self._cache.put_robots(host, body, status, self._ttl)
        else:
            body, status = cached
        parser: RobotFileParser | None
        if status in (401, 403):
            # Per RFC 9309 an access denied robots.txt means the whole site is disallowed.
            parser = RobotFileParser()
            parser.parse(["User-agent: *", "Disallow: /"])
        elif status >= 400:
            parser = None  # not found or unreachable: no restrictions
        else:
            parser = RobotFileParser()
            parser.parse(body.splitlines())
        self._parsers[host] = parser
        return parser

    def allowed(self, url: str) -> bool:
        if not self._enabled:
            return True
        parser = self._parser_for(self.host_of(url))
        if parser is None:
            return True
        # Check both our product token and the wildcard; the most specific rule wins in the parser.
        token = self._ua.split("/")[0].split(" ")[0]
        return parser.can_fetch(token, url)

    def crawl_delay(self, url: str) -> float | None:
        parser = self._parser_for(self.host_of(url))
        if parser is None:
            return None
        token = self._ua.split("/")[0].split(" ")[0]
        delay = parser.crawl_delay(token)
        return float(delay) if delay is not None else None
