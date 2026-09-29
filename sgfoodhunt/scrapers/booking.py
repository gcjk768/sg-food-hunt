"""Booking platforms: Chope, Quandoo, TableCheck and Google Reserve.

Chope's search is JavaScript rendered (``fetch: browser``). Quandoo and TableCheck search
pages carry JSON-LD restaurant data which is preferred over CSS selectors when present. Every
selector can be overridden from ``sources.yaml`` (``options.selectors``) when a site changes.
"""

from __future__ import annotations

from typing import Any, ClassVar

from sgfoodhunt.models import ScrapeResult, SearchQuery, VenueCandidate
from sgfoodhunt.scrapers.base import BaseScraper
from sgfoodhunt.scrapers.google_places import GooglePlacesScraper
from sgfoodhunt.scrapers.html import Card, CardSelectors, parse_cards, parse_json_ld_restaurants


class CardSearchScraper(BaseScraper):
    """Generic: fetch the search page, parse JSON-LD or cards, one candidate per card."""

    default_selectors: ClassVar[CardSelectors]
    booking_source: ClassVar[bool] = True

    def _selectors(self) -> CardSelectors:
        return CardSelectors.from_options(self.source.options, self.default_selectors)

    def _cards(self, soup, url: str) -> list[Card]:  # type: ignore[no-untyped-def]
        base = self.source.base_url or url
        cards = parse_json_ld_restaurants(soup, base)
        return cards or parse_cards(soup, base, self._selectors())

    async def search(self, query: SearchQuery) -> ScrapeResult:
        result = self.new_result(query)
        url = self.search_url(query)
        fetched = await self.fetch_soup(url, result)
        if fetched is None:
            return result
        soup, page = fetched
        result.pages.append(page)
        cards = self._cards(soup, url)
        if not cards:
            result.warnings.append(f"no results parsed from {url} (selectors may be stale)")
        for card in cards:
            cand = self.candidate(
                card.name,
                source_ref=card.url,
                address=card.address,
                booking_url=card.url if self.booking_source else None,
                website=None if self.booking_source else card.url,
                rating=card.rating,
                review_count=card.review_count,
                price_text=card.price_text,
                cuisine=card.cuisine,
                snippet=card.text,
                page=page,
            )
            if cand:
                result.candidates.append(cand)
        return result


class ChopeScraper(CardSearchScraper):
    key: ClassVar[str] = "chope"
    default_selectors = CardSelectors(
        card="div.restaurant-card, li.restaurant-item, article[data-restaurant-id]",
        name="h3, .restaurant-name, .restaurant-card__name",
        link="a[href*='/singapore-restaurants/']",
        address=".restaurant-address, .restaurant-card__address, .location",
        rating=".rating, .restaurant-card__rating",
        review_count=".reviews, .review-count",
        price=".price, .restaurant-card__price",
        cuisine=".cuisine, .restaurant-card__cuisine",
    )


class QuandooScraper(CardSearchScraper):
    key: ClassVar[str] = "quandoo"
    default_selectors = CardSelectors(
        card="[data-testid='merchant-card'], div.merchant-card, article.merchant",
        name="h3, [data-testid='merchant-card-name'], .merchant-name",
        link="a[href*='/place/']",
        address="[data-testid='merchant-card-location'], .merchant-location, address",
        rating="[data-testid='merchant-card-rating'], .rating",
        review_count="[data-testid='merchant-card-reviews'], .reviews",
        price="[data-testid='merchant-card-price'], .price",
        cuisine="[data-testid='merchant-card-cuisine'], .cuisine",
    )


class TableCheckScraper(CardSearchScraper):
    key: ClassVar[str] = "tablecheck"
    default_selectors = CardSelectors(
        card="div.shop-card, li.shop-list-item, article[data-shop-slug]",
        name="h3, .shop-card__name, .shop-name",
        link="a[href*='/shops/']",
        address=".shop-card__address, .shop-address, address",
        rating=".shop-card__rating, .rating",
        review_count=".shop-card__reviews, .reviews",
        price=".shop-card__budget, .budget",
        cuisine=".shop-card__cuisine, .cuisine",
    )


class GoogleReserveScraper(GooglePlacesScraper):
    """Google Reserve links derived from the Places API ``reservable`` flag.

    Reuses the exact Places request (so it is served from cache after ``google_places`` ran)
    and keeps only reservable places, recording the Google Maps listing as the booking link.
    """

    key: ClassVar[str] = "google_reserve"

    def _options(self) -> dict[str, Any]:
        """Use google_places options unless overridden, so the request (and cache key) match."""
        if self.source.options:
            return self.source.options
        try:
            return self.ctx.config.sources.get("google_places").options
        except KeyError:
            return {}

    def accept(self, candidate: VenueCandidate) -> VenueCandidate | None:
        if not candidate.extra.get("reservable"):
            return None
        candidate.booking_url = candidate.extra.get("google_maps_uri")
        candidate.extra = {"reservable": True, "google_maps_uri": candidate.booking_url}
        candidate.snippet = None
        return candidate
