"""Unit tests for merchant metadata builders."""

from uuid import uuid4

import pytest

from receipt_label.merchant_validation.metadata_builder import (
    build_receipt_metadata_from_result,
    build_receipt_metadata_from_result_no_match,
)


@pytest.mark.unit
def test_build_receipt_metadata_from_result_basic():
    """Google-only metadata is mapped correctly."""
    image_id = str(uuid4())
    google_place = {
        "place_id": "pid",
        "name": "Shop",
        "formatted_address": "123 Main St",
        "formatted_phone_number": "555-1234",
        "types": ["store"],
    }

    metadata = build_receipt_metadata_from_result(
        image_id, 1, google_place, {}
    )

    assert metadata.image_id == image_id
    assert metadata.receipt_id == 1
    assert metadata.place_id == "pid"
    assert metadata.merchant_name == "Shop"
    assert metadata.address == "123 Main St"
    assert metadata.phone_number == "555-1234"
    assert metadata.merchant_category == "store"
    assert metadata.matched_fields == []
    assert metadata.validated_by == "GooglePlaces"
    assert metadata.reasoning == "Selected merchant based on Google Places"
    assert metadata.timestamp.tzinfo is not None


@pytest.mark.unit
def test_build_receipt_metadata_from_result_with_gpt():
    """Google metadata combined with GPT fields."""
    image_id = str(uuid4())
    google_place = {
        "place_id": "pid",
        "name": "Shop",
        "formatted_address": "123 Main St",
        # No phone in Google result
        "types": ["store"],
    }
    gpt_result = {"phone_number": "555-5678", "matched_fields": ["phone"]}

    metadata = build_receipt_metadata_from_result(
        image_id, 2, google_place, gpt_result
    )

    assert metadata.phone_number == "555-5678"
    assert metadata.matched_fields == ["phone"]
    assert metadata.validated_by == "GPT+GooglePlaces"
    assert (
        metadata.reasoning
        == "Selected merchant based on Google Places with GPT validation"
    )
    assert metadata.timestamp.tzinfo is not None


@pytest.mark.unit
def test_build_receipt_metadata_from_result_no_match_blank():
    """No Google match and no GPT result uses empty defaults."""
    image_id = str(uuid4())

    metadata = build_receipt_metadata_from_result_no_match(3, image_id, None)

    assert metadata.image_id == image_id
    assert metadata.receipt_id == 3
    assert metadata.place_id == ""
    assert metadata.merchant_name == ""
    assert metadata.address == ""
    assert metadata.phone_number == ""
    assert metadata.matched_fields == []
    assert metadata.validated_by == "None"
    assert (
        metadata.reasoning
        == "No valid Google Places match and no GPT inference was performed"
    )
    assert metadata.timestamp.tzinfo is not None


@pytest.mark.unit
def test_build_receipt_metadata_from_result_no_match_with_gpt():
    """No Google match but GPT provides metadata."""
    image_id = str(uuid4())
    gpt_result = {
        "name": "G Shop",
        "address": "321 Other Ave",
        "phone_number": "555-0000",
        "matched_fields": ["name", "phone"],
    }

    metadata = build_receipt_metadata_from_result_no_match(
        4, image_id, gpt_result
    )

    assert metadata.merchant_name == "G Shop"
    assert metadata.address == "321 Other Ave"
    assert metadata.phone_number == "555-0000"
    assert metadata.matched_fields == ["name", "phone"]
    assert metadata.validated_by == "GPT"
    assert (
        metadata.reasoning
        == "No valid Google Places match; used GPT inference"
    )
    assert metadata.timestamp.tzinfo is not None
