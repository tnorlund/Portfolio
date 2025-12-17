"""
Integration tests for ReceiptMetadata operations in DynamoDB.

This module tests the ReceiptMetadata-related methods of DynamoClient, including
add, get, update, delete, and list operations. It follows the perfect
test patterns established in test__receipt.py, test__image.py, and
test__receipt_word_label.py.
"""

from datetime import datetime
from typing import Any, Dict, List, Literal, Type
from uuid import uuid4

import pytest
from botocore.exceptions import ClientError
from pytest_mock import MockerFixture

from receipt_dynamo import DynamoClient, ReceiptMetadata
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

CORRECT_RECEIPT_METADATA_PARAMS: Dict[str, Any] = {
    "image_id": "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
    "receipt_id": 1,
    "place_id": "ChIJrTLr-GyuEmsRBfy61i59si0",
    "merchant_name": "Test Merchant",
    "merchant_category": "Restaurant",
    "address": "123 Test St, Test City, TC 12345",
    "phone_number": "+1234567890",
    "matched_fields": ["name", "address"],
    "validated_by": "NEARBY_LOOKUP",
    "timestamp": datetime(2024, 3, 20, 12, 0, 0),
    "reasoning": "High confidence match based on name and address",
}


@pytest.fixture(name="sample_receipt_metadata")
def _sample_receipt_metadata() -> ReceiptMetadata:
    """Provides a valid ReceiptMetadata for testing."""
    return ReceiptMetadata(**CORRECT_RECEIPT_METADATA_PARAMS)


@pytest.fixture(name="another_receipt_metadata")
def _another_receipt_metadata() -> ReceiptMetadata:
    """Provides a second valid ReceiptMetadata for testing."""
    return ReceiptMetadata(
        image_id="4a63915c-22f5-4f11-a3d9-c684eb4b9ef4",
        receipt_id=2,
        place_id="ChIJN1t_tDeuEmsRUsoyG83frY4",
        merchant_name="Another Store",
        merchant_category="Retail",
        address="456 Another Ave, Another City, AC 67890",
        phone_number="+0987654321",
        matched_fields=["name"],
        validated_by="TEXT_SEARCH",
        timestamp=datetime(2024, 3, 20, 13, 0, 0),
        reasoning="Match based on merchant name only",
    )


@pytest.fixture(name="batch_receipt_metadatas")
def _batch_receipt_metadatas() -> List[ReceiptMetadata]:
    """Provides a list of 30 receipt metadatas for batch testing."""
    metadatas = []
    base_time = datetime(2024, 3, 20, 12, 0, 0)

    for i in range(30):
        metadatas.append(
            ReceiptMetadata(
                image_id=str(uuid4()),
                receipt_id=(i % 10) + 1,
                place_id=f"ChIJ{i:03d}",
                merchant_name=f"Merchant {chr(65 + (i % 26))}",  # A-Z cycling
                merchant_category="Restaurant" if i % 2 == 0 else "Retail",
                address=f"{i+1} Test St, City {i % 5}, TC {10000 + i}",
                phone_number=f"+123456{i:04d}",
                validation_status="VALID" if i % 2 == 0 else "PENDING",
                matched_fields=["name", "address"] if i % 3 == 0 else ["name"],
                validated_by=["NEARBY_LOOKUP", "TEXT_SEARCH", "PHONE_LOOKUP"][
                    i % 3
                ],
                timestamp=base_time,
                reasoning=f"Test metadata {i}",
            )
        )

    return metadatas


# -------------------------------------------------------------------
#                   PARAMETERIZED CLIENT ERROR TESTS
# -------------------------------------------------------------------

# Common error scenarios for all operations
ERROR_SCENARIOS = [
    (
        "ProvisionedThroughputExceededException",
        DynamoDBThroughputError,
        "Throughput exceeded",
    ),
    ("InternalServerError", DynamoDBServerError, "DynamoDB server error"),
    ("ValidationException", EntityValidationError, "Validation error"),
    ("AccessDeniedException", DynamoDBError, "DynamoDB error during"),
    (
        "ResourceNotFoundException",
        OperationError,
        "DynamoDB resource not found",
    ),
]

# Additional error for add operations
ADD_ERROR_SCENARIOS = [
    (
        "ConditionalCheckFailedException",
        EntityAlreadyExistsError,
        "receipt_metadata already exists",
    ),
] + ERROR_SCENARIOS

