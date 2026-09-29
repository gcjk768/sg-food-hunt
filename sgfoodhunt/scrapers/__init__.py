"""Scraper registry: maps the ``scraper`` name in sources.yaml to a class."""

from __future__ import annotations

from sgfoodhunt.scrapers.aggregators import BurppleScraper, HungryGoWhereScraper
from sgfoodhunt.scrapers.base import BaseScraper, ScraperContext
from sgfoodhunt.scrapers.blogs import (
    DanielFoodDiaryScraper,
    EatbookScraper,
    HoneycombersScraper,
    MissTamChiakScraper,
    SassyMamaScraper,
    SethLuiScraper,
    TatlerDiningScraper,
    TheSmartLocalScraper,
    TimeOutScraper,
)
from sgfoodhunt.scrapers.booking import (
    ChopeScraper,
    GoogleReserveScraper,
    QuandooScraper,
    TableCheckScraper,
)
from sgfoodhunt.scrapers.google_places import GooglePlacesScraper
from sgfoodhunt.scrapers.michelin import MichelinScraper
from sgfoodhunt.scrapers.reddit import RedditScraper
from sgfoodhunt.scrapers.sfa import SfaHygieneScraper
from sgfoodhunt.scrapers.tripadvisor import TripAdvisorScraper

REGISTRY: dict[str, type[BaseScraper]] = {
    cls.__name__: cls
    for cls in (
        GooglePlacesScraper,
        GoogleReserveScraper,
        ChopeScraper,
        QuandooScraper,
        TableCheckScraper,
        BurppleScraper,
        HungryGoWhereScraper,
        EatbookScraper,
        SethLuiScraper,
        DanielFoodDiaryScraper,
        MissTamChiakScraper,
        TimeOutScraper,
        HoneycombersScraper,
        TheSmartLocalScraper,
        SassyMamaScraper,
        TatlerDiningScraper,
        MichelinScraper,
        RedditScraper,
        SfaHygieneScraper,
        TripAdvisorScraper,
    )
}


def build_scraper(ctx: ScraperContext) -> BaseScraper:
    try:
        cls = REGISTRY[ctx.source.scraper]
    except KeyError as exc:
        raise KeyError(
            f"source {ctx.source.key!r} names unknown scraper {ctx.source.scraper!r}; "
            f"known: {sorted(REGISTRY)}"
        ) from exc
    return cls(ctx)


__all__ = ["REGISTRY", "BaseScraper", "ScraperContext", "build_scraper"]
