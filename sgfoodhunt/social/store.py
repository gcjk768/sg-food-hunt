"""Mentions on disk: data/social/mentions.jsonl (deduplicated) and unmatched.jsonl for review."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from sgfoodhunt.http.cache import atomic_write_text
from sgfoodhunt.social.models import SocialMention


class SocialStore:
    def __init__(self, data_dir: Path | str) -> None:
        self.dir = Path(data_dir) / "social"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.mentions_path = self.dir / "mentions.jsonl"
        self.unmatched_path = self.dir / "unmatched.jsonl"

    def load(self) -> list[SocialMention]:
        if not self.mentions_path.exists():
            return []
        out = []
        with self.mentions_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    out.append(SocialMention.from_dict(json.loads(line)))
        return out

    def upsert(self, mentions: Iterable[SocialMention]) -> tuple[int, int]:
        """Merge into the file; returns (new, updated). A later match for a stored mention wins."""
        existing = {m.key: m for m in self.load()}
        new = updated = 0
        for m in mentions:
            if m.key in existing:
                cur = existing[m.key]
                if (
                    m.venue_id and (cur.venue_id != m.venue_id or cur.matched_by != m.matched_by)
                ) or (not cur.caption and m.caption):
                    existing[m.key] = m
                    updated += 1
            else:
                existing[m.key] = m
                new += 1
        atomic_write_text(
            self.mentions_path,
            "".join(json.dumps(m.to_dict(), ensure_ascii=False) + "\n" for m in existing.values()),
        )
        return new, updated

    def log_unmatched(
        self, run_id: str, mention: SocialMention, candidates: list[dict[str, Any]]
    ) -> None:
        row = {
            "run_id": run_id,
            "source": mention.source,
            "url": mention.url,
            "caption": mention.caption,
            "creator_handle": mention.creator_handle,
            "candidates": candidates,
            "resolved_venue_id": None,
        }
        with self.unmatched_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    def manual_resolutions(self) -> dict[str, str]:
        """{url: venue_id} for unmatched rows you resolved by hand (edit resolved_venue_id)."""
        out: dict[str, str] = {}
        if not self.unmatched_path.exists():
            return out
        with self.unmatched_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                row = json.loads(line)
                if row.get("resolved_venue_id") and row.get("url"):
                    out[row["url"]] = row["resolved_venue_id"]
        return out
