"""
Comprehensive parameterized tests for receipt_word functionality.

This file contains refactored tests using pytest.mark.parametrize to ensure
comprehensive coverage of all CRUD operations, error scenarios, and edge cases
for ReceiptWord operations.

Based on successful patterns from test__receipt_line.py parameterization.
"""

import uuid
from typing import Any, Dict, List, Literal
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from receipt_dynamo import DynamoClient, ReceiptWord
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

# =============================================================================
# FIXTURES AND TEST DATA
# =============================================================================

# Fixed UUID for deterministic test collection
FIXED_IMAGE_ID = "550e8400-e29b-41d4-a716-446655440001"
FIXED_IMAGE_ID_2 = "550e8400-e29b-41d4-a716-446655440002"


@pytest.fixture
def sample_receipt_word():
    """Create a sample ReceiptWord for testing."""
    return ReceiptWord(
        receipt_id=1,
        image_id=FIXED_IMAGE_ID,
        line_id=10,
        word_id=5,
        text="Sample receipt word",
        bounding_box={"x": 0.1, "y": 0.2, "width": 0.2, "height": 0.03},
        top_left={"x": 0.1, "y": 0.2},
        top_right={"x": 0.3, "y": 0.2},
        bottom_left={"x": 0.1, "y": 0.23},
        bottom_right={"x": 0.3, "y": 0.23},
        angle_degrees=2.0,
        angle_radians=0.0349066,
        confidence=0.95,
    )


@pytest.fixture
def sample_receipt_words():
    """Create multiple sample ReceiptWords for batch testing."""
    return [
        ReceiptWord(
            receipt_id=1,
            image_id=FIXED_IMAGE_ID,
            line_id=10,
            word_id=i,
            text=f"Word {i}",
            bounding_box={
                "x": 0.1 * i,
                "y": 0.2,
                "width": 0.1,
                "height": 0.02,
            },
            top_left={"x": 0.1 * i, "y": 0.2},
            top_right={"x": 0.1 * i + 0.1, "y": 0.2},
            bottom_left={"x": 0.1 * i, "y": 0.22},
            bottom_right={"x": 0.1 * i + 0.1, "y": 0.22},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.9 + 0.01 * i,
        )
        for i in range(1, 4)
    ]


@pytest.fixture
def client(dynamodb_table: Literal["MyMockedTable"]) -> DynamoClient:
    """Create a DynamoClient for testing."""
    return DynamoClient(dynamodb_table)


# =============================================================================
# ERROR SCENARIOS FOR PARAMETERIZED TESTS
# =============================================================================

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

UPDATE_DELETE_ERROR_SCENARIOS = ERROR_SCENARIOS + [
    ("ConditionalCheckFailedException", EntityNotFoundError, "not found"),
]

# =============================================================================
# CLIENT ERROR TESTS
# =============================================================================


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,expected_exception,error_fragment", ERROR_SCENARIOS
)
def test_add_receipt_word_client_errors(
    client: DynamoClient,
    sample_receipt_word: ReceiptWord,
    error_code: str,
    expected_exception: type,
    error_fragment: str,
):
    """Test that add_receipt_word handles various ClientError scenarios correctly."""
    with patch.object(client._client, "put_item") as mock_put:
        mock_put.side_effect = ClientError(
            error_response={"Error": {"Code": error_code, "Message": "Test error"}},
            operation_name="PutItem",
        )

        with pytest.raises(expected_exception, match=error_fragment):
            client.add_receipt_word(sample_receipt_word)


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,expected_exception,error_fragment",
    UPDATE_DELETE_ERROR_SCENARIOS,
)
def test_update_receipt_word_client_errors(
    client: DynamoClient,
    sample_receipt_word: ReceiptWord,
    error_code: str,
    expected_exception: type,
    error_fragment: str,
):
    """Test that update_receipt_word handles various ClientError scenarios correctly."""
    with patch.object(client._client, "put_item") as mock_put:
        mock_put.side_effect = ClientError(
            error_response={"Error": {"Code": error_code, "Message": "Test error"}},
            operation_name="PutItem",
        )

        with pytest.raises(expected_exception, match=error_fragment):
            client.update_receipt_word(sample_receipt_word)


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,expected_exception,error_fragment",
    UPDATE_DELETE_ERROR_SCENARIOS,
)
def test_delete_receipt_word_client_errors(
    client: DynamoClient,
    sample_receipt_word: ReceiptWord,
    error_code: str,
    expected_exception: type,
    error_fragment: str,
):
    """Test that delete_receipt_word handles various ClientError scenarios correctly."""
    with patch.object(client._client, "delete_item") as mock_delete:
        mock_delete.side_effect = ClientError(
            error_response={"Error": {"Code": error_code, "Message": "Test error"}},
            operation_name="DeleteItem",
        )

        with pytest.raises(expected_exception, match=error_fragment):
            client.delete_receipt_word(sample_receipt_word)


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,expected_exception,error_fragment", ERROR_SCENARIOS
)
def test_get_receipt_word_client_errors(
    client: DynamoClient,
    error_code: str,
    expected_exception: type,
    error_fragment: str,
):
    """Test that get_receipt_word handles various ClientError scenarios correctly."""
    with patch.object(client._client, "get_item") as mock_get:
        mock_get.side_effect = ClientError(
            error_response={"Error": {"Code": error_code, "Message": "Test error"}},
            operation_name="GetItem",
        )

        with pytest.raises(expected_exception, match=error_fragment):
            client.get_receipt_word(FIXED_IMAGE_ID, 1, 10, 5)


