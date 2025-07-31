# pylint: disable=too-many-lines,too-many-positional-arguments
"""
Comprehensive parameterized integration tests for receipt letter operations.
This file contains refactored tests using pytest.mark.parametrize to ensure
complete test coverage matching test__receipt_line.py standards.
"""

from typing import Any, Literal, Type
from uuid import uuid4

import pytest
from botocore.exceptions import ClientError
from pytest_mock import MockerFixture

from receipt_dynamo import DynamoClient, ReceiptLetter
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
@pytest.fixture(name="unique_image_id")
def _unique_image_id() -> str:
    """Generate a unique image ID for each test to avoid conflicts."""
    return str(uuid4())


# pylint: disable=redefined-outer-name
@pytest.fixture(name="sample_receipt_letter")
def _sample_receipt_letter(unique_image_id: str) -> ReceiptLetter:
    """Returns a valid ReceiptLetter object with unique data."""
    return ReceiptLetter(
        receipt_id=1,
        image_id=unique_image_id,
        line_id=10,
        word_id=5,
        letter_id=2,
        text="A",
        bounding_box={"x": 0.15, "y": 0.20, "width": 0.02, "height": 0.02},
        top_left={"x": 0.15, "y": 0.20},
        top_right={"x": 0.17, "y": 0.20},
        bottom_left={"x": 0.15, "y": 0.22},
        bottom_right={"x": 0.17, "y": 0.22},
        angle_degrees=1.5,
        angle_radians=0.0261799,
        confidence=0.97,
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
        "Cannot update receiptletters",
    ),
]

DELETE_BATCH_ERROR_SCENARIOS = ERROR_SCENARIOS + [
    (
        "ConditionalCheckFailedException",
        EntityNotFoundError,
        "receiptletter not found during delete_receipt_letters",
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
def test_add_receipt_letter_client_errors(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_letter: ReceiptLetter,
    mocker: MockerFixture,
    error_code: str,
    expected_exception: Type[Exception],
    error_match: str,
) -> None:
    """Tests error handling for add_receipt_letter operations."""
    client = DynamoClient(dynamodb_table)

    # pylint: disable=protected-access
    mock_put = mocker.patch.object(
        client._client,
        "put_item",
        side_effect=ClientError({"Error": {"Code": error_code}}, "PutItem"),
    )

    with pytest.raises(expected_exception, match=error_match):
        client.add_receipt_letter(sample_receipt_letter)

    mock_put.assert_called_once()


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,expected_exception,error_match", ERROR_SCENARIOS
)
# pylint: disable=too-many-arguments
def test_update_receipt_letter_client_errors(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_letter: ReceiptLetter,
    mocker: MockerFixture,
    error_code: str,
    expected_exception: Type[Exception],
    error_match: str,
) -> None:
    """Tests error handling for update_receipt_letter operations."""
    client = DynamoClient(dynamodb_table)
    client.add_receipt_letter(sample_receipt_letter)

    # pylint: disable=protected-access
    mock_put = mocker.patch.object(
        client._client,
        "put_item",
        side_effect=ClientError({"Error": {"Code": error_code}}, "PutItem"),
    )

    with pytest.raises(expected_exception, match=error_match):
        client.update_receipt_letter(sample_receipt_letter)

    mock_put.assert_called_once()


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,expected_exception,error_match", ERROR_SCENARIOS
)
# pylint: disable=too-many-arguments
def test_delete_receipt_letter_client_errors(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_letter: ReceiptLetter,
    mocker: MockerFixture,
    error_code: str,
    expected_exception: Type[Exception],
    error_match: str,
) -> None:
    """Tests error handling for delete_receipt_letter operations."""
    client = DynamoClient(dynamodb_table)
    client.add_receipt_letter(sample_receipt_letter)

    # pylint: disable=protected-access
    mock_delete = mocker.patch.object(
        client._client,
        "delete_item",
        side_effect=ClientError({"Error": {"Code": error_code}}, "DeleteItem"),
    )

    with pytest.raises(expected_exception, match=error_match):
        client.delete_receipt_letter(
            sample_receipt_letter.receipt_id,
            sample_receipt_letter.image_id,
            sample_receipt_letter.line_id,
            sample_receipt_letter.word_id,
            sample_receipt_letter.letter_id,
        )

    mock_delete.assert_called_once()


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,expected_exception,error_match", ERROR_SCENARIOS
)
# pylint: disable=too-many-arguments
def test_get_receipt_letter_client_errors(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_letter: ReceiptLetter,
    mocker: MockerFixture,
    error_code: str,
    expected_exception: Type[Exception],
    error_match: str,
) -> None:
    """Tests error handling for get_receipt_letter operations."""
    client = DynamoClient(dynamodb_table)

    # pylint: disable=protected-access
    mock_get = mocker.patch.object(
        client._client,
        "get_item",
        side_effect=ClientError({"Error": {"Code": error_code}}, "GetItem"),
    )

    with pytest.raises(expected_exception, match=error_match):
        client.get_receipt_letter(
            sample_receipt_letter.receipt_id,
            sample_receipt_letter.image_id,
            sample_receipt_letter.line_id,
            sample_receipt_letter.word_id,
            sample_receipt_letter.letter_id,
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
def test_add_receipt_letters_client_errors(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_letter: ReceiptLetter,
    mocker: MockerFixture,
    error_code: str,
    expected_exception: Type[Exception],
    error_match: str,
) -> None:
    """Tests error handling for add_receipt_letters batch operations."""
    client = DynamoClient(dynamodb_table)
    letters = [sample_receipt_letter]

    # pylint: disable=protected-access
    mock_transact = mocker.patch.object(
        client._client,
        "transact_write_items",
        side_effect=ClientError(
            {"Error": {"Code": error_code}}, "TransactWriteItems"
        ),
    )

    with pytest.raises(expected_exception, match=error_match):
        client.add_receipt_letters(letters)

    mock_transact.assert_called_once()


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,expected_exception,error_match", UPDATE_BATCH_ERROR_SCENARIOS
)
# pylint: disable=too-many-arguments
def test_update_receipt_letters_client_errors(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_letter: ReceiptLetter,
    mocker: MockerFixture,
    error_code: str,
    expected_exception: Type[Exception],
    error_match: str,
) -> None:
    """Tests error handling for update_receipt_letters batch operations."""
    client = DynamoClient(dynamodb_table)
    letters = [sample_receipt_letter]

    # pylint: disable=protected-access
    mock_transact = mocker.patch.object(
        client._client,
        "transact_write_items",
        side_effect=ClientError(
            {"Error": {"Code": error_code}}, "TransactWriteItems"
        ),
    )

    with pytest.raises(expected_exception, match=error_match):
        client.update_receipt_letters(letters)

    mock_transact.assert_called_once()


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,expected_exception,error_match", DELETE_BATCH_ERROR_SCENARIOS
)
# pylint: disable=too-many-arguments
def test_delete_receipt_letters_client_errors(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_letter: ReceiptLetter,
    mocker: MockerFixture,
    error_code: str,
    expected_exception: Type[Exception],
    error_match: str,
) -> None:
    """Tests error handling for delete_receipt_letters batch operations."""
    client = DynamoClient(dynamodb_table)
    letters = [sample_receipt_letter]

    # pylint: disable=protected-access
    mock_transact = mocker.patch.object(
        client._client,
        "transact_write_items",
        side_effect=ClientError(
            {"Error": {"Code": error_code}}, "TransactWriteItems"
        ),
    )

    with pytest.raises(expected_exception, match=error_match):
        client.delete_receipt_letters(letters)

    mock_transact.assert_called_once()


