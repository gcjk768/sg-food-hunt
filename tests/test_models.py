from __future__ import annotations

import pytest

from sgfoodhunt.models import (
    SearchQuery,
    VenueCandidate,
    clean_snippet,
    extract_phone,
    extract_postal_code,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("56 Eng Hoon Street, #01-70, Singapore 160056", "160056"),
        ("Bedok North, S(460123)", "460123"),
        ("Blk 123 Toa Payoh Lorong 1 Singapore 310123", "310123"),
        ("no code here 12345", None),
        ("phone 6220 3430 is not a postal code", None),
        (None, None),
    ],
)
def test_extract_postal_code(text: str | None, expected: str | None) -> None:
    assert extract_postal_code(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Tel: <PHONE>", "<PHONE>"),
        ("call 9123-4567 now", "<PHONE>"),
        ("Singapore 160056", None),  # postal code must not be read as a phone
        ("", None),
    ],
)
def test_extract_phone(text: str, expected: str | None) -> None:
    assert extract_phone(text) == expected


def test_clean_snippet_truncates_and_collapses() -> None:
    assert clean_snippet("  a \n\n b  ") == "a b"
    long = "x" * 500
    out = clean_snippet(long)
    assert out is not None and len(out) == 300 and out.endswith("…")
    assert clean_snippet("   ") is None


def test_candidate_derives_postal_and_phone_from_address() -> None:
    c = VenueCandidate(
        source_key="t", name="  Cafe   X ", address="1 Road, Singapore 123456, tel 6123 4567"
    )
    assert c.name == "Cafe X"
    assert c.postal_code == "123456"
    assert c.phone == "<PHONE>"
    assert c.confidence == 1.0


def test_candidate_rejects_empty_name() -> None:
    with pytest.raises(ValueError):
        VenueCandidate(source_key="t", name="   ")


def test_candidate_to_row_is_json_friendly() -> None:
    c = VenueCandidate(source_key="t", name="X", cuisine=["a"], extra={"k": 1}, confidence=2.0)
    q = SearchQuery("cafes_date", "dating", "best cafes", 2)
    row = c.to_row("20260101T000000Z", q, page_url="https://x")
    assert row["run_id"] == "20260101T000000Z"
    assert row["category_key"] == "cafes_date" and row["party_size"] == 2
    assert row["page_url"] == "https://x" and "page" not in row
    assert row["confidence"] == 1.0  # clamped
    assert row["captured_at"].endswith("+00:00")
