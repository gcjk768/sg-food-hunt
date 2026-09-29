"""Parsers for your own data exports.

Instagram "Download your information" (JSON format): saved posts live in
``your_instagram_activity/saved/saved_posts.json`` and liked posts in
``your_instagram_activity/likes/liked_posts.json``. Both carry the post URL, the creator handle
(as ``title``) and a timestamp; Instagram does not include captions for other people's posts.

TikTok "Download your data" (JSON format): one ``user_data*.json`` file with
``Activity -> Favorite Videos -> FavoriteVideoList`` and ``Activity -> Like List -> ItemFavoriteList``
entries of ``{Date, Link}``. Captions and creators come from the public oEmbed endpoint.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sgfoodhunt.social.models import SocialMention

log = logging.getLogger(__name__)

IG_SAVED_NAMES = ("saved_posts.json", "saved_collections.json")
IG_LIKED_NAMES = ("liked_posts.json",)


def _find_files(root: Path, names: tuple[str, ...]) -> list[Path]:
    if root.is_file():
        return [root] if root.name in names else []
    return [p for name in names for p in root.rglob(name)]


def _ts_to_date(ts: Any) -> str | None:
    try:
        return datetime.fromtimestamp(int(ts), tz=UTC).date().isoformat()
    except (TypeError, ValueError, OSError):
        return None


def _ig_entries(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, dict):
        for key in ("saved_saved_media", "likes_media_likes", "saved_saved_collections"):
            if isinstance(data.get(key), list):
                return [e for e in data[key] if isinstance(e, dict)]
        return []
    return [e for e in data if isinstance(e, dict)] if isinstance(data, list) else []


def parse_instagram_export(root: Path | str) -> list[SocialMention]:
    """Saved and liked posts from an Instagram export folder (or a single JSON file)."""
    root = Path(root)
    mentions: list[SocialMention] = []
    for kind, names in (("saved", IG_SAVED_NAMES), ("liked", IG_LIKED_NAMES)):
        for path in _find_files(root, names):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                log.warning("cannot read %s: %s", path, exc)
                continue
            for entry in _ig_entries(data):
                handle = entry.get("title") or None
                items: list[dict[str, Any]] = []
                smd = entry.get("string_map_data")
                if isinstance(smd, dict):
                    items.extend(v for v in smd.values() if isinstance(v, dict))
                sld = entry.get("string_list_data")
                if isinstance(sld, list):
                    items.extend(v for v in sld if isinstance(v, dict))
                for item in items:
                    url = item.get("href") or item.get("value")
                    if not isinstance(url, str) or "instagram.com" not in url:
                        continue
                    mentions.append(
                        SocialMention(
                            source="ig_export",
                            platform="instagram",
                            url=url.split("?")[0],
                            caption=None,
                            creator_handle=handle,
                            post_date=_ts_to_date(item.get("timestamp")),
                            query=kind,
                        )
                    )
    log.info("instagram export: %d posts from %s", len(mentions), root)
    return mentions


def _walk_tiktok(data: Any) -> list[tuple[str, str | None, str]]:
    """Yield (link, date, list_name) from the nested TikTok export structure."""
    found: list[tuple[str, str | None, str]] = []

    def visit(node: Any, name: str) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                visit(v, k)
        elif isinstance(node, list):
            for item in node:
                if isinstance(item, dict) and any(
                    key in item for key in ("Link", "link", "VideoLink")
                ):
                    link = item.get("Link") or item.get("link") or item.get("VideoLink")
                    date_raw = item.get("Date") or item.get("date")
                    if isinstance(link, str) and "tiktok" in link:
                        found.append(
                            (link.split("?")[0], str(date_raw)[:10] if date_raw else None, name)
                        )
                else:
                    visit(item, name)

    visit(data, "root")
    return found


def parse_tiktok_export(
    root: Path | str, lists: tuple[str, ...] = ("FavoriteVideoList", "ItemFavoriteList")
) -> list[SocialMention]:
    """Favourite and liked videos from a TikTok export (folder or the user_data JSON file)."""
    root = Path(root)
    files = [root] if root.is_file() else sorted(root.rglob("user_data*.json"))
    mentions: list[SocialMention] = []
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.warning("cannot read %s: %s", path, exc)
            continue
        for link, when, list_name in _walk_tiktok(data):
            if list_name not in lists:
                continue
            mentions.append(
                SocialMention(
                    source="tiktok_export",
                    platform="tiktok",
                    url=link,
                    post_date=when,
                    query="favourites" if list_name == "FavoriteVideoList" else "liked",
                )
            )
    log.info("tiktok export: %d videos from %s", len(mentions), root)
    return mentions
