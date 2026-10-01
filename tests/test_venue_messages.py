"""One Telegram message per top pick; blog search keeps only query-relevant food articles."""

import json
from pathlib import Path
from typing import Any

import httpx

from sgfoodhunt.config import load_config
from sgfoodhunt.diff import RunDiff, venue_messages
from sgfoodhunt.notify import esc, html_to_plain, send_telegram, split_message
from sgfoodhunt.scrapers.blogs import relevant_article
from sgfoodhunt.scrapers.html import clean_venue_heading, strip_news_wording
from sgfoodhunt.storage.runs import RunStore

CONFIG = Path(__file__).resolve().parents[1] / "config"


def _write_run(root: Path, run_id: str, scores: dict[str, Any], venues: list[Any]) -> None:
    d = root / "runs" / run_id
    d.mkdir(parents=True)
    (d / "scores.json").write_text(json.dumps(scores), encoding="utf-8")
    (d / "venues.json").write_text(json.dumps(venues), encoding="utf-8")


def test_one_message_per_new_top_pick(tmp_path: Path) -> None:
    config = load_config(CONFIG)
    cat_a, cat_b = [c.key for c in config.categories.categories[:2]]
    venues = [{"id": f"v{i}", "name": f"Venue {i}", "evidence": []} for i in range(5)]

    def s(vid: str, rank: int) -> dict[str, Any]:
        return {"venue_id": vid, "rank": rank, "name": vid}

    _write_run(tmp_path, "r1", {cat_a: [s("v0", 1), s("v1", 2), s("v2", 3), s("v3", 4)]}, venues)
    _write_run(
        tmp_path, "r2", {cat_a: [s("v3", 1), s("v0", 2), s("v1", 3)], cat_b: [s("v0", 1)]}, venues
    )
    store = RunStore(tmp_path)
    first = venue_messages(config, store, RunDiff(run_id="r1", prev_run_id=None))
    assert [m.split("</b>")[0] for m in first] == [f"🍽 <b>Venue {i}" for i in range(3)]
    # next week: only v3 is new to cat_a's top 3, v0 is new to cat_b's
    second = venue_messages(config, store, RunDiff(run_id="r2", prev_run_id="r1"))
    assert sorted(m.split("</b>")[0] for m in second) == ["🍽 <b>Venue 0", "🍽 <b>Venue 3"]
    # a venue entering a long top list (not the top 3) gets a card with its rank too
    from sgfoodhunt.diff import CategoryDiff

    label = config.categories.get(cat_a).display_name
    v4 = {"venue_id": "v4", "name": "Venue 4", "rank": 15}
    entered = CategoryDiff(cat_a, label, entered=[v4])
    third = venue_messages(config, store, RunDiff("r2", "r1", categories=[entered]))
    assert f"🍽 <b>Venue 4</b> · #15 · {esc(label)}" in third


def test_card_keeps_fields_in_order_and_escapes() -> None:
    """the owner's fixed card: name / ranks / address · MRT / facts / summary / link / sources."""
    from sgfoodhunt.diff import _card

    v = {
        "name": "Tom & Jerry's <Bar>",
        "name_zh": "汤姆",
        "address": "1 Raffles Pl #01-01",
        "nearest_mrt": "Raffles Place",
        "cuisine": ["Japanese"],
        "price_text": "$$",
        "ratings": {"google_places": {"rating": 4.5, "review_count": 120}},
        "summary": "LLM says <script>alert(1)</script> & more",
        "booking_url": "https://chope.co/x?a=1&b=2",
        "evidence": [{"url": "https://www.sethlui.com/a"}, {"url": "https://eatbook.sg/b"}],
    }
    card = _card(v, [(4, "Cafes"), (1, "Date night"), (2, "Brunch"), (9, "A"), (7, "B")])
    assert card.splitlines()[0] == (
        "🍽 <b>Tom &amp; Jerry&#x27;s &lt;Bar&gt; (汤姆)</b> · #1 · Date night"
    )
    assert "<script>" not in card and "&lt;script&gt;" in card
    assert 'href="https://chope.co/x?a=1&amp;b=2"' in card
    assert card.endswith("</blockquote>") and "<blockquote expandable>" in card
    plain = html_to_plain(card)
    fields = [
        "Tom & Jerry's <Bar> (汤姆)",
        "#1 · Date night",
        "#2 · Brunch",
        "#4 · Cafes",
        "…and top 3 in 2 more lists",
        "📍 1 Raffles Pl #01-01 · 🚇 near Raffles Place MRT",
        "Japanese · $$ · ★ 4.5 (120)",
        "LLM says <script>alert(1)</script> & more",
        "Book a table (https://chope.co/x?a=1&b=2)",
        "Sources: sethlui.com (https://www.sethlui.com/a)  ·  eatbook.sg (https://eatbook.sg/b)",
    ]
    pos = [plain.index(f) for f in fields]
    assert pos == sorted(pos)


