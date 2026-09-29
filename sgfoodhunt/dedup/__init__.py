"""Deduplication: raw sightings -> one canonical venue per outlet, persisted across runs."""

from sgfoodhunt.dedup.registry import VenueRegistry, build_venues
from sgfoodhunt.dedup.venue import Evidence, Venue

__all__ = ["Evidence", "Venue", "VenueRegistry", "build_venues"]
