"""Shared scraper interface.

Every source implements :class:`BaseScraper`. The orchestrator calls ``search`` once per query
for query driven sources, or once per run for catalogue sources (``query_driven = False``) such
as the Michelin listing and SFA data.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import ClassVar
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from sgfoodhunt.config import AppConfig, Source
from sgfoodhunt.http.browser import BrowserFetcher, BrowserUnavailable
from sgfoodhunt.http.cache import CachedResponse, CacheMiss
from sgfoodhunt.http.client import AsyncApiClient, FetchError, PoliteClient, RobotsDisallowed
from sgfoodhunt.models import ScrapeResult, SearchQuery, SourcePage, VenueCandidate
from sgfoodhunt.scrapers.html import article_published_at, content_hash, page_title, soup_of

log = logging.getLogger(__name__)

ApiClientFactory = Callable[[str, dict[str, str] | None], AsyncApiClient]


@dataclass(slots=True)
class ScraperContext:
    config: AppConfig
    source: Source
    http: PoliteClient
    api_factory: ApiClientFactory
    dry_run: bool = False
    browser: BrowserFetcher | None = None

    @property
    def ttl(self) -> timedelta:
        hours = self.source.cache_ttl_hours or self.config.settings.http.default_cache_ttl_hours
        return timedelta(hours=hours)


class BaseScraper(ABC):
    key: ClassVar[str] = ""
    query_driven: ClassVar[bool] = True
    #: Sources that only make sense for some category groups can declare them in config
    #: (``options.groups``); the orchestrator consults :meth:`applies_to`.

    def __init__(self, ctx: ScraperContext) -> None:
        self.ctx = ctx
        self.source = ctx.source
        self.http = ctx.http
        self.log = logging.getLogger(f"sgfoodhunt.scrapers.{self.source.key}")

    # -- interface ---------------------------------------------------------------------------
    @abstractmethod
    async def search(self, query: SearchQuery) -> ScrapeResult:
        """Return every venue that appears for ``query`` in this source."""

    def applies_to(self, query: SearchQuery) -> bool:
        groups = self.source.options.get("groups")
        return not groups or query.category_group in groups

    def missing_credentials(self) -> str | None:
        """Return a message when a required secret is absent; the source is then skipped."""
        return None

    # -- helpers -----------------------------------------------------------------------------
    def new_result(self, query: SearchQuery) -> ScrapeResult:
        return ScrapeResult(source_key=self.source.key, query=query)

    def search_url(self, query: SearchQuery) -> str:
        if not self.source.search_url:
            raise ValueError(f"source {self.source.key} has no search_url")
        return self.source.search_url.format(query=quote_plus(query.text))

    async def fetch(self, url: str, result: ScrapeResult) -> CachedResponse | None:
        """Fetch a static page (or via browser when ``fetch: browser``), recording outcomes.

        Returns ``None`` when the page could not be fetched; the reason is recorded on ``result``.
        """
        fetcher: Callable[[str], CachedResponse]
        if self.source.fetch == "browser":
            browser = self.ctx.browser or BrowserFetcher(self.http)
            fetcher = lambda u: browser.get(u, self.ctx.ttl)  # noqa: E731
        else:
            fetcher = lambda u: self.http.get(u, ttl=self.ctx.ttl)  # noqa: E731
        try:
            resp = await asyncio.to_thread(fetcher, url)
        except CacheMiss:
            result.warnings.append(f"dry run: not cached {url}")
            return None
        except RobotsDisallowed:
            result.skipped_reason = f"robots.txt disallows {url}"
            self.log.warning(result.skipped_reason)
            return None
        except BrowserUnavailable as exc:
            result.skipped_reason = str(exc)
            self.log.warning(result.skipped_reason)
            return None
        except FetchError as exc:
            result.warnings.append(str(exc))
            self.log.warning("fetch failed: %s", exc)
            return None
        if resp.from_cache:
            result.cache_hits += 1
        else:
            result.requests_made += 1
        return resp

    async def fetch_soup(
        self, url: str, result: ScrapeResult
    ) -> tuple[BeautifulSoup, SourcePage] | None:
        resp = await self.fetch(url, result)
        if resp is None:
            return None
        soup = soup_of(resp.body)
        page = SourcePage(
            source_key=self.source.key,
            url=url,
            title=page_title(soup),
            published_at=article_published_at(soup),
            content_hash=content_hash(soup.get_text(" ", strip=True)),
        )
        return soup, page

    def candidate(self, name: str, **kwargs: object) -> VenueCandidate | None:
        """Build a candidate, returning None (and logging) if the name is unusable."""
        try:
            return VenueCandidate(source_key=self.source.key, name=name, **kwargs)  # type: ignore[arg-type]
        except ValueError as exc:
            self.log.debug("dropped candidate: %s", exc)
            return None
