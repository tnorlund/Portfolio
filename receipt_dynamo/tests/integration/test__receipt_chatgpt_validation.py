import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

import pytest
from botocore.exceptions import ClientError

from receipt_dynamo import ReceiptChatGPTValidation
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.data.shared_exceptions import (
    DynamoDBAccessError,
    DynamoDBError,
    DynamoDBServerError,
    DynamoDBThroughputError,
    DynamoDBValidationError,
    EntityAlreadyExistsError,
    EntityNotFoundError,
    EntityValidationError,
    OperationError,
)

pytestmark = [pytest.mark.integration, pytest.mark.unused_in_production]

TIMESTAMP_BASE = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _timestamp(offset: int = 0) -> str:
    """Return a valid, distinct ISO timestamp for generated test entities."""
    return (TIMESTAMP_BASE + timedelta(microseconds=offset)).isoformat()


def _client_error(error_code, operation, message=None):
    return ClientError(
        {
            "Error": {
                "Code": error_code,
                "Message": message or f"Mocked {error_code}",
            }
        },
        operation,
    )


def _patch_client_error(
    mocker, client, method, error_code, operation, message=None
):
    return mocker.patch.object(
        client._client,
        method,
        side_effect=_client_error(error_code, operation, message),
    )


def _expected_client_exception(
    error_code, conditional_exception=DynamoDBError
):
    """Map DynamoDB error codes to the public exception contract."""
    return {
        "ConditionalCheckFailedException": conditional_exception,
        "ResourceNotFoundException": OperationError,
        "ProvisionedThroughputExceededException": DynamoDBThroughputError,
        "InternalServerError": DynamoDBServerError,
        "ValidationException": EntityValidationError,
        "AccessDeniedException": DynamoDBError,
        "TransactionCanceledException": DynamoDBError,
    }.get(error_code, DynamoDBError)


@pytest.fixture
def sample_receipt_chatgpt_validation():
    """Create a sample ReceiptChatGPTValidation object for testing."""
    receipt_id = 12345
    image_id = str(uuid.uuid4())
    original_status = "PENDING"
    revised_status = "VALID"
    reasoning = "All required fields are present and valid."
    corrections = [
        {
            "field": "total",
            "original": "15.50",
            "corrected": "15.50",
            "reason": "No correction needed.",
        }
    ]
    prompt = "Please validate this receipt..."
    response = "The receipt is valid."
    timestamp = _timestamp()
    metadata = {"confidence": 0.95}

    return ReceiptChatGPTValidation(
        receipt_id=receipt_id,
        image_id=image_id,
        original_status=original_status,
        revised_status=revised_status,
        reasoning=reasoning,
        corrections=corrections,
        prompt=prompt,
        response=response,
        timestamp=timestamp,
        metadata=metadata,
    )


@pytest.mark.integration
def test_addReceiptChatGPTValidation_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_chatgpt_validation: ReceiptChatGPTValidation,
):
    """Test that a ReceiptChatGPTValidation can be successfully added to DynamoDB."""
    client = DynamoClient(dynamodb_table)

    client.add_receipt_chat_gpt_validation(sample_receipt_chatgpt_validation)

    response = client._client.get_item(
        TableName=dynamodb_table,
        Key={
            "PK": {"S": f"IMAGE#{sample_receipt_chatgpt_validation.image_id}"},
            "SK": {
                "S": f"RECEIPT#{sample_receipt_chatgpt_validation.receipt_id}#ANALYSIS#VALIDATION#CHATGPT#{sample_receipt_chatgpt_validation.timestamp}"
            },
        },
    )

    assert "Item" in response
    assert (
        response["Item"]["original_status"]["S"]
        == sample_receipt_chatgpt_validation.original_status
    )
    assert (
        response["Item"]["revised_status"]["S"]
        == sample_receipt_chatgpt_validation.revised_status
    )
    assert (
        response["Item"]["reasoning"]["S"]
        == sample_receipt_chatgpt_validation.reasoning
    )


@pytest.mark.integration
def test_addReceiptChatGPTValidation_duplicate_raises(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_chatgpt_validation: ReceiptChatGPTValidation,
):
    """Test that adding a duplicate ChatGPT validation raises an error."""
    client = DynamoClient(dynamodb_table)

    client.add_receipt_chat_gpt_validation(sample_receipt_chatgpt_validation)

    from receipt_dynamo.data.shared_exceptions import EntityAlreadyExistsError

    with pytest.raises(
        EntityAlreadyExistsError,
        match="already exists",
    ):
        client.add_receipt_chat_gpt_validation(
            sample_receipt_chatgpt_validation
        )


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (None, "validation cannot be None"),
        (
            "not-a-validation",
            "validation must be an instance of ReceiptChatGPTValidation",
        ),
    ],
)
def test_addReceiptChatGPTValidation_invalid_parameters(
    dynamodb_table,
    sample_receipt_chatgpt_validation,
    mocker,
    invalid_input,
    expected_error,
):
    """Test adding a ChatGPT validation with invalid parameters."""
    client = DynamoClient(dynamodb_table)

    mocker.patch.object(client._client, "put_item")

    with pytest.raises(OperationError, match=expected_error):
        client.add_receipt_chat_gpt_validation(invalid_input)

    client._client.put_item.assert_not_called()


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_exception",
    [
        (
            "ConditionalCheckFailedException",
            "Item already exists",
            EntityAlreadyExistsError,
        ),
        (
            "ResourceNotFoundException",
            "Table not found",
            OperationError,
        ),
        (
            "ProvisionedThroughputExceededException",
            "Throughput exceeded",
            DynamoDBThroughputError,
        ),
        (
            "InternalServerError",
            "Internal server error",
            DynamoDBServerError,
        ),
        (
            "UnknownError",
            "Unknown error",
            DynamoDBError,
        ),
        (
            "ValidationException",
            "One or more parameters were invalid",
            EntityValidationError,
        ),
        ("AccessDeniedException", "Access denied", DynamoDBError),
    ],
)
def test_addReceiptChatGPTValidation_client_errors(
    dynamodb_table,
    sample_receipt_chatgpt_validation,
    mocker,
    error_code,
    error_message,
    expected_exception,
):
    """Test handling of client errors when adding a ChatGPT validation."""
    client = DynamoClient(dynamodb_table)

    _patch_client_error(
        mocker, client, "put_item", error_code, "PutItem", error_message
    )

    error_match = (
        "already exists"
        if error_code == "ConditionalCheckFailedException"
        else error_message
    )
    with pytest.raises(expected_exception, match=error_match):
        client.add_receipt_chat_gpt_validation(
            sample_receipt_chatgpt_validation
        )


