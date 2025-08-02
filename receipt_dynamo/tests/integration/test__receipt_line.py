# pylint: disable=too-many-lines
"""
Comprehensive parameterized integration tests for receipt line operations.
This file contains refactored tests using pytest.mark.parametrize to ensure
complete test coverage matching test__receipt.py standards.
"""


from typing import Any, Literal, Type
from uuid import uuid4

import pytest
from botocore.exceptions import ClientError
from pytest_mock import MockerFixture

from receipt_dynamo import DynamoClient, ReceiptLine
from receipt_dynamo.constants import EmbeddingStatus
from receipt_dynamo.data.shared_exceptions import (
    DynamoDBError,
    DynamoDBServerError,
    DynamoDBThroughputError,
    EntityAlreadyExistsError,
    EntityNotFoundError,
    EntityValidationError,
    OperationError,
)

# -------------------------------------------------------------------
#           FIXTURES
# -------------------------------------------------------------------


# pylint: disable=redefined-outer-name
@pytest.fixture
def unique_image_id() -> str:
    """Generate a unique image ID for each test to avoid conflicts."""
    return str(uuid4())


# pylint: disable=redefined-outer-name
@pytest.fixture
def sample_receipt_line(unique_image_id: str) -> ReceiptLine:
    """Returns a valid ReceiptLine object with unique data."""
    return ReceiptLine(
        receipt_id=1,
        image_id=unique_image_id,
        line_id=10,
        text="Sample receipt line",
        bounding_box={"x": 0.1, "y": 0.2, "width": 0.4, "height": 0.05},
        top_left={"x": 0.1, "y": 0.2},
        top_right={"x": 0.5, "y": 0.2},
        bottom_left={"x": 0.1, "y": 0.25},
        bottom_right={"x": 0.5, "y": 0.25},
        angle_degrees=5.0,
        angle_radians=0.0872665,
        confidence=0.98,
        embedding_status=EmbeddingStatus.NONE,
    )


# -------------------------------------------------------------------
#           ERROR SCENARIO PARAMETERS
# -------------------------------------------------------------------

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

UPDATE_BATCH_ERROR_SCENARIOS = ERROR_SCENARIOS + [
    (
        "ConditionalCheckFailedException",
        EntityNotFoundError,
        "Cannot update receiptlines",
    ),
]

DELETE_BATCH_ERROR_SCENARIOS = ERROR_SCENARIOS + [
    (
        "ConditionalCheckFailedException",
        EntityNotFoundError,
        "receiptline not found during delete_receipt_lines",
    ),
]

# Fixed UUIDs for test consistency
FIXED_UUIDS = [
    "550e8400-e29b-41d4-a716-446655440001",
    "550e8400-e29b-41d4-a716-446655440002",
    "550e8400-e29b-41d4-a716-446655440003",
]


# -------------------------------------------------------------------
#           PARAMETERIZED SINGLE OPERATION CLIENT ERROR TESTS
# -------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,expected_exception,error_match", ERROR_SCENARIOS
)
# pylint: disable=too-many-arguments
def test_add_receipt_line_client_errors(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_line: ReceiptLine,
    mocker: MockerFixture,
    error_code: str,
    expected_exception: Type[Exception],
    error_match: str,
) -> None:
    """Tests error handling for add_receipt_line operations."""
    client = DynamoClient(dynamodb_table)

    # pylint: disable=protected-access
    mock_put = mocker.patch.object(
        client._client,
        "put_item",
        side_effect=ClientError({"Error": {"Code": error_code}}, "PutItem"),
    )

    with pytest.raises(expected_exception, match=error_match):
        client.add_receipt_line(sample_receipt_line)

    mock_put.assert_called_once()


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,expected_exception,error_match", ERROR_SCENARIOS
)
# pylint: disable=too-many-arguments
def test_update_receipt_line_client_errors(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_line: ReceiptLine,
    mocker: MockerFixture,
    error_code: str,
    expected_exception: Type[Exception],
    error_match: str,
) -> None:
    """Tests error handling for update_receipt_line operations."""
    client = DynamoClient(dynamodb_table)
    client.add_receipt_line(sample_receipt_line)

    # pylint: disable=protected-access
    mock_put = mocker.patch.object(
        client._client,
        "put_item",
        side_effect=ClientError({"Error": {"Code": error_code}}, "PutItem"),
    )

    with pytest.raises(expected_exception, match=error_match):
        client.update_receipt_line(sample_receipt_line)

    mock_put.assert_called_once()


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,expected_exception,error_match", ERROR_SCENARIOS
)
# pylint: disable=too-many-arguments
def test_delete_receipt_line_client_errors(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_line: ReceiptLine,
    mocker: MockerFixture,
    error_code: str,
    expected_exception: Type[Exception],
    error_match: str,
) -> None:
    """Tests error handling for delete_receipt_line operations."""
    client = DynamoClient(dynamodb_table)
    client.add_receipt_line(sample_receipt_line)

    # pylint: disable=protected-access
    mock_delete = mocker.patch.object(
        client._client,
        "delete_item",
        side_effect=ClientError({"Error": {"Code": error_code}}, "DeleteItem"),
    )

    with pytest.raises(expected_exception, match=error_match):
        client.delete_receipt_line(
            sample_receipt_line.receipt_id,
            sample_receipt_line.image_id,
            sample_receipt_line.line_id,
        )

    mock_delete.assert_called_once()


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,expected_exception,error_match", ERROR_SCENARIOS
)
# pylint: disable=too-many-arguments
def test_get_receipt_line_client_errors(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_line: ReceiptLine,
    mocker: MockerFixture,
    error_code: str,
    expected_exception: Type[Exception],
    error_match: str,
) -> None:
    """Tests error handling for get_receipt_line operations."""
    client = DynamoClient(dynamodb_table)

    # pylint: disable=protected-access
    mock_get = mocker.patch.object(
        client._client,
        "get_item",
        side_effect=ClientError({"Error": {"Code": error_code}}, "GetItem"),
    )

    with pytest.raises(expected_exception, match=error_match):
        client.get_receipt_line(
            sample_receipt_line.receipt_id,
            sample_receipt_line.image_id,
            sample_receipt_line.line_id,
        )

    mock_get.assert_called_once()