# -------------------------------------------------------------------
#           PARAMETERIZED VALIDATION TESTS
# -------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize(
    "method_name,invalid_input,error_match",
    [
        ("add_receipt_letter", None, "letter cannot be None"),
        (
            "add_receipt_letter",
            "not-a-letter",
            "letter must be an instance of",
        ),
        ("update_receipt_letter", None, "letter cannot be None"),
        (
            "update_receipt_letter",
            "not-a-letter",
            "letter must be an instance of",
        ),
    ],
)
def test_single_receipt_letter_validation(
    dynamodb_table: Literal["MyMockedTable"],
    method_name: str,
    invalid_input: Any,
    error_match: str,
) -> None:
    """Tests validation for single receipt letter operations."""
    client = DynamoClient(dynamodb_table)
    method = getattr(client, method_name)

    with pytest.raises(EntityValidationError, match=error_match):
        method(invalid_input)


@pytest.mark.integration
@pytest.mark.parametrize(
    "method_name,invalid_input,error_match",
    [
        ("add_receipt_letters", None, "letters cannot be None"),
        ("add_receipt_letters", "not-a-list", "letters must be a list"),
        ("update_receipt_letters", None, "letters cannot be None"),
        ("update_receipt_letters", "not-a-list", "letters must be a list"),
        ("delete_receipt_letters", None, "letters cannot be None"),
        ("delete_receipt_letters", "not-a-list", "letters must be a list"),
    ],
)
def test_batch_receipt_letter_validation_basic(
    dynamodb_table: Literal["MyMockedTable"],
    method_name: str,
    invalid_input: Any,
    error_match: str,
) -> None:
    """Tests basic validation for batch receipt letter operations."""
    client = DynamoClient(dynamodb_table)
    method = getattr(client, method_name)

    with pytest.raises(EntityValidationError, match=error_match):
        method(invalid_input)