@pytest.mark.integration
def test_addReceiptChatGPTValidations_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_chatgpt_validation: ReceiptChatGPTValidation,
):
    """Test that multiple ReceiptChatGPTValidations can be successfully added to DynamoDB."""
    client = DynamoClient(dynamodb_table)

    second_validation = ReceiptChatGPTValidation(
        receipt_id=sample_receipt_chatgpt_validation.receipt_id,
        image_id=sample_receipt_chatgpt_validation.image_id,
        original_status=sample_receipt_chatgpt_validation.original_status,
        revised_status="INVALID",  # Changed status
        reasoning="Receipt is missing critical information.",
        corrections=sample_receipt_chatgpt_validation.corrections,
        prompt=sample_receipt_chatgpt_validation.prompt,
        response="The receipt is invalid.",
        timestamp=_timestamp(1),
        metadata=sample_receipt_chatgpt_validation.metadata,
    )

    validations = [sample_receipt_chatgpt_validation, second_validation]

    client.add_receipt_chatgpt_validations(validations)

    response1 = client._client.get_item(
        TableName=dynamodb_table,
        Key={
            "PK": {"S": f"IMAGE#{sample_receipt_chatgpt_validation.image_id}"},
            "SK": {
                "S": f"RECEIPT#{sample_receipt_chatgpt_validation.receipt_id}#ANALYSIS#VALIDATION#CHATGPT#{sample_receipt_chatgpt_validation.timestamp}"
            },
        },
    )

    assert "Item" in response1
    assert (
        response1["Item"]["revised_status"]["S"]
        == sample_receipt_chatgpt_validation.revised_status
    )

    response2 = client._client.get_item(
        TableName=dynamodb_table,
        Key={
            "PK": {"S": f"IMAGE#{second_validation.image_id}"},
            "SK": {
                "S": f"RECEIPT#{second_validation.receipt_id}#ANALYSIS#VALIDATION#CHATGPT#{second_validation.timestamp}"
            },
        },
    )

    assert "Item" in response2
    assert (
        response2["Item"]["revised_status"]["S"]
        == second_validation.revised_status
    )


@pytest.mark.integration
def test_addReceiptChatGPTValidations_with_large_batch(
    dynamodb_table, sample_receipt_chatgpt_validation
):
    """Test adding a large batch of validations (more than 25) to verify batch processing."""
    client = DynamoClient(dynamodb_table)

    validations = []
    for i in range(30):
        validation = ReceiptChatGPTValidation(
            receipt_id=sample_receipt_chatgpt_validation.receipt_id + i,
            image_id=sample_receipt_chatgpt_validation.image_id,
            original_status=sample_receipt_chatgpt_validation.original_status,
            revised_status=sample_receipt_chatgpt_validation.revised_status,
            reasoning=f"Reasoning {i}",
            corrections=sample_receipt_chatgpt_validation.corrections,
            prompt=sample_receipt_chatgpt_validation.prompt,
            response=sample_receipt_chatgpt_validation.response,
            timestamp=_timestamp(i),
            metadata=sample_receipt_chatgpt_validation.metadata,
        )
        validations.append(validation)

    client.add_receipt_chatgpt_validations(validations)

    for idx in [0, 15, 29]:  # Check first, middle, and last
        response = client._client.get_item(
            TableName=dynamodb_table,
            Key={
                "PK": {"S": f"IMAGE#{validations[idx].image_id}"},
                "SK": {
                    "S": f"RECEIPT#{validations[idx].receipt_id}#ANALYSIS#VALIDATION#CHATGPT#{validations[idx].timestamp}"
                },
            },
        )
        assert "Item" in response
        assert response["Item"]["reasoning"]["S"] == f"Reasoning {idx}"


@pytest.mark.integration
def test_addReceiptChatGPTValidations_with_unprocessed_items_retries(
    dynamodb_table, sample_receipt_chatgpt_validation, mocker
):
    """Test that the method retries processing unprocessed items."""
    client = DynamoClient(dynamodb_table)

    validations = [
        sample_receipt_chatgpt_validation,
        ReceiptChatGPTValidation(
            receipt_id=sample_receipt_chatgpt_validation.receipt_id + 1,
            image_id=sample_receipt_chatgpt_validation.image_id,
            original_status=sample_receipt_chatgpt_validation.original_status,
            revised_status=sample_receipt_chatgpt_validation.revised_status,
            reasoning="Second validation",
            corrections=sample_receipt_chatgpt_validation.corrections,
            prompt=sample_receipt_chatgpt_validation.prompt,
            response=sample_receipt_chatgpt_validation.response,
            timestamp=_timestamp(2),
            metadata=sample_receipt_chatgpt_validation.metadata,
        ),
    ]

    def batch_write_side_effect(*args, **kwargs):
        if batch_write_side_effect.call_count == 0:
            batch_write_side_effect.call_count += 1
            unprocessed_items = {
                dynamodb_table: [
                    {"PutRequest": {"Item": validations[1].to_item()}}
                ]
            }
            return {"UnprocessedItems": unprocessed_items}
        else:
            return {"UnprocessedItems": {}}

    batch_write_side_effect.call_count = 0

    mocker.patch.object(
        client._client, "batch_write_item", side_effect=batch_write_side_effect
    )

    client.add_receipt_chatgpt_validations(validations)

    assert client._client.batch_write_item.call_count == 2


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (None, "validations cannot be None"),
        (
            "not-a-list",
            "validations must be a list",
        ),
        (
            ["not-a-validation"],
            "All items in validations must be instances of ReceiptChatGPTValidation",
        ),
    ],
)
def test_addReceiptChatGPTValidations_invalid_parameters(
    dynamodb_table,
    sample_receipt_chatgpt_validation,
    mocker,
    invalid_input,
    expected_error,
):
    """Test adding validations with invalid parameters."""
    client = DynamoClient(dynamodb_table)

    mocker.patch.object(client._client, "batch_write_item")

    with pytest.raises(OperationError, match=expected_error):
        client.add_receipt_chatgpt_validations(invalid_input)

    client._client.batch_write_item.assert_not_called()


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_error_message",
    [
        (
            "ResourceNotFoundException",
            "Table not found",
            "Table not found",
        ),
        (
            "ProvisionedThroughputExceededException",
            "Throughput exceeded",
            "Throughput exceeded",
        ),
        (
            "InternalServerError",
            "Internal server error",
            "Internal server error",
        ),
        (
            "ValidationException",
            "One or more parameters were invalid",
            "One or more parameters given were invalid",
        ),
        (
            "AccessDeniedException",
            "Access denied",
            "Access denied",
        ),
        (
            "UnknownError",
            "Unknown error occurred",
            "Could not add receipt ChatGPT validations to DynamoDB",
        ),
    ],
)
def test_addReceiptChatGPTValidations_client_errors(
    dynamodb_table,
    sample_receipt_chatgpt_validation,
    mocker,
    error_code,
    error_message,
    expected_error_message,
):
    """Test handling of client errors when adding multiple ChatGPT validations."""
    client = DynamoClient(dynamodb_table)

    validations = [
        sample_receipt_chatgpt_validation,
        ReceiptChatGPTValidation(
            receipt_id=sample_receipt_chatgpt_validation.receipt_id + 1,
            image_id=sample_receipt_chatgpt_validation.image_id,
            original_status=sample_receipt_chatgpt_validation.original_status,
            revised_status=sample_receipt_chatgpt_validation.revised_status,
            reasoning="Second validation",
            corrections=sample_receipt_chatgpt_validation.corrections,
            prompt=sample_receipt_chatgpt_validation.prompt,
            response=sample_receipt_chatgpt_validation.response,
            timestamp=_timestamp(2),
            metadata=sample_receipt_chatgpt_validation.metadata,
        ),
    ]

    _patch_client_error(
        mocker,
        client,
        "batch_write_item",
        error_code,
        "BatchWriteItem",
        error_message,
    )

    with pytest.raises(
        _expected_client_exception(error_code), match=error_message
    ):
        client.add_receipt_chatgpt_validations(validations)


