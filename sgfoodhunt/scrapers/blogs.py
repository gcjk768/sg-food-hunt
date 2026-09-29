"""Food blogs and media: search the site, open the matching listicles, extract one venue per
heading. Adding a similar WordPress style blog needs only a small subclass (or just config
overrides via ``options``: ``article_pattern``, ``content_selectors``, ``max_articles``).
"""

from __future__ import annotations

import re
from typing import ClassVar
from urllib.parse import urlsplit

from sgfoodhunt.ai.tasks import extract_listicle
from sgfoodhunt.models import ScrapeResult, SearchQuery
from sgfoodhunt.scrapers.base import BaseScraper
from sgfoodhunt.scrapers.html import (
    absolute_links,
    extract_listicle_entries,
    looks_like_venue_entry,
)

#: an article slug must name food to be opened; keeps out cruise/tour/hotel/"things to do" guides
FOOD_SLUG_RE = re.compile(
    r"(?:^|[-/])(?:food|foodie|eat|eats|eatery|eateries|restaurant|cafe|coffee|dining|dine|dinner|"
    r"lunch|buffet|hawker|brunch|dim-sum|dimsum|steamboat|hotpot|hot-pot|zi-char|zichar|dessert|"
    r"high-tea|tea|bakery|bakeries|bakehouse|omakase|menu|steak|steakhouse|sushi|ramen|bbq|"
    r"seafood|noodle|laksa|bistro|brasserie|kitchen|cuisine|halal|vegetarian|pizza|burger|"
    r"dumpling|kopitiam|bar|bars|supper|ice-cream|gelato|korean|japanese|chinese|thai|italian|"
    r"indian|western)s?(?=[-/]|$)"
)
#: guides to other cities (same blogs cover JB, Bali, Phuket...)
OVERSEAS_SLUG_RE = re.compile(
    r"(?:^|[-/])(?:johor|jb|malaysia|kuala-lumpur|kl|penang|melaka|malacca|batam|bintan|bali|"
    r"seminyak|phuket|bangkok|thailand|hong-kong|taipei|taiwan|tokyo|osaka|japan|seoul|korea)"
    r"(?=[-/]|$)"
)
#: query words too generic to show an article matches the query
_GENERIC_WORDS = frozenset(
    [
        "best",
        "top",
        "singapore",
        "sg",
        "for",
        "a",
        "an",
        "the",
        "in",
        "of",
        "and",
        "with",
        "to",
        "near",
        "new",
        "places",
        "spots",
        "guide",
        "date",
        "night",
        "couples",
        "couple",
        "family",
        "families",
        "kids",
        "weekend",
        "instagrammable",
        "tiktok",
        "viral",
    ]
)


def _stem(word: str) -> str:
    return word[:-1] if len(word) > 3 and word.endswith("s") else word


def relevant_article(url: str, query: str) -> bool:
    """A search page also links sidebar/"latest" posts that ignore the query; those came back for
    every query and put the same unrelated article in every category. Keep a link only when its
    slug names food and shares a meaningful word with the query."""
    slug = urlsplit(url).path.lower()
    if not FOOD_SLUG_RE.search(slug) or OVERSEAS_SLUG_RE.search(slug):
        return False
    words = {_stem(w) for w in re.findall(r"[a-z]+", query.lower())} - _GENERIC_WORDS
    if not words:  # a Chinese query on an English blog: its search can't have matched anything
        return False
    slug_words = {_stem(w) for w in re.findall(r"[a-z]+", slug)}
    return bool(words & slug_words) or any(w in slug for w in words if len(w) > 4)


DEFAULT_CONTENT_SELECTORS = (
    "article .entry-content",
    "div.entry-content",
    "div.post-content",
    "div.article-content",
    "div.td-post-content",
    ".elementor-widget-theme-post-content",  # Elementor sites (e.g. Seth Lui) have no <article>
    "article",
    "main",
)


