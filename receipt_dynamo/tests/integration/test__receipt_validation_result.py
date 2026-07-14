import uuid
from copy import deepcopy
from typing import Any, Dict, List, Literal, Optional, Type
from unittest.mock import MagicMock, call, patch

import pytest
from botocore.exceptions import ClientError
from pytest_mock import MockerFixture

from receipt_dynamo import (
    ReceiptValidationResult,
    item_to_receipt_validation_result,
)
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.data.shared_exceptions import (
    DynamoDBError,
    DynamoDBServerError,
    DynamoDBThroughputError,
    EntityAlreadyExistsError,
    EntityNotFoundError,
    EntityValidationError,
    OperationError,
)

pytestmark = [pytest.mark.integration, pytest.mark.unused_in_production]


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


@pytest.fixture
def sample_receipt_validation_result() -> ReceiptValidationResult:
    """
    Creates a sample ReceiptValidationResult for testing.
    """
    return ReceiptValidationResult(
        receipt_id=1,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        field_name="total_amount",
        result_index=0,
        type="error",
        message="Total amount does not match sum of items",
        reasoning="The total ($45.99) does not equal the sum of line items ($42.99)",
        field="price",
        expected_value="42.99",
        actual_value="45.99",
        validation_timestamp="2023-05-15T10:30:00",
        metadata={
            "source_info": {"model": "validation-v1"},
            "confidence": 0.92,
        },
    )


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

ADD_ERROR_SCENARIOS = [
    (
        "ConditionalCheckFailedException",
        EntityAlreadyExistsError,
        "already exists",
    ),
] + ERROR_SCENARIOS

UPDATE_ERROR_SCENARIOS = [
    (
        "ConditionalCheckFailedException",
        EntityNotFoundError,
        "does not exist",
    ),
] + ERROR_SCENARIOS

DELETE_ERROR_SCENARIOS = [
    (
        "ConditionalCheckFailedException",
        EntityNotFoundError,
        "does not exist",
    ),
] + ERROR_SCENARIOS


ADD_VALIDATION_SCENARIOS = [
    (None, "result cannot be None"),
    (
        "not-a-validation-result",
        "result must be an instance of ReceiptValidationResult",
    ),
]

UPDATE_VALIDATION_SCENARIOS = [
    (None, "result cannot be None"),
    (
        "not a ReceiptValidationResult",
        "result must be an instance of ReceiptValidationResult",
    ),
]


@pytest.mark.integration
def test_addReceiptValidationResult_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_validation_result: ReceiptValidationResult,
):
    """Test adding a validation result successfully"""
    client = DynamoClient(dynamodb_table)

    client.add_receipt_validation_result(sample_receipt_validation_result)

    response = client._client.get_item(
        TableName=dynamodb_table,
        Key={
            "PK": {"S": f"IMAGE#{sample_receipt_validation_result.image_id}"},
            "SK": {
                "S": f"RECEIPT#{sample_receipt_validation_result.receipt_id:05d}#ANALYSIS#VALIDATION#CATEGORY#{sample_receipt_validation_result.field_name}#RESULT#{sample_receipt_validation_result.result_index}"
            },
        },
    )

    assert "Item" in response
    result = item_to_receipt_validation_result(response["Item"])
    assert result == sample_receipt_validation_result


@pytest.mark.integration
def test_addReceiptValidationResult_duplicate_raises(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_validation_result: ReceiptValidationResult,
):
    """Test that adding a duplicate validation result raises an error"""
    client = DynamoClient(dynamodb_table)

    client.add_receipt_validation_result(sample_receipt_validation_result)

    with pytest.raises(
        EntityAlreadyExistsError,
        match="already exists",
    ):
        client.add_receipt_validation_result(sample_receipt_validation_result)


@pytest.mark.integration
@pytest.mark.parametrize("invalid_input,error_match", ADD_VALIDATION_SCENARIOS)
def test_addReceiptValidationResult_invalid_parameters(
    dynamodb_table: Literal["MyMockedTable"],
    invalid_input: Any,
    error_match: str,
) -> None:
    """Tests that add_receipt_validation_result raises appropriate error for
    invalid inputs."""
    client = DynamoClient(dynamodb_table)
    with pytest.raises(OperationError, match=error_match):
        client.add_receipt_validation_result(invalid_input)  # type: ignore


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,expected_exception,error_match", ADD_ERROR_SCENARIOS
)
def test_addReceiptValidationResult_client_errors(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_validation_result: ReceiptValidationResult,
    mocker: MockerFixture,
    error_code: str,
    expected_exception: Type[Exception],
    error_match: str,
) -> None:
    """Tests that add_receipt_validation_result raises appropriate exceptions for various
    ClientError scenarios."""
    client = DynamoClient(dynamodb_table)
    mock_put = _patch_client_error(
        mocker, client, "put_item", error_code, "PutItem"
    )
    with pytest.raises(expected_exception, match=error_match):
        client.add_receipt_validation_result(sample_receipt_validation_result)
    mock_put.assert_called_once()


@pytest.mark.integration
def test_addReceiptValidationResults_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_validation_result: ReceiptValidationResult,
):
    """Test adding multiple validation results successfully"""
    client = DynamoClient(dynamodb_table)

    result1 = sample_receipt_validation_result

    result2 = deepcopy(result1)
    result2.result_index = 1
    result2.message = "Different message"

    result3 = deepcopy(result1)
    result3.result_index = 2
    result3.field_name = "subtotal"

    validation_results = [result1, result2, result3]

    client.add_receipt_validation_results(validation_results)

    for result in validation_results:
        response = client._client.get_item(
            TableName=dynamodb_table,
            Key={
                "PK": {"S": f"IMAGE#{result.image_id}"},
                "SK": {
                    "S": f"RECEIPT#{result.receipt_id:05d}#ANALYSIS#VALIDATION#CATEGORY#{result.field_name}#RESULT#{result.result_index}"
                },
            },
        )
        assert "Item" in response
        retrieved_result = item_to_receipt_validation_result(response["Item"])
        assert retrieved_result == result


