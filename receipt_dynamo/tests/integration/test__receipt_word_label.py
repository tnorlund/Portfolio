"""
Integration tests for ReceiptWordLabel operations in DynamoDB.

This module tests the ReceiptWordLabel-related methods of DynamoClient, including
add, get, update, delete, and list operations. It follows the perfect
test patterns established in test__receipt.py and test__image.py.
"""

from datetime import datetime
from typing import Any, Dict, List, Literal, Type
from uuid import uuid4

import pytest
from botocore.exceptions import ClientError
from pytest_mock import MockerFixture

from receipt_dynamo import DynamoClient, ReceiptWordLabel
from receipt_dynamo.constants import ValidationStatus
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

CORRECT_RECEIPT_WORD_LABEL_PARAMS: Dict[str, Any] = {
    "receipt_id": 1,
    "image_id": "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
    "line_id": 10,
    "word_id": 5,
    "label": "ITEM",
    "reasoning": "This word appears to be an item description",
    "timestamp_added": "2024-03-20T12:00:00+00:00",
    "validation_status": ValidationStatus.VALID,
}


@pytest.fixture(name="sample_receipt_word_label")
def _sample_receipt_word_label() -> ReceiptWordLabel:
    """Provides a valid ReceiptWordLabel for testing."""
    return ReceiptWordLabel(**CORRECT_RECEIPT_WORD_LABEL_PARAMS)


@pytest.fixture(name="another_receipt_word_label")
def _another_receipt_word_label() -> ReceiptWordLabel:
    """Provides a second valid ReceiptWordLabel for testing."""
    return ReceiptWordLabel(
        receipt_id=2,
        image_id="4a63915c-22f5-4f11-a3d9-c684eb4b9ef4",
        line_id=20,
        word_id=10,
        label="PRICE",
        reasoning="This word appears to be a price",
        timestamp_added="2024-03-20T13:00:00+00:00",
        validation_status=ValidationStatus.VALID,
    )


@pytest.fixture(name="batch_receipt_word_labels")
def _batch_receipt_word_labels() -> List[ReceiptWordLabel]:
    """Provides a list of 100 receipt word labels for batch testing."""
    labels = []
    base_time = datetime.fromisoformat("2024-03-20T12:00:00+00:00")

    for i in range(100):
        labels.append(
            ReceiptWordLabel(
                receipt_id=i + 1,
                image_id=str(uuid4()),
                line_id=(i % 10) + 1,
                word_id=(i % 5) + 1,
                label="ITEM" if i % 2 == 0 else "PRICE",
                reasoning=f"Test label {i}",
                timestamp_added=base_time.isoformat(),
                validation_status=ValidationStatus.VALID,
            )
        )

    return labels


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
        "receipt_word_label already exists",
    ),
] + ERROR_SCENARIOS

