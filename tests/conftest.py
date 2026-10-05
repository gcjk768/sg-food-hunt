"""Shared fixtures: a temp copy of the real config with temp paths, and fake network layers."""

from __future__ import annotations

import logging
import shutil
from collections.abc import Callable, MutableMapping
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

from sgfoodhunt.config import AppConfig, HttpSettings, load_config
from sgfoodhunt.http.cache import ResponseCache
from sgfoodhunt.http.client import AsyncApiClient, PoliteClient
from sgfoodhunt.scrapers import BaseScraper, ScraperContext, build_scraper
from sgfoodhunt.storage.runs import RunStore
from sgfoodhunt.storage.vault import Vault

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


class FakeResponse:
    def __init__(self, status: int, body: bytes, content_type: str = "text/html") -> None:
        self.status_code = status
        self.content = body
        self.headers = {"Content-Type": content_type}

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")


class FakeSession:
    """Stand in for requests.Session: routes URLs to canned responses and records calls."""

    def __init__(self) -> None:
        self.headers: MutableMapping[str, str | bytes] = {}
        self.routes: dict[str, Any] = {}
        self.calls: list[str] = []
        self.default = FakeResponse(404, b"not found")

    def add(
        self, url: str, body: str | bytes, status: int = 200, content_type: str = "text/html"
    ) -> None:
        data = body.encode() if isinstance(body, str) else body
        self.routes[url] = FakeResponse(status, data, content_type)

    def add_sequence(self, url: str, responses: list[FakeResponse]) -> None:
        self.routes[url] = list(responses)

    def get(self, url: str | bytes, *, headers: Any = None, timeout: Any = None) -> FakeResponse:
        url = url.decode() if isinstance(url, bytes) else url
        self.calls.append(url)
        route = self.routes.get(url)
        if isinstance(route, list):
            return route.pop(0) if len(route) > 1 else route[0]
        if route is None:
            for prefix, resp in self.routes.items():
                if prefix.endswith("*") and url.startswith(prefix[:-1]):
                    return resp if not isinstance(resp, list) else resp[0]
            return self.default
        return route


@pytest.fixture(autouse=True)
def _reset_logging() -> Any:
    """setup_logging installs handlers on the root logger; drop them after every test."""
    yield
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()


@pytest.fixture()
def http_settings() -> HttpSettings:
    return HttpSettings(
        user_agent="sgfoodhunt-test/0.1 (+test)",
        min_delay_seconds=0,
        max_delay_seconds=0,
        max_retries=2,
        backoff_base_seconds=0,
        backoff_max_seconds=0,
    )


@pytest.fixture()
def cache(tmp_path: Path) -> ResponseCache:
    return ResponseCache(tmp_path / "cache")


@pytest.fixture()
def fake_session() -> FakeSession:
    s = FakeSession()
    s.add("https://example-blog.test/robots.txt", "User-agent: *\nDisallow: /private/\n")
    return s


@pytest.fixture()
def polite_client(
    http_settings: HttpSettings, cache: ResponseCache, fake_session: FakeSession
) -> PoliteClient:
    return PoliteClient(http_settings, cache, session=fake_session, sleep=lambda _s: None)


@pytest.fixture()
def app_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AppConfig:
    """The real config files, copied to a temp dir with paths and options pointed at temp dirs."""
    cfg_dir = tmp_path / "config"
    shutil.copytree(ROOT / "config", cfg_dir)
    settings = yaml.safe_load((cfg_dir / "settings.yaml").read_text())
    settings["paths"] = {
        "vault_dir": str(tmp_path / "vault"),
        "vault_folder": "SG Food Hunt",
        "data_dir": str(tmp_path / "data"),
        "cache_dir": str(tmp_path / "data" / "cache"),
        "exports_dir": str(tmp_path / "data" / "exports"),
        "logs_dir": str(tmp_path / "logs"),
    }
    settings["ai"] = {"enabled": False}  # tests enable the AI layer explicitly where they need it
    settings["http"].update(
        {
            "min_delay_seconds": 0,
            "max_delay_seconds": 0,
            "max_retries": 1,
            "backoff_base_seconds": 0,
            "backoff_max_seconds": 0,
        }
    )
    (cfg_dir / "settings.yaml").write_text(yaml.safe_dump(settings))
    for var in ("GOOGLE_PLACES_API_KEY", "REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET"):
        monkeypatch.delenv(var, raising=False)
    return load_config(cfg_dir, env_file=tmp_path / "missing.env")


@pytest.fixture()
def run_store(app_config: AppConfig) -> RunStore:
    return RunStore(app_config.settings.paths.data_dir)


@pytest.fixture()
def vault(app_config: AppConfig) -> Vault:
    v = Vault(app_config.settings.paths.vault_dir, app_config.settings.paths.vault_folder)
    v.ensure()
    return v


class MockApiFactory:
    """Builds AsyncApiClient instances over an httpx MockTransport so no network is touched."""

    def __init__(
        self,
        settings: HttpSettings,
        cache: ResponseCache,
        handler: Callable[[httpx.Request], httpx.Response],
        dry_run: bool = False,
    ) -> None:
        self.settings = settings
        self.cache = cache
        self.handler = handler
        self.dry_run = dry_run
        self.requests: list[httpx.Request] = []

    def __call__(self, name: str, headers: dict[str, str] | None) -> AsyncApiClient:
        def wrapped(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return self.handler(request)

        client = httpx.AsyncClient(transport=httpx.MockTransport(wrapped), headers=headers or {})
        return AsyncApiClient(
            self.settings, self.cache, rpm=1000, dry_run=self.dry_run, client=client
        )


@pytest.fixture()
def make_scraper(
    app_config: AppConfig,
    polite_client: PoliteClient,
    cache: ResponseCache,
    http_settings: HttpSettings,
):
    def _make(
        source_key: str,
        handler: Callable[[httpx.Request], httpx.Response] | None = None,
        dry_run: bool = False,
        **overrides: Any,
    ) -> tuple[BaseScraper, MockApiFactory]:
        source = app_config.sources.get(source_key).model_copy(update=overrides)
        polite_client.dry_run = dry_run
        factory = MockApiFactory(
            http_settings, cache, handler or (lambda r: httpx.Response(404)), dry_run=dry_run
        )
        ctx = ScraperContext(
            config=app_config,
            source=source,
            http=polite_client,
            api_factory=factory,
            dry_run=dry_run,
        )
        return build_scraper(ctx), factory

    return _make


@pytest.fixture()
def ttl() -> timedelta:
    return timedelta(hours=1)