@pytest.mark.integration
def test_addReceiptValidationResults_with_large_batch(
    dynamodb_table, sample_receipt_validation_result
):
    """Test adding a large batch of validation results (more than 25)"""
    client = DynamoClient(dynamodb_table)

    validation_results = []
    for i in range(30):
        result = deepcopy(sample_receipt_validation_result)
        result.result_index = i
        validation_results.append(result)

    client.add_receipt_validation_results(validation_results)

    for result in validation_results:
        response = client._client.get_item(
            TableName=dynamodb_table,
            Key={
                "PK": {"S": f"IMAGE#{result.image_id}"},
                "SK": {
                    "S": f"RECEIPT#{result.receipt_id:05d}#ANALYSIS#VALIDATION#CATEGORY#{result.field_name}#RESULT#{result.result_index}"
                },
            },
        )
        assert "Item" in response
        retrieved_result = item_to_receipt_validation_result(response["Item"])
        assert retrieved_result == result


@pytest.mark.integration
def test_addReceiptValidationResults_with_unprocessed_items_retries(
    dynamodb_table, sample_receipt_validation_result, mocker
):
    """Test that unprocessed items are retried when adding validation results"""
    client = DynamoClient(dynamodb_table)

    validation_results = []
    for i in range(5):
        result = deepcopy(sample_receipt_validation_result)
        result.result_index = i
        validation_results.append(result)

    original_batch_write = client._client.batch_write_item

    def batch_write_side_effect(*args, **kwargs):
        if batch_write_side_effect.call_count == 0:
            batch_write_side_effect.call_count += 1
            return {
                "UnprocessedItems": {
                    dynamodb_table: [
                        {
                            "PutRequest": {
                                "Item": validation_results[0].to_item()
                            }
                        }
                    ]
                }
            }
        else:
            return {"UnprocessedItems": {}}

    batch_write_side_effect.call_count = 0

    mock_batch_write = mocker.patch.object(
        client._client, "batch_write_item", side_effect=batch_write_side_effect
    )

    client.add_receipt_validation_results(validation_results)

    assert mock_batch_write.call_count >= 2


ADD_RESULTS_VALIDATION_SCENARIOS = [
    (None, "results cannot be None"),
    (
        "not-a-list",
        "results must be a list",
    ),
    (
        ["not-a-validation-result"],
        "All items in results must be instances of ReceiptValidationResult",
    ),
]


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,error_match", ADD_RESULTS_VALIDATION_SCENARIOS
)
def test_addReceiptValidationResults_invalid_parameters(
    dynamodb_table: Literal["MyMockedTable"],
    invalid_input: Any,
    error_match: str,
) -> None:
    """Tests that add_receipt_validation_results raises appropriate error for
    invalid inputs."""
    client = DynamoClient(dynamodb_table)
    with pytest.raises(OperationError, match=error_match):
        client.add_receipt_validation_results(invalid_input)  # type: ignore


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,expected_exception,error_match", ERROR_SCENARIOS
)
def test_addReceiptValidationResults_client_errors(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_validation_result: ReceiptValidationResult,
    mocker: MockerFixture,
    error_code: str,
    expected_exception: Type[Exception],
    error_match: str,
) -> None:
    """Tests that add_receipt_validation_results raises appropriate exceptions for various
    ClientError scenarios."""
    client = DynamoClient(dynamodb_table)

    validation_results = []
    for i in range(3):
        result = deepcopy(sample_receipt_validation_result)
        result.result_index = i
        validation_results.append(result)

    mock_batch_write = _patch_client_error(
        mocker, client, "batch_write_item", error_code, "BatchWriteItem"
    )
    with pytest.raises(expected_exception, match=error_match):
        client.add_receipt_validation_results(validation_results)
    mock_batch_write.assert_called_once()


@pytest.mark.integration
def test_updateReceiptValidationResult_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_validation_result: ReceiptValidationResult,
):
    """Test updating a validation result successfully"""
    client = DynamoClient(dynamodb_table)

    client.add_receipt_validation_result(sample_receipt_validation_result)

    updated_result = deepcopy(sample_receipt_validation_result)
    updated_result.message = "Updated message for testing"
    updated_result.reasoning = "Updated reasoning for testing"

    client.update_receipt_validation_result(updated_result)

    response = client._client.get_item(
        TableName=dynamodb_table,
        Key={
            "PK": {"S": f"IMAGE#{updated_result.image_id}"},
            "SK": {
                "S": f"RECEIPT#{updated_result.receipt_id:05d}#ANALYSIS#VALIDATION#CATEGORY#{updated_result.field_name}#RESULT#{updated_result.result_index}"
            },
        },
    )

    assert "Item" in response
    result = item_to_receipt_validation_result(response["Item"])
    assert result == updated_result
    assert result.message == "Updated message for testing"
    assert result.reasoning == "Updated reasoning for testing"


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,error_match", UPDATE_VALIDATION_SCENARIOS
)
def test_updateReceiptValidationResult_invalid_parameters(
    dynamodb_table: Literal["MyMockedTable"],
    invalid_input: Any,
    error_match: str,
) -> None:
    """Tests that update_receipt_validation_result raises appropriate error for
    invalid inputs."""
    client = DynamoClient(dynamodb_table)
    with pytest.raises(OperationError, match=error_match):
        client.update_receipt_validation_result(invalid_input)  # type: ignore


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,expected_exception,error_match", UPDATE_ERROR_SCENARIOS
)
def test_updateReceiptValidationResult_client_errors(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_validation_result: ReceiptValidationResult,
    mocker: MockerFixture,
    error_code: str,
    expected_exception: Type[Exception],
    error_match: str,
) -> None:
    """Tests that update_receipt_validation_result raises appropriate exceptions for various
    ClientError scenarios."""
    client = DynamoClient(dynamodb_table)
    mock_put = _patch_client_error(
        mocker, client, "put_item", error_code, "PutItem"
    )
    with pytest.raises(expected_exception, match=error_match):
        client.update_receipt_validation_result(
            sample_receipt_validation_result
        )
    mock_put.assert_called_once()


@pytest.mark.integration
def test_updateReceiptValidationResults_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_validation_result: ReceiptValidationResult,
):
    """Test updating multiple validation results successfully"""
    client = DynamoClient(dynamodb_table)

    result1 = sample_receipt_validation_result

    result2 = deepcopy(result1)
    result2.result_index = 1
    result2.message = "Second message"

    result3 = deepcopy(result1)
    result3.result_index = 2
    result3.field_name = "subtotal"

    validation_results = [result1, result2, result3]

    client.add_receipt_validation_results(validation_results)

    updated_results = []
    for i, result in enumerate(validation_results):
        updated = deepcopy(result)
        updated.message = f"Updated message {i}"
        updated.reasoning = f"Updated reasoning {i}"
        updated_results.append(updated)

    client.update_receipt_validation_results(updated_results)

    for i, result in enumerate(updated_results):
        response = client._client.get_item(
            TableName=dynamodb_table,
            Key={
                "PK": {"S": f"IMAGE#{result.image_id}"},
                "SK": {
                    "S": f"RECEIPT#{result.receipt_id:05d}#ANALYSIS#VALIDATION#CATEGORY#{result.field_name}#RESULT#{result.result_index}"
                },
            },
        )
        assert "Item" in response
        retrieved_result = item_to_receipt_validation_result(response["Item"])
        assert retrieved_result.message == f"Updated message {i}"
        assert retrieved_result.reasoning == f"Updated reasoning {i}"


