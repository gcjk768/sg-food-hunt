"""Stage one orchestrator: run every enabled source for every category query and store raw
sightings on disk. Later stages (dedup, scoring, enrichment, social, reporting) plug in after
``collect``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from sgfoodhunt.ai import AiClient, build_ai_client
from sgfoodhunt.config import AppConfig, Category, Source
from sgfoodhunt.http.cache import ResponseCache
from sgfoodhunt.http.client import AsyncApiClient, PoliteClient
from sgfoodhunt.models import ScrapeResult, SearchQuery
from sgfoodhunt.scrapers import BaseScraper, ScraperContext, build_scraper
from sgfoodhunt.storage.runs import RunRecord, RunStore

log = logging.getLogger(__name__)

CATALOGUE_QUERY = SearchQuery(category_key="*", category_group="*", text="catalogue", party_size=0)


@dataclass(slots=True)
class CollectStats:
    sources_run: list[str] = field(default_factory=list)
    sources_skipped: dict[str, str] = field(default_factory=dict)
    queries: int = 0
    candidates: int = 0
    candidates_by_source: dict[str, int] = field(default_factory=dict)
    pages: int = 0
    requests: int = 0
    cache_hits: int = 0
    warnings: int = 0
    errors: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "sources_run": self.sources_run,
            "sources_skipped": self.sources_skipped,
            "queries": self.queries,
            "candidates": self.candidates,
            "candidates_by_source": self.candidates_by_source,
            "pages": self.pages,
            "requests": self.requests,
            "cache_hits": self.cache_hits,
            "warnings": self.warnings,
            "errors": self.errors,
        }


def select_categories(config: AppConfig, keys: Iterable[str] | None) -> list[Category]:
    wanted = list(keys or [])
    if not wanted:
        return list(config.categories.categories)
    return [config.categories.get(k) for k in wanted]


def select_sources(
    config: AppConfig, keys: Iterable[str] | None
) -> tuple[list[Source], dict[str, str]]:
    """Return (runnable sources, {key: reason skipped})."""
    wanted = list(keys or [])
    pool = [config.sources.get(k) for k in wanted] if wanted else list(config.sources.sources)
    runnable: list[Source] = []
    skipped: dict[str, str] = {}
    for src in pool:
        if src.tos_status == "disallowed":
            skipped[src.key] = "tos_status is disallowed"
        elif not src.enabled:
            skipped[src.key] = "disabled in sources.yaml"
        else:
            runnable.append(src)
    return runnable, skipped


def queries_for(category: Category) -> list[SearchQuery]:
    return [
        SearchQuery(
            category_key=category.key,
            category_group=category.group,
            text=q,
            party_size=category.party_size,
        )
        for q in category.queries
    ]


class Collector:
    def __init__(
        self,
        config: AppConfig,
        store: RunStore,
        cache: ResponseCache,
        dry_run: bool = False,
        concurrency: int = 4,
        http: PoliteClient | None = None,
        api_factory: Any | None = None,
        ai: AiClient | None = None,
    ) -> None:
        self.config = config
        self.store = store
        self.cache = cache
        self.dry_run = dry_run
        self.concurrency = concurrency
        self.http = http or PoliteClient(config.settings.http, cache, dry_run=dry_run)
        self._api_factory = api_factory or self._default_api_factory
        self.ai = (
            ai if ai is not None else build_ai_client(config.settings.ai, cache, offline=dry_run)
        )
        self.stats = CollectStats()

    def _default_api_factory(self, name: str, headers: dict[str, str] | None) -> AsyncApiClient:
        rpm = self.config.settings.api_rate_limits.get(name, 60)
        return AsyncApiClient(
            self.config.settings.http,
            self.cache,
            rpm=rpm,
            dry_run=self.dry_run,
            default_headers=headers,
        )

    def scraper_for(self, source: Source) -> BaseScraper:
        ctx = ScraperContext(
            config=self.config,
            source=source,
            http=self.http,
            api_factory=self._api_factory,
            dry_run=self.dry_run,
            ai=self.ai,
        )
        return build_scraper(ctx)

    async def _run_source(self, run: RunRecord, source: Source, categories: list[Category]) -> None:
        scraper = self.scraper_for(source)
        missing = scraper.missing_credentials()
        if missing:
            self.stats.sources_skipped[source.key] = missing
            self.store.log_event(run.run_id, "warning", source.key, f"skipped: {missing}")
            log.warning("%s skipped: %s", source.key, missing)
            return
        self.stats.sources_run.append(source.key)
        if scraper.query_driven:
            queries = [q for cat in categories for q in queries_for(cat) if scraper.applies_to(q)]
        else:
            queries = [CATALOGUE_QUERY]
        for query in queries:
            self.stats.queries += 1
            try:
                result = await scraper.search(query)
            except Exception as exc:  # one bad query must not kill the run
                self.stats.errors += 1
                log.exception("%s failed on %r", source.key, query.text)
                self.store.log_event(
                    run.run_id,
                    "error",
                    source.key,
                    f"{type(exc).__name__}: {exc}",
                    {"query": query.text, "category": query.category_key},
                )
                continue
            self._record(run, result)
            if result.skipped_reason and "robots" in result.skipped_reason:
                self.stats.sources_skipped[source.key] = result.skipped_reason
                break

    def _record(self, run: RunRecord, result: ScrapeResult) -> None:
        self.store.append_result(run.run_id, result)
        n = len(result.candidates)
        self.stats.candidates += n
        self.stats.candidates_by_source[result.source_key] = (
            self.stats.candidates_by_source.get(result.source_key, 0) + n
        )
        self.stats.pages += len(result.pages)
        self.stats.requests += result.requests_made
        self.stats.cache_hits += result.cache_hits
        self.stats.warnings += len(result.warnings)
        log.info(
            "%-16s %-28s %3d candidates  %2d pages  %2d req  %2d cached%s",
            result.source_key,
            result.query.text[:28],
            n,
            len(result.pages),
            result.requests_made,
            result.cache_hits,
            f"  [{result.skipped_reason}]" if result.skipped_reason else "",
        )

    async def collect(
        self, category_keys: Iterable[str] | None = None, source_keys: Iterable[str] | None = None
    ) -> RunRecord:
        categories = select_categories(self.config, category_keys)
        sources, skipped = select_sources(self.config, source_keys)
        self.stats.sources_skipped.update(skipped)
        run = self.store.create_run(
            mode="dry_run" if self.dry_run else "collect",
            categories=[c.key for c in categories],
            sources=[s.key for s in sources],
        )
        log.info(
            "run %s: %d categories, %d sources%s",
            run.run_id,
            len(categories),
            len(sources),
            " (dry run, cache only)" if self.dry_run else "",
        )
        for key, reason in skipped.items():
            self.store.log_event(run.run_id, "info", key, f"not run: {reason}")
        sem = asyncio.Semaphore(self.concurrency)

        async def guarded(src: Source) -> None:
            async with sem:
                await self._run_source(run, src, categories)

        await asyncio.gather(*(guarded(s) for s in sources))
        stats = self.stats.as_dict()
        stats["http"] = self.http.stats.as_dict()
        stats["cache"] = self.cache.stats()
        if self.ai is not None:
            stats["ai"] = self.ai.stats.as_dict()
        status = "ok" if self.stats.errors == 0 else "partial"
        self.store.finish_run(run, status, stats)
        log.info(
            "run %s finished: %s, %d candidates from %d sources",
            run.run_id,
            status,
            self.stats.candidates,
            len(self.stats.sources_run),
        )
        return run
