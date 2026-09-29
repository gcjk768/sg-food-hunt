from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from sgfoodhunt.config import AppConfig, CategoriesConfig, Category, HardFilters, load_config


def test_real_config_loads(app_config: AppConfig) -> None:
    keys = [c.key for c in app_config.categories.categories]
    assert len(keys) == 15
    assert keys[-3:] == ["new_cafes", "new_restaurants", "new_zichar"]
    assert keys[0] == "cafes_date"
    assert app_config.sources.get("google_places").kind == "api"
    assert app_config.settings.home.postal_code.isdigit()


def test_category_defaults_are_merged(app_config: AppConfig) -> None:
    cat = app_config.categories.get("family_weekend")
    assert cat.weights["rating"] == 0.30  # from defaults
    assert cat.weights["kid_friendly"] == 0.15  # overridden
    assert cat.hard_filters.exclude_closed is True
    assert cat.hard_filters.open_weekends is True
    assert "grandparents" in cat.keywords  # group lexicon
    assert "playground" in cat.keywords  # own keywords
    total = sum(cat.normalised_weights().values())
    assert abs(total - 1.0) < 1e-9


def test_unknown_weight_component_rejected() -> None:
    with pytest.raises(ValueError, match="unknown weight components"):
        Category(
            key="x",
            display_name="x",
            group="dating",
            party_size=2,
            queries=["q"],
            weights={"charisma": 1.0},
        )


def test_duplicate_category_keys_rejected() -> None:
    base = {"key": "dup", "display_name": "d", "group": "dating", "party_size": 2, "queries": ["q"]}
    with pytest.raises(ValueError, match="duplicate category keys"):
        CategoriesConfig(categories=[Category(**base), Category(**base)])  # type: ignore[arg-type]


def test_hard_filters_defaults() -> None:
    assert HardFilters().exclude_closed is True
    assert HardFilters().open_weekends is False


def test_sources_sorted_by_priority(app_config: AppConfig) -> None:
    priorities = [s.priority for s in app_config.sources.sources]
    assert priorities == sorted(priorities)
    assert app_config.sources.get("tripadvisor").runnable is False


def test_secrets_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config"
    cfg.mkdir()
    for name in ("settings", "categories", "sources"):
        (cfg / f"{name}.yaml").write_text((Path("config") / f"{name}.yaml").read_text())
    (tmp_path / ".env").write_text("GOOGLE_PLACES_API_KEY=abc123\n")
    monkeypatch.delenv("GOOGLE_PLACES_API_KEY", raising=False)
    config = load_config(cfg)
    assert config.secrets.google_places_api_key == "abc123"
    assert config.root_dir == tmp_path.resolve()


def test_adding_category_needs_only_yaml(tmp_path: Path) -> None:
    data = yaml.safe_load(Path("config/categories.yaml").read_text())
    data["categories"].append(
        {
            "key": "supper_date",
            "display_name": "Late supper",
            "group": "dating",
            "party_size": 2,
            "queries": ["late night supper Singapore"],
            "weights": {"ambience": 0.5},
            "hard_filters": {"open_weekends": False},
        }
    )
    cfg = CategoriesConfig(**data)
    cat = cfg.get("supper_date")
    assert cat.weights["ambience"] == 0.5 and cat.weights["rating"] == 0.30
    assert cat.hard_filters.exclude_closed is True


def test_new_openings_categories(app_config: AppConfig) -> None:
    for key in ("new_cafes", "new_restaurants", "new_zichar"):
        cat = app_config.categories.get(key)
        assert cat.group == "general"
        assert cat.hard_filters.opened_within_months == 12
        assert "newly opened" in cat.keywords  # group lexicon applied
        assert cat.weights["recommendations"] == 0.40 and cat.weights["rating"] == 0.15
        assert any("新" in q for q in cat.queries)
