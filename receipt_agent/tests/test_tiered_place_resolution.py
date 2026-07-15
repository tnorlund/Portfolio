"""Tests for the low-cost fix-place resolution tiers."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from receipt_agent.subagents.place_finder.tiered import (
    PlaceCandidate,
    ReceiptClues,
    Tier2Selection,
    _select_with_llm,
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
):
    return SimpleNamespace(
        place_id=place_id,
        name=name,
        formatted_address=address,
        formatted_phone_number=phone,
        international_phone_number=None,
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
    assert result["confidence"] == 1.0
    assert result["search_methods_used"] == ["phone"]
    assert stats["llm_calls"] == 0
    assert [method for method, _ in places.calls] == ["phone"]


def test_tier1_address_match_resolves_without_llm():
    places = _FakePlaces(address=_place())

    result, stats = asyncio.run(
        resolve_tiered_place(_address_details(), places)
    )

    assert result["resolution_tier"] == "tier1"
    assert result["confidence"] == 1.0
    assert result["search_methods_used"] == ["address"]
    assert stats["llm_calls"] == 0
    assert [method for method, _ in places.calls] == ["address"]


def test_tier1_rejects_conflicting_high_confidence_candidates():
    candidates = [
        PlaceCandidate("p1", "One", None, None, 0.99, {"phone"}),
        PlaceCandidate("p2", "Two", None, None, 0.95, {"address"}),
    ]

    assert select_deterministic_candidate(candidates, 0.90) is None


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
