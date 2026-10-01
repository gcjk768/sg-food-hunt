"""Obsidian notes for runs, sources and the home page."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from sgfoodhunt.config import AppConfig
from sgfoodhunt.diff import RunDiff, diff_markdown
from sgfoodhunt.http.cache import read_json
from sgfoodhunt.reporting.memory import now_sgt
from sgfoodhunt.storage.frontmatter import Note
from sgfoodhunt.storage.runs import RunRecord, RunStore
from sgfoodhunt.storage.vault import ACTIVITY, CATEGORIES, RUNS, VENUES, Vault


def _md_table(headers: list[str], rows: list[list[Any]]) -> str:
    def cell(v: Any) -> str:
        return str(v if v is not None else "").replace("|", "\\|").replace("\n", " ")

    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(" --- " for _ in headers) + "|"]
    lines.extend("| " + " | ".join(cell(v) for v in row) + " |" for row in rows)
    return "\n".join(lines)


def write_run_note(vault: Vault, store: RunStore, run: RunRecord, config: AppConfig) -> str:
    stats = run.stats or {}
    by_source = stats.get("candidates_by_source", {})
    skipped = stats.get("sources_skipped", {})
    events = list(store.iter_events(run.run_id))
    candidates = list(store.iter_candidates(run.run_id))

    per_category: dict[str, Counter[str]] = defaultdict(Counter)
    distinct_names: dict[str, set[str]] = defaultdict(set)
    for row in candidates:
        per_category[row["category_key"]][row["source_key"]] += 1
        distinct_names[row["category_key"]].add(row["name"].lower())

    source_rows = []
    for src in config.sources.sources:
        if src.key in by_source:
            state = "ran"
        elif src.key in skipped:
            state = f"skipped: {skipped[src.key]}"
        elif src.key in run.sources:
            state = "ran (0 candidates)"
        else:
            state = "not selected"
        source_rows.append([src.name, src.kind, by_source.get(src.key, 0), state])

    cat_rows = []
    for cat in config.categories.categories:
        if cat.key not in run.categories:
            continue
        cat_rows.append(
            [
                f"[[{vault.link_target(CATEGORIES, cat.display_name)}|{cat.display_name}]]",
                sum(per_category[cat.key].values()),
                len(distinct_names[cat.key]),
                ", ".join(f"{k} {v}" for k, v in per_category[cat.key].most_common(5)),
            ]
        )

    warn_rows = [
        [e["at"][11:19], e["level"], e["component"], e["message"][:140]]
        for e in events
        if e["level"] in ("warning", "error")
    ][:60]

    top_names: Counter[str] = Counter()
    for row in candidates:
        if row.get("confidence", 1.0) >= 0.5 and not (row.get("extra") or {}).get("enrich_only"):
            top_names[row["name"]] += 1
    frequent = [[name, n] for name, n in top_names.most_common(40)]

    social = (stats.get("ranking") or {}).get("social") or {}
    if not social or not social.get("enabled", False):
        social_section = "_social module disabled (`social.enabled: false`)_"
    else:
        social_section = (
            f"- Instagram export posts: {social.get('ig_export', 0)}, TikTok export videos: {social.get('tiktok_export', 0)} "
            f"(oEmbed filled {social.get('oembed_filled', 0)})\n"
            f"- SERP results: {social.get('serp', 0)}, hashtag posts: {social.get('hashtag', 0)}\n"
            f"- Matched to venues: {social.get('matched', 0)}, unmatched (see data/social/unmatched.jsonl): {social.get('unmatched', 0)}\n"
            f"- New mentions stored: {social.get('stored_new', 0)}; secondhand mentions in reviews/articles: {social.get('secondhand_total', 0)}\n"
            f"- Trending venues: {', '.join(social.get('trending') or []) or 'none'}\n"
            + ("".join(f"- skipped: {s}\n" for s in social.get("skipped") or []))
        )
    diff_path = store.run_dir(run.run_id) / "diff.json"
    diff_section = "_not computed yet (run `sgfh rank`)_"
    if diff_path.exists():
        d = read_json(diff_path)
        diff_obj = RunDiff(run_id=d["run_id"], prev_run_id=d.get("prev_run_id"))
        from sgfoodhunt.diff import CategoryDiff

        diff_obj.categories = [
            CategoryDiff(c["key"], c["display_name"], c["entered"], c["left"])
            for c in d.get("categories", [])
        ]
        diff_obj.rating_changes = d.get("rating_changes", [])
        diff_obj.newly_closed = d.get("newly_closed", [])
        diff_obj.reopened = d.get("reopened", [])
        diff_obj.new_venues = d.get("new_venues", [])
        diff_obj.new_sources = d.get("new_sources", [])
        diff_obj.new_social_mentions = d.get("new_social_mentions", [])
        diff_section = diff_markdown(diff_obj)
    ai_stats = stats.get("ai") or {}
    if not ai_stats:
        ai_section = "_disabled (`ai.enabled: false`)_"
    else:
        ai_section = (
            f"- CLI calls: {ai_stats.get('calls', 0)}, cache hits: {ai_stats.get('cache_hits', 0)}, "
            f"failures: {ai_stats.get('failures', 0)}, skipped over budget: {ai_stats.get('skipped_budget', 0)}\n"
            f"- Cost this run: ${ai_stats.get('cost_usd', 0):.2f}\n"
            f"- By task: {', '.join(f'{k} {v}' for k, v in (ai_stats.get('by_task') or {}).items()) or 'none'}"
        )
    body = f"""# Run {run.run_id}