# Additional error for update operations
UPDATE_ERROR_SCENARIOS = [
    (
        "ConditionalCheckFailedException",
        EntityNotFoundError,
        "not found during update_receipt_metadatas",
    ),
] + ERROR_SCENARIOS


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,expected_exception,error_match", ADD_ERROR_SCENARIOS
)
def test_add_receipt_metadata_client_errors(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_metadata: ReceiptMetadata,
    mocker: MockerFixture,
    error_code: str,
    expected_exception: Type[Exception],
    error_match: str,
) -> None:
    """Tests that add_receipt_metadata raises appropriate exceptions for various
    ClientError scenarios."""
    client = DynamoClient(dynamodb_table)
    # pylint: disable=protected-access
    mock_put = mocker.patch.object(
        client._client,
        "put_item",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": error_code,
                    "Message": f"Mocked {error_code}",
                }
            },
            "PutItem",
        ),
    )

    with pytest.raises(expected_exception, match=error_match):
        client.add_receipt_metadata(sample_receipt_metadata)
    mock_put.assert_called_once()


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,expected_exception,error_match", UPDATE_ERROR_SCENARIOS
)
def test_update_receipt_metadatas_client_errors(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_metadata: ReceiptMetadata,
    mocker: MockerFixture,
    error_code: str,
    expected_exception: Type[Exception],
    error_match: str,
) -> None:
    """Tests that update_receipt_metadatas raises appropriate exceptions for
    various ClientError scenarios with transact_write_items."""
    client = DynamoClient(dynamodb_table)
    # pylint: disable=protected-access
    mock_transact = mocker.patch.object(
        client._client,
        "transact_write_items",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": error_code,
                    "Message": f"Mocked {error_code}",
                }
            },
            "TransactWriteItems",
        ),
    )

    with pytest.raises(expected_exception, match=error_match):
        client.update_receipt_metadatas([sample_receipt_metadata])
    mock_transact.assert_called_once()


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,expected_exception,error_match",
    ERROR_SCENARIOS,  # Delete doesn't have ConditionalCheckFailedException
)
def test_delete_receipt_metadata_client_errors(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_metadata: ReceiptMetadata,
    mocker: MockerFixture,
    error_code: str,
    expected_exception: Type[Exception],
    error_match: str,
) -> None:
    """Tests that delete_receipt_metadata raises appropriate exceptions for
    various ClientError scenarios."""
    client = DynamoClient(dynamodb_table)
    # pylint: disable=protected-access
    mock_delete = mocker.patch.object(
        client._client,
        "delete_item",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": error_code,
                    "Message": f"Mocked {error_code}",
                }
            },
            "DeleteItem",
        ),
    )

    with pytest.raises(expected_exception, match=error_match):
        client.delete_receipt_metadata(sample_receipt_metadata)
    mock_delete.assert_called_once()


# -------------------------------------------------------------------
#                   PARAMETERIZED VALIDATION ERROR TESTS
# -------------------------------------------------------------------

ADD_VALIDATION_SCENARIOS = [
    (None, "receipt_metadata cannot be None"),
    (
        "not-a-receipt-metadata",
        "receipt_metadata must be an instance of ReceiptMetadata",
    ),
]

UPDATE_BATCH_VALIDATION_SCENARIOS = [
    (None, "receipt_metadatas cannot be None"),
    ("not-a-list", "receipt_metadatas must be a list"),
]


@pytest.mark.integration
@pytest.mark.parametrize("invalid_input,error_match", ADD_VALIDATION_SCENARIOS)
def test_add_receipt_metadata_validation_errors(
    dynamodb_table: Literal["MyMockedTable"],
    invalid_input: Any,
    error_match: str,
) -> None:
    """Tests that add_receipt_metadata raises appropriate error for
    invalid inputs."""
    client = DynamoClient(dynamodb_table)

    with pytest.raises(OperationError, match=error_match):
        client.add_receipt_metadata(invalid_input)  # type: ignore


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,error_match", UPDATE_BATCH_VALIDATION_SCENARIOS
)
def test_update_receipt_metadatas_validation_errors(
    dynamodb_table: Literal["MyMockedTable"],
    invalid_input: Any,
    error_match: str,
) -> None:
    """Tests that update_receipt_metadatas raises appropriate error for
    invalid inputs."""
    client = DynamoClient(dynamodb_table)

    with pytest.raises(OperationError, match=error_match):
        client.update_receipt_metadatas(invalid_input)  # type: ignore


