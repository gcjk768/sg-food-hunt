"""Optional Google Sheets export (``exports.google_sheets: true``; needs gspread and a service
account JSON in GOOGLE_SHEETS_CREDENTIALS_JSON plus GOOGLE_SHEETS_SPREADSHEET_ID)."""

from __future__ import annotations

import csv
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


def export_csvs_to_sheets(
    csv_paths: dict[str, Path],
    spreadsheet_id: str | None = None,
    credentials_path: str | None = None,
    client: Any | None = None,
) -> list[str]:
    """One worksheet per category CSV. Returns the worksheet titles written."""
    spreadsheet_id = spreadsheet_id or os.environ.get("GOOGLE_SHEETS_SPREADSHEET_ID", "")
    credentials_path = credentials_path or os.environ.get("GOOGLE_SHEETS_CREDENTIALS_JSON", "")
    if not spreadsheet_id or (client is None and not credentials_path):
        raise ValueError(
            "GOOGLE_SHEETS_SPREADSHEET_ID and GOOGLE_SHEETS_CREDENTIALS_JSON must be set"
        )
    if client is None:
        try:
            import gspread
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "pip install -e .[sheets] to enable the Google Sheets export"
            ) from exc
        client = gspread.service_account(filename=credentials_path)
    book = client.open_by_key(spreadsheet_id)
    written = []
    for key, path in sorted(csv_paths.items()):
        with path.open("r", encoding="utf-8", newline="") as fh:
            rows = list(csv.reader(fh))
        title = key[:99]
        try:
            ws = book.worksheet(title)
            ws.clear()
        except Exception:
            ws = book.add_worksheet(
                title=title, rows=max(len(rows), 1), cols=max(len(rows[0]) if rows else 1, 1)
            )
        if rows:
            ws.update(range_name="A1", values=rows)
        written.append(title)
    log.info("google sheets: wrote %d worksheets", len(written))
    return written
