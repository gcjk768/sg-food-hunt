from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from sgfoodhunt.config import AppConfig
from sgfoodhunt.dedup import Evidence, Venue, VenueRegistry, build_venues
from sgfoodhunt.scoring import PersonalNote, score_category
from sgfoodhunt.scoring.score import bayesian_rating, recency_weight

RUN = "20260901T000000Z"


def row(source: str, name: str, category: str = "zichar_family", **kw: Any) -> dict[str, Any]:
    base = {
        "run_id": RUN,
        "source_key": source,
        "category_key": category,
        "category_group": "family",
        "party_size": 5,
        "query": "best zi char Singapore",
        "page_url": f"https://{source}.test/{name.replace(' ', '-')}",
        "source_ref": None,
        "name": name,
        "name_zh": None,
        "brand": None,
        "address": None,
        "postal_code": None,
        "lat": None,
        "lng": None,
        "phone": None,
        "website": None,
        "booking_url": None,
        "rating": None,
        "review_count": None,
        "price_level": None,
        "price_text": None,
        "cuisine": [],
        "opening_hours": None,
        "business_status": None,
        "michelin": None,
        "hygiene_grade": None,
        "snippet": None,
        "confidence": 1.0,
        "extra": {},
        "captured_at": "2026-09-01T00:00:00+00:00",
    }
    base.update(kw)
    return base


def test_registry_matching_paths(tmp_path: Path) -> None:
    reg = VenueRegistry(tmp_path / "venues.json")
    rows = [
        row(
            "google_places",
            "Keng Eng Kee Seafood",
            source_ref="ChIJ_kek",
            postal_code="150124",
            phone="<PHONE>",
            address="124 Bukit Merah Lane 1, Singapore 150124",
            rating=4.3,
            review_count=2100,
            price_level=1,
            cuisine=["chinese_restaurant", "seafood_restaurant"],
            opening_hours={
                "weekday_descriptions": [
                    "Monday: 11:30 AM – 2:30 PM, 5:00 – 10:30 PM",
                    "Saturday: 11:30 AM – 10:30 PM",
                    "Sunday: 11:30 AM – 10:30 PM",
                ]
            },
            extra={
                "good_for_groups": True,
                "good_for_children": True,
                "reservable": True,
                "reviews": [
                    {
                        "rating": 5,
                        "published_at": "2026-05-01T10:00:00Z",
                        "snippet": "Great family zi char, saw it on tiktok",
                        "social_words": 1,
                    }
                ],
            },
        ),
        row(
            "sethlui",
            "Keng Eng Kee Seafood (琼荣记)",
            postal_code="150124",
            name_zh="琼荣记",
            snippet="romantic? no, but family favourite",
            extra={"article_title": "Best zi char", "published_at": "2026-03-14"},
        ),
        row("burpple", "KEK Seafood", phone="<PHONE>"),  # phone relink
        row(
            "michelin",
            "Keng Eng Kee Seafood",
            michelin="Bib Gourmand",
            website="https://guide.michelin.com/x",
        ),
        row(
            "reddit", "Keng Eng Kee", confidence=0.6, snippet="**Keng Eng Kee** is the one"
        ),  # fuzzy, low conf attaches
        row("reddit", "Some Unknown Stall", confidence=0.4),  # low conf, never creates
        row(
            "sfa",
            "KENG ENG KEE SEAFOOD",
            postal_code="150124",
            hygiene_grade="A",
            confidence=0.0,
            extra={"enrich_only": True},
        ),
        row(
            "sfa",
            "Random Licensee",
            postal_code="999999",
            hygiene_grade="C",
            confidence=0.0,
            extra={"enrich_only": True},
        ),
        row(
            "google_places",
            "Keng Eng Kee Seafood",
            source_ref="ChIJ_kek",
            category="family_weekend",
            query="family restaurants",
            rating=4.3,
            review_count=2100,
        ),
        row(
            "chope",
            "Two Chefs Eating Place",
            postal_code="140116",
            booking_url="https://chope.test/two-chefs",
        ),
    ]
    stats = reg.ingest(rows, RUN, independent_sources={"sethlui", "burpple", "michelin", "reddit"})
    assert stats.created == 2 and stats.skipped_low_confidence == 2
    assert (
        stats.by_method["place_id"] == 1
        and stats.by_method["phone"] == 1
        and stats.by_method["name_postal"] >= 1
    )
    kek = next(v for v in reg.venues.values() if v.name == "Keng Eng Kee Seafood")
    assert kek.id == "v00001"
    assert (
        kek.postal_code == "150124"
        and kek.region == "Central"
        and (kek.district or "").startswith("D03")
    )
    assert kek.name_zh == "琼荣记" and "KEK Seafood" in kek.aliases
    assert kek.cuisine == ["Chinese", "Seafood"] and kek.price_level == 1
    assert kek.hours is not None
    assert (
        kek.hours["mon"] == [["11:30", "14:30"], ["17:00", "22:30"]] and kek.open_weekends is True
    )
    assert kek.michelin == "Bib Gourmand" and kek.hygiene_grade == "A"
    assert kek.flags["good_for_groups"] is True and kek.flags["kid_friendly"] is True
    assert kek.ratings["google_places"] == {"rating": 4.3, "review_count": 2100}
    assert kek.social_words == 1 and len(kek.review_snippets) == 1
    assert sorted(kek.independent_sources) == ["burpple", "michelin", "reddit", "sethlui"]
    assert kek.category_keys == ["family_weekend", "zichar_family"]
    assert kek.earliest_evidence == "2026-03-14"
    methods = {m.method for m in stats.merges}
    assert "phone" in methods and "fuzzy_name" in methods
    assert all(m.kept_venue_id == kek.id for m in stats.merges)
    # persistence keeps ids stable
    reg.save()
    reg2 = VenueRegistry(tmp_path / "venues.json")
    assert reg2.venues["v00001"].name == "Keng Eng Kee Seafood" and reg2.venues["v00001"].evidence
    stats2 = reg2.ingest(
        [row("eatbook", "Keng Eng Kee Seafood", postal_code="150124")],
        "20260908T000000Z",
        {"eatbook"},
    )
    assert stats2.created == 0 and reg2.venues["v00001"].last_seen_run == "20260908T000000Z"
    assert reg2.venues["v00001"].first_seen_run == RUN


