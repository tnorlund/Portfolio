# pylint: disable=redefined-outer-name,protected-access
from datetime import datetime

import pytest

from receipt_dynamo.constants import MerchantValidationStatus
from receipt_dynamo.entities.receipt_metadata import (
    ReceiptMetadata,
    item_to_receipt_metadata,
)


@pytest.fixture
def example_receipt_metadata():
    return ReceiptMetadata(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        receipt_id=101,
        place_id="ChIJZ6Yy123",
        merchant_name="Starbucks",
        merchant_category="Coffee Shop",
        address="123 Main St, Anytown, USA",
        phone_number="(123) 456-7890",
        matched_fields=["name", "address"],
        validated_by="NEARBY_LOOKUP",
        timestamp=datetime(2025, 1, 1, 12, 0, 0),
        reasoning="Matches known Starbucks location",
    )


@pytest.mark.unit
def test_basic_construction_and_status(example_receipt_metadata):
    m = example_receipt_metadata
    assert m.image_id == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    assert m.receipt_id == 101
    assert m.place_id == "ChIJZ6Yy123"
    assert m.merchant_name == "Starbucks"
    assert m.merchant_category == "Coffee Shop"
    assert m.address == "123 Main St, Anytown, USA"
    assert m.phone_number == "(123) 456-7890"
    assert m.matched_fields == ["name", "address"]
    assert m.validated_by == "NEARBY_LOOKUP"
    assert m.timestamp == datetime(2025, 1, 1, 12, 0, 0)
    assert m.reasoning == "Matches known Starbucks location"
    assert m.validation_status == MerchantValidationStatus.MATCHED


@pytest.mark.unit
@pytest.mark.parametrize(
    "matched_fields, expected_status",
    [
        (["name", "address"], MerchantValidationStatus.MATCHED),
        (["name"], MerchantValidationStatus.UNSURE),
        ([], MerchantValidationStatus.NO_MATCH),
    ],
)
def test_validation_status_buckets(matched_fields, expected_status):
    m = ReceiptMetadata(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        receipt_id=1,
        place_id="id",
        merchant_name="Name",
        merchant_category="Category",
        address="Address",
        phone_number="Phone",
        matched_fields=matched_fields,
        validated_by="NEARBY_LOOKUP",
        timestamp=datetime.now(),
        reasoning="testing",
    )
    assert m.validation_status == expected_status


@pytest.mark.unit
def test_key_and_gsi_keys(example_receipt_metadata):
    m = example_receipt_metadata
    pk = m.key
    assert pk["PK"]["S"] == f"IMAGE#{m.image_id}"
    assert pk["SK"]["S"] == f"RECEIPT#{m.receipt_id:05d}#METADATA"

    gsi1 = m.gsi1_key()
    assert (
        gsi1["GSI1PK"]["S"]
        == f"MERCHANT#{m.merchant_name.upper().replace(' ', '_')}"
    )
    assert "IMAGE#" in gsi1["GSI1SK"]["S"]
    assert "RECEIPT#" in gsi1["GSI1SK"]["S"]

    gsi2 = m.gsi2_key()
    assert gsi2["GSI2PK"]["S"] == f"PLACE#{m.place_id}"
    assert "IMAGE#" in gsi2["GSI2SK"]["S"]
    assert "RECEIPT#" in gsi2["GSI2SK"]["S"]

    gsi3 = m.gsi3_key()
    assert gsi3["GSI3PK"]["S"] == "MERCHANT_VALIDATION"
    assert f"STATUS#{m.validation_status}" in gsi3["GSI3SK"]["S"]


@pytest.mark.unit
def test_to_item_and_back(example_receipt_metadata):
    m = example_receipt_metadata
    item = m.to_item()
    restored = item_to_receipt_metadata(item)
    # Compare essential fields
    assert restored.image_id == m.image_id
    assert restored.receipt_id == m.receipt_id
    assert restored.place_id == m.place_id
    assert restored.merchant_name == m.merchant_name
    assert restored.merchant_category == m.merchant_category
    assert restored.address == m.address
    assert restored.phone_number == m.phone_number
    assert restored.matched_fields == m.matched_fields
    assert restored.validated_by == m.validated_by
    assert restored.timestamp == m.timestamp
    assert restored.reasoning == m.reasoning


@pytest.mark.unit
@pytest.mark.parametrize(
    "field,value,error",
    [
        ("receipt_id", "a", "receipt_id must be an integer"),
        ("receipt_id", 0, "receipt_id must be positive"),
        ("place_id", 123, "place id must be a string"),
        ("merchant_name", 456, "merchant name must be a string"),
        ("merchant_category", 789, "merchant category must be a string"),
        ("address", None, "address must be a string"),
        ("phone_number", 12345, "phone number must be a string"),
        ("matched_fields", "notalist", "matched fields must be a list"),
        ("matched_fields", [1, 2], "matched fields must be a list of strings"),
        ("matched_fields", ["dup", "dup"], "matched fields must be unique"),
        (
            "validated_by",
            123,
            "ValidationMethod must be a str or ValidationMethod instance",
        ),
        ("timestamp", "2025-01-01", "timestamp must be a datetime"),
        ("reasoning", 456, "reasoning must be a string"),
    ],
)
def test_invalid_field_validation(field, value, error):
    kwargs = {
        "image_id": "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "receipt_id": 1,
        "place_id": "id",
        "merchant_name": "Name",
        "merchant_category": "Cat",
        "address": "Addr",
        "phone_number": "Phone",
        "matched_fields": [],
        "validated_by": "NEARBY_LOOKUP",
        "timestamp": datetime.now(),
        "reasoning": "Reason",
    }
    kwargs[field] = value
    with pytest.raises(ValueError, match=error):
        ReceiptMetadata(**kwargs)


