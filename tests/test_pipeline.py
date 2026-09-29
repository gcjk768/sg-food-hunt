from __future__ import annotations

import json
from pathlib import Path

import httpx
from typer.testing import CliRunner

from sgfoodhunt.cli import app
from sgfoodhunt.config import AppConfig
from sgfoodhunt.http.cache import ResponseCache
from sgfoodhunt.http.client import PoliteClient
from sgfoodhunt.pipeline import Collector, select_categories, select_sources
from sgfoodhunt.reporting import export_raw_candidates, write_home_note, write_run_note
from sgfoodhunt.storage.runs import RunStore
from sgfoodhunt.storage.vault import Vault
from tests.conftest import FakeSession, MockApiFactory, fixture_text


def test_select_helpers(app_config: AppConfig) -> None:
    assert [c.key for c in select_categories(app_config, ["zichar_family"])] == ["zichar_family"]
    assert len(select_categories(app_config, None)) == 15
    runnable, skipped = select_sources(app_config, None)
    assert "tripadvisor" not in [s.key for s in runnable] and "disallowed" in skipped["tripadvisor"]
    runnable, skipped = select_sources(app_config, ["sethlui", "tripadvisor"])
    assert [s.key for s in runnable] == ["sethlui"]


def _wire_blog(app_config: AppConfig, fake_session: FakeSession) -> None:
    src = app_config.sources.get("sethlui")
    src.search_url = "https://example-blog.test/?s={query}"
    src.base_url = "https://example-blog.test"
    fake_session.add("https://example-blog.test/?s=*", fixture_text("blog_search.html"))
    fake_session.add(
        "https://example-blog.test/best-romantic-cafes-singapore/",
        fixture_text("blog_article.html"),
    )
    fake_session.add(
        "https://example-blog.test/hawker-guide-to-tiong-bahru/",
        "<html><body><main><p>x</p></main></body></html>",
    )


async def test_collector_end_to_end(
    app_config: AppConfig,
    fake_session: FakeSession,
    http_settings,
    run_store: RunStore,
    vault: Vault,
) -> None:
    _wire_blog(app_config, fake_session)
    cache = ResponseCache(app_config.settings.paths.cache_dir)
    http = PoliteClient(http_settings, cache, session=fake_session, sleep=lambda _s: None)
    factory = MockApiFactory(http_settings, cache, lambda r: httpx.Response(404))
    collector = Collector(app_config, run_store, cache, http=http, api_factory=factory)
    run = await collector.collect(["cafes_date"], ["sethlui", "google_places", "tripadvisor"])

    assert run.status == "ok" and run.mode == "collect"
    stats = run.stats
    assert stats["sources_run"] == ["sethlui"]
    assert stats["sources_skipped"]["google_places"] == "GOOGLE_PLACES_API_KEY is not set"
    assert "tripadvisor" in stats["sources_skipped"]
    assert stats["queries"] == 6 and stats["candidates"] == 12  # 2 venues x 6 query variants
    assert stats["candidates_by_source"] == {"sethlui": 12}
    assert stats["requests"] == 8  # 6 searches + 2 articles; the rest are cache hits
    assert stats["cache_hits"] == 10
    assert run_store.candidate_counts(run.run_id) == {"sethlui": 12}
    rows = list(run_store.iter_candidates(run.run_id))
    assert {r["query"] for r in rows} == set(app_config.categories.get("cafes_date").queries)
    events = list(run_store.iter_events(run.run_id))
    assert any("GOOGLE_PLACES_API_KEY" in e["message"] for e in events)

    csv_path, json_path = export_raw_candidates(
        run_store, run.run_id, app_config.settings.paths.exports_dir
    )
    assert csv_path.read_text().splitlines()[0].startswith("source_key,category_key,query,name")
    assert len(json.loads(json_path.read_text())) == 12

    link = write_run_note(vault, run_store, run, app_config)
    note_path = vault.note_path("Runs", run.run_id)
    text = note_path.read_text()
    assert link.endswith(run.run_id) and text.startswith("---\ntype: run\n")
    assert "| Tiong Bahru Bakery | 6 |" in text
    assert "Seth Lui | blog | 12 | ran" in text
    assert "skipped: GOOGLE_PLACES_API_KEY is not set" in text
    write_home_note(vault, app_config, link)
    home = (vault.root / "Home.md").read_text()
    assert f"[[{link}]]" in home and "```dataview" in home


