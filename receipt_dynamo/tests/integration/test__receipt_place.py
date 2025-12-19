"""
Integration tests for ReceiptPlace operations in DynamoDB.

This module tests the ReceiptPlace-related methods of DynamoClient, including
add, get, update, delete, list operations, and GSI queries. It follows patterns
established in the ReceiptMetadata integration tests.

These tests require a running DynamoDB instance (usually via LocalStack or moto).
"""

from datetime import datetime, timezone
from typing import Any, Dict, List
from uuid import uuid4

import pytest
from botocore.exceptions import ClientError

from receipt_dynamo import DynamoClient, ReceiptPlace
from receipt_dynamo.constants import MerchantValidationStatus, ValidationMethod
from receipt_dynamo.data.shared_exceptions import (
    DynamoDBError,
    DynamoDBServerError,
    DynamoDBThroughputError,
    EntityAlreadyExistsError,
    EntityNotFoundError,
    EntityValidationError,
    OperationError,
)


# =============================================================================
# TEST DATA AND FIXTURES
# =============================================================================

CORRECT_RECEIPT_PLACE_PARAMS: Dict[str, Any] = {
    "image_id": "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
    "receipt_id": 1,
    "place_id": "ChIJrTLr-GyuEmsRBfy61i59si0",
    "merchant_name": "Starbucks Coffee",
    "merchant_category": "cafe",
    "merchant_types": ["cafe", "restaurant"],
    "formatted_address": "1234 Main St, Seattle, WA 98101, USA",
    "short_address": "Seattle, WA",
    "latitude": 47.6062,
    "longitude": -122.3321,
    "viewport_ne_lat": 47.6075,
    "viewport_ne_lng": -122.3310,
    "viewport_sw_lat": 47.6049,
    "viewport_sw_lng": -122.3332,
    "phone_number": "(206) 555-0123",
    "phone_intl": "+1 206-555-0123",
    "website": "https://www.starbucks.com",
    "business_status": "OPERATIONAL",
    "hours_summary": ["Mon: 5:00 AM–9:00 PM", "Tue: 5:00 AM–9:00 PM"],
    "photo_references": ["photo1", "photo2"],
    "matched_fields": ["name", "address", "phone"],
    "validated_by": ValidationMethod.INFERENCE.value,
    "validation_status": MerchantValidationStatus.MATCHED.value,
    "confidence": 0.85,
    "reasoning": "Matched via nearby location lookup with address confirmation",
    "timestamp": datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
    "places_api_version": "v1",
}


@pytest.fixture(name="sample_receipt_place")
def _sample_receipt_place() -> ReceiptPlace:
    """Provides a valid ReceiptPlace for testing."""
    return ReceiptPlace(**CORRECT_RECEIPT_PLACE_PARAMS)


@pytest.fixture(name="another_receipt_place")
def _another_receipt_place() -> ReceiptPlace:
    """Provides a second valid ReceiptPlace for testing."""
    return ReceiptPlace(
        image_id=str(uuid4()),
        receipt_id=2,
        place_id="ChIJN1t_tDeuEmsRUsoyG83frY4",
        merchant_name="McDonald's Restaurant",
        merchant_category="restaurant",
        merchant_types=["restaurant", "food"],
        formatted_address="5678 Oak Ave, Seattle, WA 98102, USA",
        short_address="Seattle, WA",
        latitude=47.6100,
        longitude=-122.3300,
        phone_number="(206) 555-9999",
        matched_fields=["name"],
        validated_by=ValidationMethod.INFERENCE.value,
        validation_status=MerchantValidationStatus.UNSURE.value,
        confidence=0.65,
        reasoning="Partial match on merchant name",
        timestamp=datetime(2025, 1, 1, 13, 0, 0, tzinfo=timezone.utc),
    )


@pytest.fixture(name="batch_receipt_places")
def _batch_receipt_places() -> List[ReceiptPlace]:
    """Provides a list of 30 receipt places for batch testing."""
    places = []
    base_time = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    for i in range(30):
        confidence = 0.5 + (i * 0.01)  # Range from 0.5 to 0.79
        places.append(
            ReceiptPlace(
                image_id=str(uuid4()),
                receipt_id=(i % 10) + 1,
                place_id=f"ChIJ{i:06d}",
                merchant_name=f"Store {chr(65 + (i % 26))}",
                merchant_category="restaurant" if i % 2 == 0 else "cafe",
                latitude=47.6 + (i * 0.001),
                longitude=-122.3 - (i * 0.001),
                matched_fields=["name", "address"] if i % 3 == 0 else ["name"],
                validated_by=ValidationMethod.INFERENCE.value,
                validation_status=(
                    MerchantValidationStatus.MATCHED.value
                    if i % 3 == 0
                    else MerchantValidationStatus.UNSURE.value
                ),
                confidence=confidence,
                timestamp=base_time,
                reasoning=f"Test place {i}",
            )
        )

    return places


