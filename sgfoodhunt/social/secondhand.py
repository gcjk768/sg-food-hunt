"""Secondhand social signals: how often reviews and articles mention TikTok / Instagram / viral."""

from __future__ import annotations

import re

from sgfoodhunt.dedup.venue import Venue

SOCIAL_WORDS = (
    "tiktok",
    "instagram",
    "insta worthy",
    "insta-worthy",
    "instaworthy",
    "viral",
    "ig-worthy",
    "小红书",
    "xiaohongshu",
)
_RE = re.compile("|".join(re.escape(w) for w in SOCIAL_WORDS), re.IGNORECASE)


def secondhand_mentions(venue: Venue) -> int:
    texts = [r.get("snippet") or "" for r in venue.review_snippets]
    texts += [e.snippet or "" for e in venue.evidence]
    texts += [e.title or "" for e in venue.evidence]
    return sum(len(_RE.findall(t)) for t in texts if t)