# -------------------------------------------------------------------
#           PARAMETERIZED BATCH OPERATION CLIENT ERROR TESTS
# -------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,expected_exception,error_match", ERROR_SCENARIOS
)
# pylint: disable=too-many-arguments
def test_add_receipt_lines_client_errors(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_line: ReceiptLine,
    mocker: MockerFixture,
    error_code: str,
    expected_exception: Type[Exception],
    error_match: str,
) -> None:
    """Tests error handling for add_receipt_lines batch operations."""
    client = DynamoClient(dynamodb_table)
    lines = [sample_receipt_line]

    # pylint: disable=protected-access
    mock_transact = mocker.patch.object(
        client._client,
        "transact_write_items",
        side_effect=ClientError(
            {"Error": {"Code": error_code}}, "TransactWriteItems"
        ),
    )

    with pytest.raises(expected_exception, match=error_match):
        client.add_receipt_lines(lines)

    mock_transact.assert_called_once()


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,expected_exception,error_match", UPDATE_BATCH_ERROR_SCENARIOS
)
# pylint: disable=too-many-arguments
def test_update_receipt_lines_client_errors(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_line: ReceiptLine,
    mocker: MockerFixture,
    error_code: str,
    expected_exception: Type[Exception],
    error_match: str,
) -> None:
    """Tests error handling for update_receipt_lines batch operations."""
    client = DynamoClient(dynamodb_table)
    lines = [sample_receipt_line]

    # pylint: disable=protected-access
    mock_transact = mocker.patch.object(
        client._client,
        "transact_write_items",
        side_effect=ClientError(
            {"Error": {"Code": error_code}}, "TransactWriteItems"
        ),
    )

    with pytest.raises(expected_exception, match=error_match):
        client.update_receipt_lines(lines)

    mock_transact.assert_called_once()


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,expected_exception,error_match", DELETE_BATCH_ERROR_SCENARIOS
)
# pylint: disable=too-many-arguments
def test_delete_receipt_lines_client_errors(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_line: ReceiptLine,
    mocker: MockerFixture,
    error_code: str,
    expected_exception: Type[Exception],
    error_match: str,
) -> None:
    """Tests error handling for delete_receipt_lines batch operations."""
    client = DynamoClient(dynamodb_table)
    lines = [sample_receipt_line]

    # pylint: disable=protected-access
    mock_transact = mocker.patch.object(
        client._client,
        "transact_write_items",
        side_effect=ClientError(
            {"Error": {"Code": error_code}}, "TransactWriteItems"
        ),
    )

    with pytest.raises(expected_exception, match=error_match):
        client.delete_receipt_lines(lines)

    mock_transact.assert_called_once()


# -------------------------------------------------------------------
#           PARAMETERIZED VALIDATION TESTS
# -------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize(
    "method_name,invalid_input,error_match",
    [
        ("add_receipt_line", None, "receipt_line cannot be None"),
        (
            "add_receipt_line",
            "not-a-line",
            "receipt_line must be an instance of ReceiptLine",
        ),
        ("update_receipt_line", None, "receipt_line cannot be None"),
        (
            "update_receipt_line",
            "not-a-line",
            "receipt_line must be an instance of ReceiptLine",
        ),
    ],
)
def test_single_receipt_line_validation(
    dynamodb_table: Literal["MyMockedTable"],
    method_name: str,
    invalid_input: Any,
    error_match: str,
) -> None:
    """Tests validation for single receipt line operations."""
    client = DynamoClient(dynamodb_table)
    method = getattr(client, method_name)

    with pytest.raises(EntityValidationError, match=error_match):
        method(invalid_input)