@pytest.mark.integration
@pytest.mark.parametrize(
    "method_name",
    [
        "add_receipt_letters",
        "update_receipt_letters",
        "delete_receipt_letters",
    ],
)
# pylint: disable=redefined-outer-name
def test_batch_receipt_letter_validation_mixed_types(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_letter: ReceiptLetter,
    method_name: str,
) -> None:
    """Tests validation for batch operations with mixed types."""
    client = DynamoClient(dynamodb_table)
    method = getattr(client, method_name)

    mixed_list = [sample_receipt_letter, "not-a-letter", 123]

    with pytest.raises(
        EntityValidationError,
        match=r"letters\[1\] must be an instance of ReceiptLetter",
    ):
        method(mixed_list)


@pytest.mark.integration
@pytest.mark.parametrize(
    "receipt_id,image_id,line_id,word_id,letter_id,expected_error,error_match",
    [
        (
            None,
            FIXED_UUIDS[0],
            1,
            1,
            1,
            EntityValidationError,
            "receipt_id cannot be None",
        ),
        (
            "not-an-int",
            FIXED_UUIDS[0],
            1,
            1,
            1,
            EntityValidationError,
            "receipt_id must be an integer",
        ),
        (
            -1,
            FIXED_UUIDS[0],
            1,
            1,
            1,
            EntityNotFoundError,
            "ReceiptLetter with ID 1 not found",
        ),
        (1, None, 1, 1, 1, EntityValidationError, "image_id cannot be None"),
        (
            1,
            "not-a-uuid",
            1,
            1,
            1,
            OperationError,
            "uuid must be a valid UUIDv4",
        ),
        (
            1,
            FIXED_UUIDS[0],
            None,
            1,
            1,
            EntityValidationError,
            "line_id cannot be None",
        ),
        (
            1,
            FIXED_UUIDS[0],
            "not-an-int",
            1,
            1,
            EntityValidationError,
            "line_id must be an integer",
        ),
        (
            1,
            FIXED_UUIDS[0],
            -1,
            1,
            1,
            EntityNotFoundError,
            "ReceiptLetter with ID 1 not found",
        ),
        (
            1,
            FIXED_UUIDS[0],
            1,
            None,
            1,
            EntityValidationError,
            "word_id cannot be None",
        ),
        (
            1,
            FIXED_UUIDS[0],
            1,
            "not-an-int",
            1,
            EntityValidationError,
            "word_id must be an integer",
        ),
        (
            1,
            FIXED_UUIDS[0],
            1,
            -1,
            1,
            EntityNotFoundError,
            "ReceiptLetter with ID 1 not found",
        ),
        (
            1,
            FIXED_UUIDS[0],
            1,
            1,
            None,
            EntityValidationError,
            "letter_id cannot be None",
        ),
        (
            1,
            FIXED_UUIDS[0],
            1,
            1,
            "not-an-int",
            EntityValidationError,
            "letter_id must be an integer",
        ),
        (
            1,
            FIXED_UUIDS[0],
            1,
            1,
            -1,
            EntityNotFoundError,
            "ReceiptLetter with ID -1 not found",
        ),
    ],
)
# pylint: disable=too-many-arguments,too-many-locals
def test_get_receipt_letter_parameter_validation(
    dynamodb_table: Literal["MyMockedTable"],
    receipt_id: Any,
    image_id: Any,
    line_id: Any,
    word_id: Any,
    letter_id: Any,
    expected_error: Type[Exception],
    error_match: str,
) -> None:
    """Tests parameter validation for get_receipt_letter."""
    client = DynamoClient(dynamodb_table)

    with pytest.raises(expected_error, match=error_match):
        client.get_receipt_letter(
            receipt_id, image_id, line_id, word_id, letter_id
        )


# -------------------------------------------------------------------
#           LIST OPERATIONS VALIDATION TESTS
# -------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize(
    "limit,error_match",
    [
        ("not-an-int", "limit must be an integer"),
        (-1, "Parameter validation failed"),
        (0, "Parameter validation failed"),
    ],
)
def test_list_receipt_letters_invalid_limit(
    dynamodb_table: Literal["MyMockedTable"],
    limit: Any,
    error_match: str,
) -> None:
    """Tests list_receipt_letters with invalid limit parameter."""
    client = DynamoClient(dynamodb_table)

    with pytest.raises(EntityValidationError, match=error_match):
        client.list_receipt_letters(limit=limit)


