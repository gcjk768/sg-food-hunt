"""Social mention record. Never holds commenter or viewer data: only the post itself."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

Platform = Literal["instagram", "tiktok"]
MentionSource = Literal["ig_export", "tiktok_export", "serp", "ig_hashtag"]


@dataclass(slots=True)
class SocialMention:
    source: MentionSource
    platform: Platform
    url: str | None
    caption: str | None = None
    creator_handle: str | None = None
    post_date: str | None = None  # ISO date
    venue_id: str | None = None
    matched_by: str | None = None  # handle | caption | manual | unmatched
    match_score: float | None = None
    run_id: str | None = None
    query: str | None = None
    hashtag: str | None = None

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.source, self.url or "", "" if self.url else (self.caption or "")[:120])

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SocialMention:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