# =============================================================================
# BASIC CRUD TESTS
# =============================================================================


@pytest.mark.integration
def test_add_receipt_place_success(sample_receipt_place, dynamodb_table: str) -> None:
    """Tests adding a single ReceiptPlace successfully."""
    client = DynamoClient(dynamodb_table)
    # Should not raise
    client.add_receipt_place(sample_receipt_place)


@pytest.mark.integration
def test_add_receipt_place_duplicate_raises(
    sample_receipt_place, dynamodb_table: str
) -> None:
    """Tests that adding duplicate ReceiptPlace raises error."""
    client = DynamoClient(dynamodb_table)
    client.add_receipt_place(sample_receipt_place)

    # Adding same receipt again should raise
    with pytest.raises(EntityAlreadyExistsError):
        client.add_receipt_place(sample_receipt_place)


@pytest.mark.integration
def test_get_receipt_place_success(
    sample_receipt_place, dynamodb_table: str
) -> None:
    """Tests retrieving a single ReceiptPlace by indices."""
    client = DynamoClient(dynamodb_table)
    client.add_receipt_place(sample_receipt_place)

    retrieved = client.get_receipt_place(
        sample_receipt_place.image_id, sample_receipt_place.receipt_id
    )
    assert retrieved.merchant_name == sample_receipt_place.merchant_name
    assert retrieved.latitude == sample_receipt_place.latitude
    assert retrieved.confidence == sample_receipt_place.confidence


@pytest.mark.integration
def test_get_receipt_place_not_found(dynamodb_table: str) -> None:
    """Tests that getting non-existent ReceiptPlace raises error."""
    client = DynamoClient(dynamodb_table)

    with pytest.raises(EntityNotFoundError):
        client.get_receipt_place(str(uuid4()), 999)


@pytest.mark.integration
def test_update_receipt_place_success(
    sample_receipt_place, dynamodb_table: str
) -> None:
    """Tests updating an existing ReceiptPlace."""
    client = DynamoClient(dynamodb_table)
    client.add_receipt_place(sample_receipt_place)

    # Update confidence
    sample_receipt_place.confidence = 0.95
    sample_receipt_place.validation_status = MerchantValidationStatus.MATCHED.value
    client.update_receipt_place(sample_receipt_place)

    retrieved = client.get_receipt_place(
        sample_receipt_place.image_id, sample_receipt_place.receipt_id
    )
    assert retrieved.confidence == 0.95


@pytest.mark.integration
def test_delete_receipt_place_success(
    sample_receipt_place, dynamodb_table: str
) -> None:
    """Tests deleting a ReceiptPlace."""
    client = DynamoClient(dynamodb_table)
    client.add_receipt_place(sample_receipt_place)

    client.delete_receipt_place(sample_receipt_place)

    with pytest.raises(EntityNotFoundError):
        client.get_receipt_place(
            sample_receipt_place.image_id, sample_receipt_place.receipt_id
        )


@pytest.mark.integration
def test_add_receipt_places_batch(batch_receipt_places, dynamodb_table: str) -> None:
    """Tests adding multiple ReceiptPlaces in batch."""
    client = DynamoClient(dynamodb_table)
    client.add_receipt_places(batch_receipt_places)

    # Verify all were added by retrieving a few
    retrieved = client.get_receipt_place(
        batch_receipt_places[0].image_id, batch_receipt_places[0].receipt_id
    )
    assert retrieved.merchant_name == batch_receipt_places[0].merchant_name


@pytest.mark.integration
def test_list_receipt_places(batch_receipt_places, dynamodb_table: str) -> None:
    """Tests listing ReceiptPlaces with pagination."""
    client = DynamoClient(dynamodb_table)
    client.add_receipt_places(batch_receipt_places)

    places, next_key = client.list_receipt_places(limit=10)
    assert len(places) == 10
    assert next_key is not None  # More results available

    # Get next page
    places2, final_key = client.list_receipt_places(limit=10, last_evaluated_key=next_key)
    assert len(places2) == 10


# =============================================================================
# GSI QUERY TESTS
# =============================================================================


