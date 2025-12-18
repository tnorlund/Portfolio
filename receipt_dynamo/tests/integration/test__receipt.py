# pylint: disable=too-many-lines
"""
Parameterized integration tests for receipt operations.
This file contains refactored tests using pytest.mark.parametrize to reduce
code duplication.
"""

from datetime import datetime
from typing import Any, Literal, Type
from uuid import uuid4

import pytest
from botocore.exceptions import ClientError
from pytest_mock import MockerFixture

from receipt_dynamo import (
    DynamoClient,
    Image,
    Receipt,
    ReceiptLetter,
    ReceiptWord,
    ReceiptWordLabel,
)
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
#                        FIXTURES
# -------------------------------------------------------------------


@pytest.fixture(name="unique_image_id")
def _unique_image_id() -> str:
    """Provides a unique IMAGE_ID for each test to avoid conflicts."""
    return str(uuid4())


@pytest.fixture(name="sample_receipt")
def _sample_receipt(unique_image_id: str) -> Receipt:
    """Provides a sample Receipt for testing."""
    return Receipt(
        receipt_id=1,
        image_id=unique_image_id,
        width=10,
        height=20,
        timestamp_added=datetime.now().isoformat(),
        raw_s3_bucket="bucket",
        raw_s3_key="key",
        top_left={"x": 0, "y": 0},
        top_right={"x": 10, "y": 0},
        bottom_left={"x": 0, "y": 20},
        bottom_right={"x": 10, "y": 20},
        sha256="sha256",
    )


@pytest.fixture(name="sample_receipt_word")
def _sample_receipt_word(unique_image_id: str) -> ReceiptWord:
    """Provides a sample ReceiptWord for testing."""
    return ReceiptWord(
        image_id=unique_image_id,
        receipt_id=1,
        line_id=1,
        word_id=1,
        text="example-word",
        bounding_box={"x": 0, "y": 0, "width": 10, "height": 20},
        top_left={"x": 0, "y": 0},
        top_right={"x": 10, "y": 0},
        bottom_left={"x": 0, "y": 20},
        bottom_right={"x": 10, "y": 20},
        angle_degrees=0,
        angle_radians=0,
        confidence=1,
    )


@pytest.fixture(name="sample_receipt_words")
def _sample_receipt_words(unique_image_id: str) -> list[ReceiptWord]:
    """Provides a list of sample ReceiptWords for testing."""
    return [
        ReceiptWord(
            image_id=unique_image_id,
            receipt_id=1,
            line_id=1,
            word_id=1,
            text="example-word",
            bounding_box={"x": 0, "y": 0, "width": 10, "height": 20},
            top_left={"x": 0, "y": 0},
            top_right={"x": 10, "y": 0},
            bottom_left={"x": 0, "y": 20},
            bottom_right={"x": 10, "y": 20},
            angle_degrees=0,
            angle_radians=0,
            confidence=1,
        ),
        ReceiptWord(
            image_id=unique_image_id,
            receipt_id=1,
            line_id=1,
            word_id=2,
            text="Store",
            bounding_box={"x": 0, "y": 0, "width": 10, "height": 20},
            top_left={"x": 0, "y": 0},
            top_right={"x": 10, "y": 0},
            bottom_left={"x": 0, "y": 20},
            bottom_right={"x": 10, "y": 20},
            angle_degrees=0,
            angle_radians=0,
            confidence=1,
        ),
    ]


@pytest.fixture(name="sample_receipt_word_labels")
def _sample_receipt_word_labels(
    unique_image_id: str,
) -> list[ReceiptWordLabel]:
    """Provides a list of sample ReceiptWordLabels for testing."""
    return [
        ReceiptWordLabel(
            image_id=unique_image_id,
            receipt_id=1,
            line_id=1,
            word_id=2,
            label="store_name",
            reasoning="This is a store name",
            timestamp_added=datetime.now().isoformat(),
        ),
    ]


@pytest.fixture(name="sample_receipt_letter")
def _sample_receipt_letter(unique_image_id: str) -> ReceiptLetter:
    """Provides a sample ReceiptLetter for testing."""
    return ReceiptLetter(
        image_id=unique_image_id,
        receipt_id=1,
        line_id=1,
        word_id=1,
        letter_id=1,
        text="X",
        bounding_box={"x": 0, "y": 0, "width": 5, "height": 10},
        top_left={"x": 0, "y": 0},
        top_right={"x": 5, "y": 0},
        bottom_left={"x": 0, "y": 10},
        bottom_right={"x": 5, "y": 10},
        angle_degrees=0,
        angle_radians=0,
        confidence=1,
    )


