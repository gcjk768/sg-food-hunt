from __future__ import annotations

import base64
import json
import time
from pathlib import Path

from sgfoodhunt.config import AppConfig, Secrets
from sgfoodhunt.enrich.geocode import onemap_state, onemap_token
from sgfoodhunt.http.cache import read_json, write_json
from sgfoodhunt.site import build_site
from sgfoodhunt.storage.runs import RunStore
from tests.test_stage5 import _scores, _venue, _write_run


def test_build_site_groups_ranked_venues(app_config: AppConfig, run_store: RunStore) -> None:
    venues = [
        _venue("v1", "Alpha", first_seen="20260908T000000Z"),  # same run -> new
        _venue("v2", "Beta", rating=3.5),
        _venue("v3", "Gamma"),  # not ranked anywhere -> left out
    ]
    scores = _scores([("v1", "Alpha"), ("v2", "Beta")])
    scores["hawker_family"] = [
        {
            "venue_id": "v2",
            "name": "Beta",
            "score": 0.5,
            "rank": 1,
            "components": {"food": 0.3},
            "adjustments": {},
            "excluded_reason": None,
            "hidden": False,
        },
        {
            "venue_id": "v3",
            "name": "Gamma",
            "score": 0.1,
            "rank": None,
            "components": {},
            "adjustments": {},
            "excluded_reason": "closed",
            "hidden": False,
        },
    ]
    _write_run(
        run_store, "20260901T000000Z", venues[1:], _scores([("v2", "Beta")])
    )  # bootstrap run
    _write_run(run_store, "20260908T000000Z", venues, scores)
    write_json(
        run_store.run_dir("20260908T000000Z") / "diff.json",
        {
            "prev_run_id": "20260901T000000Z",
            "categories": [
                {
                    "key": "cafes_date",
                    "display_name": "x",
                    "entered": [{"venue_id": "v1", "name": "Alpha", "rank": 1}],
                    "left": [],
                }
            ],
            "new_venues": [{"venue_id": "v1"}],
        },
    )
    data_dir = app_config.resolve(app_config.settings.paths.data_dir)
    write_json(
        data_dir / "promos.json",
        {
            "found_at": "2026-09-08T00:00:00+00:00",
            "promos": {
                "Alpha": {
                    "deal": "1-for-1 brunch",
                    "ends": None,
                    "url": "https://x.test/deal",
                    "festive": False,
                }
            },
        },
    )

    path = build_site(app_config, run_store)
    data = read_json(path)
    assert path == data_dir / "site" / "data.json"
    assert set(data["venues"]) == {"v1", "v2"}
    assert [g["key"] for g in data["groups"]] == ["cafes", "hawker"]
    assert data["groups"][0]["categories"] == ["cafes_date"]
    assert [r["id"] for r in data["categories"]["cafes_date"]["ranked"]] == ["v1", "v2"]
    assert [r["id"] for r in data["categories"]["hawker_family"]["ranked"]] == [
        "v2"
    ]  # excluded dropped
    alpha, beta = data["venues"]["v1"], data["venues"]["v2"]
    assert (
        alpha["new"] is True
        and beta["new"] is False
        and alpha["worth"] is True
        and alpha["promo"]["deal"] == "1-for-1 brunch"
    )
    assert beta["worth"] is False and "3.5" in beta["why"] and beta["promo"] is None
    assert alpha["per_pax"] == 35.0 and alpha["halal"] is True and alpha["mrt"] == "Orchard"
    assert alpha["sources"][0]["url"] == "https://sethlui.test/x"
    assert data["changes"]["entered"] == [
        {"category": "cafes_date", "label": "x", "id": "v1", "rank": 1}
    ]
    assert data["changes"]["new_venues"] == ["v1"] and data["promos"]["count"] == 1
    # a list the previous run did not have (just switched on) does not flood "since the last run"
    diff_path = run_store.run_dir("20260908T000000Z") / "diff.json"
    diff = read_json(diff_path)
    diff["categories"].append(
        {
            "key": "hawker_family",
            "display_name": "h",
            "entered": [{"venue_id": "v2", "name": "Beta", "rank": 1}],
            "left": [],
        }
    )
    write_json(diff_path, diff)
    entered = read_json(build_site(app_config, run_store))["changes"]["entered"]
    assert [e["category"] for e in entered] == ["cafes_date"]


def _jwt(exp: float) -> str:
    body = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJSUzI1NiJ9.{body}.sig"