@pytest.mark.integration
def test_get_receipt_places_by_merchant(batch_receipt_places, dynamodb_table: str) -> None:
    """Tests querying ReceiptPlaces by merchant name (GSI1)."""
    client = DynamoClient(dynamodb_table)
    client.add_receipt_places(batch_receipt_places)

    # Query by first merchant name
    merchant_name = batch_receipt_places[0].merchant_name
    places, _ = client.get_receipt_places_by_merchant(merchant_name)

    # Should find at least one
    assert len(places) > 0
    assert all(p.merchant_name == merchant_name for p in places)


@pytest.mark.integration
def test_list_receipt_places_with_place_id(
    batch_receipt_places, dynamodb_table: str
) -> None:
    """Tests querying ReceiptPlaces by place_id (GSI2)."""
    client = DynamoClient(dynamodb_table)
    client.add_receipt_places(batch_receipt_places)

    # Query by place_id
    place_id = batch_receipt_places[0].place_id
    places, _ = client.list_receipt_places_with_place_id(place_id)

    assert len(places) > 0
    assert all(p.place_id == place_id for p in places)


@pytest.mark.integration
def test_get_receipt_places_by_status(batch_receipt_places, dynamodb_table: str) -> None:
    """Tests querying ReceiptPlaces by validation status (GSI3)."""
    client = DynamoClient(dynamodb_table)
    client.add_receipt_places(batch_receipt_places)

    places, _ = client.get_receipt_places_by_status(
        MerchantValidationStatus.MATCHED.value
    )

    assert len(places) > 0
    assert all(
        p.validation_status == MerchantValidationStatus.MATCHED.value for p in places
    )


@pytest.mark.integration
def test_get_receipt_places_by_confidence_above(
    batch_receipt_places, dynamodb_table: str
) -> None:
    """Tests querying ReceiptPlaces by confidence >= threshold."""
    client = DynamoClient(dynamodb_table)
    client.add_receipt_places(batch_receipt_places)

    # Query for high confidence (>= 0.7)
    places, _ = client.get_receipt_places_by_confidence(confidence=0.70, above=True)

    assert len(places) > 0
    assert all(p.confidence >= 0.70 for p in places)


@pytest.mark.integration
def test_get_receipt_places_by_confidence_below(
    batch_receipt_places, dynamodb_table: str
) -> None:
    """Tests querying ReceiptPlaces by confidence <= threshold."""
    client = DynamoClient(dynamodb_table)
    client.add_receipt_places(batch_receipt_places)

    # Query for low confidence (<= 0.6)
    places, _ = client.get_receipt_places_by_confidence(confidence=0.60, above=False)

    assert len(places) > 0
    assert all(p.confidence <= 0.60 for p in places)


@pytest.mark.integration
def test_get_receipt_places_by_confidence_boundary(
    dynamodb_table: str,
) -> None:
    """Tests confidence queries at exact boundary values."""
    client = DynamoClient(dynamodb_table)

    # Add places with specific confidence values
    confidences = [0.3, 0.5, 0.7, 0.85, 0.95]
    places = []
    for i, conf in enumerate(confidences):
        places.append(
            ReceiptPlace(
                image_id=str(uuid4()),
                receipt_id=i + 1,
                place_id=f"place{i}",
                merchant_name=f"Test {i}",
                confidence=conf,
                latitude=47.6 + (i * 0.01),
                longitude=-122.3 - (i * 0.01),
                matched_fields=["name"],
                validated_by=ValidationMethod.INFERENCE.value,
                validation_status=MerchantValidationStatus.MATCHED.value,
                timestamp=datetime.now(timezone.utc),
            )
        )
    client.add_receipt_places(places)

    # Test exact threshold
    above_threshold, _ = client.get_receipt_places_by_confidence(confidence=0.5, above=True)
    below_threshold, _ = client.get_receipt_places_by_confidence(
        confidence=0.5, above=False
    )

    # At confidence 0.5, "above" should include 0.5, 0.7, 0.85, 0.95 (4 items)
    assert len(above_threshold) >= 3  # At least the ones >= 0.5
    # "below" should include 0.3, 0.5 (2 items)
    assert len(below_threshold) >= 1  # At least some <= 0.5


@pytest.mark.integration
def test_get_receipt_places_by_confidence_with_pagination(
    batch_receipt_places, dynamodb_table: str
) -> None:
    """Tests pagination with confidence queries."""
    client = DynamoClient(dynamodb_table)
    client.add_receipt_places(batch_receipt_places)

    # Get high-confidence places with pagination
    places1, next_key = client.get_receipt_places_by_confidence(
        confidence=0.60, above=True, limit=5
    )

    assert len(places1) <= 5
    if next_key:
        # Get next page
        places2, _ = client.get_receipt_places_by_confidence(
            confidence=0.60, above=True, limit=5, last_evaluated_key=next_key
        )
        assert len(places2) <= 5
        # Make sure they're different
        place_ids_1 = {(p.image_id, p.receipt_id) for p in places1}
        place_ids_2 = {(p.image_id, p.receipt_id) for p in places2}
        assert len(place_ids_1 & place_ids_2) == 0  # No overlap


