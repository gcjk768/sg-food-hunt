"""File based cache of raw responses with per entry expiry.

Layout under ``cache_dir``::

    http/ab/abcdef....meta.json   {url, method, status, content_type, fetched_at, expires_at}
    http/ab/abcdef....body        raw bytes
    robots/<host>.json            {body, status, fetched_at, expires_at}

Plain files so the cache works on a NAS share without database locking.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


class CacheMiss(LookupError):
    """Raised in dry run mode when a response is not cached."""


@dataclass(slots=True)
class CachedResponse:
    url: str
    status: int
    body: bytes
    content_type: str | None
    fetched_at: datetime
    from_cache: bool = True

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


def _now() -> datetime:
    return datetime.now(UTC)


def cache_key(method: str, url: str, body: bytes | str | None = None) -> str:
    h = hashlib.sha256()
    h.update(method.upper().encode())
    h.update(b"\0")
    h.update(url.encode())
    if body:
        h.update(b"\0")
        h.update(body if isinstance(body, bytes) else body.encode())
    return h.hexdigest()


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write via a temp file and rename so a crash never leaves a half written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=path.suffix)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def atomic_write_text(path: Path, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))


def write_json(path: Path, payload: Any) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


class ResponseCache:
    def __init__(self, cache_dir: Path | str) -> None:
        self.dir = Path(cache_dir)
        (self.dir / "http").mkdir(parents=True, exist_ok=True)
        (self.dir / "robots").mkdir(parents=True, exist_ok=True)

    def close(self) -> None:  # kept for symmetry with earlier designs; nothing to release
        return None

    def _paths(self, key: str) -> tuple[Path, Path]:
        key = key.replace(":", "_")  # "ai:<hash>" keys: ':' is not a legal filename char on Windows
        base = self.dir / "http" / key[:2]
        return base / f"{key}.meta.json", base / f"{key}.body"

    def get(self, key: str) -> CachedResponse | None:
        meta_path, body_path = self._paths(key)
        if not meta_path.exists() or not body_path.exists():
            return None
        try:
            meta = read_json(meta_path)
        except (OSError, ValueError):
            return None
        if datetime.fromisoformat(meta["expires_at"]) < _now():
            return None
        return CachedResponse(
            url=meta["url"],
            status=int(meta["status"]),
            body=body_path.read_bytes(),
            content_type=meta.get("content_type"),
            fetched_at=datetime.fromisoformat(meta["fetched_at"]),
        )

    def put(
        self,
        key: str,
        url: str,
        method: str,
        status: int,
        body: bytes,
        content_type: str | None,
        ttl: timedelta,
    ) -> None:
        meta_path, body_path = self._paths(key)
        now = _now()
        atomic_write_bytes(body_path, body)
        write_json(
            meta_path,
            {
                "url": url,
                "method": method.upper(),
                "status": status,
                "content_type": content_type,
                "fetched_at": now.isoformat(),
                "expires_at": (now + ttl).isoformat(),
            },
        )

    @staticmethod
    def _robots_name(host: str) -> str:
        return re.sub(r"[^A-Za-z0-9.-]+", "_", host) + ".json"

    def get_robots(self, host: str) -> tuple[str, int] | None:
        path = self.dir / "robots" / self._robots_name(host)
        if not path.exists():
            return None
        try:
            meta = read_json(path)
        except (OSError, ValueError):
            return None
        if datetime.fromisoformat(meta["expires_at"]) < _now():
            return None
        return str(meta["body"]), int(meta["status"])

    def put_robots(self, host: str, body: str, status: int, ttl: timedelta) -> None:
        now = _now()
        write_json(
            self.dir / "robots" / self._robots_name(host),
            {
                "host": host,
                "body": body,
                "status": status,
                "fetched_at": now.isoformat(),
                "expires_at": (now + ttl).isoformat(),
            },
        )

    def purge_expired(self) -> int:
        removed = 0
        now = _now()
        for meta_path in (self.dir / "http").glob("*/*.meta.json"):
            try:
                meta = read_json(meta_path)
                expired = datetime.fromisoformat(meta["expires_at"]) < now
            except (OSError, ValueError, KeyError):
                expired = True
            if expired:
                body_path = meta_path.with_name(meta_path.name.replace(".meta.json", ".body"))
                meta_path.unlink(missing_ok=True)
                body_path.unlink(missing_ok=True)
                removed += 1
        return removed

    def stats(self) -> dict[str, int]:
        now = _now()
        total = live = 0
        for meta_path in (self.dir / "http").glob("*/*.meta.json"):
            total += 1
            try:
                if datetime.fromisoformat(read_json(meta_path)["expires_at"]) >= now:
                    live += 1
            except (OSError, ValueError, KeyError):
                continue
        robots = len(list((self.dir / "robots").glob("*.json")))
        return {"entries": total, "live": live, "robots": robots}
