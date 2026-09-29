"""Google Maps data through the official Places API (New) Text Search endpoint.

Only official API calls are made; Google Maps web pages are never fetched. Review author data
returned by the API is dropped before anything is stored (PDPA).
"""

from __future__ import annotations

from typing import Any, ClassVar

from sgfoodhunt.http.cache import CacheMiss
from sgfoodhunt.http.client import FetchError
from sgfoodhunt.models import (
    BusinessStatus,
    ScrapeResult,
    SearchQuery,
    SourcePage,
    VenueCandidate,
    clean_snippet,
)
from sgfoodhunt.scrapers.base import BaseScraper

SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
FIELD_MASK = ",".join(
    [
        "places.id",
        "places.displayName",
        "places.formattedAddress",
        "places.addressComponents",
        "places.location",
        "places.rating",
        "places.userRatingCount",
        "places.priceLevel",
        "places.priceRange",
        "places.regularOpeningHours",
        "places.businessStatus",
        "places.websiteUri",
        "places.googleMapsUri",
        "places.nationalPhoneNumber",
        "places.types",
        "places.primaryType",
        "places.reservable",
        "places.servesVegetarianFood",
        "places.goodForChildren",
        "places.goodForGroups",
        "places.outdoorSeating",
        "places.allowsDogs",
        "places.accessibilityOptions",
        "places.photos",
        "places.editorialSummary",
        "places.reviews",
        "nextPageToken",
    ]
)
PRICE_LEVELS = {
    "PRICE_LEVEL_FREE": 1,
    "PRICE_LEVEL_INEXPENSIVE": 1,
    "PRICE_LEVEL_MODERATE": 2,
    "PRICE_LEVEL_EXPENSIVE": 3,
    "PRICE_LEVEL_VERY_EXPENSIVE": 4,
}
SOCIAL_WORDS = ("tiktok", "instagram", "insta worthy", "insta-worthy", "viral")


def _postal_from_components(components: list[dict[str, Any]]) -> str | None:
    for comp in components:
        if "postal_code" in (comp.get("types") or []):
            text = comp.get("longText") or comp.get("shortText")
            if isinstance(text, str) and text.isdigit() and len(text) == 6:
                return text
    return None