@pytest.mark.integration
def test_updateReceiptChatGPTValidation_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_chatgpt_validation: ReceiptChatGPTValidation,
):
    """Test that a ReceiptChatGPTValidation can be successfully updated in DynamoDB."""
    client = DynamoClient(dynamodb_table)

    client.add_receipt_chat_gpt_validation(sample_receipt_chatgpt_validation)

    updated_validation = ReceiptChatGPTValidation(
        receipt_id=sample_receipt_chatgpt_validation.receipt_id,
        image_id=sample_receipt_chatgpt_validation.image_id,
        original_status=sample_receipt_chatgpt_validation.original_status,
        revised_status="UPDATED_STATUS",  # Changed status
        reasoning="Updated reasoning",
        corrections=[
            {
                "field": "total",
                "original": "15.50",
                "corrected": "16.50",
                "reason": "Updated correction.",
            }
        ],
        prompt=sample_receipt_chatgpt_validation.prompt,
        response="Updated response",
        timestamp=sample_receipt_chatgpt_validation.timestamp,  # Same timestamp to update the same record
        metadata={"confidence": 0.99, "updated": True},
    )

    client.update_receipt_chatgpt_validation(updated_validation)

    response = client._client.get_item(
        TableName=dynamodb_table,
        Key={
            "PK": {"S": f"IMAGE#{updated_validation.image_id}"},
            "SK": {
                "S": f"RECEIPT#{updated_validation.receipt_id}#ANALYSIS#VALIDATION#CHATGPT#{updated_validation.timestamp}"
            },
        },
    )

    assert "Item" in response
    assert response["Item"]["revised_status"]["S"] == "UPDATED_STATUS"
    assert response["Item"]["reasoning"]["S"] == "Updated reasoning"
    assert response["Item"]["response"]["S"] == "Updated response"
    assert response["Item"]["metadata"]["M"]["updated"]["BOOL"] is True


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (None, "validation cannot be None"),
        (
            "not a ReceiptChatGPTValidation",
            "validation must be an instance of ReceiptChatGPTValidation",
        ),
    ],
)
def test_updateReceiptChatGPTValidation_invalid_parameters(
    dynamodb_table,
    sample_receipt_chatgpt_validation,
    mocker,
    invalid_input,
    expected_error,
):
    """Test updating a validation with invalid parameters."""
    client = DynamoClient(dynamodb_table)

    mocker.patch.object(client._client, "put_item")

    with pytest.raises(OperationError, match=expected_error):
        client.update_receipt_chatgpt_validation(invalid_input)

    client._client.put_item.assert_not_called()


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_error",
    [
        (
            "ConditionalCheckFailedException",
            "Item does not exist",
            "does not exist",
        ),
        (
            "ProvisionedThroughputExceededException",
            "Throughput exceeded",
            "Throughput exceeded",
        ),
        (
            "InternalServerError",
            "Internal server error",
            "Internal server error",
        ),
        (
            "ResourceNotFoundException",
            "Table not found",
            "Table not found",
        ),
        (
            "ValidationException",
            "One or more parameters were invalid",
            "One or more parameters given were invalid",
        ),
        (
            "AccessDeniedException",
            "Access denied",
            "Access denied",
        ),
        (
            "UnknownError",
            "Unknown error occurred",
            "Could not update receipt ChatGPT validation in DynamoDB",
        ),
    ],
)
def test_updateReceiptChatGPTValidation_client_errors(
    dynamodb_table,
    sample_receipt_chatgpt_validation,
    mocker,
    error_code,
    error_message,
    expected_error,
):
    """Test handling of client errors when updating a ChatGPT validation."""
    client = DynamoClient(dynamodb_table)

    _patch_client_error(
        mocker, client, "put_item", error_code, "PutItem", error_message
    )

    exception_type = _expected_client_exception(
        error_code, EntityNotFoundError
    )
    error_match = (
        "does not exist"
        if error_code == "ConditionalCheckFailedException"
        else error_message
    )
    with pytest.raises(exception_type, match=error_match):
        client.update_receipt_chatgpt_validation(
            sample_receipt_chatgpt_validation
        )


@pytest.mark.integration
def test_updateReceiptChatGPTValidations_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_chatgpt_validation: ReceiptChatGPTValidation,
):
    """Test that multiple ReceiptChatGPTValidations can be successfully updated in DynamoDB."""
    client = DynamoClient(dynamodb_table)

    second_validation = ReceiptChatGPTValidation(
        receipt_id=sample_receipt_chatgpt_validation.receipt_id + 1,
        image_id=sample_receipt_chatgpt_validation.image_id,
        original_status="PENDING",
        revised_status="INVALID",
        reasoning="Initial reasoning",
        corrections=sample_receipt_chatgpt_validation.corrections,
        prompt=sample_receipt_chatgpt_validation.prompt,
        response="Initial response",
        timestamp=_timestamp(2),
        metadata={"confidence": 0.8},
    )

    validations = [sample_receipt_chatgpt_validation, second_validation]
    client.add_receipt_chatgpt_validations(validations)

    updated_validation1 = ReceiptChatGPTValidation(
        receipt_id=sample_receipt_chatgpt_validation.receipt_id,
        image_id=sample_receipt_chatgpt_validation.image_id,
        original_status=sample_receipt_chatgpt_validation.original_status,
        revised_status="UPDATED_STATUS_1",
        reasoning="Updated reasoning 1",
        corrections=sample_receipt_chatgpt_validation.corrections,
        prompt=sample_receipt_chatgpt_validation.prompt,
        response="Updated response 1",
        timestamp=sample_receipt_chatgpt_validation.timestamp,  # Same timestamp to update the same record
        metadata={"confidence": 0.99, "updated": True},
    )

    updated_validation2 = ReceiptChatGPTValidation(
        receipt_id=second_validation.receipt_id,
        image_id=second_validation.image_id,
        original_status=second_validation.original_status,
        revised_status="UPDATED_STATUS_2",
        reasoning="Updated reasoning 2",
        corrections=second_validation.corrections,
        prompt=second_validation.prompt,
        response="Updated response 2",
        timestamp=second_validation.timestamp,  # Same timestamp to update the same record
        metadata={"confidence": 0.95, "updated": True},
    )

    updated_validations = [updated_validation1, updated_validation2]

    client.update_receipt_chatgpt_validations(updated_validations)

    response1 = client._client.get_item(
        TableName=dynamodb_table,
        Key={
            "PK": {"S": f"IMAGE#{updated_validation1.image_id}"},
            "SK": {
                "S": f"RECEIPT#{updated_validation1.receipt_id}#ANALYSIS#VALIDATION#CHATGPT#{updated_validation1.timestamp}"
            },
        },
    )

    assert "Item" in response1
    assert response1["Item"]["revised_status"]["S"] == "UPDATED_STATUS_1"
    assert response1["Item"]["reasoning"]["S"] == "Updated reasoning 1"

    response2 = client._client.get_item(
        TableName=dynamodb_table,
        Key={
            "PK": {"S": f"IMAGE#{updated_validation2.image_id}"},
            "SK": {
                "S": f"RECEIPT#{updated_validation2.receipt_id}#ANALYSIS#VALIDATION#CHATGPT#{updated_validation2.timestamp}"
            },
        },
    )

    assert "Item" in response2
    assert response2["Item"]["revised_status"]["S"] == "UPDATED_STATUS_2"
    assert response2["Item"]["reasoning"]["S"] == "Updated reasoning 2"


