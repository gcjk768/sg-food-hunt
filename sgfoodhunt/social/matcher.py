"""Match a social mention to a venue by creator handle or by venue names inside the caption."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from rapidfuzz import fuzz

from sgfoodhunt.dedup.venue import Venue
from sgfoodhunt.normalise.names import name_key

HANDLE_THRESHOLD = 90
CAPTION_THRESHOLD = 88
MIN_KEY_CHARS = 5


def handle_key(text: str) -> str:
    return re.sub(r"[^a-z0-9一-鿿]", "", text.lower().replace("_", "").replace(".", ""))


@dataclass(slots=True)
class MatchResult:
    venue_id: str
    method: str
    score: float


class VenueMatcher:
    def __init__(self, venues: dict[str, Venue]) -> None:
        self._entries: list[tuple[str, str, str]] = []  # (venue_id, name_key, handle_key)
        for v in venues.values():
            names = [v.name, *v.aliases]
            if v.name_zh:
                names.append(v.name_zh)
            if v.brand:
                names.append(v.brand)
            for n in names:
                k = name_key(n)
                if len(k) >= MIN_KEY_CHARS or re.search(r"[一-鿿]", k):
                    self._entries.append((v.id, k, handle_key(n)))

    def by_handle(self, handle: str | None) -> MatchResult | None:
        if not handle:
            return None
        h = handle_key(handle)
        if len(h) < MIN_KEY_CHARS:
            return None
        best: MatchResult | None = None
        for vid, _k, hk in self._entries:
            if not hk:
                continue
            score = float(fuzz.ratio(h, hk))
            if hk in h and len(hk) >= MIN_KEY_CHARS:
                score = max(score, 95.0)
            if score >= HANDLE_THRESHOLD and (best is None or score > best.score):
                best = MatchResult(vid, "handle", score)
        return best

    def by_caption(self, caption: str | None) -> MatchResult | None:
        if not caption:
            return None
        text = name_key(caption, keep_noise=True)
        hashtags = " ".join(handle_key(t) for t in re.findall(r"#([\w一-鿿]+)", caption))
        best: MatchResult | None = None
        for vid, k, hk in self._entries:
            score = float(fuzz.partial_ratio(k, text)) if len(k) <= len(text) else 0.0
            if hk and len(hk) >= MIN_KEY_CHARS and hk in hashtags:
                score = max(score, 96.0)
            if score >= CAPTION_THRESHOLD and (best is None or score > best.score):
                best = MatchResult(vid, "caption", score)
        return best

    def candidates(
        self, caption: str | None, handle: str | None, n: int = 3
    ) -> list[dict[str, Any]]:
        """Best guesses for the unmatched log."""
        text = name_key(caption or "", keep_noise=True)
        h = handle_key(handle or "")
        scored: dict[str, float] = {}
        for vid, k, hk in self._entries:
            s = 0.0
            if text and len(k) <= len(text):
                s = float(fuzz.partial_ratio(k, text))
            if h and hk:
                s = max(s, float(fuzz.ratio(h, hk)))
            if s > scored.get(vid, 0):
                scored[vid] = s
        top = sorted(scored.items(), key=lambda kv: -kv[1])[:n]
        return [{"venue_id": vid, "score": round(s, 1)} for vid, s in top if s >= 60]

    def match(self, caption: str | None, handle: str | None) -> MatchResult | None:
        return self.by_handle(handle) or self.by_caption(caption)