@pytest.mark.integration
def test_list_receipt_letters_invalid_last_evaluated_key(
    dynamodb_table: Literal["MyMockedTable"],
) -> None:
    """Tests list_receipt_letters with invalid last_evaluated_key."""
    client = DynamoClient(dynamodb_table)

    with pytest.raises(
        EntityValidationError, match="last_evaluated_key must be a dictionary"
    ):
        client.list_receipt_letters(last_evaluated_key="not-a-dict")


@pytest.mark.integration
@pytest.mark.parametrize(
    "receipt_id,image_id,line_id,word_id,expected_error,error_match",
    [
        (
            None,
            FIXED_UUIDS[0],
            1,
            1,
            EntityValidationError,
            "receipt_id cannot be None",
        ),
        (
            "not-an-int",
            FIXED_UUIDS[0],
            1,
            1,
            EntityValidationError,
            "receipt_id must be a positive integer",
        ),
        (1, None, 1, 1, EntityValidationError, "image_id cannot be None"),
        (1, "not-a-uuid", 1, 1, OperationError, "uuid must be a valid UUIDv4"),
        (
            1,
            FIXED_UUIDS[0],
            None,
            1,
            EntityValidationError,
            "line_id cannot be None",
        ),
        (
            1,
            FIXED_UUIDS[0],
            "not-an-int",
            1,
            EntityValidationError,
            "line_id must be an integer",
        ),
        (
            1,
            FIXED_UUIDS[0],
            1,
            None,
            EntityValidationError,
            "word_id cannot be None",
        ),
        (
            1,
            FIXED_UUIDS[0],
            1,
            "not-an-int",
            EntityValidationError,
            "word_id must be an integer",
        ),
    ],
)
# pylint: disable=too-many-arguments
def test_list_receipt_letters_from_word_validation(
    dynamodb_table: Literal["MyMockedTable"],
    receipt_id: Any,
    image_id: Any,
    line_id: Any,
    word_id: Any,
    expected_error: Type[Exception],
    error_match: str,
) -> None:
    """Tests validation for list_receipt_letters_from_word."""
    client = DynamoClient(dynamodb_table)

    with pytest.raises(expected_error, match=error_match):
        client.list_receipt_letters_from_word(
            receipt_id, image_id, line_id, word_id
        )


# -------------------------------------------------------------------
#           QUERY OPERATIONS ERROR TESTS
# -------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,expected_exception,error_match", ERROR_SCENARIOS
)
# pylint: disable=too-many-arguments
def test_list_receipt_letters_client_errors(
    dynamodb_table: Literal["MyMockedTable"],
    mocker: MockerFixture,
    error_code: str,
    expected_exception: Type[Exception],
    error_match: str,
) -> None:
    """Tests error handling for list_receipt_letters operations."""
    client = DynamoClient(dynamodb_table)

    # pylint: disable=protected-access
    mock_query = mocker.patch.object(
        client._client,
        "query",
        side_effect=ClientError({"Error": {"Code": error_code}}, "Query"),
    )

    with pytest.raises(expected_exception, match=error_match):
        client.list_receipt_letters()

    mock_query.assert_called_once()


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,expected_exception,error_match", ERROR_SCENARIOS
)
# pylint: disable=too-many-arguments
def test_list_receipt_letters_from_word_client_errors(
    dynamodb_table: Literal["MyMockedTable"],
    mocker: MockerFixture,
    error_code: str,
    expected_exception: Type[Exception],
    error_match: str,
) -> None:
    """Tests error handling for list_receipt_letters_from_word operations."""
    client = DynamoClient(dynamodb_table)

    # pylint: disable=protected-access
    mock_query = mocker.patch.object(
        client._client,
        "query",
        side_effect=ClientError({"Error": {"Code": error_code}}, "Query"),
    )

    with pytest.raises(expected_exception, match=error_match):
        client.list_receipt_letters_from_word(1, FIXED_UUIDS[0], 1, 1)

    mock_query.assert_called_once()


# -------------------------------------------------------------------
#           CONDITIONAL CHECK FAILED TESTS
# -------------------------------------------------------------------


@pytest.mark.integration
# pylint: disable=redefined-outer-name
def test_add_receipt_letter_conditional_check_failed(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_letter: ReceiptLetter,
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
        EntityAlreadyExistsError, match="receipt_letter already exists"
    ):
        client.add_receipt_letter(sample_receipt_letter)

    mock_put.assert_called_once()


@pytest.mark.integration
# pylint: disable=redefined-outer-name
def test_update_receipt_letter_conditional_check_failed(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_letter: ReceiptLetter,
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
        EntityNotFoundError,
        match="receiptletter not found during update_receipt_letter",
    ):
        client.update_receipt_letter(sample_receipt_letter)

    mock_put.assert_called_once()