@pytest.mark.integration
def test_updateReceiptChatGPTValidations_with_large_batch(
    dynamodb_table, sample_receipt_chatgpt_validation
):
    """Test updating a large batch of validations (more than 25) to verify batch processing."""
    client = DynamoClient(dynamodb_table)

    validations = []
    for i in range(30):
        validation = ReceiptChatGPTValidation(
            receipt_id=sample_receipt_chatgpt_validation.receipt_id + i,
            image_id=sample_receipt_chatgpt_validation.image_id,
            original_status="PENDING",
            revised_status="INITIAL",
            reasoning=f"Initial reasoning {i}",
            corrections=sample_receipt_chatgpt_validation.corrections,
            prompt=sample_receipt_chatgpt_validation.prompt,
            response="Initial response",
            timestamp=_timestamp(i),
            metadata={"confidence": 0.8},
        )
        validations.append(validation)

    client.add_receipt_chatgpt_validations(validations)

    updated_validations = []
    for i, orig_validation in enumerate(validations):
        updated_validation = ReceiptChatGPTValidation(
            receipt_id=orig_validation.receipt_id,
            image_id=orig_validation.image_id,
            original_status=orig_validation.original_status,
            revised_status="UPDATED",
            reasoning=f"Updated reasoning {i}",
            corrections=orig_validation.corrections,
            prompt=orig_validation.prompt,
            response=f"Updated response {i}",
            timestamp=orig_validation.timestamp,  # Same timestamp to update the same record
            metadata={"confidence": 0.9, "updated": True},
        )
        updated_validations.append(updated_validation)

    client.update_receipt_chatgpt_validations(updated_validations)

    for idx in [0, 15, 29]:  # Check first, middle, and last
        response = client._client.get_item(
            TableName=dynamodb_table,
            Key={
                "PK": {"S": f"IMAGE#{updated_validations[idx].image_id}"},
                "SK": {
                    "S": f"RECEIPT#{updated_validations[idx].receipt_id}#ANALYSIS#VALIDATION#CHATGPT#{updated_validations[idx].timestamp}"
                },
            },
        )
        assert "Item" in response
        assert response["Item"]["revised_status"]["S"] == "UPDATED"
        assert response["Item"]["reasoning"]["S"] == f"Updated reasoning {idx}"
        assert response["Item"]["response"]["S"] == f"Updated response {idx}"


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (None, "validations cannot be None"),
        (
            "not-a-list",
            "validations must be a list",
        ),
        (
            [123, "not-a-validation"],
            "All items in validations must be instances of ReceiptChatGPTValidation",
        ),
    ],
)
def test_updateReceiptChatGPTValidations_invalid_inputs(
    dynamodb_table,
    sample_receipt_chatgpt_validation,
    mocker,
    invalid_input,
    expected_error,
):
    """Test updating validations with invalid inputs."""
    client = DynamoClient(dynamodb_table)

    mocker.patch.object(client._client, "transact_write_items")

    with pytest.raises(OperationError, match=expected_error):
        client.update_receipt_chatgpt_validations(invalid_input)

    client._client.transact_write_items.assert_not_called()


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_exception,cancellation_reasons",
    [
        (
            "ResourceNotFoundException",
            "Table not found",
            OperationError,
            None,
        ),
        (
            "TransactionCanceledException",
            "Transaction canceled due to ConditionalCheckFailed",
            DynamoDBError,
            [{"Code": "ConditionalCheckFailed"}],
        ),
        (
            "InternalServerError",
            "Internal server error",
            DynamoDBServerError,
            None,
        ),
        (
            "ProvisionedThroughputExceededException",
            "Throughput exceeded",
            DynamoDBThroughputError,
            None,
        ),
        (
            "ValidationException",
            "One or more parameters were invalid",
            EntityValidationError,
            None,
        ),
        (
            "AccessDeniedException",
            "Access denied",
            DynamoDBError,
            None,
        ),
        (
            "UnknownError",
            "Unknown error occurred",
            DynamoDBError,
            None,
        ),
    ],
)
def test_updateReceiptChatGPTValidations_client_errors(
    dynamodb_table,
    sample_receipt_chatgpt_validation,
    mocker,
    error_code,
    error_message,
    expected_exception,
    cancellation_reasons,
):
    """Test handling of client errors when updating multiple ChatGPT validations."""
    client = DynamoClient(dynamodb_table)

    validations = [
        sample_receipt_chatgpt_validation,
        ReceiptChatGPTValidation(
            receipt_id=sample_receipt_chatgpt_validation.receipt_id + 1,
            image_id=sample_receipt_chatgpt_validation.image_id,
            original_status="PENDING",
            revised_status="VALID",
            reasoning="Another reasoning",
            corrections=sample_receipt_chatgpt_validation.corrections,
            prompt=sample_receipt_chatgpt_validation.prompt,
            response="Another response",
            timestamp=_timestamp(2),
            metadata={"confidence": 0.85},
        ),
    ]

    error_response = {
        "Error": {
            "Code": error_code,
            "Message": error_message,
        }
    }

    if cancellation_reasons:
        error_response["CancellationReasons"] = cancellation_reasons

    mocker.patch.object(
        client._client,
        "transact_write_items",
        side_effect=ClientError(error_response, "TransactWriteItems"),
    )

    with pytest.raises(expected_exception, match=error_message):
        client.update_receipt_chatgpt_validations(validations)


@pytest.mark.integration
def test_deleteReceiptChatGPTValidation_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_chatgpt_validation: ReceiptChatGPTValidation,
):
    """Test that a ReceiptChatGPTValidation can be successfully deleted from DynamoDB."""
    client = DynamoClient(dynamodb_table)

    client.add_receipt_chat_gpt_validation(sample_receipt_chatgpt_validation)

    response_before = client._client.get_item(
        TableName=dynamodb_table,
        Key={
            "PK": {"S": f"IMAGE#{sample_receipt_chatgpt_validation.image_id}"},
            "SK": {
                "S": f"RECEIPT#{sample_receipt_chatgpt_validation.receipt_id}#ANALYSIS#VALIDATION#CHATGPT#{sample_receipt_chatgpt_validation.timestamp}"
            },
        },
    )
    assert "Item" in response_before

    client.delete_receipt_chat_gpt_validation(
        sample_receipt_chatgpt_validation
    )

    response_after = client._client.get_item(
        TableName=dynamodb_table,
        Key={
            "PK": {"S": f"IMAGE#{sample_receipt_chatgpt_validation.image_id}"},
            "SK": {
                "S": f"RECEIPT#{sample_receipt_chatgpt_validation.receipt_id}#ANALYSIS#VALIDATION#CHATGPT#{sample_receipt_chatgpt_validation.timestamp}"
            },
        },
    )
    assert "Item" not in response_after


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (None, "validation cannot be None"),
        (
            "not-a-validation-result",
            "validation must be an instance of ReceiptChatGPTValidation",
        ),
    ],
)
def test_deleteReceiptChatGPTValidation_invalid_parameters(
    dynamodb_table,
    sample_receipt_chatgpt_validation,
    mocker,
    invalid_input,
    expected_error,
):
    """Test deleting a validation with invalid parameters."""
    client = DynamoClient(dynamodb_table)

    mocker.patch.object(client._client, "delete_item")

    with pytest.raises(OperationError, match=expected_error):
        client.delete_receipt_chat_gpt_validation(invalid_input)

    client._client.delete_item.assert_not_called()


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_error",
    [
        (
            "ConditionalCheckFailedException",
            "Item does not exist",
            "does not exist",
        ),
        (
            "ResourceNotFoundException",
            "Table not found",
            "Table not found",
        ),
        (
            "ProvisionedThroughputExceededException",
            "Throughput exceeded",
            "Throughput exceeded",
        ),
        (
            "InternalServerError",
            "Internal server error",
            "Internal server error",
        ),
        (
            "ValidationException",
            "One or more parameters were invalid",
            "One or more parameters given were invalid",
        ),
        ("AccessDeniedException", "Access denied", "Access denied"),
        (
            "UnknownError",
            "Unknown error occurred",
            "Could not delete receipt ChatGPT validation from DynamoDB",
        ),
    ],
)
def test_deleteReceiptChatGPTValidation_client_errors(
    dynamodb_table,
    sample_receipt_chatgpt_validation,
    mocker,
    error_code,
    error_message,
    expected_error,
):
    """Test handling of client errors when deleting a ChatGPT validation."""
    client = DynamoClient(dynamodb_table)

    _patch_client_error(
        mocker, client, "delete_item", error_code, "DeleteItem", error_message
    )

    exception_type = _expected_client_exception(
        error_code, EntityNotFoundError
    )
    error_match = (
        "does not exist"
        if error_code == "ConditionalCheckFailedException"
        else error_message
    )
    with pytest.raises(exception_type, match=error_match):
        client.delete_receipt_chat_gpt_validation(
            sample_receipt_chatgpt_validation
        )


