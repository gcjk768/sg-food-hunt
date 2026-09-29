"""Review aggregators with search pages: Burpple and HungryGoWhere."""

from __future__ import annotations

from typing import ClassVar

from sgfoodhunt.scrapers.booking import CardSearchScraper
from sgfoodhunt.scrapers.html import CardSelectors


class BurppleScraper(CardSearchScraper):
    key: ClassVar[str] = "burpple"
    booking_source = False
    default_selectors = CardSelectors(
        card="div.searchVenue, div.venue-card, a.venue-list-item",
        # 2026-09 markup first (searchVenue-header-*), older class names kept as fallbacks
        name=".searchVenue-header-name-name, .searchVenue-name, .venue-card__name, h3",
        link=".searchVenue-header a[href^='/'], a[href^='/']",
        address=".searchVenue-header-locationDistancePrice-location, .searchVenue-location, "
        ".venue-card__location, .location",
        rating=".searchVenue-rating, .rating",
        review_count=".searchVenue-header-reviews, .searchVenue-reviews, .reviews",
        price=".searchVenue-header-locationDistancePrice-price, .searchVenue-price, .price",
        cuisine=".searchVenue-header-categories, .searchVenue-tags, .tags",
    )


class HungryGoWhereScraper(CardSearchScraper):
    key: ClassVar[str] = "hungrygowhere"
    booking_source = False
    default_selectors = CardSelectors(
        card="div.venue-card, article.restaurant, li.search-result",
        name=".venue-card__title, h3, .restaurant-name",
        link="a[href*='/restaurant/'], a[href*='/dining/']",
        address=".venue-card__address, .address",
        rating=".venue-card__rating, .rating",
        review_count=".venue-card__reviews, .reviews",
        price=".venue-card__price, .price",
        cuisine=".venue-card__cuisine, .cuisine",
    )
