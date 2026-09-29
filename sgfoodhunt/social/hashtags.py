"""Instagram Hashtag Search (Graph API) for a fixed hashtag list, capped at 25 tags per week
(the platform allows 30 unique tags per rolling week). A local log enforces the cap."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sgfoodhunt.http.cache import CacheMiss, read_json, write_json
from sgfoodhunt.http.client import AsyncApiClient, FetchError
from sgfoodhunt.social.models import SocialMention

log = logging.getLogger(__name__)
GRAPH = "https://graph.facebook.com/v19.0"
WEEKLY_CAP = 25


class InstagramHashtagClient:
    def __init__(
        self,
        client: AsyncApiClient,
        token: str,
        business_account_id: str,
        log_path: Path,
        weekly_cap: int = WEEKLY_CAP,
    ) -> None:
        self.client = client
        self.token = token
        self.user_id = business_account_id
        self.log_path = log_path
        self.weekly_cap = weekly_cap
        self.requests = 0
        self.cache_hits = 0
        self.skipped_cap = 0

    def _recent_tags(self) -> dict[str, str]:
        if not self.log_path.exists():
            return {}
        data: dict[str, str] = read_json(self.log_path)
        cutoff = (datetime.now(UTC) - timedelta(days=7)).isoformat()
        return {tag: when for tag, when in data.items() if when >= cutoff}

    def _log_tag(self, tag: str, recent: dict[str, str]) -> None:
        recent[tag] = datetime.now(UTC).isoformat()
        write_json(self.log_path, recent)

    async def recent_media(self, hashtag: str) -> list[SocialMention]:
        tag = hashtag.lstrip("#").lower()
        recent = self._recent_tags()
        if tag not in recent and len(recent) >= self.weekly_cap:
            self.skipped_cap += 1
            log.warning("hashtag #%s skipped: %d tags already queried this week", tag, len(recent))
            return []
        try:
            search, cached = await self.client.request_json(
                "GET",
                f"{GRAPH}/ig_hashtag_search",
                params={"user_id": self.user_id, "q": tag, "access_token": self.token},
                ttl=timedelta(days=30),
                cache_salt="ig_hashtag_id",
            )
            self.cache_hits += int(cached)
            self.requests += int(not cached)
            tag_id = ((search.get("data") or [{}])[0]).get("id")
            if not tag_id:
                return []
            media, cached = await self.client.request_json(
                "GET",
                f"{GRAPH}/{tag_id}/recent_media",
                params={
                    "user_id": self.user_id,
                    "fields": "id,caption,permalink,timestamp,media_type",
                    "limit": 50,
                    "access_token": self.token,
                },
                ttl=timedelta(days=1),
                cache_salt="ig_hashtag_media",
            )
            self.cache_hits += int(cached)
            self.requests += int(not cached)
        except CacheMiss:
            return []
        except FetchError as exc:
            log.warning("hashtag #%s failed: %s", tag, exc)
            return []
        self._log_tag(tag, recent)
        out: list[SocialMention] = []
        for item in media.get("data") or []:
            out.append(
                SocialMention(
                    source="ig_hashtag",
                    platform="instagram",
                    url=item.get("permalink"),
                    caption=(item.get("caption") or None),
                    creator_handle=None,
                    post_date=str(item.get("timestamp") or "")[:10] or None,
                    hashtag=tag,
                )
            )
        return out


def parse_hashtag_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return list(payload.get("data") or [])
