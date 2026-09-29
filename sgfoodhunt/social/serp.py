"""Search engine index of instagram.com / tiktok.com through a SERP API (SerpAPI shaped).

Only the search results (post URL, title, snippet) are stored; post pages are never fetched.
"""

from __future__ import annotations

import logging
import re
from datetime import timedelta
from typing import Any

from sgfoodhunt.http.cache import CacheMiss
from sgfoodhunt.http.client import AsyncApiClient, FetchError
from sgfoodhunt.social.models import Platform, SocialMention

log = logging.getLogger(__name__)
DEFAULT_ENDPOINT = "https://serpapi.com/search.json"
HANDLE_RE = re.compile(r"(?:instagram\.com|tiktok\.com)/@?([A-Za-z0-9_.]+)")
DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})|([A-Z][a-z]{2} \d{1,2}, \d{4})")


class SerpClient:
    def __init__(
        self,
        client: AsyncApiClient,
        api_key: str,
        endpoint: str = DEFAULT_ENDPOINT,
        max_results: int = 10,
        ttl_days: int = 14,
    ) -> None:
        self.client = client
        self.api_key = api_key
        self.endpoint = endpoint
        self.max_results = max_results
        self.ttl = timedelta(days=ttl_days)
        self.requests = 0
        self.cache_hits = 0

    async def search(self, query: str, site: str) -> list[SocialMention]:
        platform: Platform = "instagram" if "instagram" in site else "tiktok"
        try:
            payload, cached = await self.client.request_json(
                "GET",
                self.endpoint,
                params={
                    "engine": "google",
                    "q": f"site:{site} {query}",
                    "num": self.max_results,
                    "gl": "sg",
                    "hl": "en",
                    "api_key": self.api_key,
                },
                ttl=self.ttl,
                cache_salt="serp",
            )
        except CacheMiss:
            return []
        except FetchError as exc:
            log.warning("SERP request failed for %r: %s", query, exc)
            return []
        self.cache_hits += int(cached)
        self.requests += int(not cached)
        out: list[SocialMention] = []
        for r in (payload.get("organic_results") or [])[: self.max_results]:
            link = r.get("link")
            if not isinstance(link, str) or site not in link:
                continue
            title, snippet = str(r.get("title") or ""), str(r.get("snippet") or "")
            handle = HANDLE_RE.search(link)
            date_match = DATE_RE.search(str(r.get("date") or "") + " " + snippet)
            out.append(
                SocialMention(
                    source="serp",
                    platform=platform,
                    url=link.split("?")[0],
                    caption=(title + " " + snippet).strip()[:400] or None,
                    creator_handle=handle.group(1)
                    if handle and handle.group(1) not in ("p", "reel", "explore", "video")
                    else None,
                    post_date=_normalise_date(date_match) if date_match else None,
                    query=query,
                )
            )
        return out


def _normalise_date(match: Any) -> str | None:
    from datetime import datetime

    text = match.group(1) or match.group(2)
    for fmt in ("%Y-%m-%d", "%b %d, %Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None
