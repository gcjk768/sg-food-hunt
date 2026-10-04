"""Special occasion list: one message, Michelin stars, Worth and Buffet tags, fortnightly gap."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sgfoodhunt.config import AppConfig, load_config
from sgfoodhunt.dedup import Venue
from sgfoodhunt.occasion import (
    CATEGORY,
    OCCASION_EVERY_DAYS,
    due,
    is_buffet,
    occasion_message,
    star_count,
)
from sgfoodhunt.scoring.score import hard_filter_reason
from sgfoodhunt.storage.runs import RunStore

CONFIG = Path(__file__).resolve().parents[1] / "config"


def _run(root: Path, venues: list[dict[str, Any]]) -> RunStore:
    d = root / "runs" / "r1"
    d.mkdir(parents=True)
    scores = [{"venue_id": v["id"], "rank": i, "name": v["name"]} for i, v in enumerate(venues, 1)]
    (d / "scores.json").write_text(json.dumps({CATEGORY: scores}), encoding="utf-8")
    (d / "venues.json").write_text(json.dumps(venues), encoding="utf-8")
    return RunStore(root)


def test_star_count_and_buffet() -> None:
    assert [
        star_count(m) for m in ("3 Stars", "1 Star", "Star", "Bib Gourmand", "Green Star", None)
    ] == [
        3,
        1,
        1,
        0,
        0,
        0,
    ]
    assert is_buffet({"name": "Lime", "cuisine": ["International Buffet"]})
    assert is_buffet(Venue(id="x", name="Seafood Buffet at the Hotel"))
    assert not is_buffet({"name": "Plain Bistro", "cuisine": ["French"]})


def test_one_message_with_stars_worth_and_buffet(tmp_path: Path) -> None:
    config = load_config(CONFIG)
    venues = [
        {
            "id": "a",
            "name": "Buffet Lime",
            "cuisine": ["Buffet"],
            "price_level": 3,
            "ratings": {"google_places": {"rating": 4.5, "review_count": 800}},
        },
        {
            "id": "b",
            "name": "Star Place & Co",
            "price_level": 4,
            "michelin": "2 Stars",
            "address": "1 Orchard Rd",
            "booking_url": "https://x.sg/book?a=1&b=2",
        },
        {"id": "c", "name": "Plain Bistro", "price_level": 3, "evidence": []},
        *[{"id": f"f{i}", "name": f"Filler {i}", "price_level": 3} for i in range(7)],
        {"id": "z", "name": "Late Bistro", "price_level": 3, "evidence": []},
    ]
    text = occasion_message(config, _run(tmp_path, venues), "r1")
    assert text is not None
    # Michelin first, with the stars, then the ranker's order
    assert (
        text.index("Star Place &amp; Co")
        < text.index("Buffet Lime")
        < text.index("Plain Bistro")
        < text.index("Late Bistro")
    )
    assert "⭐⭐ <b>Star Place &amp; Co</b> · <b>Michelin</b> 2 Stars ✅ <b>Worth</b>" in text
    assert "🍴 <b>Buffet</b>" in text and text.count("🍴 <b>Buffet</b>") == 1
    assert "about S$640 for 4" in text and "about S$320 for 4" in text
    assert 'href="https://x.sg/book?a=1&amp;b=2"' in text
    assert "Plain Bistro</b> ✅ <b>Worth</b>" in text  # top 10 place counts as Worth
    assert "Late Bistro</b> ✅" not in text  # rank 11 with no rating or sources: not Worth
    assert text.startswith("🎉 <b>SPECIAL OCCASIONS</b> · S$200+ for 4") and len(text) <= 3900


def test_long_list_still_one_message(tmp_path: Path) -> None:
    config = load_config(CONFIG)
    venues = [
        {
            "id": f"v{i}",
            "name": f"Restaurant number {i} " + "x" * 60,
            "price_level": 3,
            "address": "A very long address " * 5,
            "michelin": "1 Star" if i % 2 else None,
        }
        for i in range(40)
    ]
    text = occasion_message(config, _run(tmp_path, venues), "r1")
    assert text is not None and len(text) <= 3900


def test_over_200_for_4_filter_and_unknown_price(app_config: AppConfig) -> None:
    cat = app_config.categories.get(CATEGORY)
    s, today = app_config.settings.scoring, datetime.now(UTC).date()
    assert cat.party_size == 4 and cat.digest_only
    for level, expect_ok in ((1, False), (2, False), (3, True), (4, True)):
        v = Venue(id=f"v{level}", name="V")
        v.price_level = level
        assert (hard_filter_reason(v, cat, s, today) is None) is expect_ok
    plain = Venue(id="p", name="Plain")
    assert hard_filter_reason(plain, cat, s, today) == "no price level"
    starred = Venue(id="s", name="Starred")
    starred.michelin = "1 Star"
    assert hard_filter_reason(starred, cat, s, today) is None
    assert hard_filter_reason(Venue(id="b", name="Lime Buffet"), cat, s, today) is None


def test_sent_every_two_weeks(tmp_path: Path, app_config: AppConfig) -> None:
    assert due(app_config) is True  # never sent
    state = app_config.resolve(app_config.settings.paths.data_dir) / "occasion_state.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    state.write_text(
        json.dumps({"last_sent": (now - timedelta(days=3)).isoformat()}), encoding="utf-8"
    )
    assert due(app_config, now) is False
    state.write_text(
        json.dumps({"last_sent": (now - timedelta(days=OCCASION_EVERY_DAYS)).isoformat()}),
        encoding="utf-8",
    )
    assert due(app_config, now) is True