Mode **{run.mode}**, status **{run.status}**. Started {run.started_at}, finished {run.finished_at}.

- Queries run: {stats.get("queries", 0)}
- Raw candidates: {stats.get("candidates", 0)} across {len(by_source)} sources
- Pages recorded: {stats.get("pages", 0)}
- HTTP requests: {stats.get("requests", 0)} (cache hits {stats.get("cache_hits", 0)})
- Warnings: {stats.get("warnings", 0)}, errors: {stats.get("errors", 0)}

## Sources

{_md_table(["Source", "Kind", "Candidates", "State"], source_rows)}

## Categories

{_md_table(["Category", "Sightings", "Distinct names", "Top sources"], cat_rows)}

## Most frequently surfaced names (raw, before dedup)

{_md_table(["Name", "Sightings"], frequent) if frequent else "_none_"}

## Warnings

{_md_table(["Time", "Level", "Component", "Message"], warn_rows) if warn_rows else "_none_"}

## Social buzz

{social_section}

## AI layer

{ai_section}

## Diff

{diff_section}

## My notes

"""
    note = Note(
        frontmatter={
            "type": "run",
            "run_id": run.run_id,
            "mode": run.mode,
            "status": run.status,
            "started_at": run.started_at,
            "finished_at": run.finished_at,
            "candidates": stats.get("candidates", 0),
            "sources_run": stats.get("sources_run", []),
            "categories": run.categories,
            "tags": ["sgfoodhunt/run"],
        },
        body=body,
    )
    vault.write(RUNS, run.run_id, note)
    return vault.link_target(RUNS, run.run_id)


def write_sources_note(
    vault: Vault, config: AppConfig, robots_status: dict[str, str] | None = None
) -> None:
    robots_status = robots_status or {}
    rows = [
        [
            s.name,
            s.kind,
            s.fetch,
            "yes" if s.enabled else "no",
            s.tos_status,
            robots_status.get(s.key, ""),
            s.notes.strip().replace("\n", " "),
        ]
        for s in config.sources.sources
    ]
    body = f"""# Sources

Registry of every source in `config/sources.yaml`. `tos_status` is your own review of each site's
terms; sources marked `disallowed` never run. robots.txt is re-checked live on every run.

{_md_table(["Source", "Kind", "Fetch", "Enabled", "ToS", "robots.txt", "Notes"], rows)}

## My notes

"""
    vault.write(
        None,
        "Sources",
        Note(frontmatter={"type": "sources", "tags": ["sgfoodhunt/meta"]}, body=body),
    )


def write_home_note(vault: Vault, config: AppConfig, latest_run_link: str | None) -> None:
    cats = "\n".join(
        f"- [[{vault.link_target(CATEGORIES, c.display_name)}|{c.display_name}]] "
        f"(party of {c.party_size}, {c.group})"
        for c in config.categories.categories
    )
    latest = f"Latest run: [[{latest_run_link}]]" if latest_run_link else "No runs yet."
    today = f"{now_sgt():%Y-%m-%d}"
    latest += (
        f"\nMovement log: [[{vault.link_target(ACTIVITY, today)}|Activity {today}]] "
        "(one note per day in `Activity/`; each venue note keeps a `## History`)."
    )
    body = f"""# SG Food Hunt

{latest}

## Categories

{cats}

## Venues

Venue notes live in `{VENUES}/` (created from stage 2 onwards). Each note carries its data as
properties, so Obsidian's Bases and Dataview can filter them. Mark a venue in its properties:
`status: visited | wishlist | excluded`, `my_rating: 1-5`, `my_comment`. Those fields and any
`## My ...` section survive regeneration.

```dataview
TABLE region, price_level, google_rating, best_for
FROM "{vault.folder}/{VENUES}"
WHERE status = "wishlist"
SORT google_rating DESC
```

## Runs

Run reports are in `{RUNS}/`, one note per run with the diff against the previous run.

- [[{vault.link_target(None, "Sources")}|Sources]]

## My notes

"""
    vault.write(
        None, "Home", Note(frontmatter={"type": "home", "tags": ["sgfoodhunt/meta"]}, body=body)
    )
