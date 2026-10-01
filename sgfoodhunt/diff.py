"""Diff between a run and the previous completed run: top list churn, rating moves, closures,
new venues, new sources and new social mentions. Stored as ``diff.json`` in the run folder."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from sgfoodhunt.config import AppConfig
from sgfoodhunt.http.cache import read_json, write_json
from sgfoodhunt.notify import esc
from sgfoodhunt.storage.runs import RunRecord, RunStore


@dataclass(slots=True)
class CategoryDiff:
    key: str
    display_name: str
    entered: list[dict[str, Any]] = field(default_factory=list)  # {venue_id, name, rank}
    left: list[dict[str, Any]] = field(default_factory=list)  # {venue_id, name, prev_rank}


@dataclass(slots=True)
class RunDiff:
    run_id: str
    prev_run_id: str | None
    categories: list[CategoryDiff] = field(default_factory=list)
    rating_changes: list[dict[str, Any]] = field(default_factory=list)
    newly_closed: list[dict[str, Any]] = field(default_factory=list)
    new_venues: list[dict[str, Any]] = field(default_factory=list)
    new_sources: list[dict[str, Any]] = field(default_factory=list)
    new_social_mentions: list[dict[str, Any]] = field(default_factory=list)
    reopened: list[dict[str, Any]] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (
            any(c.entered or c.left for c in self.categories)
            or self.rating_changes
            or self.newly_closed
            or self.new_venues
            or self.new_sources
            or self.new_social_mentions
            or self.reopened
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "prev_run_id": self.prev_run_id,
            "categories": [
                {"key": c.key, "display_name": c.display_name, "entered": c.entered, "left": c.left}
                for c in self.categories
            ],
            "rating_changes": self.rating_changes,
            "newly_closed": self.newly_closed,
            "reopened": self.reopened,
            "new_venues": self.new_venues,
            "new_sources": self.new_sources,
            "new_social_mentions": self.new_social_mentions,
        }


def _load_scores(run_dir: Path) -> dict[str, list[dict[str, Any]]]:
    path = run_dir / "scores.json"
    if not path.exists():
        return {}
    data: dict[str, list[dict[str, Any]]] = read_json(path)
    return data


def _load_venues(run_dir: Path) -> dict[str, dict[str, Any]]:
    path = run_dir / "venues.json"
    if not path.exists():
        return {}
    return {v["id"]: v for v in read_json(path)}


def _top(scored: list[dict[str, Any]], n: int) -> dict[str, dict[str, Any]]:
    return {
        s["venue_id"]: s
        for s in scored
        if s.get("rank") and s["rank"] <= n and not s.get("excluded_reason")
    }


def _social_new(data_dir: Path, run_id: str) -> list[dict[str, Any]]:
    path = data_dir / "social" / "mentions.jsonl"
    if not path.exists():
        return []
    out = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            m = json.loads(line)
            if m.get("run_id") == run_id:
                out.append(
                    {
                        "url": m.get("url"),
                        "platform": m.get("platform"),
                        "venue_id": m.get("venue_id"),
                        "creator_handle": m.get("creator_handle"),
                        "post_date": m.get("post_date"),
                    }
                )
    return out


def previous_run(store: RunStore, run_id: str) -> RunRecord | None:
    runs = [
        r
        for r in store.list_runs()
        if r.status in ("ok", "partial")
        and r.run_id < run_id
        and (store.run_dir(r.run_id) / "scores.json").exists()
    ]
    return runs[-1] if runs else None


def compute_diff(
    config: AppConfig, store: RunStore, run_id: str, prev_run_id: str | None = None
) -> RunDiff:
    prev = previous_run(store, run_id) if prev_run_id is None else store.load_run(prev_run_id)
    diff = RunDiff(run_id=run_id, prev_run_id=prev.run_id if prev else None)
    cur_dir = store.run_dir(run_id)
    cur_scores, cur_venues = _load_scores(cur_dir), _load_venues(cur_dir)
    prev_scores = _load_scores(store.run_dir(prev.run_id)) if prev else {}
    prev_venues = _load_venues(store.run_dir(prev.run_id)) if prev else {}
    # registry gives us every venue ever seen, for names and first_seen
    registry_path = config.resolve(config.settings.paths.data_dir) / "venues.json"
    registry = (
        {v["id"]: v for v in read_json(registry_path).get("venues", [])}
        if registry_path.exists()
        else {}
    )
    names = {vid: v["name"] for vid, v in {**registry, **prev_venues, **cur_venues}.items()}
    top_n = config.settings.scoring.top_n

    for key, scored in cur_scores.items():
        cat = config.categories.get(key)
        cur_top = _top(scored, top_n)
        prev_top = _top(prev_scores.get(key, []), top_n)
        cd = CategoryDiff(key=key, display_name=cat.display_name)
        for vid, s in cur_top.items():
            if vid not in prev_top:
                cd.entered.append({"venue_id": vid, "name": names.get(vid, vid), "rank": s["rank"]})
        for vid, s in prev_top.items():
            if vid not in cur_top:
                cd.left.append(
                    {"venue_id": vid, "name": names.get(vid, vid), "prev_rank": s["rank"]}
                )
        cd.entered.sort(key=lambda e: e["rank"])
        cd.left.sort(key=lambda e: e["prev_rank"])
        if prev is not None and (cd.entered or cd.left):
            diff.categories.append(cd)

    threshold = config.settings.notifications.rating_change_threshold
    for vid, v in cur_venues.items():
        pv = prev_venues.get(vid) or registry.get(vid)
        cur_g = (v.get("ratings") or {}).get("google_places") or {}
        if prev and pv is not None:
            prev_g = (pv.get("ratings") or {}).get("google_places") or {}
            if cur_g.get("rating") is not None and prev_g.get("rating") is not None:
                delta = float(cur_g["rating"]) - float(prev_g["rating"])
                if abs(delta) >= threshold:
                    diff.rating_changes.append(
                        {
                            "venue_id": vid,
                            "name": v["name"],
                            "from": prev_g["rating"],
                            "to": cur_g["rating"],
                            "delta": round(delta, 2),
                        }
                    )
            if (v.get("business_status") or "").startswith("CLOSED") and not str(
                pv.get("business_status") or ""
            ).startswith("CLOSED"):
                diff.newly_closed.append(
                    {"venue_id": vid, "name": v["name"], "status": v["business_status"]}
                )
            if (
                str(pv.get("business_status") or "").startswith("CLOSED")
                and v.get("business_status") == "OPERATIONAL"
            ):
                diff.reopened.append({"venue_id": vid, "name": v["name"]})
            prev_sources = {e["source_key"] for e in pv.get("evidence", [])}
            added = sorted({e["source_key"] for e in v.get("evidence", [])} - prev_sources)
            if added:
                diff.new_sources.append({"venue_id": vid, "name": v["name"], "sources": added})
        if v.get("first_seen_run") == run_id:
            diff.new_venues.append(
                {
                    "venue_id": vid,
                    "name": v["name"],
                    "region": v.get("region"),
                    "categories": sorted(
                        {
                            c
                            for e in v.get("evidence", [])
                            for c in e.get("category_keys", [])
                            if c != "*"
                        }
                    ),
                }
            )
    diff.new_venues.sort(key=lambda e: e["name"])
    diff.rating_changes.sort(key=lambda e: -abs(e["delta"]))
    diff.new_social_mentions = _social_new(config.resolve(config.settings.paths.data_dir), run_id)
    write_json(cur_dir / "diff.json", diff.to_dict())
    return diff


def diff_markdown(diff: RunDiff, names_limit: int = 15) -> str:
    if diff.prev_run_id is None:
        head = "_First scored run: nothing to compare against yet._"
    else:
        head = f"Compared with run `{diff.prev_run_id}`."
    lines = [head, ""]
    for c in diff.categories:
        if not (c.entered or c.left):
            continue
        lines.append(f"**{c.display_name}**")
        for e in c.entered:
            lines.append(f"- ▲ entered top list at #{e['rank']}: {e['name']}")
        for e in c.left:
            lines.append(f"- ▼ left top list (was #{e['prev_rank']}): {e['name']}")
        lines.append("")
    if diff.rating_changes:
        lines.append("**Rating changes**")
        lines += [
            f"- {r['name']}: {r['from']} → {r['to']} ({r['delta']:+.2f})"
            for r in diff.rating_changes[:names_limit]
        ]
        lines.append("")
    if diff.newly_closed:
        lines.append("**Newly closed**")
        lines += [
            f"- {r['name']} ({r['status'].replace('_', ' ').lower()})" for r in diff.newly_closed
        ]
        lines.append("")
    if diff.reopened:
        lines.append("**Reopened**")
        lines += [f"- {r['name']}" for r in diff.reopened]
        lines.append("")
    if diff.new_venues:
        lines.append(f"**New venues ({len(diff.new_venues)})**")
        lines += [
            f"- {r['name']}{(' · ' + r['region']) if r.get('region') else ''}"
            for r in diff.new_venues[:names_limit]
        ]
        if len(diff.new_venues) > names_limit:
            lines.append(f"- … and {len(diff.new_venues) - names_limit} more")
        lines.append("")
    if diff.new_sources:
        lines.append("**New sources found**")
        lines += [
            f"- {r['name']}: {', '.join(r['sources'])}" for r in diff.new_sources[:names_limit]
        ]
        lines.append("")
    if diff.new_social_mentions:
        lines.append(f"**New social mentions:** {len(diff.new_social_mentions)}")
        lines.append("")
    if diff.is_empty and diff.prev_run_id is not None:
        lines.append("_No changes since the previous run._")
    return "\n".join(lines).rstrip() + "\n"


def diff_plain_text(diff: RunDiff, title: str = "SG Food Hunt weekly diff") -> str:
    md = diff_markdown(diff)
    text = (
        md.replace("**", "").replace("`", "").replace("▲", "+").replace("▼", "-").replace("→", "->")
    )
    return f"{title} ({diff.run_id})\n\n{text}"


def _link(url: str, label: str) -> str:
    return f'<a href="{esc(url)}">{esc(label)}</a>'


def _card(v: dict[str, Any], tops: list[tuple[int, str]]) -> str:
    """Telegram HTML card, the owner's fixed field order: name / ranks / address · MRT / facts /
    summary / link / sources (sources tucked into an expandable quote). Every value is escaped."""
    name = v["name"] + (f" ({v['name_zh']})" if v.get("name_zh") else "")
    ranked = sorted(tops)
    rank_lines = [f"#{rank} · {esc(label)}" for rank, label in ranked[:3]]
    lines = [f"🍽 <b>{esc(name)}</b> · {rank_lines[0]}" if rank_lines else f"🍽 <b>{esc(name)}</b>"]
    lines += [f"🏆 {r}" for r in rank_lines[1:]]
    if len(ranked) > 3:
        lines.append(f"<i>…and top 3 in {len(ranked) - 3} more lists</i>")
    where = v.get("address") or v.get("district") or v.get("region")
    if where:
        mrt = f" · 🚇 near {esc(v['nearest_mrt'])} MRT" if v.get("nearest_mrt") else ""
        lines.append(f"📍 <code>{esc(where)}</code>{mrt}")
    g = (v.get("ratings") or {}).get("google_places") or {}
    facts = [
        ", ".join(v.get("cuisine") or []),
        (v.get("price_text") or "").strip(" ·|"),
        f"★ {g['rating']} ({g.get('review_count', '?')})" if g.get("rating") else "",
    ]
    if any(facts):
        lines.append("🍴 " + esc(" · ".join(f for f in facts if f)))
    if v.get("summary"):
        lines.append(f"💬 {esc(v['summary'])}")
    if v.get("booking_url"):
        lines.append("🔗 " + _link(v["booking_url"], "Book a table"))
    elif v.get("website"):
        lines.append("🔗 " + _link(v["website"], "Website"))
    urls = list(dict.fromkeys(e["url"] for e in v.get("evidence", []) if e.get("url")))[:3]
    if urls:
        links = "  ·  ".join(_link(u, urlsplit(u).netloc.removeprefix("www.") or u) for u in urls)
        lines.append(f"<blockquote expandable>📚 Sources: {links}</blockquote>")
    return "\n".join(lines)


def card_ranks(tops: list[tuple[int, str]]) -> str:
    """The ranks a card is sent at, e.g. ``#2 · Zi char; #9 · Hawker`` (vault dedupe key)."""
    return "; ".join(f"#{r} · {label}" for r, label in sorted(tops))