@pytest.mark.integration
# pylint: disable=redefined-outer-name
def test_delete_receipt_letter_conditional_check_failed(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_letter: ReceiptLetter,
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
        EntityNotFoundError, match="not found during delete_receipt_letter"
    ):
        client.delete_receipt_letter(
            sample_receipt_letter.receipt_id,
            sample_receipt_letter.image_id,
            sample_receipt_letter.line_id,
            sample_receipt_letter.word_id,
            sample_receipt_letter.letter_id,
        )

    mock_delete.assert_called_once()


# -------------------------------------------------------------------
#           SUCCESS PATH TESTS
# -------------------------------------------------------------------


@pytest.mark.integration
# pylint: disable=redefined-outer-name
def test_add_receipt_letter_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_letter: ReceiptLetter,
) -> None:
    """Tests successful add_receipt_letter operation."""
    client = DynamoClient(dynamodb_table)

    # Act
    client.add_receipt_letter(sample_receipt_letter)

    # Assert
    retrieved = client.get_receipt_letter(
        sample_receipt_letter.receipt_id,
        sample_receipt_letter.image_id,
        sample_receipt_letter.line_id,
        sample_receipt_letter.word_id,
        sample_receipt_letter.letter_id,
    )
    assert retrieved == sample_receipt_letter


@pytest.mark.integration
# pylint: disable=redefined-outer-name
def test_add_receipt_letter_duplicate_raises(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_letter: ReceiptLetter,
) -> None:
    """Tests that adding duplicate receipt letter raises error."""
    client = DynamoClient(dynamodb_table)
    client.add_receipt_letter(sample_receipt_letter)

    with pytest.raises(EntityAlreadyExistsError, match="already exists"):
        client.add_receipt_letter(sample_receipt_letter)


@pytest.mark.integration
# pylint: disable=redefined-outer-name
def test_update_receipt_letter_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_letter: ReceiptLetter,
) -> None:
    """Tests successful update_receipt_letter operation."""
    client = DynamoClient(dynamodb_table)
    client.add_receipt_letter(sample_receipt_letter)

    # Modify some fields
    sample_receipt_letter.text = "Z"
    sample_receipt_letter.confidence = 0.99

    # Update
    client.update_receipt_letter(sample_receipt_letter)

    # Verify
    retrieved = client.get_receipt_letter(
        sample_receipt_letter.receipt_id,
        sample_receipt_letter.image_id,
        sample_receipt_letter.line_id,
        sample_receipt_letter.word_id,
        sample_receipt_letter.letter_id,
    )
    assert retrieved.text == "Z"
    assert retrieved.confidence == 0.99


@pytest.mark.integration
# pylint: disable=redefined-outer-name
def test_delete_receipt_letter_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_letter: ReceiptLetter,
) -> None:
    """Tests successful delete_receipt_letter operation."""
    client = DynamoClient(dynamodb_table)
    client.add_receipt_letter(sample_receipt_letter)

    # Delete
    client.delete_receipt_letter(
        sample_receipt_letter.receipt_id,
        sample_receipt_letter.image_id,
        sample_receipt_letter.line_id,
        sample_receipt_letter.word_id,
        sample_receipt_letter.letter_id,
    )

    # Verify
    with pytest.raises(
        EntityNotFoundError, match="ReceiptLetter with.*not found"
    ):
        client.get_receipt_letter(
            sample_receipt_letter.receipt_id,
            sample_receipt_letter.image_id,
            sample_receipt_letter.line_id,
            sample_receipt_letter.word_id,
            sample_receipt_letter.letter_id,
        )


@pytest.mark.integration
# pylint: disable=redefined-outer-name
def test_get_receipt_letter_not_found(
    dynamodb_table: Literal["MyMockedTable"],
    unique_image_id: str,
) -> None:
    """Tests get_receipt_letter when letter doesn't exist."""
    client = DynamoClient(dynamodb_table)

    with pytest.raises(
        EntityNotFoundError,
        match="ReceiptLetter with.*not found",
    ):
        client.get_receipt_letter(999, unique_image_id, 999, 999, 999)


# -------------------------------------------------------------------
#           BATCH OPERATIONS SUCCESS TESTS
# -------------------------------------------------------------------