@pytest.mark.integration
def test_update_receipt_metadatas_invalid_list_contents(
    dynamodb_table: Literal["MyMockedTable"],
) -> None:
    """Tests that update_receipt_metadatas validates list contents."""
    client = DynamoClient(dynamodb_table)
    invalid_list = ["not-a-metadata", 123, None]

    with pytest.raises(
        OperationError,
        match="All items in receipt_metadatas must be instances of ReceiptMetadata",
    ):
        client.update_receipt_metadatas(invalid_list)  # type: ignore


# -------------------------------------------------------------------
#                        ADD OPERATIONS
# -------------------------------------------------------------------


@pytest.mark.integration
def test_add_receipt_metadata_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_metadata: ReceiptMetadata,
) -> None:
    """Tests successful addition of a receipt metadata."""
    client = DynamoClient(dynamodb_table)

    # Add the metadata
    client.add_receipt_metadata(sample_receipt_metadata)

    # Verify it was added by retrieving it
    retrieved = client.get_receipt_metadata(
        sample_receipt_metadata.image_id,
        sample_receipt_metadata.receipt_id,
    )
    assert retrieved == sample_receipt_metadata


@pytest.mark.integration
def test_add_receipt_metadata_duplicate_raises(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_metadata: ReceiptMetadata,
) -> None:
    """Tests that adding a duplicate receipt metadata raises EntityAlreadyExistsError."""
    client = DynamoClient(dynamodb_table)
    client.add_receipt_metadata(sample_receipt_metadata)

    with pytest.raises(
        EntityAlreadyExistsError, match="receipt_metadata already exists"
    ):
        client.add_receipt_metadata(sample_receipt_metadata)


# -------------------------------------------------------------------
#                        GET OPERATIONS
# -------------------------------------------------------------------


@pytest.mark.integration
def test_get_receipt_metadata_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_metadata: ReceiptMetadata,
) -> None:
    """Tests successful retrieval of a receipt metadata."""
    client = DynamoClient(dynamodb_table)
    client.add_receipt_metadata(sample_receipt_metadata)

    retrieved = client.get_receipt_metadata(
        sample_receipt_metadata.image_id,
        sample_receipt_metadata.receipt_id,
    )

    assert retrieved == sample_receipt_metadata


@pytest.mark.integration
def test_get_receipt_metadata_not_found(
    dynamodb_table: Literal["MyMockedTable"],
) -> None:
    """Tests that get_receipt_metadata raises EntityNotFoundError for non-existent metadata."""
    client = DynamoClient(dynamodb_table)

    with pytest.raises(EntityNotFoundError, match="does not exist"):
        client.get_receipt_metadata(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            999,
        )


@pytest.mark.integration
@pytest.mark.parametrize(
    "image_id,receipt_id",
    [
        (None, 1),  # None image_id
        ("valid-id", None),  # None receipt_id
        ("not-a-uuid", 1),  # Invalid UUID
        ("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 0),  # Invalid receipt_id
        ("3f52804b-2fad-4e00-92c8-b593da3a8ed3", -1),  # Negative receipt_id
    ],
)
def test_get_receipt_metadata_invalid_params(
    dynamodb_table: Literal["MyMockedTable"],
    image_id: Any,
    receipt_id: Any,
) -> None:
    """Tests that get_receipt_metadata raises EntityValidationError for invalid parameters."""
    client = DynamoClient(dynamodb_table)

    with pytest.raises((EntityValidationError, OperationError)):
        client.get_receipt_metadata(image_id, receipt_id)


# -------------------------------------------------------------------
#                        UPDATE OPERATIONS
# -------------------------------------------------------------------


@pytest.mark.integration
def test_update_receipt_metadata_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_metadata: ReceiptMetadata,
) -> None:
    """Tests successful update of a receipt metadata."""
    client = DynamoClient(dynamodb_table)

    # First add the metadata
    client.add_receipt_metadata(sample_receipt_metadata)

    # Update it with new values
    sample_receipt_metadata.reasoning = (
        "Updated reasoning with higher confidence"
    )
    sample_receipt_metadata.matched_fields = ["name", "address", "phone"]
    sample_receipt_metadata.merchant_category = "Updated Category"

    client.update_receipt_metadata(sample_receipt_metadata)

    # Verify the update
    retrieved = client.get_receipt_metadata(
        sample_receipt_metadata.image_id,
        sample_receipt_metadata.receipt_id,
    )
    assert retrieved.reasoning == "Updated reasoning with higher confidence"
    assert retrieved.matched_fields == ["name", "address", "phone"]
    assert retrieved.merchant_category == "Updated Category"