@pytest.mark.integration
def test_deleteReceiptChatGPTValidations_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_chatgpt_validation: ReceiptChatGPTValidation,
):
    """Test that multiple ReceiptChatGPTValidations can be successfully deleted from DynamoDB."""
    client = DynamoClient(dynamodb_table)

    second_validation = ReceiptChatGPTValidation(
        receipt_id=sample_receipt_chatgpt_validation.receipt_id + 1,
        image_id=sample_receipt_chatgpt_validation.image_id,
        original_status="PENDING",
        revised_status="INVALID",
        reasoning="Initial reasoning",
        corrections=sample_receipt_chatgpt_validation.corrections,
        prompt=sample_receipt_chatgpt_validation.prompt,
        response="Initial response",
        timestamp=_timestamp(2),
        metadata={"confidence": 0.8},
    )

    validations = [sample_receipt_chatgpt_validation, second_validation]
    client.add_receipt_chatgpt_validations(validations)

    response1_before = client._client.get_item(
        TableName=dynamodb_table,
        Key={
            "PK": {"S": f"IMAGE#{sample_receipt_chatgpt_validation.image_id}"},
            "SK": {
                "S": f"RECEIPT#{sample_receipt_chatgpt_validation.receipt_id}#ANALYSIS#VALIDATION#CHATGPT#{sample_receipt_chatgpt_validation.timestamp}"
            },
        },
    )
    response2_before = client._client.get_item(
        TableName=dynamodb_table,
        Key={
            "PK": {"S": f"IMAGE#{second_validation.image_id}"},
            "SK": {
                "S": f"RECEIPT#{second_validation.receipt_id}#ANALYSIS#VALIDATION#CHATGPT#{second_validation.timestamp}"
            },
        },
    )
    assert "Item" in response1_before
    assert "Item" in response2_before

    client.delete_receipt_chat_gpt_validations(validations)

    response1_after = client._client.get_item(
        TableName=dynamodb_table,
        Key={
            "PK": {"S": f"IMAGE#{sample_receipt_chatgpt_validation.image_id}"},
            "SK": {
                "S": f"RECEIPT#{sample_receipt_chatgpt_validation.receipt_id}#ANALYSIS#VALIDATION#CHATGPT#{sample_receipt_chatgpt_validation.timestamp}"
            },
        },
    )
    response2_after = client._client.get_item(
        TableName=dynamodb_table,
        Key={
            "PK": {"S": f"IMAGE#{second_validation.image_id}"},
            "SK": {
                "S": f"RECEIPT#{second_validation.receipt_id}#ANALYSIS#VALIDATION#CHATGPT#{second_validation.timestamp}"
            },
        },
    )
    assert "Item" not in response1_after
    assert "Item" not in response2_after


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (None, "validations cannot be None"),
        (
            "not-a-list",
            "validations must be a list",
        ),
        (
            [123, "not-a-validation"],
            "All items in validations must be instances of ReceiptChatGPTValidation",
        ),
    ],
)
def test_deleteReceiptChatGPTValidations_invalid_parameters(
    dynamodb_table,
    sample_receipt_chatgpt_validation,
    mocker,
    invalid_input,
    expected_error,
):
    """Test deleting validations with invalid parameters."""
    client = DynamoClient(dynamodb_table)

    mocker.patch.object(client._client, "batch_write_item")

    with pytest.raises(OperationError, match=expected_error):
        client.delete_receipt_chat_gpt_validations(invalid_input)

    client._client.batch_write_item.assert_not_called()


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_error",
    [
        (
            "ResourceNotFoundException",
            "Table not found",
            "Table not found",
        ),
        (
            "ProvisionedThroughputExceededException",
            "Throughput exceeded",
            "Throughput exceeded",
        ),
        (
            "InternalServerError",
            "Internal server error",
            "Internal server error",
        ),
        (
            "ValidationException",
            "One or more parameters were invalid",
            "One or more parameters given were invalid",
        ),
        (
            "AccessDeniedException",
            "Access denied",
            "Access denied",
        ),
        (
            "UnknownError",
            "Unknown error occurred",
            "Could not delete receipt ChatGPT validations from DynamoDB",
        ),
    ],
)
def test_deleteReceiptChatGPTValidations_client_errors(
    dynamodb_table,
    sample_receipt_chatgpt_validation,
    mocker,
    error_code,
    error_message,
    expected_error,
):
    """Test handling of client errors when deleting multiple ChatGPT validations."""
    client = DynamoClient(dynamodb_table)

    validations = [
        sample_receipt_chatgpt_validation,
        ReceiptChatGPTValidation(
            receipt_id=sample_receipt_chatgpt_validation.receipt_id + 1,
            image_id=sample_receipt_chatgpt_validation.image_id,
            original_status="PENDING",
            revised_status="VALID",
            reasoning="Another reasoning",
            corrections=sample_receipt_chatgpt_validation.corrections,
            prompt=sample_receipt_chatgpt_validation.prompt,
            response="Another response",
            timestamp=_timestamp(2),
            metadata={"confidence": 0.85},
        ),
    ]

    _patch_client_error(
        mocker,
        client,
        "batch_write_item",
        error_code,
        "BatchWriteItem",
        error_message,
    )

    with pytest.raises(
        _expected_client_exception(error_code), match=error_message
    ):
        client.delete_receipt_chat_gpt_validations(validations)


