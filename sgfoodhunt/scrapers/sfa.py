"""Singapore Food Agency hygiene grades via the data.gov.sg open data API.

Catalogue source: downloads the configured dataset once per run. Rows are stored with
``confidence = 0`` and ``extra.enrich_only = true`` so they attach hygiene grades to venues found
elsewhere but never create a venue on their own.
"""

from __future__ import annotations

from typing import Any, ClassVar

from sgfoodhunt.http.cache import CacheMiss
from sgfoodhunt.http.client import FetchError
from sgfoodhunt.models import ScrapeResult, SearchQuery, SourcePage
from sgfoodhunt.scrapers.base import BaseScraper

DATASTORE_URL = "https://data.gov.sg/api/action/datastore_search"


class SfaHygieneScraper(BaseScraper):
    key: ClassVar[str] = "sfa"
    query_driven: ClassVar[bool] = False

    def _columns(self) -> dict[str, str]:
        o = self.source.options
        return {
            "name": str(o.get("name_column", "licensee_name")),
            "address": str(o.get("address_column", "premises_address")),
            "postal": str(o.get("postal_column", "postal_code")),
            "grade": str(o.get("grade_column", "grade")),
        }

    def row_to_candidate(self, row: dict[str, Any], page: SourcePage):  # type: ignore[no-untyped-def]
        cols = self._columns()
        name = row.get(cols["name"])
        if not isinstance(name, str) or not name.strip():
            return None
        postal = row.get(cols["postal"])
        return self.candidate(
            name.title() if name.isupper() else name,
            source_ref=str(row.get("_id") or row.get("licence_no") or ""),
            address=str(row.get(cols["address"]) or "") or None,
            postal_code=str(postal).zfill(6) if postal not in (None, "") else None,
            hygiene_grade=str(row.get(cols["grade"]) or "").strip().upper() or None,
            confidence=0.0,
            page=page,
            extra={"enrich_only": True, "row": {k: v for k, v in row.items() if k != "_id"}},
        )

    async def search(self, query: SearchQuery) -> ScrapeResult:
        result = self.new_result(query)
        dataset = str(self.source.options.get("dataset_id", ""))
        if not dataset or "replace_me" in dataset:
            result.skipped_reason = "sfa dataset_id is not configured in sources.yaml"
            return result
        base = str(self.source.options.get("datastore_url", DATASTORE_URL))
        page_size = int(self.source.options.get("page_size", 5000))
        max_rows = int(self.source.options.get("max_rows", 100000))
        client = self.ctx.api_factory("data_gov_sg", None)
        page = SourcePage(
            self.source.key,
            f"https://data.gov.sg/datasets/{dataset}/view",
            title="SFA hygiene grades",
        )
        result.pages.append(page)
        offset = 0
        try:
            while offset < max_rows:
                payload, cached = await client.request_json(
                    "GET",
                    base,
                    params={"resource_id": dataset, "limit": page_size, "offset": offset},
                    ttl=self.ctx.ttl,
                )
                result.cache_hits += int(cached)
                result.requests_made += int(not cached)
                records = (payload.get("result") or {}).get("records") or []
                if not records:
                    break
                for row in records:
                    cand = self.row_to_candidate(row, page)
                    if cand:
                        result.candidates.append(cand)
                offset += len(records)
                total = (payload.get("result") or {}).get("total")
                if isinstance(total, int) and offset >= total:
                    break
        except CacheMiss as exc:
            result.warnings.append(f"dry run: not cached {exc}")
        except FetchError as exc:
            result.warnings.append(str(exc))
            self.log.warning("data.gov.sg request failed: %s", exc)
        finally:
            await client.aclose()
        if not result.candidates and not result.warnings:
            result.warnings.append("dataset returned no rows; check dataset_id and column names")
        return result