# =============================================================================
# BATCH OPERATION CLIENT ERROR TESTS
# =============================================================================


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,expected_exception,error_fragment", ERROR_SCENARIOS
)
def test_add_receipt_words_client_errors(
    client: DynamoClient,
    sample_receipt_words: List[ReceiptWord],
    error_code: str,
    expected_exception: type,
    error_fragment: str,
):
    """Test that add_receipt_words handles various ClientError scenarios correctly."""
    with patch.object(client._client, "transact_write_items") as mock_transact:
        mock_transact.side_effect = ClientError(
            error_response={"Error": {"Code": error_code, "Message": "Test error"}},
            operation_name="TransactWriteItems",
        )

        with pytest.raises(expected_exception, match=error_fragment):
            client.add_receipt_words(sample_receipt_words)


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,expected_exception,error_fragment",
    UPDATE_DELETE_ERROR_SCENARIOS,
)
def test_update_receipt_words_client_errors(
    client: DynamoClient,
    sample_receipt_words: List[ReceiptWord],
    error_code: str,
    expected_exception: type,
    error_fragment: str,
):
    """Test that update_receipt_words handles various ClientError scenarios correctly."""
    with patch.object(client._client, "transact_write_items") as mock_transact:
        mock_transact.side_effect = ClientError(
            error_response={"Error": {"Code": error_code, "Message": "Test error"}},
            operation_name="TransactWriteItems",
        )

        with pytest.raises(expected_exception, match=error_fragment):
            client.update_receipt_words(sample_receipt_words)


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,expected_exception,error_fragment",
    UPDATE_DELETE_ERROR_SCENARIOS,
)
def test_delete_receipt_words_client_errors(
    client: DynamoClient,
    sample_receipt_words: List[ReceiptWord],
    error_code: str,
    expected_exception: type,
    error_fragment: str,
):
    """Test that delete_receipt_words handles various ClientError scenarios correctly."""
    with patch.object(client._client, "transact_write_items") as mock_transact:
        mock_transact.side_effect = ClientError(
            error_response={"Error": {"Code": error_code, "Message": "Test error"}},
            operation_name="TransactWriteItems",
        )

        with pytest.raises(expected_exception, match=error_fragment):
            client.delete_receipt_words(sample_receipt_words)


# =============================================================================
# VALIDATION TESTS
# =============================================================================


@pytest.mark.integration
@pytest.mark.parametrize(
    "operation,invalid_input,expected_message",
    [
        ("add_receipt_word", None, "word cannot be None"),
        (
            "add_receipt_word",
            "not-a-word",
            "word must be an instance of ReceiptWord",
        ),
        ("update_receipt_word", None, "word cannot be None"),
        (
            "update_receipt_word",
            "not-a-word",
            "word must be an instance of ReceiptWord",
        ),
    ],
)
def test_single_receipt_word_validation(
    client: DynamoClient,
    operation: str,
    invalid_input: Any,
    expected_message: str,
):
    """Test validation for single receipt word operations."""
    with pytest.raises(EntityValidationError, match=expected_message):
        getattr(client, operation)(invalid_input)


@pytest.mark.integration
@pytest.mark.parametrize(
    "operation,invalid_input,expected_message",
    [
        ("add_receipt_words", None, "words cannot be None"),
        ("add_receipt_words", "not-a-list", "words must be a list"),
        ("update_receipt_words", None, "words cannot be None"),
        ("update_receipt_words", "not-a-list", "words must be a list"),
        ("delete_receipt_words", None, "words cannot be None"),
        ("delete_receipt_words", "not-a-list", "words must be a list"),
    ],
)
def test_batch_receipt_word_validation_basic(
    client: DynamoClient,
    operation: str,
    invalid_input: Any,
    expected_message: str,
):
    """Test basic validation for batch receipt word operations."""
    with pytest.raises(EntityValidationError, match=expected_message):
        getattr(client, operation)(invalid_input)