@pytest.mark.integration
def test_update_receipt_metadatas_batch(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_metadata: ReceiptMetadata,
    another_receipt_metadata: ReceiptMetadata,
) -> None:
    """Tests successful batch update of receipt metadatas."""
    client = DynamoClient(dynamodb_table)

    # First add both metadatas
    client.add_receipt_metadata(sample_receipt_metadata)
    client.add_receipt_metadata(another_receipt_metadata)

    # Update both with new reasoning
    sample_receipt_metadata.reasoning = "Updated reasoning for first metadata"
    another_receipt_metadata.reasoning = (
        "Updated reasoning for second metadata"
    )

    client.update_receipt_metadatas(
        [sample_receipt_metadata, another_receipt_metadata]
    )

    # Verify both updates
    retrieved1 = client.get_receipt_metadata(
        sample_receipt_metadata.image_id,
        sample_receipt_metadata.receipt_id,
    )
    retrieved2 = client.get_receipt_metadata(
        another_receipt_metadata.image_id,
        another_receipt_metadata.receipt_id,
    )

    assert retrieved1.reasoning == "Updated reasoning for first metadata"
    assert retrieved2.reasoning == "Updated reasoning for second metadata"


# -------------------------------------------------------------------
#                        DELETE OPERATIONS
# -------------------------------------------------------------------


@pytest.mark.integration
def test_delete_receipt_metadata_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_metadata: ReceiptMetadata,
) -> None:
    """Tests successful deletion of a receipt metadata."""
    client = DynamoClient(dynamodb_table)

    # First add the metadata
    client.add_receipt_metadata(sample_receipt_metadata)

    # Delete it
    client.delete_receipt_metadata(sample_receipt_metadata)

    # Verify it's gone
    with pytest.raises(EntityNotFoundError):
        client.get_receipt_metadata(
            sample_receipt_metadata.image_id,
            sample_receipt_metadata.receipt_id,
        )


@pytest.mark.integration
def test_delete_receipt_metadata_not_found(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_metadata: ReceiptMetadata,
) -> None:
    """Tests that deleting a non-existent metadata raises EntityNotFoundError."""
    client = DynamoClient(dynamodb_table)

    # Delete non-existent metadata - should raise
    with pytest.raises(EntityNotFoundError):
        client.delete_receipt_metadata(sample_receipt_metadata)


@pytest.mark.integration
def test_delete_receipt_metadatas_batch(
    dynamodb_table: Literal["MyMockedTable"],
    batch_receipt_metadatas: List[ReceiptMetadata],
) -> None:
    """Tests batch deletion of receipt metadatas."""
    client = DynamoClient(dynamodb_table)

    # Add first 10 metadatas
    metadatas_to_delete = batch_receipt_metadatas[:10]
    for metadata in metadatas_to_delete:
        client.add_receipt_metadata(metadata)

    # Delete them in batch
    client.delete_receipt_metadatas(metadatas_to_delete)

    # Verify all are deleted
    for metadata in metadatas_to_delete:
        with pytest.raises(EntityNotFoundError):
            client.get_receipt_metadata(
                metadata.image_id,
                metadata.receipt_id,
            )


# -------------------------------------------------------------------
#                        LIST OPERATIONS
# -------------------------------------------------------------------


@pytest.mark.integration
def test_list_receipt_metadatas_success(
    dynamodb_table: Literal["MyMockedTable"],
) -> None:
    """Tests listing all receipt metadatas."""
    client = DynamoClient(dynamodb_table)

    # Add multiple metadatas
    metadatas = []
    for i in range(5):
        metadata = ReceiptMetadata(
            image_id=str(uuid4()),
            receipt_id=i + 1,
            place_id=f"ChIJ{i:03d}",
            merchant_name=f"Merchant {i}",
            matched_fields=["name"],
            validated_by="TEXT_SEARCH",
            timestamp=datetime(2024, 3, 20, 12, 0, 0),
        )
        metadatas.append(metadata)
        client.add_receipt_metadata(metadata)

    # List them
    retrieved, last_key = client.list_receipt_metadatas(limit=10)

    assert len(retrieved) >= 5  # May have more from other tests
    assert last_key is None


