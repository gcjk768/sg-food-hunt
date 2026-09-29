"""Reddit recommendation threads via the official API (OAuth client credentials).

Usernames and profile data are never stored: only the thread title and URL, and short
anonymised snippets around candidate venue names. Extraction is heuristic (bold text, list
items, "X at Y" patterns), so candidates carry a low ``confidence`` and only count once the
dedup stage matches them against a venue found by a stronger source.
"""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Any, ClassVar

from sgfoodhunt.ai.tasks import extract_venues
from sgfoodhunt.http.cache import CacheMiss
from sgfoodhunt.http.client import AsyncApiClient, FetchError
from sgfoodhunt.models import ScrapeResult, SearchQuery, SourcePage, clean_snippet
from sgfoodhunt.scrapers.base import BaseScraper

TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
API = "https://oauth.reddit.com"

BOLD_RE = re.compile(r"\*\*([^*\n]{3,60})\*\*")
LIST_RE = re.compile(
    r"^\s*(?:[-*•]|\d{1,2}[.)])\s+([A-Z][^\n:—–-]{2,60}?)(?:\s*[:—–-]|\s*\(|$)", re.M
)
AT_RE = re.compile(
    r"\b(?:at|from|try|tried|recommend(?:ed)?)\s+([A-Z][\w'&]+(?:\s+[A-Z][\w'&]+){0,4})"
)
AREAS = {
    "commonwealth",
    "bukit timah",
    "tiong bahru",
    "katong",
    "joo chiat",
    "tanjong pagar",
    "holland village",
    "holland v",
    "jurong",
    "jurong east",
    "jurong west",
    "tampines",
    "bedok",
    "punggol",
    "sengkang",
    "woodlands",
    "yishun",
    "ang mo kio",
    "amk",
    "bishan",
    "toa payoh",
    "clementi",
    "queenstown",
    "novena",
    "dempsey",
    "dempsey hill",
    "sentosa",
    "bugis",
    "kallang",
    "geylang",
    "serangoon",
    "hougang",
    "pasir ris",
    "changi",
    "marina bay",
    "raffles place",
    "robertson quay",
    "clarke quay",
    "boat quay",
    "little india",
    "kampong glam",
    "arab street",
    "haji lane",
    "keong saik",
    "duxton",
    "telok ayer",
    "amoy street",
    "orchard road",
    "somerset",
    "dhoby ghaut",
    "city hall",
    "bras basah",
    "outram",
    "redhill",
    "alexandra",
    "east coast",
    "siglap",
    "marine parade",
    "upper thomson",
    "thomson",
    "seletar",
    "sembawang",
    "choa chu kang",
    "bukit batok",
    "bukit panjang",
    "boon lay",
    "tuas",
    "harbourfront",
    "vivocity",
    "jalan besar",
    "lavender",
    "farrer park",
    "macpherson",
    "paya lebar",
    "ubi",
    "eunos",
    "kembangan",
    "simei",
    "tanah merah",
    "expo",
    "buona vista",
    "one north",
    "one-north",
    "kent ridge",
    "pasir panjang",
    "labrador park",
    "west coast",
    "ghim moh",
    "beauty world",
    "sixth avenue",
    "stevens",
    "newton",
    "tanglin",
    "river valley",
    "great world",
    "mount faber",
}
GENERIC = {
    "I",
    "The",
    "This",
    "That",
    "It",
    "Singapore",
    "Reddit",
    "Edit",
    "Also",
    "Yes",
    "No",
    "Not",
    "Best",
    "Good",
    "Great",
    "Nice",
    "Try",
    "Go",
    "Google",
    "Michelin",
    "Instagram",
    "TikTok",
    "MRT",
    "CBD",
    "Orchard",
    "Chinatown",
    "East",
    "West",
    "North",
    "South",
}


def extract_venue_names(text: str) -> list[tuple[str, float]]:
    """Return (name, confidence) pairs found in a comment."""
    found: dict[str, float] = {}
    for m in BOLD_RE.finditer(text):
        found[m.group(1).strip()] = max(found.get(m.group(1).strip(), 0), 0.6)
    for m in LIST_RE.finditer(text):
        found[m.group(1).strip()] = max(found.get(m.group(1).strip(), 0), 0.5)
    for m in AT_RE.finditer(text):
        found[m.group(1).strip()] = max(found.get(m.group(1).strip(), 0), 0.35)
    out = []
    for name, conf in found.items():
        words = name.split()
        if not words or words[0] in GENERIC or len(name) < 3 or name.lower() in {"the", "a"}:
            continue
        if all(w in GENERIC for w in words) or name.lower() in AREAS:
            continue
        out.append((name.strip(" .,!?"), conf))
    return out


