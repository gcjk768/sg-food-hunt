"""One Telegram message per top pick; blog search keeps only query-relevant food articles."""

import json
from pathlib import Path

import httpx

from sgfoodhunt.config import load_config
from sgfoodhunt.diff import RunDiff, venue_messages
from sgfoodhunt.notify import send_telegram
from sgfoodhunt.scrapers.blogs import relevant_article
from sgfoodhunt.scrapers.html import clean_venue_heading
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