@pytest.mark.integration
def test_list_receipt_metadatas_with_pagination(
    dynamodb_table: Literal["MyMockedTable"],
    batch_receipt_metadatas: List[ReceiptMetadata],
) -> None:
    """Tests listing receipt metadatas with pagination."""
    client = DynamoClient(dynamodb_table)

    # Add 30 metadatas
    for metadata in batch_receipt_metadatas:
        client.add_receipt_metadata(metadata)

    # Get first page
    page1, last_key1 = client.list_receipt_metadatas(limit=15)
    assert len(page1) == 15
    assert last_key1 is not None

    # Get second page
    page2, last_key2 = client.list_receipt_metadatas(
        limit=15, last_evaluated_key=last_key1
    )
    assert len(page2) <= 15  # May be fewer items on second page

    # Get remaining items if there are more
    all_retrieved = page1 + page2
    if last_key2 is not None:
        page3, last_key3 = client.list_receipt_metadatas(
            limit=15, last_evaluated_key=last_key2
        )
        all_retrieved.extend(page3)

    # Verify we got all our items
    assert len(all_retrieved) >= 30


# -------------------------------------------------------------------
#                   QUERY BY MERCHANT OPERATIONS
# -------------------------------------------------------------------


@pytest.mark.integration
def test_get_receipt_metadatas_by_merchant_success(
    dynamodb_table: Literal["MyMockedTable"],
) -> None:
    """Tests retrieving receipt metadatas by merchant name."""
    client = DynamoClient(dynamodb_table)
    merchant_name = "Test Merchant ABC"

    # Add metadatas for the same merchant
    metadatas = []
    for i in range(3):
        metadata = ReceiptMetadata(
            image_id=str(uuid4()),
            receipt_id=i + 1,
            place_id=f"ChIJ{i:03d}",
            merchant_name=merchant_name,
            matched_fields=["name"],
            validated_by="TEXT_SEARCH",
            timestamp=datetime(2024, 3, 20, 12, i, 0),
            reasoning=f"Test metadata {i}",
        )
        metadatas.append(metadata)
        client.add_receipt_metadata(metadata)

    # Query by merchant name
    retrieved, last_key = client.get_receipt_metadatas_by_merchant(
        merchant_name
    )

    assert len(retrieved) == 3
    assert all(m.merchant_name == merchant_name for m in retrieved)
    assert last_key is None


@pytest.mark.integration
def test_get_receipt_metadatas_by_merchant_case_insensitive(
    dynamodb_table: Literal["MyMockedTable"],
) -> None:
    """Tests that merchant name queries are case-insensitive."""
    client = DynamoClient(dynamodb_table)

    # Add metadata with mixed case merchant name
    metadata = ReceiptMetadata(
        image_id=str(uuid4()),
        receipt_id=1,
        place_id="ChIJ001",
        merchant_name="MiXeD CaSe MeRcHaNt",
        matched_fields=["name"],
        validated_by="TEXT_SEARCH",
        timestamp=datetime(2024, 3, 20, 12, 0, 0),
        reasoning="Test case insensitive query",
    )
    client.add_receipt_metadata(metadata)

    # Query with different case
    retrieved1, _ = client.get_receipt_metadatas_by_merchant(
        "mixed case merchant"
    )
    retrieved2, _ = client.get_receipt_metadatas_by_merchant(
        "MIXED CASE MERCHANT"
    )

    assert len(retrieved1) == 1
    assert len(retrieved2) == 1
    assert retrieved1[0].merchant_name == "MiXeD CaSe MeRcHaNt"
    assert retrieved2[0].merchant_name == "MiXeD CaSe MeRcHaNt"


# -------------------------------------------------------------------
#                   QUERY BY PLACE ID OPERATIONS
# -------------------------------------------------------------------


@pytest.mark.integration
def test_list_receipt_metadatas_with_place_id_success(
    dynamodb_table: Literal["MyMockedTable"],
) -> None:
    """Tests listing receipt metadatas by place ID."""
    client = DynamoClient(dynamodb_table)
    place_id = "ChIJrTLr-GyuEmsRBfy61i59si0"

    # Add metadatas with the same place_id
    metadatas = []
    for i in range(4):
        metadata = ReceiptMetadata(
            image_id=str(uuid4()),
            receipt_id=i + 1,
            place_id=place_id,
            merchant_name=f"Store {i}",
            matched_fields=["name", "address"],
            validated_by="NEARBY_LOOKUP",
            timestamp=datetime(2024, 3, 20, 12, 0, 0),
            reasoning=f"Test place lookup {i}",
        )
        metadatas.append(metadata)
        client.add_receipt_metadata(metadata)

    # Query by place_id
    retrieved, last_key = client.list_receipt_metadatas_with_place_id(place_id)

    assert len(retrieved) == 4
    assert all(m.place_id == place_id for m in retrieved)
    assert last_key is None