@pytest.mark.integration
def test_updateReceiptValidationResults_with_large_batch(
    dynamodb_table, sample_receipt_validation_result
):
    """Test updating a large batch of validation results (more than 25)"""
    client = DynamoClient(dynamodb_table)

    validation_results = []
    for i in range(30):
        result = deepcopy(sample_receipt_validation_result)
        result.result_index = i
        validation_results.append(result)

    client.add_receipt_validation_results(validation_results)

    updated_results = []
    for i, result in enumerate(validation_results):
        updated = deepcopy(result)
        updated.message = f"Large batch updated message {i}"
        updated_results.append(updated)

    client.update_receipt_validation_results(updated_results)

    for i in [0, 15, 29]:  # Check first, middle, and last
        result = updated_results[i]
        response = client._client.get_item(
            TableName=dynamodb_table,
            Key={
                "PK": {"S": f"IMAGE#{result.image_id}"},
                "SK": {
                    "S": f"RECEIPT#{result.receipt_id:05d}#ANALYSIS#VALIDATION#CATEGORY#{result.field_name}#RESULT#{result.result_index}"
                },
            },
        )
        assert "Item" in response
        retrieved_result = item_to_receipt_validation_result(response["Item"])
        assert retrieved_result.message == f"Large batch updated message {i}"


UPDATE_RESULTS_VALIDATION_SCENARIOS = [
    (None, "results cannot be None"),
    (
        "not-a-list",
        "results must be a list",
    ),
    (
        [123, "not-a-validation-result"],
        "All items in results must be instances of ReceiptValidationResult",
    ),
]


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,error_match", UPDATE_RESULTS_VALIDATION_SCENARIOS
)
def test_updateReceiptValidationResults_invalid_inputs(
    dynamodb_table: Literal["MyMockedTable"],
    invalid_input: Any,
    error_match: str,
) -> None:
    """Tests that update_receipt_validation_results raises appropriate error for
    invalid inputs."""
    client = DynamoClient(dynamodb_table)
    with pytest.raises(OperationError, match=error_match):
        client.update_receipt_validation_results(invalid_input)  # type: ignore


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,expected_exception,error_match", ERROR_SCENARIOS
)
def test_updateReceiptValidationResults_client_errors(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_validation_result: ReceiptValidationResult,
    mocker: MockerFixture,
    error_code: str,
    expected_exception: Type[Exception],
    error_match: str,
) -> None:
    """Tests that update_receipt_validation_results raises appropriate exceptions for various
    ClientError scenarios."""
    client = DynamoClient(dynamodb_table)

    validation_results = []
    for i in range(3):
        result = deepcopy(sample_receipt_validation_result)
        result.result_index = i
        validation_results.append(result)

    mock_transact = _patch_client_error(
        mocker,
        client,
        "transact_write_items",
        error_code,
        "TransactWriteItems",
    )
    with pytest.raises(expected_exception, match=error_match):
        client.update_receipt_validation_results(validation_results)
    mock_transact.assert_called_once()


@pytest.mark.integration
def test_deleteReceiptValidationResult_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_validation_result: ReceiptValidationResult,
):
    """Test deleting a validation result successfully"""
    client = DynamoClient(dynamodb_table)

    client.add_receipt_validation_result(sample_receipt_validation_result)

    response = client._client.get_item(
        TableName=dynamodb_table,
        Key={
            "PK": {"S": f"IMAGE#{sample_receipt_validation_result.image_id}"},
            "SK": {
                "S": f"RECEIPT#{sample_receipt_validation_result.receipt_id:05d}#ANALYSIS#VALIDATION#CATEGORY#{sample_receipt_validation_result.field_name}#RESULT#{sample_receipt_validation_result.result_index}"
            },
        },
    )
    assert "Item" in response

    client.delete_receipt_validation_result(sample_receipt_validation_result)

    response = client._client.get_item(
        TableName=dynamodb_table,
        Key={
            "PK": {"S": f"IMAGE#{sample_receipt_validation_result.image_id}"},
            "SK": {
                "S": f"RECEIPT#{sample_receipt_validation_result.receipt_id:05d}#ANALYSIS#VALIDATION#CATEGORY#{sample_receipt_validation_result.field_name}#RESULT#{sample_receipt_validation_result.result_index}"
            },
        },
    )
    assert "Item" not in response


DELETE_VALIDATION_SCENARIOS = [
    (None, "result cannot be None"),
    (
        "not-a-validation-result",
        "result must be an instance of ReceiptValidationResult",
    ),
]


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,error_match", DELETE_VALIDATION_SCENARIOS
)
def test_deleteReceiptValidationResult_invalid_parameters(
    dynamodb_table: Literal["MyMockedTable"],
    invalid_input: Any,
    error_match: str,
) -> None:
    """Tests that delete_receipt_validation_result raises appropriate error for
    invalid inputs."""
    client = DynamoClient(dynamodb_table)
    with pytest.raises(OperationError, match=error_match):
        client.delete_receipt_validation_result(invalid_input)  # type: ignore


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,expected_exception,error_match", DELETE_ERROR_SCENARIOS
)
def test_deleteReceiptValidationResult_client_errors(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_validation_result: ReceiptValidationResult,
    mocker: MockerFixture,
    error_code: str,
    expected_exception: Type[Exception],
    error_match: str,
) -> None:
    """Tests that delete_receipt_validation_result raises appropriate exceptions for various
    ClientError scenarios."""
    client = DynamoClient(dynamodb_table)
    mock_delete = _patch_client_error(
        mocker, client, "delete_item", error_code, "DeleteItem"
    )
    with pytest.raises(expected_exception, match=error_match):
        client.delete_receipt_validation_result(
            sample_receipt_validation_result
        )
    mock_delete.assert_called_once()


