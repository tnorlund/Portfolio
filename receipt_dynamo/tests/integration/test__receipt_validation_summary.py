from datetime import datetime
from typing import Any, Dict, Literal, Type

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

# This entity is not used in production infrastructure
pytestmark = [
    pytest.mark.integration,
    pytest.mark.unused_in_production
]

# =============================================================================
# TEST DATA AND FIXTURES
# =============================================================================

CORRECT_RECEIPT_VALIDATION_SUMMARY_PARAMS: Dict[str, Any] = {
    "image_id": "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
    "receipt_id": 12345,
    "overall_status": "valid",
    "overall_reasoning": "All fields validated successfully",
    "validation_timestamp": datetime.now().isoformat(),
    "version": "1.0",
    "field_summary": {
        "merchant": {
            "status": "valid",
            "count": 2,
            "has_errors": False,
            "has_warnings": False,
        },
        "date": {
            "status": "valid",
            "count": 1,
            "has_errors": False,
            "has_warnings": False,
        },
        "total": {
            "status": "valid",
            "count": 1,
            "has_errors": False,
            "has_warnings": False,
        },
    },
    "metadata": {
        "processing_metrics": {"processing_time_ms": 1500, "api_calls": 3},
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
    "timestamp_added": datetime.now(),
}


@pytest.fixture(name="sample_receipt_validation_summary")
def _sample_receipt_validation_summary() -> ReceiptValidationSummary:
    """Provides a valid ReceiptValidationSummary for testing."""
    return ReceiptValidationSummary(**CORRECT_RECEIPT_VALIDATION_SUMMARY_PARAMS)


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
        "receipt_validation_summary already exists",
    ),
] + ERROR_SCENARIOS

# Additional error for update operations
UPDATE_ERROR_SCENARIOS = [
    (
        "ConditionalCheckFailedException",
        EntityNotFoundError,
        "not found during update_receipt_validation_summary",
    ),
] + ERROR_SCENARIOS

# -------------------------------------------------------------------
#                   PARAMETERIZED VALIDATION ERROR TESTS
# -------------------------------------------------------------------

ADD_VALIDATION_SCENARIOS = [
    (None, "summary cannot be None"),
    (
        "not-a-validation-summary",
        "summary must be an instance of ReceiptValidationSummary",
    ),
]

UPDATE_VALIDATION_SCENARIOS = [
    (None, "summary cannot be None"),
    (
        "not a ReceiptValidationSummary",
        "summary must be an instance of ReceiptValidationSummary",
    ),
]


@pytest.mark.integration
def test_addReceiptValidationSummary_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_validation_summary: ReceiptValidationSummary,
):
    """Test adding a ReceiptValidationSummary to DynamoDB successfully."""
    # Create a DynamoDB client with the table name from the fixture
    client = DynamoClient(table_name=dynamodb_table)

    # Add the sample validation summary to the table
    client.add_receipt_validation_summary(sample_receipt_validation_summary)

    # Try to retrieve the validation summary to verify it was added correctly
    response = client._client.get_item(
        TableName=dynamodb_table,
        Key={
            "PK": {"S": f"IMAGE#{sample_receipt_validation_summary.image_id}"},
            "SK": {
                "S": f"RECEIPT#{sample_receipt_validation_summary.receipt_id}#ANALYSIS#VALIDATION"
            },
        },
    )

    # Make assertions based on the response
    assert "Item" in response
    assert response["Item"]["overall_status"]["S"] == "valid"
    assert (
        response["Item"]["overall_reasoning"]["S"]
        == "All fields validated successfully"
    )
    assert response["Item"]["version"]["S"] == "1.0"

    # Verify field_summary is correctly saved
    assert "field_summary" in response["Item"]
    assert "merchant" in response["Item"]["field_summary"]["M"]
    assert "date" in response["Item"]["field_summary"]["M"]
    assert "total" in response["Item"]["field_summary"]["M"]


@pytest.mark.integration
def test_addReceiptValidationSummary_duplicate_raises(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_validation_summary: ReceiptValidationSummary,
):
    """Test adding a duplicate ReceiptValidationSummary raises an error."""
    # Create a DynamoDB client with the table name from the fixture
    client = DynamoClient(table_name=dynamodb_table)

    # Add the validation summary for the first time
    client.add_receipt_validation_summary(sample_receipt_validation_summary)

    # Attempt to add the same validation summary again, which should raise an error
    with pytest.raises(EntityAlreadyExistsError) as excinfo:
        client.add_receipt_validation_summary(
            sample_receipt_validation_summary
        )

    # Check that the error message contains useful information
    assert "already exists" in str(excinfo.value)