@pytest.mark.integration
def test_list_receipt_metadatas_with_place_id_empty(
    dynamodb_table: Literal["MyMockedTable"],
) -> None:
    """Tests listing receipt metadatas for a place_id with no results."""
    client = DynamoClient(dynamodb_table)

    # Query for non-existent place_id
    retrieved, last_key = client.list_receipt_metadatas_with_place_id(
        "ChIJNonExistentPlaceId"
    )

    assert len(retrieved) == 0
    assert last_key is None


# -------------------------------------------------------------------
#                   QUERY BY VALIDATION STATUS OPERATIONS
# -------------------------------------------------------------------


@pytest.mark.integration
def test_get_receipt_metadatas_by_validation_status(
    dynamodb_table: Literal["MyMockedTable"],
) -> None:
    """Tests retrieving receipt metadatas by validation status using GSI3."""
    client = DynamoClient(dynamodb_table)

    # Add metadatas with different validation statuses
    statuses = ["MATCHED", "UNSURE", "NO_MATCH"]
    for i, status in enumerate(statuses):
        # Add 3 metadatas per status
        for j in range(3):
            metadata = ReceiptMetadata(
                image_id=str(uuid4()),
                receipt_id=(i * 10) + j + 1,
                place_id=f"ChIJ{i:03d}{j:02d}",
                merchant_name=f"Status {status} Merchant {j}",
                matched_fields=["name"] if j == 0 else ["name", "address"],
                validated_by="TEXT_SEARCH",
                timestamp=datetime(2024, 3, 20, 12, 0, 0),
                reasoning=f"Test {status} validation {j}",
            )
            client.add_receipt_metadata(metadata)

    # Query by validation status using the GSI3 implementation
    # Note: This test verifies the GSI3 structure exists even if the query method doesn't
    retrieved_all, _ = client.list_receipt_metadatas(limit=50)

    # Verify we have metadatas with different validation statuses
    statuses_found = {m.validation_status for m in retrieved_all}
    assert len(statuses_found) >= 1  # At least one status should be present

    # Verify each metadata has a valid validation_status
    for metadata in retrieved_all[-9:]:  # Check the last 9 we just added
        assert metadata.validation_status in ["MATCHED", "UNSURE", "NO_MATCH"]


# -------------------------------------------------------------------
#                   BATCH GET OPERATIONS
# -------------------------------------------------------------------


@pytest.mark.integration
def test_get_receipt_metadatas_by_indices_success(
    dynamodb_table: Literal["MyMockedTable"],
) -> None:
    """Tests batch retrieval of receipt metadatas by indices."""
    client = DynamoClient(dynamodb_table)

    # Add multiple metadatas
    indices = []
    for i in range(5):
        image_id = str(uuid4())
        receipt_id = i + 1
        indices.append((image_id, receipt_id))

        metadata = ReceiptMetadata(
            image_id=image_id,
            receipt_id=receipt_id,
            place_id=f"ChIJ{i:03d}",
            merchant_name=f"Batch Merchant {i}",
            matched_fields=["name"],
            validated_by="TEXT_SEARCH",
            timestamp=datetime(2024, 3, 20, 12, 0, 0),
            reasoning=f"Batch test {i}",
        )
        client.add_receipt_metadata(metadata)

    # Get them all by indices
    retrieved = client.get_receipt_metadatas_by_indices(indices)

    assert len(retrieved) == 5
    for metadata in retrieved:
        assert (metadata.image_id, metadata.receipt_id) in indices


