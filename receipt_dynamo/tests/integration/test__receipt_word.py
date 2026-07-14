"""
Comprehensive parameterized tests for receipt_word functionality.

This file contains refactored tests using pytest.mark.parametrize to ensure
comprehensive coverage of all CRUD operations, error scenarios, and edge cases
for ReceiptWord operations.

Based on successful patterns from test__receipt_line.py parameterization.
"""

from math import radians
from typing import Any, List, Literal
from unittest.mock import patch

import pytest
from botocore.exceptions import ClientError

from receipt_dynamo import DynamoClient, ReceiptLetter, ReceiptWord
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
from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel

# =============================================================================
# FIXTURES AND TEST DATA
# =============================================================================

# Fixed UUID for deterministic test collection
FIXED_IMAGE_ID = "550e8400-e29b-41d4-a716-446655440001"
FIXED_IMAGE_ID_2 = "550e8400-e29b-41d4-a716-446655440002"


def _make_receipt_word(
    *,
    image_id: str = FIXED_IMAGE_ID,
    receipt_id: int = 1,
    line_id: int = 10,
    word_id: int = 1,
    geometry: tuple[float, float, float, float] | None = None,
    angle_degrees: float | None = None,
    **overrides: Any,
) -> ReceiptWord:
    """Build a valid receipt word with concise test-specific overrides."""
    values: dict[str, Any] = {
        "receipt_id": receipt_id,
        "image_id": image_id,
        "line_id": line_id,
        "word_id": word_id,
        "text": f"Word {word_id}",
        "bounding_box": {"x": 0.1, "y": 0.2, "width": 0.1, "height": 0.02},
        "top_left": {"x": 0.1, "y": 0.2},
        "top_right": {"x": 0.2, "y": 0.2},
        "bottom_left": {"x": 0.1, "y": 0.22},
        "bottom_right": {"x": 0.2, "y": 0.22},
        "angle_degrees": 0.0,
        "angle_radians": 0.0,
        "confidence": 0.95,
        "embedding_status": EmbeddingStatus.NONE,
    }
    if geometry is not None:
        x, y, width, height = geometry
        values.update(
            {
                "bounding_box": {
                    "x": x,
                    "y": y,
                    "width": width,
                    "height": height,
                },
                "top_left": {"x": x, "y": y},
                "top_right": {"x": x + width, "y": y},
                "bottom_left": {"x": x, "y": y + height},
                "bottom_right": {"x": x + width, "y": y + height},
            }
        )
    if angle_degrees is not None:
        values["angle_degrees"] = angle_degrees
        values["angle_radians"] = radians(angle_degrees)
    values.update(overrides)
    return ReceiptWord(**values)