@pytest.mark.integration
@pytest.mark.parametrize("invalid_input,error_match", ADD_VALIDATION_SCENARIOS)
def test_addReceiptValidationSummary_invalid_parameters(
    dynamodb_table: Literal["MyMockedTable"],
    invalid_input: Any,
    error_match: str,
) -> None:
    """Tests that add_receipt_validation_summary raises appropriate error for
    invalid inputs."""
    client = DynamoClient(dynamodb_table)
    with pytest.raises(OperationError, match=error_match):
        client.add_receipt_validation_summary(invalid_input)  # type: ignore


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,expected_exception,error_match", ADD_ERROR_SCENARIOS
)
def test_addReceiptValidationSummary_client_errors(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_validation_summary: ReceiptValidationSummary,
    mocker: MockerFixture,
    error_code: str,
    expected_exception: Type[Exception],
    error_match: str,
) -> None:
    """Tests that add_receipt_validation_summary raises appropriate exceptions for various
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
        client.add_receipt_validation_summary(sample_receipt_validation_summary)
    mock_put.assert_called_once()


@pytest.mark.integration
def test_updateReceiptValidationSummary_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_validation_summary: ReceiptValidationSummary,
):
    """Test updating a ReceiptValidationSummary in DynamoDB successfully."""
    # Create a DynamoDB client with the table name from the fixture
    client = DynamoClient(table_name=dynamodb_table)

    # First, add the sample validation summary to the table
    client.add_receipt_validation_summary(sample_receipt_validation_summary)

    # Now, modify the validation summary
    updated_summary = sample_receipt_validation_summary
    updated_summary.overall_status = "COMPLETED"
    updated_summary.overall_reasoning = "Some validation errors were found"
    updated_summary.field_summary["total"]["status"] = "invalid"
    updated_summary.field_summary["total"]["has_errors"] = True
    updated_summary.field_summary["total"]["total_fields"] = 10
    updated_summary.field_summary["total"]["fields_with_errors"] = 2
    updated_summary.field_summary["total"]["error_rate"] = 0.2
    updated_summary.metadata = {"test": "value"}

    # Update the validation summary in the table
    client.update_receipt_validation_summary(updated_summary)

    # Get the updated summary from the table - use the correct key format
    response = client._client.get_item(
        TableName=dynamodb_table,
        Key={
            "PK": {"S": f"IMAGE#{updated_summary.image_id}"},
            "SK": {
                "S": f"RECEIPT#{updated_summary.receipt_id}#ANALYSIS#VALIDATION"
            },
        },
    )

    # Print the response structure to debug
    print(f"Response: {response}")
    if "Item" in response:
        print(f"Field summary: {response['Item'].get('field_summary')}")
        if (
            "field_summary" in response["Item"]
            and "M" in response["Item"]["field_summary"]
        ):
            print(
                f"Total field: {response['Item']['field_summary']['M'].get('total')}"
            )
            if (
                "total" in response["Item"]["field_summary"]["M"]
                and "M" in response["Item"]["field_summary"]["M"]["total"]
            ):
                print(
                    f"has_errors field: {response['Item']['field_summary']['M']['total']['M'].get('has_errors')}"
                )

    # Verify the updated summary
    assert response["Item"]["overall_status"]["S"] == "COMPLETED"
    assert (
        response["Item"]["overall_reasoning"]["S"]
        == "Some validation errors were found"
    )
    assert (
        response["Item"]["field_summary"]["M"]["total"]["M"]["status"]["S"]
        == "invalid"
    )
    # Check how has_errors is stored - it's stored as a string in the N field, not as a BOOL
    assert (
        response["Item"]["field_summary"]["M"]["total"]["M"]["has_errors"]["N"]
        == "True"
    )
    assert (
        response["Item"]["field_summary"]["M"]["total"]["M"]["total_fields"][
            "N"
        ]
        == "10"
    )
    assert (
        response["Item"]["field_summary"]["M"]["total"]["M"][
            "fields_with_errors"
        ]["N"]
        == "2"
    )
    assert (
        response["Item"]["field_summary"]["M"]["total"]["M"]["error_rate"]["N"]
        == "0.2"
    )
    assert response["Item"]["metadata"]["M"]["test"]["S"] == "value"


@pytest.mark.integration
def test_updateReceiptValidationSummary_not_exists_raises(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_validation_summary: ReceiptValidationSummary,
):
    """Test updating a non-existent ReceiptValidationSummary raises an error."""
    # Create a DynamoDB client with the table name from the fixture
    client = DynamoClient(table_name=dynamodb_table)

    # Attempt to update a validation summary that wasn't previously added
    with pytest.raises(EntityNotFoundError) as excinfo:
        client.update_receipt_validation_summary(
            sample_receipt_validation_summary
        )

    # Check that the error message contains useful information
    assert "receiptvalidationsummary not found during update_receipt_validation_summary" in str(excinfo.value)


@pytest.mark.integration
@pytest.mark.parametrize("invalid_input,error_match", UPDATE_VALIDATION_SCENARIOS)
def test_updateReceiptValidationSummary_invalid_parameters(
    dynamodb_table: Literal["MyMockedTable"],
    invalid_input: Any,
    error_match: str,
) -> None:
    """Tests that update_receipt_validation_summary raises appropriate error for
    invalid inputs."""
    client = DynamoClient(dynamodb_table)
    with pytest.raises(OperationError, match=error_match):
        client.update_receipt_validation_summary(invalid_input)  # type: ignore


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,expected_exception,error_match", UPDATE_ERROR_SCENARIOS
)
def test_updateReceiptValidationSummary_client_errors(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_validation_summary: ReceiptValidationSummary,
    mocker: MockerFixture,
    error_code: str,
    expected_exception: Type[Exception],
    error_match: str,
) -> None:
    """Tests that update_receipt_validation_summary raises appropriate exceptions for various
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
        client.update_receipt_validation_summary(sample_receipt_validation_summary)
    mock_put.assert_called_once()