async def test_collector_dry_run_uses_cache_only(
    app_config: AppConfig, fake_session: FakeSession, http_settings, run_store: RunStore
) -> None:
    _wire_blog(app_config, fake_session)
    cache = ResponseCache(app_config.settings.paths.cache_dir)
    http = PoliteClient(http_settings, cache, session=fake_session, sleep=lambda _s: None)
    await Collector(
        app_config,
        run_store,
        cache,
        http=http,
        api_factory=MockApiFactory(http_settings, cache, lambda r: httpx.Response(404)),
    ).collect(["cafes_date"], ["sethlui"])
    calls_before = len(fake_session.calls)
    dry_http = PoliteClient(
        http_settings, cache, dry_run=True, session=fake_session, sleep=lambda _s: None
    )
    run = await Collector(
        app_config,
        run_store,
        cache,
        dry_run=True,
        http=dry_http,
        api_factory=MockApiFactory(
            http_settings, cache, lambda r: httpx.Response(404), dry_run=True
        ),
    ).collect(["cafes_date"], ["sethlui"])
    assert run.mode == "dry_run" and run.stats["candidates"] == 12 and run.stats["requests"] == 0
    assert len(fake_session.calls) == calls_before


async def test_collector_survives_scraper_exception(
    app_config: AppConfig,
    fake_session: FakeSession,
    http_settings,
    run_store: RunStore,
    monkeypatch,
) -> None:
    from sgfoodhunt.scrapers import blogs

    async def boom(self, query):
        raise RuntimeError("parser exploded")

    monkeypatch.setattr(blogs.SethLuiScraper, "search", boom)
    cache = ResponseCache(app_config.settings.paths.cache_dir)
    http = PoliteClient(http_settings, cache, session=fake_session, sleep=lambda _s: None)
    run = await Collector(
        app_config,
        run_store,
        cache,
        http=http,
        api_factory=MockApiFactory(http_settings, cache, lambda r: httpx.Response(404)),
    ).collect(["cafes_date"], ["sethlui"])
    assert run.status == "partial" and run.stats["errors"] == 6
    assert any(
        e["level"] == "error" and "parser exploded" in e["message"]
        for e in run_store.iter_events(run.run_id)
    )


def test_cli_commands(app_config: AppConfig, tmp_path: Path) -> None:
    runner = CliRunner(env={"COLUMNS": "200"})
    cfg = str(app_config.config_dir)
    assert runner.invoke(app, ["--version"]).output.startswith("sgfoodhunt")
    res = runner.invoke(app, ["sources", "-C", cfg])
    assert res.exit_code == 0 and "tripadvisor" in res.output
    res = runner.invoke(app, ["categories", "-C", cfg])
    assert res.exit_code == 0 and "zichar_family" in res.output
    res = runner.invoke(app, ["init", "-C", cfg])
    assert (
        res.exit_code == 0
        and (Path(app_config.settings.paths.vault_dir) / "SG Food Hunt" / "Home.md").exists()
    )
    res = runner.invoke(app, ["run", "-C", cfg, "--diff-only"])
    assert res.exit_code == 2 and "stage 5" in res.output
    res = runner.invoke(app, ["run", "-C", cfg, "-c", "nope"])
    assert res.exit_code == 2 and "unknown category" in res.output
    res = runner.invoke(app, ["runs", "-C", cfg])
    assert res.exit_code == 0
    res = runner.invoke(app, ["cache", "stats", "-C", cfg])
    assert res.exit_code == 0 and "entries" in res.output


def test_cli_dry_run_with_empty_cache_touches_no_network(app_config: AppConfig) -> None:
    """A dry run with nothing cached must complete, record warnings, and write the run note."""
    runner = CliRunner(env={"COLUMNS": "200"})
    cfg = str(app_config.config_dir)
    res = runner.invoke(
        app,
        ["run", "-C", cfg, "--dry-run", "-c", "zichar_family", "-s", "sethlui", "-s", "michelin"],
    )
    assert res.exit_code == 0, res.output
    store = RunStore(app_config.settings.paths.data_dir)
    run = store.latest_run()
    assert run is not None and run.mode == "dry_run" and run.stats["requests"] == 0
    assert run.stats["warnings"] >= 7  # 6 zi char queries + 1 michelin listing, none cached
    note = Path(app_config.settings.paths.vault_dir) / "SG Food Hunt" / "Runs" / f"{run.run_id}.md"
    assert note.exists() and "dry run: not cached" in note.read_text()


