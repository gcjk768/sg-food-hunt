"""Social buzz module (optional, ``social.enabled`` in settings.yaml).

Routes, each its own parser: personal Instagram / TikTok exports, secondhand mentions in reviews
and articles, a SERP API restricted to instagram.com / tiktok.com, and the Instagram Hashtag
Search API. Instagram and TikTok are never scraped, and no commenter or viewer data is stored.
"""

from sgfoodhunt.social.buzz import BuzzInfo, buzz_scores
from sgfoodhunt.social.exports import parse_instagram_export, parse_tiktok_export
from sgfoodhunt.social.matcher import VenueMatcher
from sgfoodhunt.social.models import SocialMention
from sgfoodhunt.social.pipeline import SocialStats, run_social
from sgfoodhunt.social.secondhand import secondhand_mentions
from sgfoodhunt.social.store import SocialStore

__all__ = [
    "BuzzInfo",
    "SocialMention",
    "SocialStats",
    "SocialStore",
    "VenueMatcher",
    "buzz_scores",
    "parse_instagram_export",
    "parse_tiktok_export",
    "run_social",
    "secondhand_mentions",
]