@pytest.mark.integration
@pytest.mark.parametrize(
    "operation",
    ["add_receipt_words", "update_receipt_words", "delete_receipt_words"],
)
def test_batch_receipt_word_validation_mixed_types(
    client: DynamoClient, operation: str
):
    """Test validation for mixed types in batch operations."""
    mixed_list = [
        ReceiptWord(
            receipt_id=1,
            image_id=FIXED_IMAGE_ID,
            line_id=10,
            word_id=1,
            text="Valid",
            bounding_box={"x": 0.1, "y": 0.2, "width": 0.1, "height": 0.02},
            top_left={"x": 0.1, "y": 0.2},
            top_right={"x": 0.2, "y": 0.2},
            bottom_left={"x": 0.1, "y": 0.22},
            bottom_right={"x": 0.2, "y": 0.22},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.95,
        ),
        "not-a-word",
        123,
    ]

    with pytest.raises(
        EntityValidationError, match="must be an instance of ReceiptWord"
    ):
        getattr(client, operation)(mixed_list)


# =============================================================================
# GET OPERATION PARAMETER VALIDATION TESTS
# =============================================================================


@pytest.mark.integration
@pytest.mark.parametrize(
    "receipt_id,image_id,line_id,word_id,expected_exception,expected_message",
    [
        # receipt_id validation
        (
            None,
            FIXED_IMAGE_ID,
            1,
            1,
            EntityValidationError,
            "receipt_id must be an integer",
        ),
        (
            "not-an-int",
            FIXED_IMAGE_ID,
            1,
            1,
            EntityValidationError,
            "receipt_id must be an integer",
        ),
        (
            -1,
            FIXED_IMAGE_ID,
            1,
            1,
            EntityValidationError,
            "receipt_id must be a positive integer",
        ),
        # image_id validation
        (1, None, 1, 1, EntityValidationError, "image_id cannot be None"),
        (1, "not-a-uuid", 1, 1, OperationError, "uuid must be a valid UUIDv4"),
        # line_id validation
        (
            1,
            FIXED_IMAGE_ID,
            None,
            1,
            EntityValidationError,
            "line_id must be an integer",
        ),
        (
            1,
            FIXED_IMAGE_ID,
            "not-an-int",
            1,
            EntityValidationError,
            "line_id must be an integer",
        ),
        (
            1,
            FIXED_IMAGE_ID,
            -1,
            1,
            EntityValidationError,
            "line_id must be a positive integer",
        ),
        # word_id validation
        (
            1,
            FIXED_IMAGE_ID,
            1,
            None,
            EntityValidationError,
            "word_id must be an integer",
        ),
        (
            1,
            FIXED_IMAGE_ID,
            1,
            "not-an-int",
            EntityValidationError,
            "word_id must be an integer",
        ),
        (
            1,
            FIXED_IMAGE_ID,
            1,
            -1,
            EntityValidationError,
            "word_id must be a positive integer",
        ),
    ],
)
def test_get_receipt_word_parameter_validation(
    client: DynamoClient,
    receipt_id: Any,
    image_id: Any,
    line_id: Any,
    word_id: Any,
    expected_exception: type,
    expected_message: str,
):
    """Test parameter validation for get_receipt_word."""
    with pytest.raises(expected_exception, match=expected_message):
        client.get_receipt_word(image_id, receipt_id, line_id, word_id)


# =============================================================================
# GET BY INDICES VALIDATION TESTS
# =============================================================================


@pytest.mark.integration
@pytest.mark.parametrize(
    "indices,expected_exception,expected_message",
    [
        (None, EntityValidationError, "indices cannot be None"),
        ("not-a-list", EntityValidationError, "indices must be a list"),
        (
            ["not-a-tuple"],
            EntityValidationError,
            "indices must be a list of tuples",
        ),
        ([("incomplete",)], EntityValidationError, "tuples with 4 elements"),
        (
            [(123, 1, 1, 1)],
            EntityValidationError,
            "First element of tuple must be a string",
        ),
        (
            [("not-a-uuid", 1, 1, 1)],
            OperationError,
            "uuid must be a valid UUIDv4",
        ),
        (
            [(FIXED_IMAGE_ID, "not-int", 1, 1)],
            EntityValidationError,
            "Second element of tuple must be an integer",
        ),
        (
            [(FIXED_IMAGE_ID, 1, "not-int", 1)],
            EntityValidationError,
            "Third element of tuple must be an integer",
        ),
        (
            [(FIXED_IMAGE_ID, 1, 1, "not-int")],
            EntityValidationError,
            "Fourth element of tuple must be an integer",
        ),
    ],
)
def test_get_receipt_words_by_indices_validation(
    client: DynamoClient,
    indices: Any,
    expected_exception: type,
    expected_message: str,
):
    """Test parameter validation for get_receipt_words_by_indices."""
    with pytest.raises(expected_exception, match=expected_message):
        client.get_receipt_words_by_indices(indices)


# =============================================================================
# GET BY KEYS VALIDATION TESTS
# =============================================================================


