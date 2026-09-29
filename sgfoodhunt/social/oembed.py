"""TikTok's public oEmbed endpoint: caption (title) and creator for a video URL. Cached."""

from __future__ import annotations

import logging
from datetime import timedelta

from sgfoodhunt.http.cache import CacheMiss
from sgfoodhunt.http.client import AsyncApiClient, FetchError
from sgfoodhunt.social.models import SocialMention

log = logging.getLogger(__name__)
OEMBED_URL = "https://www.tiktok.com/oembed"


class TikTokOEmbed:
    def __init__(self, client: AsyncApiClient, ttl_days: int = 180) -> None:
        self.client = client
        self.ttl = timedelta(days=ttl_days)
        self.requests = 0
        self.cache_hits = 0
        self.failed = 0

    async def fill(self, mention: SocialMention) -> bool:
        if mention.platform != "tiktok" or not mention.url or mention.caption:
            return False
        try:
            payload, cached = await self.client.request_json(
                "GET", OEMBED_URL, params={"url": mention.url}, ttl=self.ttl
            )
        except CacheMiss:
            return False
        except FetchError as exc:
            self.failed += 1
            log.debug("oembed failed for %s: %s", mention.url, exc)
            return False
        self.cache_hits += int(cached)
        self.requests += int(not cached)
        if not isinstance(payload, dict):
            return False
        mention.caption = (
            (payload.get("title") or None) if isinstance(payload.get("title"), str) else None
        )
        handle = payload.get("author_unique_id") or payload.get("author_name")
        mention.creator_handle = str(handle) if handle else mention.creator_handle
        return True
