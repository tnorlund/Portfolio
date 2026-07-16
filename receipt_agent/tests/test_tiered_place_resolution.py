"""Tests for the low-cost fix-place resolution tiers."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from receipt_agent.subagents.place_finder.tiered import (
    PlaceCandidate,
    ReceiptClues,
    Tier2Selection,
    _addresses_match,
    _select_with_llm,
    extract_receipt_clues,
    resolve_tiered_place,
    select_deterministic_candidate,
)


def _word(line_id, word_id, text):
    return SimpleNamespace(line_id=line_id, word_id=word_id, text=text)


def _label(line_id, word_id, label, status="VALID"):
    return SimpleNamespace(
        line_id=line_id,
        word_id=word_id,
        label=label,
        validation_status=status,
    )


def _place(
    place_id="ChIJ-test",
    name="Test Market",
    address="123 Main St, Las Vegas, NV 89101, USA",
    phone="(702) 555-0100",
    types=None,
):
    return SimpleNamespace(
        place_id=place_id,
        name=name,
        formatted_address=address,
        formatted_phone_number=phone,
        international_phone_number=None,
        types=types or ["establishment", "point_of_interest"],
    )


def _details(*, current_place=None, phone="(702) 555-0100"):
    return SimpleNamespace(
        place=current_place,
        words=[_word(1, 1, phone)],
        labels=[_label(1, 1, "PHONE_NUMBER")],
        lines=[SimpleNamespace(line_id=1, text=phone)],
    )


def _address_details():
    words = [
        _word(2, 1, "123"),
        _word(2, 2, "Main"),
        _word(2, 3, "St"),
        _word(3, 1, "Las Vegas, NV 89101"),
    ]
    return SimpleNamespace(
        place=None,
        words=words,
        labels=[
            _label(word.line_id, word.word_id, "ADDRESS_LINE")
            for word in words
        ],
        lines=[
            SimpleNamespace(line_id=2, text="123 Main St"),
            SimpleNamespace(line_id=3, text="Las Vegas, NV 89101"),
        ],
    )


class _FakePlaces:
    def __init__(self, *, details=None, phone=None, address=None, text=None):
        self.details = details
        self.phone = phone
        self.address = address
        self.text = text
        self.calls = []

    def get_place_details(self, place_id):
        self.calls.append(("details", place_id))
        return self.details

    def search_by_phone(self, phone):
        self.calls.append(("phone", phone))
        return self.phone

    def search_by_address(self, address):
        self.calls.append(("address", address))
        return self.address

    def search_by_text(self, text):
        self.calls.append(("text", text))
        return self.text


def test_tier0_keeps_consistent_existing_place_without_llm():
    current = SimpleNamespace(
        place_id="ChIJ-test",
        merchant_name="Test Market",
        formatted_address="123 Main St, Las Vegas, NV 89101, USA",
        phone_number="(702) 555-0100",
    )
    places = _FakePlaces(details=_place())

    result, stats = asyncio.run(
        resolve_tiered_place(_details(current_place=current), places)
    )

    assert result["resolution_tier"] == "tier0"
    assert result["place_id"] == "ChIJ-test"
    assert stats["llm_calls"] == 0
    assert places.calls == [("details", "ChIJ-test")]


def test_tier1_phone_match_resolves_without_llm():
    places = _FakePlaces(phone=_place())

    result, stats = asyncio.run(resolve_tiered_place(_details(), places))

    assert result["resolution_tier"] == "tier1"
    assert result["confidence"] == 0.95
    assert result["search_methods_used"] == ["phone"]
    assert stats["llm_calls"] == 0
    assert [method for method, _ in places.calls] == ["phone"]


def test_tier1_address_match_resolves_without_llm():
    places = _FakePlaces(address=_place())

    result, stats = asyncio.run(
        resolve_tiered_place(_address_details(), places)
    )

    assert result["resolution_tier"] == "tier1"
    assert result["confidence"] == 0.90
    assert result["search_methods_used"] == ["address"]
    assert stats["llm_calls"] == 0
    assert [method for method, _ in places.calls] == ["address"]


def test_tier1_rejects_conflicting_high_confidence_candidates():
    candidates = [
        PlaceCandidate("p1", "One", None, None, 0.99, {"phone"}),
        PlaceCandidate("p2", "Two", None, None, 0.95, {"address"}),
    ]

    assert select_deterministic_candidate(candidates, 0.90) is None


def test_tier1_rejects_unrelated_phone_text_fallback(monkeypatch):
    """A phone search result must return the same phone it was searched by."""
    monkeypatch.setenv("FIX_PLACE_TIER2_ENABLED", "false")
    unrelated = _place(
        place_id="wrong",
        name="Professional Roofing Services",
        address="4180 W Patrick Ln, Las Vegas, NV 89118, USA",
        phone="(702) 500-0670",
    )
    places = _FakePlaces(phone=unrelated)

    result, stats = asyncio.run(resolve_tiered_place(_details(), places))

    assert result is None
    assert stats["llm_calls"] == 0


def test_text_search_uses_merchant_and_rejects_wrong_phone_result():
    details = SimpleNamespace(
        place=None,
        words=[
            _word(1, 1, "CRAFTKITCHEN"),
            _word(2, 1, "10940"),
            _word(2, 2, "S"),
            _word(2, 3, "EASTERN"),
            _word(2, 4, "AVE"),
            _word(2, 5, "#107"),
            _word(3, 1, "HENDERSON,"),
            _word(3, 2, "NV"),
            _word(3, 3, "89052"),
            _word(4, 1, "702"),
            _word(4, 2, "728"),
            _word(4, 3, "5838"),
        ],
        labels=[
            _label(1, 1, "MERCHANT_NAME"),
            *[_label(2, word_id, "ADDRESS_LINE") for word_id in range(1, 6)],
            *[_label(3, word_id, "ADDRESS_LINE") for word_id in range(1, 4)],
            *[_label(4, word_id, "PHONE_NUMBER") for word_id in range(1, 4)],
        ],
        lines=[SimpleNamespace(line_id=1, text="CRAFTKITCHEN")],
    )
    unrelated = _place(
        place_id="wrong",
        name="Professional Roofing Services",
        address="4180 W Patrick Ln, Las Vegas, NV 89118, USA",
        phone="(702) 500-0670",
    )
    correct = _place(
        place_id="craft",
        name="CRAFT Kitchen",
        address="10940 S Eastern Ave Suite 107, Henderson, NV 89052, USA",
        phone=None,
    )
    address_only = _place(
        place_id="address",
        name="10940 S Eastern Ave Suite 107",
        address="10940 S Eastern Ave Suite 107, Henderson, NV 89052, USA",
        phone=None,
        types=["street_address", "subpremise"],
    )
    places = _FakePlaces(phone=unrelated, address=address_only, text=correct)

    result, stats = asyncio.run(resolve_tiered_place(details, places))

    assert result["place_id"] == "craft"
    assert result["resolution_tier"] == "tier1"
    assert result["phone_number"] == "702 728 5838"
    assert result["sources"]["phone_number"] == "receipt_labels"
    assert stats["llm_calls"] == 0
    text_call = next(
        query for method, query in places.calls if method == "text"
    )
    assert text_call == "CRAFTKITCHEN"


def test_address_clues_drop_stray_header_labels_before_street_number():
    details = SimpleNamespace(
        place=None,
        words=[
            _word(1, 1, "Henderson"),
            _word(2, 1, "4300"),
            _word(2, 2, "E Sunset Rd. Suite A3"),
            _word(3, 1, "Henderson, NV 89014"),
        ],
        labels=[
            _label(1, 1, "ADDRESS_LINE"),
            _label(2, 1, "ADDRESS_LINE"),
            _label(2, 2, "ADDRESS_LINE"),
            _label(3, 1, "ADDRESS_LINE"),
        ],
        lines=[],
    )

    assert extract_receipt_clues(details).address == (
        "4300 E Sunset Rd. Suite A3, Henderson, NV 89014"
    )


def test_address_matching_ignores_equivalent_unit_notation():
    assert _addresses_match(
        "10940 S EASTERN AVE #107, HENDERSON, NV 89052",
        "10940 S Eastern Avenue Suite 107, Henderson, NV 89052, USA",
    )


def test_address_matching_requires_exact_street_number():
    assert not _addresses_match(
        "1 Main St, Las Vegas, NV 89101",
        "11 Main St, Las Vegas, NV 89101",
    )


def test_tiered_resolution_escalates_without_receipt_place_evidence():
    current = SimpleNamespace(
        place_id="ChIJ-wrong",
        merchant_name="Wrong Market",
        formatted_address="11 Main St, Las Vegas, NV 89101, USA",
        phone_number="(702) 555-0111",
    )
    details = SimpleNamespace(
        place=current,
        words=[_word(1, 1, "Wrong Market")],
        labels=[_label(1, 1, "MERCHANT_NAME", status="INVALID")],
        lines=[SimpleNamespace(line_id=1, text="Wrong Market")],
    )
    places = _FakePlaces(details=_place(place_id="ChIJ-wrong"))

    result, stats = asyncio.run(resolve_tiered_place(details, places))

    assert result is None
    assert stats["llm_calls"] == 0
    assert places.calls == []


def test_tier2_makes_one_structured_picker_call():
    candidate = PlaceCandidate(
        "ChIJ-test",
        "Test Market",
        "123 Main St, Las Vegas, NV 89101, USA",
        "(702) 555-0100",
        0.80,
        {"text"},
    )
    structured_llm = MagicMock()
    structured_llm.ainvoke = AsyncMock(
        return_value=Tier2Selection(
            place_id="ChIJ-test",
            confidence=0.91,
            reasoning="Receipt name and address match.",
        )
    )
    base_llm = MagicMock()
    base_llm.with_structured_output.return_value = structured_llm

    with patch(
        "receipt_agent.subagents.place_finder.tiered.create_llm",
        return_value=base_llm,
    ) as create_llm:
        result, _ = asyncio.run(
            _select_with_llm(
                ReceiptClues(
                    merchant_name="Test Market",
                    address="123 Main St",
                    snippet="TEST MARKET\n123 MAIN ST",
                ),
                [candidate],
            )
        )

    assert result["resolution_tier"] == "tier2"
    assert result["place_id"] == "ChIJ-test"
    structured_llm.ainvoke.assert_awaited_once()
    assert create_llm.call_args.kwargs["model"] == "openai/gpt-oss-120b"
    assert create_llm.call_args.kwargs["reasoning"] is True