@pytest.mark.integration
@pytest.mark.parametrize(
    "method_name,invalid_input,error_match",
    [
        ("add_receipt_lines", None, "receipt_lines cannot be None"),
        ("add_receipt_lines", "not-a-list", "receipt_lines must be a list"),
        ("update_receipt_lines", None, "receipt_lines cannot be None"),
        ("update_receipt_lines", "not-a-list", "receipt_lines must be a list"),
        ("delete_receipt_lines", None, "receipt_lines cannot be None"),
        ("delete_receipt_lines", "not-a-list", "receipt_lines must be a list"),
    ],
)
def test_batch_receipt_line_validation_basic(
    dynamodb_table: Literal["MyMockedTable"],
    method_name: str,
    invalid_input: Any,
    error_match: str,
) -> None:
    """Tests basic validation for batch receipt line operations."""
    client = DynamoClient(dynamodb_table)
    method = getattr(client, method_name)

    with pytest.raises(EntityValidationError, match=error_match):
        method(invalid_input)


@pytest.mark.integration
@pytest.mark.parametrize(
    "method_name",
    [
        "add_receipt_lines",
        "update_receipt_lines",
        "delete_receipt_lines",
    ],
)
# pylint: disable=redefined-outer-name
def test_batch_receipt_line_validation_mixed_types(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_line: ReceiptLine,
    method_name: str,
) -> None:
    """Tests validation for batch operations with mixed types."""
    client = DynamoClient(dynamodb_table)
    method = getattr(client, method_name)

    mixed_list = [sample_receipt_line, "not-a-line", 123]

    with pytest.raises(
        EntityValidationError, match=r"receipt_lines\[1\] must be an instance of ReceiptLine"
    ):
        method(mixed_list)


@pytest.mark.integration
@pytest.mark.parametrize(
    "receipt_id,image_id,line_id,expected_error,error_match",
    [
        (
            None,
            FIXED_UUIDS[0],
            1,
            EntityValidationError,
            "receipt_id must be an integer",
        ),
        (
            "not-an-int",
            FIXED_UUIDS[0],
            1,
            EntityValidationError,
            "receipt_id must be an integer",
        ),
        (
            -1,
            FIXED_UUIDS[0],
            1,
            EntityValidationError,
            "receipt_id must be a positive integer",
        ),
        (1, None, 1, EntityValidationError, "image_id cannot be None"),
        (1, "not-a-uuid", 1, OperationError, "uuid must be a valid UUIDv4"),
        (
            1,
            FIXED_UUIDS[0],
            None,
            EntityValidationError,
            "line_id must be an integer",
        ),
        (
            1,
            FIXED_UUIDS[0],
            "not-an-int",
            EntityValidationError,
            "line_id must be an integer",
        ),
        (
            1,
            FIXED_UUIDS[0],
            -1,
            EntityValidationError,
            "line_id must be a positive integer",
        ),
    ],
)
# pylint: disable=too-many-arguments
def test_get_receipt_line_parameter_validation(
    dynamodb_table: Literal["MyMockedTable"],
    receipt_id: Any,
    image_id: Any,
    line_id: Any,
    expected_error: Type[Exception],
    error_match: str,
) -> None:
    """Tests parameter validation for get_receipt_line."""
    client = DynamoClient(dynamodb_table)

    with pytest.raises(expected_error, match=error_match):
        client.get_receipt_line(receipt_id, image_id, line_id)


# -------------------------------------------------------------------
#           INDICES AND KEYS VALIDATION TESTS
# -------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize(
    "indices,expected_error,error_match",
    [
        (None, EntityValidationError, "indices cannot be None"),
        ("not-a-list", EntityValidationError, "indices must be a list"),
        (
            [("valid", 1, 1), "not-a-tuple"],
            EntityValidationError,
            "indices must be a list of tuples",
        ),
        ([("valid", 1)], EntityValidationError, "tuples with 3 elements"),
        (
            [(123, 1, 1)],
            EntityValidationError,
            "First element of tuple must be a string",
        ),
        (
            [("not-a-uuid", 1, 1)],
            OperationError,
            "uuid must be a valid UUIDv4",
        ),
        (
            [(FIXED_UUIDS[0], "not-int", 1)],
            EntityValidationError,
            "Second element of tuple must be an integer",
        ),
        (
            [(FIXED_UUIDS[0], 1, "not-int")],
            EntityValidationError,
            "Third element of tuple must be an integer",
        ),
    ],
)
def test_get_receipt_lines_by_indices_validation(
    dynamodb_table: Literal["MyMockedTable"],
    indices: Any,
    expected_error: Type[Exception],
    error_match: str,
) -> None:
    """Tests validation for get_receipt_lines_by_indices."""
    client = DynamoClient(dynamodb_table)

    with pytest.raises(expected_error, match=error_match):
        client.get_receipt_lines_by_indices(indices)


