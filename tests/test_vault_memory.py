"""Vault movement log + memory: Activity format, append-only History, capped excerpt reaching
the AI prompt, vault-sent cards not re-sent, and vault errors never raising."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from sgfoodhunt import cli as cli_module
from sgfoodhunt.config import AppConfig
from sgfoodhunt.dedup import Evidence, Venue
from sgfoodhunt.diff import RunDiff
from sgfoodhunt.http.cache import ResponseCache
from sgfoodhunt.ranking import _memory
from sgfoodhunt.reporting.memory import (
    MEMORY_CAP,
    append_history,
    cap_excerpt,
    history_lines,
    last_card,
    log_activity,
    recent_activity,
    venue_memory,
)
from sgfoodhunt.reporting.venue_notes import write_venue_notes
from sgfoodhunt.reviews.analysis import analyse_venue
from sgfoodhunt.scoring import ScoredVenue
from sgfoodhunt.storage.vault import VENUES, Vault
from tests.test_ai import FakeClaude, _client

WHEN = datetime(2026, 10, 1, 19, 40, tzinfo=UTC)  # 03:40 SGT on 2026-10-02


def test_activity_line_format_in_sgt(vault: Vault) -> None:
    log_activity(vault, "▶️", "run started", "dry run", when=WHEN)
    log_activity(
        vault, "📨", "card sent", "#2 · Zi char", "SG Food Hunt/Venues/Kok Sen|Kok Sen", when=WHEN
    )
    text = (vault.root / "Activity" / "2026-10-02.md").read_text(encoding="utf-8")
    assert text.startswith("---\ntags: [sgfoodhunt/activity]\nupdated: 2026-10-02\n---\n")
    assert text.count("# Activity 2026-10-02") == 1  # header written once, then append-only
    assert text.splitlines()[-2:] == [
        "- 03:40 ▶️ **run started** · dry run",
        "- 03:40 📨 **card sent** · #2 · Zi char · [[SG Food Hunt/Venues/Kok Sen|Kok Sen]]",
    ]


def _scores(app_config: AppConfig, rank: int) -> dict[str, list[ScoredVenue]]:
    return {"zichar_family": [ScoredVenue(venue_id="v", name="Kok Sen", score=0.9, rank=rank)]}


def test_history_is_append_only_and_tracks_rank_moves(app_config: AppConfig, vault: Vault) -> None:
    v = Venue(id="v", name="Kok Sen")
    write_venue_notes(vault, app_config, [v], _scores(app_config, 5), "r1")
    path = vault.note_path(VENUES, "Kok Sen")
    path.write_text(path.read_text(encoding="utf-8") + "my own words\n", encoding="utf-8")
    write_venue_notes(vault, app_config, [v], _scores(app_config, 2), "r2")
    append_history(vault, "Kok Sen", ["- 2026-10-02 03:41 📨 **card sent** · #2 · Zi"])
    write_venue_notes(vault, app_config, [v], _scores(app_config, 2), "r3")  # no move: no line
    note = vault.read(VENUES, "Kok Sen")
    lines = history_lines(note)
    assert len(lines) == 4
    assert "🆕 **first seen** · run r1" in lines[0]
    assert "🆕 **entered top 15** · #5 · Best zi char" in lines[1]
    assert "⬆️ **moved up** · #5 → #2 · Best zi char" in lines[2]
    assert lines[3].endswith("**card sent** · #2 · Zi")
    assert note is not None and "my own words" in note.body  # ## My notes survives
    assert note.body.index("## History") < note.body.index("## My notes")
    assert last_card(vault, "Kok Sen") == "#2 · Zi"
    # the move went to the Activity log with a venue wikilink
    activity = recent_activity(vault, days=2)
    assert any(
        "moved up" in ln and "[[SG Food Hunt/Venues/Kok Sen|Kok Sen]]" in ln for ln in activity
    )


def test_memory_excerpt_is_capped_newest_first_and_reaches_prompt(
    app_config: AppConfig, vault: Vault, cache: ResponseCache
) -> None:
    v = Venue(
        id="v",
        name="Kok Sen",
        evidence=[
            Evidence("sethlui", "u", snippet="great"),
            Evidence("eatbook", "u2", snippet="ok"),
        ],
    )
    write_venue_notes(vault, app_config, [v], _scores(app_config, 5), "r1")
    old = [f"- 2026-09-{d:02d} 03:40 ⬇️ **moved down** · filler {'x' * 80}" for d in range(1, 29)]
    append_history(vault, "Kok Sen", [*old, "- 2026-10-01 03:41 📨 **card sent** · #5 · Zi"])
    excerpt = venue_memory(vault, "Kok Sen", [], cap=500)
    assert len(excerpt) <= 500 and excerpt.startswith("- 2026-10-01 03:41 📨 **card sent**")
    assert cap_excerpt(["a" * 10] * 1000) == "\n".join(["a" * 10] * 363)  # whole lines, ≤4000
    memory = _memory(vault, app_config, v, {"zichar_family": 2, "hawker_family": 40}, [])
    assert memory.startswith("Now: #2 · Best zi char") and "hawker" not in memory.lower()
    assert "Last run: #5 · Best zi char" in memory  # from the note, before it is rewritten
    assert len(memory) <= MEMORY_CAP and "card sent" in memory
    assert _memory(vault, app_config, v, {"zichar_family": 40}, []) == ""  # not in a top list

    fake = FakeClaude(
        {
            "reviews": {
                "food": 0.9,
                "service": 0.5,
                "ambience": 0.5,
                "value": 0.5,
                "noise_level": "loud",
                "summary": "Moved up to #2: wok hei.",
            }
        }
    )
    analyse_venue(v, app_config, {"zichar_family": 2}, ai=_client(cache, fake), memory=memory)
    prompt = fake.calls[-1][2]
    assert "vault log, newest first" in prompt and "**card sent** · #5 · Zi" in prompt
    assert v.summary == "Moved up to #2: wok hei."


def test_vault_errors_never_raise(tmp_path: Path, app_config: AppConfig) -> None:
    blocker = tmp_path / "file-not-dir"
    blocker.write_text("x")
    broken = Vault(blocker, "SG Food Hunt")  # every path under it is invalid
    log_activity(broken, "▶️", "run started")
    append_history(broken, "Kok Sen", ["- line"])
    assert recent_activity(broken) == []
    assert venue_memory(broken, "Kok Sen", ["x"]) == ""
    assert last_card(broken, "Kok Sen") is None
    assert write_venue_notes(broken, app_config, [Venue(id="v", name="A")], {}, "r1") == 0


def test_card_already_sent_per_vault_is_not_resent(
    app_config: AppConfig, vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    app_config.settings.notifications.telegram = True
    write_venue_notes(vault, app_config, [Venue(id="v", name="Kok Sen")], {}, "r1")
    write_venue_notes(vault, app_config, [Venue(id="w", name="Hai Kee")], {}, "r1")
    cards = [
        ({"id": "v", "name": "Kok Sen"}, [(2, "Zi")], "card v"),
        ({"id": "w", "name": "Hai Kee"}, [(3, "Zi")], "card w"),
    ]
    monkeypatch.setattr(cli_module, "venue_cards", lambda *a, **k: cards)
    sent: list[list[str] | None] = []

    def fake_notify(config, text, subject, messages=None):  # type: ignore[no-untyped-def]
        sent.append(messages)
        return type("R", (), {"telegram": "sent", "email": None})()

    monkeypatch.setattr(cli_module, "notify", fake_notify)
    change = RunDiff(run_id="r2", prev_run_id="r1", new_venues=[{"venue_id": "v", "name": "x"}])
    cli_module._notify(app_config, change)
    assert sent == [["card v", "card w"]] and last_card(vault, "Kok Sen") == "#2 · Zi"
    cards[1] = ({"id": "w", "name": "Hai Kee"}, [(1, "Zi")], "card w moved")
    cli_module._notify(app_config, change)  # same ranks for v: skipped; w moved: re-sent
    assert sent[-1] == ["card w moved"]
    assert any("card skipped" in ln for ln in recent_activity(vault, days=2))