@pytest.mark.integration
def test_deleteReceiptChatGPTValidations_with_unprocessed_items_retries(
    dynamodb_table, sample_receipt_chatgpt_validation, mocker
):
    """Test that the method retries processing unprocessed items when deleting validations."""
    client = DynamoClient(dynamodb_table)

    validations = [
        sample_receipt_chatgpt_validation,
        ReceiptChatGPTValidation(
            receipt_id=sample_receipt_chatgpt_validation.receipt_id + 1,
            image_id=sample_receipt_chatgpt_validation.image_id,
            original_status=sample_receipt_chatgpt_validation.original_status,
            revised_status=sample_receipt_chatgpt_validation.revised_status,
            reasoning="Second validation",
            corrections=sample_receipt_chatgpt_validation.corrections,
            prompt=sample_receipt_chatgpt_validation.prompt,
            response=sample_receipt_chatgpt_validation.response,
            timestamp=_timestamp(2),
            metadata=sample_receipt_chatgpt_validation.metadata,
        ),
    ]

    def batch_write_side_effect(*args, **kwargs):
        if batch_write_side_effect.call_count == 0:
            batch_write_side_effect.call_count += 1
            unprocessed_items = {
                dynamodb_table: [
                    {"DeleteRequest": {"Key": validations[1].key}}
                ]
            }
            return {"UnprocessedItems": unprocessed_items}
        else:
            return {"UnprocessedItems": {}}

    batch_write_side_effect.call_count = 0

    mocker.patch.object(
        client._client, "batch_write_item", side_effect=batch_write_side_effect
    )

    client.delete_receipt_chat_gpt_validations(validations)

    assert client._client.batch_write_item.call_count == 2


@pytest.mark.integration
def test_deleteReceiptChatGPTValidations_with_large_batch(
    dynamodb_table, sample_receipt_chatgpt_validation
):
    """Test deleting a large batch of validations (more than 25) to verify batch processing."""
    client = DynamoClient(dynamodb_table)

    validations = []
    for i in range(30):
        validation = ReceiptChatGPTValidation(
            receipt_id=sample_receipt_chatgpt_validation.receipt_id + i,
            image_id=sample_receipt_chatgpt_validation.image_id,
            original_status="PENDING",
            revised_status="INITIAL",
            reasoning=f"Initial reasoning {i}",
            corrections=sample_receipt_chatgpt_validation.corrections,
            prompt=sample_receipt_chatgpt_validation.prompt,
            response="Initial response",
            timestamp=_timestamp(i),
            metadata={"confidence": 0.8},
        )
        validations.append(validation)

    client.add_receipt_chatgpt_validations(validations)

    for idx in [0, 15, 29]:  # Check first, middle, and last
        response_before = client._client.get_item(
            TableName=dynamodb_table,
            Key={
                "PK": {"S": f"IMAGE#{validations[idx].image_id}"},
                "SK": {
                    "S": f"RECEIPT#{validations[idx].receipt_id}#ANALYSIS#VALIDATION#CHATGPT#{validations[idx].timestamp}"
                },
            },
        )
        assert "Item" in response_before

    client.delete_receipt_chat_gpt_validations(validations)

    for idx in [0, 15, 29]:  # Check first, middle, and last
        response_after = client._client.get_item(
            TableName=dynamodb_table,
            Key={
                "PK": {"S": f"IMAGE#{validations[idx].image_id}"},
                "SK": {
                    "S": f"RECEIPT#{validations[idx].receipt_id}#ANALYSIS#VALIDATION#CHATGPT#{validations[idx].timestamp}"
                },
            },
        )
        assert "Item" not in response_after


@pytest.mark.integration
def test_getReceiptChatGPTValidation_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_chatgpt_validation: ReceiptChatGPTValidation,
):
    """Test that a ReceiptChatGPTValidation can be successfully retrieved from DynamoDB."""
    client = DynamoClient(dynamodb_table)

    client.add_receipt_chat_gpt_validation(sample_receipt_chatgpt_validation)

    result = client.get_receipt_chat_gpt_validation(
        receipt_id=sample_receipt_chatgpt_validation.receipt_id,
        image_id=sample_receipt_chatgpt_validation.image_id,
        timestamp=sample_receipt_chatgpt_validation.timestamp,
    )

    assert result is not None
    assert isinstance(result, ReceiptChatGPTValidation)
    assert result.receipt_id == sample_receipt_chatgpt_validation.receipt_id
    assert result.image_id == sample_receipt_chatgpt_validation.image_id
    assert result.timestamp == sample_receipt_chatgpt_validation.timestamp
    assert (
        result.original_status
        == sample_receipt_chatgpt_validation.original_status
    )
    assert (
        result.revised_status
        == sample_receipt_chatgpt_validation.revised_status
    )
    assert result.reasoning == sample_receipt_chatgpt_validation.reasoning


@pytest.mark.integration
def test_getReceiptChatGPTValidation_not_found(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_chatgpt_validation: ReceiptChatGPTValidation,
):
    """Test retrieving a non-existent ReceiptChatGPTValidation."""
    client = DynamoClient(dynamodb_table)

    non_existent_timestamp = "2099-01-01T00:00:00.000000"

    with pytest.raises(
        EntityNotFoundError,
        match=f"ReceiptChatGPTValidation with receipt ID {sample_receipt_chatgpt_validation.receipt_id}, image ID {sample_receipt_chatgpt_validation.image_id}, and timestamp {non_existent_timestamp} not found",
    ):
        client.get_receipt_chat_gpt_validation(
            receipt_id=sample_receipt_chatgpt_validation.receipt_id,
            image_id=sample_receipt_chatgpt_validation.image_id,
            timestamp=non_existent_timestamp,
        )


@pytest.mark.integration
@pytest.mark.parametrize(
    "receipt_id,image_id,timestamp,expected_error",
    [
        (
            None,
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "2023-01-01T12:00:00",
            "receipt_id cannot be None",
        ),
        (
            1,
            None,
            "2023-01-01T12:00:00",
            "image_id cannot be None",
        ),
        (
            1,
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            None,
            "timestamp cannot be None",
        ),
        (
            1,
            "invalid-uuid",
            "2023-01-01T12:00:00",
            "uuid must be a valid UUIDv4",
        ),
        (
            1,
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            12345,
            "timestamp must be a string.",
        ),
    ],
)
def test_getReceiptChatGPTValidation_invalid_parameters(
    dynamodb_table: Literal["MyMockedTable"],
    receipt_id,
    image_id,
    timestamp,
    expected_error,
):
    """Test retrieving a validation with invalid parameters."""
    client = DynamoClient(dynamodb_table)

    exception_type = (
        OperationError if image_id == "invalid-uuid" else EntityValidationError
    )
    with pytest.raises(exception_type, match=expected_error):
        client.get_receipt_chat_gpt_validation(
            receipt_id=receipt_id,
            image_id=image_id,
            timestamp=timestamp,
        )


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_error",
    [
        (
            "ResourceNotFoundException",
            "Table not found",
            "Error getting receipt ChatGPT validation",
        ),
        (
            "ProvisionedThroughputExceededException",
            "Throughput exceeded",
            "Throughput exceeded",
        ),
        (
            "InternalServerError",
            "Internal server error",
            "Internal server error",
        ),
        (
            "ValidationException",
            "One or more parameters were invalid",
            "Validation error",
        ),
        (
            "AccessDeniedException",
            "Access denied",
            "Access denied",
        ),
        (
            "UnknownError",
            "Unknown error occurred",
            "Error getting receipt ChatGPT validation",
        ),
    ],
)
def test_getReceiptChatGPTValidation_client_errors(
    dynamodb_table,
    sample_receipt_chatgpt_validation,
    mocker,
    error_code,
    error_message,
    expected_error,
):
    """Test handling of client errors when retrieving a ChatGPT validation."""
    client = DynamoClient(dynamodb_table)

    _patch_client_error(
        mocker, client, "get_item", error_code, "GetItem", error_message
    )

    with pytest.raises(
        _expected_client_exception(error_code), match=error_message
    ):
        client.get_receipt_chat_gpt_validation(
            receipt_id=sample_receipt_chatgpt_validation.receipt_id,
            image_id=sample_receipt_chatgpt_validation.image_id,
            timestamp=sample_receipt_chatgpt_validation.timestamp,
        )