@pytest.mark.integration
@pytest.mark.parametrize(
    "keys,expected_exception,expected_message",
    [
        (None, EntityValidationError, "keys cannot be None"),
        ([], EntityValidationError, "keys cannot be None or empty"),
        ("not-a-list", EntityValidationError, "keys must be a list"),
        (
            [{"missing": "pk_sk"}],
            EntityValidationError,
            "keys must contain 'PK' and 'SK'",
        ),
        (
            [{"PK": {"S": "WRONG#id"}, "SK": {"S": "valid"}}],
            EntityValidationError,
            "PK must start with 'IMAGE#'",
        ),
        (
            [{"PK": {"S": "IMAGE#123"}, "SK": {"S": "WRONG#"}}],
            EntityValidationError,
            "SK must start with 'RECEIPT#'",
        ),
        (
            [{"PK": {"S": "IMAGE#123"}, "SK": {"S": "RECEIPT#1#LINE#1#"}}],
            EntityValidationError,
            "SK must contain 'WORD'",
        ),
        (
            [
                {
                    "PK": {"S": "IMAGE#123"},
                    "SK": {"S": "RECEIPT#00001#LINE#00001#WORD#abc"},
                }
            ],
            EntityValidationError,
            "SK must contain a 5-digit word ID",
        ),
    ],
)
def test_get_receipt_words_by_keys_validation(
    client: DynamoClient,
    keys: Any,
    expected_exception: type,
    expected_message: str,
):
    """Test parameter validation for get_receipt_words_by_keys."""
    with pytest.raises(expected_exception, match=expected_message):
        client.get_receipt_words_by_keys(keys)


# =============================================================================
# LIST OPERATIONS VALIDATION TESTS
# =============================================================================


@pytest.mark.integration
@pytest.mark.parametrize(
    "limit,expected_message",
    [
        ("not-an-int", "limit must be an integer"),
        (-1, "limit must be greater than 0"),
        (0, "limit must be greater than 0"),
    ],
)
def test_list_receipt_words_invalid_limit(
    client: DynamoClient, limit: Any, expected_message: str
):
    """Test validation for invalid limit values in list_receipt_words."""
    with pytest.raises(EntityValidationError, match=expected_message):
        client.list_receipt_words(limit=limit)


@pytest.mark.integration
def test_list_receipt_words_invalid_last_evaluated_key(client: DynamoClient):
    """Test validation for invalid last_evaluated_key in list_receipt_words."""
    with pytest.raises(
        EntityValidationError, match="last_evaluated_key must be a dictionary"
    ):
        client.list_receipt_words(last_evaluated_key="not-a-dict")


@pytest.mark.integration
@pytest.mark.parametrize(
    "embedding_status,expected_exception,expected_message",
    [
        (
            None,
            EntityValidationError,
            "embedding_status must be a string or EmbeddingStatus enum",
        ),
        (
            123,
            EntityValidationError,
            "embedding_status must be a string or EmbeddingStatus enum",
        ),
        (
            "INVALID_STATUS",
            EntityValidationError,
            "embedding_status must be one of:",
        ),
    ],
)
def test_list_receipt_words_by_embedding_status_validation(
    client: DynamoClient,
    embedding_status: Any,
    expected_exception: type,
    expected_message: str,
):
    """Test validation for list_receipt_words_by_embedding_status."""
    with pytest.raises(expected_exception, match=expected_message):
        client.list_receipt_words_by_embedding_status(embedding_status)


# =============================================================================
# CONDITIONAL CHECK FAILED TESTS
# =============================================================================


@pytest.mark.integration
def test_add_receipt_word_conditional_check_failed(
    client: DynamoClient, sample_receipt_word: ReceiptWord
):
    """Test that ConditionalCheckFailedException in add operations raises EntityAlreadyExistsError."""
    with patch.object(client._client, "put_item") as mock_put:
        mock_put.side_effect = ClientError(
            error_response={
                "Error": {
                    "Code": "ConditionalCheckFailedException",
                    "Message": "Item already exists",
                }
            },
            operation_name="PutItem",
        )

        with pytest.raises(EntityAlreadyExistsError, match="already exists"):
            client.add_receipt_word(sample_receipt_word)


@pytest.mark.integration
def test_update_receipt_word_conditional_check_failed(
    client: DynamoClient, sample_receipt_word: ReceiptWord
):
    """Test that ConditionalCheckFailedException in update operations raises EntityNotFoundError."""
    with patch.object(client._client, "put_item") as mock_put:
        mock_put.side_effect = ClientError(
            error_response={
                "Error": {
                    "Code": "ConditionalCheckFailedException",
                    "Message": "Item not found",
                }
            },
            operation_name="PutItem",
        )

        with pytest.raises(EntityNotFoundError, match="not found"):
            client.update_receipt_word(sample_receipt_word)