@pytest.mark.integration
# pylint: disable=redefined-outer-name
def test_add_receipt_letters_success(
    dynamodb_table: Literal["MyMockedTable"],
    unique_image_id: str,
) -> None:
    """Tests successful batch add operation."""
    client = DynamoClient(dynamodb_table)

    letters = [
        ReceiptLetter(
            receipt_id=1,
            image_id=unique_image_id,
            line_id=1,
            word_id=1,
            letter_id=i + 1,
            text=chr(65 + i),  # A, B, C
            bounding_box={
                "x": 0.1 * i,
                "y": 0.2,
                "width": 0.02,
                "height": 0.02,
            },
            top_left={"x": 0.1 * i, "y": 0.2},
            top_right={"x": 0.1 * i + 0.02, "y": 0.2},
            bottom_left={"x": 0.1 * i, "y": 0.22},
            bottom_right={"x": 0.1 * i + 0.02, "y": 0.22},
            angle_degrees=0,
            angle_radians=0,
            confidence=0.95 + i * 0.01,
        )
        for i in range(3)
    ]

    client.add_receipt_letters(letters)

    # Verify all were added
    for letter in letters:
        retrieved = client.get_receipt_letter(
            letter.receipt_id,
            letter.image_id,
            letter.line_id,
            letter.word_id,
            letter.letter_id,
        )
        assert retrieved == letter


@pytest.mark.integration
# pylint: disable=redefined-outer-name
def test_add_receipt_letters_empty_list(
    dynamodb_table: Literal["MyMockedTable"],
) -> None:
    """Tests add_receipt_letters with empty list."""
    client = DynamoClient(dynamodb_table)

    with pytest.raises(OperationError, match="Parameter validation failed"):
        client.add_receipt_letters([])


@pytest.mark.integration
# pylint: disable=redefined-outer-name
def test_update_receipt_letters_success(
    dynamodb_table: Literal["MyMockedTable"],
    unique_image_id: str,
) -> None:
    """Tests successful batch update operation."""
    client = DynamoClient(dynamodb_table)

    # First add letters
    letters = [
        ReceiptLetter(
            receipt_id=1,
            image_id=unique_image_id,
            line_id=1,
            word_id=1,
            letter_id=i + 1,
            text=chr(65 + i),  # A, B, C
            bounding_box={
                "x": 0.1 * i,
                "y": 0.2,
                "width": 0.02,
                "height": 0.02,
            },
            top_left={"x": 0.1 * i, "y": 0.2},
            top_right={"x": 0.1 * i + 0.02, "y": 0.2},
            bottom_left={"x": 0.1 * i, "y": 0.22},
            bottom_right={"x": 0.1 * i + 0.02, "y": 0.22},
            angle_degrees=0,
            angle_radians=0,
            confidence=0.95,
        )
        for i in range(3)
    ]
    client.add_receipt_letters(letters)

    # Update them
    for letter in letters:
        letter.text = letter.text.lower()  # Convert to lowercase
        letter.confidence = 0.99

    client.update_receipt_letters(letters)

    # Verify updates
    for letter in letters:
        retrieved = client.get_receipt_letter(
            letter.receipt_id,
            letter.image_id,
            letter.line_id,
            letter.word_id,
            letter.letter_id,
        )
        assert retrieved.text == letter.text
        assert retrieved.confidence == 0.99


@pytest.mark.integration
# pylint: disable=redefined-outer-name
def test_delete_receipt_letters_success(
    dynamodb_table: Literal["MyMockedTable"],
    unique_image_id: str,
) -> None:
    """Tests successful batch delete operation."""
    client = DynamoClient(dynamodb_table)

    # First add letters
    letters = [
        ReceiptLetter(
            receipt_id=1,
            image_id=unique_image_id,
            line_id=1,
            word_id=1,
            letter_id=i + 1,
            text=chr(65 + i),  # A, B, C
            bounding_box={
                "x": 0.1 * i,
                "y": 0.2,
                "width": 0.02,
                "height": 0.02,
            },
            top_left={"x": 0.1 * i, "y": 0.2},
            top_right={"x": 0.1 * i + 0.02, "y": 0.2},
            bottom_left={"x": 0.1 * i, "y": 0.22},
            bottom_right={"x": 0.1 * i + 0.02, "y": 0.22},
            angle_degrees=0,
            angle_radians=0,
            confidence=0.95,
        )
        for i in range(3)
    ]
    client.add_receipt_letters(letters)

    # Delete them
    client.delete_receipt_letters(letters)

    # Verify deletion
    for letter in letters:
        with pytest.raises(EntityNotFoundError):
            client.get_receipt_letter(
                letter.receipt_id,
                letter.image_id,
                letter.line_id,
                letter.word_id,
                letter.letter_id,
            )


# -------------------------------------------------------------------
#           LIST OPERATIONS TESTS
# -------------------------------------------------------------------