@pytest.mark.integration
def test_listReceiptChatGPTValidations_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_chatgpt_validation: ReceiptChatGPTValidation,
    mocker,
):
    """Test that all ReceiptChatGPTValidations can be successfully listed from DynamoDB."""
    client = DynamoClient(dynamodb_table)

    validation1 = sample_receipt_chatgpt_validation
    validation2 = ReceiptChatGPTValidation(
        receipt_id=validation1.receipt_id + 1,
        image_id=validation1.image_id,
        original_status="PENDING",
        revised_status="INVALID",
        reasoning="Validation 2 reasoning",
        corrections=validation1.corrections,
        prompt=validation1.prompt,
        response="Validation 2 response",
        timestamp=_timestamp(2),
        metadata={"confidence": 0.7},
    )
    validation3 = ReceiptChatGPTValidation(
        receipt_id=validation1.receipt_id + 2,
        image_id=validation1.image_id,
        original_status="PENDING",
        revised_status="NEEDS_REVIEW",
        reasoning="Validation 3 reasoning",
        corrections=validation1.corrections,
        prompt=validation1.prompt,
        response="Validation 3 response",
        timestamp=_timestamp(3),
        metadata={"confidence": 0.6},
    )

    validations = [validation1, validation2, validation3]
    client.add_receipt_chatgpt_validations(validations)

    mock_query = mocker.patch.object(client._client, "query")

    mock_response = {
        "Items": [validation.to_item() for validation in validations],
        "Count": len(validations),
        "ScannedCount": len(validations),
    }
    mock_query.return_value = mock_response

    result_validations, last_evaluated_key = (
        client.list_receipt_chat_gpt_validations()
    )

    assert result_validations is not None
    assert len(result_validations) == 3
    assert last_evaluated_key is None

    assert any(
        v.receipt_id == validation1.receipt_id
        and v.timestamp == validation1.timestamp
        for v in result_validations
    )
    assert any(
        v.receipt_id == validation2.receipt_id
        and v.timestamp == validation2.timestamp
        for v in result_validations
    )
    assert any(
        v.receipt_id == validation3.receipt_id
        and v.timestamp == validation3.timestamp
        for v in result_validations
    )

    mock_query.assert_called_once()
    args, kwargs = mock_query.call_args
    assert kwargs["TableName"] == dynamodb_table
    assert kwargs["IndexName"] == "GSI1"
    assert (
        kwargs["ExpressionAttributeValues"][":pk_val"]["S"] == "ANALYSIS_TYPE"
    )
    assert (
        kwargs["ExpressionAttributeValues"][":sk_prefix"]["S"]
        == "VALIDATION_CHATGPT#"
    )


@pytest.mark.integration
def test_listReceiptChatGPTValidations_with_pagination(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_chatgpt_validation: ReceiptChatGPTValidation,
    mocker,
):
    """Test that ReceiptChatGPTValidations can be listed with pagination."""
    client = DynamoClient(dynamodb_table)

    validation1 = sample_receipt_chatgpt_validation
    validation2 = ReceiptChatGPTValidation(
        receipt_id=validation1.receipt_id + 1,
        image_id=validation1.image_id,
        original_status="PENDING",
        revised_status="INVALID",
        reasoning="Validation 2 reasoning",
        corrections=validation1.corrections,
        prompt=validation1.prompt,
        response="Validation 2 response",
        timestamp=_timestamp(2),
        metadata={"confidence": 0.7},
    )
    validation3 = ReceiptChatGPTValidation(
        receipt_id=validation1.receipt_id + 2,
        image_id=validation1.image_id,
        original_status="PENDING",
        revised_status="NEEDS_REVIEW",
        reasoning="Validation 3 reasoning",
        corrections=validation1.corrections,
        prompt=validation1.prompt,
        response="Validation 3 response",
        timestamp=_timestamp(3),
        metadata={"confidence": 0.6},
    )

    validations = [validation1, validation2, validation3]
    client.add_receipt_chatgpt_validations(validations)

    mock_query = mocker.patch.object(client._client, "query")

    first_call_response = {
        "Items": [validation1.to_item()],
        "Count": 1,
        "ScannedCount": 1,
        "LastEvaluatedKey": {
            "PK": {"S": "dummy-key-1"},
            "SK": {"S": "dummy-sk-1"},
        },
    }

    second_call_response = {
        "Items": [validation2.to_item()],
        "Count": 1,
        "ScannedCount": 1,
        "LastEvaluatedKey": {
            "PK": {"S": "dummy-key-2"},
            "SK": {"S": "dummy-sk-2"},
        },
    }

    third_call_response = {
        "Items": [validation3.to_item()],
        "Count": 1,
        "ScannedCount": 1,
    }

    mock_query.side_effect = [
        first_call_response,
        second_call_response,
        third_call_response,
    ]

    result_validations, last_evaluated_key = (
        client.list_receipt_chat_gpt_validations()
    )

    assert result_validations is not None
    assert len(result_validations) == 3
    assert last_evaluated_key is None

    assert mock_query.call_count == 3

    mock_query.reset_mock()
    mock_query.side_effect = [first_call_response]

    result_validations, last_evaluated_key = (
        client.list_receipt_chat_gpt_validations(limit=1)
    )

    assert result_validations is not None
    assert len(result_validations) == 1
    assert last_evaluated_key == {
        **validation1.key,
        **validation1.gsi1_key,
    }

    mock_query.assert_called_once()


@pytest.mark.integration
def test_listReceiptChatGPTValidations_empty_results(
    dynamodb_table: Literal["MyMockedTable"],
    mocker,
):
    """Test listing ChatGPT validations when none exist."""
    client = DynamoClient(dynamodb_table)

    mock_query = mocker.patch.object(client._client, "query")

    mock_query.return_value = {
        "Items": [],
        "Count": 0,
        "ScannedCount": 0,
    }

    result_validations, last_evaluated_key = (
        client.list_receipt_chat_gpt_validations()
    )

    assert result_validations is not None
    assert len(result_validations) == 0
    assert last_evaluated_key is None

    mock_query.assert_called_once()


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_error",
    [
        (
            "ResourceNotFoundException",
            "Table not found",
            "Could not list receipt ChatGPT validations from DynamoDB",
        ),
        (
            "ProvisionedThroughputExceededException",
            "Throughput exceeded",
            "Throughput exceeded",
        ),
        (
            "InternalServerError",
            "Internal server error",
            "Internal server error",
        ),
        (
            "ValidationException",
            "One or more parameters were invalid",
            "One or more parameters given were invalid",
        ),
        (
            "AccessDeniedException",
            "Access denied",
            "Access denied",
        ),
        (
            "UnknownError",
            "Unknown error occurred",
            "Error listing receipt ChatGPT validations",
        ),
    ],
)
def test_listReceiptChatGPTValidations_client_errors(
    dynamodb_table,
    mocker,
    error_code,
    error_message,
    expected_error,
):
    """Test handling of client errors when listing ChatGPT validations."""
    client = DynamoClient(dynamodb_table)

    _patch_client_error(
        mocker, client, "query", error_code, "Query", error_message
    )

    with pytest.raises(
        _expected_client_exception(error_code), match=error_message
    ):
        client.list_receipt_chat_gpt_validations()


