from datetime import datetime
from typing import Any, Literal

import pytest
from botocore.exceptions import ClientError
from pytest_mock import MockerFixture
from receipt_dynamo import DynamoClient, ReceiptValidationSummary
from receipt_dynamo.data.shared_exceptions import (
    DynamoDBError,
    DynamoDBServerError,
    DynamoDBThroughputError,
    EntityAlreadyExistsError,
    EntityNotFoundError,
    EntityValidationError,
    OperationError,
)

# This entity is not used in production infrastructure.
pytestmark = [pytest.mark.integration, pytest.mark.unused_in_production]

IMAGE_ID = "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
RECEIPT_ID = 12345
MISSING_ENTITY_ERROR = (
    f"ReceiptValidationSummary with ID {IMAGE_ID}#{RECEIPT_ID} does not exist"
)

COMMON_ERROR_SCENARIOS = [
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

OPERATION_METHODS = {
    "add": "put_item",
    "update": "put_item",
    "delete": "delete_item",
    "get": "get_item",
}

CLIENT_ERROR_SCENARIOS = [
    (operation, method, *scenario)
    for operation, method in OPERATION_METHODS.items()
    for scenario in COMMON_ERROR_SCENARIOS
] + [
    (
        "add",
        "put_item",
        "ConditionalCheckFailedException",
        EntityAlreadyExistsError,
        "already exists",
    ),
    (
        "update",
        "put_item",
        "ConditionalCheckFailedException",
        EntityNotFoundError,
        MISSING_ENTITY_ERROR,
    ),
    (
        "delete",
        "delete_item",
        "ConditionalCheckFailedException",
        EntityNotFoundError,
        MISSING_ENTITY_ERROR,
    ),
]


@pytest.fixture(name="summary")
def _summary() -> ReceiptValidationSummary:
    """Return a representative validation summary with nested data."""
    return ReceiptValidationSummary(
        image_id=IMAGE_ID,
        receipt_id=RECEIPT_ID,
        overall_status="valid",
        overall_reasoning="All fields validated successfully",
        validation_timestamp=datetime.now().isoformat(),
        version="1.0",
        field_summary={
            field: {
                "status": "valid",
                "count": count,
                "has_errors": False,
                "has_warnings": False,
            }
            for field, count in (("merchant", 2), ("date", 1), ("total", 1))
        },
        metadata={
            "test_flags": [True, False],
            "processing_metrics": {
                "processing_time_ms": 1500,
                "api_calls": 3,
            },
            "processing_history": [
                {
                    "event_type": "validation_started",
                    "timestamp": datetime.now().isoformat(),
                    "description": "Started validation process",
                    "model_version": "1.0",
                }
            ],
            "source_information": {
                "model_name": "receipt-validation-v1",
                "model_version": "1.0",
                "algorithm": "rule-based",
                "configuration": "standard",
            },
        },
        timestamp_added=datetime.now(),
    )


@pytest.fixture(name="client")
def _client(
    dynamodb_table: Literal["MyMockedTable"],
) -> DynamoClient:
    return DynamoClient(table_name=dynamodb_table)


def _stored_item(
    client: DynamoClient, summary: ReceiptValidationSummary
) -> dict[str, Any]:
    response = client._client.get_item(  # pylint: disable=protected-access
        TableName=client.table_name,
        Key=summary.key,
    )
    return response.get("Item", {})


def _invoke_operation(
    client: DynamoClient,
    operation: str,
    summary: ReceiptValidationSummary,
) -> None:
    if operation == "get":
        client.get_receipt_validation_summary(
            receipt_id=summary.receipt_id,
            image_id=summary.image_id,
        )
        return

    getattr(client, f"{operation}_receipt_validation_summary")(summary)


def test_add_receipt_validation_summary_success(
    client: DynamoClient,
    summary: ReceiptValidationSummary,
) -> None:
    client.add_receipt_validation_summary(summary)

    item = _stored_item(client, summary)
    assert item["overall_status"] == {"S": "valid"}
    assert item["overall_reasoning"] == {
        "S": "All fields validated successfully"
    }
    assert item["version"] == {"S": "1.0"}
    assert set(item["field_summary"]["M"]) == {"merchant", "date", "total"}
    for field in item["field_summary"]["M"].values():
        assert field["M"]["has_errors"] == {"BOOL": False}
        assert field["M"]["has_warnings"] == {"BOOL": False}
    assert item["metadata"]["M"]["test_flags"] == {
        "L": [{"BOOL": True}, {"BOOL": False}]
    }


def test_add_receipt_validation_summary_duplicate_raises(
    client: DynamoClient,
    summary: ReceiptValidationSummary,
) -> None:
    client.add_receipt_validation_summary(summary)

    with pytest.raises(EntityAlreadyExistsError, match="already exists"):
        client.add_receipt_validation_summary(summary)


@pytest.mark.parametrize("operation", ["add", "update"])
@pytest.mark.parametrize(
    "invalid_summary,error_match",
    [
        (None, "summary cannot be None"),
        (
            "not-a-validation-summary",
            "summary must be an instance of ReceiptValidationSummary",
        ),
    ],
)
def test_write_receipt_validation_summary_rejects_invalid_entity(
    client: DynamoClient,
    summary: ReceiptValidationSummary,
    operation: str,
    invalid_summary: Any,
    error_match: str,
) -> None:
    with pytest.raises(OperationError, match=error_match):
        getattr(client, f"{operation}_receipt_validation_summary")(
            invalid_summary
        )


@pytest.mark.parametrize(
    "operation,client_method,error_code,expected_exception,error_match",
    CLIENT_ERROR_SCENARIOS,
)
def test_receipt_validation_summary_client_errors(
    client: DynamoClient,
    summary: ReceiptValidationSummary,
    mocker: MockerFixture,
    operation: str,
    client_method: str,
    error_code: str,
    expected_exception: type[Exception],
    error_match: str,
) -> None:
    mocked_method = mocker.patch.object(
        client._client,  # pylint: disable=protected-access
        client_method,
        side_effect=ClientError(
            {"Error": {"Code": error_code, "Message": f"Mocked {error_code}"}},
            client_method,
        ),
    )

    with pytest.raises(expected_exception, match=error_match):
        _invoke_operation(client, operation, summary)

    mocked_method.assert_called_once()


def test_update_receipt_validation_summary_success(
    client: DynamoClient,
    summary: ReceiptValidationSummary,
) -> None:
    client.add_receipt_validation_summary(summary)
    summary.overall_status = "COMPLETED"
    summary.overall_reasoning = "Some validation errors were found"
    summary.field_summary["total"].update(
        {
            "status": "invalid",
            "has_errors": True,
            "total_fields": 10,
            "fields_with_errors": 2,
            "error_rate": 0.2,
        }
    )
    summary.metadata = {"test": "value"}

    client.update_receipt_validation_summary(summary)

    item = _stored_item(client, summary)
    total = item["field_summary"]["M"]["total"]["M"]
    assert item["overall_status"] == {"S": "COMPLETED"}
    assert item["overall_reasoning"] == {
        "S": "Some validation errors were found"
    }
    assert total["status"] == {"S": "invalid"}
    assert total["has_errors"] == {"BOOL": True}
    assert total["total_fields"] == {"N": "10"}
    assert total["fields_with_errors"] == {"N": "2"}
    assert total["error_rate"] == {"N": "0.2"}
    assert item["metadata"]["M"]["test"] == {"S": "value"}


def test_update_receipt_validation_summary_missing_raises(
    client: DynamoClient,
    summary: ReceiptValidationSummary,
) -> None:
    with pytest.raises(EntityNotFoundError, match=MISSING_ENTITY_ERROR):
        client.update_receipt_validation_summary(summary)


def test_delete_receipt_validation_summary_success(
    client: DynamoClient,
    summary: ReceiptValidationSummary,
) -> None:
    client.add_receipt_validation_summary(summary)
    assert _stored_item(client, summary)

    client.delete_receipt_validation_summary(summary)

    assert not _stored_item(client, summary)


def test_delete_receipt_validation_summary_missing_raises(
    client: DynamoClient,
    summary: ReceiptValidationSummary,
) -> None:
    with pytest.raises(EntityNotFoundError, match=MISSING_ENTITY_ERROR):
        client.delete_receipt_validation_summary(summary)


@pytest.mark.parametrize(
    "receipt_id,image_id,error_match",
    [
        (None, IMAGE_ID, "receipt_id must be an integer"),
        ("not_an_int", IMAGE_ID, "receipt_id must be an integer"),
        (12345, None, "uuid must be a string"),
        (12345, "invalid-uuid", "uuid must be a valid UUIDv4"),
    ],
)
def test_receipt_validation_summary_rejects_invalid_identity(
    receipt_id: Any,
    image_id: Any,
    error_match: str,
) -> None:
    with pytest.raises(ValueError, match=error_match):
        ReceiptValidationSummary(
            receipt_id=receipt_id,
            image_id=image_id,
            overall_status="VALID",
            overall_reasoning="Test reasoning",
            field_summary={},
            validation_timestamp="2023-01-01T00:00:00",
        )


def test_get_receipt_validation_summary_success(
    client: DynamoClient,
    summary: ReceiptValidationSummary,
) -> None:
    client.add_receipt_validation_summary(summary)

    result = client.get_receipt_validation_summary(
        receipt_id=summary.receipt_id,
        image_id=summary.image_id,
    )

    assert result == summary
    assert result.field_summary["merchant"]["status"] == "valid"
    assert set(result.metadata) >= {
        "processing_metrics",
        "processing_history",
        "source_information",
    }


def test_get_receipt_validation_summary_missing_raises(
    client: DynamoClient,
    summary: ReceiptValidationSummary,
) -> None:
    expected = (
        f"ReceiptValidationSummary for receipt {summary.receipt_id} and "
        f"image {summary.image_id} does not exist"
    )
    with pytest.raises(EntityNotFoundError, match=expected):
        client.get_receipt_validation_summary(
            receipt_id=summary.receipt_id,
            image_id=summary.image_id,
        )


@pytest.mark.parametrize(
    "receipt_id,image_id,error_match",
    [
        (None, IMAGE_ID, "receipt_id must be an integer, got NoneType"),
        ("not_an_int", IMAGE_ID, "receipt_id must be an integer"),
        (12345, None, "image_id must be a string, got NoneType"),
        (
            12345,
            "invalid-uuid",
            "Invalid image_id format: uuid must be a valid UUIDv4",
        ),
    ],
)
def test_get_receipt_validation_summary_rejects_invalid_identity(
    client: DynamoClient,
    receipt_id: Any,
    image_id: Any,
    error_match: str,
) -> None:
    with pytest.raises(EntityValidationError, match=error_match):
        client.get_receipt_validation_summary(
            receipt_id=receipt_id,
            image_id=image_id,
        )
