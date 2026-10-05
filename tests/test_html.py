from __future__ import annotations

import re

from sgfoodhunt.scrapers.html import (
    CardSelectors,
    absolute_links,
    article_published_at,
    clean_venue_heading,
    extract_listicle_entries,
    looks_like_venue_entry,
    page_title,
    parse_cards,
    parse_json_ld_restaurants,
    soup_of,
    split_chinese_name,
)
from tests.conftest import fixture_text


def test_listicle_extraction() -> None:
    soup = soup_of(fixture_text("blog_article.html"))
    container = soup.select_one("div.entry-content")
    assert container is not None
    entries = extract_listicle_entries(container)
    names = [e.name for e in entries]
    assert names == [
        "Tiong Bahru Bakery",
        "Keng Eng Kee Seafood",
        "Editor's pick",
        "Some Cafe Without Details",
    ]
    tbb, kek = entries[0], entries[1]
    assert tbb.postal_code == "160056" and tbb.phone == "<PHONE>"
    assert tbb.hours == "7.30am to 8pm daily"
    assert "https://www.tiongbahrubakery.com" in tbb.links
    assert kek.name_zh == "琼荣记海鲜" and kek.postal_code == "150124" and kek.price_text == "$$"
    venues = [e for e in entries if looks_like_venue_entry(e)]
    assert [e.name for e in venues] == ["Tiong Bahru Bakery", "Keng Eng Kee Seafood"]


def test_article_metadata() -> None:
    soup = soup_of(fixture_text("blog_article.html"))
    assert article_published_at(soup) == "2026-03-14"
    assert page_title(soup) == "10 Best Romantic Cafes In Singapore For Your Next Date"


def test_clean_venue_heading() -> None:
    assert clean_venue_heading("#3. Luna Rooftop – best views in town") == "Luna Rooftop"
    assert clean_venue_heading("12) Kok Sen (Closed)") == "Kok Sen"
    assert clean_venue_heading("  Ah   Hua  ") == "Ah Hua"


def test_split_chinese_name() -> None:
    assert split_chinese_name("Keng Eng Kee (琼荣记)") == ("Keng Eng Kee", "琼荣记")
    assert split_chinese_name("Kok Sen 国成") == ("Kok Sen", "国成")
    assert split_chinese_name("Plain Name") == ("Plain Name", None)


def test_absolute_links_dedup_and_pattern() -> None:
    soup = soup_of(fixture_text("blog_search.html"))
    links = absolute_links(
        soup, "https://example-blog.test/?s=x", re.compile(r"^/[a-z0-9-]{12,}/?$")
    )
    assert links == [
        "https://example-blog.test/best-romantic-cafes-singapore/",
        "https://example-blog.test/hawker-guide-to-tiong-bahru/",
    ]


def test_parse_cards_with_selectors() -> None:
    soup = soup_of(fixture_text("chope_search.html"))
    sel = CardSelectors(
        card="div.restaurant-card",
        name="h3",
        link="a",
        address=".restaurant-address",
        rating=".rating",
        review_count=".reviews",
        price=".price",
        cuisine=".cuisine",
    )
    cards = parse_cards(soup, "https://www.chope.co/x", sel)
    assert [c.name for c in cards] == ["Sky Garden Grill", "Mum's Kitchen 妈妈厨房"]
    first = cards[0]
    assert first.url == "https://www.chope.co/singapore-restaurants/sky-garden-grill"
    assert first.rating == 4.5 and first.review_count == 1203 and first.price_text == "$$$$"
    assert first.cuisine == ["Western", "Grill"]


def test_parse_json_ld_restaurants() -> None:
    soup = soup_of(fixture_text("quandoo_search.html"))
    cards = parse_json_ld_restaurants(soup, "https://www.quandoo.sg/en/result")
    assert [c.name for c in cards] == ["Luna Rooftop", "Ah Hua Zi Char"]
    luna = cards[0]
    assert luna.url == "https://www.quandoo.sg/en/place/luna-rooftop-12345"
    assert luna.address == "1 Fullerton Road, Singapore, 049213"
    assert luna.rating == 4.6 and luna.review_count == 812 and luna.cuisine == ["Italian"]
    assert cards[1].cuisine == ["Chinese", "Seafood"]