@pytest.mark.integration
def test_deleteReceiptValidationResults_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_validation_result: ReceiptValidationResult,
):
    """Test deleting multiple validation results successfully"""
    client = DynamoClient(dynamodb_table)

    result1 = sample_receipt_validation_result

    result2 = deepcopy(result1)
    result2.result_index = 1

    result3 = deepcopy(result1)
    result3.result_index = 2
    result3.field_name = "subtotal"

    validation_results = [result1, result2, result3]

    client.add_receipt_validation_results(validation_results)

    for result in validation_results:
        response = client._client.get_item(
            TableName=dynamodb_table,
            Key={
                "PK": {"S": f"IMAGE#{result.image_id}"},
                "SK": {
                    "S": f"RECEIPT#{result.receipt_id:05d}#ANALYSIS#VALIDATION#CATEGORY#{result.field_name}#RESULT#{result.result_index}"
                },
            },
        )
        assert "Item" in response

    client.delete_receipt_validation_results(validation_results)

    for result in validation_results:
        response = client._client.get_item(
            TableName=dynamodb_table,
            Key={
                "PK": {"S": f"IMAGE#{result.image_id}"},
                "SK": {
                    "S": f"RECEIPT#{result.receipt_id:05d}#ANALYSIS#VALIDATION#CATEGORY#{result.field_name}#RESULT#{result.result_index}"
                },
            },
        )
        assert "Item" not in response


DELETE_RESULTS_VALIDATION_SCENARIOS = [
    (None, "results cannot be None"),
    (
        "not-a-list",
        "results must be a list",
    ),
    (
        [123, "not-a-validation-result"],
        "All items in results must be instances of ReceiptValidationResult",
    ),
]


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,error_match", DELETE_RESULTS_VALIDATION_SCENARIOS
)
def test_deleteReceiptValidationResults_invalid_parameters(
    dynamodb_table: Literal["MyMockedTable"],
    invalid_input: Any,
    error_match: str,
) -> None:
    """Tests that delete_receipt_validation_results raises appropriate error for
    invalid inputs."""
    client = DynamoClient(dynamodb_table)
    with pytest.raises(OperationError, match=error_match):
        client.delete_receipt_validation_results(invalid_input)  # type: ignore


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,expected_exception,error_match", ERROR_SCENARIOS
)
def test_deleteReceiptValidationResults_client_errors(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_validation_result: ReceiptValidationResult,
    mocker: MockerFixture,
    error_code: str,
    expected_exception: Type[Exception],
    error_match: str,
) -> None:
    """Tests that delete_receipt_validation_results raises appropriate exceptions for various
    ClientError scenarios."""
    client = DynamoClient(dynamodb_table)

    validation_results = []
    for i in range(3):
        result = deepcopy(sample_receipt_validation_result)
        result.result_index = i
        validation_results.append(result)

    mock_batch_write = _patch_client_error(
        mocker, client, "batch_write_item", error_code, "BatchWriteItem"
    )
    with pytest.raises(expected_exception, match=error_match):
        client.delete_receipt_validation_results(validation_results)
    mock_batch_write.assert_called_once()


@pytest.mark.integration
def test_deleteReceiptValidationResults_with_unprocessed_items_retries(
    dynamodb_table, sample_receipt_validation_result, mocker
):
    """Test that unprocessed items are retried when deleting validation results"""
    client = DynamoClient(dynamodb_table)

    validation_results = []
    for i in range(5):
        result = deepcopy(sample_receipt_validation_result)
        result.result_index = i
        validation_results.append(result)

    client.add_receipt_validation_results(validation_results)

    def batch_write_side_effect(*args, **kwargs):
        if batch_write_side_effect.call_count == 0:
            batch_write_side_effect.call_count += 1
            return {
                "UnprocessedItems": {
                    dynamodb_table: [
                        {"DeleteRequest": {"Key": validation_results[0].key}}
                    ]
                }
            }
        else:
            return {"UnprocessedItems": {}}

    batch_write_side_effect.call_count = 0

    mock_batch_write = mocker.patch.object(
        client._client, "batch_write_item", side_effect=batch_write_side_effect
    )

    client.delete_receipt_validation_results(validation_results)

    assert mock_batch_write.call_count >= 2


@pytest.mark.integration
def test_deleteReceiptValidationResults_with_large_batch(
    dynamodb_table, sample_receipt_validation_result
):
    """Test deleting a large batch of validation results (more than 25)"""
    client = DynamoClient(dynamodb_table)

    validation_results = []
    for i in range(30):
        result = deepcopy(sample_receipt_validation_result)
        result.result_index = i
        validation_results.append(result)

    client.add_receipt_validation_results(validation_results)

    for result in validation_results:
        response = client._client.get_item(
            TableName=dynamodb_table,
            Key={
                "PK": {"S": f"IMAGE#{result.image_id}"},
                "SK": {
                    "S": f"RECEIPT#{result.receipt_id:05d}#ANALYSIS#VALIDATION#CATEGORY#{result.field_name}#RESULT#{result.result_index}"
                },
            },
        )
        assert "Item" in response

    client.delete_receipt_validation_results(validation_results)

    for i in [0, 15, 29]:  # Check first, middle, and last
        result = validation_results[i]
        response = client._client.get_item(
            TableName=dynamodb_table,
            Key={
                "PK": {"S": f"IMAGE#{result.image_id}"},
                "SK": {
                    "S": f"RECEIPT#{result.receipt_id:05d}#ANALYSIS#VALIDATION#CATEGORY#{result.field_name}#RESULT#{result.result_index}"
                },
            },
        )
        assert "Item" not in response


@pytest.mark.integration
def test_getReceiptValidationResult_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_validation_result: ReceiptValidationResult,
):
    """Test retrieving a validation result successfully"""
    client = DynamoClient(dynamodb_table)

    client.add_receipt_validation_result(sample_receipt_validation_result)

    result = client.get_receipt_validation_result(
        receipt_id=sample_receipt_validation_result.receipt_id,
        image_id=sample_receipt_validation_result.image_id,
        field_name=sample_receipt_validation_result.field_name,
        result_index=sample_receipt_validation_result.result_index,
    )

    assert result is not None
    assert result == sample_receipt_validation_result
    assert result.message == sample_receipt_validation_result.message
    assert result.reasoning == sample_receipt_validation_result.reasoning