@pytest.mark.integration
def test_get_receipt_places_by_geohash(batch_receipt_places, dynamodb_table: str) -> None:
    """Tests spatial queries by geohash (GSI4)."""
    client = DynamoClient(dynamodb_table)
    client.add_receipt_places(batch_receipt_places)

    # Get geohash from first place
    geohash = batch_receipt_places[0].geohash
    if geohash:
        places, _ = client.get_receipt_places_by_geohash(geohash)
        assert len(places) > 0


# =============================================================================
# PARAMETER VALIDATION TESTS
# =============================================================================


@pytest.mark.integration
@pytest.mark.parametrize(
    "confidence,expected_error",
    [
        (None, "confidence cannot be None"),
        ("not-a-float", "confidence must be a float"),
        (-0.1, "confidence must be between"),
        (1.5, "confidence must be between"),
    ],
)
def test_confidence_query_validation(confidence, expected_error, dynamodb_table: str):
    """Tests confidence query parameter validation."""
    client = DynamoClient(dynamodb_table)

    with pytest.raises(EntityValidationError, match=expected_error):
        client.get_receipt_places_by_confidence(confidence=confidence)


@pytest.mark.integration
def test_get_receipt_places_by_confidence_invalid_above(dynamodb_table: str):
    """Tests that above parameter must be boolean."""
    client = DynamoClient(dynamodb_table)

    with pytest.raises(EntityValidationError, match="above must be a boolean"):
        client.get_receipt_places_by_confidence(confidence=0.5, above="yes")


@pytest.mark.integration
@pytest.mark.parametrize("invalid_limit", [-1, 0, "not-int"])
def test_get_receipt_places_by_confidence_invalid_limit(
    invalid_limit, dynamodb_table: str
):
    """Tests limit parameter validation."""
    client = DynamoClient(dynamodb_table)

    with pytest.raises(EntityValidationError):
        client.get_receipt_places_by_confidence(confidence=0.5, limit=invalid_limit)


# =============================================================================
# BATCH OPERATION TESTS
# =============================================================================


@pytest.mark.integration
def test_update_receipt_places_batch(batch_receipt_places, dynamodb_table: str) -> None:
    """Tests batch updating ReceiptPlaces."""
    client = DynamoClient(dynamodb_table)
    client.add_receipt_places(batch_receipt_places)

    # Update all to high confidence
    for place in batch_receipt_places:
        place.confidence = 0.99

    client.update_receipt_places(batch_receipt_places)

    # Verify first one was updated
    retrieved = client.get_receipt_place(
        batch_receipt_places[0].image_id, batch_receipt_places[0].receipt_id
    )
    assert retrieved.confidence == 0.99


@pytest.mark.integration
def test_delete_receipt_places_batch(batch_receipt_places, dynamodb_table: str) -> None:
    """Tests batch deleting ReceiptPlaces."""
    client = DynamoClient(dynamodb_table)
    client.add_receipt_places(batch_receipt_places[:5])

    client.delete_receipt_places(batch_receipt_places[:5])

    # Verify first one is deleted
    with pytest.raises(EntityNotFoundError):
        client.get_receipt_place(
            batch_receipt_places[0].image_id, batch_receipt_places[0].receipt_id
        )


@pytest.mark.integration
def test_get_receipt_places_by_indices(batch_receipt_places, dynamodb_table: str):
    """Tests batch getting ReceiptPlaces by indices."""
    client = DynamoClient(dynamodb_table)
    client.add_receipt_places(batch_receipt_places)

    # Get specific indices
    indices = [
        (batch_receipt_places[0].image_id, batch_receipt_places[0].receipt_id),
        (batch_receipt_places[1].image_id, batch_receipt_places[1].receipt_id),
    ]
    places = client.get_receipt_places_by_indices(indices)

    assert len(places) == 2
    assert places[0].merchant_name == batch_receipt_places[0].merchant_name


# =============================================================================
# ENTITY VALIDATION TESTS
# =============================================================================


@pytest.mark.integration
def test_add_invalid_receipt_place_type(dynamodb_table: str) -> None:
    """Tests that adding invalid entity type raises error."""
    from receipt_dynamo.data.shared_exceptions import OperationError
    client = DynamoClient(dynamodb_table)

    with pytest.raises(OperationError, match="must be an instance of ReceiptPlace"):
        client.add_receipt_place("not a receipt place")