@pytest.mark.integration
def test_deleteReceiptValidationSummary_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_validation_summary: ReceiptValidationSummary,
):
    """Test deleting a ReceiptValidationSummary from DynamoDB successfully."""
    # Create a DynamoDB client with the table name from the fixture
    client = DynamoClient(table_name=dynamodb_table)

    # First, add the sample validation summary to the table
    client.add_receipt_validation_summary(sample_receipt_validation_summary)

    # Verify it was added
    response = client._client.get_item(
        TableName=dynamodb_table,
        Key={
            "PK": {"S": f"IMAGE#{sample_receipt_validation_summary.image_id}"},
            "SK": {
                "S": f"RECEIPT#{sample_receipt_validation_summary.receipt_id}#ANALYSIS#VALIDATION"
            },
        },
    )
    assert "Item" in response

    # Now delete the validation summary
    client.delete_receipt_validation_summary(sample_receipt_validation_summary)

    # Verify it was deleted
    response = client._client.get_item(
        TableName=dynamodb_table,
        Key={
            "PK": {"S": f"IMAGE#{sample_receipt_validation_summary.image_id}"},
            "SK": {
                "S": f"RECEIPT#{sample_receipt_validation_summary.receipt_id}#ANALYSIS#VALIDATION"
            },
        },
    )
    assert "Item" not in response


@pytest.mark.integration
def test_deleteReceiptValidationSummary_not_exists_succeeds(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_validation_summary: ReceiptValidationSummary,
):
    """Test deleting a non-existent ReceiptValidationSummary succeeds (idempotent)."""
    # Create a DynamoDB client with the table name from the fixture
    client = DynamoClient(table_name=dynamodb_table)

    # Attempt to delete a validation summary that wasn't previously added
    # This should succeed without error (idempotent delete)
    client.delete_receipt_validation_summary(sample_receipt_validation_summary)

    # Verify we can still try to get it and it doesn't exist
    with pytest.raises(EntityNotFoundError) as excinfo:
        client.get_receipt_validation_summary(
            receipt_id=sample_receipt_validation_summary.receipt_id,
            image_id=sample_receipt_validation_summary.image_id,
        )
    assert "ReceiptValidationSummary for receipt" in str(excinfo.value)
    assert "does not exist" in str(excinfo.value)


@pytest.mark.integration
@pytest.mark.parametrize(
    "receipt_id,image_id,expected_error",
    [
        (
            None,
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "receipt_id must be an integer",
        ),
        (
            "not_an_int",
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "receipt_id must be an integer",
        ),
        (12345, None, "uuid must be a string"),
        (12345, "invalid-uuid", "uuid must be a valid UUIDv4"),
    ],
)
def test_deleteReceiptValidationSummary_invalid_parameters(
    dynamodb_table,
    receipt_id,
    image_id,
    expected_error,
):
    """Test deleting a ReceiptValidationSummary with invalid parameters."""
    # Create a client with the mocked table
    client = DynamoClient(table_name=dynamodb_table)

    # Try to delete with invalid input
    with pytest.raises(ValueError) as excinfo:
        # Create a summary object with the test parameters
        try:
            summary = ReceiptValidationSummary(
                receipt_id=receipt_id,
                image_id=image_id,
                overall_status="VALID",
                overall_reasoning="Test reasoning",
                field_summary={},
                validation_timestamp="2023-01-01T00:00:00",
            )
            # If we get here without error, pass the summary to the delete method
            client.delete_receipt_validation_summary(summary)
        except ValueError as e:
            # Re-raise the ValueError to be caught by pytest.raises
            raise ValueError(str(e))

    # Verify the error message
    assert expected_error in str(excinfo.value)


