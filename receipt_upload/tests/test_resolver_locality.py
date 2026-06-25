"""Tests for locality ("City ST") parsing used to disambiguate chain stores.

A bare "Trader Joe's" Places text query returns an arbitrary branch; appending
the receipt's own "City ST" resolves the right store. The parser must pull the
locality from real receipt lines while ignoring incidental two-letter tokens.
"""

import pytest

from receipt_upload.merchant_resolution.resolver import (
    locality_from_lines,
    parse_locality,
    state_from_zip,
)


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


@pytest.mark.parametrize(
    "zip5,expected",
    [
        ("89014", "NV"),  # Henderson
        ("89169", "NV"),  # Las Vegas
        ("02132", "MA"),  # Boston
        ("75068", "TX"),  # Little Elm
        ("90001", "CA"),
        ("00097", None),  # 000 prefix -> not a state
        ("abcde", None),
    ],
)
def test_state_from_zip(zip5, expected):
    assert state_from_zip(zip5) == expected


def test_locality_from_lines_clean_form():
    lines = ["TRADER JOE'S", "123 Main St", "Henderson, NV 89014"]
    assert locality_from_lines(lines) == "Henderson, NV"


def test_locality_from_lines_fragmented_zip_pairs_with_prior_city():
    """Regression for the faint Trader Joe's scan: the state letters were dropped,
    leaving "Henderson." over a bare "89014". The ZIP supplies the state and the
    line above supplies the city."""
    lines = [
        "TRADER JOE'S",
        "2716 North Green Valley Parkway",
        "Henderson.",
        "89014",
        "Store #0097 - 702 433-6773",
    ]
    assert locality_from_lines(lines) == "Henderson, NV"


def test_locality_from_lines_city_on_same_line_as_zip():
    assert locality_from_lines(["x", "Las Vegas NV 89169"]) == "Las Vegas, NV"


def test_locality_from_lines_street_only_prev_line_is_not_a_city():
    # The line above the ZIP is pure street -> no city is fabricated.
    assert locality_from_lines(["4600 E Sunset Rd", "89014"]) is None


def test_locality_from_lines_none_without_zip_or_state():
    assert locality_from_lines(["WELCOME", "THANK YOU"]) is None


def test_parse_locality_keeps_saint_city():
    # "St." at the start is part of the city name, not a street suffix.
    assert parse_locality("St. Louis, MO 63101") == "St. Louis, MO"


def test_locality_from_lines_ignores_non_zip_5digit_tokens():
    # A 5-digit auth/transaction number must not fabricate a state+city.
    assert locality_from_lines(["AUTH #12345 APPROVED", "Anytown"]) is None
    assert locality_from_lines(["TOTAL 12345", "Anytown"]) is None


@pytest.mark.parametrize(
    "zip5,expected",
    [
        ("05501", "MA"),  # 055xx is MA, not VT
        ("20101", "VA"),  # 201xx (Dulles) is VA, not DC
        ("20001", "DC"),
        ("34002", None),  # 340xx military, not FL
        ("33901", "FL"),
    ],
)
def test_state_from_zip_exceptions(zip5, expected):
    assert state_from_zip(zip5) == expected
