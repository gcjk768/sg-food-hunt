"""Cuisine label normalisation across Google types, blog tags and platform labels."""

from __future__ import annotations

import re

CANONICAL: dict[str, tuple[str, ...]] = {
    "Zi Char": ("zi char", "zichar", "tze char", "cze char", "煮炒", "zi-char"),
    "Dim Sum": ("dim sum", "dimsum", "点心", "yum cha"),
    "Hotpot": ("hotpot", "hot pot", "steamboat", "火锅", "mookata", "mala"),
    "Chinese": (
        "chinese",
        "chinese_restaurant",
        "cantonese",
        "teochew",
        "hokkien",
        "sichuan",
        "szechuan",
        "hainanese",
    ),
    "Seafood": ("seafood", "seafood_restaurant", "crab"),
    "Cafe": (
        "cafe",
        "café",
        "cafes",
        "coffee",
        "coffee_shop",
        "cafes & coffee",
        "bakery",
        "brunch",
    ),
    "Dessert": ("dessert", "desserts", "ice cream", "ice_cream_shop", "patisserie", "cake"),
    "High Tea": ("high tea", "afternoon tea"),
    "Bar": ("bar", "bars", "cocktail", "wine bar", "pub", "rooftop bar"),
    "Japanese": (
        "japanese",
        "japanese_restaurant",
        "sushi",
        "sushi_restaurant",
        "ramen",
        "ramen_restaurant",
        "omakase",
        "izakaya",
    ),
    "Korean": ("korean", "korean_restaurant", "kbbq"),
    "Thai": ("thai", "thai_restaurant"),
    "Vietnamese": ("vietnamese", "vietnamese_restaurant", "pho"),
    "Indian": ("indian", "indian_restaurant", "north indian", "south indian"),
    "Malay": ("malay", "nasi lemak", "nasi padang"),
    "Peranakan": ("peranakan", "nyonya", "nonya"),
    "Indonesian": ("indonesian", "indonesian_restaurant"),
    "Western": (
        "western",
        "grill",
        "steakhouse",
        "steak_house",
        "american",
        "american_restaurant",
        "burger",
        "hamburger_restaurant",
    ),
    "Italian": ("italian", "italian_restaurant", "pizza", "pizza_restaurant", "pasta"),
    "French": ("french", "french_restaurant"),
    "Spanish": ("spanish", "spanish_restaurant", "tapas"),
    "Mediterranean": (
        "mediterranean",
        "mediterranean_restaurant",
        "greek",
        "greek_restaurant",
        "middle eastern",
        "middle_eastern_restaurant",
        "turkish",
        "lebanese",
    ),
    "Mexican": ("mexican", "mexican_restaurant"),
    "Fine Dining": ("fine dining", "fine_dining_restaurant", "tasting menu", "degustation"),
    "Buffet": ("buffet", "buffet_restaurant"),
    "Hawker": (
        "hawker",
        "hawker centre",
        "hawker center",
        "food court",
        "food_court",
        "kopitiam",
        "coffee shop",
    ),
    "Vegetarian": ("vegetarian", "vegetarian_restaurant", "vegan", "vegan_restaurant"),
    "Halal": ("halal",),
    "Local": ("local", "singaporean", "singapore cuisine"),
    "Asian": ("asian", "asian_restaurant", "pan asian", "fusion"),
    "Restaurant": ("restaurant", "food"),
}
_LOOKUP = {alias: canon for canon, aliases in CANONICAL.items() for alias in aliases}


def normalise_cuisines(labels: list[str] | None) -> list[str]:
    """Map raw labels to canonical cuisines, dropping unknowns and the generic 'Restaurant'
    when anything more specific is present."""
    out: list[str] = []
    for raw in labels or []:
        text = raw.strip().lower().replace("_restaurant", "_restaurant")
        canon = _LOOKUP.get(text)
        if canon is None:
            for alias, c in _LOOKUP.items():
                if re.search(rf"(?<![a-z]){re.escape(alias)}(?![a-z])", text):
                    canon = c
                    break
        if canon and canon not in out:
            out.append(canon)
    if len(out) > 1 and "Restaurant" in out:
        out.remove("Restaurant")
    return out
