"""Promotions: only sourced, current deals for the venues asked about; one message; occasion lines."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from sgfoodhunt.config import load_config
from sgfoodhunt.occasion import CATEGORY, occasion_message
from sgfoodhunt.promos import BATCH, find_promos, promo_line, promo_message
from sgfoodhunt.storage.runs import RunStore

CONFIG = Path(__file__).resolve().parents[1] / "config"
TODAY = date(2026, 10, 4)


class FakeAi:
    """Stands in for AiClient: records calls, answers with a canned reply."""

    def __init__(self, promos: list[dict[str, Any]], enabled: bool = True) -> None:
        self.promos, self.enabled, self.calls = promos, enabled, []

    def task_enabled(self, task: str) -> bool:
        return self.enabled

    def ask(self, task: str, system: str, prompt: str, schema: dict[str, Any], **kw: Any) -> Any:
        self.calls.append((prompt, kw))
        return {"promos": self.promos}


def p(name: str, **kw: Any) -> dict[str, Any]:
    return {
        "name": name,
        "deal": "1-for-1 buffet, S$88++ for two",
        "ends": "2026-10-31",
        "url": "https://example.sg/deal",
        **kw,
    }


def test_only_sourced_current_deals_for_asked_venues() -> None:
    ai = FakeAi(
        [
            p("Lime"),
            p("Stranger"),  # not asked about
            p("Odette", url="http://insecure.sg/x"),  # not https
            p("Zen", ends="2026-09-01"),  # already ended
            p("Burnt Ends", ends=None, deal="Christmas festive buffet early bird"),
        ]
    )
    out = find_promos(ai, ["Lime", "Odette", "Zen", "Burnt Ends"], TODAY)  # type: ignore[arg-type]
    assert set(out) == {"Lime", "Burnt Ends"}
    assert out["Lime"]["festive"] is False and out["Burnt Ends"]["festive"] is True
    assert ai.calls[0][1] == {"web": True, "ttl_days": 1}


def test_batches_and_off_switch() -> None:
    names = [f"Place {i}" for i in range(BATCH + 3)]
    ai = FakeAi([])
    find_promos(ai, names, TODAY)  # type: ignore[arg-type]
    assert len(ai.calls) == 2
    off = FakeAi([p("Lime")], enabled=False)
    assert find_promos(off, ["Lime"], TODAY) == {} and off.calls == []  # type: ignore[arg-type]
    assert find_promos(None, ["Lime"], TODAY) == {}


def test_message_and_lines_are_escaped_and_single() -> None:
    assert promo_message({}) is None
    promos = {"Tom & Jerry": {**p("Tom & Jerry", deal="<b>50% off</b>"), "festive": True}}
    text = promo_message(promos)
    assert text is not None and "&lt;b&gt;50% off" in text and "🎄" in text
    assert promo_line({**p("X"), "festive": False}).startswith("🎁 1-for-1 buffet")
    assert "until 2026-10-31" in promo_line({**p("X"), "festive": False})
    many = {f"Place {i}": {**p(f"Place {i}"), "festive": False} for i in range(200)}
    assert len(promo_message(many) or "") <= 3900


def test_occasion_list_gets_the_promo_line(tmp_path: Path) -> None:
    config = load_config(CONFIG)
    d = tmp_path / "runs" / "r1"
    d.mkdir(parents=True)
    venues = [{"id": "a", "name": "Lime", "cuisine": ["Buffet"], "price_level": 3}]
    (d / "scores.json").write_text(
        json.dumps({CATEGORY: [{"venue_id": "a", "rank": 1, "name": "Lime"}]}), encoding="utf-8"
    )
    (d / "venues.json").write_text(json.dumps(venues), encoding="utf-8")
    promos = {"Lime": {**p("Lime"), "festive": False}}
    text = occasion_message(config, RunStore(tmp_path), "r1", promos=promos)
    assert text is not None and "🎁 1-for-1 buffet" in text
    assert "🎁" not in (occasion_message(config, RunStore(tmp_path), "r1") or "")
