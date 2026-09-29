"""TripAdvisor search results. Disabled by default: TripAdvisor's terms prohibit automated access
without a Content API licence. The orchestrator refuses to run any source whose ``tos_status`` is
``disallowed`` regardless of the ``enabled`` flag, so this only runs after you set both.
"""

from __future__ import annotations

from typing import ClassVar

from sgfoodhunt.scrapers.booking import CardSearchScraper
from sgfoodhunt.scrapers.html import CardSelectors


class TripAdvisorScraper(CardSearchScraper):
    key: ClassVar[str] = "tripadvisor"
    booking_source = False
    default_selectors = CardSelectors(
        card="div[data-test-attribute='location-results'] div.result, div.result-card",
        name=".result-title, h3",
        link="a[href*='Restaurant_Review']",
        address=".address, .result-address",
        rating=".ui_bubble_rating, .rating",
        review_count=".review_count, .reviews",
        price=".price",
        cuisine=".cuisine, .tags",
    )