class ListicleBlogScraper(BaseScraper):
    key: ClassVar[str] = ""
    #: regex on the URL path identifying an article on this site
    article_pattern: ClassVar[str] = r"^/[a-z0-9-]{12,}/?$"
    content_selectors: ClassVar[tuple[str, ...]] = DEFAULT_CONTENT_SELECTORS
    #: CSS selector for the search results area; limits link discovery so nav/footer links that
    #: happen to match ``article_pattern`` are not opened. None = whole page.
    results_selector: ClassVar[str | None] = None
    heading_tags: ClassVar[tuple[str, ...]] = ("h2", "h3")
    max_articles: ClassVar[int] = 5
    #: articles whose title matches none of these words are still opened; this only orders them
    relevance_words: ClassVar[tuple[str, ...]] = (
        "best",
        "top",
        "guide",
        "places",
        "cafes",
        "restaurants",
    )

    def _article_re(self) -> re.Pattern[str]:
        return re.compile(str(self.source.options.get("article_pattern", self.article_pattern)))

    def _content_selectors(self) -> tuple[str, ...]:
        raw = self.source.options.get("content_selectors")
        return tuple(raw) if raw else self.content_selectors

    def _max_articles(self) -> int:
        return int(self.source.options.get("max_articles", self.max_articles))

    def article_links(self, soup, search_url: str, query: str = "") -> list[str]:  # type: ignore[no-untyped-def]
        scope = self.source.options.get("results_selector", self.results_selector)
        roots = (soup.select(str(scope)) if scope else []) or [soup]
        links = list(
            dict.fromkeys(
                u for r in roots for u in absolute_links(r, search_url, self._article_re())
            )
        )
        # Same site only: an off-site profile link (facebook.com/<blog>) can match article_pattern,
        # and a robots.txt refusal there used to skip the whole source for the rest of the run.
        host = urlsplit(search_url).netloc
        links = [
            u
            for u in links
            if urlsplit(u).netloc == host
            and u.rstrip("/") != search_url.rstrip("/")
            and relevant_article(u, query)
        ]

        # Prefer listicle looking URLs; keep document order otherwise.
        def score(url: str) -> int:
            path = url.lower()
            return -sum(1 for w in self.relevance_words if w in path)

        return sorted(links, key=score)[: self._max_articles()]

    async def search(self, query: SearchQuery) -> ScrapeResult:
        result = self.new_result(query)
        search_url = self.search_url(query)
        fetched = await self.fetch_soup(search_url, result)
        if fetched is None:
            return result
        soup, page = fetched
        result.pages.append(page)
        links = self.article_links(soup, search_url, query.text)
        if not links:
            result.warnings.append(f"no article links found on {search_url}")
        for url in links:
            if result.skipped_reason:
                break
            article = await self.fetch_soup(url, result)
            if article is None:
                continue
            asoup, apage = article
            result.pages.append(apage)
            container = None
            for selector in self._content_selectors():
                container = asoup.select_one(selector)
                if container is not None:
                    break
            if container is None:
                result.warnings.append(f"no content container in {url}")
                continue
            entries = extract_listicle_entries(container, self.heading_tags)
            venue_entries = [e for e in entries if looks_like_venue_entry(e)]
            ai = self.ctx.ai
            if not venue_entries and ai is not None and ai.task_enabled("article_extraction"):
                found = extract_listicle(ai, apage.title, container.get_text(" ", strip=True))
                if found:
                    for ev in found:
                        cand = self.candidate(
                            ev.name,
                            source_ref=f"{url}#{ev.name}",
                            name_zh=ev.name_zh,
                            address=ev.address,
                            price_text=ev.price,
                            opening_hours={"text": ev.hours} if ev.hours else None,
                            snippet=ev.note,
                            confidence=max(0.5, min(1.0, ev.confidence)),
                            page=apage,
                            extra={
                                "article_title": apage.title,
                                "published_at": apage.published_at,
                                "area": ev.area,
                                "extracted_by": "ai",
                            },
                        )
                        if cand:
                            result.candidates.append(cand)
                    continue
            if entries and not venue_entries:
                result.warnings.append(f"{len(entries)} headings but no venue details in {url}")
            for entry in venue_entries:
                website = next(
                    (
                        link
                        for link in entry.links
                        if not re.search(r"(maps\.|goo\.gl|instagram|facebook|tiktok)", link)
                    ),
                    None,
                )
                cand = self.candidate(
                    entry.name,
                    source_ref=f"{url}#{entry.name}",
                    name_zh=entry.name_zh,
                    address=entry.address,
                    postal_code=entry.postal_code,
                    phone=entry.phone,
                    website=website,
                    price_text=entry.price_text,
                    opening_hours={"text": entry.hours} if entry.hours else None,
                    snippet=entry.snippet(),
                    page=apage,
                    extra={"article_title": apage.title, "published_at": apage.published_at},
                )
                if cand:
                    result.candidates.append(cand)
        return result


class SethLuiScraper(ListicleBlogScraper):
    key = "sethlui"
    article_pattern = r"^/[a-z0-9-]+-singapore/?$|^/[a-z0-9-]{15,}/?$"
    results_selector = ".e-loop-item"


class DanielFoodDiaryScraper(ListicleBlogScraper):
    key = "danielfooddiary"
    article_pattern = r"^/\d{4}/\d{2}/\d{2}/[a-z0-9-]+/?$"


class MissTamChiakScraper(ListicleBlogScraper):
    key = "misstamchiak"
    article_pattern = r"^/[a-z0-9-]{10,}/?$"


class TimeOutScraper(ListicleBlogScraper):
    key = "timeout"
    article_pattern = (
        r"^/singapore/(?:restaurants|bars|things-to-do|food-and-drink|cafes)/[a-z0-9-]+/?$"
    )
    content_selectors = ("div[data-testid='article-body']", "article", "main")


class HoneycombersScraper(ListicleBlogScraper):
    key = "honeycombers"
    article_pattern = r"^/singapore/[a-z0-9-]{10,}/?$"


class TheSmartLocalScraper(ListicleBlogScraper):
    key = "thesmartlocal"
    article_pattern = r"^/read/[a-z0-9-]+/?$"


class SassyMamaScraper(ListicleBlogScraper):
    key = "sassymama"
    article_pattern = r"^/[a-z0-9-]{10,}/?$"


class TatlerDiningScraper(ListicleBlogScraper):
    key = "tatler"
    article_pattern = r"^/dining/[a-z0-9-]+/[a-z0-9-]+/?$"
    content_selectors = ("div.article-body", "article", "main")


class EatbookScraper(ListicleBlogScraper):
    key = "eatbook"
    article_pattern = r"^/[a-z0-9-]{10,}/?$"
