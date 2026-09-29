from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

from sgfoodhunt.config import AppConfig
from sgfoodhunt.dashboard.data import load_dashboard_data, venue_table
from sgfoodhunt.diff import compute_diff, diff_markdown, diff_plain_text
from sgfoodhunt.http.cache import write_json
from sgfoodhunt.notify import notify, send_telegram
from sgfoodhunt.sheets import export_csvs_to_sheets
from sgfoodhunt.storage.runs import RunStore


def _venue(
    vid: str,
    name: str,
    rating: float | None = 4.3,
    status: str = "OPERATIONAL",
    sources=("sethlui",),
    first_seen: str = "20260901T000000Z",
    region: str = "Central",
) -> dict[str, Any]:
    return {
        "id": vid,
        "name": name,
        "region": region,
        "business_status": status,
        "first_seen_run": first_seen,
        "ratings": {"google_places": {"rating": rating, "review_count": 100}}
        if rating is not None
        else {},
        "evidence": [
            {"source_key": s, "url": f"https://{s}.test/x", "category_keys": ["cafes_date"]}
            for s in sources
        ],
        "cuisine": ["Cafe"],
        "price_level": 2,
        "flags": {"halal": True},
        "lat": 1.3,
        "lng": 103.8,
        "nearest_mrt": "Orchard",
    }


def _scores(order: list[tuple[str, str]]) -> dict[str, list[dict[str, Any]]]:
    return {
        "cafes_date": [
            {
                "venue_id": vid,
                "name": name,
                "score": 1 - i * 0.05,
                "rank": i + 1,
                "components": {},
                "adjustments": {},
                "excluded_reason": None,
                "hidden": False,
            }
            for i, (vid, name) in enumerate(order)
        ]
    }


def _write_run(
    store: RunStore, run_id: str, venues: list[dict[str, Any]], scores: dict[str, Any]
) -> None:
    rdir = store.run_dir(run_id)
    (rdir / "raw").mkdir(parents=True, exist_ok=True)
    write_json(
        rdir / "run.json",
        {
            "run_id": run_id,
            "mode": "collect",
            "categories": ["cafes_date"],
            "sources": ["sethlui"],
            "started_at": "2026-09-01T00:00:00+00:00",
            "finished_at": "2026-09-01T00:01:00+00:00",
            "status": "ok",
            "stats": {},
            "notes": "",
        },
    )
    write_json(rdir / "venues.json", venues)
    write_json(rdir / "scores.json", scores)


def test_compute_diff(app_config: AppConfig, run_store: RunStore) -> None:
    app_config.settings.scoring.top_n = 2
    prev = [
        _venue("v1", "Alpha"),
        _venue("v2", "Beta", rating=4.0),
        _venue("v3", "Gamma", rating=4.5),
    ]
    cur = [
        _venue("v1", "Alpha", rating=4.6, sources=("sethlui", "eatbook")),
        _venue("v2", "Beta", rating=4.0, status="CLOSED_PERMANENTLY"),
        _venue("v3", "Gamma", rating=4.5),
        _venue("v4", "Delta", first_seen="20260908T000000Z", region="East"),
    ]
    _write_run(
        run_store,
        "20260901T000000Z",
        prev,
        _scores([("v1", "Alpha"), ("v2", "Beta"), ("v3", "Gamma")]),
    )
    _write_run(
        run_store,
        "20260908T000000Z",
        cur,
        _scores([("v3", "Gamma"), ("v4", "Delta"), ("v1", "Alpha")]),
    )
    write_json(
        app_config.resolve(app_config.settings.paths.data_dir) / "venues.json",
        {"next_id": 5, "venues": cur},
    )
    social = app_config.resolve(app_config.settings.paths.data_dir) / "social"
    social.mkdir(parents=True)
    (social / "mentions.jsonl").write_text(
        json.dumps(
            {
                "source": "ig_export",
                "platform": "instagram",
                "url": "https://ig/p/1",
                "run_id": "20260908T000000Z",
                "venue_id": "v1",
            }
        )
        + "\n"
    )

    diff = compute_diff(app_config, run_store, "20260908T000000Z")
    assert diff.prev_run_id == "20260901T000000Z" and not diff.is_empty
    cat = diff.categories[0]
    assert [e["name"] for e in cat.entered] == ["Gamma", "Delta"]
    assert [e["name"] for e in cat.left] == ["Alpha", "Beta"]
    assert diff.rating_changes == [
        {"venue_id": "v1", "name": "Alpha", "from": 4.3, "to": 4.6, "delta": 0.3}
    ]
    assert [c["name"] for c in diff.newly_closed] == ["Beta"]
    assert [v["name"] for v in diff.new_venues] == ["Delta"] and diff.new_venues[0][
        "region"
    ] == "East"
    assert diff.new_sources == [{"venue_id": "v1", "name": "Alpha", "sources": ["eatbook"]}]
    assert len(diff.new_social_mentions) == 1
    assert (run_store.run_dir("20260908T000000Z") / "diff.json").exists()
    md = diff_markdown(diff)
    assert "▲ entered top list at #2: Delta" in md and "▼ left top list (was #2): Beta" in md
    assert (
        "Alpha: 4.3 → 4.6 (+0.30)" in md
        and "Newly closed" in md
        and "New social mentions:** 1" in md
    )
    text = diff_plain_text(diff)
    assert "**" not in text and "+ entered" in text
    first = compute_diff(app_config, run_store, "20260901T000000Z")
    assert first.prev_run_id is None and "First scored run" in diff_markdown(first)
    assert first.categories == [] and [v["name"] for v in first.new_venues] == [
        "Alpha",
        "Beta",
        "Gamma",
    ]


