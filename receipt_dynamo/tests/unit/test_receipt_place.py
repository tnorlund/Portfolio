# pylint: disable=redefined-outer-name,protected-access
from datetime import datetime, timezone

import pytest

from receipt_dynamo.constants import (
    MerchantValidationStatus,
    ValidationMethod,
)
from receipt_dynamo.data.shared_exceptions import EntityValidationError
from receipt_dynamo.entities.receipt_place import ReceiptPlace


@pytest.fixture
def example_receipt_place():
    """Valid ReceiptPlace for testing."""
    return ReceiptPlace(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        receipt_id=101,
        place_id="ChIJrTLr-GyuEmsRBfy61i59si0",
        merchant_name="Starbucks Coffee",
        merchant_category="cafe",
        merchant_types=["cafe", "restaurant"],
        formatted_address="1234 Main St, Seattle, WA 98101, USA",
        short_address="Seattle, WA",
        latitude=47.6062,
        longitude=-122.3321,
        viewport_ne_lat=47.6075,
        viewport_ne_lng=-122.3310,
        viewport_sw_lat=47.6049,
        viewport_sw_lng=-122.3332,
        phone_number="(206) 555-0123",
        phone_intl="+1 206-555-0123",
        website="https://www.starbucks.com",
        maps_url="https://www.google.com/maps/search/?api=1&query=47.6062,-122.3321",
        business_status="OPERATIONAL",
        open_now=True,
        hours_summary=["Mon: 5:00 AM-9:00 PM", "Tue: 5:00 AM-9:00 PM"],
        hours_data={"periods": [{"open": {"day": 1}, "close": {"day": 1}}]},
        photo_references=["photo1", "photo2"],
        matched_fields=["name", "address", "phone"],
        validated_by=ValidationMethod.INFERENCE.value,
        validation_status=MerchantValidationStatus.MATCHED.value,
        confidence=0.85,
        reasoning="Matched via nearby location lookup with address confirmation",
        timestamp=datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
        places_api_version="v1",
    )


# ===== Basic Construction Tests =====


@pytest.mark.unit
def test_basic_construction_and_attributes(example_receipt_place):
    """Test basic ReceiptPlace construction and attribute assignment."""
    rp = example_receipt_place
    assert rp.image_id == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    assert rp.receipt_id == 101
    assert rp.place_id == "ChIJrTLr-GyuEmsRBfy61i59si0"
    assert rp.merchant_name == "Starbucks Coffee"
    assert rp.latitude == 47.6062
    assert rp.longitude == -122.3321
    assert rp.confidence == 0.85
    assert rp.validation_status == MerchantValidationStatus.MATCHED.value


@pytest.mark.unit
def test_auto_geohash_calculation(example_receipt_place):
    """Test that geohash is auto-calculated from coordinates."""
    rp = example_receipt_place
    assert rp.geohash != ""
    assert len(rp.geohash) == 6  # Default precision
    assert rp.geohash.startswith("c")  # Seattle area starts with 'c'


@pytest.mark.unit
def test_list_field_uniqueness():
    """Test that list fields are deduplicated."""
    rp = ReceiptPlace(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        receipt_id=1,
        place_id="place_id",
        merchant_name="Name",
        merchant_types=["cafe", "restaurant", "cafe"],  # Duplicate
        matched_fields=["name", "address", "name"],  # Duplicate
        hours_summary=["Mon: 9-5", "Mon: 9-5"],  # Duplicate
        photo_references=["photo1", "photo2", "photo1"],  # Duplicate
        latitude=47.6,
        longitude=-122.3,
        validation_status=MerchantValidationStatus.MATCHED.value,
        timestamp=datetime.now(timezone.utc),
    )
    assert rp.merchant_types == ["cafe", "restaurant"]
    assert rp.matched_fields == ["name", "address"]
    assert rp.hours_summary == ["Mon: 9-5"]
    assert rp.photo_references == ["photo1", "photo2"]


# ===== Coordinate Validation Tests =====