@pytest.mark.integration
def test_listReceiptChatGPTValidationsForReceipt_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_chatgpt_validation: ReceiptChatGPTValidation,
    mocker,
):
    """Test that all ReceiptChatGPTValidations for a specific receipt can be listed."""
    client = DynamoClient(dynamodb_table)

    validation1 = sample_receipt_chatgpt_validation
    validation2 = ReceiptChatGPTValidation(
        receipt_id=validation1.receipt_id,  # Same receipt ID
        image_id=validation1.image_id,  # Same image ID
        original_status="PENDING",
        revised_status="INVALID",
        reasoning="Validation 2 reasoning",
        corrections=validation1.corrections,
        prompt=validation1.prompt,
        response="Validation 2 response",
        timestamp=_timestamp(2),
        metadata={"confidence": 0.7},
    )

    validation3 = ReceiptChatGPTValidation(
        receipt_id=validation1.receipt_id + 1,  # Different receipt ID
        image_id=validation1.image_id,
        original_status="PENDING",
        revised_status="NEEDS_REVIEW",
        reasoning="Validation 3 reasoning",
        corrections=validation1.corrections,
        prompt=validation1.prompt,
        response="Validation 3 response",
        timestamp=_timestamp(3),
        metadata={"confidence": 0.6},
    )

    validations = [validation1, validation2, validation3]
    client.add_receipt_chatgpt_validations(validations)

    mock_query = mocker.patch.object(client._client, "query")

    mock_response = {
        "Items": [validation1.to_item(), validation2.to_item()],
        "Count": 2,
        "ScannedCount": 2,
    }
    mock_query.return_value = mock_response

    result_validations = client.list_receipt_chat_gpt_validations_for_receipt(
        receipt_id=validation1.receipt_id,
        image_id=validation1.image_id,
    )

    assert result_validations is not None
    assert len(result_validations) == 2

    assert any(
        v.receipt_id == validation1.receipt_id
        and v.timestamp == validation1.timestamp
        for v in result_validations
    )
    assert any(
        v.receipt_id == validation2.receipt_id
        and v.timestamp == validation2.timestamp
        for v in result_validations
    )

    mock_query.assert_called_once()
    args, kwargs = mock_query.call_args
    assert kwargs["TableName"] == dynamodb_table
    assert (
        kwargs["ExpressionAttributeValues"][":pkVal"]["S"]
        == f"IMAGE#{validation1.image_id}"
    )
    assert (
        kwargs["ExpressionAttributeValues"][":skPrefix"]["S"]
        == f"RECEIPT#{validation1.receipt_id}#ANALYSIS#VALIDATION#CHATGPT#"
    )


@pytest.mark.integration
@pytest.mark.parametrize(
    "receipt_id,image_id,expected_error",
    [
        (
            None,
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "receipt_id cannot be None",
        ),
        (
            "not_an_int",
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "receipt_id must be a positive integer",
        ),
        (1, None, "image_id cannot be None"),
        (1, "invalid-uuid", "uuid must be a valid UUIDv4"),
    ],
)
def test_listReceiptChatGPTValidationsForReceipt_invalid_parameters(
    dynamodb_table,
    receipt_id,
    image_id,
    expected_error,
):
    """Test listing validations for a receipt with invalid parameters."""
    client = DynamoClient(dynamodb_table)

    exception_type = (
        OperationError if image_id == "invalid-uuid" else EntityValidationError
    )
    with pytest.raises(exception_type, match=expected_error):
        client.list_receipt_chat_gpt_validations_for_receipt(
            receipt_id=receipt_id,
            image_id=image_id,
        )


@pytest.mark.integration
def test_listReceiptChatGPTValidationsByStatus_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_chatgpt_validation: ReceiptChatGPTValidation,
    mocker,
):
    """Test that ReceiptChatGPTValidations can be filtered by status."""
    client = DynamoClient(dynamodb_table)

    validation1 = sample_receipt_chatgpt_validation
    validation1.revised_status = "VALID"  # Ensure we know the status

    validation2 = ReceiptChatGPTValidation(
        receipt_id=validation1.receipt_id + 1,
        image_id=validation1.image_id,
        original_status="PENDING",
        revised_status="VALID",  # Same status as validation1
        reasoning="Validation 2 reasoning",
        corrections=validation1.corrections,
        prompt=validation1.prompt,
        response="Validation 2 response",
        timestamp=_timestamp(2),
        metadata={"confidence": 0.7},
    )

    validation3 = ReceiptChatGPTValidation(
        receipt_id=validation1.receipt_id + 2,
        image_id=validation1.image_id,
        original_status="PENDING",
        revised_status="INVALID",  # Different status
        reasoning="Validation 3 reasoning",
        corrections=validation1.corrections,
        prompt=validation1.prompt,
        response="Validation 3 response",
        timestamp=_timestamp(3),
        metadata={"confidence": 0.6},
    )

    validations = [validation1, validation2, validation3]
    client.add_receipt_chatgpt_validations(validations)

    mock_query = mocker.patch.object(client._client, "query")

    mock_response = {
        "Items": [validation1.to_item(), validation2.to_item()],
        "Count": 2,
        "ScannedCount": 2,
    }
    mock_query.return_value = mock_response

    result_validations, last_evaluated_key = (
        client.list_receipt_chat_gpt_validations_by_status(status="VALID")
    )

    assert result_validations is not None
    assert len(result_validations) == 2
    assert last_evaluated_key is None

    for validation in result_validations:
        assert validation.revised_status == "VALID"

    mock_query.assert_called_once()
    args, kwargs = mock_query.call_args
    assert kwargs["TableName"] == dynamodb_table
    assert kwargs["IndexName"] == "GSI3"
    assert (
        kwargs["ExpressionAttributeValues"][":pk_val"]["S"]
        == "VALIDATION_STATUS#VALID"
    )


@pytest.mark.integration
@pytest.mark.parametrize(
    "status,expected_error",
    [
        (None, "status cannot be None"),
        ("", "status must not be empty"),
    ],
)
def test_listReceiptChatGPTValidationsByStatus_invalid_parameters(
    dynamodb_table,
    status,
    expected_error,
):
    """Test listing validations by status with invalid parameters."""
    client = DynamoClient(dynamodb_table)

    with pytest.raises(EntityValidationError, match=expected_error):
        client.list_receipt_chat_gpt_validations_by_status(status=status)


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_error",
    [
        (
            "ResourceNotFoundException",
            "Table not found",
            "Could not list receipt ChatGPT validations from DynamoDB",
        ),
        (
            "ProvisionedThroughputExceededException",
            "Throughput exceeded",
            "Throughput exceeded",
        ),
        (
            "InternalServerError",
            "Internal server error",
            "Internal server error",
        ),
        (
            "ValidationException",
            "One or more parameters were invalid",
            "One or more parameters given were invalid",
        ),
        (
            "AccessDeniedException",
            "Access denied",
            "Access denied",
        ),
        (
            "UnknownError",
            "Unknown error occurred",
            "Error listing receipt ChatGPT validations",
        ),
    ],
)
def test_listReceiptChatGPTValidationsByStatus_client_errors(
    dynamodb_table,
    mocker,
    error_code,
    error_message,
    expected_error,
):
    """Test handling of client errors when listing ChatGPT validations by status."""
    client = DynamoClient(dynamodb_table)

    _patch_client_error(
        mocker, client, "query", error_code, "Query", error_message
    )

    with pytest.raises(
        _expected_client_exception(error_code), match=error_message
    ):
        client.list_receipt_chat_gpt_validations_by_status(status="VALID")