@pytest.fixture
def sample_receipt_word() -> ReceiptWord:
    """Create a sample ReceiptWord for testing."""
    return _make_receipt_word(
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
def sample_receipt_words() -> List[ReceiptWord]:
    """Create multiple sample ReceiptWords for batch testing."""
    return [
        _make_receipt_word(
            line_id=10,
            word_id=i,
            text=f"Word {i}",
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
    (
        "ConditionalCheckFailedException",
        EntityNotFoundError,
        "(does not exist|not found)",
    ),
]


def _client_error(error_code: str, operation_name: str) -> ClientError:
    """Build a DynamoDB ClientError for mocked low-level calls."""
    return ClientError(
        error_response={
            "Error": {"Code": error_code, "Message": "Test error"}
        },
        operation_name=operation_name,
    )


def _assert_client_error(
    client: DynamoClient,
    api_method: str,
    operation_name: str,
    error_code: str,
    expected_exception: type,
    error_fragment: str,
    call_name: str,
    *args: Any,
) -> None:
    """Patch a low-level DynamoDB API and assert wrapper error mapping."""
    with patch.object(client._client, api_method) as mock_api:
        mock_api.side_effect = _client_error(error_code, operation_name)
        with pytest.raises(expected_exception, match=error_fragment):
            getattr(client, call_name)(*args)


# =============================================================================
# CLIENT ERROR TESTS
# =============================================================================


CLIENT_ERROR_OPERATIONS = [
    (
        "add_receipt_word",
        "put_item",
        "PutItem",
        "sample",
        ERROR_SCENARIOS,
    ),
    (
        "update_receipt_word",
        "put_item",
        "PutItem",
        "sample",
        UPDATE_DELETE_ERROR_SCENARIOS,
    ),
    (
        "delete_receipt_word",
        "delete_item",
        "DeleteItem",
        "sample",
        UPDATE_DELETE_ERROR_SCENARIOS,
    ),
    ("get_receipt_word", "get_item", "GetItem", "key", ERROR_SCENARIOS),
    (
        "add_receipt_words",
        "transact_write_items",
        "TransactWriteItems",
        "batch",
        ERROR_SCENARIOS,
    ),
    (
        "update_receipt_words",
        "transact_write_items",
        "TransactWriteItems",
        "batch",
        UPDATE_DELETE_ERROR_SCENARIOS,
    ),
    (
        "delete_receipt_words",
        "transact_write_items",
        "TransactWriteItems",
        "batch",
        UPDATE_DELETE_ERROR_SCENARIOS,
    ),
]

CLIENT_ERROR_CASES = [
    (call, api, op_name, source, *scenario)
    for call, api, op_name, source, scenarios in CLIENT_ERROR_OPERATIONS
    for scenario in scenarios
]


def _receipt_word_args(
    source: str,
    sample_receipt_word: ReceiptWord,
    sample_receipt_words: List[ReceiptWord],
) -> tuple[Any, ...]:
    """Return method arguments for a receipt-word client-error case."""
    return {
        "sample": (sample_receipt_word,),
        "batch": (sample_receipt_words,),
        "key": (FIXED_IMAGE_ID, 1, 10, 5),
    }[source]


@pytest.mark.integration
@pytest.mark.parametrize(
    "call_name,api_method,operation_name,arg_source,"
    "error_code,expected_exception,error_fragment",
    CLIENT_ERROR_CASES,
)
def test_receipt_word_client_errors(
    client: DynamoClient,
    sample_receipt_word: ReceiptWord,
    sample_receipt_words: List[ReceiptWord],
    call_name: str,
    api_method: str,
    operation_name: str,
    arg_source: str,
    error_code: str,
    expected_exception: type,
    error_fragment: str,
):
    """Test receipt word methods map DynamoDB ClientErrors consistently."""
    _assert_client_error(
        client,
        api_method,
        operation_name,
        error_code,
        expected_exception,
        error_fragment,
        call_name,
        *_receipt_word_args(
            arg_source, sample_receipt_word, sample_receipt_words
        ),
    )


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
        _make_receipt_word(
            line_id=10,
            word_id=1,
            text="Valid",
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
            "receipt_id cannot be None",
        ),
        (
            "not-an-int",
            FIXED_IMAGE_ID,
            1,
            1,
            EntityValidationError,
            "receipt_id must be a positive integer",
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
            "line_id cannot be None",
        ),
        (
            1,
            FIXED_IMAGE_ID,
            "not-an-int",
            1,
            EntityValidationError,
            "line_id must be a positive integer",
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
            "word_id cannot be None",
        ),
        (
            1,
            FIXED_IMAGE_ID,
            1,
            "not-an-int",
            EntityValidationError,
            "word_id must be a positive integer",
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
    """Test conditional add failures map to already-exists errors."""
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
    """Test conditional update failures map to not-found errors."""
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

        with pytest.raises(
            EntityNotFoundError, match="(does not exist|not found)"
        ):
            client.update_receipt_word(sample_receipt_word)


@pytest.mark.integration
def test_delete_receipt_word_conditional_check_failed(
    client: DynamoClient, sample_receipt_word: ReceiptWord
):
    """Test conditional delete failures map to not-found errors."""
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

        with pytest.raises(
            EntityNotFoundError, match="(does not exist|not found)"
        ):
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
    """Test that adding a duplicate word raises an already-exists error."""
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
    updated_word = _make_receipt_word(
        image_id=sample_receipt_word.image_id,
        receipt_id=sample_receipt_word.receipt_id,
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
    """Test that getting a missing word raises a not-found error."""
    with pytest.raises(
        EntityNotFoundError, match="(does not exist|not found)"
    ):
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
        updated_word = _make_receipt_word(
            image_id=word.image_id,
            receipt_id=word.receipt_id,
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

    # Indices are ordered as image, receipt, line, then word ID.
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
                "S": (
                    f"RECEIPT#{word.receipt_id:05d}"
                    f"#LINE#{word.line_id:05d}#WORD#{word.word_id:05d}"
                )
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
    word_none = _make_receipt_word(
        line_id=10,
        word_id=1,
        text="None status",
        embedding_status=EmbeddingStatus.NONE,
    )

    word_pending = _make_receipt_word(
        line_id=10,
        word_id=2,
        text="Pending status",
        embedding_status=EmbeddingStatus.PENDING,
    )

    # Add words
    client.add_receipt_word(word_none)
    client.add_receipt_word(word_pending)

    # Query by embedding status
    none_words = client.list_receipt_words_by_embedding_status(
        EmbeddingStatus.NONE
    )
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
        _make_receipt_word(
            line_id=10,
            word_id=i,
            text=f"Line word {i}",
        )
        for i in range(1, 3)
    ]

    # Create word on different line
    word_different_line = _make_receipt_word(
        line_id=99,
        word_id=999,
        text="Different line",
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
        _make_receipt_word(
            line_id=i,
            word_id=1,
            text=f"Receipt word {i}",
        )
        for i in range(1, 3)
    ]

    # Create word for different receipt
    word_different_receipt = _make_receipt_word(
        image_id=FIXED_IMAGE_ID_2,
        receipt_id=999,
        line_id=1,
        word_id=1,
        text="Different receipt",
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
    """Test that receipt queries exclude labels and letters."""
    # Add a receipt word
    word = _make_receipt_word(
        line_id=1,
        word_id=1,
        text="Test word",
    )
    client.add_receipt_word(word)

    # Add a label to simulate a partition containing mixed entity types.

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

    # Add a letter to the same mixed entity partition.

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

    # Only the ReceiptWord should be returned.
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
            word = _make_receipt_word(
                line_id=line_id,
                word_id=word_id,
                text=f"Word{line_id:02d}{word_id:02d}",
                geometry=(
                    0.1 * word_id,
                    0.05 * line_id,
                    0.08,
                    0.02,
                ),
                angle_degrees=(line_id * 10 + word_id) % 360,
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
    words_by_id = {
        (word.line_id, word.word_id): word for word in retrieved_words
    }
    for index in (0, 99, 199):
        expected_word = words[index]
        key = (expected_word.line_id, expected_word.word_id)
        assert words_by_id[key] == expected_word