def test_fuzzy_requires_compatible_postal(tmp_path: Path) -> None:
    reg = VenueRegistry(tmp_path / "venues.json")
    reg.ingest([row("sethlui", "Ah Hua Zi Char", postal_code="310123")], RUN, {"sethlui"})
    reg.ingest(
        [row("eatbook", "Ah Hua Zichar", postal_code="460999")], RUN, {"eatbook"}
    )  # different outlet
    assert len(reg.venues) == 2
    stats = reg.ingest(
        [row("burpple", "Ah Hua Zichar")], RUN, {"burpple"}
    )  # no postal: fuzzy attach allowed
    assert stats.attached == 1


def test_build_venues_writes_files(app_config: AppConfig, tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    rows = [
        row("sethlui", "Cafe A", postal_code="160056"),
        row("burpple", "Cafe A", phone="<PHONE>", postal_code="160056"),
        row("eatbook", "Kafe A", phone="<PHONE>"),
    ]
    registry, stats = build_venues(app_config, rows, RUN, run_dir)
    assert len(registry.venues) == 1 and stats.created == 1
    assert (app_config.resolve(app_config.settings.paths.data_dir) / "venues.json").exists()
    seen = json.loads((run_dir / "venues.json").read_text())
    assert len(seen) == 1 and seen[0]["name"] == "Cafe A"
    merges = (run_dir / "merges.jsonl").read_text().splitlines()
    assert len(merges) == 1 and json.loads(merges[0])["method"] == "phone"


# --- scoring ---------------------------------------------------------------------------------
def test_bayesian_rating_prefers_volume() -> None:
    small = bayesian_rating(4.9, 12, prior_rating=4.2, prior_count=100)
    big = bayesian_rating(4.6, 2000, prior_rating=4.2, prior_count=100)
    assert small is not None and big is not None and big > small
    assert bayesian_rating(None, 5, 4.2, 100) is None


def test_recency_weight() -> None:
    today = date(2026, 9, 1)
    assert recency_weight("2026-09-01", today, 18, 4) == 1.0
    assert abs(recency_weight("2025-03-01", today, 18, 4) - 0.5) < 0.02
    assert recency_weight("2020-01-01", today, 18, 4) == 0.05
    assert recency_weight(None, today, 18, 4) == 0.5


def _venue(vid: str, name: str, **kw) -> Venue:
    v = Venue(id=vid, name=name)
    for k, val in kw.items():
        setattr(v, k, val)
    return v


def test_score_category_ranking_filters_and_personal(app_config: AppConfig) -> None:
    cat = app_config.categories.get("zichar_family")
    s = app_config.settings.scoring
    today = date(2026, 9, 1)
    strong = _venue(
        "v1",
        "Strong",
        ratings={"google_places": {"rating": 4.6, "review_count": 2000}},
        open_weekends=True,
        michelin="Bib Gourmand",
        flags={"good_for_groups": True},
        evidence=[
            Evidence(
                "sethlui", "u1", published_at="2026-06-01", snippet="best zi char cereal prawn"
            ),
            Evidence("eatbook", "u2", published_at="2026-05-01"),
            Evidence("michelin", "u3", published_at="2026-01-01"),
        ],
    )
    tiny = _venue(
        "v2",
        "Tiny",
        ratings={"google_places": {"rating": 4.9, "review_count": 12}},
        open_weekends=True,
        evidence=[Evidence("burpple", "u4", published_at="2026-08-01", snippet="zi char")],
    )
    closed = _venue(
        "v3",
        "Closed",
        business_status="CLOSED_PERMANENTLY",
        ratings={"google_places": {"rating": 5.0, "review_count": 500}},
    )
    weekday = _venue(
        "v4",
        "Weekday Only",
        open_weekends=False,
        ratings={"google_places": {"rating": 4.8, "review_count": 900}},
    )
    dirty = _venue(
        "v5",
        "Dirty",
        ratings={"google_places": {"rating": 4.6, "review_count": 2000}},
        hygiene_grade="C",
        open_weekends=True,
        evidence=[Evidence("sethlui", "u5", published_at="2026-06-01")],
    )
    excluded = _venue(
        "v6", "Excluded", ratings={"google_places": {"rating": 4.9, "review_count": 5000}}
    )
    visited = _venue(
        "v7",
        "Visited",
        ratings={"google_places": {"rating": 4.7, "review_count": 3000}},
        open_weekends=True,
        evidence=[Evidence("sethlui", "u7", published_at="2026-08-01")],
    )
    personal = {
        "v6": PersonalNote(status="excluded"),
        "v7": PersonalNote(status="visited", my_rating=5.0),
    }
    result = score_category(
        cat,
        [strong, tiny, closed, weekday, dirty, excluded, visited],
        s,
        personal=personal,
        trends={"v2": "declining"},
        hide_visited=True,
        today=today,
    )
    by_id = {r.venue_id: r for r in result}
    assert by_id["v3"].excluded_reason == "closed permanently"
    assert by_id["v4"].excluded_reason == "not open on weekends"
    assert by_id["v6"].excluded_reason == "excluded by you"
    assert by_id["v1"].rank == 1 and by_id["v1"].adjustments["michelin"] == s.michelin_bonus
    assert by_id["v1"].adjustments["multi_source"] == s.multi_source_bonus
    assert by_id["v1"].score > by_id["v2"].score  # 4.6 x 2000 beats 4.9 x 12
    assert by_id["v2"].adjustments["declining_trend"] == -s.declining_trend_penalty
    assert (
        by_id["v5"].adjustments["poor_hygiene"] == -s.poor_hygiene_penalty
        and by_id["v5"].score < by_id["v1"].score
    )
    assert (
        by_id["v7"].hidden
        and by_id["v7"].rank == 0
        and by_id["v7"].adjustments["personal"] == s.personal_rating_weight
    )
    ranked = [r for r in result if not r.excluded_reason and not r.hidden]
    assert [r.rank for r in ranked] == list(range(1, len(ranked) + 1))
    assert result[-1].excluded_reason  # excluded venues come last
    assert (
        abs(
            sum(by_id["v1"].components.values())
            - (by_id["v1"].score - sum(by_id["v1"].adjustments.values()))
        )
        < 1e-9
    )


def test_newly_opened_filter(app_config: AppConfig) -> None:
    cat = app_config.categories.get("new_zichar")
    s = app_config.settings.scoring
    today = date(2026, 9, 1)
    new = _venue(
        "n1",
        "Brand New",
        earliest_evidence="2026-08-20",
        evidence=[Evidence("sethlui", "u", published_at="2026-08-20")],
    )
    old = _venue(
        "n2",
        "Old Timer",
        earliest_evidence="2024-01-01",
        evidence=[Evidence("sethlui", "u", published_at="2024-01-01")],
    )
    undated = _venue("n3", "Undated")
    result = {r.venue_id: r for r in score_category(cat, [new, old, undated], s, today=today)}
    assert result["n1"].rank == 1 and result["n1"].excluded_reason is None
    assert "older than 30 days" in (result["n2"].excluded_reason or "")
    assert result["n3"].excluded_reason == "no dated evidence for opening"
