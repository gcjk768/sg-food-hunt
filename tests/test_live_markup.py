"""Regressions for site markup observed live on 2026-09-29 (trimmed from real pages)."""

from bs4 import BeautifulSoup

from sgfoodhunt.scrapers.aggregators import BurppleScraper
from sgfoodhunt.scrapers.booking import ChopeScraper
from sgfoodhunt.scrapers.html import extract_listicle_entries, looks_like_venue_entry, parse_cards

BURPPLE = """<div class="searchVenue card feed-item"><div class="searchVenue-header card-item">
<a href="/keng-eng-kee-seafood?bp_ref=%2Fsearch%2Fsg"><div class="searchVenue-header-left">
<span class="searchVenue-header-name-name headingMedium">KEK Keng Eng Kee Seafood (Alexandra)</span>
<span class="searchVenue-header-reviews">404 Reviews</span>
<div class="searchVenue-header-locationDistancePrice">
<span class="searchVenue-header-locationDistancePrice-location">Alexandra</span>
<span class="searchVenue-header-locationDistancePrice-price">~$15/pax</span></div>
<span class="searchVenue-header-categories">Chinese, Seafood, Zi Char</span></div></a></div></div>"""

CHOPE = """<ul><li class="card_container search-result-scribe"><div class="card_container_n">
<div class="card_info_box"><div class="content_fir"><a class="res_name"
href="/singapore-restaurants/restaurant/madame">Madame</a></div>
<div class="content_sec">3.8 (120 reviews)</div>
<ul class="content_thi"><li><span class="price"><b class="select-price">$$$</b>$$</span></li>
<li>Bar</li><li>French</li><li>Duxton</li></ul></div></div></li></ul>"""

SETHLUI = """<div class="elementor-widget-theme-post-content"><div class="elementor-widget-container">
<p>Long review text about the dim sum and the bao.</p>
<h3>Lian Bang Fu Zhou: #01-42, Blk 643 Bukit Batok Central, Singapore 650643 | Tel: +65 8866 9140 |
Opening hours: 6am – 8pm (Daily)</h3></div></div>"""


def test_burpple_2026_cards() -> None:
    soup = BeautifulSoup(BURPPLE, "lxml")
    [card] = parse_cards(soup, "https://www.burpple.com", BurppleScraper.default_selectors)
    assert card.name == "KEK Keng Eng Kee Seafood (Alexandra)"
    assert card.url and card.url.startswith("https://www.burpple.com/keng-eng-kee-seafood")
    assert card.review_count == 404 and card.address == "Alexandra" and "Zi Char" in card.cuisine


def test_chope_2026_cards() -> None:
    soup = BeautifulSoup(CHOPE, "lxml")
    [card] = parse_cards(soup, "https://www.chope.co", ChopeScraper.default_selectors)
    assert card.name == "Madame" and card.rating == 3.8 and card.price_text == "$$$"
    assert card.cuisine == ["Bar"] and card.address == "Duxton"
    assert card.url == "https://www.chope.co/singapore-restaurants/restaurant/madame"


def test_single_venue_info_heading() -> None:
    body = BeautifulSoup(SETHLUI, "lxml").select_one(".elementor-widget-theme-post-content")
    assert body is not None
    [entry] = extract_listicle_entries(body)
    assert entry.name == "Lian Bang Fu Zhou" and entry.postal_code == "650643"
    assert looks_like_venue_entry(entry)


def test_blog_ignores_offsite_links(make_scraper) -> None:  # type: ignore[no-untyped-def]
    scraper, _ = make_scraper("misstamchiak", search_url="https://www.misstamchiak.com/?s={query}")
    soup = BeautifulSoup(
        '<a href="https://www.facebook.com/misstamchiak">fb</a>'
        '<a href="https://www.misstamchiak.com/best-zi-char-singapore/">post</a>',
        "lxml",
    )
    links = scraper.article_links(soup, "https://www.misstamchiak.com/?s=zi+char")
    assert links == ["https://www.misstamchiak.com/best-zi-char-singapore/"]