@pytest.mark.integration
def test_getReceiptValidationResult_not_found(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_validation_result: ReceiptValidationResult,
):
    """Test retrieving a validation result that doesn't exist"""
    client = DynamoClient(dynamodb_table)
    field_name = sample_receipt_validation_result.field_name
    result_index = sample_receipt_validation_result.result_index

    empty_response: Dict[str, Any] = {}

    client._client.get_item = MagicMock(return_value=empty_response)  # type: ignore[method-assign]

    with pytest.raises(
        EntityNotFoundError,
        match=f"ReceiptValidationResult with field {field_name} and index {result_index} not found",
    ):
        client.get_receipt_validation_result(
            receipt_id=sample_receipt_validation_result.receipt_id,
            image_id=sample_receipt_validation_result.image_id,
            field_name=field_name,
            result_index=result_index,
        )


@pytest.mark.integration
@pytest.mark.parametrize(
    "receipt_id,image_id,field_name,result_index,expected_error",
    [
        (
            None,
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "field",
            0,
            "receipt_id cannot be None",
        ),
        (
            1,
            None,
            "field",
            0,
            "image_id cannot be None",
        ),
        (
            1,
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            None,
            0,
            "field_name cannot be None",
        ),
        (
            1,
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "field",
            None,
            "result_index cannot be None",
        ),
        (1, "", "field", 0, "uuid must be a valid UUIDv4"),
        (
            1,
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "",
            0,
            "field_name must not be empty",
        ),
        (
            1,
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "field",
            -1,
            "result_index must be non-negative",
        ),
    ],
)
def test_getReceiptValidationResult_invalid_parameters(
    dynamodb_table: Literal["MyMockedTable"],
    receipt_id: Any,
    image_id: Any,
    field_name: Any,
    result_index: Any,
    expected_error: str,
) -> None:
    """Test retrieving a validation result with invalid parameters"""
    client = DynamoClient(dynamodb_table)

    with pytest.raises(EntityValidationError, match=expected_error):
        client.get_receipt_validation_result(
            receipt_id=receipt_id,
            image_id=image_id,
            field_name=field_name,
            result_index=result_index,
        )


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,expected_exception,error_match", ERROR_SCENARIOS
)
def test_getReceiptValidationResult_client_errors(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_validation_result: ReceiptValidationResult,
    mocker: MockerFixture,
    error_code: str,
    expected_exception: Type[Exception],
    error_match: str,
) -> None:
    """Tests that get_receipt_validation_result raises appropriate exceptions for various
    ClientError scenarios."""
    client = DynamoClient(dynamodb_table)
    mock_get = _patch_client_error(
        mocker, client, "get_item", error_code, "GetItem"
    )
    with pytest.raises(expected_exception, match=error_match):
        client.get_receipt_validation_result(
            receipt_id=sample_receipt_validation_result.receipt_id,
            image_id=sample_receipt_validation_result.image_id,
            field_name=sample_receipt_validation_result.field_name,
            result_index=sample_receipt_validation_result.result_index,
        )
    mock_get.assert_called_once()


@pytest.mark.integration
def test_listReceiptValidationResults_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_validation_result: ReceiptValidationResult,
    mocker,
):
    """Test listing all validation results successfully"""
    client = DynamoClient(dynamodb_table)

    result1 = sample_receipt_validation_result

    result2 = deepcopy(result1)
    result2.result_index = 1
    result2.field_name = "subtotal"

    result3 = deepcopy(result1)
    result3.result_index = 2
    result3.field_name = "tax_amount"

    client.add_receipt_validation_results([result1, result2, result3])

    mock_scan = mocker.patch.object(client._client, "scan")
    mock_scan.return_value = {
        "Items": [result1.to_item(), result2.to_item(), result3.to_item()],
        "Count": 3,
        "ScannedCount": 3,
    }

    results, last_key = client.list_receipt_validation_results()

    assert len(results) == 3
    assert last_key is None

    field_names = [r.field_name for r in results]
    assert "total_amount" in field_names
    assert "subtotal" in field_names
    assert "tax_amount" in field_names


@pytest.mark.integration
def test_listReceiptValidationResults_with_pagination(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_validation_result: ReceiptValidationResult,
    mocker,
):
    """Test listing validation results with pagination"""
    client = DynamoClient(dynamodb_table)

    validation_results = []
    for i in range(10):
        result = deepcopy(sample_receipt_validation_result)
        result.result_index = i
        result.field_name = f"field_{i}"
        validation_results.append(result)

    client.add_receipt_validation_results(validation_results)

    mock_scan = mocker.patch.object(client._client, "scan")

    first_page_items = [r.to_item() for r in validation_results[:3]]
    first_page_response = {
        "Items": first_page_items,
        "Count": 3,
        "ScannedCount": 3,
        "LastEvaluatedKey": {"PK": {"S": "some_key"}, "SK": {"S": "some_sk"}},
    }

    second_page_items = [r.to_item() for r in validation_results[3:6]]
    second_page_response = {
        "Items": second_page_items,
        "Count": 3,
        "ScannedCount": 3,
        "LastEvaluatedKey": {
            "PK": {"S": "some_key2"},
            "SK": {"S": "some_sk2"},
        },
    }

    third_page_items = [r.to_item() for r in validation_results[6:9]]
    third_page_response = {
        "Items": third_page_items,
        "Count": 3,
        "ScannedCount": 3,
        "LastEvaluatedKey": {
            "PK": {"S": "some_key3"},
            "SK": {"S": "some_sk3"},
        },
    }

    fourth_page_items = [r.to_item() for r in validation_results[9:]]
    fourth_page_response = {
        "Items": fourth_page_items,
        "Count": 1,
        "ScannedCount": 1,
    }

    mock_scan.side_effect = [
        first_page_response,
        second_page_response,
        third_page_response,
        fourth_page_response,
    ]

    page1_results, pagination_key1 = client.list_receipt_validation_results(
        limit=3
    )

    assert pagination_key1 is not None
    assert len(page1_results) == 3

    page2_results, pagination_key2 = client.list_receipt_validation_results(
        limit=3, last_evaluated_key=pagination_key1
    )

    assert pagination_key2 is not None
    assert len(page2_results) == 3

    page3_results, pagination_key3 = client.list_receipt_validation_results(
        limit=3, last_evaluated_key=pagination_key2
    )

    assert pagination_key3 is not None
    assert len(page3_results) == 3

    page4_results, pagination_key4 = client.list_receipt_validation_results(
        limit=3, last_evaluated_key=pagination_key3
    )

    assert pagination_key4 is None  # Should be None as this is the last page
    assert len(page4_results) == 1

    all_field_names = [
        r.field_name
        for r in page1_results + page2_results + page3_results + page4_results
    ]
    assert len(all_field_names) == 10
    assert len(set(all_field_names)) == 10