@pytest.mark.integration
def test_add_invalid_receipt_place_list(dynamodb_table: str) -> None:
    """Tests that adding invalid list raises error."""
    from receipt_dynamo.data.shared_exceptions import OperationError
    client = DynamoClient(dynamodb_table)

    with pytest.raises(OperationError, match="must be a list"):
        client.add_receipt_places("not a list")


@pytest.mark.integration
def test_merchant_case_handling(dynamodb_table: str) -> None:
    """Tests that merchant name querying is case-insensitive."""
    client = DynamoClient(dynamodb_table)

    place = ReceiptPlace(
        image_id=str(uuid4()),
        receipt_id=1,
        place_id="place",
        merchant_name="Test Restaurant",
        matched_fields=["name"],
        validated_by=ValidationMethod.INFERENCE.value,
        validation_status=MerchantValidationStatus.MATCHED.value,
        timestamp=datetime.now(timezone.utc),
    )
    client.add_receipt_place(place)

    # Query with different cases
    places, _ = client.get_receipt_places_by_merchant("test restaurant")
    assert len(places) > 0

    places, _ = client.get_receipt_places_by_merchant("TEST RESTAURANT")
    assert len(places) > 0


# =============================================================================
# EDGE CASE TESTS
# =============================================================================


@pytest.mark.integration
def test_receipt_place_with_no_coordinates(dynamodb_table: str) -> None:
    """Tests ReceiptPlace without geographic coordinates."""
    client = DynamoClient(dynamodb_table)

    place = ReceiptPlace(
        image_id=str(uuid4()),
        receipt_id=1,
        place_id="place",
        merchant_name="No Coordinates",
        matched_fields=["name"],
        validated_by=ValidationMethod.INFERENCE.value,
        validation_status=MerchantValidationStatus.MATCHED.value,
        timestamp=datetime.now(timezone.utc),
    )
    client.add_receipt_place(place)

    retrieved = client.get_receipt_place(place.image_id, place.receipt_id)
    assert retrieved.latitude is None
    assert retrieved.longitude is None
    assert retrieved.geohash == ""


@pytest.mark.integration
def test_receipt_place_with_all_optional_fields(dynamodb_table: str) -> None:
    """Tests ReceiptPlace with all optional fields populated."""
    client = DynamoClient(dynamodb_table)

    place = ReceiptPlace(
        image_id=str(uuid4()),
        receipt_id=1,
        place_id="place",
        merchant_name="Full Place",
        merchant_category="cafe",
        merchant_types=["cafe", "restaurant"],
        formatted_address="123 Main St",
        short_address="Main St",
        latitude=47.6,
        longitude=-122.3,
        viewport_ne_lat=47.7,
        viewport_ne_lng=-122.2,
        viewport_sw_lat=47.5,
        viewport_sw_lng=-122.4,
        phone_number="(206) 555-0123",
        phone_intl="+1 206-555-0123",
        website="https://example.com",
        maps_url="https://maps.google.com",
        business_status="OPERATIONAL",
        open_now=True,
        hours_summary=["Mon: 9-5"],
        hours_data={"periods": []},
        photo_references=["photo1", "photo2"],
        matched_fields=["name", "address", "phone"],
        validated_by=ValidationMethod.INFERENCE.value,
        validation_status=MerchantValidationStatus.MATCHED.value,
        confidence=0.95,
        reasoning="Full test",
        timestamp=datetime.now(timezone.utc),
    )
    client.add_receipt_place(place)

    retrieved = client.get_receipt_place(place.image_id, place.receipt_id)
    assert retrieved.formatted_address == "123 Main St"
    assert retrieved.website == "https://example.com"
    assert retrieved.confidence == 0.95


@pytest.mark.integration
def test_confidence_precision_preserved(dynamodb_table: str) -> None:
    """Tests that confidence values with 4+ decimal places are preserved."""
    client = DynamoClient(dynamodb_table)

    place = ReceiptPlace(
        image_id=str(uuid4()),
        receipt_id=1,
        place_id="place",
        merchant_name="Precision Test",
        confidence=0.856789,  # More than 4 decimals
        matched_fields=["name"],
        validated_by=ValidationMethod.INFERENCE.value,
        validation_status=MerchantValidationStatus.MATCHED.value,
        timestamp=datetime.now(timezone.utc),
    )
    client.add_receipt_place(place)

    retrieved = client.get_receipt_place(place.image_id, place.receipt_id)
    # Should preserve original value
    assert retrieved.confidence == 0.856789