@pytest.mark.integration
# pylint: disable=redefined-outer-name
def test_list_receipt_letters_with_pagination(
    dynamodb_table: Literal["MyMockedTable"],
    unique_image_id: str,
) -> None:
    """Tests list_receipt_letters with pagination."""
    client = DynamoClient(dynamodb_table)

    # Add multiple letters across different words
    for word_id in range(1, 3):
        for letter_id in range(1, 4):
            letter = ReceiptLetter(
                receipt_id=1,
                image_id=unique_image_id,
                line_id=1,
                word_id=word_id,
                letter_id=letter_id,
                text=chr(65 + letter_id - 1),
                bounding_box={
                    "x": 0.1 * letter_id,
                    "y": 0.2,
                    "width": 0.02,
                    "height": 0.02,
                },
                top_left={"x": 0.1 * letter_id, "y": 0.2},
                top_right={"x": 0.1 * letter_id + 0.02, "y": 0.2},
                bottom_left={"x": 0.1 * letter_id, "y": 0.22},
                bottom_right={"x": 0.1 * letter_id + 0.02, "y": 0.22},
                angle_degrees=0,
                angle_radians=0,
                confidence=0.95,
            )
            client.add_receipt_letter(letter)

    # List with limit
    first_page, last_key = client.list_receipt_letters(limit=3)
    assert len(first_page) == 3
    assert last_key is not None

    # Get next page
    second_page, last_key = client.list_receipt_letters(
        limit=3, last_evaluated_key=last_key
    )
    assert len(second_page) >= 3

    # Ensure no duplicates between pages
    first_ids = {
        (l.receipt_id, l.image_id, l.line_id, l.word_id, l.letter_id)
        for l in first_page
    }
    second_ids = {
        (l.receipt_id, l.image_id, l.line_id, l.word_id, l.letter_id)
        for l in second_page
    }
    assert first_ids.isdisjoint(second_ids)


@pytest.mark.integration
def test_list_receipt_letters_empty(
    dynamodb_table: Literal["MyMockedTable"],
) -> None:
    """Tests list_receipt_letters when no letters exist."""
    client = DynamoClient(dynamodb_table)

    letters, last_key = client.list_receipt_letters()

    assert len(letters) == 0
    assert last_key is None


@pytest.mark.integration
# pylint: disable=redefined-outer-name
def test_list_receipt_letters_from_word_success(
    dynamodb_table: Literal["MyMockedTable"],
    unique_image_id: str,
) -> None:
    """Tests list_receipt_letters_from_word filtering."""
    client = DynamoClient(dynamodb_table)

    # Add letters for specific word
    for letter_id in range(1, 4):
        letter = ReceiptLetter(
            receipt_id=1,
            image_id=unique_image_id,
            line_id=1,
            word_id=1,
            letter_id=letter_id,
            text=chr(65 + letter_id - 1),
            bounding_box={
                "x": 0.1 * letter_id,
                "y": 0.2,
                "width": 0.02,
                "height": 0.02,
            },
            top_left={"x": 0.1 * letter_id, "y": 0.2},
            top_right={"x": 0.1 * letter_id + 0.02, "y": 0.2},
            bottom_left={"x": 0.1 * letter_id, "y": 0.22},
            bottom_right={"x": 0.1 * letter_id + 0.02, "y": 0.22},
            angle_degrees=0,
            angle_radians=0,
            confidence=0.95,
        )
        client.add_receipt_letter(letter)

    # Add letter for different word
    other_letter = ReceiptLetter(
        receipt_id=1,
        image_id=unique_image_id,
        line_id=1,
        word_id=2,
        letter_id=1,
        text="X",
        bounding_box={"x": 0.5, "y": 0.2, "width": 0.02, "height": 0.02},
        top_left={"x": 0.5, "y": 0.2},
        top_right={"x": 0.52, "y": 0.2},
        bottom_left={"x": 0.5, "y": 0.22},
        bottom_right={"x": 0.52, "y": 0.22},
        angle_degrees=0,
        angle_radians=0,
        confidence=0.99,
    )
    client.add_receipt_letter(other_letter)

    # Query for specific word only
    word_letters = client.list_receipt_letters_from_word(
        receipt_id=1, image_id=unique_image_id, line_id=1, word_id=1
    )

    # Verify filtering
    assert len(word_letters) == 3
    assert all(l.word_id == 1 for l in word_letters)
    assert other_letter not in word_letters


# -------------------------------------------------------------------
#           SPECIAL CASES
# -------------------------------------------------------------------