@pytest.mark.integration
def test_listReceiptValidationResults_empty_results(
    dynamodb_table: Literal["MyMockedTable"],
    mocker,
):
    """Test listing validation results when none exist"""
    client = DynamoClient(dynamodb_table)

    mock_scan = mocker.patch.object(client._client, "scan")
    mock_scan.return_value = {
        "Items": [],
        "Count": 0,
        "ScannedCount": 0,
    }

    results, last_key = client.list_receipt_validation_results()

    assert len(results) == 0
    assert last_key is None


@pytest.mark.integration
def test_listReceiptValidationResults_with_negative_limit(
    dynamodb_table: Literal["MyMockedTable"],
):
    """Test listing validation results with a negative limit should raise EntityValidationError"""
    client = DynamoClient(dynamodb_table)

    with pytest.raises(
        EntityValidationError, match="Invalid value for parameter Limit"
    ):
        client.list_receipt_validation_results(limit=-1)


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,expected_exception,error_match", ERROR_SCENARIOS
)
def test_listReceiptValidationResults_client_errors(
    dynamodb_table: Literal["MyMockedTable"],
    mocker: MockerFixture,
    error_code: str,
    expected_exception: Type[Exception],
    error_match: str,
) -> None:
    """Tests that list_receipt_validation_results raises appropriate exceptions for various
    ClientError scenarios."""
    client = DynamoClient(dynamodb_table)
    mock_query = _patch_client_error(
        mocker, client, "query", error_code, "Query"
    )
    with pytest.raises(expected_exception, match=error_match):
        client.list_receipt_validation_results()
    mock_query.assert_called_once()


@pytest.mark.integration
def test_listReceiptValidationResultsByType_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_validation_result: ReceiptValidationResult,
    mocker,
):
    """Test listing validation results by type successfully"""
    client = DynamoClient(dynamodb_table)

    result1 = deepcopy(sample_receipt_validation_result)
    result1.type = "error"

    result2 = deepcopy(result1)
    result2.result_index = 1
    result2.field_name = "subtotal"
    result2.type = "error"

    result3 = deepcopy(result1)
    result3.result_index = 2
    result3.field_name = "tax_amount"
    result3.type = "warning"

    result4 = deepcopy(result1)
    result4.result_index = 3
    result4.field_name = "date"
    result4.type = "info"

    client.add_receipt_validation_results([result1, result2, result3, result4])

    mock_query = mocker.patch.object(client._client, "query")

    error_response = {
        "Items": [result1.to_item(), result2.to_item()],
        "Count": 2,
        "ScannedCount": 2,
    }
    mock_query.return_value = error_response

    error_results, _ = client.list_receipt_validation_results_by_type(
        result_type="error"
    )

    assert len(error_results) == 2

    for result in error_results:
        assert result.type == "error"

    warning_response = {
        "Items": [result3.to_item()],
        "Count": 1,
        "ScannedCount": 1,
    }
    mock_query.return_value = warning_response

    warning_results, _ = client.list_receipt_validation_results_by_type(
        result_type="warning"
    )

    assert len(warning_results) == 1
    assert warning_results[0].type == "warning"
    assert warning_results[0].field_name == "tax_amount"

    info_response = {
        "Items": [result4.to_item()],
        "Count": 1,
        "ScannedCount": 1,
    }
    mock_query.return_value = info_response

    info_results, _ = client.list_receipt_validation_results_by_type(
        result_type="info"
    )

    assert len(info_results) == 1
    assert info_results[0].type == "info"
    assert info_results[0].field_name == "date"


@pytest.mark.integration
def test_listReceiptValidationResultsByType_pagination(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_validation_result: ReceiptValidationResult,
    mocker,
):
    """Test listing validation results by type with pagination"""
    client = DynamoClient(dynamodb_table)

    validation_results = []
    for i in range(10):
        result = deepcopy(sample_receipt_validation_result)
        result.result_index = i
        result.field_name = f"field_{i}"
        result.type = "error"
        validation_results.append(result)

    client.add_receipt_validation_results(validation_results)

    mock_query = mocker.patch.object(client._client, "query")

    first_page_items = [r.to_item() for r in validation_results[:3]]
    first_page_response = {
        "Items": first_page_items,
        "Count": 3,
        "ScannedCount": 3,
        "LastEvaluatedKey": {
            "PK": {"S": "some_key"},
            "SK": {"S": "some_sk"},
            "GSI3SK": {"S": "some_gsi3sk"},
        },
    }

    second_page_items = [r.to_item() for r in validation_results[3:6]]
    second_page_response = {
        "Items": second_page_items,
        "Count": 3,
        "ScannedCount": 3,
        "LastEvaluatedKey": {
            "PK": {"S": "some_key2"},
            "SK": {"S": "some_sk2"},
            "GSI3SK": {"S": "some_gsi3sk2"},
        },
    }

    third_page_items = [r.to_item() for r in validation_results[6:9]]
    third_page_response = {
        "Items": third_page_items,
        "Count": 3,
        "ScannedCount": 3,
        "LastEvaluatedKey": {
            "PK": {"S": "some_key3"},
            "SK": {"S": "some_sk3"},
            "GSI3SK": {"S": "some_gsi3sk3"},
        },
    }

    fourth_page_items = [r.to_item() for r in validation_results[9:]]
    fourth_page_response = {
        "Items": fourth_page_items,
        "Count": 1,
        "ScannedCount": 1,
    }

    mock_query.side_effect = [
        first_page_response,
        second_page_response,
        third_page_response,
        fourth_page_response,
    ]

    page1_results, pagination_key1 = (
        client.list_receipt_validation_results_by_type(
            result_type="error", limit=3
        )
    )

    assert pagination_key1 is not None
    assert len(page1_results) == 3

    page2_results, pagination_key2 = (
        client.list_receipt_validation_results_by_type(
            result_type="error", limit=3, last_evaluated_key=pagination_key1
        )
    )

    assert pagination_key2 is not None
    assert len(page2_results) == 3

    page3_results, pagination_key3 = (
        client.list_receipt_validation_results_by_type(
            result_type="error", limit=3, last_evaluated_key=pagination_key2
        )
    )

    assert pagination_key3 is not None
    assert len(page3_results) == 3

    page4_results, pagination_key4 = (
        client.list_receipt_validation_results_by_type(
            result_type="error", limit=3, last_evaluated_key=pagination_key3
        )
    )

    assert pagination_key4 is None  # Should be None as this is the last page
    assert len(page4_results) == 1

    all_results = page1_results + page2_results + page3_results + page4_results
    assert len(all_results) == 10
    assert all(r.type == "error" for r in all_results)