def test_onemap_token_choice(tmp_path: Path) -> None:
    now = time.time()
    assert onemap_token(Secrets(), tmp_path) is None
    live, dead = _jwt(now + 3600), _jwt(now - 3600)
    assert onemap_token(Secrets(onemap_access_token=live), tmp_path, now) == live
    assert onemap_token(Secrets(onemap_access_token=dead), tmp_path, now) is None
    assert "EXPIRED" in onemap_state(Secrets(onemap_access_token=dead), now)
    assert "valid until" in onemap_state(Secrets(onemap_access_token=live), now)
    assert onemap_state(Secrets(), now).startswith("onemap: no account")
    # a saved login token is reused without a network call
    write_json(tmp_path / "onemap_token.json", {"token": "saved", "renew_at": now + 100})
    assert (
        onemap_token(Secrets(onemap_email="a@b.c", onemap_password="p"), tmp_path, now) == "saved"
    )


def test_site_reads_ranked_venues_missing_from_the_run(
    app_config: AppConfig, run_store: RunStore
) -> None:
    """A price tier ranks registry venues this run did not see; the page shows them, no rank gaps."""
    seen, old = _venue("v1", "Alpha"), _venue("v9", "Old Place")
    _write_run(
        run_store, "20260908T000000Z", [seen], _scores([("v9", "Old Place"), ("v1", "Alpha")])
    )
    write_json(
        app_config.resolve(app_config.settings.paths.data_dir) / "venues.json",
        {"next_id": 10, "venues": [seen, old]},
    )
    data = read_json(build_site(app_config, run_store))
    assert [r["id"] for r in data["categories"]["cafes_date"]["ranked"]] == ["v9", "v1"]
    assert data["venues"]["v9"]["name"] == "Old Place"


def test_michelin_tabs_per_star_level(app_config: AppConfig, run_store: RunStore) -> None:
    v3, v2, vs = _venue("a", "Odette"), _venue("b", "Jaan"), _venue("c", "Candlenut")
    v3["michelin"], v2["michelin"], vs["michelin"] = "3 Stars", "2 Stars", "Star"
    scores = {
        "michelin_stars": [
            {
                "venue_id": vid,
                "name": n,
                "score": 1 - i / 10,
                "rank": i + 1,
                "components": {},
                "adjustments": {},
                "excluded_reason": None,
                "hidden": False,
            }
            for i, (vid, n) in enumerate([("b", "Jaan"), ("a", "Odette"), ("c", "Candlenut")])
        ]
    }
    _write_run(run_store, "20260908T000000Z", [v3, v2, vs], scores)
    data = read_json(build_site(app_config, run_store))
    assert "michelin_stars" not in data["categories"]
    assert [r["id"] for r in data["categories"]["michelin_3"]["ranked"]] == ["a"]
    assert data["categories"]["michelin_2"]["ranked"][0] == {
        **data["categories"]["michelin_2"]["ranked"][0],
        "id": "b",
        "rank": 1,
    }
    assert data["categories"]["michelin_star"]["label"].startswith("Michelin-starred")
    assert "michelin_1" not in data["categories"]  # no 1-star venue: no empty tab
    assert [g["categories"] for g in data["groups"] if g["key"] == "michelin"] == [
        ["michelin_3", "michelin_2", "michelin_star"]
    ]


def test_curated_dishes_and_izakaya_list(app_config: AppConfig, run_store: RunStore) -> None:
    """curated.json fills dishes by name and adds a hand-picked Izakaya list (ids pick known venues)."""
    v1 = _venue("v1", "Alpha")
    _write_run(run_store, "20260908T000000Z", [v1], _scores([("v1", "Alpha")]))
    curated = {
        "dishes": {"Alpha": ["Dish A", "Dish B", "Dish C"]},
        "izakaya": [
            {"id": "v1"},
            {
                "name": "Bar X",
                "lat": 1.2796,
                "lng": 103.8428,
                "dishes": ["Yakitori"],
                "source": {"url": "https://x.test"},
            },
        ],
    }
    write_json(app_config.config_dir / "curated.json", curated)
    data = read_json(build_site(app_config, run_store))
    assert data["venues"]["v1"]["dishes"] == ["Dish A", "Dish B", "Dish C"]
    assert [r["id"] for r in data["categories"]["izakaya"]["ranked"]] == ["v1", "x002"]
    assert data["venues"]["x002"]["mrt"] and data["venues"]["x002"]["worth"]
    assert any(g["key"] == "izakaya" for g in data["groups"])


def test_curated_hide_keeps_closed_venues_off_every_list(
    app_config: AppConfig, run_store: RunStore
) -> None:
    v1, v2 = _venue("v1", "Alpha"), _venue("v2", "Gone Cafe")
    _write_run(
        run_store, "20260908T000000Z", [v1, v2], _scores([("v1", "Alpha"), ("v2", "Gone Cafe")])
    )
    write_json(app_config.config_dir / "curated.json", {"hide": ["Gone Cafe"]})
    data = read_json(build_site(app_config, run_store))
    assert "v1" in data["venues"] and "v2" not in data["venues"]
    assert all(r["id"] != "v2" for c in data["categories"].values() for r in c["ranked"])