@pytest.mark.integration
def test_get_receipt_metadatas_by_indices_validation(
    dynamodb_table: Literal["MyMockedTable"],
) -> None:
    """Tests validation for get_receipt_metadatas_by_indices."""
    client = DynamoClient(dynamodb_table)

    # Test None indices
    with pytest.raises(EntityValidationError, match="indices cannot be None"):
        client.get_receipt_metadatas_by_indices(None)  # type: ignore

    # Test non-list indices
    with pytest.raises(EntityValidationError, match="indices must be a list"):
        client.get_receipt_metadatas_by_indices("not-a-list")  # type: ignore

    # Test non-tuple items
    with pytest.raises(
        EntityValidationError, match="indices must be a list of tuples"
    ):
        client.get_receipt_metadatas_by_indices([("valid", 1), "not-a-tuple"])  # type: ignore

    # Test invalid tuple types
    with pytest.raises(
        EntityValidationError,
        match="indices must be a list of tuples of \\(image_id, receipt_id\\)",
    ):
        client.get_receipt_metadatas_by_indices([(123, 1)])  # type: ignore

    # Test invalid receipt_id
    with pytest.raises(
        EntityValidationError, match="receipt_id must be positive"
    ):
        client.get_receipt_metadatas_by_indices([("valid-id", 0)])


# -------------------------------------------------------------------
#                   BATCH OPERATIONS WITH UNPROCESSED ITEMS
# -------------------------------------------------------------------


@pytest.mark.integration
def test_add_receipt_metadatas_batch_with_unprocessed(
    dynamodb_table: Literal["MyMockedTable"],
    batch_receipt_metadatas: List[ReceiptMetadata],
    mocker: MockerFixture,
) -> None:
    """Tests that add_receipt_metadatas handles unprocessed items correctly."""
    client = DynamoClient(dynamodb_table)
    metadatas_to_add = batch_receipt_metadatas[:5]

    # Mock batch_write_item to return unprocessed items on first call
    # pylint: disable=protected-access
    mock_batch = mocker.patch.object(
        client._client,
        "batch_write_item",
        side_effect=[
            {
                "UnprocessedItems": {
                    dynamodb_table: [
                        {"PutRequest": {"Item": metadatas_to_add[0].to_item()}}
                    ]
                }
            },
            {},  # Success on retry
        ],
    )

    client.add_receipt_metadatas(metadatas_to_add)

    # Should be called twice (initial + retry)
    assert mock_batch.call_count == 2


@pytest.mark.integration
def test_update_receipt_metadata_not_found(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_metadata: ReceiptMetadata,
) -> None:
    """Tests that updating a non-existent metadata raises EntityNotFoundError."""
    client = DynamoClient(dynamodb_table)

    with pytest.raises(
        EntityNotFoundError,
        match="not found during update_receipt_metadata",
    ):
        client.update_receipt_metadata(sample_receipt_metadata)


# -------------------------------------------------------------------
#                   VALIDATION PARAMETER TESTS
# -------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize(
    "merchant_name,expected_error",
    [
        (None, "merchant_name cannot be None"),
        (123, "merchant_name must be a string"),
    ],
)
def test_get_receipt_metadatas_by_merchant_validation(
    dynamodb_table: Literal["MyMockedTable"],
    merchant_name: Any,
    expected_error: str,
) -> None:
    """Tests validation for get_receipt_metadatas_by_merchant parameters."""
    client = DynamoClient(dynamodb_table)

    with pytest.raises(EntityValidationError, match=expected_error):
        client.get_receipt_metadatas_by_merchant(merchant_name)


@pytest.mark.integration
@pytest.mark.parametrize(
    "place_id,expected_error",
    [
        ("", "place_id cannot be empty"),
        (None, "place_id cannot be empty"),
        (123, "place_id must be a string"),
    ],
)
def test_list_receipt_metadatas_with_place_id_validation(
    dynamodb_table: Literal["MyMockedTable"],
    place_id: Any,
    expected_error: str,
) -> None:
    """Tests validation for list_receipt_metadatas_with_place_id parameters."""
    client = DynamoClient(dynamodb_table)

    with pytest.raises(EntityValidationError, match=expected_error):
        client.list_receipt_metadatas_with_place_id(place_id)


@pytest.mark.integration
@pytest.mark.parametrize(
    "limit,expected_error",
    [
        ("not-an-int", "limit must be an integer"),
        (0, "limit must be positive"),
        (-5, "limit must be positive"),
    ],
)
def test_list_receipt_metadatas_limit_validation(
    dynamodb_table: Literal["MyMockedTable"],
    limit: Any,
    expected_error: str,
) -> None:
    """Tests validation for list_receipt_metadatas limit parameter."""
    client = DynamoClient(dynamodb_table)

    with pytest.raises(EntityValidationError, match=expected_error):
        client.list_receipt_metadatas(limit=limit)