@pytest.mark.integration
def test_listReceiptValidationResultsByType_empty_results(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_validation_result: ReceiptValidationResult,
    mocker,
):
    """Test listing validation results by type when none exist"""
    client = DynamoClient(dynamodb_table)

    result = deepcopy(sample_receipt_validation_result)
    result.type = "error"
    client.add_receipt_validation_result(result)

    mock_query = mocker.patch.object(client._client, "query")
    mock_query.return_value = {
        "Items": [],
        "Count": 0,
        "ScannedCount": 0,
    }

    results, pagination_key = client.list_receipt_validation_results_by_type(
        result_type="warning"
    )

    assert len(results) == 0
    assert pagination_key is None


@pytest.mark.integration
@pytest.mark.parametrize(
    "result_type,expected_error",
    [
        (None, "result_type parameter is required"),
        ("", "result_type must not be empty"),
    ],
)
def test_listReceiptValidationResultsByType_invalid_parameters(
    dynamodb_table: Literal["MyMockedTable"],
    result_type: Any,
    expected_error: str,
) -> None:
    """Test listing validation results by type with invalid parameters"""
    client = DynamoClient(dynamodb_table)

    with pytest.raises(EntityValidationError, match=expected_error):
        client.list_receipt_validation_results_by_type(result_type=result_type)


@pytest.mark.integration
def test_listReceiptValidationResultsByType_with_negative_limit(
    dynamodb_table: Literal["MyMockedTable"],
):
    """Test listing validation results by type with a negative limit should raise EntityValidationError"""
    client = DynamoClient(dynamodb_table)

    with pytest.raises(
        EntityValidationError, match="Invalid value for parameter Limit"
    ):
        client.list_receipt_validation_results_by_type(
            result_type="error", limit=-1
        )


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,expected_exception,error_match", ERROR_SCENARIOS
)
def test_listReceiptValidationResultsByType_client_errors(
    dynamodb_table: Literal["MyMockedTable"],
    mocker: MockerFixture,
    error_code: str,
    expected_exception: Type[Exception],
    error_match: str,
) -> None:
    """Tests that list_receipt_validation_results_by_type raises appropriate exceptions for various
    ClientError scenarios."""
    client = DynamoClient(dynamodb_table)
    mock_query = _patch_client_error(
        mocker, client, "query", error_code, "Query"
    )
    with pytest.raises(expected_exception, match=error_match):
        client.list_receipt_validation_results_by_type(result_type="error")
    mock_query.assert_called_once()


@pytest.mark.integration
def test_listReceiptValidationResultsForField_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_validation_result: ReceiptValidationResult,
    mocker,
):
    """
    Tests that listReceiptValidationResultsForField successfully retrieves validation results for a specific field.
    """
    client = DynamoClient(table_name=dynamodb_table)

    mock_query = mocker.patch.object(client._client, "query")
    mock_query.return_value = {
        "Items": [
            {
                "PK": {
                    "S": f"IMAGE#{sample_receipt_validation_result.image_id}"
                },
                "SK": {
                    "S": f"RECEIPT#{sample_receipt_validation_result.receipt_id:05d}#ANALYSIS#VALIDATION#CATEGORY#{sample_receipt_validation_result.field_name}#RESULT#0"
                },
                "receipt_id": {
                    "N": str(sample_receipt_validation_result.receipt_id)
                },
                "image_id": {"S": sample_receipt_validation_result.image_id},
                "field_name": {
                    "S": sample_receipt_validation_result.field_name
                },
                "result_index": {"N": "0"},
                "type": {"S": sample_receipt_validation_result.type},
                "message": {"S": sample_receipt_validation_result.message},
                "reasoning": {"S": sample_receipt_validation_result.reasoning},
                "field": {"S": sample_receipt_validation_result.field},
                "expected_value": {
                    "S": sample_receipt_validation_result.expected_value
                },
                "actual_value": {
                    "S": sample_receipt_validation_result.actual_value
                },
                "validation_timestamp": {
                    "S": sample_receipt_validation_result.validation_timestamp
                },
                "metadata": {
                    "M": {
                        "source_info": {
                            "M": {"model": {"S": "validation-v1"}}
                        },
                        "confidence": {"N": "0.92"},
                    }
                },
            }
        ],
        "Count": 1,
        "ScannedCount": 1,
    }

    results, _ = client.list_receipt_validation_results_for_field(
        receipt_id=sample_receipt_validation_result.receipt_id,
        image_id=sample_receipt_validation_result.image_id,
        field_name=sample_receipt_validation_result.field_name,
    )

    assert len(results) == 1
    assert results[0].receipt_id == sample_receipt_validation_result.receipt_id
    assert results[0].image_id == sample_receipt_validation_result.image_id
    assert results[0].field_name == sample_receipt_validation_result.field_name
    assert results[0].type == sample_receipt_validation_result.type

    mock_query.assert_called_once()
    call_args = mock_query.call_args
    assert call_args[1]["TableName"] == dynamodb_table
    assert "#pk = :pk AND begins_with(#sk, :sk_prefix)" in str(call_args)
    assert call_args[1]["ExpressionAttributeNames"] == {
        "#pk": "PK",
        "#sk": "SK",
    }
    assert call_args[1]["ExpressionAttributeValues"] == {
        ":pk": {"S": f"IMAGE#{sample_receipt_validation_result.image_id}"},
        ":sk_prefix": {
            "S": f"RECEIPT#{sample_receipt_validation_result.receipt_id:05d}#ANALYSIS#VALIDATION#CATEGORY#{sample_receipt_validation_result.field_name}#RESULT#"
        },
    }