@pytest.fixture(name="sample_image")
def _sample_image(unique_image_id: str) -> Image:
    """Provides a sample Image for testing."""
    return Image(
        image_id=unique_image_id,
        width=640,
        height=480,
        timestamp_added=datetime.now().isoformat(),
        raw_s3_bucket="bucket",
        raw_s3_key="key",
    )


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


@pytest.mark.integration
@pytest.mark.parametrize("error_code,expected_exception,error_match", ERROR_SCENARIOS)
# pylint: disable=too-many-arguments
def test_add_receipt_client_errors(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt: Receipt,
    mocker: MockerFixture,
    error_code: str,
    expected_exception: Type[Exception],
    error_match: str,
) -> None:
    """Tests that add_receipt raises appropriate exceptions for various
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
        client.add_receipt(sample_receipt)
    mock_put.assert_called_once()


@pytest.mark.integration
@pytest.mark.parametrize("error_code,expected_exception,error_match", ERROR_SCENARIOS)
# pylint: disable=too-many-arguments
def test_update_receipt_client_errors(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt: Receipt,
    mocker: MockerFixture,
    error_code: str,
    expected_exception: Type[Exception],
    error_match: str,
) -> None:
    """
    Tests that update_receipt raises appropriate exceptions for various
    ClientError scenarios.
    """
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
        client.update_receipt(sample_receipt)
    mock_put.assert_called_once()


@pytest.mark.integration
@pytest.mark.parametrize("error_code,expected_exception,error_match", ERROR_SCENARIOS)
# pylint: disable=too-many-arguments
def test_delete_receipt_client_errors(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt: Receipt,
    mocker: MockerFixture,
    error_code: str,
    expected_exception: Type[Exception],
    error_match: str,
) -> None:
    """
    Tests that delete_receipt raises appropriate exceptions for various
    ClientError scenarios.
    """
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
        client.delete_receipt(sample_receipt)
    mock_delete.assert_called_once()


@pytest.mark.integration
@pytest.mark.parametrize("error_code,expected_exception,error_match", ERROR_SCENARIOS)
# pylint: disable=too-many-arguments
def test_get_receipt_client_errors(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt: Receipt,
    mocker: MockerFixture,
    error_code: str,
    expected_exception: Type[Exception],
    error_match: str,
) -> None:
    """
    Tests that get_receipt raises appropriate exceptions for various
    ClientError scenarios.
    """
    client = DynamoClient(dynamodb_table)
    # pylint: disable=protected-access
    mock_get = mocker.patch.object(
        client._client,
        "get_item",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": error_code,
                    "Message": f"Mocked {error_code}",
                }
            },
            "GetItem",
        ),
    )

    with pytest.raises(expected_exception, match=error_match):
        client.get_receipt(sample_receipt.image_id, sample_receipt.receipt_id)
    mock_get.assert_called_once()


# -------------------------------------------------------------------
#             PARAMETERIZED BATCH OPERATION CLIENT ERROR TESTS
# -------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize("error_code,expected_exception,error_match", ERROR_SCENARIOS)
# pylint: disable=too-many-arguments
def test_add_receipts_client_errors(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt: Receipt,
    mocker: MockerFixture,
    error_code: str,
    expected_exception: Type[Exception],
    error_match: str,
) -> None:
    """
    Tests that add_receipts raises appropriate exceptions for various
    ClientError scenarios.
    """
    client = DynamoClient(dynamodb_table)
    # pylint: disable=protected-access
    mock_batch = mocker.patch.object(
        client._client,
        "batch_write_item",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": error_code,
                    "Message": f"Mocked {error_code}",
                }
            },
            "BatchWriteItem",
        ),
    )

    with pytest.raises(expected_exception, match=error_match):
        client.add_receipts([sample_receipt])
    mock_batch.assert_called_once()


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,expected_exception,error_match",
    [
        (
            "ConditionalCheckFailedException",
            EntityNotFoundError,
            "Cannot update receipts: one or more receipts not found",
        ),
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
    ],
)
# pylint: disable=too-many-arguments
def test_update_receipts_client_errors(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt: Receipt,
    mocker: MockerFixture,
    error_code: str,
    expected_exception: Type[Exception],
    error_match: str,
) -> None:
    """
    Tests that update_receipts raises appropriate exceptions for various
    ClientError scenarios.
    """
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
        client.update_receipts([sample_receipt])
    mock_transact.assert_called_once()


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,expected_exception,error_match",
    [
        (
            "ConditionalCheckFailedException",
            EntityNotFoundError,
            "Cannot delete receipts: one or more receipts not found",
        ),
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
    ],
)
# pylint: disable=too-many-arguments
def test_delete_receipts_client_errors(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt: Receipt,
    mocker: MockerFixture,
    error_code: str,
    expected_exception: Type[Exception],
    error_match: str,
) -> None:
    """
    Tests that delete_receipts raises appropriate exceptions for various
    ClientError scenarios.
    """
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
        client.delete_receipts([sample_receipt])
    mock_transact.assert_called_once()


# -------------------------------------------------------------------
#              PARAMETERIZED INPUT VALIDATION TESTS
# -------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize(
    "method_name,invalid_input,error_match",
    [
        (
            "add_receipt",
            None,
            "Unexpected error during add_receipt: receipt cannot be None",
        ),
        (
            "add_receipt",
            "not-a-receipt",
            "Unexpected error during add_receipt: receipt "
            "must be an instance of Receipt",
        ),
        (
            "update_receipt",
            None,
            "Unexpected error during update_receipt: receipt cannot be None",
        ),
        (
            "update_receipt",
            "not-a-receipt",
            "Unexpected error during update_receipt: receipt "
            "must be an instance of Receipt",
        ),
        (
            "delete_receipt",
            None,
            "Unexpected error during delete_receipt: receipt cannot be None",
        ),
        (
            "delete_receipt",
            "not-a-receipt",
            "Unexpected error during delete_receipt: receipt "
            "must be an instance of Receipt",
        ),
    ],
)
def test_single_receipt_validation(
    dynamodb_table: Literal["MyMockedTable"],
    method_name: str,
    invalid_input: Any,
    error_match: str,
) -> None:
    """Tests that single receipt operations validate input correctly."""
    client = DynamoClient(dynamodb_table)
    method = getattr(client, method_name)

    with pytest.raises(OperationError, match=error_match):
        method(invalid_input)


@pytest.mark.integration
@pytest.mark.parametrize(
    "method_name,invalid_input,error_match",
    [
        (
            "add_receipts",
            None,
            "Unexpected error during add_receipts: receipts cannot be None",
        ),
        (
            "add_receipts",
            "not-a-list",
            "Unexpected error during add_receipts: receipts must be a list",
        ),
        (
            "update_receipts",
            None,
            "Unexpected error during update_entities: receipts cannot be None",
        ),
        (
            "update_receipts",
            "not-a-list",
            "Unexpected error during update_entities: receipts must be a list",
        ),
        (
            "delete_receipts",
            None,
            "Unexpected error during delete_receipts: receipts cannot be None",
        ),
        (
            "delete_receipts",
            "not-a-list",
            "Unexpected error during delete_receipts: receipts must be a list",
        ),
    ],
)
def test_batch_receipt_validation_basic(
    dynamodb_table: Literal["MyMockedTable"],
    method_name: str,
    invalid_input: Any,
    error_match: str,
) -> None:
    """Tests that batch receipt operations validate basic input correctly."""
    client = DynamoClient(dynamodb_table)
    method = getattr(client, method_name)

    with pytest.raises(OperationError, match=error_match):
        method(invalid_input)


@pytest.mark.integration
@pytest.mark.parametrize(
    "method_name,error_match",
    [
        (
            "add_receipts",
            "Unexpected error during add_receipts: receipts "
            "must be a list of Receipt instances",
        ),
        (
            "update_receipts",
            "Unexpected error during update_entities: receipts "
            "must be a list of Receipt instances",
        ),
        (
            "delete_receipts",
            "Unexpected error during delete_receipts: receipts "
            "must be a list of Receipt instances",
        ),
    ],
)
def test_batch_receipt_validation_mixed_types(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt: Receipt,
    method_name: str,
    error_match: str,
) -> None:
    """Tests that batch receipt operations validate list contents correctly."""
    client = DynamoClient(dynamodb_table)
    method = getattr(client, method_name)

    with pytest.raises(OperationError, match=error_match):
        method([sample_receipt, "not-a-receipt"])


# -------------------------------------------------------------------
#           PARAMETERIZED GET_RECEIPT PARAMETER VALIDATION
# -------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize(
    "image_id,receipt_id,expected_error,error_match",
    [
        (None, 1, ValueError, "image_id cannot be None"),
        (
            "not-a-uuid",
            1,
            OperationError,
            "Unexpected error during get_receipt: uuid must be a valid UUIDv4",
        ),
        (
            "550e8400-e29b-41d4-a716-446655440001",
            # Fixed UUID for test consistency
            None,
            EntityValidationError,
            "receipt_id must be an integer",
        ),
        (
            "550e8400-e29b-41d4-a716-446655440002",
            # Fixed UUID for test consistency
            "not-an-int",
            EntityValidationError,
            "receipt_id must be an integer",
        ),
        (
            "550e8400-e29b-41d4-a716-446655440003",
            # Fixed UUID for test consistency
            -1,
            EntityValidationError,
            "receipt_id must be a positive integer",
        ),
    ],
)
def test_get_receipt_parameter_validation(
    dynamodb_table: Literal["MyMockedTable"],
    image_id: Any,
    receipt_id: Any,
    expected_error: Type[Exception],
    error_match: str,
) -> None:
    """Tests that get_receipt validates parameters correctly."""
    client = DynamoClient(dynamodb_table)

    with pytest.raises(expected_error, match=error_match):
        client.get_receipt(image_id, receipt_id)


# -------------------------------------------------------------------
#              SPECIAL CASE TESTS (ConditionalCheckFailed)
# -------------------------------------------------------------------


@pytest.mark.integration
def test_add_receipt_conditional_check_failed(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt: Receipt,
    mocker: MockerFixture,
) -> None:
    """
    Tests that add_receipt raises EntityAlreadyExistsError when receipt
    already exists.
    """
    client = DynamoClient(dynamodb_table)
    # pylint: disable=protected-access
    mock_put = mocker.patch.object(
        client._client,
        "put_item",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "ConditionalCheckFailedException",
                    "Message": "Item already exists",
                }
            },
            "PutItem",
        ),
    )

    with pytest.raises(EntityAlreadyExistsError, match="receipt already exists"):
        client.add_receipt(sample_receipt)
    mock_put.assert_called_once()


@pytest.mark.integration
def test_update_receipt_conditional_check_failed(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt: Receipt,
    mocker: MockerFixture,
) -> None:
    """
    Tests that update_receipt raises EntityNotFoundError when receipt does
    not exist.
    """
    client = DynamoClient(dynamodb_table)
    # pylint: disable=protected-access
    mock_put = mocker.patch.object(
        client._client,
        "put_item",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "ConditionalCheckFailedException",
                    "Message": "Item does not exist",
                }
            },
            "PutItem",
        ),
    )

    with pytest.raises(
        EntityNotFoundError, match="receipt not found during update_receipt"
    ):
        client.update_receipt(sample_receipt)
    mock_put.assert_called_once()


@pytest.mark.integration
def test_delete_receipt_conditional_check_failed(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt: Receipt,
    mocker: MockerFixture,
) -> None:
    """
    Tests that delete_receipt raises EntityNotFoundError when receipt does
    not exist.
    """
    client = DynamoClient(dynamodb_table)
    # pylint: disable=protected-access
    mock_delete = mocker.patch.object(
        client._client,
        "delete_item",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "ConditionalCheckFailedException",
                    "Message": "Item does not exist",
                }
            },
            "DeleteItem",
        ),
    )

    with pytest.raises(
        EntityNotFoundError, match="receipt not found during delete_receipt"
    ):
        client.delete_receipt(sample_receipt)
    mock_delete.assert_called_once()


# -------------------------------------------------------------------
#           LIST OPERATIONS ERROR SCENARIOS
# -------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,expected_exception,error_match",
    [
        (
            "ResourceNotFoundException",
            OperationError,
            "DynamoDB resource not found",
        ),
        (
            "ProvisionedThroughputExceededException",
            DynamoDBThroughputError,
            "Throughput exceeded",
        ),
        ("ValidationException", EntityValidationError, "Validation error"),
        ("InternalServerError", DynamoDBServerError, "DynamoDB server error"),
        ("UnknownError", DynamoDBError, "DynamoDB error during"),
    ],
)
def test_list_receipts_client_errors(
    dynamodb_table: Literal["MyMockedTable"],
    mocker: MockerFixture,
    error_code: str,
    expected_exception: Type[Exception],
    error_match: str,
) -> None:
    """
    Tests that list_receipts raises appropriate exceptions for various
    ClientError scenarios.
    """
    client = DynamoClient(dynamodb_table)
    # pylint: disable=protected-access
    mock_query = mocker.patch.object(
        client._client,
        "query",
        side_effect=ClientError(
            {"Error": {"Code": error_code}},
            "Query",
        ),
    )

    with pytest.raises(expected_exception, match=error_match):
        client.list_receipts()
    mock_query.assert_called_once()


# -------------------------------------------------------------------
#                      SUCCESS PATH TESTS
# -------------------------------------------------------------------


@pytest.mark.integration
def test_add_receipt_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt: Receipt,
) -> None:
    """Tests the happy path of add_receipt."""
    client = DynamoClient(dynamodb_table)
    client.add_receipt(sample_receipt)

    # Verify the receipt in DynamoDB
    retrieved = client.get_receipt(sample_receipt.image_id, sample_receipt.receipt_id)
    assert retrieved == sample_receipt, "Stored and retrieved receipts should match."


@pytest.mark.integration
def test_add_receipt_duplicate_raises(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt: Receipt,
) -> None:
    """
    Tests that add_receipt raises EntityAlreadyExistsError when the receipt
    already exists.
    """
    client = DynamoClient(dynamodb_table)
    client.add_receipt(sample_receipt)

    with pytest.raises(EntityAlreadyExistsError, match="receipt already exists"):
        client.add_receipt(sample_receipt)


@pytest.mark.integration
def test_update_receipt_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt: Receipt,
) -> None:
    """Tests happy path for update_receipt."""
    client = DynamoClient(dynamodb_table)
    client.add_receipt(sample_receipt)

    # Modify something
    sample_receipt.raw_s3_key = "new/path"
    client.update_receipt(sample_receipt)

    updated = client.get_receipt(sample_receipt.image_id, sample_receipt.receipt_id)
    assert updated.raw_s3_key == "new/path"


@pytest.mark.integration
def test_delete_receipt_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt: Receipt,
) -> None:
    """Tests happy path for delete_receipt."""
    client = DynamoClient(dynamodb_table)
    client.add_receipt(sample_receipt)

    client.delete_receipt(sample_receipt)
    receipts, _ = client.list_receipts()
    assert sample_receipt not in receipts, "Receipt should be deleted."


@pytest.mark.integration
def test_get_receipt_not_found(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt: Receipt,
) -> None:
    """Tests get_receipt raises EntityNotFoundError if receipt not found."""
    client = DynamoClient(dynamodb_table)
    with pytest.raises(
        EntityNotFoundError,
        match="receipt with receipt_id=.* and image_id=.* does not exist.",
    ):
        client.get_receipt(sample_receipt.image_id, sample_receipt.receipt_id)


# -------------------------------------------------------------------
#                      BATCH OPERATION TESTS
# -------------------------------------------------------------------


@pytest.mark.integration
def test_add_receipts_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt: Receipt,
) -> None:
    """Tests the happy path of add_receipts (batch write)."""
    client = DynamoClient(dynamodb_table)
    receipts = [sample_receipt]
    second_receipt = Receipt(
        **{
            **sample_receipt.__dict__,
            "receipt_id": sample_receipt.receipt_id + 1,
        }
    )
    receipts.append(second_receipt)

    client.add_receipts(receipts)

    stored, _ = client.list_receipts()
    assert len(stored) == 2
    assert sample_receipt in stored
    assert second_receipt in stored


@pytest.mark.integration
def test_add_receipts_unprocessed_items_retry(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt: Receipt,
    mocker: MockerFixture,
) -> None:
    """
    Tests add_receipts retry logic when DynamoDB returns unprocessed
    items.
    """
    client = DynamoClient(dynamodb_table)
    receipts = [sample_receipt]
    second_receipt = Receipt(
        **{
            **sample_receipt.__dict__,
            "receipt_id": sample_receipt.receipt_id + 1,
        }
    )
    receipts.append(second_receipt)

    # pylint: disable=protected-access
    real_batch_write_item = client._client.batch_write_item
    call_count = {"value": 0}

    def custom_side_effect(*args, **kwargs):
        call_count["value"] += 1
        real_batch_write_item(*args, **kwargs)
        # On first call, pretend second receipt was unprocessed
        if call_count["value"] == 1:
            return {
                "UnprocessedItems": {
                    client.table_name: [
                        {"PutRequest": {"Item": second_receipt.to_item()}}
                    ]
                }
            }
        return {"UnprocessedItems": {}}

    # pylint: disable=protected-access
    mocker.patch.object(
        client._client,
        "batch_write_item",
        side_effect=custom_side_effect,
    )
    client.add_receipts(receipts)

    assert call_count["value"] == 2, "Should have retried once."

    stored, _ = client.list_receipts()
    assert len(stored) == 2
    assert sample_receipt in stored
    assert second_receipt in stored


@pytest.mark.integration
def test_update_receipts_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt: Receipt,
) -> None:
    """Tests happy path of update_receipts (batch write)."""
    client = DynamoClient(dynamodb_table)
    r1 = sample_receipt
    r2 = Receipt(
        **{
            **sample_receipt.__dict__,
            "receipt_id": sample_receipt.receipt_id + 1,
        }
    )
    client.add_receipts([r1, r2])

    # Now update them
    r1.raw_s3_key = "updated/path/1"
    r2.raw_s3_key = "updated/path/2"
    client.update_receipts([r1, r2])

    stored, _ = client.list_receipts()
    assert len(stored) == 2
    # Confirm the updated s3_keys
    for item in stored:
        if item.receipt_id == r1.receipt_id:
            assert item.raw_s3_key == "updated/path/1"
        else:
            assert item.raw_s3_key == "updated/path/2"


@pytest.mark.integration
def test_delete_receipts_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt: Receipt,
) -> None:
    """Tests happy path for delete_receipts."""
    client = DynamoClient(dynamodb_table)
    r1 = sample_receipt
    r2 = Receipt(
        **{
            **sample_receipt.__dict__,
            "receipt_id": sample_receipt.receipt_id + 1,
        }
    )
    client.add_receipts([r1, r2])

    client.delete_receipts([r1, r2])
    receipts, _ = client.list_receipts()
    assert not receipts, "All receipts should be deleted."


# -------------------------------------------------------------------
#              GET_RECEIPT_DETAILS AND RELATED TESTS
# -------------------------------------------------------------------


@pytest.mark.integration
def test_get_receipt_details_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt: Receipt,
    sample_receipt_word: ReceiptWord,
    sample_receipt_letter: ReceiptLetter,
) -> None:
    """
    Tests get_receipt_details retrieves a receipt with its associated
    words and letters.
    """
    client = DynamoClient(dynamodb_table)
    client.add_receipt(sample_receipt)
    client.add_receipt_words([sample_receipt_word])
    client.add_receipt_letters([sample_receipt_letter])

    details = client.get_receipt_details(
        sample_receipt.image_id, sample_receipt.receipt_id
    )

    (
        r,
        lines,
        words,
        letters,
        _,  # labels not used in this test
    ) = details

    assert r == sample_receipt
    assert len(words) == 1 and words[0] == sample_receipt_word
    assert len(letters) == 1 and letters[0] == sample_receipt_letter
    assert lines == [], "No lines were added in this test, so expect an empty list."


# -------------------------------------------------------------------
#                      LIST OPERATIONS TESTS
# -------------------------------------------------------------------


@pytest.mark.integration
def test_list_receipts_no_limit(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt: Receipt,
) -> None:
    """Tests list_receipts retrieves all receipts without a limit."""
    client = DynamoClient(dynamodb_table)
    client.add_receipt(sample_receipt)
    r2 = Receipt(
        **{
            **sample_receipt.__dict__,
            "receipt_id": sample_receipt.receipt_id + 1,
        }
    )
    client.add_receipt(r2)

    receipts, lek = client.list_receipts()
    assert len(receipts) == 2
    assert sample_receipt in receipts
    assert r2 in receipts
    assert lek is None, "Should have no pagination key if all items are fetched."


@pytest.mark.integration
def test_list_receipts_with_pagination(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt: Receipt,
    mocker: MockerFixture,
) -> None:
    """Tests list_receipts handles pagination correctly with a limit."""
    client = DynamoClient(dynamodb_table)

    # Fake responses
    first_page = {
        "Items": [sample_receipt.to_item()],
        "LastEvaluatedKey": {"dummy": "key"},
    }
    second_page = {"Items": [sample_receipt.to_item()]}

    # pylint: disable=protected-access
    mock_query = mocker.patch.object(
        client._client, "query", side_effect=[first_page, second_page]
    )

    receipts, lek = client.list_receipts(limit=10)
    assert len(receipts) == 2  # 1 from first page, 1 from second page
    assert lek is None
    assert mock_query.call_count == 2


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_limit,error_match",
    [
        ("not-an-int", "Limit must be an integer"),
        (0, "Limit must be greater than 0"),
        (-1, "Limit must be greater than 0"),
    ],
)
def test_list_receipts_invalid_limit(
    dynamodb_table: Literal["MyMockedTable"],
    invalid_limit: Any,
    error_match: str,
) -> None:
    """Tests that list_receipts validates limit parameter."""
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match=error_match):
        client.list_receipts(limit=invalid_limit)


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_lek",
    [
        "not-a-dict",
        {"PK": {"S": "IMAGE#start"}},  # Missing SK
        {"SK": {"S": "DUMMY_START"}},  # Missing PK
        {"PK": "not-a-dict", "SK": {"S": "DUMMY_START"}},  # Invalid PK format
        {"PK": {"S": "IMAGE#start"}, "SK": "not-a-dict"},  # Invalid SK format
    ],
)
def test_list_receipts_invalid_last_evaluated_key(
    dynamodb_table: Literal["MyMockedTable"],
    invalid_lek: Any,
) -> None:
    """
    Tests that list_receipts raises ValueError when last_evaluated_key is
    invalid.
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match="LastEvaluatedKey"):
        client.list_receipts(last_evaluated_key=invalid_lek)


