"""
Integration tests for BatchSummary operations in DynamoDB.

This module tests the BatchSummary-related methods of DynamoClient, including
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

from receipt_dynamo import BatchSummary, DynamoClient
from receipt_dynamo.constants import BatchStatus, BatchType
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

CORRECT_BATCH_SUMMARY_PARAMS: Dict[str, Any] = {
    "batch_id": str(uuid4()),
    "batch_type": BatchType.EMBEDDING.value,
    "openai_batch_id": "openai-xyz",
    "submitted_at": datetime(2024, 1, 1, 12, 0, 0),
    "status": BatchStatus.PENDING.value,
    "result_file_id": "file-456",
    "receipt_refs": [(str(uuid4()), 101)],
}


@pytest.fixture(name="sample_batch_summary")
def _sample_batch_summary() -> BatchSummary:
    """Provides a valid BatchSummary for testing."""
    return BatchSummary(**CORRECT_BATCH_SUMMARY_PARAMS)


@pytest.fixture(name="another_batch_summary")
def _another_batch_summary() -> BatchSummary:
    """Provides a second valid BatchSummary for testing."""
    return BatchSummary(
        batch_id=str(uuid4()),
        batch_type=BatchType.COMPLETION.value,
        openai_batch_id="openai-abc",
        submitted_at=datetime(2024, 1, 2, 10, 0, 0),
        status=BatchStatus.COMPLETED.value,
        result_file_id="file-789",
        receipt_refs=[(str(uuid4()), 202), (str(uuid4()), 203)],
    )


@pytest.fixture(name="batch_summaries")
def _batch_summaries() -> List[BatchSummary]:
    """Provides a list of 30 batch summaries for batch testing."""
    summaries = []
    base_time = datetime(2024, 1, 1, 12, 0, 0)

    for i in range(30):
        summaries.append(
            BatchSummary(
                batch_id=str(uuid4()),
                batch_type=[
                    BatchType.EMBEDDING,
                    BatchType.COMPLETION,
                    BatchType.LINE_EMBEDDING,
                ][i % 3].value,
                openai_batch_id=f"openai-{i:03d}",
                submitted_at=base_time,
                status=[
                    BatchStatus.PENDING,
                    BatchStatus.COMPLETED,
                    BatchStatus.FAILED,
                ][i % 3].value,
                result_file_id=f"file-{i:03d}",
                receipt_refs=[(str(uuid4()), i + 100)] if i % 3 != 2 else [],
            )
        )

    return summaries


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
        "batch_summary already exists",
    ),
] + ERROR_SCENARIOS

# Additional error for update operations
UPDATE_ERROR_SCENARIOS = [
    (
        "ConditionalCheckFailedException",
        EntityNotFoundError,
        "not found during update_batch_summary",
    ),
] + ERROR_SCENARIOS

# Additional error for batch update operations
UPDATE_BATCH_ERROR_SCENARIOS = [
    (
        "ConditionalCheckFailedException",
        EntityNotFoundError,
        "not found during update_batch_summaries",
    ),
] + ERROR_SCENARIOS


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,expected_exception,error_match", ADD_ERROR_SCENARIOS
)
def test_add_batch_summary_client_errors(
    dynamodb_table: Literal["MyMockedTable"],
    sample_batch_summary: BatchSummary,
    mocker: MockerFixture,
    error_code: str,
    expected_exception: Type[Exception],
    error_match: str,
) -> None:
    """Tests that add_batch_summary raises appropriate exceptions for various
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
        client.add_batch_summary(sample_batch_summary)
    mock_put.assert_called_once()


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,expected_exception,error_match", UPDATE_ERROR_SCENARIOS
)
def test_update_batch_summary_client_errors(
    dynamodb_table: Literal["MyMockedTable"],
    sample_batch_summary: BatchSummary,
    mocker: MockerFixture,
    error_code: str,
    expected_exception: Type[Exception],
    error_match: str,
) -> None:
    """Tests that update_batch_summary raises appropriate exceptions for
    various ClientError scenarios."""
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
        client.update_batch_summary(sample_batch_summary)
    mock_put.assert_called_once()


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,expected_exception,error_match", UPDATE_BATCH_ERROR_SCENARIOS
)
def test_update_batch_summaries_client_errors(
    dynamodb_table: Literal["MyMockedTable"],
    sample_batch_summary: BatchSummary,
    mocker: MockerFixture,
    error_code: str,
    expected_exception: Type[Exception],
    error_match: str,
) -> None:
    """Tests that update_batch_summaries raises appropriate exceptions for
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
        client.update_batch_summaries([sample_batch_summary])
    mock_transact.assert_called_once()


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,expected_exception,error_match",
    ERROR_SCENARIOS,  # Delete doesn't have ConditionalCheckFailedException
)
def test_delete_batch_summary_client_errors(
    dynamodb_table: Literal["MyMockedTable"],
    sample_batch_summary: BatchSummary,
    mocker: MockerFixture,
    error_code: str,
    expected_exception: Type[Exception],
    error_match: str,
) -> None:
    """Tests that delete_batch_summary raises appropriate exceptions for
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
        client.delete_batch_summary(sample_batch_summary)
    mock_delete.assert_called_once()


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,expected_exception,error_match",
    ERROR_SCENARIOS,  # Delete doesn't have ConditionalCheckFailedException
)
def test_delete_batch_summaries_client_errors(
    dynamodb_table: Literal["MyMockedTable"],
    sample_batch_summary: BatchSummary,
    mocker: MockerFixture,
    error_code: str,
    expected_exception: Type[Exception],
    error_match: str,
) -> None:
    """Tests that delete_batch_summaries raises appropriate exceptions for
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
        client.delete_batch_summaries([sample_batch_summary])
    mock_transact.assert_called_once()


# -------------------------------------------------------------------
#                   PARAMETERIZED VALIDATION ERROR TESTS
# -------------------------------------------------------------------

ADD_VALIDATION_SCENARIOS = [
    (None, "batch_summary cannot be None"),
    (
        "not-a-batch-summary",
        "batch_summary must be an instance of BatchSummary",
    ),
]

ADD_BATCH_VALIDATION_SCENARIOS = [
    (None, "batch_summaries cannot be None"),
    ("not-a-list", "batch_summaries must be a list"),
]


@pytest.mark.integration
@pytest.mark.parametrize("invalid_input,error_match", ADD_VALIDATION_SCENARIOS)
def test_add_batch_summary_validation_errors(
    dynamodb_table: Literal["MyMockedTable"],
    invalid_input: Any,
    error_match: str,
) -> None:
    """Tests that add_batch_summary raises appropriate error for
    invalid inputs."""
    client = DynamoClient(dynamodb_table)

    with pytest.raises(OperationError, match=error_match):
        client.add_batch_summary(invalid_input)  # type: ignore


@pytest.mark.integration
@pytest.mark.parametrize("invalid_input,error_match", ADD_BATCH_VALIDATION_SCENARIOS)
def test_add_batch_summaries_validation_errors(
    dynamodb_table: Literal["MyMockedTable"],
    invalid_input: Any,
    error_match: str,
) -> None:
    """Tests that add_batch_summaries raises appropriate error for
    invalid inputs."""
    client = DynamoClient(dynamodb_table)

    with pytest.raises(OperationError, match=error_match):
        client.add_batch_summaries(invalid_input)  # type: ignore


@pytest.mark.integration
def test_add_batch_summaries_invalid_list_contents(
    dynamodb_table: Literal["MyMockedTable"],
) -> None:
    """Tests that add_batch_summaries validates list contents."""
    client = DynamoClient(dynamodb_table)
    invalid_list = ["not-a-summary", 123, None]

    with pytest.raises(
        OperationError,
        match="All items in batch_summaries must be instances of BatchSummary",
    ):
        client.add_batch_summaries(invalid_list)  # type: ignore


# -------------------------------------------------------------------
#                        ADD OPERATIONS
# -------------------------------------------------------------------


@pytest.mark.integration
def test_add_batch_summary_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_batch_summary: BatchSummary,
) -> None:
    """Tests successful addition of a batch summary."""
    client = DynamoClient(dynamodb_table)

    # Add the summary
    client.add_batch_summary(sample_batch_summary)

    # Verify it was added by retrieving it
    retrieved = client.get_batch_summary(sample_batch_summary.batch_id)
    assert retrieved == sample_batch_summary


@pytest.mark.integration
def test_add_batch_summary_duplicate_raises(
    dynamodb_table: Literal["MyMockedTable"],
    sample_batch_summary: BatchSummary,
) -> None:
    """Tests that adding a duplicate batch summary raises EntityAlreadyExistsError."""
    client = DynamoClient(dynamodb_table)
    client.add_batch_summary(sample_batch_summary)

    with pytest.raises(EntityAlreadyExistsError, match="batch_summary already exists"):
        client.add_batch_summary(sample_batch_summary)


@pytest.mark.integration
def test_add_batch_summaries_success(
    dynamodb_table: Literal["MyMockedTable"],
    batch_summaries: List[BatchSummary],
) -> None:
    """Tests successful batch addition of batch summaries."""
    client = DynamoClient(dynamodb_table)

    # Add first 10 summaries
    summaries_to_add = batch_summaries[:10]
    client.add_batch_summaries(summaries_to_add)

    # Verify all were added
    for summary in summaries_to_add:
        retrieved = client.get_batch_summary(summary.batch_id)
        assert retrieved == summary


# -------------------------------------------------------------------
#                        GET OPERATIONS
# -------------------------------------------------------------------


@pytest.mark.integration
def test_get_batch_summary_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_batch_summary: BatchSummary,
) -> None:
    """Tests successful retrieval of a batch summary."""
    client = DynamoClient(dynamodb_table)
    client.add_batch_summary(sample_batch_summary)

    retrieved = client.get_batch_summary(sample_batch_summary.batch_id)

    assert retrieved == sample_batch_summary


@pytest.mark.integration
def test_get_batch_summary_not_found(
    dynamodb_table: Literal["MyMockedTable"],
) -> None:
    """Tests that get_batch_summary raises EntityNotFoundError for non-existent summary."""
    client = DynamoClient(dynamodb_table)

    with pytest.raises(EntityNotFoundError, match="does not exist"):
        client.get_batch_summary(str(uuid4()))


@pytest.mark.integration
@pytest.mark.parametrize(
    "batch_id",
    [
        None,  # None batch_id
        123,  # Non-string batch_id
        "not-a-uuid",  # Invalid UUID format
        "",  # Empty string
    ],
)
def test_get_batch_summary_invalid_params(
    dynamodb_table: Literal["MyMockedTable"],
    batch_id: Any,
) -> None:
    """Tests that get_batch_summary raises EntityValidationError for invalid parameters."""
    client = DynamoClient(dynamodb_table)

    with pytest.raises((EntityValidationError, OperationError)):
        client.get_batch_summary(batch_id)


# -------------------------------------------------------------------
#                        UPDATE OPERATIONS
# -------------------------------------------------------------------


@pytest.mark.integration
def test_update_batch_summary_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_batch_summary: BatchSummary,
) -> None:
    """Tests successful update of a batch summary."""
    client = DynamoClient(dynamodb_table)

    # First add the summary
    client.add_batch_summary(sample_batch_summary)

    # Update it with new values
    sample_batch_summary.status = BatchStatus.COMPLETED.value
    sample_batch_summary.result_file_id = "file-999"

    client.update_batch_summary(sample_batch_summary)

    # Verify the update
    retrieved = client.get_batch_summary(sample_batch_summary.batch_id)
    assert retrieved.status == BatchStatus.COMPLETED.value
    assert retrieved.result_file_id == "file-999"


@pytest.mark.integration
def test_update_batch_summary_not_found(
    dynamodb_table: Literal["MyMockedTable"],
    sample_batch_summary: BatchSummary,
) -> None:
    """Tests that updating a non-existent summary raises EntityNotFoundError."""
    client = DynamoClient(dynamodb_table)

    with pytest.raises(
        EntityNotFoundError,
        match="not found during update_batch_summary",
    ):
        client.update_batch_summary(sample_batch_summary)


@pytest.mark.integration
def test_update_batch_summaries_batch(
    dynamodb_table: Literal["MyMockedTable"],
    sample_batch_summary: BatchSummary,
    another_batch_summary: BatchSummary,
) -> None:
    """Tests successful batch update of batch summaries."""
    client = DynamoClient(dynamodb_table)

    # First add both summaries
    client.add_batch_summary(sample_batch_summary)
    client.add_batch_summary(another_batch_summary)

    # Update both with new status
    sample_batch_summary.status = BatchStatus.FAILED.value
    another_batch_summary.status = BatchStatus.FAILED.value
    sample_batch_summary.result_file_id = "file-error-1"
    another_batch_summary.result_file_id = "file-error-2"

    client.update_batch_summaries([sample_batch_summary, another_batch_summary])

    # Verify both updates
    retrieved1 = client.get_batch_summary(sample_batch_summary.batch_id)
    retrieved2 = client.get_batch_summary(another_batch_summary.batch_id)

    assert retrieved1.status == BatchStatus.FAILED.value
    assert retrieved1.result_file_id == "file-error-1"
    assert retrieved2.status == BatchStatus.FAILED.value
    assert retrieved2.result_file_id == "file-error-2"


# -------------------------------------------------------------------
#                        DELETE OPERATIONS
# -------------------------------------------------------------------


@pytest.mark.integration
def test_delete_batch_summary_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_batch_summary: BatchSummary,
) -> None:
    """Tests successful deletion of a batch summary."""
    client = DynamoClient(dynamodb_table)

    # First add the summary
    client.add_batch_summary(sample_batch_summary)

    # Delete it
    client.delete_batch_summary(sample_batch_summary)

    # Verify it's gone
    with pytest.raises(EntityNotFoundError):
        client.get_batch_summary(sample_batch_summary.batch_id)


@pytest.mark.integration
def test_delete_batch_summary_not_found(
    dynamodb_table: Literal["MyMockedTable"],
    sample_batch_summary: BatchSummary,
) -> None:
    """Tests that deleting a non-existent summary raises EntityNotFoundError."""
    client = DynamoClient(dynamodb_table)

    # Delete non-existent summary - should raise
    with pytest.raises(EntityNotFoundError):
        client.delete_batch_summary(sample_batch_summary)


@pytest.mark.integration
def test_delete_batch_summaries_batch(
    dynamodb_table: Literal["MyMockedTable"],
    batch_summaries: List[BatchSummary],
) -> None:
    """Tests batch deletion of batch summaries."""
    client = DynamoClient(dynamodb_table)

    # Add first 10 summaries
    summaries_to_delete = batch_summaries[:10]
    for summary in summaries_to_delete:
        client.add_batch_summary(summary)

    # Delete them in batch
    client.delete_batch_summaries(summaries_to_delete)

    # Verify all are deleted
    for summary in summaries_to_delete:
        with pytest.raises(EntityNotFoundError):
            client.get_batch_summary(summary.batch_id)


# -------------------------------------------------------------------
#                        LIST OPERATIONS
# -------------------------------------------------------------------


@pytest.mark.integration
def test_list_batch_summaries_success(
    dynamodb_table: Literal["MyMockedTable"],
) -> None:
    """Tests listing all batch summaries."""
    client = DynamoClient(dynamodb_table)

    # Add multiple summaries
    summaries = []
    for i in range(5):
        summary = BatchSummary(
            batch_id=str(uuid4()),
            batch_type=BatchType.EMBEDDING.value,
            openai_batch_id=f"openai-test-{i}",
            submitted_at=datetime(2024, 1, 1, 12, 0, 0),
            status=BatchStatus.PENDING.value,
            result_file_id=f"file-test-{i}",
            receipt_refs=[(str(uuid4()), i + 1)],
        )
        summaries.append(summary)
        client.add_batch_summary(summary)

    # List them
    retrieved, last_key = client.list_batch_summaries(limit=10)

    assert len(retrieved) >= 5  # May have more from other tests
    assert last_key is None


@pytest.mark.integration
def test_list_batch_summaries_with_pagination(
    dynamodb_table: Literal["MyMockedTable"],
    batch_summaries: List[BatchSummary],
) -> None:
    """Tests listing batch summaries with pagination."""
    client = DynamoClient(dynamodb_table)

    # Add 30 summaries
    for summary in batch_summaries:
        client.add_batch_summary(summary)

    # Get first page
    page1, last_key1 = client.list_batch_summaries(limit=15)
    assert len(page1) == 15
    assert last_key1 is not None

    # Get second page
    page2, last_key2 = client.list_batch_summaries(
        limit=15, last_evaluated_key=last_key1
    )
    assert len(page2) <= 15  # May be fewer items on second page

    # Get remaining items if there are more
    all_retrieved = page1 + page2
    if last_key2 is not None:
        page3, last_key3 = client.list_batch_summaries(
            limit=15, last_evaluated_key=last_key2
        )
        all_retrieved.extend(page3)

    # Verify we got all our items
    assert len(all_retrieved) >= 30


# -------------------------------------------------------------------
#                   QUERY BY STATUS OPERATIONS
# -------------------------------------------------------------------


@pytest.mark.integration
def test_get_batch_summaries_by_status_success(
    dynamodb_table: Literal["MyMockedTable"],
) -> None:
    """Tests retrieving batch summaries by status."""
    client = DynamoClient(dynamodb_table)

    # Add summaries with different statuses
    statuses = [BatchStatus.PENDING, BatchStatus.COMPLETED, BatchStatus.FAILED]
    summaries = []

    for i, status in enumerate(statuses):
        for j in range(3):  # 3 summaries per status
            summary = BatchSummary(
                batch_id=str(uuid4()),
                batch_type=BatchType.EMBEDDING.value,
                openai_batch_id=f"openai-{status.value}-{j}",
                submitted_at=datetime(2024, 1, 1, 12, 0, 0),
                status=status,
                result_file_id=f"file-{status.value}-{j}",
                receipt_refs=[(str(uuid4()), (i * 10) + j + 1)],
            )
            summaries.append(summary)
            client.add_batch_summary(summary)

    # Query by status
    pending_summaries, _ = client.get_batch_summaries_by_status(
        BatchStatus.PENDING, BatchType.EMBEDDING
    )
    completed_summaries, _ = client.get_batch_summaries_by_status(
        BatchStatus.COMPLETED, BatchType.EMBEDDING
    )
    failed_summaries, _ = client.get_batch_summaries_by_status(
        BatchStatus.FAILED, BatchType.EMBEDDING
    )

    # Filter to only our test summaries (may have others from other tests)
    test_batch_ids = {s.batch_id for s in summaries}

    pending_test = [s for s in pending_summaries if s.batch_id in test_batch_ids]
    completed_test = [s for s in completed_summaries if s.batch_id in test_batch_ids]
    failed_test = [s for s in failed_summaries if s.batch_id in test_batch_ids]

    assert len(pending_test) == 3
    assert len(completed_test) == 3
    assert len(failed_test) == 3

    assert all(s.status == BatchStatus.PENDING.value for s in pending_test)
    assert all(s.status == BatchStatus.COMPLETED.value for s in completed_test)
    assert all(s.status == BatchStatus.FAILED.value for s in failed_test)


@pytest.mark.integration
def test_get_batch_summaries_by_status_with_batch_type(
    dynamodb_table: Literal["MyMockedTable"],
) -> None:
    """Tests retrieving batch summaries by status and batch type."""
    client = DynamoClient(dynamodb_table)

    # Add summaries with different batch types
    batch_types = [
        BatchType.EMBEDDING,
        BatchType.COMPLETION,
        BatchType.LINE_EMBEDDING,
    ]
    summaries = []

    for batch_type in batch_types:
        summary = BatchSummary(
            batch_id=str(uuid4()),
            batch_type=batch_type,
            openai_batch_id=f"openai-{batch_type.value}",
            submitted_at=datetime(2024, 1, 1, 12, 0, 0),
            status=BatchStatus.PENDING,
            result_file_id=f"file-{batch_type.value}",
            receipt_refs=[(str(uuid4()), 1)],
        )
        summaries.append(summary)
        client.add_batch_summary(summary)

    # Query by status and specific batch type
    embedding_summaries, _ = client.get_batch_summaries_by_status(
        BatchStatus.PENDING, batch_type=BatchType.EMBEDDING
    )
    completion_summaries, _ = client.get_batch_summaries_by_status(
        BatchStatus.PENDING, batch_type=BatchType.COMPLETION
    )

    # Filter to only our test summaries
    test_batch_ids = {s.batch_id for s in summaries}

    embedding_test = [s for s in embedding_summaries if s.batch_id in test_batch_ids]
    completion_test = [s for s in completion_summaries if s.batch_id in test_batch_ids]

    assert len(embedding_test) == 1
    assert len(completion_test) == 1
    assert embedding_test[0].batch_type == BatchType.EMBEDDING.value
    assert completion_test[0].batch_type == BatchType.COMPLETION.value


@pytest.mark.integration
def test_get_batch_summaries_by_status_with_pagination(
    dynamodb_table: Literal["MyMockedTable"],
) -> None:
    """Tests retrieving batch summaries by status with pagination."""
    client = DynamoClient(dynamodb_table)

    # Add 15 pending summaries
    summaries = []
    for i in range(15):
        summary = BatchSummary(
            batch_id=str(uuid4()),
            batch_type=BatchType.EMBEDDING.value,
            openai_batch_id=f"openai-pending-{i}",
            submitted_at=datetime(2024, 1, 1, 12, 0, 0),
            status=BatchStatus.PENDING,
            result_file_id=f"file-pending-{i}",
            receipt_refs=[(str(uuid4()), i + 1)],
        )
        summaries.append(summary)
        client.add_batch_summary(summary)

    # Get first page
    page1, last_key1 = client.get_batch_summaries_by_status(
        BatchStatus.PENDING, BatchType.EMBEDDING, limit=8
    )
    assert len(page1) >= 8  # At least 8
    assert last_key1 is not None

    # Get second page
    page2, last_key2 = client.get_batch_summaries_by_status(
        BatchStatus.PENDING,
        BatchType.EMBEDDING,
        limit=8,
        last_evaluated_key=last_key1,
    )
    assert len(page2) >= 0  # May have items

    # All items should be pending
    all_retrieved = page1 + page2
    assert all(s.status == BatchStatus.PENDING.value for s in all_retrieved)


# -------------------------------------------------------------------
#                   VALIDATION PARAMETER TESTS
# -------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize(
    "status,expected_error",
    [
        (None, "status must be either a BatchStatus enum or a string"),
        (123, "status must be either a BatchStatus enum or a string"),
        ("INVALID_STATUS", "Invalid status"),
    ],
)
def test_get_batch_summaries_by_status_validation(
    dynamodb_table: Literal["MyMockedTable"],
    status: Any,
    expected_error: str,
) -> None:
    """Tests validation for get_batch_summaries_by_status parameters."""
    client = DynamoClient(dynamodb_table)

    with pytest.raises(EntityValidationError, match=expected_error):
        client.get_batch_summaries_by_status(status)  # type: ignore


@pytest.mark.integration
@pytest.mark.parametrize(
    "batch_type,expected_error",
    [
        (123, "batch_type must be either a BatchType enum or a string"),
        ("INVALID_TYPE", "Invalid batch type"),
    ],
)
def test_get_batch_summaries_by_status_batch_type_validation(
    dynamodb_table: Literal["MyMockedTable"],
    batch_type: Any,
    expected_error: str,
) -> None:
    """Tests validation for batch_type parameter."""
    client = DynamoClient(dynamodb_table)

    with pytest.raises(EntityValidationError, match=expected_error):
        client.get_batch_summaries_by_status(BatchStatus.PENDING, batch_type=batch_type)


@pytest.mark.integration
@pytest.mark.parametrize(
    "limit,expected_error",
    [
        ("not-an-int", "limit must be an integer"),
        (0, "limit must be greater than 0"),
        (-5, "limit must be greater than 0"),
    ],
)
def test_list_batch_summaries_limit_validation(
    dynamodb_table: Literal["MyMockedTable"],
    limit: Any,
    expected_error: str,
) -> None:
    """Tests validation for list_batch_summaries limit parameter."""
    client = DynamoClient(dynamodb_table)

    with pytest.raises(EntityValidationError, match=expected_error):
        client.list_batch_summaries(limit=limit)


# -------------------------------------------------------------------
#                   BATCH OPERATIONS WITH UNPROCESSED ITEMS
# -------------------------------------------------------------------


@pytest.mark.integration
def test_add_batch_summaries_with_unprocessed(
    dynamodb_table: Literal["MyMockedTable"],
    batch_summaries: List[BatchSummary],
    mocker: MockerFixture,
) -> None:
    """Tests that add_batch_summaries handles unprocessed items correctly."""
    client = DynamoClient(dynamodb_table)
    summaries_to_add = batch_summaries[:5]

    # Mock batch_write_item to return unprocessed items on first call
    # pylint: disable=protected-access
    mock_batch = mocker.patch.object(
        client._client,
        "batch_write_item",
        side_effect=[
            {
                "UnprocessedItems": {
                    dynamodb_table: [
                        {"PutRequest": {"Item": summaries_to_add[0].to_item()}}
                    ]
                }
            },
            {},  # Success on retry
        ],
    )

    client.add_batch_summaries(summaries_to_add)

    # Should be called twice (initial + retry)
    assert mock_batch.call_count == 2


@pytest.mark.integration
def test_update_batch_summaries_chunked(
    dynamodb_table: Literal["MyMockedTable"],
    batch_summaries: List[BatchSummary],
    mocker: MockerFixture,
) -> None:
    """Tests that update_batch_summaries properly chunks large batches."""
    client = DynamoClient(dynamodb_table)

    # Add all summaries first
    for summary in batch_summaries:
        client.add_batch_summary(summary)

    # Mock transact_write_items to verify chunking
    # pylint: disable=protected-access
    mock_transact = mocker.patch.object(
        client._client,
        "transact_write_items",
        return_value={},
    )

    # Update all 30 summaries (should be chunked into 2 calls)
    client.update_batch_summaries(batch_summaries)

    # Should be called twice (25 + 5 items)
    assert mock_transact.call_count == 2


@pytest.mark.integration
def test_delete_batch_summaries_chunked(
    dynamodb_table: Literal["MyMockedTable"],
    batch_summaries: List[BatchSummary],
    mocker: MockerFixture,
) -> None:
    """Tests that delete_batch_summaries properly chunks large batches."""
    client = DynamoClient(dynamodb_table)

    # Add all summaries first
    for summary in batch_summaries:
        client.add_batch_summary(summary)

    # Mock transact_write_items to verify chunking
    # pylint: disable=protected-access
    mock_transact = mocker.patch.object(
        client._client,
        "transact_write_items",
        return_value={},
    )

    # Delete all 30 summaries (should be chunked into 2 calls)
    client.delete_batch_summaries(batch_summaries)

    # Should be called twice (25 + 5 items)
    assert mock_transact.call_count == 2


# -------------------------------------------------------------------
#                   SPECIAL CASES
# -------------------------------------------------------------------


@pytest.mark.integration
def test_batch_summary_with_empty_receipt_refs(
    dynamodb_table: Literal["MyMockedTable"],
) -> None:
    """Tests batch summary with empty receipt_refs list."""
    client = DynamoClient(dynamodb_table)

    # Create summary with empty receipt_refs
    summary = BatchSummary(
        batch_id=str(uuid4()),
        batch_type=BatchType.EMBEDDING.value,
        openai_batch_id="openai-empty",
        submitted_at=datetime(2024, 1, 1, 12, 0, 0),
        status=BatchStatus.PENDING,
        result_file_id="file-empty",
        receipt_refs=[],  # Empty list
    )

    # Add and retrieve
    client.add_batch_summary(summary)
    retrieved = client.get_batch_summary(summary.batch_id)

    assert retrieved == summary
    assert retrieved.receipt_refs == []


@pytest.mark.integration
def test_batch_summary_with_multiple_receipt_refs(
    dynamodb_table: Literal["MyMockedTable"],
) -> None:
    """Tests batch summary with multiple receipt references."""
    client = DynamoClient(dynamodb_table)

    # Create summary with multiple receipt_refs
    receipt_refs = [(str(uuid4()), i) for i in range(10)]
    summary = BatchSummary(
        batch_id=str(uuid4()),
        batch_type=BatchType.COMPLETION.value,
        openai_batch_id="openai-multiple",
        submitted_at=datetime(2024, 1, 1, 12, 0, 0),
        status=BatchStatus.PENDING,
        result_file_id="file-multiple",
        receipt_refs=receipt_refs,
    )

    # Add and retrieve
    client.add_batch_summary(summary)
    retrieved = client.get_batch_summary(summary.batch_id)

    assert retrieved == summary
    assert len(retrieved.receipt_refs) == 10
    assert retrieved.receipt_refs == receipt_refs


@pytest.mark.integration
def test_batch_summary_status_transitions(
    dynamodb_table: Literal["MyMockedTable"],
    sample_batch_summary: BatchSummary,
) -> None:
    """Tests batch summary status transitions."""
    client = DynamoClient(dynamodb_table)

    # Add summary with PENDING status
    sample_batch_summary.status = BatchStatus.PENDING.value
    client.add_batch_summary(sample_batch_summary)

    # Transition to COMPLETED
    sample_batch_summary.status = BatchStatus.COMPLETED.value
    sample_batch_summary.result_file_id = "file-completed"
    client.update_batch_summary(sample_batch_summary)

    # Verify
    retrieved = client.get_batch_summary(sample_batch_summary.batch_id)
    assert retrieved.status == BatchStatus.COMPLETED.value
    assert retrieved.result_file_id == "file-completed"

    # Transition to FAILED
    sample_batch_summary.status = BatchStatus.FAILED.value
    sample_batch_summary.result_file_id = "file-failed"
    client.update_batch_summary(sample_batch_summary)

    # Verify final state
    retrieved = client.get_batch_summary(sample_batch_summary.batch_id)
    assert retrieved.status == BatchStatus.FAILED.value
    assert retrieved.result_file_id == "file-failed"


@pytest.mark.integration
def test_batch_summary_different_batch_types(
    dynamodb_table: Literal["MyMockedTable"],
) -> None:
    """Tests batch summaries with different batch types."""
    client = DynamoClient(dynamodb_table)

    # Create summaries with different types
    batch_types = [
        (BatchType.EMBEDDING, "openai-emb"),
        (BatchType.COMPLETION, "openai-comp"),
        (BatchType.LINE_EMBEDDING, "openai-line"),
    ]

    summaries = []
    for batch_type, openai_id in batch_types:
        summary = BatchSummary(
            batch_id=str(uuid4()),
            batch_type=batch_type,
            openai_batch_id=openai_id,
            submitted_at=datetime(2024, 1, 1, 12, 0, 0),
            status=BatchStatus.PENDING,
            result_file_id=f"{openai_id}-file",
            receipt_refs=[(str(uuid4()), 1)],
        )
        summaries.append(summary)
        client.add_batch_summary(summary)

    # Retrieve and verify each type
    for summary in summaries:
        retrieved = client.get_batch_summary(summary.batch_id)
        assert retrieved.batch_type == summary.batch_type


@pytest.mark.integration
def test_get_batch_summaries_by_status_enum_vs_string(
    dynamodb_table: Literal["MyMockedTable"],
) -> None:
    """Tests that both enum and string values work for status queries."""
    client = DynamoClient(dynamodb_table)

    # Add a pending summary
    summary = BatchSummary(
        batch_id=str(uuid4()),
        batch_type=BatchType.EMBEDDING,
        openai_batch_id="openai-test",
        submitted_at=datetime(2024, 1, 1, 12, 0, 0),
        status=BatchStatus.PENDING,
        result_file_id="file-test",
        receipt_refs=[(str(uuid4()), 1)],
    )
    client.add_batch_summary(summary)

    # Query with enum
    enum_results, _ = client.get_batch_summaries_by_status(
        BatchStatus.PENDING, BatchType.EMBEDDING
    )

    # Query with string
    string_results, _ = client.get_batch_summaries_by_status(
        "PENDING", BatchType.EMBEDDING
    )

    # Filter to our test summary
    enum_test = [r for r in enum_results if r.batch_id == summary.batch_id]
    string_test = [r for r in string_results if r.batch_id == summary.batch_id]

    assert len(enum_test) == 1
    assert len(string_test) == 1
    assert enum_test[0] == string_test[0]