# Additional error for update operations
UPDATE_ERROR_SCENARIOS = [
    (
        "ConditionalCheckFailedException",
        EntityNotFoundError,
        "Cannot update receiptwordlabels: one or more receiptwordlabels not found",
    ),
] + ERROR_SCENARIOS


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,expected_exception,error_match", ADD_ERROR_SCENARIOS
)
def test_add_receipt_word_label_client_errors(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_word_label: ReceiptWordLabel,
    mocker: MockerFixture,
    error_code: str,
    expected_exception: Type[Exception],
    error_match: str,
) -> None:
    """Tests that add_receipt_word_label raises appropriate exceptions for various
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
        client.add_receipt_word_label(sample_receipt_word_label)
    mock_put.assert_called_once()


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,expected_exception,error_match", UPDATE_ERROR_SCENARIOS
)
def test_update_receipt_word_labels_client_errors(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_word_label: ReceiptWordLabel,
    mocker: MockerFixture,
    error_code: str,
    expected_exception: Type[Exception],
    error_match: str,
) -> None:
    """Tests that update_receipt_word_labels raises appropriate exceptions for
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
        client.update_receipt_word_labels([sample_receipt_word_label])
    mock_transact.assert_called_once()


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,expected_exception,error_match",
    ERROR_SCENARIOS,  # Delete doesn't have ConditionalCheckFailedException
)
def test_delete_receipt_word_label_client_errors(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_word_label: ReceiptWordLabel,
    mocker: MockerFixture,
    error_code: str,
    expected_exception: Type[Exception],
    error_match: str,
) -> None:
    """Tests that delete_receipt_word_label raises appropriate exceptions for
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
        client.delete_receipt_word_label(sample_receipt_word_label)
    mock_delete.assert_called_once()


# -------------------------------------------------------------------
#                   PARAMETERIZED VALIDATION ERROR TESTS
# -------------------------------------------------------------------

ADD_VALIDATION_SCENARIOS = [
    (None, "receipt_word_label cannot be None"),
    (
        "not-a-receipt-word-label",
        "receipt_word_label must be an instance of ReceiptWordLabel",
    ),
]

UPDATE_BATCH_VALIDATION_SCENARIOS = [
    (None, "receipt_word_labels cannot be None"),
    ("not-a-list", "receipt_word_labels must be a list"),
]


@pytest.mark.integration
@pytest.mark.parametrize("invalid_input,error_match", ADD_VALIDATION_SCENARIOS)
def test_add_receipt_word_label_validation_errors(
    dynamodb_table: Literal["MyMockedTable"],
    invalid_input: Any,
    error_match: str,
) -> None:
    """Tests that add_receipt_word_label raises appropriate error for
    invalid inputs."""
    client = DynamoClient(dynamodb_table)

    with pytest.raises(OperationError, match=error_match):
        client.add_receipt_word_label(invalid_input)  # type: ignore


@pytest.mark.integration
@pytest.mark.parametrize("invalid_input,error_match", UPDATE_BATCH_VALIDATION_SCENARIOS)
def test_update_receipt_word_labels_validation_errors(
    dynamodb_table: Literal["MyMockedTable"],
    invalid_input: Any,
    error_match: str,
) -> None:
    """Tests that update_receipt_word_labels raises appropriate error for
    invalid inputs."""
    client = DynamoClient(dynamodb_table)

    with pytest.raises(OperationError, match=error_match):
        client.update_receipt_word_labels(invalid_input)  # type: ignore


@pytest.mark.integration
def test_update_receipt_word_labels_invalid_list_contents(
    dynamodb_table: Literal["MyMockedTable"],
) -> None:
    """Tests that update_receipt_word_labels validates list contents."""
    client = DynamoClient(dynamodb_table)
    invalid_list = ["not-a-label", 123, None]

    with pytest.raises(
        OperationError,
        match="All items in receipt_word_labels must be instances of ReceiptWordLabel",
    ):
        client.update_receipt_word_labels(invalid_list)  # type: ignore


# -------------------------------------------------------------------
#                        ADD OPERATIONS
# -------------------------------------------------------------------


@pytest.mark.integration
def test_add_receipt_word_label_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_word_label: ReceiptWordLabel,
) -> None:
    """Tests successful addition of a receipt word label."""
    client = DynamoClient(dynamodb_table)

    # Add the label
    client.add_receipt_word_label(sample_receipt_word_label)

    # Verify it was added by retrieving it
    retrieved = client.get_receipt_word_label(
        sample_receipt_word_label.image_id,
        sample_receipt_word_label.receipt_id,
        sample_receipt_word_label.line_id,
        sample_receipt_word_label.word_id,
        sample_receipt_word_label.label,
    )
    assert retrieved == sample_receipt_word_label


@pytest.mark.integration
def test_add_receipt_word_label_duplicate_raises(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_word_label: ReceiptWordLabel,
) -> None:
    """Tests that adding a duplicate receipt word label raises EntityAlreadyExistsError."""
    client = DynamoClient(dynamodb_table)
    client.add_receipt_word_label(sample_receipt_word_label)

    with pytest.raises(
        EntityAlreadyExistsError, match="receipt_word_label already exists"
    ):
        client.add_receipt_word_label(sample_receipt_word_label)


# -------------------------------------------------------------------
#                        GET OPERATIONS
# -------------------------------------------------------------------


@pytest.mark.integration
def test_get_receipt_word_label_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_word_label: ReceiptWordLabel,
) -> None:
    """Tests successful retrieval of a receipt word label."""
    client = DynamoClient(dynamodb_table)
    client.add_receipt_word_label(sample_receipt_word_label)

    retrieved = client.get_receipt_word_label(
        sample_receipt_word_label.image_id,
        sample_receipt_word_label.receipt_id,
        sample_receipt_word_label.line_id,
        sample_receipt_word_label.word_id,
        sample_receipt_word_label.label,
    )

    assert retrieved == sample_receipt_word_label


@pytest.mark.integration
def test_get_receipt_word_label_not_found(
    dynamodb_table: Literal["MyMockedTable"],
) -> None:
    """Tests that get_receipt_word_label raises EntityNotFoundError for non-existent label."""
    client = DynamoClient(dynamodb_table)

    with pytest.raises(EntityNotFoundError, match="does not exist"):
        client.get_receipt_word_label(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            1,
            1,
            1,
            "ITEM",
        )


@pytest.mark.integration
@pytest.mark.parametrize(
    "image_id,receipt_id,line_id,word_id,label",
    [
        (None, 1, 1, 1, "ITEM"),  # None image_id
        ("valid-id", None, 1, 1, "ITEM"),  # None receipt_id
        ("valid-id", 1, None, 1, "ITEM"),  # None line_id
        ("valid-id", 1, 1, None, "ITEM"),  # None word_id
        ("valid-id", 1, 1, 1, None),  # None label
    ],
)
def test_get_receipt_word_label_invalid_params(
    dynamodb_table: Literal["MyMockedTable"],
    image_id: Any,
    receipt_id: Any,
    line_id: Any,
    word_id: Any,
    label: Any,
) -> None:
    """Tests that get_receipt_word_label raises EntityValidationError for invalid parameters."""
    client = DynamoClient(dynamodb_table)

    with pytest.raises((EntityValidationError, OperationError)):
        client.get_receipt_word_label(image_id, receipt_id, line_id, word_id, label)


# -------------------------------------------------------------------
#                        UPDATE OPERATIONS
# -------------------------------------------------------------------


@pytest.mark.integration
def test_update_receipt_word_label_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_word_label: ReceiptWordLabel,
) -> None:
    """Tests successful update of a receipt word label."""
    client = DynamoClient(dynamodb_table)

    # First add the label
    client.add_receipt_word_label(sample_receipt_word_label)

    # Update it with new reasoning
    updated_label = ReceiptWordLabel(
        receipt_id=sample_receipt_word_label.receipt_id,
        image_id=sample_receipt_word_label.image_id,
        line_id=sample_receipt_word_label.line_id,
        word_id=sample_receipt_word_label.word_id,
        label=sample_receipt_word_label.label,
        reasoning="Updated reasoning for the label",
        timestamp_added=sample_receipt_word_label.timestamp_added,
        validation_status=ValidationStatus.INVALID,
    )

    client.update_receipt_word_label(updated_label)

    # Verify the update
    retrieved = client.get_receipt_word_label(
        updated_label.image_id,
        updated_label.receipt_id,
        updated_label.line_id,
        updated_label.word_id,
        updated_label.label,
    )
    assert retrieved == updated_label
    assert retrieved.reasoning == "Updated reasoning for the label"
    assert retrieved.validation_status == ValidationStatus.INVALID


@pytest.mark.integration
def test_update_receipt_word_labels_batch(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_word_label: ReceiptWordLabel,
    another_receipt_word_label: ReceiptWordLabel,
) -> None:
    """Tests successful batch update of receipt word labels."""
    client = DynamoClient(dynamodb_table)

    # First add both labels
    client.add_receipt_word_label(sample_receipt_word_label)
    client.add_receipt_word_label(another_receipt_word_label)

    # Update both with new reasoning
    updated_labels = [
        ReceiptWordLabel(
            receipt_id=sample_receipt_word_label.receipt_id,
            image_id=sample_receipt_word_label.image_id,
            line_id=sample_receipt_word_label.line_id,
            word_id=sample_receipt_word_label.word_id,
            label=sample_receipt_word_label.label,
            reasoning="Batch updated reasoning 1",
            timestamp_added=sample_receipt_word_label.timestamp_added,
            validation_status=sample_receipt_word_label.validation_status,
        ),
        ReceiptWordLabel(
            receipt_id=another_receipt_word_label.receipt_id,
            image_id=another_receipt_word_label.image_id,
            line_id=another_receipt_word_label.line_id,
            word_id=another_receipt_word_label.word_id,
            label=another_receipt_word_label.label,
            reasoning="Batch updated reasoning 2",
            timestamp_added=another_receipt_word_label.timestamp_added,
            validation_status=another_receipt_word_label.validation_status,
        ),
    ]

    client.update_receipt_word_labels(updated_labels)

    # Verify both updates
    for updated in updated_labels:
        retrieved = client.get_receipt_word_label(
            updated.image_id,
            updated.receipt_id,
            updated.line_id,
            updated.word_id,
            updated.label,
        )
        assert retrieved == updated


# -------------------------------------------------------------------
#                        DELETE OPERATIONS
# -------------------------------------------------------------------


@pytest.mark.integration
def test_delete_receipt_word_label_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_word_label: ReceiptWordLabel,
) -> None:
    """Tests successful deletion of a receipt word label."""
    client = DynamoClient(dynamodb_table)

    # First add the label
    client.add_receipt_word_label(sample_receipt_word_label)

    # Delete it
    client.delete_receipt_word_label(sample_receipt_word_label)

    # Verify it's gone
    with pytest.raises(EntityNotFoundError):
        client.get_receipt_word_label(
            sample_receipt_word_label.image_id,
            sample_receipt_word_label.receipt_id,
            sample_receipt_word_label.line_id,
            sample_receipt_word_label.word_id,
            sample_receipt_word_label.label,
        )


@pytest.mark.integration
def test_delete_receipt_word_label_not_found(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_word_label: ReceiptWordLabel,
) -> None:
    """Tests that deleting a non-existent label raises EntityNotFoundError."""
    client = DynamoClient(dynamodb_table)

    # Delete non-existent label - should raise
    with pytest.raises(EntityNotFoundError):
        client.delete_receipt_word_label(sample_receipt_word_label)


@pytest.mark.integration
def test_delete_receipt_word_labels_batch(
    dynamodb_table: Literal["MyMockedTable"],
    batch_receipt_word_labels: List[ReceiptWordLabel],
) -> None:
    """Tests batch deletion of receipt word labels."""
    client = DynamoClient(dynamodb_table)

    # Add first 10 labels
    labels_to_delete = batch_receipt_word_labels[:10]
    for label in labels_to_delete:
        client.add_receipt_word_label(label)

    # Delete them in batch
    client.delete_receipt_word_labels(labels_to_delete)

    # Verify all are deleted
    for label in labels_to_delete:
        with pytest.raises(EntityNotFoundError):
            client.get_receipt_word_label(
                label.image_id,
                label.receipt_id,
                label.line_id,
                label.word_id,
                label.label,
            )


# -------------------------------------------------------------------
#                        LIST OPERATIONS
# -------------------------------------------------------------------


@pytest.mark.integration
def test_list_receipt_word_labels_for_image_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_word_label: ReceiptWordLabel,
) -> None:
    """Tests listing receipt word labels by image ID."""
    client = DynamoClient(dynamodb_table)

    # Add multiple labels for the same image
    labels = []
    for i in range(5):
        label = ReceiptWordLabel(
            receipt_id=i + 1,
            image_id=sample_receipt_word_label.image_id,
            line_id=i + 1,
            word_id=i + 1,
            label="ITEM" if i % 2 == 0 else "PRICE",
            reasoning=f"Label {i}",
            timestamp_added=sample_receipt_word_label.timestamp_added,
        )
        labels.append(label)
        client.add_receipt_word_label(label)

    # List them
    retrieved, last_key = client.list_receipt_word_labels_for_image(
        sample_receipt_word_label.image_id
    )

    assert len(retrieved) == 5
    assert all(l.image_id == sample_receipt_word_label.image_id for l in retrieved)
    assert last_key is None


@pytest.mark.integration
def test_list_receipt_word_labels_for_image_empty(
    dynamodb_table: Literal["MyMockedTable"],
) -> None:
    """Tests listing receipt word labels for an image with no labels."""
    client = DynamoClient(dynamodb_table)

    # Use a new image ID that has no labels
    empty_image_id = str(uuid4())

    # List labels for empty image
    retrieved, last_key = client.list_receipt_word_labels_for_image(empty_image_id)

    assert len(retrieved) == 0
    assert last_key is None


@pytest.mark.integration
def test_list_receipt_word_labels_for_image_with_pagination(
    dynamodb_table: Literal["MyMockedTable"],
) -> None:
    """Tests listing receipt word labels with pagination."""
    client = DynamoClient(dynamodb_table)

    # Add 30 labels for the same image to test pagination
    image_id = str(uuid4())
    labels = []
    for i in range(30):
        label = ReceiptWordLabel(
            receipt_id=(i // 3) + 1,  # 10 receipts
            image_id=image_id,
            line_id=(i % 3) + 1,  # 3 lines per receipt
            word_id=(i % 2) + 1,  # 2 words per line
            label="ITEM" if i % 2 == 0 else "PRICE",
            reasoning=f"Label {i}",
            timestamp_added="2024-03-20T12:00:00+00:00",
        )
        labels.append(label)
        client.add_receipt_word_label(label)

    # Test pagination with limit
    page1, last_key1 = client.list_receipt_word_labels_for_image(image_id, limit=10)
    assert len(page1) == 10
    assert last_key1 is not None

    # Get second page
    page2, last_key2 = client.list_receipt_word_labels_for_image(
        image_id, limit=10, last_evaluated_key=last_key1
    )
    assert len(page2) == 10
    assert last_key2 is not None

    # Get remaining items
    page3, last_key3 = client.list_receipt_word_labels_for_image(
        image_id, limit=10, last_evaluated_key=last_key2
    )
    assert len(page3) == 10
    assert last_key3 is None  # No more items

    # Verify all items retrieved
    all_retrieved = page1 + page2 + page3
    assert len(all_retrieved) == 30
    assert all(l.image_id == image_id for l in all_retrieved)


@pytest.mark.integration
def test_list_receipt_word_labels_success(
    dynamodb_table: Literal["MyMockedTable"],
) -> None:
    """Tests listing all receipt word labels."""
    client = DynamoClient(dynamodb_table)

    # Add multiple labels
    labels = []
    for i in range(6):
        label = ReceiptWordLabel(
            receipt_id=i + 1,
            image_id=str(uuid4()),
            line_id=i + 1,
            word_id=i + 1,
            label="ITEM",
            reasoning=f"Receipt label {i}",
            timestamp_added="2024-03-20T12:00:00+00:00",
        )
        labels.append(label)
        client.add_receipt_word_label(label)

    # List them
    retrieved, _ = client.list_receipt_word_labels(limit=10)

    assert len(retrieved) >= 6  # May have more from other tests


@pytest.mark.integration
def test_list_receipt_word_labels_for_receipt_success(
    dynamodb_table: Literal["MyMockedTable"],
) -> None:
    """Tests listing all receipt word labels for a specific receipt."""
    client = DynamoClient(dynamodb_table)
    image_id = str(uuid4())

    # Add labels for multiple receipts
    labels_receipt_1 = []
    labels_receipt_2 = []

    # Add 5 labels for receipt 1
    for i in range(5):
        label = ReceiptWordLabel(
            receipt_id=1,
            image_id=image_id,
            line_id=i + 1,
            word_id=i + 1,
            label="ITEM" if i % 2 == 0 else "PRICE",
            reasoning=f"Receipt 1 label {i}",
            timestamp_added="2024-03-20T12:00:00+00:00",
        )
        labels_receipt_1.append(label)
        client.add_receipt_word_label(label)

    # Add 3 labels for receipt 2
    for i in range(3):
        label = ReceiptWordLabel(
            receipt_id=2,
            image_id=image_id,
            line_id=i + 1,
            word_id=i + 1,
            label="MERCHANT_NAME" if i == 0 else "DATE",
            reasoning=f"Receipt 2 label {i}",
            timestamp_added="2024-03-20T12:00:00+00:00",
        )
        labels_receipt_2.append(label)
        client.add_receipt_word_label(label)

    # List labels for receipt 1
    retrieved_r1, last_key = client.list_receipt_word_labels_for_receipt(image_id, 1)

    assert len(retrieved_r1) == 5
    assert all(l.receipt_id == 1 for l in retrieved_r1)
    assert all(l.image_id == image_id for l in retrieved_r1)
    assert last_key is None

    # List labels for receipt 2
    retrieved_r2, last_key = client.list_receipt_word_labels_for_receipt(image_id, 2)

    assert len(retrieved_r2) == 3
    assert all(l.receipt_id == 2 for l in retrieved_r2)
    assert all(l.image_id == image_id for l in retrieved_r2)
    assert last_key is None


@pytest.mark.integration
def test_list_receipt_word_labels_for_receipt_empty(
    dynamodb_table: Literal["MyMockedTable"],
) -> None:
    """Tests listing receipt word labels for a receipt with no labels."""
    client = DynamoClient(dynamodb_table)

    # Use a new image ID and receipt ID that have no labels
    empty_image_id = str(uuid4())

    # List labels for non-existent receipt
    retrieved, last_key = client.list_receipt_word_labels_for_receipt(
        empty_image_id, 999
    )

    assert len(retrieved) == 0
    assert last_key is None


@pytest.mark.integration
def test_list_receipt_word_labels_for_receipt_with_pagination(
    dynamodb_table: Literal["MyMockedTable"],
) -> None:
    """Tests listing receipt word labels for a receipt with pagination."""
    client = DynamoClient(dynamodb_table)
    image_id = str(uuid4())
    receipt_id = 1

    # Add 20 labels for the same receipt to test pagination
    labels = []
    for line_id in range(1, 11):  # 10 lines
        for word_id in range(1, 3):  # 2 words per line = 20 total
            label = ReceiptWordLabel(
                receipt_id=receipt_id,
                image_id=image_id,
                line_id=line_id,
                word_id=word_id,
                label="ITEM" if word_id == 1 else "PRICE",
                reasoning=f"Line {line_id} word {word_id}",
                timestamp_added="2024-03-20T12:00:00+00:00",
            )
            labels.append(label)
            client.add_receipt_word_label(label)

    # Test pagination with limit
    page1, last_key1 = client.list_receipt_word_labels_for_receipt(
        image_id, receipt_id, limit=8
    )
    assert len(page1) == 8
    assert last_key1 is not None

    # Get second page
    page2, last_key2 = client.list_receipt_word_labels_for_receipt(
        image_id, receipt_id, limit=8, last_evaluated_key=last_key1
    )
    assert len(page2) == 8
    assert last_key2 is not None

    # Get remaining items
    page3, last_key3 = client.list_receipt_word_labels_for_receipt(
        image_id, receipt_id, limit=8, last_evaluated_key=last_key2
    )
    assert len(page3) == 4  # Only 4 items left
    assert last_key3 is None  # No more items

    # Verify all items retrieved
    all_retrieved = page1 + page2 + page3
    assert len(all_retrieved) == 20
    assert all(l.image_id == image_id for l in all_retrieved)
    assert all(l.receipt_id == receipt_id for l in all_retrieved)


@pytest.mark.integration
@pytest.mark.parametrize(
    "image_id,receipt_id,expected_error",
    [
        (None, 1, "image_id must be a string, got NoneType"),
        ("not-a-uuid", 1, "uuid must be a valid UUIDv4"),
        (
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            None,
            "receipt_id must be an integer, got NoneType",
        ),
        (
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "not-an-int",
            "receipt_id must be an integer, got str",
        ),
        (
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            0,
            "receipt_id must be a positive integer",
        ),
        (
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            -1,
            "receipt_id must be a positive integer",
        ),
    ],
)
def test_list_receipt_word_labels_for_receipt_validation(
    dynamodb_table: Literal["MyMockedTable"],
    image_id: Any,
    receipt_id: Any,
    expected_error: str,
) -> None:
    """Tests validation for list_receipt_word_labels_for_receipt parameters."""
    client = DynamoClient(dynamodb_table)

    with pytest.raises((EntityValidationError, OperationError), match=expected_error):
        client.list_receipt_word_labels_for_receipt(image_id, receipt_id)


# -------------------------------------------------------------------
#                   BATCH OPERATIONS WITH UNPROCESSED ITEMS
# -------------------------------------------------------------------


@pytest.mark.integration
def test_add_receipt_word_labels_batch_with_unprocessed(
    dynamodb_table: Literal["MyMockedTable"],
    batch_receipt_word_labels: List[ReceiptWordLabel],
    mocker: MockerFixture,
) -> None:
    """Tests that add_receipt_word_labels handles unprocessed items correctly."""
    client = DynamoClient(dynamodb_table)
    labels_to_add = batch_receipt_word_labels[:5]

    # Mock batch_write_item to return unprocessed items on first call
    # pylint: disable=protected-access
    mock_batch = mocker.patch.object(
        client._client,
        "batch_write_item",
        side_effect=[
            {
                "UnprocessedItems": {
                    dynamodb_table: [
                        {"PutRequest": {"Item": labels_to_add[0].to_item()}}
                    ]
                }
            },
            {},  # Success on retry
        ],
    )

    client.add_receipt_word_labels(labels_to_add)

    # Should be called twice (initial + retry)
    assert mock_batch.call_count == 2


@pytest.mark.integration
def test_update_receipt_word_label_not_found(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_word_label: ReceiptWordLabel,
) -> None:
    """Tests that updating a non-existent label raises EntityNotFoundError."""
    client = DynamoClient(dynamodb_table)

    with pytest.raises(
        EntityNotFoundError,
        match="not found during update_receipt_word_label",
    ):
        client.update_receipt_word_label(sample_receipt_word_label)


# -------------------------------------------------------------------
#                   VALIDATION STATUS FILTERING
# -------------------------------------------------------------------


@pytest.mark.integration
def test_list_receipt_word_labels_with_validation_status(
    dynamodb_table: Literal["MyMockedTable"],
) -> None:
    """Tests listing receipt word labels filtered by validation status."""
    client = DynamoClient(dynamodb_table)
    image_id = str(uuid4())

    # Add labels with different validation statuses
    valid_labels = []
    invalid_labels = []

    for i in range(3):
        valid_label = ReceiptWordLabel(
            receipt_id=i + 1,
            image_id=image_id,
            line_id=i + 1,
            word_id=1,
            label="ITEM",
            reasoning="Valid label",
            timestamp_added="2024-03-20T12:00:00+00:00",
            validation_status=ValidationStatus.VALID,
        )
        valid_labels.append(valid_label)
        client.add_receipt_word_label(valid_label)

        invalid_label = ReceiptWordLabel(
            receipt_id=i + 10,
            image_id=image_id,
            line_id=i + 1,
            word_id=2,
            label="PRICE",
            reasoning="Invalid label",
            timestamp_added="2024-03-20T12:00:00+00:00",
            validation_status=ValidationStatus.INVALID,
        )
        invalid_labels.append(invalid_label)
        client.add_receipt_word_label(invalid_label)

    # List labels with specific validation status
    valid_only, _ = client.list_receipt_word_labels_with_status(ValidationStatus.VALID)
    invalid_only, _ = client.list_receipt_word_labels_with_status(
        ValidationStatus.INVALID
    )

    # Filter by image_id since list_receipt_word_labels_with_status returns all labels
    valid_for_image = [l for l in valid_only if l.image_id == image_id]
    invalid_for_image = [l for l in invalid_only if l.image_id == image_id]

    assert len(valid_for_image) == 3
    assert len(invalid_for_image) == 3
    assert all(l.validation_status == ValidationStatus.VALID for l in valid_for_image)
    assert all(
        l.validation_status == ValidationStatus.INVALID for l in invalid_for_image
    )