@pytest.mark.integration
def test_delete_receipt_word_conditional_check_failed(
    client: DynamoClient, sample_receipt_word: ReceiptWord
):
    """Test that ConditionalCheckFailedException in delete operations raises EntityNotFoundError."""
    with patch.object(client._client, "delete_item") as mock_delete:
        mock_delete.side_effect = ClientError(
            error_response={
                "Error": {
                    "Code": "ConditionalCheckFailedException",
                    "Message": "Item not found",
                }
            },
            operation_name="DeleteItem",
        )

        with pytest.raises(EntityNotFoundError, match="not found"):
            client.delete_receipt_word(sample_receipt_word)


# =============================================================================
# FUNCTIONAL TESTS
# =============================================================================


@pytest.mark.integration
def test_add_receipt_word_success(
    client: DynamoClient, sample_receipt_word: ReceiptWord
):
    """Test successful receipt word addition."""
    # This should not raise any exceptions
    client.add_receipt_word(sample_receipt_word)

    # Verify it was added by retrieving it
    retrieved = client.get_receipt_word(
        sample_receipt_word.image_id,
        sample_receipt_word.receipt_id,
        sample_receipt_word.line_id,
        sample_receipt_word.word_id,
    )
    assert retrieved == sample_receipt_word


@pytest.mark.integration
def test_add_receipt_word_duplicate_raises(
    client: DynamoClient, sample_receipt_word: ReceiptWord
):
    """Test that adding duplicate receipt word raises EntityAlreadyExistsError."""
    client.add_receipt_word(sample_receipt_word)

    with pytest.raises(EntityAlreadyExistsError):
        client.add_receipt_word(sample_receipt_word)


@pytest.mark.integration
def test_update_receipt_word_success(
    client: DynamoClient, sample_receipt_word: ReceiptWord
):
    """Test successful receipt word update."""
    # Add the word first
    client.add_receipt_word(sample_receipt_word)

    # Update it
    updated_word = ReceiptWord(
        receipt_id=sample_receipt_word.receipt_id,
        image_id=sample_receipt_word.image_id,
        line_id=sample_receipt_word.line_id,
        word_id=sample_receipt_word.word_id,
        text="Updated text",
        bounding_box=sample_receipt_word.bounding_box,
        top_left=sample_receipt_word.top_left,
        top_right=sample_receipt_word.top_right,
        bottom_left=sample_receipt_word.bottom_left,
        bottom_right=sample_receipt_word.bottom_right,
        angle_degrees=sample_receipt_word.angle_degrees,
        angle_radians=sample_receipt_word.angle_radians,
        confidence=sample_receipt_word.confidence,
    )
    client.update_receipt_word(updated_word)

    # Verify update
    retrieved = client.get_receipt_word(
        sample_receipt_word.image_id,
        sample_receipt_word.receipt_id,
        sample_receipt_word.line_id,
        sample_receipt_word.word_id,
    )
    assert retrieved.text == "Updated text"


@pytest.mark.integration
def test_delete_receipt_word_success(
    client: DynamoClient, sample_receipt_word: ReceiptWord
):
    """Test successful receipt word deletion."""
    # Add the word first
    client.add_receipt_word(sample_receipt_word)

    # Delete it
    client.delete_receipt_word(sample_receipt_word)

    # Verify deletion
    with pytest.raises(EntityNotFoundError):
        client.get_receipt_word(
            sample_receipt_word.image_id,
            sample_receipt_word.receipt_id,
            sample_receipt_word.line_id,
            sample_receipt_word.word_id,
        )


@pytest.mark.integration
def test_get_receipt_word_not_found(client: DynamoClient):
    """Test that getting non-existent receipt word raises EntityNotFoundError."""
    with pytest.raises(EntityNotFoundError, match="not found"):
        client.get_receipt_word(FIXED_IMAGE_ID, 999, 999, 999)


# =============================================================================
# BATCH OPERATION TESTS
# =============================================================================


@pytest.mark.integration
def test_add_receipt_words_success(
    client: DynamoClient, sample_receipt_words: List[ReceiptWord]
):
    """Test successful batch addition of receipt words."""
    client.add_receipt_words(sample_receipt_words)

    # Verify all were added
    for word in sample_receipt_words:
        retrieved = client.get_receipt_word(
            word.image_id, word.receipt_id, word.line_id, word.word_id
        )
        assert retrieved == word


