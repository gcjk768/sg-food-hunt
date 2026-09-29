from __future__ import annotations

from pathlib import Path

from sgfoodhunt.models import ScrapeResult, SearchQuery, SourcePage, VenueCandidate
from sgfoodhunt.storage.frontmatter import (
    Note,
    merge_preserving_user_edits,
    parse_note,
    render_note,
    safe_filename,
)
from sgfoodhunt.storage.runs import RunStore, candidate_from_row, query_from_row
from sgfoodhunt.storage.vault import Vault


def test_frontmatter_roundtrip() -> None:
    note = Note(
        {"name": "Cafe X", "rating": 4.5, "tags": ["a"], "multi": "l1\nl2", "none": None},
        "# Cafe X\n\nbody\n",
    )
    text = render_note(note)
    assert text.startswith("---\nname: Cafe X\n")
    parsed = parse_note(text)
    assert parsed.frontmatter == {"name": "Cafe X", "rating": 4.5, "tags": ["a"], "multi": "l1\nl2"}
    assert parsed.body == "# Cafe X\n\nbody\n"


def test_parse_note_without_frontmatter() -> None:
    parsed = parse_note("just text")
    assert parsed.frontmatter == {} and parsed.body == "just text"


def test_merge_preserves_user_fields_and_sections() -> None:
    existing = parse_note(
        "---\nname: Old\nstatus: visited\nmy_rating: 4\ntags: [x]\n---\n# Old\n\n## Summary\nold summary\n\n## My notes\nloved the pasta\n- go back\n\n## My todo\nbook\n"
    )
    generated = Note(
        {"name": "New", "rating": 4.6, "status": "wishlist"},
        "# New\n\n## Summary\nnew summary\n\n## My notes\n\n",
    )
    merged = merge_preserving_user_edits(generated, existing)
    assert merged.frontmatter["name"] == "New"
    assert merged.frontmatter["status"] == "visited"  # user value wins
    assert merged.frontmatter["my_rating"] == 4 and merged.frontmatter["tags"] == ["x"]
    assert "new summary" in merged.body and "old summary" not in merged.body
    assert "loved the pasta\n- go back" in merged.body
    assert merged.body.rstrip().endswith(
        "## My todo\nbook"
    )  # section absent in template is appended


def test_merge_without_existing_returns_generated() -> None:
    g = Note({"a": 1}, "b")
    assert merge_preserving_user_edits(g, None) is g


def test_safe_filename() -> None:
    assert safe_filename('Cafe: "A/B" [x] #1?') == "Cafe A B x 1"
    assert safe_filename("   ") == "untitled"
    assert len(safe_filename("x" * 300)) == 120


def test_vault_write_read_and_preserve(tmp_path: Path) -> None:
    vault = Vault(tmp_path / "vault", "SG Food Hunt")
    vault.ensure()
    assert (vault.root / "Venues").is_dir()
    path = vault.write("Venues", "Cafe X", Note({"name": "Cafe X"}, "# Cafe X\n\n## My notes\n\n"))
    assert path == vault.root / "Venues" / "Cafe X.md"
    path.write_text(
        path.read_text().replace("## My notes\n", "## My notes\nmine\n") + "", encoding="utf-8"
    )
    edited = parse_note(path.read_text())
    edited.frontmatter["status"] = "visited"
    path.write_text(render_note(edited))
    vault.write(
        "Venues",
        "Cafe X",
        Note({"name": "Cafe X", "rating": 4.1}, "# Cafe X\n\n## Summary\nnew\n\n## My notes\n\n"),
    )
    final = vault.read("Venues", "Cafe X")
    assert final is not None
    assert final.frontmatter["status"] == "visited" and final.frontmatter["rating"] == 4.1
    assert "mine" in final.body and "new" in final.body
    assert vault.link_target("Venues", "Cafe X") == "SG Food Hunt/Venues/Cafe X"
    assert [p.name for p in vault.list_notes("Venues")] == ["Cafe X.md"]


def _result(source: str, names: list[str]) -> ScrapeResult:
    q = SearchQuery("cafes_date", "dating", "best cafes", 2)
    page = SourcePage(
        source, f"https://{source}.test/a", title="Article", published_at="2026-01-01"
    )
    r = ScrapeResult(source, q, pages=[page], warnings=["w1"], requests_made=1, cache_hits=2)
    r.candidates = [
        VenueCandidate(source, n, page=page, cuisine=["cafe"], extra={"k": 1}) for n in names
    ]
    return r


def test_run_store_lifecycle(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "data")
    run = store.create_run("collect", ["cafes_date"], ["sethlui", "burpple"])
    assert (store.run_dir(run.run_id) / "run.json").exists()
    store.append_result(run.run_id, _result("sethlui", ["A", "B"]))
    store.append_result(run.run_id, _result("burpple", ["C"]))
    store.append_result(
        run.run_id,
        ScrapeResult("reddit", SearchQuery("c", "dating", "q"), skipped_reason="no creds"),
    )
    store.finish_run(run, "ok", {"candidates": 3})

    loaded = store.load_run(run.run_id)
    assert loaded.status == "ok" and loaded.finished_at and loaded.stats == {"candidates": 3}
    assert store.candidate_counts(run.run_id) == {"burpple": 1, "sethlui": 2}
    rows = list(store.iter_candidates(run.run_id))
    assert [r["name"] for r in rows] == ["C", "A", "B"]  # files in sorted order
    assert rows[0]["page_url"] == "https://burpple.test/a" and rows[0]["extra"] == {"k": 1}
    pages = list(store.iter_pages(run.run_id))
    assert len(pages) == 2 and pages[0]["published_at"] == "2026-01-01"
    events = list(store.iter_events(run.run_id))
    assert [e["level"] for e in events] == ["warning", "warning", "info"]
    assert events[2]["message"] == "no creds"
    assert store.list_runs()[-1].run_id == run.run_id
    assert store.latest_run().run_id == run.run_id  # type: ignore[union-attr]

    cand = candidate_from_row(rows[0])
    assert cand.name == "C" and cand.page and cand.page.url == "https://burpple.test/a"
    q = query_from_row(rows[0])
    assert q.category_key == "cafes_date" and q.party_size == 2


def test_run_ids_do_not_collide(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "data")
    a = store.create_run("collect", [], [])
    b = store.create_run("collect", [], [])
    assert a.run_id != b.run_id


def test_atomic_write_uses_umask_not_mkstemp_0600(tmp_path) -> None:  # type: ignore[no-untyped-def]
    import os
    import stat

    from sgfoodhunt.http.cache import _UMASK, atomic_write_text

    p = tmp_path / "note.md"
    atomic_write_text(p, "x")
    if os.name == "posix":
        assert stat.S_IMODE(p.stat().st_mode) == 0o666 & ~_UMASK
