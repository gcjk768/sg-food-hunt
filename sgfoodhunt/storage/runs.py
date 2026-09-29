"""Run records on disk (no database).

Layout under ``data_dir/runs``::

    <run_id>/run.json              manifest: mode, categories, sources, status, stats, timings
    <run_id>/raw/<source>.jsonl    one raw VenueCandidate sighting per line
    <run_id>/pages.jsonl           every source page that produced evidence
    <run_id>/events.jsonl          notable events (parse warnings, robots denials...)

``run_id`` is a UTC timestamp like ``20260929T031500Z`` so directory order is run order.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sgfoodhunt.http.cache import read_json, write_json
from sgfoodhunt.models import ScrapeResult, SearchQuery, SourcePage, VenueCandidate, utcnow_iso


def new_run_id(now: datetime | None = None) -> str:
    return (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")


@dataclass(slots=True)
class RunRecord:
    run_id: str
    mode: str
    categories: list[str]
    sources: list[str]
    started_at: str = field(default_factory=utcnow_iso)
    finished_at: str | None = None
    status: str = "running"
    stats: dict[str, Any] = field(default_factory=dict)
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "mode": self.mode,
            "categories": self.categories,
            "sources": self.sources,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "status": self.status,
            "stats": self.stats,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunRecord:
        return cls(**{k: data[k] for k in cls.__dataclass_fields__ if k in data})


class RunStore:
    def __init__(self, data_dir: Path | str) -> None:
        self.data_dir = Path(data_dir)
        self.runs_dir = self.data_dir / "runs"
        self.runs_dir.mkdir(parents=True, exist_ok=True)

    # -- run lifecycle ---------------------------------------------------------------------------
    def run_dir(self, run_id: str) -> Path:
        return self.runs_dir / run_id

    def create_run(self, mode: str, categories: list[str], sources: list[str]) -> RunRecord:
        run_id = new_run_id()
        suffix = 1
        while (self.runs_dir / run_id).exists():
            run_id = f"{new_run_id()}-{suffix}"
            suffix += 1
        record = RunRecord(run_id=run_id, mode=mode, categories=categories, sources=sources)
        (self.run_dir(run_id) / "raw").mkdir(parents=True, exist_ok=True)
        self.save_run(record)
        return record

    def save_run(self, record: RunRecord) -> None:
        write_json(self.run_dir(record.run_id) / "run.json", record.to_dict())

    def finish_run(self, record: RunRecord, status: str, stats: dict[str, Any]) -> None:
        record.finished_at = utcnow_iso()
        record.status = status
        record.stats = stats
        self.save_run(record)

    def load_run(self, run_id: str) -> RunRecord:
        return RunRecord.from_dict(read_json(self.run_dir(run_id) / "run.json"))

    def list_runs(self) -> list[RunRecord]:
        records = []
        for path in sorted(self.runs_dir.glob("*/run.json")):
            try:
                records.append(RunRecord.from_dict(read_json(path)))
            except (OSError, ValueError, TypeError):
                continue
        return records

    def latest_run(self, status: str | None = "ok") -> RunRecord | None:
        runs = [r for r in self.list_runs() if status is None or r.status == status]
        return runs[-1] if runs else None

    # -- appending evidence ----------------------------------------------------------------------
    @staticmethod
    def _append_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    def append_result(self, run_id: str, result: ScrapeResult) -> None:
        rdir = self.run_dir(run_id)
        page_rows = [
            {
                "source_key": p.source_key,
                "url": p.url,
                "title": p.title,
                "published_at": p.published_at,
                "content_hash": p.content_hash,
                "fetched_at": utcnow_iso(),
                "query": result.query.text,
                "category_key": result.query.category_key,
            }
            for p in result.pages
        ]
        self._append_jsonl(rdir / "pages.jsonl", page_rows)
        cand_rows = [
            c.to_row(run_id, result.query, page_url=c.page.url if c.page else None)
            for c in result.candidates
        ]
        self._append_jsonl(rdir / "raw" / f"{result.source_key}.jsonl", cand_rows)
        if result.warnings or result.skipped_reason:
            self.log_event(
                run_id,
                "warning" if result.warnings else "info",
                result.source_key,
                result.skipped_reason
                or f"{len(result.warnings)} warning(s): {result.warnings[0][:160]}",
                {
                    "query": result.query.text,
                    "category": result.query.category_key,
                    "warnings": result.warnings,
                },
            )

    def log_event(
        self,
        run_id: str,
        level: str,
        component: str,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        self._append_jsonl(
            self.run_dir(run_id) / "events.jsonl",
            [
                {
                    "at": utcnow_iso(),
                    "level": level,
                    "component": component,
                    "message": message,
                    "data": data or {},
                }
            ],
        )

    # -- reading back ----------------------------------------------------------------------------
    @staticmethod
    def _iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
        if not path.exists():
            return
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield json.loads(line)

    def iter_candidates(
        self, run_id: str, source_key: str | None = None
    ) -> Iterator[dict[str, Any]]:
        raw_dir = self.run_dir(run_id) / "raw"
        files = [raw_dir / f"{source_key}.jsonl"] if source_key else sorted(raw_dir.glob("*.jsonl"))
        for path in files:
            yield from self._iter_jsonl(path)

    def iter_pages(self, run_id: str) -> Iterator[dict[str, Any]]:
        yield from self._iter_jsonl(self.run_dir(run_id) / "pages.jsonl")

    def iter_events(self, run_id: str) -> Iterator[dict[str, Any]]:
        yield from self._iter_jsonl(self.run_dir(run_id) / "events.jsonl")

    def candidate_counts(self, run_id: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        for path in sorted((self.run_dir(run_id) / "raw").glob("*.jsonl")):
            counts[path.stem] = sum(1 for _ in self._iter_jsonl(path))
        return counts


def candidate_from_row(row: dict[str, Any]) -> VenueCandidate:
    """Rebuild a VenueCandidate from a JSONL row (used by later stages)."""
    page = SourcePage(row["source_key"], row["page_url"]) if row.get("page_url") else None
    return VenueCandidate(
        source_key=row["source_key"],
        name=row["name"],
        source_ref=row.get("source_ref"),
        name_zh=row.get("name_zh"),
        brand=row.get("brand"),
        address=row.get("address"),
        postal_code=row.get("postal_code"),
        lat=row.get("lat"),
        lng=row.get("lng"),
        phone=row.get("phone"),
        website=row.get("website"),
        booking_url=row.get("booking_url"),
        rating=row.get("rating"),
        review_count=row.get("review_count"),
        price_level=row.get("price_level"),
        price_text=row.get("price_text"),
        cuisine=list(row.get("cuisine") or []),
        opening_hours=row.get("opening_hours"),
        business_status=row.get("business_status"),
        michelin=row.get("michelin"),
        hygiene_grade=row.get("hygiene_grade"),
        snippet=row.get("snippet"),
        confidence=float(row.get("confidence", 1.0)),
        extra=dict(row.get("extra") or {}),
        page=page,
    )


def query_from_row(row: dict[str, Any]) -> SearchQuery:
    return SearchQuery(
        category_key=row["category_key"],
        category_group=row.get("category_group", ""),
        text=row["query"],
        party_size=int(row.get("party_size", 2)),
    )