def test_split_message_never_cuts_a_tag() -> None:
    block = "🍽 <b>Name</b> · #1\n📍 <code>addr</code>\n<blockquote expandable>src</blockquote>"
    text = "\n\n".join([block] * 200)
    chunks = split_message(text, limit=500)
    assert len(chunks) > 1 and all(len(c) <= 500 for c in chunks)
    for c in chunks:  # every chunk is whole blocks: tags balanced
        for start, end in (
            ("<b>", "</b>"),
            ("<code>", "</code>"),
            ("<blockquote", "</blockquote>"),
        ):
            assert c.count(start) == c.count(end)
    assert "\n\n".join(chunks) == text
    # one giant line can't be split between tags: it goes as escaped plain text
    huge = split_message("<b>" + "x&" * 600 + "</b>", limit=500)
    assert all(len(c) <= 500 and "<b>" not in c for c in huge)
    assert html_to_plain("".join(huge)).replace("\n", "") == "x&" * 600


def test_html_rejected_resends_as_plain_text() -> None:
    posts: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        posts.append(body)
        if body.get("parse_mode") == "HTML":
            return httpx.Response(
                400, json={"ok": False, "description": "Bad Request: can't parse entities"}
            )
        return httpx.Response(200, json={"ok": True})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    send_telegram("t", "c", '🍽 <b>A &amp; B</b>\n<a href="https://x.sg">Book</a>', client=client)
    assert posts[0]["parse_mode"] == "HTML" and posts[0]["disable_web_page_preview"]
    assert "parse_mode" not in posts[1]
    assert posts[1]["text"] == "🍽 A & B\nBook (https://x.sg)"


def test_telegram_sends_each_message_separately() -> None:
    sent: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        sent.append(json.loads(req.content)["text"])
        return httpx.Response(200, json={"ok": True})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    send_telegram("t", "c", ["a", "b", "c"], client=client, gap_seconds=0)
    assert sent == ["a", "b", "c"]


def test_blog_links_must_match_query_and_be_food() -> None:
    q = "romantic restaurants Singapore"
    assert relevant_article("https://sethlui.com/best-romantic-restaurants-singapore/", q)
    assert not relevant_article("https://sethlui.com/best-cruise-guide-singapore/", q)
    assert not relevant_article("https://eatbook.sg/marymount-bakehouse-upper-thomson/", q)
    assert not relevant_article("https://sethlui.com/phuket-best-restaurants-guide/", q)
    assert not relevant_article(
        "https://sethlui.com/best-restaurants-singapore/", "新加坡 浪漫 餐厅"
    )


def test_heading_news_prefix_stripped() -> None:
    assert clean_venue_heading("New menu: Maggie’s") == "Maggie’s"
    assert clean_venue_heading("New restaurant: Yanhuo Restaurant") == "Yanhuo Restaurant"