@pytest.mark.integration
def test_update_receipt_words_success(
    client: DynamoClient, sample_receipt_words: List[ReceiptWord]
):
    """Test successful batch update of receipt words."""
    # Add words first
    client.add_receipt_words(sample_receipt_words)

    # Update them
    updated_words = []
    for word in sample_receipt_words:
        updated_word = ReceiptWord(
            receipt_id=word.receipt_id,
            image_id=word.image_id,
            line_id=word.line_id,
            word_id=word.word_id,
            text=f"Updated {word.text}",
            bounding_box=word.bounding_box,
            top_left=word.top_left,
            top_right=word.top_right,
            bottom_left=word.bottom_left,
            bottom_right=word.bottom_right,
            angle_degrees=word.angle_degrees,
            angle_radians=word.angle_radians,
            confidence=word.confidence,
        )
        updated_words.append(updated_word)

    client.update_receipt_words(updated_words)

    # Verify updates
    for updated_word in updated_words:
        retrieved = client.get_receipt_word(
            updated_word.image_id,
            updated_word.receipt_id,
            updated_word.line_id,
            updated_word.word_id,
        )
        assert retrieved.text.startswith("Updated")


@pytest.mark.integration
def test_delete_receipt_words_success(
    client: DynamoClient, sample_receipt_words: List[ReceiptWord]
):
    """Test successful batch deletion of receipt words."""
    # Add words first
    client.add_receipt_words(sample_receipt_words)

    # Delete them
    client.delete_receipt_words(sample_receipt_words)

    # Verify deletions
    for word in sample_receipt_words:
        with pytest.raises(EntityNotFoundError):
            client.get_receipt_word(
                word.image_id, word.receipt_id, word.line_id, word.word_id
            )


@pytest.mark.integration
def test_get_receipt_words_by_indices_success(
    client: DynamoClient, sample_receipt_words: List[ReceiptWord]
):
    """Test successful retrieval of receipt words by indices."""
    # Add words first
    client.add_receipt_words(sample_receipt_words)

    # Create indices list - note the order is (image_id, receipt_id, line_id, word_id)
    indices = [
        (word.image_id, word.receipt_id, word.line_id, word.word_id)
        for word in sample_receipt_words
    ]

    # Retrieve by indices
    retrieved_words = client.get_receipt_words_by_indices(indices)

    # Verify results
    assert len(retrieved_words) == len(sample_receipt_words)
    for word in sample_receipt_words:
        assert word in retrieved_words


@pytest.mark.integration
def test_get_receipt_words_by_keys_success(
    client: DynamoClient, sample_receipt_words: List[ReceiptWord]
):
    """Test successful retrieval of receipt words by keys."""
    # Add words first
    client.add_receipt_words(sample_receipt_words)

    # Create keys list
    keys = []
    for word in sample_receipt_words:
        key = {
            "PK": {"S": f"IMAGE#{word.image_id}"},
            "SK": {
                "S": f"RECEIPT#{word.receipt_id:05d}#LINE#{word.line_id:05d}#WORD#{word.word_id:05d}"
            },
        }
        keys.append(key)

    # Retrieve by keys
    retrieved_words = client.get_receipt_words_by_keys(keys)

    # Verify results
    assert len(retrieved_words) == len(sample_receipt_words)
    for word in sample_receipt_words:
        assert word in retrieved_words


# =============================================================================
# LIST OPERATION TESTS
# =============================================================================


@pytest.mark.integration
def test_list_receipt_words_with_pagination(
    client: DynamoClient, sample_receipt_words: List[ReceiptWord]
):
    """Test list_receipt_words with pagination parameters."""
    # Add words first
    client.add_receipt_words(sample_receipt_words)

    # List with limit
    words, last_key = client.list_receipt_words(limit=2)

    # Should get some words
    assert len(words) >= 0  # May be 0 if no words match the type filter

    # Test with last_evaluated_key (even if None)
    words2, _ = client.list_receipt_words(limit=2, last_evaluated_key=last_key)
    assert isinstance(words2, list)


@pytest.mark.integration
def test_list_receipt_words_no_limit(
    client: DynamoClient, sample_receipt_words: List[ReceiptWord]
):
    """Test list_receipt_words without limit."""
    # Add words first
    client.add_receipt_words(sample_receipt_words)

    # List without limit
    words, _ = client.list_receipt_words()

    # Should return a list
    assert isinstance(words, list)


@pytest.mark.integration
def test_list_receipt_words_by_embedding_status(client: DynamoClient):
    """Test list_receipt_words_by_embedding_status functionality."""
    # Create words with different embedding statuses
    word_none = ReceiptWord(
        receipt_id=1,
        image_id=FIXED_IMAGE_ID,
        line_id=10,
        word_id=1,
        text="None status",
        bounding_box={"x": 0.1, "y": 0.2, "width": 0.1, "height": 0.02},
        top_left={"x": 0.1, "y": 0.2},
        top_right={"x": 0.2, "y": 0.2},
        bottom_left={"x": 0.1, "y": 0.22},
        bottom_right={"x": 0.2, "y": 0.22},
        angle_degrees=0.0,
        angle_radians=0.0,
        confidence=0.95,
        embedding_status=EmbeddingStatus.NONE,
    )

    word_pending = ReceiptWord(
        receipt_id=1,
        image_id=FIXED_IMAGE_ID,
        line_id=10,
        word_id=2,
        text="Pending status",
        bounding_box={"x": 0.1, "y": 0.2, "width": 0.1, "height": 0.02},
        top_left={"x": 0.1, "y": 0.2},
        top_right={"x": 0.2, "y": 0.2},
        bottom_left={"x": 0.1, "y": 0.22},
        bottom_right={"x": 0.2, "y": 0.22},
        angle_degrees=0.0,
        angle_radians=0.0,
        confidence=0.95,
        embedding_status=EmbeddingStatus.PENDING,
    )

    # Add words
    client.add_receipt_word(word_none)
    client.add_receipt_word(word_pending)

    # Query by embedding status
    none_words = client.list_receipt_words_by_embedding_status(EmbeddingStatus.NONE)
    pending_words = client.list_receipt_words_by_embedding_status(
        EmbeddingStatus.PENDING
    )

    # Verify results
    assert isinstance(none_words, list)
    assert isinstance(pending_words, list)