@pytest.mark.unit
@pytest.mark.parametrize(
    "latitude,should_succeed",
    [
        (0.0, True),
        (45.0, True),
        (-45.0, True),
        (90.0, True),  # North Pole
        (-90.0, True),  # South Pole
        (90.1, False),
        (-90.1, False),
        (91.0, False),
        (-91.0, False),
    ],
)
def test_latitude_validation(latitude, should_succeed):
    """Test latitude validation (-90 to 90)."""
    if should_succeed:
        rp = ReceiptPlace(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=1,
            place_id="place",
            merchant_name="Name",
            latitude=latitude,
            longitude=0.0,
            validation_status=MerchantValidationStatus.MATCHED.value,
            timestamp=datetime.now(timezone.utc),
        )
        assert rp.latitude == latitude
    else:
        with pytest.raises(ValueError, match="latitude out of range"):
            ReceiptPlace(
                image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
                receipt_id=1,
                place_id="place",
                merchant_name="Name",
                latitude=latitude,
                longitude=0.0,
                validation_status=MerchantValidationStatus.MATCHED.value,
                timestamp=datetime.now(timezone.utc),
            )


@pytest.mark.unit
@pytest.mark.parametrize(
    "longitude,should_succeed",
    [
        (0.0, True),
        (90.0, True),
        (-90.0, True),
        (180.0, True),  # Date line
        (-180.0, True),  # Date line
        (180.1, False),
        (-180.1, False),
        (181.0, False),
        (-181.0, False),
    ],
)
def test_longitude_validation(longitude, should_succeed):
    """Test longitude validation (-180 to 180)."""
    if should_succeed:
        rp = ReceiptPlace(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=1,
            place_id="place",
            merchant_name="Name",
            latitude=0.0,
            longitude=longitude,
            validation_status=MerchantValidationStatus.MATCHED.value,
            timestamp=datetime.now(timezone.utc),
        )
        assert rp.longitude == longitude
    else:
        with pytest.raises(ValueError, match="longitude out of range"):
            ReceiptPlace(
                image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
                receipt_id=1,
                place_id="place",
                merchant_name="Name",
                latitude=0.0,
                longitude=longitude,
                validation_status=MerchantValidationStatus.MATCHED.value,
                timestamp=datetime.now(timezone.utc),
            )


@pytest.mark.unit
def test_viewport_coordinate_validation():
    """Test viewport coordinate validation."""
    # Valid viewport
    rp = ReceiptPlace(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        receipt_id=1,
        place_id="place",
        merchant_name="Name",
        latitude=47.6,
        longitude=-122.3,
        viewport_ne_lat=47.7,
        viewport_ne_lng=-122.2,
        viewport_sw_lat=47.5,
        viewport_sw_lng=-122.4,
        validation_status=MerchantValidationStatus.MATCHED.value,
        timestamp=datetime.now(timezone.utc),
    )
    assert rp.viewport_ne_lat == 47.7

    # Invalid viewport latitude
    with pytest.raises(ValueError, match="viewport_ne_lat out of range"):
        ReceiptPlace(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=1,
            place_id="place",
            merchant_name="Name",
            latitude=47.6,
            longitude=-122.3,
            viewport_ne_lat=91.0,  # Invalid
            validation_status=MerchantValidationStatus.MATCHED.value,
            timestamp=datetime.now(timezone.utc),
        )


# ===== Confidence Validation Tests =====


@pytest.mark.unit
@pytest.mark.parametrize(
    "confidence,should_succeed",
    [
        (0.0, True),
        (0.5, True),
        (1.0, True),
        (0.9999, True),
        (-0.1, False),
        (1.1, False),
        (-1.0, False),
    ],
)
def test_confidence_validation(confidence, should_succeed):
    """Test confidence validation (0.0 to 1.0)."""
    if should_succeed:
        rp = ReceiptPlace(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=1,
            place_id="place",
            merchant_name="Name",
            confidence=confidence,
            validation_status=MerchantValidationStatus.MATCHED.value,
            timestamp=datetime.now(timezone.utc),
        )
        assert rp.confidence == confidence
    else:
        with pytest.raises(ValueError, match="confidence must be between"):
            ReceiptPlace(
                image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
                receipt_id=1,
                place_id="place",
                merchant_name="Name",
                confidence=confidence,
                validation_status=MerchantValidationStatus.MATCHED.value,
                timestamp=datetime.now(timezone.utc),
            )