def test_notify_telegram_and_email(app_config: AppConfig, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    posts: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert "bot123:abc/sendMessage" in str(request.url)
        posts.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    send_telegram("123:abc", "42", "x" * 5000, client=client)
    assert len(posts) == 2 and posts[0]["chat_id"] == "42"

    sent: list[Any] = []

    class FakeSmtp:
        def __enter__(self) -> FakeSmtp:
            return self

        def __exit__(self, *a: Any) -> None:
            return None

        def login(self, u: str, p: str) -> None:
            sent.append(("login", u))

        def send_message(self, msg: Any) -> None:
            sent.append(("msg", msg["Subject"], msg["To"]))

    monkeypatch.setenv("SMTP_HOST", "smtp.test")
    monkeypatch.setenv("SMTP_USER", "me")
    monkeypatch.setenv("SMTP_PASSWORD", "pw")
    monkeypatch.setenv("REPORT_EMAIL_TO", "you@test")
    app_config.settings.notifications.telegram = True
    app_config.settings.notifications.email = True
    app_config.secrets.telegram_bot_token = "123:abc"
    app_config.secrets.telegram_chat_id = "42"
    result = notify(app_config, "hello", "subj", client=client, smtp_factory=FakeSmtp)
    assert result.telegram == "sent" and result.email == "sent" and not result.errors
    assert ("msg", "subj", "you@test") in sent
    app_config.secrets.telegram_bot_token = None
    result = notify(app_config, "hello", "subj", client=client, smtp_factory=FakeSmtp)
    assert "not set" in (result.telegram or "")
    app_config.settings.notifications.telegram = False
    app_config.settings.notifications.email = False
    assert notify(app_config, "x", "y").telegram == "disabled"


def test_sheets_export(tmp_path: Path) -> None:
    csv_path = tmp_path / "cafes_date.csv"
    csv_path.write_text("rank,name\n1,Alpha\n")

    class FakeWs:
        def __init__(self) -> None:
            self.values: list[list[str]] = []

        def clear(self) -> None:
            self.values = []

        def update(self, range_name: str, values: list[list[str]]) -> None:
            self.values = values

    class FakeBook:
        def __init__(self) -> None:
            self.sheets: dict[str, FakeWs] = {}

        def worksheet(self, title: str) -> FakeWs:
            if title not in self.sheets:
                raise KeyError(title)
            return self.sheets[title]

        def add_worksheet(self, title: str, rows: int, cols: int) -> FakeWs:
            self.sheets[title] = FakeWs()
            return self.sheets[title]

    class FakeClient:
        def __init__(self) -> None:
            self.book = FakeBook()

        def open_by_key(self, key: str) -> FakeBook:
            assert key == "sheet123"
            return self.book

    client = FakeClient()
    assert export_csvs_to_sheets(
        {"cafes_date": csv_path}, spreadsheet_id="sheet123", client=client
    ) == ["cafes_date"]
    assert client.book.sheets["cafes_date"].values == [["rank", "name"], ["1", "Alpha"]]
    assert export_csvs_to_sheets(
        {"cafes_date": csv_path}, spreadsheet_id="sheet123", client=client
    ) == ["cafes_date"]  # clears and rewrites


def test_dashboard_data(app_config: AppConfig, run_store: RunStore) -> None:
    config, run_id, rows, _ = load_dashboard_data(app_config.config_dir)
    assert run_id is None and rows == []
    venues = [_venue("v1", "Alpha"), _venue("v2", "Beta", status="CLOSED_PERMANENTLY")]
    _write_run(run_store, "20260908T000000Z", venues, _scores([("v1", "Alpha")]))
    write_json(
        app_config.resolve(app_config.settings.paths.data_dir) / "venues.json",
        {"next_id": 3, "venues": venues},
    )
    config, run_id, rows, _ = load_dashboard_data(app_config.config_dir)
    assert run_id == "20260908T000000Z" and len(rows) == 2
    table = venue_table(rows, "cafes_date", config)
    assert (
        [t["name"] for t in table] == ["Alpha"]
        and table[0]["rank"] == 1
        and table[0]["halal"] is True
    )
    assert (
        table[0]["per_pax_sgd"] == 35.0
        and table[0]["nearest_mrt"] == "Orchard"
        and table[0]["lon"] == 103.8
    )
    everything = venue_table(rows, None, config)
    assert [t["name"] for t in everything] == ["Alpha", "Beta"]
