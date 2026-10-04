"""Typed configuration loaded from the YAML files in ``config/`` and secrets from ``.env``."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator, model_validator

WEIGHT_COMPONENTS: tuple[str, ...] = (
    "rating",
    "recommendations",
    "keyword_match",
    "food",
    "service",
    "value",
    "ambience",
    "space",
    "kid_friendly",
    "parking",
    "weekend_open",
    "quiet",
)

CategoryGroup = Literal["dating", "family", "general"]
SourceKind = Literal[
    "api", "booking", "aggregator", "blog", "guide", "community", "government", "review_site"
]
FetchMode = Literal["static", "browser", "api"]
TosStatus = Literal["verified_ok", "unverified", "disallowed"]


class PathSettings(BaseModel):
    vault_dir: Path = Path("vault")
    vault_folder: str = "SG Food Hunt"
    data_dir: Path = Path("data")
    cache_dir: Path = Path("data/cache")
    exports_dir: Path = Path("data/exports")
    logs_dir: Path = Path("logs")


class HttpSettings(BaseModel):
    user_agent: str
    min_delay_seconds: float = 2.0
    max_delay_seconds: float = 5.0
    timeout_seconds: float = 30.0
    max_retries: int = 4
    backoff_base_seconds: float = 1.5
    backoff_max_seconds: float = 60.0
    robots_cache_ttl_hours: int = 168
    respect_robots: bool = True
    default_cache_ttl_hours: int = 168

    @model_validator(mode="after")
    def _check_delays(self) -> HttpSettings:
        if self.min_delay_seconds < 0 or self.max_delay_seconds < self.min_delay_seconds:
            raise ValueError("http delay range is invalid")
        return self


class ScoringSettings(BaseModel):
    top_n: int = 15
    fuzzy_match_threshold: int = 88
    price_per_pax_sgd: dict[int, float] = Field(
        default_factory=lambda: {1: 15.0, 2: 35.0, 3: 80.0, 4: 160.0}
    )
    new_within_days: int = 30
    bayesian_prior_reviews: int = 100
    bayesian_prior_rating: float = 4.2
    recommendation_half_life_months: float = 18
    recommendation_stale_after_years: float = 4
    michelin_bonus: float = 0.03
    multi_source_bonus: float = 0.03
    multi_source_threshold: int = 3
    declining_trend_penalty: float = 0.05
    poor_hygiene_penalty: float = 0.10
    temporarily_closed_penalty: float = 0.50
    buzz_bonus_max: float = 0.05
    personal_rating_weight: float = 0.15


class SocialSettings(BaseModel):
    enabled: bool = False
    exports_dir: Path = Path("social_exports")
    buzz_window_months: int = 6
    buzz_half_life_days: int = 60
    trending_threshold: int = 5
    hashtags: list[str] = Field(default_factory=list, max_length=25)
    serp_sites: list[str] = Field(default_factory=lambda: ["instagram.com", "tiktok.com"])
    serp_endpoint: str = "https://serpapi.com/search.json"
    serp_max_results: int = 10


class AiTasks(BaseModel):
    reddit_extraction: bool = True
    article_extraction: bool = True
    review_analysis: bool = True
    social_matching: bool = True


class AiSettings(BaseModel):
    enabled: bool = True
    provider: Literal["claude_cli"] = "claude_cli"
    model: str = "claude-sonnet-5-5"
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "low"
    claude_bin: str = "claude"
    timeout_seconds: int = 180
    max_calls_per_run: int = 200
    max_budget_usd: float = 5.0
    cache_ttl_days: int = 90
    tasks: AiTasks = Field(default_factory=AiTasks)


class ExportSettings(BaseModel):
    google_sheets: bool = False
    csv: bool = True
    json_file: bool = Field(default=True, alias="json")

    model_config = {"populate_by_name": True}


class NotificationSettings(BaseModel):
    telegram: bool = False
    email: bool = False
    rating_change_threshold: float = 0.2


class Settings(BaseModel):
    paths: PathSettings = Field(default_factory=PathSettings)
    http: HttpSettings
    api_rate_limits: dict[str, int] = Field(default_factory=dict)
    scoring: ScoringSettings = Field(default_factory=ScoringSettings)
    social: SocialSettings = Field(default_factory=SocialSettings)
    ai: AiSettings = Field(default_factory=AiSettings)
    exports: ExportSettings = Field(default_factory=ExportSettings)
    notifications: NotificationSettings = Field(default_factory=NotificationSettings)


class HardFilters(BaseModel):
    exclude_closed: bool = True
    open_weekends: bool = False
    requires_private_room: bool = False
    halal_only: bool = False
    max_price_level: int | None = None
    price_required: bool = False  # drop venues with no price level (Michelin and buffets are kept)
    bill_range_sgd: tuple[float, float] | None = None  # estimated bill for the party, [low, high]
    opened_within_months: int | None = None  # keep only venues first seen / opened recently


class Category(BaseModel):
    key: str = Field(pattern=r"^[a-z0-9_]+$")
    display_name: str
    group: CategoryGroup
    party_size: int = Field(ge=1, le=30)
    queries: list[str] = Field(min_length=1)
    keywords: list[str] = Field(default_factory=list)
    weights: dict[str, float] = Field(default_factory=dict)
    hard_filters: HardFilters = Field(default_factory=HardFilters)
    digest_only: bool = False  # ranked for the fortnightly list, never sent as per-venue cards
    enabled: bool = True  # false = kept in the file but never searched, ranked or sent

    @field_validator("weights")
    @classmethod
    def _known_components(cls, value: dict[str, float]) -> dict[str, float]:
        unknown = set(value) - set(WEIGHT_COMPONENTS)
        if unknown:
            raise ValueError(f"unknown weight components: {sorted(unknown)}")
        return value

    def normalised_weights(self) -> dict[str, float]:
        total = sum(self.weights.values())
        if total <= 0:
            return {k: 0.0 for k in self.weights}
        return {k: v / total for k, v in self.weights.items()}


class CategoryDefaults(BaseModel):
    weights: dict[str, float] = Field(default_factory=dict)
    hard_filters: dict[str, Any] = Field(default_factory=dict)
    group_keywords: dict[str, list[str]] = Field(default_factory=dict)


class CategoriesConfig(BaseModel):
    defaults: CategoryDefaults = Field(default_factory=CategoryDefaults)
    categories: list[Category]

    @model_validator(mode="after")
    def _apply_defaults_and_check_keys(self) -> CategoriesConfig:
        keys = [c.key for c in self.categories]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate category keys")
        for cat in self.categories:
            merged = dict(self.defaults.weights)
            merged.update(cat.weights)
            cat.weights = merged
            hf = dict(self.defaults.hard_filters)
            hf.update(cat.hard_filters.model_dump(exclude_unset=True))
            cat.hard_filters = HardFilters(**hf)
            group_kw = self.defaults.group_keywords.get(cat.group, [])
            cat.keywords = list(dict.fromkeys([*group_kw, *cat.keywords]))
        return self

    def has(self, key: str) -> bool:
        return any(c.key == key for c in self.categories)

    def get(self, key: str) -> Category:
        for cat in self.categories:
            if cat.key == key:
                return cat
        raise KeyError(f"unknown category: {key}")


class LocationBias(BaseModel):
    lat: float
    lng: float
    radius_m: float


class Source(BaseModel):
    key: str = Field(pattern=r"^[a-z0-9_]+$")
    name: str
    kind: SourceKind
    scraper: str
    enabled: bool = True
    priority: int = 100
    base_url: str
    search_url: str | None = None
    fetch: FetchMode = "static"
    independent: bool = True
    tos_status: TosStatus = "unverified"
    cache_ttl_hours: int | None = None
    notes: str = ""
    options: dict[str, Any] = Field(default_factory=dict)

    @property
    def runnable(self) -> bool:
        return self.enabled and self.tos_status != "disallowed"


class SourcesConfig(BaseModel):
    sources: list[Source]

    @model_validator(mode="after")
    def _unique_keys(self) -> SourcesConfig:
        keys = [s.key for s in self.sources]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate source keys")
        self.sources.sort(key=lambda s: s.priority)
        return self

    def get(self, key: str) -> Source:
        for src in self.sources:
            if src.key == key:
                return src
        raise KeyError(f"unknown source: {key}")


class Secrets(BaseModel):
    """Values read from the environment (populated from .env by python-dotenv)."""

    google_places_api_key: str | None = None
    reddit_client_id: str | None = None
    reddit_client_secret: str | None = None
    reddit_user_agent: str | None = None
    serp_api_key: str | None = None
    instagram_graph_token: str | None = None
    instagram_business_account_id: str | None = None
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    telegram_thread_id: str | None = None  # forum topic id (the last number in a t.me/c/... link)

    @classmethod
    def from_env(cls) -> Secrets:
        def get(name: str) -> str | None:
            value = os.environ.get(name, "").strip()
            return value or None

        return cls(
            google_places_api_key=get("GOOGLE_PLACES_API_KEY"),
            reddit_client_id=get("REDDIT_CLIENT_ID"),
            reddit_client_secret=get("REDDIT_CLIENT_SECRET"),
            reddit_user_agent=get("REDDIT_USER_AGENT"),
            serp_api_key=get("SERP_API_KEY"),
            instagram_graph_token=get("INSTAGRAM_GRAPH_TOKEN"),
            instagram_business_account_id=get("INSTAGRAM_BUSINESS_ACCOUNT_ID"),
            telegram_bot_token=get("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=get("TELEGRAM_CHAT_ID"),
            telegram_thread_id=get("TELEGRAM_THREAD_ID"),
        )


class AppConfig(BaseModel):
    settings: Settings
    categories: CategoriesConfig
    sources: SourcesConfig
    secrets: Secrets
    config_dir: Path
    root_dir: Path

    def resolve(self, path: Path) -> Path:
        return path if path.is_absolute() else self.root_dir / path


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a mapping at the top level")
    return data


def load_config(config_dir: Path | str = "config", env_file: Path | str | None = None) -> AppConfig:
    """Load settings, categories, sources and secrets.

    ``config_dir`` may be relative to the current working directory. ``.env`` is looked up next to
    the config directory's parent (the project root) unless ``env_file`` is given.
    """
    cdir = Path(config_dir).resolve()
    root = cdir.parent
    dotenv_path = Path(env_file) if env_file else root / ".env"
    if dotenv_path.exists():
        load_dotenv(dotenv_path, override=False)
    settings = Settings(**_load_yaml(cdir / "settings.yaml"))
    categories = CategoriesConfig(**_load_yaml(cdir / "categories.yaml"))
    if not os.environ.get("SGFH_ALL_CATEGORIES"):  # tests set it so every category stays loadable
        categories.categories = [c for c in categories.categories if c.enabled]
    sources = SourcesConfig(**_load_yaml(cdir / "sources.yaml"))
    return AppConfig(
        settings=settings,
        categories=categories,
        sources=sources,
        secrets=Secrets.from_env(),
        config_dir=cdir,
        root_dir=root,
    )