def venue_messages(
    config: AppConfig, store: RunStore, diff: RunDiff, per_category: int = 3
) -> list[str]:
    """One message per venue that is newly in a category's top ``per_category`` (every top pick on
    the first run), best rank first. Venues in several top lists get one message listing them."""
    return [html for _v, _tops, html in venue_cards(config, store, diff, per_category)]


def venue_cards(
    config: AppConfig, store: RunStore, diff: RunDiff, per_category: int = 3
) -> list[tuple[dict[str, Any], list[tuple[int, str]], str]]:
    """``venue_messages`` with the venue dict and its (rank, list) pairs kept alongside."""
    cur_dir = store.run_dir(diff.run_id)
    cur_scores, venues = _load_scores(cur_dir), _load_venues(cur_dir)
    prev_scores = _load_scores(store.run_dir(diff.prev_run_id)) if diff.prev_run_id else {}
    tops: dict[str, list[tuple[int, str]]] = {}
    for key, scored in cur_scores.items():
        label = config.categories.get(key).display_name
        prev_top = _top(prev_scores.get(key, []), per_category)
        for vid, s in _top(scored, per_category).items():
            if vid not in prev_top and not s.get("hidden"):
                tops.setdefault(vid, []).append((s["rank"], label))
    # any venue that entered a top list (e.g. #15) also gets a card, so every update is a card
    for cd in diff.categories:
        for e in cd.entered:
            pairs = tops.setdefault(e["venue_id"], [])
            if (e["rank"], cd.display_name) not in pairs:
                pairs.append((e["rank"], cd.display_name))
    order = sorted(tops, key=lambda vid: (min(tops[vid])[0], -len(tops[vid])))
    return [
        (venues[vid], tops[vid], _card(venues[vid], tops[vid])) for vid in order if vid in venues
    ]