@pytest.mark.unit
def test_item_to_receipt_metadata_missing_keys():
    item = {"PK": {"S": "IMAGE#id"}}
    with pytest.raises(ValueError, match="Item is missing required keys"):
        item_to_receipt_metadata(item)


@pytest.mark.unit
def test_item_to_receipt_metadata_parse_error(example_receipt_metadata):
    item = example_receipt_metadata.to_item()
    item["SK"]["S"] = "BADFORMAT"
    with pytest.raises(ValueError, match="Invalid SK format:"):
        item_to_receipt_metadata(item)


@pytest.mark.unit
def test_configurable_validation_thresholds(monkeypatch):
    """Test that validation thresholds can be configured via environment
    variables."""
    # Test with custom thresholds: 3 fields for MATCHED, 2 for UNSURE
    monkeypatch.setenv("MIN_FIELDS_FOR_MATCH", "3")
    monkeypatch.setenv("MIN_FIELDS_FOR_UNSURE", "2")

    # 2 fields should now be UNSURE instead of MATCHED
    m1 = ReceiptMetadata(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        receipt_id=1,
        place_id="id",
        merchant_name="Test Store",
        address="123 Test St",
        phone_number="555-1234",
        matched_fields=["name", "phone"],
        validated_by="PHONE_LOOKUP",
        timestamp=datetime.now(),
        reasoning="testing",
    )
    assert m1.validation_status == MerchantValidationStatus.UNSURE.value

    # 3 fields should be MATCHED
    m2 = ReceiptMetadata(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        receipt_id=2,
        place_id="id2",
        merchant_name="Test Store 2",
        address="456 Test Ave",
        phone_number="555-5678",
        matched_fields=["name", "phone", "address"],
        validated_by="ADDRESS_LOOKUP",
        timestamp=datetime.now(),
        reasoning="testing",
    )
    assert m2.validation_status == MerchantValidationStatus.MATCHED.value

    # 1 field should be NO_MATCH
    m3 = ReceiptMetadata(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        receipt_id=3,
        place_id="id3",
        merchant_name="Test Store 3",
        address="",
        phone_number="",
        matched_fields=["name"],
        validated_by="TEXT_SEARCH",
        timestamp=datetime.now(),
        reasoning="testing",
    )
    assert m3.validation_status == MerchantValidationStatus.NO_MATCH.value


@pytest.mark.unit
def test_address_validation_quality():
    """Test that address validation properly handles various address
    formats."""
    test_cases = [
        # (address, should_pass, description)
        ("123 Main St", True, "Common format with abbreviation"),
        ("456 Broadway Avenue", True, "Full street name"),
        ("1st Ave", True, "Alphanumeric street number with abbreviation"),
        ("42 E 23rd Street", True, "Multiple components with direction"),
        ("5678 Highway 101", True, "Highway address"),
        ("100-200 Park Ln", True, "Range format"),
        ("10 Downing Street, London", True, "International format"),
        ("Suite 500, Oak Tower", True, "Suite address"),
        ("St", False, "Just abbreviation - not enough"),
        ("123", False, "Just number - not enough"),
        ("", False, "Empty address"),
        ("X Y", False, "Too short tokens"),
        ("The Mall", True, "Named location (3+ char tokens)"),
        ("5th & Main", True, "Intersection format"),
    ]

    for address, should_pass, description in test_cases:
        m = ReceiptMetadata(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=1,
            place_id="test",
            merchant_name="Test Store",
            address=address,
            phone_number="555-555-5555",
            matched_fields=["address", "phone"],  # Claims to match address
            validated_by="ADDRESS_LOOKUP",
            timestamp=datetime.now(),
            reasoning="testing address validation",
        )

        # With default thresholds, 2 fields = MATCHED, but only if quality
        # passes
        if should_pass:
            assert (
                m.validation_status == MerchantValidationStatus.MATCHED.value
            ), (f"{description}: Address '{address}' should have passed "
                "validation")
        else:
            # If address quality fails, it's effectively only 1 field (phone)
            assert (
                m.validation_status == MerchantValidationStatus.UNSURE.value
            ), (f"{description}: Address '{address}' should have failed "
                "quality validation")