@pytest.mark.unit
def test_confidence_precision():
    """Test confidence formatting in GSI3 key."""
    rp = ReceiptPlace(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        receipt_id=1,
        place_id="place",
        merchant_name="Name",
        confidence=0.856789,  # More than 4 decimals
        validation_status=MerchantValidationStatus.MATCHED.value,
        timestamp=datetime.now(timezone.utc),
    )
    gsi3 = rp.gsi3_key
    # Should be formatted to 4 decimal places
    assert "CONFIDENCE#0.8568" in gsi3["GSI3SK"]["S"]


# ===== Geohash Validation Tests =====


@pytest.mark.unit
def test_geohash_not_set_without_coordinates():
    """Test that geohash is empty when coordinates are not provided."""
    rp = ReceiptPlace(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        receipt_id=1,
        place_id="place",
        merchant_name="Name",
        validation_status=MerchantValidationStatus.MATCHED.value,
        timestamp=datetime.now(timezone.utc),
    )
    assert rp.geohash == ""


@pytest.mark.unit
def test_geohash_set_with_coordinates():
    """Test that geohash is auto-calculated with coordinates."""
    rp = ReceiptPlace(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        receipt_id=1,
        place_id="place",
        merchant_name="Name",
        latitude=40.7128,  # NYC
        longitude=-74.0060,
        validation_status=MerchantValidationStatus.MATCHED.value,
        timestamp=datetime.now(timezone.utc),
    )
    assert len(rp.geohash) == 6
    assert rp.geohash.startswith("d")  # NYC area starts with 'd'


@pytest.mark.unit
def test_manual_geohash_not_overwritten():
    """Test that manually provided geohash is not overwritten."""
    rp = ReceiptPlace(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        receipt_id=1,
        place_id="place",
        merchant_name="Name",
        latitude=40.7128,
        longitude=-74.0060,
        geohash="custom",  # Manual geohash
        validation_status=MerchantValidationStatus.MATCHED.value,
        timestamp=datetime.now(timezone.utc),
    )
    assert rp.geohash == "custom"


# ===== Key Generation Tests =====


@pytest.mark.unit
def test_primary_key_generation(example_receipt_place):
    """Test DynamoDB primary key generation."""
    rp = example_receipt_place
    pk = rp.key
    assert pk["PK"]["S"] == f"IMAGE#{rp.image_id}"
    assert pk["SK"]["S"] == f"RECEIPT#{rp.receipt_id:05d}#PLACE"
    # Format check
    assert pk["SK"]["S"] == "RECEIPT#00101#PLACE"


@pytest.mark.unit
def test_gsi1_key_merchant_normalization(example_receipt_place):
    """Test GSI1 key with merchant name normalization."""
    rp = example_receipt_place
    gsi1 = rp.gsi1_key
    # Name should be uppercase and spaces replaced with underscores
    assert gsi1["GSI1PK"]["S"] == "MERCHANT#STARBUCKS_COFFEE"
    assert "IMAGE#" in gsi1["GSI1SK"]["S"]
    assert "RECEIPT#" in gsi1["GSI1SK"]["S"]


@pytest.mark.unit
def test_gsi1_key_special_character_handling():
    """Test GSI1 key handles special characters correctly."""
    rp = ReceiptPlace(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        receipt_id=1,
        place_id="place",
        merchant_name="McDonald's & Co.",
        validation_status=MerchantValidationStatus.MATCHED.value,
        timestamp=datetime.now(timezone.utc),
    )
    gsi1 = rp.gsi1_key
    # Special characters should be replaced with underscores and collapsed
    assert gsi1["GSI1PK"]["S"] == "MERCHANT#MCDONALD_S_CO"


@pytest.mark.unit
def test_gsi1_key_single_character_merchant():
    """Test GSI1 key handles single alphanumeric character names."""
    rp = ReceiptPlace(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        receipt_id=1,
        place_id="place",
        merchant_name="A",
        validation_status=MerchantValidationStatus.MATCHED.value,
        timestamp=datetime.now(timezone.utc),
    )
    gsi1 = rp.gsi1_key
    assert gsi1["GSI1PK"]["S"] == "MERCHANT#A"


