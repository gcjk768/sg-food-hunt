"""Optional AI layer. Every call goes through the Claude Code CLI in print mode (``claude -p``)
with a JSON schema, is cached by prompt hash, and is capped per run by call count and dollars.
When ``ai.enabled`` is false, or the CLI is missing or fails, every caller falls back to the
rule-based path, so the pipeline never depends on the model being available."""

from sgfoodhunt.ai.client import AiClient, AiStats, build_ai_client
from sgfoodhunt.ai.tasks import (
    ExtractedVenue,
    ReviewAnalysis,
    analyse_reviews,
    extract_listicle,
    extract_venues,
    match_caption,
)

__all__ = [
    "AiClient",
    "AiStats",
    "ExtractedVenue",
    "ReviewAnalysis",
    "analyse_reviews",
    "build_ai_client",
    "extract_listicle",
    "extract_venues",
    "match_caption",
]
