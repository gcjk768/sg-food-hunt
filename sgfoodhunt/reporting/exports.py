"""CSV and JSON exports of run data (stage one: raw candidates)."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from sgfoodhunt.storage.runs import RunStore

RAW_COLUMNS = [
    "source_key",
    "category_key",
    "query",
    "name",
    "name_zh",
    "address",
    "postal_code",
    "lat",
    "lng",
    "phone",
    "website",
    "booking_url",
    "rating",
    "review_count",
    "price_level",
    "price_text",
    "cuisine",
    "business_status",
    "michelin",
    "hygiene_grade",
    "confidence",
    "page_url",
    "source_ref",
    "captured_at",
]


def export_raw_candidates(store: RunStore, run_id: str, exports_dir: Path) -> tuple[Path, Path]:
    out_dir = exports_dir / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "raw_candidates.csv"
    json_path = out_dir / "raw_candidates.json"
    rows = list(store.iter_candidates(run_id))
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=RAW_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            flat = dict(row)
            if isinstance(flat.get("cuisine"), list):
                flat["cuisine"] = "; ".join(flat["cuisine"])
            writer.writerow(flat)
    with json_path.open("w", encoding="utf-8") as fh:
        json.dump(rows, fh, ensure_ascii=False, indent=1)
    return csv_path, json_path
