from __future__ import annotations

import json
import subprocess
from typing import Any

import httpx
import pytest
import yaml
from typer.testing import CliRunner

from sgfoodhunt.ai import (
    AiClient,
    analyse_reviews,
    build_ai_client,
    extract_listicle,
    extract_venues,
    match_caption,
)
from sgfoodhunt.cli import app
from sgfoodhunt.config import AiSettings, AiTasks, AppConfig
from sgfoodhunt.dedup import Evidence, Venue
from sgfoodhunt.http.cache import ResponseCache
from sgfoodhunt.models import SearchQuery
from sgfoodhunt.reviews.analysis import analyse_venue
from tests.conftest import FakeSession, fixture_text


class FakeClaude:
    """Stands in for the `claude` binary: returns canned structured output per task."""

    def __init__(
        self, answers: dict[str, Any] | None = None, cost: float = 0.01, fail: bool = False
    ) -> None:
        self.answers = answers or {}
        self.cost = cost
        self.fail = fail
        self.calls: list[list[str]] = []

    def __call__(self, cmd: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
        self.calls.append(cmd)
        assert (
            cmd[1] == "-p"
            and "--json-schema" in cmd
            and "--tools" in cmd
            and cmd[cmd.index("--tools") + 1] == ""
        )
        system = cmd[cmd.index("--system-prompt") + 1]
        if self.fail:
            return subprocess.CompletedProcess(cmd, 1, "", "boom")
        key = (
            "extract"
            if "extract dining venues" in system
            else "reviews"
            if "analyse" in system
            else "match"
        )
        answer = self.answers.get(key, {})
        payload = {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "total_cost_usd": self.cost,
            "result": json.dumps(answer),
            "structured_output": answer,
        }
        return subprocess.CompletedProcess(cmd, 0, json.dumps(payload), "")


def _client(cache: ResponseCache, fake: FakeClaude, **overrides: Any) -> AiClient:
    settings = AiSettings(enabled=True, **overrides)
    return AiClient(settings, cache, runner=fake)


def test_client_caches_budgets_and_fails_soft(cache: ResponseCache) -> None:
    fake = FakeClaude(
        {"extract": {"venues": [{"name": "Kok Sen", "area": "Keong Saik"}]}}, cost=0.4
    )
    ai = _client(cache, fake, max_budget_usd=1.0, max_calls_per_run=10)
    assert ai.available() and ai.task_enabled("reddit_extraction")
    first = extract_venues(ai, "go to Kok Sen")
    assert (
        first is not None
        and first[0].name == "Kok Sen"
        and first[0].area == "Keong Saik"
        and first[0].confidence == 0.8
    )
    again = extract_venues(ai, "go to Kok Sen")
    assert again == first and ai.stats.calls == 1 and ai.stats.cache_hits == 1
    assert (
        "--model" in fake.calls[0]
        and fake.calls[0][fake.calls[0].index("--model") + 1] == "claude-sonnet-5-5"
    )
    extract_venues(ai, "second")
    extract_venues(ai, "third")  # cost now 1.2 > budget
    assert ai.stats.calls == 3 and abs(ai.stats.cost_usd - 1.2) < 1e-9
    assert extract_venues(ai, "fourth") is None and ai.stats.skipped_budget == 1
    disabled = AiClient(
        AiSettings(enabled=True, tasks=AiTasks(reddit_extraction=False)), cache, runner=fake
    )
    assert extract_venues(disabled, "x") is None and disabled.stats.skipped_disabled == 1
    failing = _client(cache, FakeClaude(fail=True))
    assert extract_venues(failing, "y") is None and failing.stats.failures == 1
    offline = AiClient(AiSettings(enabled=True), cache, runner=fake, offline=True)
    assert extract_venues(offline, "go to Kok Sen") is not None  # served from cache
    assert extract_venues(offline, "never seen") is None and offline.stats.calls == 0
    assert build_ai_client(AiSettings(enabled=False), cache) is None


def test_task_parsers(cache: ResponseCache) -> None:
    fake = FakeClaude(
        {
            "extract": {
                "venues": [
                    {
                        "name": "Luna Rooftop",
                        "address": "1 Fullerton Rd, Singapore 049213",
                        "hours": "5pm-11pm",
                        "price": "$$$",
                        "confidence": 0.95,
                    },
                    {"name": "  "},
                ]
            },
            "reviews": {
                "food": 0.9,
                "service": 0.4,
                "ambience": 1.4,
                "value": -1,
                "noise_level": "loud",
                "summary": "A lively rooftop spot with strong cooking and slow service.",
                "signature_dishes": ["cereal prawn"],
                "tags": ["rooftop", "bogus"],
            },
            "match": {"venue_id": "v2", "confidence": 0.7},
        }
    )
    ai = _client(cache, fake)
    entries = extract_listicle(ai, "Best rooftops", "long article text")
    assert (
        entries is not None
        and len(entries) == 1
        and (entries[0].address or "").startswith("1 Fullerton")
        and entries[0].confidence == 0.95
    )
    analysis = analyse_reviews(ai, "Luna", ["great food", "slow service"])
    assert analysis is not None and analysis.aspects == {
        "food": 0.9,
        "service": 0.4,
        "ambience": 1.0,
        "value": 0.0,
    }
    assert analysis.noise_level == "loud" and analysis.signature_dishes == ["cereal prawn"]
    assert analyse_reviews(ai, "Luna", ["  "]) is None
    assert match_caption(ai, "sunset dinner at luna", [("v1", "A"), ("v2", "Luna Rooftop")]) == (
        "v2",
        0.7,
    )
    assert match_caption(ai, "x", []) is None
    fake.answers["match"] = {"venue_id": "none"}
    ai2 = _client(cache, fake)
    assert match_caption(ai2, "unrelated caption", [("v1", "A")]) is None


async def test_reddit_uses_ai_when_enabled(make_scraper, cache: ResponseCache) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "access_token" in url:
            return httpx.Response(200, json=json.loads(fixture_text("reddit_token.json")))
        if "/search" in url:
            return httpx.Response(
                200,
                json=json.loads(fixture_text("reddit_search.json"))
                if "singaporeeats" in url
                else {"data": {"children": []}},
            )
        return httpx.Response(200, json=json.loads(fixture_text("reddit_comments.json")))

    scraper, _ = make_scraper("reddit", handler=handler)
    scraper.ctx.config.secrets.reddit_client_id = "id"
    scraper.ctx.config.secrets.reddit_client_secret = "secret"
    fake = FakeClaude(
        {
            "extract": {
                "venues": [
                    {"name": "Two Chefs Eating Place", "area": "Commonwealth"},
                    {"name": "JB Ah Meng", "name_zh": "新山亚明"},
                ]
            }
        }
    )
    scraper.ctx.ai = _client(cache, fake)
    result = await scraper.search(
        SearchQuery("zichar_family", "family", "best zi char Singapore", 5)
    )
    names = sorted(c.name for c in result.candidates)
    assert names == [
        "JB Ah Meng",
        "Two Chefs Eating Place",
    ]  # heuristics replaced by the model's answer
    assert all(c.confidence == 0.8 and c.extra["extracted_by"] == "ai" for c in result.candidates)
    assert next(c for c in result.candidates if c.name == "JB Ah Meng").name_zh == "新山亚明"
    assert len(fake.calls) == 1 and "Reddit thread title" in fake.calls[0][2]


async def test_blog_falls_back_to_ai_when_no_venue_details(
    make_scraper, fake_session: FakeSession, cache: ResponseCache
) -> None:
    scraper, _ = make_scraper(
        "sethlui",
        search_url="https://example-blog.test/?s={query}",
        base_url="https://example-blog.test",
    )
    fake_session.add(
        "https://example-blog.test/?s=romantic+cafes+Singapore",
        '<html><body><main><a href="https://example-blog.test/prose-style-article-here/">x</a></main></body></html>',
    )
    fake_session.add(
        "https://example-blog.test/prose-style-article-here/",
        "<html><head><title>Prose</title></head><body><article><div class='entry-content'><p>We wandered from a bakery in Tiong Bahru to a rooftop by the river and loved both.</p></div></article></body></html>",
    )
    fake = FakeClaude(
        {
            "extract": {
                "venues": [
                    {
                        "name": "Tiong Bahru Bakery",
                        "address": "56 Eng Hoon St, Singapore 160056",
                        "hours": "7.30am-8pm",
                    }
                ]
            }
        }
    )
    scraper.ctx.ai = _client(cache, fake)
    result = await scraper.search(
        SearchQuery("cafes_date", "dating", "romantic cafes Singapore", 2)
    )
    assert [c.name for c in result.candidates] == ["Tiong Bahru Bakery"]
    assert (
        result.candidates[0].postal_code == "160056"
        and result.candidates[0].extra["extracted_by"] == "ai"
    )
    assert result.candidates[0].opening_hours == {"text": "7.30am-8pm"}


def test_analyse_venue_with_ai(app_config: AppConfig, cache: ResponseCache) -> None:
    v = Venue(
        id="v",
        name="Kok Sen",
        cuisine=["Zi Char"],
        evidence=[
            Evidence("sethlui", "u", snippet="big prawn hor fun is great"),
            Evidence("eatbook", "u2", snippet="wok hei, noisy"),
        ],
    )
    fake = FakeClaude(
        {
            "reviews": {
                "food": 0.95,
                "service": 0.5,
                "ambience": 0.4,
                "value": 0.8,
                "noise_level": "loud",
                "summary": "Old school zi char with standout prawn noodles and a rowdy room.",
                "signature_dishes": ["Big prawn hor fun"],
                "tags": ["lively", "nonsense"],
            }
        }
    )
    ai = _client(cache, fake)
    analyse_venue(v, app_config, {"zichar_family": 1}, ai=ai)
    assert v.summary == "Old school zi char with standout prawn noodles and a rowdy room."
    assert (
        v.aspects["food"] == 0.95
        and v.noise_level == "loud"
        and v.ai_dishes == ["Big prawn hor fun"]
    )
    assert "lively" in v.ambience_tags and "nonsense" not in v.ambience_tags
    assert v.best_for == "a no-fuss zi char dinner with the family"
    single = Venue(
        id="s", name="Solo", evidence=[Evidence("sethlui", "u", snippet="one snippet only")]
    )
    analyse_venue(single, app_config, ai=ai)
    assert (single.summary or "").startswith("Solo is a restaurant.")
    assert len(fake.calls) == 1  # below the snippet threshold: no call


def test_doctor_reports_ai(app_config: AppConfig, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = CliRunner(env={"COLUMNS": "200"})
    settings = app_config.config_dir / "settings.yaml"
    data = yaml.safe_load(settings.read_text())
    data["ai"] = {"enabled": True, "claude_bin": "definitely-not-installed-bin"}
    settings.write_text(yaml.safe_dump(data))
    res = runner.invoke(app, ["doctor", "-C", str(app_config.config_dir)])
    assert res.exit_code == 0 and "not on PATH" in res.output  # warning, not a failure
