"""Tests for locality ("City ST") parsing used to disambiguate chain stores.

A bare "Trader Joe's" Places text query returns an arbitrary branch; appending
the receipt's own "City ST" resolves the right store. The parser must pull the
locality from real receipt lines while ignoring incidental two-letter tokens.
"""

import pytest

from receipt_upload.merchant_resolution.resolver import parse_locality


@pytest.mark.parametrize(
    "text,expected",
    [
        ("E. VEGAS, NV", "VEGAS, NV"),  # period directional stripped
        ("Henderson, NV", "Henderson, NV"),  # comma form
        ("Henderson NV 89014-2132", "Henderson, NV"),  # zip form
        ("Las Vegas, NV 89169", "Las Vegas, NV"),  # 2-word city
        ("North Las Vegas, NV 89030", "North Las Vegas, NV"),  # 3-word city
        # A period-less directional is part of the city name and must be kept,
        # so "N Las Vegas" is never collapsed into "Las Vegas" (a different city).
        ("N Las Vegas, NV", "N Las Vegas, NV"),
        # Street + city on one OCR line: the street is trimmed off, city kept.
        ("4600 E. SUNSET RD Henderson, NV", "Henderson, NV"),
        ("2718 N Green Valley Pkwy Henderson, NV", "Henderson, NV"),
    ],
)
def test_parse_locality_extracts_city_state(text, expected):
    assert parse_locality(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "4600 E. SUNSET RD",  # street only, no city/state
        "US DEBIT ************1454",  # 'US' is not a state in this context
        "or visit CVs.co/extracare",  # 'or' must not read as Oregon
        "2716 North Green Valley parkway",  # no state token
        "",
    ],
)
def test_parse_locality_ignores_non_localities(text):
    assert parse_locality(text) is None