@pytest.mark.integration
@pytest.mark.parametrize(
    "keys,expected_error,error_match",
    [
        (None, EntityValidationError, "keys cannot be None"),
        ([], EntityValidationError, "keys cannot be None or empty"),
        ("not-a-list", EntityValidationError, "keys must be a list"),
        (
            [{"PK": {"S": "IMAGE#123"}}],
            EntityValidationError,
            "keys must contain 'PK' and 'SK'",
        ),
        (
            [
                {
                    "PK": {"S": "WRONG#123"},
                    "SK": {"S": "RECEIPT#00001#LINE#00001"},
                }
            ],
            EntityValidationError,
            "PK must start with 'IMAGE#'",
        ),
        (
            [{"PK": {"S": "IMAGE#123"}, "SK": {"S": "WRONG#00001"}}],
            EntityValidationError,
            "SK must start with 'RECEIPT#'",
        ),
        (
            [
                {
                    "PK": {"S": "IMAGE#123"},
                    "SK": {"S": "RECEIPT#001#LINE#00001"},
                }
            ],
            EntityValidationError,
            "SK must contain a 5-digit receipt ID",
        ),
        (
            [
                {
                    "PK": {"S": "IMAGE#123"},
                    "SK": {"S": "RECEIPT#00001#LINE#001"},
                }
            ],
            EntityValidationError,
            "SK must contain a 5-digit line ID",
        ),
    ],
)
def test_get_receipt_lines_by_keys_validation(
    dynamodb_table: Literal["MyMockedTable"],
    keys: Any,
    expected_error: Type[Exception],
    error_match: str,
) -> None:
    """Tests validation for get_receipt_lines_by_keys."""
    client = DynamoClient(dynamodb_table)

    with pytest.raises(expected_error, match=error_match):
        client.get_receipt_lines_by_keys(keys)


# -------------------------------------------------------------------
#           LIST OPERATIONS VALIDATION TESTS
# -------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize(
    "limit,error_match",
    [
        ("not-an-int", "limit must be an integer"),
        (-1, "limit must be greater than 0"),
        (0, "limit must be greater than 0"),
    ],
)
def test_list_receipt_lines_invalid_limit(
    dynamodb_table: Literal["MyMockedTable"],
    limit: Any,
    error_match: str,
) -> None:
    """Tests list_receipt_lines with invalid limit parameter."""
    client = DynamoClient(dynamodb_table)

    with pytest.raises(EntityValidationError, match=error_match):
        client.list_receipt_lines(limit=limit)


@pytest.mark.integration
def test_list_receipt_lines_invalid_last_evaluated_key(
    dynamodb_table: Literal["MyMockedTable"],
) -> None:
    """Tests list_receipt_lines with invalid last_evaluated_key."""
    client = DynamoClient(dynamodb_table)

    with pytest.raises(
        EntityValidationError, match="last_evaluated_key must be a dictionary"
    ):
        client.list_receipt_lines(last_evaluated_key="not-a-dict")


@pytest.mark.integration
@pytest.mark.parametrize(
    "embedding_status,expected_error,error_match",
    [
        (None, EntityValidationError, "embedding_status must be an instance"),
        (123, EntityValidationError, "embedding_status must be an instance"),
        (
            "INVALID_STATUS",
            EntityValidationError,
            "must be a valid EmbeddingStatus",
        ),
    ],
)
def test_list_receipt_lines_by_embedding_status_validation(
    dynamodb_table: Literal["MyMockedTable"],
    embedding_status: Any,
    expected_error: Type[Exception],
    error_match: str,
) -> None:
    """Tests validation for list_receipt_lines_by_embedding_status."""
    client = DynamoClient(dynamodb_table)

    with pytest.raises(expected_error, match=error_match):
        client.list_receipt_lines_by_embedding_status(embedding_status)


# -------------------------------------------------------------------
#           CONDITIONAL CHECK FAILED TESTS
# -------------------------------------------------------------------


@pytest.mark.integration
# pylint: disable=redefined-outer-name
def test_add_receipt_line_conditional_check_failed(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_line: ReceiptLine,
    mocker: MockerFixture,
) -> None:
    """Tests ConditionalCheckFailedException handling for add."""
    client = DynamoClient(dynamodb_table)

    # pylint: disable=protected-access
    mock_put = mocker.patch.object(
        client._client,
        "put_item",
        side_effect=ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException"}}, "PutItem"
        ),
    )

    with pytest.raises(
        EntityAlreadyExistsError, match="receipt_line already exists"
    ):
        client.add_receipt_line(sample_receipt_line)

    mock_put.assert_called_once()


