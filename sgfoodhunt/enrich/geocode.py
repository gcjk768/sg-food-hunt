"""Postal code -> coordinates through OneMap's search endpoint (cached a year).

Search works without an account; with one (``ONEMAP_EMAIL`` + ``ONEMAP_PASSWORD``, or a pasted
``ONEMAP_ACCESS_TOKEN``) the request carries the token, which OneMap asks for on its other APIs and
which lifts the anonymous rate limit. Tokens last 3 days; a login renews itself in ``data/``."""

from __future__ import annotations

import base64
import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from sgfoodhunt.config import Secrets
from sgfoodhunt.http.cache import CacheMiss, read_json, write_json
from sgfoodhunt.http.client import AsyncApiClient, FetchError

log = logging.getLogger(__name__)

ONEMAP_SEARCH = "https://www.onemap.gov.sg/api/common/elastic/search"
ONEMAP_TOKEN = "https://www.onemap.gov.sg/api/auth/post/getToken"
TOKEN_FILE = "onemap_token.json"
RENEW_AFTER_S = 2 * 24 * 3600  # tokens last 3 days; renew after 2


def token_expiry(token: str) -> float | None:
    """The ``exp`` claim (epoch seconds) of a OneMap JWT, or None when unreadable. Not verified."""
    try:
        part = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        return float(claims["exp"])
    except (IndexError, ValueError, KeyError, TypeError):
        return None


def onemap_token(secrets: Secrets, data_dir: Path, now: float | None = None) -> str | None:
    """A usable token or None. Login wins over a pasted token; a pasted token is used until it
    expires. Best effort: a failed login is logged and the lookups go on anonymously."""
    now = now or time.time()
    if secrets.onemap_email and secrets.onemap_password:
        path = data_dir / TOKEN_FILE
        try:
            saved = read_json(path) if path.exists() else {}
            if saved.get("token") and now < float(saved.get("renew_at", 0)):
                return str(saved["token"])
            resp = httpx.post(
                ONEMAP_TOKEN,
                json={"email": secrets.onemap_email, "password": secrets.onemap_password},
                timeout=30,
            )
            resp.raise_for_status()
            token = str(resp.json().get("access_token") or "")
            if token:
                write_json(path, {"token": token, "renew_at": now + RENEW_AFTER_S})
                return token
            log.warning("OneMap login returned no token")
        except (httpx.HTTPError, OSError, ValueError) as exc:
            log.warning("OneMap login failed: %s", exc)
        return None
    tok = secrets.onemap_access_token
    if not tok:
        return None
    exp = token_expiry(tok)
    if exp is not None and now >= exp:
        log.warning(
            "ONEMAP_ACCESS_TOKEN expired; set ONEMAP_EMAIL/ONEMAP_PASSWORD or paste a new one"
        )
        return None
    return tok


def onemap_state(secrets: Secrets, now: float | None = None) -> str:
    """One line for ``sgfh doctor``."""
    now = now or time.time()
    if secrets.onemap_email and secrets.onemap_password:
        return "onemap: login set, token renews itself"
    tok = secrets.onemap_access_token
    if not tok:
        return "onemap: no account; public search only"
    exp = token_expiry(tok)
    if exp is None:
        return "onemap: token set, expiry unknown"
    when = datetime.fromtimestamp(exp).strftime("%d %b %H:%M")
    return f"onemap: token valid until {when}" if now < exp else f"onemap: token EXPIRED {when}"


class OneMapGeocoder:
    def __init__(
        self, client: AsyncApiClient, ttl_days: int = 365, token: str | None = None
    ) -> None:
        self.client = client
        self.ttl = timedelta(days=ttl_days)
        self.headers = {"Authorization": token} if token else None
        self.lookups = 0
        self.cache_hits = 0

    async def postal(self, postal_code: str) -> tuple[float, float] | None:
        try:
            payload, cached = await self.client.request_json(
                "GET",
                ONEMAP_SEARCH,
                params={
                    "searchVal": postal_code,
                    "returnGeom": "Y",
                    "getAddrDetails": "Y",
                    "pageNum": 1,
                },
                headers=self.headers,
                ttl=self.ttl,
            )
        except CacheMiss:
            return None
        except FetchError as exc:
            log.warning("OneMap lookup failed for %s: %s", postal_code, exc)
            return None
        self.cache_hits += int(cached)
        self.lookups += int(not cached)
        return _first_match(payload, postal_code)


def _first_match(payload: Any, postal_code: str) -> tuple[float, float] | None:
    results = payload.get("results") if isinstance(payload, dict) else None
    if not results:
        return None
    for r in results:
        if str(r.get("POSTAL", "")).strip() == postal_code:
            try:
                return float(r["LATITUDE"]), float(r["LONGITUDE"])
            except (KeyError, TypeError, ValueError):
                continue
    r = results[0]
    try:
        return float(r["LATITUDE"]), float(r["LONGITUDE"])
    except (KeyError, TypeError, ValueError):
        return None