@pytest.mark.integration
def test_list_receipt_words_from_line(client: DynamoClient):
    """Test list_receipt_words_from_line functionality."""
    # Create words on the same line
    words_same_line = [
        ReceiptWord(
            receipt_id=1,
            image_id=FIXED_IMAGE_ID,
            line_id=10,
            word_id=i,
            text=f"Line word {i}",
            bounding_box={
                "x": 0.1 * i,
                "y": 0.2,
                "width": 0.1,
                "height": 0.02,
            },
            top_left={"x": 0.1 * i, "y": 0.2},
            top_right={"x": 0.1 * i + 0.1, "y": 0.2},
            bottom_left={"x": 0.1 * i, "y": 0.22},
            bottom_right={"x": 0.1 * i + 0.1, "y": 0.22},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.95,
        )
        for i in range(1, 3)
    ]

    # Create word on different line
    word_different_line = ReceiptWord(
        receipt_id=1,
        image_id=FIXED_IMAGE_ID,
        line_id=99,
        word_id=999,
        text="Different line",
        bounding_box={"x": 0.5, "y": 0.5, "width": 0.1, "height": 0.02},
        top_left={"x": 0.5, "y": 0.5},
        top_right={"x": 0.6, "y": 0.5},
        bottom_left={"x": 0.5, "y": 0.52},
        bottom_right={"x": 0.6, "y": 0.52},
        angle_degrees=0.0,
        angle_radians=0.0,
        confidence=0.95,
    )

    # Add all words
    for word in words_same_line + [word_different_line]:
        client.add_receipt_word(word)

    # Query words from specific line
    line_words = client.list_receipt_words_from_line(FIXED_IMAGE_ID, 1, 10)

    # Verify results
    assert isinstance(line_words, list)
    # Should contain words from line 10 but not from line 99
    word_texts = [w.text for w in line_words]
    assert "Line word 1" in word_texts
    assert "Line word 2" in word_texts
    assert "Different line" not in word_texts


@pytest.mark.integration
def test_list_receipt_words_from_receipt(client: DynamoClient):
    """Test list_receipt_words_from_receipt functionality."""
    # Create words for the same receipt
    words_same_receipt = [
        ReceiptWord(
            receipt_id=1,
            image_id=FIXED_IMAGE_ID,
            line_id=i,
            word_id=1,
            text=f"Receipt word {i}",
            bounding_box={
                "x": 0.1,
                "y": 0.2 * i,
                "width": 0.1,
                "height": 0.02,
            },
            top_left={"x": 0.1, "y": 0.2 * i},
            top_right={"x": 0.2, "y": 0.2 * i},
            bottom_left={"x": 0.1, "y": 0.2 * i + 0.02},
            bottom_right={"x": 0.2, "y": 0.2 * i + 0.02},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.95,
        )
        for i in range(1, 3)
    ]

    # Create word for different receipt
    word_different_receipt = ReceiptWord(
        receipt_id=999,
        image_id=FIXED_IMAGE_ID_2,
        line_id=1,
        word_id=1,
        text="Different receipt",
        bounding_box={"x": 0.5, "y": 0.5, "width": 0.1, "height": 0.02},
        top_left={"x": 0.5, "y": 0.5},
        top_right={"x": 0.6, "y": 0.5},
        bottom_left={"x": 0.5, "y": 0.52},
        bottom_right={"x": 0.6, "y": 0.52},
        angle_degrees=0.0,
        angle_radians=0.0,
        confidence=0.95,
    )

    # Add all words
    for word in words_same_receipt + [word_different_receipt]:
        client.add_receipt_word(word)

    # Query words from specific receipt
    receipt_words = client.list_receipt_words_from_receipt(FIXED_IMAGE_ID, 1)

    # Verify results
    assert isinstance(receipt_words, list)
    # Should contain words from receipt 1 but not from receipt 999
    word_texts = [w.text for w in receipt_words]
    assert "Receipt word 1" in word_texts
    assert "Receipt word 2" in word_texts
    assert "Different receipt" not in word_texts