# -------------------------------------------------------------------
#              LIST_RECEIPT_DETAILS TESTS
# -------------------------------------------------------------------


@pytest.mark.integration
def test_list_receipt_details_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt: Receipt,
    sample_receipt_words: list[ReceiptWord],
    sample_receipt_word_labels: list[ReceiptWordLabel],
) -> None:
    """Tests list_receipt_details retrieves receipt details successfully."""
    receipt = sample_receipt
    receipt_words = sample_receipt_words
    word_labels = sample_receipt_word_labels

    client = DynamoClient(dynamodb_table)
    client.add_receipt(receipt)
    client.add_receipt_words(receipt_words)
    client.add_receipt_word_labels(word_labels)

    result = client.list_receipt_details()

    # Verify the structure of the returned data
    assert hasattr(result, "summaries")
    assert hasattr(result, "last_evaluated_key")

    key = f"{receipt.image_id}_{receipt.receipt_id}"
    assert key in result
    assert len(result) == 1

    # Verify the contents of the receipt summary
    summary = result[key]
    assert summary.receipt == receipt
    assert summary.words == receipt_words
    assert summary.word_labels == word_labels

    # Verify pagination
    assert not result.has_more  # Since we only have one receipt


@pytest.mark.integration
def test_list_receipt_details_empty_results(
    dynamodb_table: Literal["MyMockedTable"],
) -> None:
    """
    Tests list_receipt_details returns empty results when no receipts
    exist.
    """
    client = DynamoClient(dynamodb_table)

    result = client.list_receipt_details()

    # Verify empty structure
    assert hasattr(result, "summaries")
    assert hasattr(result, "last_evaluated_key")
    assert len(result) == 0
    assert not result.has_more
    assert result.last_evaluated_key is None


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_limit,error_match",
    [
        (-1, "Limit must be greater than 0"),
        (0, "Limit must be greater than 0"),
    ],
)
def test_list_receipt_details_invalid_limit(
    dynamodb_table: Literal["MyMockedTable"],
    invalid_limit: int,
    error_match: str,
) -> None:
    """Tests that list_receipt_details validates limit parameter properly."""
    client = DynamoClient(dynamodb_table)

    with pytest.raises(EntityValidationError, match=error_match):
        client.list_receipt_details(limit=invalid_limit)


