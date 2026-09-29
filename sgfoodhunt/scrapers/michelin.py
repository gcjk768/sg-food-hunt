"""Michelin Guide Singapore selection (stars, Bib Gourmand, selected restaurants).

Catalogue source: crawls the paginated Singapore listing once per run rather than per query.
"""

from __future__ import annotations

import re
from typing import ClassVar

from bs4 import Tag

from sgfoodhunt.models import ScrapeResult, SearchQuery
from sgfoodhunt.scrapers.base import BaseScraper
from sgfoodhunt.scrapers.html import parse_json_ld_restaurants

STAR_WORDS = {"one": 1, "two": 2, "three": 3, "1": 1, "2": 2, "3": 3}


def distinction_from_text(text: str) -> str:
    low = text.lower()
    if "bib gourmand" in low:
        return "Bib Gourmand"
    match = re.search(r"(one|two|three|[123])\s+(?:michelin\s+)?stars?", low)
    if match:
        n = STAR_WORDS[match.group(1)]
        return f"{n} Star{'s' if n > 1 else ''}"
    if "green star" in low:
        return "Green Star"
    return "Selected"


class MichelinScraper(BaseScraper):
    key: ClassVar[str] = "michelin"
    query_driven: ClassVar[bool] = False

    card_selector: ClassVar[str] = (
        "div.card__menu, div.js-restaurant__list_item, article.restaurant-card"
    )
    name_selector: ClassVar[str] = "h3.card__menu-content--title, h3, .card__menu-content--title"
    location_selector: ClassVar[str] = (
        ".card__menu-footer--location, .card__menu-footer--score, .location"
    )
    award_selector: ClassVar[str] = ".card__menu-content--classification, .distinction, .award"

    def _listing_url(self, page: int) -> str:
        base = str(
            self.source.options.get(
                "listing_url", "https://guide.michelin.com/sg/en/selection/singapore/restaurants"
            )
        )
        return base if page == 1 else f"{base.rstrip('/')}/page/{page}"

    def _parse_cards(self, soup: Tag, url: str, result: ScrapeResult, page) -> int:  # type: ignore[no-untyped-def]
        count = 0
        json_ld = {c.name.lower(): c for c in parse_json_ld_restaurants(soup, url)}
        for card in soup.select(self.card_selector):
            name_node = card.select_one(self.name_selector)
            if name_node is None:
                continue
            name = name_node.get_text(" ", strip=True)
            link = name_node.find("a", href=True) or card.find("a", href=True)
            href = (
                f"https://guide.michelin.com{link['href']}"
                if isinstance(link, Tag) and str(link["href"]).startswith("/")
                else (str(link["href"]) if isinstance(link, Tag) else None)
            )
            location = card.select_one(self.location_selector)
            award_node = card.select_one(self.award_selector)
            award_text = award_node.get_text(" ", strip=True) if award_node else ""
            star_icons = len(card.select("img[alt*='tar'], .michelin-award, .fa-michelin"))
            if star_icons and "star" not in award_text.lower() and "bib" not in award_text.lower():
                award_text = f"{star_icons} stars"
            jl = json_ld.get(name.lower())
            cand = self.candidate(
                name,
                source_ref=href,
                website=href,
                address=(jl.address if jl else None)
                or (location.get_text(" ", strip=True) if location else None),
                cuisine=jl.cuisine if jl else [],
                price_text=jl.price_text if jl else None,
                michelin=distinction_from_text(award_text or card.get_text(" ", strip=True)),
                snippet=card.get_text(" ", strip=True),
                page=page,
                extra={"guide_url": href},
            )
            if cand:
                result.candidates.append(cand)
                count += 1
        return count

    async def search(self, query: SearchQuery) -> ScrapeResult:
        result = self.new_result(query)
        max_pages = int(self.source.options.get("max_pages", 20))
        for n in range(1, max_pages + 1):
            url = self._listing_url(n)
            fetched = await self.fetch_soup(url, result)
            if fetched is None:
                break
            soup, page = fetched
            result.pages.append(page)
            if self._parse_cards(soup, url, result, page) == 0:
                if n == 1:
                    result.warnings.append(f"no restaurant cards parsed from {url}")
                break
        return result