class RedditScraper(BaseScraper):
    key: ClassVar[str] = "reddit"

    def missing_credentials(self) -> str | None:
        s = self.ctx.config.secrets
        if not (s.reddit_client_id and s.reddit_client_secret):
            return "REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET are not set"
        return None

    def _client(self) -> AsyncApiClient:
        ua = self.ctx.config.secrets.reddit_user_agent or self.ctx.config.settings.http.user_agent
        return self.ctx.api_factory("reddit", {"User-Agent": ua})

    async def _token(self, client: AsyncApiClient) -> str:
        s = self.ctx.config.secrets
        payload, _ = await client.request_json(
            "POST",
            TOKEN_URL,
            data={"grant_type": "client_credentials"},
            auth=(s.reddit_client_id or "", s.reddit_client_secret or ""),
            cacheable=False,
        )
        return str(payload["access_token"])

    @staticmethod
    def _walk_comments(children: list[dict[str, Any]], out: list[str], limit: int) -> None:
        for child in children:
            if len(out) >= limit:
                return
            data = child.get("data") or {}
            body = data.get("body")
            if isinstance(body, str) and body.strip() and body not in ("[deleted]", "[removed]"):
                out.append(body)
            replies = data.get("replies")
            if isinstance(replies, dict):
                RedditScraper._walk_comments(
                    (replies.get("data") or {}).get("children") or [], out, limit
                )

    async def search(self, query: SearchQuery) -> ScrapeResult:
        result = self.new_result(query)
        missing = self.missing_credentials()
        if missing:
            result.skipped_reason = missing
            return result
        opts = self.source.options
        client = self._client()
        try:
            token: str | None = None
            if not self.ctx.dry_run:
                token = await self._token(client)
            headers = {"Authorization": f"bearer {token}"} if token else {}
            for sub in opts.get("subreddits", ["singaporeeats", "singapore"]):
                payload, cached = await client.request_json(
                    "GET",
                    f"{API}/r/{sub}/search",
                    params={
                        "q": query.text,
                        "restrict_sr": 1,
                        "sort": "relevance",
                        "t": opts.get("time_filter", "year"),
                        "limit": int(opts.get("max_threads_per_query", 10)),
                    },
                    headers=headers,
                    ttl=self.ctx.ttl,
                )
                result.cache_hits += int(cached)
                result.requests_made += int(not cached)
                for child in (payload.get("data") or {}).get("children") or []:
                    thread = child.get("data") or {}
                    tid, title = thread.get("id"), thread.get("title")
                    if not tid or not title:
                        continue
                    url = f"https://www.reddit.com{thread.get('permalink', '')}"
                    page = SourcePage(self.source.key, url, title=title)
                    result.pages.append(page)
                    comments_payload, cached = await client.request_json(
                        "GET",
                        f"{API}/comments/{tid}",
                        params={"limit": int(opts.get("max_comments_per_thread", 100)), "depth": 2},
                        headers=headers,
                        ttl=self.ttl_for_thread(),
                    )
                    result.cache_hits += int(cached)
                    result.requests_made += int(not cached)
                    bodies: list[str] = []
                    if isinstance(comments_payload, list) and len(comments_payload) > 1:
                        self._walk_comments(
                            (comments_payload[1].get("data") or {}).get("children") or [],
                            bodies,
                            int(opts.get("max_comments_per_thread", 100)),
                        )
                    selftext = thread.get("selftext")
                    if isinstance(selftext, str):
                        bodies.insert(0, selftext)
                    seen: set[str] = set()
                    ai = self.ctx.ai
                    if ai is not None and ai.task_enabled("reddit_extraction"):
                        found = extract_venues(
                            ai, "\n\n".join(bodies), context=f"Reddit thread title: {title}"
                        )
                        if found is not None:
                            for ev in found:
                                key = ev.name.lower()
                                if key in seen:
                                    continue
                                seen.add(key)
                                cand = self.candidate(
                                    ev.name,
                                    source_ref=f"{url}#{key}",
                                    name_zh=ev.name_zh,
                                    snippet=clean_snippet(ev.note or title, 200),
                                    confidence=max(0.5, min(0.95, ev.confidence)),
                                    page=page,
                                    extra={
                                        "subreddit": sub,
                                        "thread_title": title,
                                        "area": ev.area,
                                        "thread_score": thread.get("score"),
                                        "thread_created_utc": thread.get("created_utc"),
                                        "extracted_by": "ai",
                                    },
                                )
                                if cand:
                                    result.candidates.append(cand)
                            bodies = []  # heuristics not needed when the model answered
                    for body in bodies:
                        for name, conf in extract_venue_names(body):
                            key = name.lower()
                            if key in seen:
                                continue
                            seen.add(key)
                            cand = self.candidate(
                                name,
                                source_ref=f"{url}#{key}",
                                snippet=clean_snippet(body, 200),
                                confidence=conf,
                                page=page,
                                extra={
                                    "subreddit": sub,
                                    "thread_title": title,
                                    "thread_score": thread.get("score"),
                                    "thread_created_utc": thread.get("created_utc"),
                                },
                            )
                            if cand:
                                result.candidates.append(cand)
        except CacheMiss as exc:
            result.warnings.append(f"dry run: not cached {exc}")
        except FetchError as exc:
            result.warnings.append(str(exc))
            self.log.warning("Reddit API failed: %s", exc)
        finally:
            await client.aclose()
        return result

    def ttl_for_thread(self) -> timedelta:
        return self.ctx.ttl