@pytest.mark.integration
def test_list_receipt_details_invalid_last_evaluated_key(
    dynamodb_table: Literal["MyMockedTable"],
) -> None:
    """
    Tests that list_receipt_details validates last_evaluated_key
    parameter.
    """
    client = DynamoClient(dynamodb_table)

    with pytest.raises(
        EntityValidationError, match="LastEvaluatedKey must be a dictionary"
    ):
        # type: ignore
        client.list_receipt_details(last_evaluated_key="invalid")


# -------------------------------------------------------------------
#           GET_RECEIPTS_FROM_IMAGE TESTS
# -------------------------------------------------------------------


@pytest.mark.integration
def test_get_receipts_from_image_success(
    dynamodb_table: Literal["MyMockedTable"],
    unique_image_id: str,
) -> None:
    """Tests retrieving all receipts from a specific image."""
    client = DynamoClient(dynamodb_table)

    # Create multiple receipts for the same image
    receipts = []
    for i in range(3):
        receipt = Receipt(
            image_id=unique_image_id,
            receipt_id=i + 1,
            width=10,
            height=20,
            timestamp_added=datetime.now().isoformat(),
            raw_s3_bucket="bucket",
            raw_s3_key=f"key_{i}",
            top_left={"x": 0, "y": 0},
            top_right={"x": 10, "y": 0},
            bottom_left={"x": 0, "y": 20},
            bottom_right={"x": 10, "y": 20},
            sha256=f"sha256_{i}",
        )
        receipts.append(receipt)

    # Add receipts
    client.add_receipts(receipts)

    # Get all receipts from the image
    retrieved_receipts = client.get_receipts_from_image(unique_image_id)

    # Verify all receipts are returned
    assert len(retrieved_receipts) == 3
    assert all(r in retrieved_receipts for r in receipts)

    # Verify they're all from the same image
    assert all(r.image_id == unique_image_id for r in retrieved_receipts)