@pytest.mark.integration
# pylint: disable=redefined-outer-name
def test_receipt_letter_with_unicode_text(
    dynamodb_table: Literal["MyMockedTable"],
    unique_image_id: str,
) -> None:
    """Tests receipt letter with unicode characters in text."""
    client = DynamoClient(dynamodb_table)
    unicode_letter = ReceiptLetter(
        receipt_id=1,
        image_id=unique_image_id,
        line_id=1,
        word_id=1,
        letter_id=1,
        text="测",  # Chinese character
        bounding_box={"x": 0, "y": 0, "width": 0.02, "height": 0.02},
        top_left={"x": 0, "y": 0},
        top_right={"x": 0.02, "y": 0},
        bottom_left={"x": 0, "y": 0.02},
        bottom_right={"x": 0.02, "y": 0.02},
        angle_degrees=0,
        angle_radians=0,
        confidence=0.95,
    )

    client.add_receipt_letter(unicode_letter)
    result = client.get_receipt_letter(
        unicode_letter.receipt_id,
        unicode_letter.image_id,
        unicode_letter.line_id,
        unicode_letter.word_id,
        unicode_letter.letter_id,
    )
    assert result == unicode_letter


@pytest.mark.integration
# pylint: disable=redefined-outer-name
def test_receipt_letter_with_extreme_coordinates(
    dynamodb_table: Literal["MyMockedTable"],
    unique_image_id: str,
) -> None:
    """Tests receipt letter with extreme coordinate values."""
    client = DynamoClient(dynamodb_table)
    extreme_letter = ReceiptLetter(
        receipt_id=1,
        image_id=unique_image_id,
        line_id=1,
        word_id=1,
        letter_id=1,
        text="E",
        bounding_box={
            "x": 0.00001,
            "y": 0.99999,
            "width": 0.00001,
            "height": 0.00001,
        },
        top_left={"x": 0.00001, "y": 0.99999},
        top_right={"x": 0.00002, "y": 0.99999},
        bottom_left={"x": 0.00001, "y": 1.0},
        bottom_right={"x": 0.00002, "y": 1.0},
        angle_degrees=359.999,
        angle_radians=6.28318,
        confidence=0.95,  # Changed from 0.00001 to valid range
    )

    client.add_receipt_letter(extreme_letter)
    result = client.get_receipt_letter(
        extreme_letter.receipt_id,
        extreme_letter.image_id,
        extreme_letter.line_id,
        extreme_letter.word_id,
        extreme_letter.letter_id,
    )
    assert result == extreme_letter


@pytest.mark.integration
# pylint: disable=redefined-outer-name
def test_large_batch_operations(
    dynamodb_table: Literal["MyMockedTable"],
    unique_image_id: str,
) -> None:
    """Tests batch operations with maximum batch size."""
    client = DynamoClient(dynamodb_table)

    # Create 100 letters (DynamoDB batch limit is 25, so this tests chunking)
    letters = []
    for word_id in range(1, 11):  # 10 words
        for letter_id in range(1, 11):  # 10 letters per word
            letters.append(
                ReceiptLetter(
                    receipt_id=1,
                    image_id=unique_image_id,
                    line_id=1,
                    word_id=word_id,
                    letter_id=letter_id,
                    text=chr(65 + (letter_id - 1) % 26),  # Cycle through A-Z
                    bounding_box={
                        "x": 0.01 * word_id,
                        "y": 0.01 * letter_id,
                        "width": 0.01,
                        "height": 0.01,
                    },
                    top_left={"x": 0.01 * word_id, "y": 0.01 * letter_id},
                    top_right={
                        "x": 0.01 * word_id + 0.01,
                        "y": 0.01 * letter_id,
                    },
                    bottom_left={
                        "x": 0.01 * word_id,
                        "y": 0.01 * letter_id + 0.01,
                    },
                    bottom_right={
                        "x": 0.01 * word_id + 0.01,
                        "y": 0.01 * letter_id + 0.01,
                    },
                    angle_degrees=word_id % 360,
                    angle_radians=(word_id % 360) * 0.017453,
                    confidence=0.9 + (letter_id % 10) * 0.01,
                )
            )

    # Add in batch
    client.add_receipt_letters(letters)

    # Verify a sample
    for i in [0, 25, 50, 75, 99]:
        letter = letters[i]
        result = client.get_receipt_letter(
            letter.receipt_id,
            letter.image_id,
            letter.line_id,
            letter.word_id,
            letter.letter_id,
        )
        assert result.text == letter.text

    # Update all
    for letter in letters:
        letter.text = letter.text.lower()
    client.update_receipt_letters(letters)

    # Verify updates
    letter = letters[50]
    result = client.get_receipt_letter(
        letter.receipt_id,
        letter.image_id,
        letter.line_id,
        letter.word_id,
        letter.letter_id,
    )
    assert result.text == letter.text.lower()

    # Delete all
    client.delete_receipt_letters(letters)

    # Verify deletion
    with pytest.raises(EntityNotFoundError):
        client.get_receipt_letter(
            letter.receipt_id,
            letter.image_id,
            letter.line_id,
            letter.word_id,
            letter.letter_id,
        )