@pytest.mark.integration
# pylint: disable=redefined-outer-name
def test_update_receipt_line_conditional_check_failed(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_line: ReceiptLine,
    mocker: MockerFixture,
) -> None:
    """Tests ConditionalCheckFailedException handling for update."""
    client = DynamoClient(dynamodb_table)

    # pylint: disable=protected-access
    mock_put = mocker.patch.object(
        client._client,
        "put_item",
        side_effect=ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException"}}, "PutItem"
        ),
    )

    with pytest.raises(
        EntityNotFoundError, match="receiptline not found during update_receipt_line"
    ):
        client.update_receipt_line(sample_receipt_line)

    mock_put.assert_called_once()


@pytest.mark.integration
# pylint: disable=redefined-outer-name
def test_delete_receipt_line_conditional_check_failed(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_line: ReceiptLine,
    mocker: MockerFixture,
) -> None:
    """Tests ConditionalCheckFailedException handling for delete."""
    client = DynamoClient(dynamodb_table)

    # pylint: disable=protected-access
    mock_delete = mocker.patch.object(
        client._client,
        "delete_item",
        side_effect=ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException"}},
            "DeleteItem",
        ),
    )

    with pytest.raises(
        EntityNotFoundError, match="not found during delete_receipt_line"
    ):
        client.delete_receipt_line(
            sample_receipt_line.receipt_id,
            sample_receipt_line.image_id,
            sample_receipt_line.line_id,
        )

    mock_delete.assert_called_once()


# -------------------------------------------------------------------
#           SUCCESS PATH TESTS
# -------------------------------------------------------------------


@pytest.mark.integration
# pylint: disable=redefined-outer-name
def test_add_receipt_line_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_line: ReceiptLine,
) -> None:
    """Tests successful add_receipt_line operation."""
    client = DynamoClient(dynamodb_table)

    # Act
    client.add_receipt_line(sample_receipt_line)

    # Assert
    retrieved = client.get_receipt_line(
        sample_receipt_line.receipt_id,
        sample_receipt_line.image_id,
        sample_receipt_line.line_id,
    )
    assert retrieved == sample_receipt_line


@pytest.mark.integration
# pylint: disable=redefined-outer-name
def test_add_receipt_line_duplicate_raises(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_line: ReceiptLine,
) -> None:
    """Tests that adding duplicate receipt line raises error."""
    client = DynamoClient(dynamodb_table)
    client.add_receipt_line(sample_receipt_line)

    with pytest.raises(EntityAlreadyExistsError, match="already exists"):
        client.add_receipt_line(sample_receipt_line)


@pytest.mark.integration
# pylint: disable=redefined-outer-name
def test_update_receipt_line_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_line: ReceiptLine,
) -> None:
    """Tests successful update_receipt_line operation."""
    client = DynamoClient(dynamodb_table)
    client.add_receipt_line(sample_receipt_line)

    # Modify some fields
    sample_receipt_line.text = "Updated line text"
    sample_receipt_line.confidence = 0.99

    # Update
    client.update_receipt_line(sample_receipt_line)

    # Verify
    retrieved = client.get_receipt_line(
        sample_receipt_line.receipt_id,
        sample_receipt_line.image_id,
        sample_receipt_line.line_id,
    )
    assert retrieved.text == "Updated line text"
    assert retrieved.confidence == 0.99


@pytest.mark.integration
# pylint: disable=redefined-outer-name
def test_delete_receipt_line_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_line: ReceiptLine,
) -> None:
    """Tests successful delete_receipt_line operation."""
    client = DynamoClient(dynamodb_table)
    client.add_receipt_line(sample_receipt_line)

    # Delete
    client.delete_receipt_line(
        sample_receipt_line.receipt_id,
        sample_receipt_line.image_id,
        sample_receipt_line.line_id,
    )

    # Verify
    with pytest.raises(
        EntityNotFoundError, match="ReceiptLine with.*not found"
    ):
        client.get_receipt_line(
            sample_receipt_line.receipt_id,
            sample_receipt_line.image_id,
            sample_receipt_line.line_id,
        )


@pytest.mark.integration
# pylint: disable=redefined-outer-name
def test_get_receipt_line_not_found(
    dynamodb_table: Literal["MyMockedTable"],
    unique_image_id: str,
) -> None:
    """Tests get_receipt_line when line doesn't exist."""
    client = DynamoClient(dynamodb_table)

    with pytest.raises(
        EntityNotFoundError,
        match="ReceiptLine with.*not found",
    ):
        client.get_receipt_line(999, unique_image_id, 999)


# -------------------------------------------------------------------
#           BATCH OPERATIONS SUCCESS TESTS
# -------------------------------------------------------------------


