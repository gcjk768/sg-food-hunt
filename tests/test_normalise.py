from __future__ import annotations

import pytest

from sgfoodhunt.normalise import (
    name_key,
    normalise_cuisines,
    normalise_hours,
    price_level_from_text,
    region_for_postal,
    split_brand_outlet,
)
from sgfoodhunt.normalise.hours import parse_hours_text


@pytest.mark.parametrize(
    ("name", "key"),
    [
        ("Keng Eng Kee Seafood (琼荣记)", "keng eng kee 琼荣记"),
        ("KEK Seafood", "kek"),
        ("Tiong Bahru Bakery", "tiong bahru bakery"),
        (
            "The Coffee Bar Pte Ltd",
            "coffee bar",
        ),  # falls back when everything is noise? no: 'coffee','bar' are noise
        ("Café Monochrome", "monochrome"),
        ("琼荣记", "琼荣记"),
    ],
)
def test_name_key(name: str, key: str) -> None:
    assert name_key(name) == key


def test_split_brand_outlet() -> None:
    assert split_brand_outlet("Din Tai Fung @ Paragon") == ("Din Tai Fung", "Paragon")
    assert split_brand_outlet("Imperial Treasure (ION Orchard)") == (
        "Imperial Treasure",
        "ION Orchard",
    )
    assert split_brand_outlet("Ah Hua Zi Char") == ("Ah Hua Zi Char", None)
    assert split_brand_outlet("Keng Eng Kee (琼荣记)") == ("Keng Eng Kee (琼荣记)", None)


@pytest.mark.parametrize(
    ("text", "level"),
    [
        ("$$", 2),
        ("$$$$", 4),
        ("20-40 SGD", 2),
        ("~$120 per pax", 4),
        ("$8", 1),
        ("", None),
        ("cheap", None),
    ],
)
def test_price_level_from_text(text: str, level: int | None) -> None:
    assert price_level_from_text(text) == level


def test_normalise_cuisines() -> None:
    assert normalise_cuisines(["chinese_restaurant", "seafood_restaurant", "restaurant"]) == [
        "Chinese",
        "Seafood",
    ]
    assert normalise_cuisines(["Zi Char", "Cafes & Coffee, Brunch"]) == ["Zi Char", "Cafe"]
    assert normalise_cuisines(["restaurant"]) == ["Restaurant"]
    assert normalise_cuisines(["Unknownese"]) == []
    assert normalise_cuisines(None) == []


def test_google_hours() -> None:
    info = normalise_hours(
        {
            "weekday_descriptions": [
                "Monday: 7:30 AM – 8:00 PM",
                "Tuesday: Closed",
                "Friday: 11:30 AM – 2:30 PM, 5:00 – 11:30 PM",
                "Saturday: 7:30 AM – 8:00 PM",
                "Sunday: Open 24 hours",
            ]
        }
    )
    assert info is not None
    assert info.hours["mon"] == [["07:30", "20:00"]] and info.hours["tue"] == []
    assert info.hours["fri"] == [["11:30", "14:30"], ["17:00", "23:30"]]
    assert info.hours["sun"] == [["00:00", "24:00"]]
    assert info.open_weekends is True and info.late_night is True


def test_text_hours() -> None:
    info = parse_hours_text("Mon-Fri 11.30am-2.30pm, 5-10pm; Sat-Sun 11am-10.30pm; Closed on PH")
    assert info.hours["mon"] == [["11:30", "14:30"], ["17:00", "22:00"]]
    assert info.hours["fri"] == info.hours["mon"]
    assert info.hours["sat"] == [["11:00", "22:30"]] and info.hours["sun"] == info.hours["sat"]
    assert info.hours["ph"] == [] and info.ph_closed is True
    assert info.open_weekends is True and info.late_night is False
    daily = parse_hours_text("7.30am to 8pm daily")
    assert daily.hours["wed"] == [["07:30", "20:00"]] and daily.open_weekends is True
    assert normalise_hours({"text": "call for hours"}) is None
    assert normalise_hours(None) is None


def test_region_for_postal() -> None:
    assert region_for_postal("160056") == ("D03 Queenstown / Tiong Bahru", "Central")
    assert region_for_postal("520123") == ("D18 Tampines / Pasir Ris", "East")
    assert region_for_postal("640123") == ("D22 Jurong / Boon Lay", "West")
    assert region_for_postal("730123") == ("D25 Woodlands / Admiralty", "North")
    assert region_for_postal("560123") == ("D20 Ang Mo Kio / Bishan", "North East")
    assert region_for_postal("999999") == (None, None)
    assert region_for_postal(None) == (None, None)