@pytest.mark.integration
def test_get_receipts_from_image_empty_result(
    dynamodb_table: Literal["MyMockedTable"],
    unique_image_id: str,
) -> None:
    """Tests get_receipts_from_image when no receipts exist for the image."""
    client = DynamoClient(dynamodb_table)

    # Get receipts from non-existent image
    receipts = client.get_receipts_from_image(unique_image_id)

    # Should return empty list
    assert receipts == []


# -------------------------------------------------------------------
#           LIST_RECEIPT_AND_WORDS TESTS
# -------------------------------------------------------------------


@pytest.mark.integration
def test_list_receipt_and_words_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt: Receipt,
    sample_receipt_words: list[ReceiptWord],
) -> None:
    """Tests retrieving a receipt and its words together.

    NOTE: This method is deprecated. This test will emit a deprecation warning.
    """
    client = DynamoClient(dynamodb_table)

    # Add receipt and words
    client.add_receipt(sample_receipt)
    client.add_receipt_words(sample_receipt_words)

    # Get receipt and words
    receipt, words = client.list_receipt_and_words(
        sample_receipt.image_id, sample_receipt.receipt_id
    )

    # Verify receipt
    assert receipt == sample_receipt

    # Verify words
    assert len(words) == len(sample_receipt_words)
    assert all(w in words for w in sample_receipt_words)

    # Verify words are sorted by line_id and word_id
    for i in range(1, len(words)):
        prev = words[i - 1]
        curr = words[i]
        assert (prev.line_id, prev.word_id) <= (curr.line_id, curr.word_id)