@pytest.mark.unit
def test_get_high_quality_matched_fields():
    """Test the _get_high_quality_matched_fields method with various edge
    cases."""

    # Test phone validation edge cases
    phone_test_cases = [
        # (phone_number, should_pass, description)
        ("(555) 123-4567", True, "Full US phone with area code"),
        ("555-1234", True, "7-digit phone"),
        ("5551234", True, "7 digits no formatting"),
        ("123-4567", True, "Exactly 7 digits with dash"),
        ("555-123", False, "Only 6 digits"),
        ("12345", False, "Only 5 digits"),
        ("(555) 12-FOOD", False, "Less than 7 digits with letters"),
        ("+1-555-123-4567", True, "International format"),
        ("555.123.4567", True, "Dot separator"),
        ("555 123 4567", True, "Space separator"),
    ]

    for phone, should_pass, description in phone_test_cases:
        m = ReceiptMetadata(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=1,
            place_id="test",
            merchant_name="Test Store",
            address="123 Main St",
            phone_number=phone,
            matched_fields=["phone"],
            validated_by="PHONE_LOOKUP",
            timestamp=datetime.now(),
            reasoning="testing phone validation",
        )

        high_quality = m._get_high_quality_matched_fields()
        if should_pass:
            assert (
                "phone" in high_quality
            ), f"{description}: Phone '{phone}' should pass quality check"
        else:
            assert (
                "phone" not in high_quality
            ), f"{description}: Phone '{phone}' should fail quality check"

    # Test name validation edge cases
    name_test_cases = [
        # (name, should_pass, description)
        ("Starbucks", True, "Normal merchant name"),
        ("A&W", True, "Short but valid name"),
        ("ABC", True, "Exactly 3 characters"),
        ("AB", False, "Only 2 characters"),
        ("X", False, "Single character"),
        ("", False, "Empty string"),
        ("   ", False, "Only whitespace"),
        ("!!!", True, "3 punctuation marks (meets length requirement)"),
        ("The Coffee Shop", True, "Multi-word name"),
    ]

    for name, should_pass, description in name_test_cases:
        m = ReceiptMetadata(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=1,
            place_id="test",
            merchant_name=name,
            address="123 Main St",
            phone_number="555-1234",
            matched_fields=["name"],
            validated_by="TEXT_SEARCH",
            timestamp=datetime.now(),
            reasoning="testing name validation",
        )

        high_quality = m._get_high_quality_matched_fields()
        if should_pass:
            assert (
                "name" in high_quality
            ), f"{description}: Name '{name}' should pass quality check"
        else:
            assert (
                "name" not in high_quality
            ), f"{description}: Name '{name}' should fail quality check"

    # Test combination of fields
    m = ReceiptMetadata(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        receipt_id=1,
        place_id="test",
        merchant_name="AB",  # Too short - should fail
        address="123 Main Street",  # Good address
        phone_number="12345",  # Too few digits - should fail
        matched_fields=["name", "address", "phone"],
        validated_by="NEARBY_LOOKUP",
        timestamp=datetime.now(),
        reasoning="testing combined validation",
    )

    high_quality = m._get_high_quality_matched_fields()
    assert "name" not in high_quality
    assert "address" in high_quality
    assert "phone" not in high_quality
    assert len(high_quality) == 1

    # Test unknown field type (future-proofing)
    m = ReceiptMetadata(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        receipt_id=1,
        place_id="test",
        merchant_name="Test Store",
        address="123 Main St",
        phone_number="555-1234",
        matched_fields=["name", "unknown_field"],
        validated_by="NEARBY_LOOKUP",
        timestamp=datetime.now(),
        reasoning="testing unknown field",
    )

    high_quality = m._get_high_quality_matched_fields()
    assert "name" in high_quality
    assert "unknown_field" in high_quality  # Unknown fields pass through


@pytest.mark.unit
def test_address_edge_cases():
    """Test specific edge cases for address validation."""
    edge_cases = [
        # Single descriptive token cases
        ("Downtown", True, "Single descriptive location"),
        ("Mall", True, "Single word location >= 3 chars"),
        ("PO", False, "Too short abbreviation alone"),
        # Number-only addresses (should fail without street name)
        ("123", False, "Just house number"),
        ("456-789", False, "Range without street"),
        # Addresses with numbers but need more info
        (
            "123 A",
            True,
            "Number + single letter (passes due to number + token)",
        ),
        ("123 Main", True, "Number + street name"),
        # Complex formats
        ("Bldg A, Suite 200", True, "Building and suite"),
        ("Floor 3, Tower B", True, "Floor and tower"),
        ("P.O. Box 123", True, "PO Box format"),
    ]

    for address, should_pass, description in edge_cases:
        m = ReceiptMetadata(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=1,
            place_id="test",
            merchant_name="Test Store",
            address=address,
            phone_number="555-1234",
            matched_fields=["address"],
            validated_by="ADDRESS_LOOKUP",
            timestamp=datetime.now(),
            reasoning="testing address edge case",
        )

        high_quality = m._get_high_quality_matched_fields()
        if should_pass:
            assert (
                "address" in high_quality
            ), f"{description}: Address '{address}' should pass"
        else:
            assert (
                "address" not in high_quality
            ), f"{description}: Address '{address}' should fail"