def test_diff_handles_null_business_status(tmp_path: Path) -> None:
    """business_status is stored as null; the second run's diff crashed on None.startswith."""
    from sgfoodhunt.diff import compute_diff

    config = load_config(CONFIG)
    config.settings.paths.data_dir = tmp_path
    venue: dict[str, Any] = {
        "id": "v1",
        "name": "A",
        "business_status": None,
        "evidence": [],
        "first_seen_run": "r1",
    }
    for r in ("r1", "r2"):
        _write_run(tmp_path, r, {}, [venue])
        (tmp_path / "runs" / r / "run.json").write_text(
            json.dumps(
                {
                    "run_id": r,
                    "status": "ok",
                    "mode": "collect",
                    "started_at": "x",
                    "categories": [],
                    "sources": [],
                }
            ),
            encoding="utf-8",
        )
    diff = compute_diff(config, RunStore(tmp_path), "r2", prev_run_id="r1")
    assert diff.newly_closed == [] and diff.reopened == []


def test_headline_names_cleaned() -> None:
    assert clean_venue_heading("New outlet: Martina’s Kitchen") == "Martina’s Kitchen"
    assert clean_venue_heading("Molly Tea is opening at Hillion Mall on 9 October") == "Molly Tea"
    assert (
        clean_venue_heading("Marymount Bakehouse opens at Upper Thomson with sourdough")
        == "Marymount Bakehouse"
    )
    assert clean_venue_heading("Opening Hours Cafe") == "Opening Hours Cafe"


def test_stored_names_keep_leading_numbers() -> None:
    from sgfoodhunt.scrapers.html import strip_news_wording

    assert strip_news_wording("99 Old Trees") == "99 Old Trees"
    assert strip_news_wording("54° Steakhouse") == "54° Steakhouse"
    assert strip_news_wording("New dining concept: LingZhi Greens") == "LingZhi Greens"
    assert strip_news_wording("Noci Bakehouse opens second outlet at Orchard Gateway") == (
        "Noci Bakehouse"
    )


def test_query_relevance_is_strict() -> None:
    # a shared generic word ("brunch", "restaurant") is not enough when the query is specific
    assert not relevant_article(
        "https://sethlui.com/best-hotel-buffets-champagne-brunch-singapore/",
        "weekend dim sum brunch Singapore",
    )
    assert relevant_article(
        "https://sethlui.com/best-dim-sum-brunch-singapore/", "weekend dim sum brunch Singapore"
    )
    assert not relevant_article(
        "https://thehoneycombers.com/singapore/best-steak-restaurant-in-singapore/",
        "new restaurants Singapore 2026",
    )
    assert relevant_article(
        "https://thehoneycombers.com/singapore/new-restaurants-menus-singapore/",
        "new restaurants Singapore 2026",
    )
    assert relevant_article(
        "https://sethlui.com/most-instagrammable-cafes-singapore/", "instagrammable cafe Singapore"
    )


def test_stale_blog_evidence_pruned(tmp_path: Path) -> None:
    from sgfoodhunt.dedup.registry import VenueRegistry, prune_blog_evidence
    from sgfoodhunt.dedup.venue import Evidence, Venue

    config = load_config(CONFIG)
    reg = VenueRegistry(tmp_path / "venues.json")
    sidebar = Evidence(
        "sethlui",
        "https://sethlui.com/best-mooncakes-singapore-2026/",
        category_keys=["hawker_family", "zichar_family"],
        queries=["best hawker centres Singapore", "best zi char Singapore"],
    )
    real = Evidence(
        "sethlui",
        "https://sethlui.com/best-hawker-centres-food-guide-singapore/",
        category_keys=["hawker_family", "cafes_date"],
        queries=["best hawker centres Singapore", "romantic cafes Singapore"],
    )
    reg.venues["v1"] = Venue(id="v1", name="X", evidence=[sidebar, real])
    assert prune_blog_evidence(reg, config) == 1
    [ev] = reg.venues["v1"].evidence
    assert ev.queries == ["best hawker centres Singapore"] and ev.category_keys == ["hawker_family"]
    assert strip_news_wording("ION Orchard Food Opera reopens") == "ION Orchard Food Opera"


def test_month_headings_are_not_venues() -> None:
    from sgfoodhunt.models import is_date_only

    for bad in ["July 2026", "February 2026", "12 Feb", "2026", "September"]:
        assert is_date_only(bad), bad
    for ok in ["Marina 2026", "1880", "Summer Hill", "May Cafe", "54° Steakhouse"]:
        assert not is_date_only(ok), ok