def anonymise_reviews(reviews: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep rating, date and a short snippet; drop author names, photos and profile links."""
    out = []
    for rev in reviews:
        text = rev.get("text") or {}
        snippet = clean_snippet(text.get("text") if isinstance(text, dict) else str(text))
        out.append(
            {
                "rating": rev.get("rating"),
                "published_at": rev.get("publishTime"),
                "snippet": snippet,
                "social_words": sum(1 for w in SOCIAL_WORDS if snippet and w in snippet.lower()),
            }
        )
    return out


def place_to_candidate(source_key: str, place: dict[str, Any]) -> VenueCandidate | None:
    name = (place.get("displayName") or {}).get("text")
    if not name:
        return None
    loc = place.get("location") or {}
    hours = place.get("regularOpeningHours") or {}
    access = place.get("accessibilityOptions") or {}
    status = place.get("businessStatus")
    business_status: BusinessStatus | None = (
        status if status in ("OPERATIONAL", "CLOSED_TEMPORARILY", "CLOSED_PERMANENTLY") else None
    )
    summary = (place.get("editorialSummary") or {}).get("text")
    reviews = anonymise_reviews(place.get("reviews") or [])
    price_range = place.get("priceRange") or {}
    return VenueCandidate(
        source_key=source_key,
        source_ref=place.get("id"),
        name=name,
        address=place.get("formattedAddress"),
        postal_code=_postal_from_components(place.get("addressComponents") or []),
        lat=loc.get("latitude"),
        lng=loc.get("longitude"),
        phone=place.get("nationalPhoneNumber"),
        website=place.get("websiteUri"),
        booking_url=place.get("googleMapsUri") if place.get("reservable") else None,
        rating=place.get("rating"),
        review_count=place.get("userRatingCount"),
        price_level=PRICE_LEVELS.get(str(place.get("priceLevel"))),
        price_text=(
            f"{price_range.get('startPrice', {}).get('units', '?')}-"
            f"{price_range.get('endPrice', {}).get('units', '?')} SGD"
            if price_range
            else None
        ),
        cuisine=[
            t
            for t in place.get("types") or []
            if t.endswith("_restaurant")
            or t in ("cafe", "bar", "bakery", "food_court", "coffee_shop")
        ],
        opening_hours={
            "weekday_descriptions": hours.get("weekdayDescriptions"),
            "periods": hours.get("periods"),
        }
        if hours
        else None,
        business_status=business_status,
        snippet=summary,
        extra={
            "google_maps_uri": place.get("googleMapsUri"),
            "primary_type": place.get("primaryType"),
            "types": place.get("types"),
            "reservable": place.get("reservable"),
            "serves_vegetarian_food": place.get("servesVegetarianFood"),
            "good_for_children": place.get("goodForChildren"),
            "good_for_groups": place.get("goodForGroups"),
            "outdoor_seating": place.get("outdoorSeating"),
            "allows_dogs": place.get("allowsDogs"),
            "wheelchair_accessible_entrance": access.get("wheelchairAccessibleEntrance"),
            "photo_count": len(place.get("photos") or []),
            "reviews": reviews,
        },
        page=SourcePage(
            source_key,
            place.get("googleMapsUri")
            or f"https://www.google.com/maps/place/?q=place_id:{place.get('id')}",
            title=name,
        ),
    )


class GooglePlacesScraper(BaseScraper):
    key: ClassVar[str] = "google_places"

    def missing_credentials(self) -> str | None:
        if not self.ctx.config.secrets.google_places_api_key:
            return "GOOGLE_PLACES_API_KEY is not set"
        return None

    def _client(self) -> Any:
        rpm = self.ctx.config.settings.api_rate_limits.get("google_places", 60)
        return (
            self.ctx.api_factory(
                "google_places",
                {
                    "X-Goog-Api-Key": self.ctx.config.secrets.google_places_api_key or "",
                    "X-Goog-FieldMask": FIELD_MASK,
                    "Content-Type": "application/json",
                },
            )
            if rpm
            else None
        )

    def _options(self) -> dict[str, Any]:
        return self.source.options

    def _body(self, query: SearchQuery, page_token: str | None) -> dict[str, Any]:
        opts = self._options()
        body: dict[str, Any] = {
            "textQuery": query.text,
            "regionCode": opts.get("region_code", "SG"),
            "languageCode": opts.get("language_code", "en"),
            "pageSize": 20,
        }
        bias = opts.get("location_bias")
        if bias:
            body["locationBias"] = {
                "circle": {
                    "center": {"latitude": bias["lat"], "longitude": bias["lng"]},
                    "radius": bias["radius_m"],
                }
            }
        if page_token:
            body["pageToken"] = page_token
        return body

    def accept(self, candidate: VenueCandidate) -> VenueCandidate | None:
        return candidate

    async def search(self, query: SearchQuery) -> ScrapeResult:
        result = self.new_result(query)
        missing = self.missing_credentials()
        if missing:
            result.skipped_reason = missing
            return result
        client = self._client()
        max_pages = int(self._options().get("max_pages", 3))
        page_token: str | None = None
        try:
            for _ in range(max_pages):
                payload, cached = await client.request_json(
                    "POST", SEARCH_URL, json_body=self._body(query, page_token), ttl=self.ctx.ttl
                )
                result.cache_hits += int(cached)
                result.requests_made += int(not cached)
                for place in payload.get("places") or []:
                    cand = place_to_candidate(self.source.key, place)
                    if cand is None:
                        continue
                    cand = self.accept(cand)
                    if cand is None:
                        continue
                    result.candidates.append(cand)
                    if cand.page:
                        result.pages.append(cand.page)
                page_token = payload.get("nextPageToken")
                if not page_token:
                    break
        except CacheMiss as exc:
            result.warnings.append(f"dry run: not cached {exc}")
        except FetchError as exc:
            result.warnings.append(str(exc))
            self.log.warning("Places API failed: %s", exc)
        finally:
            await client.aclose()
        return result