@pytest.mark.integration
# pylint: disable=redefined-outer-name
def test_add_receipt_lines_success(
    dynamodb_table: Literal["MyMockedTable"],
    unique_image_id: str,
) -> None:
    """Tests successful batch add operation."""
    client = DynamoClient(dynamodb_table)

    lines = [
        ReceiptLine(
            receipt_id=1,
            image_id=unique_image_id,
            line_id=i,
            text=f"Line {i}",
            bounding_box={"x": 0.0, "y": i * 0.1, "width": 1.0, "height": 0.1},
            top_left={"x": 0, "y": i * 0.1},
            top_right={"x": 1, "y": i * 0.1},
            bottom_left={"x": 0, "y": (i + 1) * 0.1},
            bottom_right={"x": 1, "y": (i + 1) * 0.1},
            angle_degrees=0,
            angle_radians=0,
            confidence=0.95 + i * 0.01,
        )
        for i in range(1, 4)
    ]

    client.add_receipt_lines(lines)

    # Verify all were added
    for line in lines:
        retrieved = client.get_receipt_line(
            line.receipt_id, line.image_id, line.line_id
        )
        assert retrieved == line


@pytest.mark.integration
# pylint: disable=redefined-outer-name
def test_add_receipt_lines_unprocessed_items_retry(
    dynamodb_table: Literal["MyMockedTable"],
    unique_image_id: str,
    mocker: MockerFixture,
) -> None:
    """Tests that transact_write_items is called for batch operations."""
    client = DynamoClient(dynamodb_table)

    lines = []
    first_line = ReceiptLine(
        receipt_id=1,
        image_id=unique_image_id,
        line_id=1,
        text="First line",
        bounding_box={"x": 0, "y": 0, "width": 1, "height": 0.1},
        top_left={"x": 0, "y": 0},
        top_right={"x": 1, "y": 0},
        bottom_left={"x": 0, "y": 0.1},
        bottom_right={"x": 1, "y": 0.1},
        angle_degrees=0,
        angle_radians=0,
        confidence=0.98,
    )
    lines.append(first_line)

    second_line = ReceiptLine(
        **{
            **first_line.__dict__,
            "line_id": 2,
            "text": "Second line",
        }
    )
    lines.append(second_line)

    # pylint: disable=protected-access
    real_transact_write_items = client._client.transact_write_items
    call_count = {"value": 0}

    def custom_side_effect(*args, **kwargs):
        call_count["value"] += 1
        return real_transact_write_items(*args, **kwargs)

    # pylint: disable=protected-access
    mocker.patch.object(
        client._client,
        "transact_write_items",
        side_effect=custom_side_effect,
    )

    client.add_receipt_lines(lines)

    assert call_count["value"] == 1, "Should have called transact_write_items once."


@pytest.mark.integration
# pylint: disable=redefined-outer-name
def test_update_receipt_lines_success(
    dynamodb_table: Literal["MyMockedTable"],
    unique_image_id: str,
) -> None:
    """Tests successful batch update operation."""
    client = DynamoClient(dynamodb_table)

    # First add lines
    lines = [
        ReceiptLine(
            receipt_id=1,
            image_id=unique_image_id,
            line_id=i,
            text=f"Original line {i}",
            bounding_box={"x": 0, "y": i * 0.1, "width": 1, "height": 0.1},
            top_left={"x": 0, "y": i * 0.1},
            top_right={"x": 1, "y": i * 0.1},
            bottom_left={"x": 0, "y": (i + 1) * 0.1},
            bottom_right={"x": 1, "y": (i + 1) * 0.1},
            angle_degrees=0,
            angle_radians=0,
            confidence=0.95,
        )
        for i in range(1, 4)
    ]
    client.add_receipt_lines(lines)

    # Update them
    for line in lines:
        line.text = f"Updated line {line.line_id}"
        line.confidence = 0.99

    client.update_receipt_lines(lines)

    # Verify updates
    for line in lines:
        retrieved = client.get_receipt_line(
            line.receipt_id, line.image_id, line.line_id
        )
        assert retrieved.text == f"Updated line {line.line_id}"
        assert retrieved.confidence == 0.99


@pytest.mark.integration
# pylint: disable=redefined-outer-name
def test_delete_receipt_lines_success(
    dynamodb_table: Literal["MyMockedTable"],
    unique_image_id: str,
) -> None:
    """Tests successful batch delete operation."""
    client = DynamoClient(dynamodb_table)

    # First add lines
    lines = [
        ReceiptLine(
            receipt_id=1,
            image_id=unique_image_id,
            line_id=i,
            text=f"Line to delete {i}",
            bounding_box={"x": 0, "y": i * 0.1, "width": 1, "height": 0.1},
            top_left={"x": 0, "y": i * 0.1},
            top_right={"x": 1, "y": i * 0.1},
            bottom_left={"x": 0, "y": (i + 1) * 0.1},
            bottom_right={"x": 1, "y": (i + 1) * 0.1},
            angle_degrees=0,
            angle_radians=0,
            confidence=0.95,
        )
        for i in range(1, 4)
    ]
    client.add_receipt_lines(lines)

    # Delete them
    client.delete_receipt_lines(lines)

    # Verify deletion
    for line in lines:
        with pytest.raises(EntityNotFoundError):
            client.get_receipt_line(
                line.receipt_id, line.image_id, line.line_id
            )


