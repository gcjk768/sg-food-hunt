"""Rebuild sgfoodhunt/data/mrt_stations.json from the data.gov.sg "LTA MRT Station Exit" GeoJSON.

Usage: python scripts/update_mrt_stations.py <path-to-downloaded.geojson>
Download the GeoJSON from https://data.gov.sg (search "LTA MRT Station Exit"). Exits are averaged
per station name; line names are kept from the bundled file where the station name matches.
"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "sgfoodhunt" / "data" / "mrt_stations.json"


def main(path: str) -> None:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    existing = {s["name"].lower(): s for s in json.loads(OUT.read_text())["stations"]}
    points: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for feature in data.get("features", []):
        props = feature.get("properties", {})
        desc = props.get("Description") or ""
        match = re.search(r"STATION_NA</th>\s*<td>([^<]+)</td>", desc)
        name = (match.group(1) if match else props.get("STATION_NA") or "").strip()
        name = (
            re.sub(r"\s+MRT STATION$", "", name, flags=re.I)
            .title()
            .replace("Harbourfront", "HarbourFront")
        )
        coords = feature.get("geometry", {}).get("coordinates")
        if name and coords:
            points[name].append((float(coords[1]), float(coords[0])))
    stations = []
    for name, pts in sorted(points.items()):
        lat = sum(p[0] for p in pts) / len(pts)
        lng = sum(p[1] for p in pts) / len(pts)
        lines = existing.get(name.lower(), {}).get("lines", [])
        stations.append({"name": name, "lines": lines, "lat": round(lat, 5), "lng": round(lng, 5)})
    OUT.write_text(
        json.dumps(
            {"note": "Built from data.gov.sg LTA MRT Station Exit", "stations": stations},
            ensure_ascii=False,
            indent=1,
        )
    )
    print(f"wrote {len(stations)} stations to {OUT}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
