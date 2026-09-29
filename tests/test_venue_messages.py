"""One Telegram message per top pick; blog search keeps only query-relevant food articles."""

import json
from pathlib import Path

import httpx

from sgfoodhunt.config import load_config
from sgfoodhunt.diff import RunDiff, venue_messages
from sgfoodhunt.notify import send_telegram
from sgfoodhunt.scrapers.blogs import relevant_article
from sgfoodhunt.scrapers.html import clean_venue_heading, strip_news_wording
from sgfoodhunt.storage.runs import RunStore

CONFIG = Path(__file__).resolve().parents[1] / "config"


def _write_run(root: Path, run_id: str, scores: dict, venues: list) -> None:
    d = root / "runs" / run_id
    d.mkdir(parents=True)
    (d / "scores.json").write_text(json.dumps(scores), encoding="utf-8")
    (d / "venues.json").write_text(json.dumps(venues), encoding="utf-8")


def test_one_message_per_new_top_pick(tmp_path: Path) -> None:
    config = load_config(CONFIG)
    cat_a, cat_b = [c.key for c in config.categories.categories[:2]]
    venues = [{"id": f"v{i}", "name": f"Venue {i}", "evidence": []} for i in range(5)]

    def s(vid: str, rank: int) -> dict:
        return {"venue_id": vid, "rank": rank, "name": vid}

    _write_run(tmp_path, "r1", {cat_a: [s("v0", 1), s("v1", 2), s("v2", 3), s("v3", 4)]}, venues)
    _write_run(
        tmp_path, "r2", {cat_a: [s("v3", 1), s("v0", 2), s("v1", 3)], cat_b: [s("v0", 1)]}, venues
    )
    store = RunStore(tmp_path)
    first = venue_messages(config, store, RunDiff(run_id="r1", prev_run_id=None))
    assert [m.splitlines()[0] for m in first] == ["🍽 Venue 0", "🍽 Venue 1", "🍽 Venue 2"]
    # next week: only v3 is new to cat_a's top 3, v0 is new to cat_b's
    second = venue_messages(config, store, RunDiff(run_id="r2", prev_run_id="r1"))
    assert sorted(m.splitlines()[0] for m in second) == ["🍽 Venue 0", "🍽 Venue 3"]


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
    venue = {
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