# -------------------------------------------------------------------
#           GET BY INDICES/KEYS TESTS
# -------------------------------------------------------------------


@pytest.mark.integration
# pylint: disable=redefined-outer-name
def test_get_receipt_lines_by_indices_success(
    dynamodb_table: Literal["MyMockedTable"],
    unique_image_id: str,
) -> None:
    """Tests successful get_receipt_lines_by_indices operation."""
    client = DynamoClient(dynamodb_table)

    # Add test lines
    lines = []
    for i in range(1, 4):
        line = ReceiptLine(
            receipt_id=1,
            image_id=unique_image_id,
            line_id=i,
            text=f"Line {i}",
            bounding_box={"x": 0, "y": i * 0.1, "width": 1, "height": 0.1},
            top_left={"x": 0, "y": i * 0.1},
            top_right={"x": 1, "y": i * 0.1},
            bottom_left={"x": 0, "y": (i + 1) * 0.1},
            bottom_right={"x": 1, "y": (i + 1) * 0.1},
            angle_degrees=0,
            angle_radians=0,
            confidence=0.95,
        )
        lines.append(line)
        client.add_receipt_line(line)

    # Get by indices
    indices = [(unique_image_id, 1, i) for i in range(1, 4)]
    retrieved = client.get_receipt_lines_by_indices(indices)

    assert len(retrieved) == 3
    for line in lines:
        assert line in retrieved


@pytest.mark.integration
# pylint: disable=redefined-outer-name
def test_get_receipt_lines_by_keys_success(
    dynamodb_table: Literal["MyMockedTable"],
    unique_image_id: str,
) -> None:
    """Tests successful get_receipt_lines_by_keys operation."""
    client = DynamoClient(dynamodb_table)

    # Add test lines
    lines = []
    for i in range(1, 4):
        line = ReceiptLine(
            receipt_id=1,
            image_id=unique_image_id,
            line_id=i,
            text=f"Line {i}",
            bounding_box={"x": 0, "y": i * 0.1, "width": 1, "height": 0.1},
            top_left={"x": 0, "y": i * 0.1},
            top_right={"x": 1, "y": i * 0.1},
            bottom_left={"x": 0, "y": (i + 1) * 0.1},
            bottom_right={"x": 1, "y": (i + 1) * 0.1},
            angle_degrees=0,
            angle_radians=0,
            confidence=0.95,
        )
        lines.append(line)
        client.add_receipt_line(line)

    # Get by keys
    keys = [
        {
            "PK": {"S": f"IMAGE#{unique_image_id}"},
            "SK": {"S": f"RECEIPT#00001#LINE#{i:05d}"},
        }
        for i in range(1, 4)
    ]
    retrieved = client.get_receipt_lines_by_keys(keys)

    assert len(retrieved) == 3
    for line in lines:
        assert line in retrieved


# -------------------------------------------------------------------
#           LIST OPERATIONS TESTS
# -------------------------------------------------------------------


@pytest.mark.integration
# pylint: disable=redefined-outer-name
def test_list_receipt_lines_with_pagination(
    dynamodb_table: Literal["MyMockedTable"],
    unique_image_id: str,
) -> None:
    """Tests list_receipt_lines with pagination."""
    client = DynamoClient(dynamodb_table)

    # Add multiple lines
    for i in range(1, 6):
        line = ReceiptLine(
            receipt_id=1,
            image_id=unique_image_id,
            line_id=i,
            text=f"Line {i}",
            bounding_box={"x": 0, "y": i * 0.1, "width": 1, "height": 0.1},
            top_left={"x": 0, "y": i * 0.1},
            top_right={"x": 1, "y": i * 0.1},
            bottom_left={"x": 0, "y": (i + 1) * 0.1},
            bottom_right={"x": 1, "y": (i + 1) * 0.1},
            angle_degrees=0,
            angle_radians=0,
            confidence=0.95,
        )
        client.add_receipt_line(line)

    # List with limit
    first_page, last_key = client.list_receipt_lines(limit=3)
    assert len(first_page) == 3
    assert last_key is not None

    # Get next page
    second_page, last_key = client.list_receipt_lines(
        limit=3, last_evaluated_key=last_key
    )
    assert len(second_page) >= 2

    # Ensure no duplicates between pages
    first_ids = {(l.receipt_id, l.image_id, l.line_id) for l in first_page}
    second_ids = {(l.receipt_id, l.image_id, l.line_id) for l in second_page}
    assert first_ids.isdisjoint(second_ids)


