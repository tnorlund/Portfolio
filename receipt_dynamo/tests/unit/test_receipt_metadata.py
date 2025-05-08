import pytest
from datetime import datetime
from receipt_dynamo.entities.receipt_metadata import (
    ReceiptMetadata,
    itemToReceiptMetadata,
)
from receipt_dynamo.constants import MerchantValidationStatus


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
        match_confidence=0.9,
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
    assert pytest.approx(m.match_confidence) == 0.9
    assert m.matched_fields == ["name", "address"]
    assert m.validated_by == "NEARBY_LOOKUP"
    assert m.timestamp == datetime(2025, 1, 1, 12, 0, 0)
    assert m.reasoning == "Matches known Starbucks location"
    assert m.validation_status == MerchantValidationStatus.MATCHED


@pytest.mark.unit
@pytest.mark.parametrize(
    "confidence, expected_status",
    [
        (0.9, MerchantValidationStatus.MATCHED),
        (0.75, MerchantValidationStatus.UNSURE),
        (0.4, MerchantValidationStatus.NO_MATCH),
    ],
)
def test_validation_status_buckets(confidence, expected_status):
    # Choose the minimum number of matched fields required for each confidence bucket
    if confidence >= 0.80:
        matched_fields = ["name", "address"]  # ≥2 fields  →  MATCHED
    elif confidence >= 0.50:
        matched_fields = ["name"]  # ≥1 field  →  UNSURE
    else:
        matched_fields = []  # 0 fields  →  NO_MATCH
    m = ReceiptMetadata(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        receipt_id=1,
        place_id="id",
        merchant_name="Name",
        merchant_category="Category",
        address="Address",
        phone_number="Phone",
        match_confidence=confidence,
        matched_fields=matched_fields,
        validated_by="NEARBY_LOOKUP",
        timestamp=datetime.now(),
        reasoning="testing",
    )
    assert m.validation_status == expected_status


@pytest.mark.unit
def test_key_and_gsi_keys(example_receipt_metadata):
    m = example_receipt_metadata
    pk = m.key()
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
    restored = itemToReceiptMetadata(item)
    # Compare essential fields
    assert restored.image_id == m.image_id
    assert restored.receipt_id == m.receipt_id
    assert restored.place_id == m.place_id
    assert restored.merchant_name == m.merchant_name
    assert restored.merchant_category == m.merchant_category
    assert restored.address == m.address
    assert restored.phone_number == m.phone_number
    assert pytest.approx(restored.match_confidence) == m.match_confidence
    assert restored.matched_fields == m.matched_fields
    assert restored.validated_by == m.validated_by
    assert restored.timestamp == m.timestamp
    assert restored.reasoning == m.reasoning


@pytest.mark.unit
@pytest.mark.parametrize(
    "field,value,error",
    [
        ("receipt_id", "a", "receipt id must be an integer"),
        ("receipt_id", 0, "receipt id must be positive"),
        ("place_id", 123, "place id must be a string"),
        ("merchant_name", 456, "merchant name must be a string"),
        ("merchant_category", 789, "merchant category must be a string"),
        ("address", None, "address must be a string"),
        ("phone_number", 12345, "phone number must be a string"),
        ("match_confidence", "high", "match confidence must be a float"),
        ("match_confidence", -0.1, "match confidence must be between 0 and 1"),
        ("matched_fields", "notalist", "matched fields must be a list"),
        ("matched_fields", [1, 2], "matched fields must be a list of strings"),
        ("matched_fields", ["dup", "dup"], "matched fields must be unique"),
        (
            "validated_by",
            123,
            "validated_by must be a string or ValidationMethod enum",
        ),
        ("timestamp", "2025-01-01", "timestamp must be a datetime"),
        ("reasoning", 456, "reasoning must be a string"),
    ],
)
def test_invalid_field_validation(field, value, error):
    kwargs = dict(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        receipt_id=1,
        place_id="id",
        merchant_name="Name",
        merchant_category="Cat",
        address="Addr",
        phone_number="Phone",
        match_confidence=0.5,
        matched_fields=[],
        validated_by="NEARBY_LOOKUP",
        timestamp=datetime.now(),
        reasoning="Reason",
    )
    kwargs[field] = value
    with pytest.raises(ValueError, match=error):
        ReceiptMetadata(**kwargs)


@pytest.mark.unit
def test_item_to_receipt_metadata_missing_keys():
    item = {"PK": {"S": "IMAGE#id"}}
    with pytest.raises(ValueError, match="missing keys"):
        itemToReceiptMetadata(item)


@pytest.mark.unit
def test_item_to_receipt_metadata_parse_error(example_receipt_metadata):
    item = example_receipt_metadata.to_item()
    item["SK"]["S"] = "BADFORMAT"
    with pytest.raises(ValueError, match="Error parsing receipt metadata"):
        itemToReceiptMetadata(item)