@pytest.mark.unit
def test_merchant_name_all_special_characters_fails():
    """Test that merchant names with only special characters are rejected."""
    with pytest.raises(
        ValueError, match="contains no alphanumeric characters"
    ):
        ReceiptPlace(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=1,
            place_id="place",
            merchant_name="@#$%!",
            validation_status=MerchantValidationStatus.MATCHED.value,
            timestamp=datetime.now(timezone.utc),
        )


@pytest.mark.unit
def test_merchant_name_whitespace_only_fails():
    """Test that whitespace-only merchant names are rejected."""
    with pytest.raises(
        ValueError, match="contains no alphanumeric characters"
    ):
        ReceiptPlace(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=1,
            place_id="place",
            merchant_name="   ",
            validation_status=MerchantValidationStatus.MATCHED.value,
            timestamp=datetime.now(timezone.utc),
        )


@pytest.mark.unit
def test_merchant_name_empty_fails():
    """Test that empty merchant names are rejected."""
    with pytest.raises(ValueError, match="merchant_name cannot be empty"):
        ReceiptPlace(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=1,
            place_id="place",
            merchant_name="",
            validation_status=MerchantValidationStatus.MATCHED.value,
            timestamp=datetime.now(timezone.utc),
        )


@pytest.mark.unit
def test_gsi2_key_place_id(example_receipt_place):
    """Test GSI2 key generation by place_id."""
    rp = example_receipt_place
    gsi2 = rp.gsi2_key
    assert gsi2["GSI2PK"]["S"] == f"PLACE#{rp.place_id}"
    assert "IMAGE#" in gsi2["GSI2SK"]["S"]
    assert "RECEIPT#" in gsi2["GSI2SK"]["S"]


@pytest.mark.unit
def test_gsi3_key_confidence_and_status(example_receipt_place):
    """Test GSI3 key includes confidence in sort key."""
    rp = example_receipt_place
    gsi3 = rp.gsi3_key
    # GSI3PK should be PLACE_VALIDATION
    assert gsi3["GSI3PK"]["S"] == "PLACE_VALIDATION"
    # GSI3SK should have format: CONFIDENCE#{confidence:.4f}#STATUS#{status}#IMAGE#{image_id}
    sk = gsi3["GSI3SK"]["S"]
    assert sk.startswith("CONFIDENCE#0.8500")
    assert f"STATUS#{rp.validation_status}" in sk
    assert f"IMAGE#{rp.image_id}" in sk


@pytest.mark.unit
def test_gsi3_key_confidence_formatting():
    """Test GSI3 confidence is formatted to 4 decimal places."""
    test_cases = [
        (0.1, "CONFIDENCE#0.1000"),
        (0.123456, "CONFIDENCE#0.1235"),  # Rounded
        (1.0, "CONFIDENCE#1.0000"),
        (0.0, "CONFIDENCE#0.0000"),
    ]
    for confidence, expected_prefix in test_cases:
        rp = ReceiptPlace(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=1,
            place_id="place",
            merchant_name="Name",
            confidence=confidence,
            validation_status=MerchantValidationStatus.MATCHED.value,
            timestamp=datetime.now(timezone.utc),
        )
        gsi3 = rp.gsi3_key
        assert gsi3["GSI3SK"]["S"].startswith(expected_prefix)


@pytest.mark.unit
def test_gsi4_key_geohash(example_receipt_place):
    """Test GSI4 key generation with geohash."""
    rp = example_receipt_place
    gsi4 = rp.gsi4_key
    assert gsi4["GSI4PK"]["S"] == f"GEOHASH#{rp.geohash}"
    assert gsi4["GSI4SK"]["S"] == f"PLACE#{rp.place_id}"


@pytest.mark.unit
def test_gsi4_key_empty_when_no_geohash():
    """Test GSI4 key is empty when geohash is not available."""
    rp = ReceiptPlace(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        receipt_id=1,
        place_id="place",
        merchant_name="Name",
        validation_status=MerchantValidationStatus.MATCHED.value,
        timestamp=datetime.now(timezone.utc),
    )
    gsi4 = rp.gsi4_key
    assert gsi4 == {}


# ===== Dataclass Conversion Tests =====