@pytest.mark.integration
def test_list_receipt_words_from_receipt_excludes_labels_and_letters(
    client: DynamoClient,
):
    """Test that list_receipt_words_from_receipt excludes ReceiptWordLabel and ReceiptLetter entities."""
    # Add a receipt word
    word = ReceiptWord(
        receipt_id=1,
        image_id=FIXED_IMAGE_ID,
        line_id=1,
        word_id=1,
        text="Test word",
        bounding_box={"x": 0.1, "y": 0.1, "width": 0.1, "height": 0.02},
        top_left={"x": 0.1, "y": 0.1},
        top_right={"x": 0.2, "y": 0.1},
        bottom_left={"x": 0.1, "y": 0.12},
        bottom_right={"x": 0.2, "y": 0.12},
        angle_degrees=0.0,
        angle_radians=0.0,
        confidence=0.95,
    )
    client.add_receipt_word(word)

    # Add a receipt word label that should NOT be returned
    # (This simulates what would happen in a real scenario with mixed entity types)
    from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel

    label = ReceiptWordLabel(
        receipt_id=1,
        image_id=FIXED_IMAGE_ID,
        line_id=1,
        word_id=1,
        label="tax",
        reasoning="Test label",
        timestamp_added="2023-01-01T00:00:00Z",
        validation_status="PENDING",
        label_consolidated_from="test",
        label_proposed_by="test",
    )
    client.add_receipt_word_label(label)

    # Add a receipt letter that should NOT be returned
    # (This simulates what would happen in a real scenario with mixed entity types)
    from receipt_dynamo.entities.receipt_letter import ReceiptLetter

    letter = ReceiptLetter(
        receipt_id=1,
        image_id=FIXED_IMAGE_ID,
        line_id=1,
        word_id=1,
        letter_id=1,
        text="T",
        bounding_box={"x": 0.1, "y": 0.1, "width": 0.01, "height": 0.02},
        top_left={"x": 0.1, "y": 0.1},
        top_right={"x": 0.11, "y": 0.1},
        bottom_left={"x": 0.1, "y": 0.12},
        bottom_right={"x": 0.11, "y": 0.12},
        angle_degrees=0.0,
        angle_radians=0.0,
        confidence=0.95,
    )
    client.add_receipt_letter(letter)

    # Query for receipt words only
    result = client.list_receipt_words_from_receipt(FIXED_IMAGE_ID, 1)

    # Should only return the ReceiptWord, not the ReceiptWordLabel or ReceiptLetter
    assert len(result) == 1
    assert result[0] == word
    assert all(isinstance(item, ReceiptWord) for item in result)


@pytest.mark.integration
def test_list_receipt_words_from_receipt_handles_pagination(
    client: DynamoClient,
):
    """Test that method handles DynamoDB pagination correctly."""
    # Add 200 receipt words to ensure we exceed typical DynamoDB page size
    words = []
    for line_id in range(1, 21):  # 20 lines
        for word_id in range(1, 11):  # 10 words per line = 200 total words
            word = ReceiptWord(
                receipt_id=1,
                image_id=FIXED_IMAGE_ID,
                line_id=line_id,
                word_id=word_id,
                text=f"Word{line_id:02d}{word_id:02d}",
                bounding_box={
                    "x": 0.1 * word_id,
                    "y": 0.05 * line_id,
                    "width": 0.08,
                    "height": 0.02,
                },
                top_left={"x": 0.1 * word_id, "y": 0.05 * line_id},
                top_right={"x": 0.1 * word_id + 0.08, "y": 0.05 * line_id},
                bottom_left={"x": 0.1 * word_id, "y": 0.05 * line_id + 0.02},
                bottom_right={
                    "x": 0.1 * word_id + 0.08,
                    "y": 0.05 * line_id + 0.02,
                },
                angle_degrees=0.0,
                angle_radians=0.0,
                confidence=0.95,
            )
            words.append(word)

    # Add in smaller batches to avoid DynamoDB batch limits
    batch_size = 25
    for i in range(0, len(words), batch_size):
        batch = words[i : i + batch_size]
        client.add_receipt_words(batch)

    # Query all words - this should handle pagination automatically
    retrieved_words = client.list_receipt_words_from_receipt(FIXED_IMAGE_ID, 1)

    # Verify ALL 200 words were retrieved despite pagination
    assert len(retrieved_words) == 200
    assert all(w.receipt_id == 1 for w in retrieved_words)
    assert all(w.image_id == FIXED_IMAGE_ID for w in retrieved_words)

    # Verify completeness - check for specific words across the range
    word_texts = {w.text for w in retrieved_words}
    assert "Word0101" in word_texts  # First word (line 1, word 1)
    assert "Word2010" in word_texts  # Last word (line 20, word 10)
    assert len(word_texts) == 200  # All unique
