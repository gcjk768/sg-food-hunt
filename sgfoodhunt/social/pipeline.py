"""Run every configured social route, match mentions to venues, store them, compute buzz."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from sgfoodhunt.ai.client import AiClient
from sgfoodhunt.ai.tasks import match_caption
from sgfoodhunt.config import AppConfig
from sgfoodhunt.dedup.venue import Venue
from sgfoodhunt.http.cache import ResponseCache
from sgfoodhunt.http.client import AsyncApiClient
from sgfoodhunt.social.buzz import BuzzInfo, buzz_scores
from sgfoodhunt.social.exports import parse_instagram_export, parse_tiktok_export
from sgfoodhunt.social.hashtags import InstagramHashtagClient
from sgfoodhunt.social.matcher import VenueMatcher
from sgfoodhunt.social.models import SocialMention
from sgfoodhunt.social.oembed import TikTokOEmbed
from sgfoodhunt.social.secondhand import secondhand_mentions
from sgfoodhunt.social.serp import SerpClient
from sgfoodhunt.social.store import SocialStore

log = logging.getLogger(__name__)


@dataclass(slots=True)
class SocialStats:
    enabled: bool = True
    ig_export: int = 0
    tiktok_export: int = 0
    oembed_filled: int = 0
    serp: int = 0
    hashtag: int = 0
    matched: int = 0
    unmatched: int = 0
    stored_new: int = 0
    stored_updated: int = 0
    requests: int = 0
    secondhand_total: int = 0
    skipped: list[str] = field(default_factory=list)
    buzz: dict[str, BuzzInfo] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "ig_export": self.ig_export,
            "tiktok_export": self.tiktok_export,
            "oembed_filled": self.oembed_filled,
            "serp": self.serp,
            "hashtag": self.hashtag,
            "matched": self.matched,
            "unmatched": self.unmatched,
            "stored_new": self.stored_new,
            "stored_updated": self.stored_updated,
            "requests": self.requests,
            "secondhand_total": self.secondhand_total,
            "skipped": self.skipped,
            "trending": sorted(v for v, b in self.buzz.items() if b.trending),
        }


def _client(config: AppConfig, cache: ResponseCache, name: str, offline: bool) -> AsyncApiClient:
    return AsyncApiClient(
        config.settings.http,
        cache,
        rpm=config.settings.api_rate_limits.get(name, 30),
        dry_run=offline,
    )


async def run_social(
    config: AppConfig,
    cache: ResponseCache,
    venues: dict[str, Venue],
    run_id: str,
    offline: bool = False,
    exports_only: bool = False,
    serp_client: SerpClient | None = None,
    oembed: TikTokOEmbed | None = None,
    hashtag_client: InstagramHashtagClient | None = None,
    ai: AiClient | None = None,
) -> SocialStats:
    """``offline`` uses cached responses only; ``exports_only`` (``--social-only``) parses the
    exports and secondhand mentions and makes no network calls at all."""
    stats = SocialStats()
    social = config.settings.social
    if not social.enabled:
        stats.enabled = False
        stats.skipped.append("social.enabled is false")
        return stats
    data_dir = config.resolve(config.settings.paths.data_dir)
    store = SocialStore(data_dir)
    matcher = VenueMatcher(venues)
    mentions: list[SocialMention] = []

    # 1. personal exports
    exports_dir = config.resolve(social.exports_dir)
    ig_dir = _first_existing(exports_dir, ("instagram",))
    tt_dir = _first_existing(exports_dir, ("tiktok",))
    if ig_dir:
        found = parse_instagram_export(ig_dir)
        stats.ig_export = len(found)
        mentions.extend(found)
    else:
        stats.skipped.append(f"no instagram export under {exports_dir}")
    if tt_dir:
        found = parse_tiktok_export(tt_dir)
        stats.tiktok_export = len(found)
        mentions.extend(found)
    else:
        stats.skipped.append(f"no tiktok export under {exports_dir}")

    known = {m.key: m for m in store.load()}
    if not exports_only:
        oembed = oembed or TikTokOEmbed(_client(config, cache, "tiktok_oembed", offline))
        for m in mentions:
            if m.platform == "tiktok" and not m.caption:
                cached_m = known.get(m.key)
                if cached_m and cached_m.caption:
                    m.caption, m.creator_handle = cached_m.caption, cached_m.creator_handle
                elif await oembed.fill(m):
                    stats.oembed_filled += 1
        stats.requests += oembed.requests
    else:
        for m in mentions:
            cached_m = known.get(m.key)
            if cached_m and cached_m.caption and not m.caption:
                m.caption, m.creator_handle = cached_m.caption, cached_m.creator_handle

    # 3. search engine index
    if not exports_only:
        api_key = config.secrets.serp_api_key
        if api_key or serp_client:
            serp_client = serp_client or SerpClient(
                _client(config, cache, "serp", offline),
                api_key or "",
                endpoint=social.serp_endpoint,
                max_results=social.serp_max_results,
            )
            for cat in config.categories.categories:
                for query in cat.queries:
                    for site in social.serp_sites:
                        found = await serp_client.search(query, site)
                        for m in found:
                            m.query = f"{cat.key}: {query}"
                        mentions.extend(found)
                        stats.serp += len(found)
            stats.requests += serp_client.requests
        else:
            stats.skipped.append("SERP_API_KEY not set")
        # 4. hashtag search
        token, biz = (
            config.secrets.instagram_graph_token,
            config.secrets.instagram_business_account_id,
        )
        if (token and biz and social.hashtags) or hashtag_client:
            hashtag_client = hashtag_client or InstagramHashtagClient(
                _client(config, cache, "instagram_graph", offline),
                token or "",
                biz or "",
                data_dir / "social" / "hashtag_log.json",
            )
            for tag in social.hashtags[:25]:
                found = await hashtag_client.recent_media(tag)
                mentions.extend(found)
                stats.hashtag += len(found)
            stats.requests += hashtag_client.requests
        elif not social.hashtags:
            stats.skipped.append("no hashtags configured")
        else:
            stats.skipped.append("INSTAGRAM_GRAPH_TOKEN / INSTAGRAM_BUSINESS_ACCOUNT_ID not set")

    # match
    manual = store.manual_resolutions()
    for m in mentions:
        m.run_id = run_id
        if m.url and m.url in manual and manual[m.url] in venues:
            m.venue_id, m.matched_by, m.match_score = manual[m.url], "manual", 100.0
        else:
            hit = matcher.match(m.caption, m.creator_handle)
            if hit:
                m.venue_id, m.matched_by, m.match_score = hit.venue_id, hit.method, hit.score
            elif m.caption and ai is not None and ai.task_enabled("social_matching"):
                cands = [
                    (c["venue_id"], venues[c["venue_id"]].name)
                    for c in matcher.candidates(m.caption, m.creator_handle, n=8)
                    if c["venue_id"] in venues
                ]
                picked = match_caption(ai, m.caption, cands) if cands else None
                if picked:
                    m.venue_id, m.matched_by, m.match_score = picked[0], "ai", picked[1] * 100
        if m.venue_id:
            stats.matched += 1
        else:
            m.matched_by = "unmatched"
            stats.unmatched += 1
            if m.key not in known:
                store.log_unmatched(run_id, m, matcher.candidates(m.caption, m.creator_handle))
    stats.stored_new, stats.stored_updated = store.upsert(mentions)

    # 2. secondhand + buzz
    for v in venues.values():
        v.social_secondhand = secondhand_mentions(v)
        stats.secondhand_total += v.social_secondhand
    all_mentions = store.load()
    stats.buzz = buzz_scores(all_mentions, social)
    for v in venues.values():
        b = stats.buzz.get(v.id)
        v.buzz_score = round(b.score, 3) if b else 0.0
        v.social_mentions_window = b.mentions_window if b else 0
        v.trending_social = bool(b and b.trending)
        v.recent_social = [
            {
                "url": m.url,
                "platform": m.platform,
                "date": m.post_date,
                "creator": m.creator_handle,
                "source": m.source,
            }
            for m in sorted(
                (m for m in all_mentions if m.venue_id == v.id),
                key=lambda m: m.post_date or "",
                reverse=True,
            )[:8]
        ]
    log.info(
        "social: %d mentions (%d matched, %d unmatched), %d trending",
        len(mentions),
        stats.matched,
        stats.unmatched,
        sum(1 for b in stats.buzz.values() if b.trending),
    )
    return stats


def _first_existing(root: Path, names: tuple[str, ...]) -> Path | None:
    if not root.exists():
        return None
    for child in sorted(root.iterdir()):
        if child.name.lower().startswith(names):
            return child
    return None