async def test_rank_run_end_to_end(
    app_config: AppConfig,
    fake_session: FakeSession,
    http_settings,
    run_store: RunStore,
    vault: Vault,
) -> None:
    """Collect from the fixture blog, then dedup + score + write venue and category notes."""
    from sgfoodhunt.ranking import rank_run
    from sgfoodhunt.storage.frontmatter import parse_note

    _wire_blog(app_config, fake_session)
    cache = ResponseCache(app_config.settings.paths.cache_dir)
    http = PoliteClient(http_settings, cache, session=fake_session, sleep=lambda _s: None)
    collector = Collector(
        app_config,
        run_store,
        cache,
        http=http,
        api_factory=MockApiFactory(http_settings, cache, lambda r: httpx.Response(404)),
    )
    run = await collector.collect(["cafes_date", "zichar_family"], ["sethlui"])
    result = rank_run(app_config, run_store, vault, run.run_id)
    assert {v.name for v in result.venues.values()} == {
        "Tiong Bahru Bakery",
        "Keng Eng Kee Seafood",
    }
    assert set(result.scores) == {"cafes_date", "zichar_family"}
    zichar = result.scores["zichar_family"]
    assert (
        zichar[0].name == "Keng Eng Kee Seafood" and zichar[0].rank == 1
    )  # zi char keywords in its snippet
    venue_dir = vault.root / "Venues"
    names = sorted(p.stem for p in venue_dir.glob("*.md"))
    assert names == ["Keng Eng Kee Seafood", "Tiong Bahru Bakery"]
    note = parse_note((venue_dir / "Keng Eng Kee Seafood.md").read_text())
    fm = note.frontmatter
    assert (
        fm["postal_code"] == "150124"
        and fm["region"] == "Central"
        and fm["name_zh"] == "琼荣记海鲜"
    )
    assert fm["ranks"]["zichar_family"] == 1 and fm["price_level"] == "$$"
    assert fm["bill_estimate"]["zichar_family"] == 35.0 * 5
    assert "## My notes" in note.body and "## Evidence" in note.body
    assert (vault.root / "Categories" / "Best zi char places for a family weekend meal.md").exists()
    cat_note = (vault.root / "Categories" / "Best cafes for a date.md").read_text()
    assert "## Top 15" in cat_note
    # the pipe is escaped inside a Markdown table cell
    assert "[[SG Food Hunt/Venues/Tiong Bahru Bakery\\|Tiong Bahru Bakery]]" in cat_note
    exports = Path(app_config.settings.paths.exports_dir) / run.run_id
    assert (exports / "zichar_family.csv").exists() and (exports / "venues_ranked.json").exists()
    assert (run_store.run_dir(run.run_id) / "scores.json").exists()
    assert run_store.load_run(run.run_id).stats["ranking"]["venues_total"] == 2

    # personal layer: mark KEK visited with a rating and add my notes, then re-rank with --hide-visited
    path = venue_dir / "Keng Eng Kee Seafood.md"
    edited = parse_note(path.read_text())
    edited.frontmatter["status"] = "visited"
    edited.frontmatter["my_rating"] = 4
    edited.body = edited.body.replace("## My notes\n", "## My notes\nGreat moonlight hor fun.\n")
    from sgfoodhunt.storage.frontmatter import render_note

    path.write_text(render_note(edited))
    result2 = rank_run(app_config, run_store, vault, run.run_id, hide_visited=True)
    kek = next(s for s in result2.scores["zichar_family"] if s.name == "Keng Eng Kee Seafood")
    assert kek.hidden and kek.rank == 0 and "personal" in kek.adjustments
    again = parse_note(path.read_text())
    assert again.frontmatter["status"] == "visited" and again.frontmatter["my_rating"] == 4
    assert "Great moonlight hor fun." in again.body
    cat_note = (
        vault.root / "Categories" / "Best zi char places for a family weekend meal.md"
    ).read_text()
    assert (
        "## Hidden (visited)" in cat_note
        and "Keng Eng Kee Seafood|Keng Eng Kee Seafood]] (" in cat_note
    )
    assert result2.match_stats.created == 0  # ids stable across re-ranks
