"""Nearest MRT station from a bundled station table (no network)."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources


@dataclass(slots=True, frozen=True)
class Station:
    name: str
    lines: tuple[str, ...]
    lat: float
    lng: float


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


@lru_cache(maxsize=1)
def load_stations() -> tuple[Station, ...]:
    text = resources.files("sgfoodhunt.data").joinpath("mrt_stations.json").read_text("utf-8")
    data = json.loads(text)
    return tuple(
        Station(s["name"], tuple(s.get("lines", [])), float(s["lat"]), float(s["lng"]))
        for s in data["stations"]
    )


def nearest_station(lat: float, lng: float) -> tuple[Station, float]:
    """Return (station, straight line metres)."""
    best: Station | None = None
    best_d = float("inf")
    for st in load_stations():
        d = haversine_m(lat, lng, st.lat, st.lng)
        if d < best_d:
            best, best_d = st, d
    assert best is not None
    return best, best_d