@pytest.mark.integration
# pylint: disable=redefined-outer-name
def test_list_receipt_lines_no_limit(
    dynamodb_table: Literal["MyMockedTable"],
    unique_image_id: str,
) -> None:
    """Tests list_receipt_lines without limit."""
    client = DynamoClient(dynamodb_table)

    # Add a few lines
    for i in range(1, 4):
        line = ReceiptLine(
            receipt_id=1,
            image_id=unique_image_id,
            line_id=i,
            text=f"Line {i}",
            bounding_box={"x": 0, "y": i * 0.1, "width": 1, "height": 0.1},
            top_left={"x": 0, "y": i * 0.1},
            top_right={"x": 1, "y": i * 0.1},
            bottom_left={"x": 0, "y": (i + 1) * 0.1},
            bottom_right={"x": 1, "y": (i + 1) * 0.1},
            angle_degrees=0,
            angle_radians=0,
            confidence=0.95,
        )
        client.add_receipt_line(line)

    # List without limit
    lines, _ = client.list_receipt_lines()

    assert len(lines) >= 3
    # last_key might be None or contain pagination info


@pytest.mark.integration
# pylint: disable=redefined-outer-name
def test_list_receipt_lines_by_embedding_status(
    dynamodb_table: Literal["MyMockedTable"],
    unique_image_id: str,
) -> None:
    """Tests list_receipt_lines_by_embedding_status."""
    client = DynamoClient(dynamodb_table)

    # Add lines with different embedding statuses
    statuses = [
        EmbeddingStatus.NONE,
        EmbeddingStatus.SUCCESS,
        EmbeddingStatus.FAILED,
    ]

    for i, status in enumerate(statuses, 1):
        line = ReceiptLine(
            receipt_id=1,
            image_id=unique_image_id,
            line_id=i,
            text=f"Line {i}",
            bounding_box={"x": 0, "y": i * 0.1, "width": 1, "height": 0.1},
            top_left={"x": 0, "y": i * 0.1},
            top_right={"x": 1, "y": i * 0.1},
            bottom_left={"x": 0, "y": (i + 1) * 0.1},
            bottom_right={"x": 1, "y": (i + 1) * 0.1},
            angle_degrees=0,
            angle_radians=0,
            confidence=0.95,
            embedding_status=status,
        )
        client.add_receipt_line(line)

    # Query by status
    not_embedded = client.list_receipt_lines_by_embedding_status(
        EmbeddingStatus.NONE
    )
    embedded = client.list_receipt_lines_by_embedding_status(
        EmbeddingStatus.SUCCESS
    )

    # Verify results
    assert any(
        l.embedding_status == EmbeddingStatus.NONE for l in not_embedded
    )
    assert any(l.embedding_status == EmbeddingStatus.SUCCESS for l in embedded)


@pytest.mark.integration
# pylint: disable=redefined-outer-name
def test_list_receipt_lines_from_receipt(
    dynamodb_table: Literal["MyMockedTable"],
    unique_image_id: str,
) -> None:
    """Tests list_receipt_lines_from_receipt filtering."""
    client = DynamoClient(dynamodb_table)

    # Add lines for different receipts
    # Lines for receipt 1
    for i in range(1, 3):
        line = ReceiptLine(
            receipt_id=1,
            image_id=unique_image_id,
            line_id=i,
            text=f"Receipt 1 Line {i}",
            bounding_box={"x": 0, "y": i * 0.1, "width": 1, "height": 0.1},
            top_left={"x": 0, "y": i * 0.1},
            top_right={"x": 1, "y": i * 0.1},
            bottom_left={"x": 0, "y": (i + 1) * 0.1},
            bottom_right={"x": 1, "y": (i + 1) * 0.1},
            angle_degrees=0,
            angle_radians=0,
            confidence=0.95,
        )
        client.add_receipt_line(line)

    # Line for receipt 2
    other_line = ReceiptLine(
        receipt_id=2,
        image_id=unique_image_id,
        line_id=10,
        text="Receipt 2 Line",
        bounding_box={"x": 0.2, "y": 0.2, "width": 0.1, "height": 0.1},
        top_left={"x": 0.2, "y": 0.2},
        top_right={"x": 0.3, "y": 0.2},
        bottom_left={"x": 0.2, "y": 0.3},
        bottom_right={"x": 0.3, "y": 0.3},
        angle_degrees=10,
        angle_radians=0.17453,
        confidence=0.99,
    )
    client.add_receipt_line(other_line)

    # Query for receipt 1 only
    receipt1_lines = client.list_receipt_lines_from_receipt(
        receipt_id=1, image_id=unique_image_id
    )

    # Verify filtering
    assert len(receipt1_lines) == 2
    assert all(l.receipt_id == 1 for l in receipt1_lines)
    assert other_line not in receipt1_lines