# Additional error for delete operations
DELETE_ERROR_SCENARIOS = [
    (
        "ConditionalCheckFailedException",
        EntityNotFoundError,
        "receiptvalidationsummary not found during delete_receipt_validation_summary",
    ),
] + ERROR_SCENARIOS


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,expected_exception,error_match", DELETE_ERROR_SCENARIOS
)
def test_deleteReceiptValidationSummary_client_errors(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_validation_summary: ReceiptValidationSummary,
    mocker: MockerFixture,
    error_code: str,
    expected_exception: Type[Exception],
    error_match: str,
) -> None:
    """Tests that delete_receipt_validation_summary raises appropriate exceptions for various
    ClientError scenarios."""
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
        client.delete_receipt_validation_summary(sample_receipt_validation_summary)
    mock_delete.assert_called_once()


@pytest.mark.integration
def test_getReceiptValidationSummary_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_validation_summary: ReceiptValidationSummary,
):
    """Test retrieving a ReceiptValidationSummary from DynamoDB successfully."""
    # Create a DynamoDB client with the table name from the fixture
    client = DynamoClient(table_name=dynamodb_table)

    # First, add the sample validation summary to the table
    client.add_receipt_validation_summary(sample_receipt_validation_summary)

    # Now retrieve the validation summary
    result = client.get_receipt_validation_summary(
        receipt_id=sample_receipt_validation_summary.receipt_id,
        image_id=sample_receipt_validation_summary.image_id,
    )

    # Verify the result matches the original validation summary
    assert isinstance(result, ReceiptValidationSummary)
    assert result.receipt_id == sample_receipt_validation_summary.receipt_id
    assert result.image_id == sample_receipt_validation_summary.image_id
    assert (
        result.overall_status
        == sample_receipt_validation_summary.overall_status
    )
    assert (
        result.overall_reasoning
        == sample_receipt_validation_summary.overall_reasoning
    )
    assert result.version == sample_receipt_validation_summary.version

    # Verify the field summary data
    assert "merchant" in result.field_summary
    assert "date" in result.field_summary
    assert "total" in result.field_summary
    assert result.field_summary["merchant"]["status"] == "valid"

    # Verify metadata
    assert "processing_metrics" in result.metadata
    assert "processing_history" in result.metadata
    assert "source_information" in result.metadata


@pytest.mark.integration
def test_getReceiptValidationSummary_not_exists_raises_error(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_validation_summary: ReceiptValidationSummary,
):
    """Test retrieving a non-existent ReceiptValidationSummary raises EntityNotFoundError."""
    # Create a DynamoDB client with the table name from the fixture
    client = DynamoClient(table_name=dynamodb_table)

    # Attempt to retrieve a validation summary that wasn't previously added
    with pytest.raises(EntityNotFoundError) as excinfo:
        client.get_receipt_validation_summary(
            receipt_id=sample_receipt_validation_summary.receipt_id,
            image_id=sample_receipt_validation_summary.image_id,
        )

    # Verify the error message
    receipt_id = sample_receipt_validation_summary.receipt_id
    image_id = sample_receipt_validation_summary.image_id
    assert (
        f"ReceiptValidationSummary for receipt {receipt_id} and image {image_id} does not exist"
        in str(excinfo.value)
    )


@pytest.mark.integration
@pytest.mark.parametrize(
    "receipt_id,image_id,expected_error",
    [
        (
            None,
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "receipt_id must be an integer, got NoneType",
        ),
        (
            "not_an_int",
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "receipt_id must be an integer",
        ),
        (12345, None, "image_id must be a string, got NoneType"),
        (
            12345,
            "invalid-uuid",
            "Invalid image_id format: uuid must be a valid UUIDv4",
        ),
    ],
)
def test_getReceiptValidationSummary_invalid_parameters(
    dynamodb_table,
    receipt_id,
    image_id,
    expected_error,
):
    """Test retrieving a ReceiptValidationSummary with invalid parameters."""
    # Create a client with the mocked table
    client = DynamoClient(table_name=dynamodb_table)

    # Try to retrieve with invalid input
    with pytest.raises(EntityValidationError) as excinfo:
        client.get_receipt_validation_summary(
            receipt_id=receipt_id, image_id=image_id
        )

    # Verify the error message
    assert expected_error in str(excinfo.value)


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,expected_exception,error_match", ERROR_SCENARIOS
)
def test_getReceiptValidationSummary_client_errors(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_validation_summary: ReceiptValidationSummary,
    mocker: MockerFixture,
    error_code: str,
    expected_exception: Type[Exception],
    error_match: str,
) -> None:
    """Tests that get_receipt_validation_summary raises appropriate exceptions for various
    ClientError scenarios."""
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
        client.get_receipt_validation_summary(
            receipt_id=sample_receipt_validation_summary.receipt_id,
            image_id=sample_receipt_validation_summary.image_id,
        )
    mock_get.assert_called_once()