@pytest.mark.integration
def test_listReceiptValidationResultsForField_with_pagination(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_validation_result: ReceiptValidationResult,
    mocker,
):
    """
    Tests that listReceiptValidationResultsForField correctly handles pagination.
    """
    client = DynamoClient(table_name=dynamodb_table)

    result2 = deepcopy(sample_receipt_validation_result)
    result2.result_index = 1

    mock_query = mocker.patch.object(client._client, "query")

    first_response = {
        "Items": [
            {
                "PK": {
                    "S": f"IMAGE#{sample_receipt_validation_result.image_id}"
                },
                "SK": {
                    "S": f"RECEIPT#{sample_receipt_validation_result.receipt_id:05d}#ANALYSIS#VALIDATION#CATEGORY#{sample_receipt_validation_result.field_name}#RESULT#0"
                },
                "receipt_id": {
                    "N": str(sample_receipt_validation_result.receipt_id)
                },
                "image_id": {"S": sample_receipt_validation_result.image_id},
                "field_name": {
                    "S": sample_receipt_validation_result.field_name
                },
                "result_index": {"N": "0"},
                "type": {"S": sample_receipt_validation_result.type},
                "message": {"S": sample_receipt_validation_result.message},
                "reasoning": {"S": sample_receipt_validation_result.reasoning},
                "field": {"S": sample_receipt_validation_result.field},
                "expected_value": {
                    "S": sample_receipt_validation_result.expected_value
                },
                "actual_value": {
                    "S": sample_receipt_validation_result.actual_value
                },
                "validation_timestamp": {
                    "S": sample_receipt_validation_result.validation_timestamp
                },
                "metadata": {
                    "M": {
                        "source_info": {
                            "M": {"model": {"S": "validation-v1"}}
                        },
                        "confidence": {"N": "0.92"},
                    }
                },
            }
        ],
        "Count": 1,
        "ScannedCount": 1,
        "LastEvaluatedKey": {
            "PK": {"S": f"IMAGE#{sample_receipt_validation_result.image_id}"},
            "SK": {
                "S": f"RECEIPT#{sample_receipt_validation_result.receipt_id:05d}#ANALYSIS#VALIDATION#CATEGORY#{sample_receipt_validation_result.field_name}#RESULT#0"
            },
        },
    }

    second_response = {
        "Items": [
            {
                "PK": {"S": f"IMAGE#{result2.image_id}"},
                "SK": {
                    "S": f"RECEIPT#{result2.receipt_id:05d}#ANALYSIS#VALIDATION#CATEGORY#{result2.field_name}#RESULT#1"
                },
                "receipt_id": {"N": str(result2.receipt_id)},
                "image_id": {"S": result2.image_id},
                "field_name": {"S": result2.field_name},
                "result_index": {"N": "1"},
                "type": {"S": result2.type},
                "message": {"S": result2.message},
                "reasoning": {"S": result2.reasoning},
                "field": {"S": result2.field},
                "expected_value": {"S": result2.expected_value},
                "actual_value": {"S": result2.actual_value},
                "validation_timestamp": {"S": result2.validation_timestamp},
                "metadata": {
                    "M": {
                        "source_info": {
                            "M": {"model": {"S": "validation-v1"}}
                        },
                        "confidence": {"N": "0.92"},
                    }
                },
            }
        ],
        "Count": 1,
        "ScannedCount": 1,
    }

    mock_query.side_effect = [first_response, second_response]

    results, _ = client.list_receipt_validation_results_for_field(
        receipt_id=sample_receipt_validation_result.receipt_id,
        image_id=sample_receipt_validation_result.image_id,
        field_name=sample_receipt_validation_result.field_name,
    )

    assert len(results) == 2
    assert results[0].receipt_id == sample_receipt_validation_result.receipt_id
    assert results[0].result_index == 0
    assert results[1].receipt_id == result2.receipt_id
    assert results[1].result_index == 1

    assert mock_query.call_count == 2

    first_call_args = mock_query.call_args_list[0][1]
    assert first_call_args["TableName"] == dynamodb_table
    assert "#pk = :pk AND begins_with(#sk, :sk_prefix)" in str(
        mock_query.call_args_list[0]
    )

    second_call_args = mock_query.call_args_list[1][1]
    assert second_call_args["TableName"] == dynamodb_table
    assert "#pk = :pk AND begins_with(#sk, :sk_prefix)" in str(
        mock_query.call_args_list[1]
    )
    assert (
        second_call_args["ExclusiveStartKey"]
        == first_response["LastEvaluatedKey"]
    )


@pytest.mark.integration
def test_listReceiptValidationResultsForField_empty_results(
    dynamodb_table: Literal["MyMockedTable"],
    mocker,
):
    """
    Tests that listReceiptValidationResultsForField returns an empty list when no results are found.
    """
    client = DynamoClient(table_name=dynamodb_table)

    mock_query = mocker.patch.object(client._client, "query")
    mock_query.return_value = {
        "Items": [],
        "Count": 0,
        "ScannedCount": 0,
    }

    results, _ = client.list_receipt_validation_results_for_field(
        receipt_id=1,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        field_name="total_amount",
    )

    assert len(results) == 0
    assert isinstance(results, list)


@pytest.mark.integration
@pytest.mark.parametrize(
    "receipt_id,image_id,field_name,expected_error",
    [
        (
            None,
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "field_name",
            "receipt_id cannot be None",
        ),
        (
            "not_an_int",
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "field_name",
            "receipt_id must be an integer",
        ),
        (
            1,
            None,
            "field_name",
            "image_id cannot be None",
        ),
        (1, "invalid-uuid", "field_name", "uuid must be a valid UUIDv4"),
        (
            1,
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            None,
            "field_name cannot be None",
        ),
        (
            1,
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            123,
            "field_name must be a string",
        ),
        (
            1,
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "",
            "field_name must not be empty",
        ),
    ],
)
def test_listReceiptValidationResultsForField_invalid_parameters(
    dynamodb_table: Literal["MyMockedTable"],
    receipt_id: Any,
    image_id: Any,
    field_name: Any,
    expected_error: str,
) -> None:
    """
    Tests that listReceiptValidationResultsForField raises appropriate errors for invalid parameters.
    """
    client = DynamoClient(table_name=dynamodb_table)

    with pytest.raises(EntityValidationError, match=expected_error):
        client.list_receipt_validation_results_for_field(
            receipt_id=receipt_id,
            image_id=image_id,
            field_name=field_name,
        )


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,expected_exception,error_match", ERROR_SCENARIOS
)
def test_listReceiptValidationResultsForField_client_errors(
    dynamodb_table: Literal["MyMockedTable"],
    mocker: MockerFixture,
    error_code: str,
    expected_exception: Type[Exception],
    error_match: str,
) -> None:
    """
    Tests that listReceiptValidationResultsForField handles client errors appropriately.
    """
    client = DynamoClient(table_name=dynamodb_table)

    mock_query = _patch_client_error(
        mocker, client, "query", error_code, "Query"
    )
    with pytest.raises(expected_exception, match=error_match):
        client.list_receipt_validation_results_for_field(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            field_name="total_amount",
        )
    mock_query.assert_called_once()