@pytest.mark.unit
def test_dataclass_round_trip(example_receipt_place):
    """Test entity can be reconstructed from attributes via dataclass."""
    rp = example_receipt_place

    # Recreate from attributes (simulating deserialization from DB)
    rp2 = ReceiptPlace(
        image_id=rp.image_id,
        receipt_id=rp.receipt_id,
        place_id=rp.place_id,
        merchant_name=rp.merchant_name,
        merchant_category=rp.merchant_category,
        merchant_types=rp.merchant_types,
        formatted_address=rp.formatted_address,
        short_address=rp.short_address,
        latitude=rp.latitude,
        longitude=rp.longitude,
        viewport_ne_lat=rp.viewport_ne_lat,
        viewport_ne_lng=rp.viewport_ne_lng,
        viewport_sw_lat=rp.viewport_sw_lat,
        viewport_sw_lng=rp.viewport_sw_lng,
        phone_number=rp.phone_number,
        phone_intl=rp.phone_intl,
        website=rp.website,
        maps_url=rp.maps_url,
        business_status=rp.business_status,
        open_now=rp.open_now,
        hours_summary=rp.hours_summary,
        hours_data=rp.hours_data,
        photo_references=rp.photo_references,
        matched_fields=rp.matched_fields,
        validated_by=rp.validated_by,
        validation_status=rp.validation_status,
        confidence=rp.confidence,
        reasoning=rp.reasoning,
        timestamp=rp.timestamp,
        places_api_version=rp.places_api_version,
    )

    # Verify key attributes match
    assert rp2.image_id == rp.image_id
    assert rp2.receipt_id == rp.receipt_id
    assert rp2.place_id == rp.place_id
    assert rp2.merchant_name == rp.merchant_name
    assert rp2.latitude == rp.latitude
    assert rp2.longitude == rp.longitude
    assert rp2.confidence == rp.confidence


# ===== Field Validation Tests =====


@pytest.mark.unit
@pytest.mark.parametrize(
    "receipt_id,should_succeed",
    [
        (1, True),
        (100, True),
        (999, True),
        (0, False),  # Must be positive
        (-1, False),
    ],
)
def test_receipt_id_validation(receipt_id, should_succeed):
    """Test receipt_id validation (must be positive integer)."""
    if should_succeed:
        rp = ReceiptPlace(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=receipt_id,
            place_id="place",
            merchant_name="Name",
            validation_status=MerchantValidationStatus.MATCHED.value,
            timestamp=datetime.now(timezone.utc),
        )
        assert rp.receipt_id == receipt_id
    else:
        with pytest.raises(ValueError):
            ReceiptPlace(
                image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
                receipt_id=receipt_id,
                place_id="place",
                merchant_name="Name",
                validation_status=MerchantValidationStatus.MATCHED.value,
                timestamp=datetime.now(timezone.utc),
            )


@pytest.mark.unit
def test_image_id_uuid_validation():
    """Test that image_id must be a valid UUID."""
    with pytest.raises(ValueError):
        ReceiptPlace(
            image_id="not-a-uuid",  # Invalid
            receipt_id=1,
            place_id="place",
            merchant_name="Name",
            validation_status=MerchantValidationStatus.MATCHED.value,
            timestamp=datetime.now(timezone.utc),
        )


@pytest.mark.unit
def test_validation_status_enum():
    """Test validation_status can be set directly or via constant."""
    # Direct string
    rp1 = ReceiptPlace(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        receipt_id=1,
        place_id="place",
        merchant_name="Name",
        validation_status="MATCHED",
        timestamp=datetime.now(timezone.utc),
    )
    assert rp1.validation_status == "MATCHED"

    # Via constant
    rp2 = ReceiptPlace(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        receipt_id=1,
        place_id="place",
        merchant_name="Name",
        validation_status=MerchantValidationStatus.MATCHED.value,
        timestamp=datetime.now(timezone.utc),
    )
    assert rp2.validation_status == MerchantValidationStatus.MATCHED.value


@pytest.mark.unit
def test_validated_by_enum_normalization():
    """Test that validated_by is normalized to enum value."""
    rp = ReceiptPlace(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        receipt_id=1,
        place_id="place",
        merchant_name="Name",
        validated_by="INFERENCE",
        validation_status=MerchantValidationStatus.MATCHED.value,
        timestamp=datetime.now(timezone.utc),
    )
    assert rp.validated_by == ValidationMethod.INFERENCE.value
