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
