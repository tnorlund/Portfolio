"""Geo-guard regression tests for the Place ID Finder's direct Places search.

A bare chain text search returns an arbitrary branch — "Trader Joe's" resolved
to Boston MA for a Henderson NV receipt; "CVS" to Little Elm TX for a Las Vegas
receipt. The finder now derives the receipt's own state and rejects a Places hit
whose state clearly contradicts it, falling through to the next strategy.
"""

from types import SimpleNamespace

import pytest
from receipt_agent.agents.place_id_finder.tools.place_id_finder import (
    PlaceIdFinder,
    ReceiptRecord,
    _extract_us_state,
)


def _place(place_id, name, formatted_address, phone=None):
    return SimpleNamespace(
        place_id=place_id,
        name=name,
        formatted_address=formatted_address,
        formatted_phone_number=phone,
        international_phone_number=phone,
    )


class _FakePlaces:
    """Records calls and returns scripted results per search method."""

    def __init__(self, by_phone=None, by_address=None, by_text=None):
        self._by_phone = by_phone
        self._by_address = by_address
        self._by_text = by_text
        self.calls = []

    def search_by_phone(self, phone):
        self.calls.append(("phone", phone))
        return self._by_phone

    def search_by_address(self, address):
        self.calls.append(("address", address))
        return self._by_address

    def search_by_text(self, query, lat=None, lng=None):
        self.calls.append(("text", query))
        return self._by_text


@pytest.mark.parametrize(
    "text,expected",
    [
        ("2591 FM 423, Little Elm, TX 75068, USA", "TX"),
        ("1999 Centre St, Boston, MA 02132, USA", "MA"),
        ("3810 E. VEGAS, NV", "NV"),
        ("CVS pharmacy VEGAS, NV", "NV"),  # enriched query (comma form)
        ("CVS pharmacy VEGAS NV", None),  # no comma/zip -> not trusted
        ("Trader Joe's", None),  # no confident state
        ("buy 2 or 3", None),  # 'OR' must not be read as Oregon mid-text
        ("IN-N-OUT BURGER", None),  # 'IN' must not be read as Indiana
    ],
)
def test_extract_us_state(text, expected):
    assert _extract_us_state(text) == expected


def test_wrong_state_address_hit_falls_through_to_text():
    """CVS case: address geocodes to the wrong-state branch (Little Elm TX); the
    guard rejects it and the locality-enriched text search wins (Las Vegas NV).
    """
    wrong = _place("p_tx", "CVS", "2591 FM 423, Little Elm, TX 75068, USA")
    right = _place(
        "p_nv", "CVS", "3680 S Maryland Pkwy, Las Vegas, NV 89169, USA"
    )
    places = _FakePlaces(by_address=wrong, by_text=right)
    finder = PlaceIdFinder(dynamo_client=None, places_client=places)

    receipt = ReceiptRecord(
        image_id="img",
        receipt_id=1,
        merchant_name="CVS pharmacy VEGAS, NV",
        address="3810 E. VEGAS, NV",
    )
    match = finder._search_places_for_receipt(receipt)

    assert match.found is True
    assert match.place_id == "p_nv"  # not the Little Elm TX hit
    assert match.search_method == "text"
    # The address strategy was tried (and rejected) before the text strategy.
    assert [c[0] for c in places.calls] == ["address", "text"]


def test_matching_state_is_accepted():
    """A same-state address hit is accepted as usual (no false rejection)."""
    good = _place(
        "p_nv", "CVS", "3680 S Maryland Pkwy, Las Vegas, NV 89169, USA"
    )
    places = _FakePlaces(by_address=good)
    finder = PlaceIdFinder(dynamo_client=None, places_client=places)
    receipt = ReceiptRecord(
        image_id="img",
        receipt_id=1,
        merchant_name="CVS",
        address="3810 E. VEGAS, NV",
    )
    match = finder._search_places_for_receipt(receipt)
    assert match.found is True
    assert match.place_id == "p_nv"
    assert match.search_method == "address"


def test_explicit_expected_state_rejects_wrong_state_text_hit():
    """The resolver passes the parsed state via ReceiptRecord.expected_state, so
    the guard works for a name+locality-only receipt whose merchant_name token
    stream wouldn't otherwise parse a state (no comma/zip)."""
    boston = _place(
        "p_ma", "Trader Joe's", "1999 Centre St, Boston, MA 02132, USA"
    )
    places = _FakePlaces(by_text=boston)
    finder = PlaceIdFinder(dynamo_client=None, places_client=places)
    receipt = ReceiptRecord(
        image_id="img",
        receipt_id=1,
        merchant_name="Trader Joe's Henderson NV",  # no comma -> name won't parse
        expected_state="NV",  # but the resolver knows the state
    )
    match = finder._search_places_for_receipt(receipt)
    assert (
        match.found is False
    )  # Boston rejected; no in-state alternative here
    assert ("text", "Trader Joe's Henderson NV") in places.calls


def test_unknown_receipt_state_never_rejects():
    """With no parseable state on the receipt, any hit is accepted (the guard is
    conservative — unknown is not a conflict)."""
    hit = _place(
        "p_ma", "Trader Joe's", "1999 Centre St, Boston, MA 02132, USA"
    )
    places = _FakePlaces(by_text=hit)
    finder = PlaceIdFinder(dynamo_client=None, places_client=places)
    receipt = ReceiptRecord(
        image_id="img", receipt_id=1, merchant_name="Trader Joe's"
    )
    match = finder._search_places_for_receipt(receipt)
    assert match.found is True
    assert match.place_id == "p_ma"