@pytest.mark.integration
@pytest.mark.parametrize(
    "image_id,receipt_id,expected_error,error_match",
    [
        (None, 1, ValueError, "image_id cannot be None"),
        (
            "not-a-uuid",
            1,
            OperationError,
            "Unexpected error during list_receipt_and_words: uuid "
            "must be a valid UUIDv4",
        ),
        (
            "650e8400-e29b-41d4-a716-446655440001",
            # Fixed UUID for test consistency
            None,
            EntityValidationError,
            "receipt_id must be an integer",
        ),
        (
            "650e8400-e29b-41d4-a716-446655440002",
            # Fixed UUID for test consistency
            "not-an-int",
            EntityValidationError,
            "receipt_id must be an integer",
        ),
        (
            "650e8400-e29b-41d4-a716-446655440003",
            # Fixed UUID for test consistency
            -1,
            EntityValidationError,
            "receipt_id must be positive",
        ),
    ],
)
def test_list_receipt_and_words_parameter_validation(
    dynamodb_table: Literal["MyMockedTable"],
    image_id: Any,
    receipt_id: Any,
    expected_error: Type[Exception],
    error_match: str,
) -> None:
    """Tests parameter validation for list_receipt_and_words."""
    client = DynamoClient(dynamodb_table)

    with pytest.raises(expected_error, match=error_match):
        client.list_receipt_and_words(image_id, receipt_id)


@pytest.mark.integration
def test_list_receipt_and_words_not_found(
    dynamodb_table: Literal["MyMockedTable"],
    unique_image_id: str,
) -> None:
    """Tests list_receipt_and_words when receipt doesn't exist."""
    client = DynamoClient(dynamodb_table)

    with pytest.raises(
        EntityNotFoundError,
        match=(
            f"receipt with receipt_id=1 and "
            f"image_id={unique_image_id} does not exist"
        ),
    ):
        client.list_receipt_and_words(unique_image_id, 1)
