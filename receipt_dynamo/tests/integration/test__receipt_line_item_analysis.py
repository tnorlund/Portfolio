import json
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Literal, Optional, Type, Union

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws
from pytest_mock import MockerFixture

from receipt_dynamo import DynamoClient, ReceiptLineItemAnalysis
from receipt_dynamo.data.shared_exceptions import (
    DynamoDBAccessError,
    DynamoDBError,
    DynamoDBServerError,
    DynamoDBThroughputError,
    DynamoDBValidationError,
    EntityAlreadyExistsError,
    EntityNotFoundError,
)

# This entity is not used in production infrastructure
pytestmark = [
    pytest.mark.integration,
    pytest.mark.unused_in_production
]


@pytest.fixture
def sample_receipt_line_item_analysis():
    """Create a sample ReceiptLineItemAnalysis for testing."""
    return ReceiptLineItemAnalysis(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        receipt_id=1,
        timestamp_added=datetime(2021, 1, 1),
        items=[
            {
                "description": "Test Item 1",
                "quantity": {"amount": Decimal("1"), "unit": "each"},
                "price": {
                    "unit_price": Decimal("10.99"),
                    "extended_price": Decimal("10.99"),
                },
                "reasoning": "This is a test item",
                "line_ids": [1, 2],
            },
            {
                "description": "Test Item 2",
                "quantity": {"amount": Decimal("2"), "unit": "each"},
                "price": {
                    "unit_price": Decimal("5.99"),
                    "extended_price": Decimal("11.98"),
                },
                "reasoning": "This is another test item",
                "line_ids": [3, 4],
            },
        ],
        reasoning="This is a test analysis",
        subtotal=Decimal("22.97"),
        tax=Decimal("2.30"),
        total=Decimal("25.27"),
        version="1.0",
    )


@pytest.mark.integration
def test_addReceiptLineItemAnalysis_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_line_item_analysis: ReceiptLineItemAnalysis,
):
    """Test successful addition of a ReceiptLineItemAnalysis to DynamoDB."""
    # Arrange
    client = DynamoClient(table_name=dynamodb_table)

    # Act
    client.add_receipt_line_item_analysis(sample_receipt_line_item_analysis)

    # Assert
    # Verify the item was added by retrieving it
    response = boto3.client("dynamodb", region_name="us-east-1").get_item(
        TableName=dynamodb_table,
        Key={
            "PK": {"S": f"IMAGE#{sample_receipt_line_item_analysis.image_id}"},
            "SK": {
                "S": f"RECEIPT#{sample_receipt_line_item_analysis.receipt_id:05d}#ANALYSIS#LINE_ITEMS"
            },
        },
    )
    assert "Item" in response
    assert (
        response["Item"]["PK"]["S"]
        == f"IMAGE#{sample_receipt_line_item_analysis.image_id}"
    )
    assert (
        response["Item"]["SK"]["S"]
        == f"RECEIPT#{sample_receipt_line_item_analysis.receipt_id:05d}#ANALYSIS#LINE_ITEMS"
    )
    assert response["Item"]["TYPE"]["S"] == "RECEIPT_LINE_ITEM_ANALYSIS"
    assert (
        response["Item"]["version"]["S"]
        == sample_receipt_line_item_analysis.version
    )


@pytest.mark.integration
def test_addReceiptLineItemAnalysis_duplicate_raises(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_line_item_analysis: ReceiptLineItemAnalysis,
):
    """Test that adding a duplicate ReceiptLineItemAnalysis raises an error."""
    # Arrange
    client = DynamoClient(table_name=dynamodb_table)
    client.add_receipt_line_item_analysis(sample_receipt_line_item_analysis)

    # Act & Assert
    with pytest.raises(EntityAlreadyExistsError) as excinfo:
        client.add_receipt_line_item_analysis(
            sample_receipt_line_item_analysis
        )
    assert "already exists" in str(excinfo.value)


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (None, "analysis cannot be None"),
        (
            "not-a-receipt-line-item-analysis",
            "analysis must be an instance of the ReceiptLineItemAnalysis class.",
        ),
    ],
)
def test_addReceiptLineItemAnalysis_invalid_parameters(
    dynamodb_table,
    sample_receipt_line_item_analysis,
    mocker,
    invalid_input,
    expected_error,
):
    """Test that invalid parameters raise appropriate errors."""
    # Arrange
    client = DynamoClient(table_name=dynamodb_table)

    # Mock boto3 client to prevent actual calls for invalid inputs
    mocker.patch.object(client, "_client")

    # Act & Assert
    with pytest.raises(ValueError) as excinfo:
        client.add_receipt_line_item_analysis(invalid_input)
    assert expected_error in str(excinfo.value)


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_exception",
    [
        (
            "ConditionalCheckFailedException",
            "Item already exists",
            "already exists",
        ),
        (
            "ResourceNotFoundException",
            "Table not found",
            "Table not found for operation add_receipt_line_item_analysis",
        ),
        (
            "ProvisionedThroughputExceededException",
            "Provisioned throughput exceeded",
            "Provisioned throughput exceeded",
        ),
        (
            "InternalServerError",
            "Internal server error",
            "Internal server error",
        ),
        (
            "UnknownError",
            "Unknown error",
            "Could not add receipt line item analysis to DynamoDB",
        ),
        (
            "ValidationException",
            "One or more parameters given were invalid",
            "One or more parameters given were invalid",
        ),
        ("AccessDeniedException", "Access denied", "Access denied"),
    ],
)
def test_addReceiptLineItemAnalysis_client_errors(
    dynamodb_table,
    sample_receipt_line_item_analysis,
    mocker,
    error_code,
    error_message,
    expected_exception,
):
    """Test handling of various client errors when adding a ReceiptLineItemAnalysis."""
    # Arrange
    client = DynamoClient(table_name=dynamodb_table)

    # Mock boto3 client to simulate the error
    mock_client = mocker.patch.object(client, "_client")
    error_response = {"Error": {"Code": error_code, "Message": error_message}}
    mock_client.put_item.side_effect = ClientError(error_response, "PutItem")

    # Act & Assert
    if error_code == "ConditionalCheckFailedException":
        with pytest.raises(EntityAlreadyExistsError) as excinfo:
            client.add_receipt_line_item_analysis(
                sample_receipt_line_item_analysis
            )
        assert expected_exception in str(excinfo.value)
    else:
        with pytest.raises(Exception) as excinfo:
            client.add_receipt_line_item_analysis(
                sample_receipt_line_item_analysis
            )
        assert expected_exception in str(excinfo.value)


@pytest.mark.integration
def test_addReceiptLineItemAnalyses_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_line_item_analysis: ReceiptLineItemAnalysis,
):
    """Test successful addition of multiple ReceiptLineItemAnalyses to DynamoDB."""
    # Arrange
    client = DynamoClient(table_name=dynamodb_table)

    # Create a second analysis with a different receipt_id
    second_analysis = ReceiptLineItemAnalysis(
        image_id=sample_receipt_line_item_analysis.image_id,
        receipt_id=2,
        timestamp_added=sample_receipt_line_item_analysis.timestamp_added,
        items=sample_receipt_line_item_analysis.items,
        reasoning=sample_receipt_line_item_analysis.reasoning,
        version=sample_receipt_line_item_analysis.version,
        subtotal=sample_receipt_line_item_analysis.subtotal,
        tax=sample_receipt_line_item_analysis.tax,
        total=sample_receipt_line_item_analysis.total,
    )

    analyses = [sample_receipt_line_item_analysis, second_analysis]

    # Act
    client.add_receipt_line_item_analyses(analyses)

    # Assert
    # Verify the items were added by retrieving them
    for analysis in analyses:
        response = boto3.client("dynamodb", region_name="us-east-1").get_item(
            TableName=dynamodb_table,
            Key={
                "PK": {"S": f"IMAGE#{analysis.image_id}"},
                "SK": {
                    "S": f"RECEIPT#{analysis.receipt_id:05d}#ANALYSIS#LINE_ITEMS"
                },
            },
        )
        assert "Item" in response


@pytest.mark.integration
def test_addReceiptLineItemAnalyses_with_large_batch(
    dynamodb_table, sample_receipt_line_item_analysis
):
    """Test addition of a large batch of ReceiptLineItemAnalyses (requiring multiple batches)."""
    # Arrange
    client = DynamoClient(table_name=dynamodb_table)

    # Create 30 analyses (more than the batch size of 25)
    analyses = []
    for i in range(1, 31):
        analysis = ReceiptLineItemAnalysis(
            image_id=sample_receipt_line_item_analysis.image_id,
            receipt_id=i,
            timestamp_added=sample_receipt_line_item_analysis.timestamp_added,
            items=sample_receipt_line_item_analysis.items,
            reasoning=sample_receipt_line_item_analysis.reasoning,
            version=sample_receipt_line_item_analysis.version,
            subtotal=sample_receipt_line_item_analysis.subtotal,
            tax=sample_receipt_line_item_analysis.tax,
            total=sample_receipt_line_item_analysis.total,
        )
        analyses.append(analysis)

    # Act
    client.add_receipt_line_item_analyses(analyses)

    # Assert
    # Verify the first, middle, and last items were added
    for receipt_id in [1, 15, 30]:
        response = boto3.client("dynamodb", region_name="us-east-1").get_item(
            TableName=dynamodb_table,
            Key={
                "PK": {
                    "S": f"IMAGE#{sample_receipt_line_item_analysis.image_id}"
                },
                "SK": {"S": f"RECEIPT#{receipt_id:05d}#ANALYSIS#LINE_ITEMS"},
            },
        )
        assert "Item" in response


@pytest.mark.integration
def test_addReceiptLineItemAnalyses_with_unprocessed_items_retries(
    dynamodb_table, sample_receipt_line_item_analysis, mocker
):
    """Test that unprocessed items are retried when adding multiple ReceiptLineItemAnalyses."""
    # Arrange
    client = DynamoClient(table_name=dynamodb_table)

    # Create mock responses for batch_write_item
    mock_client = mocker.patch.object(client, "_client")

    # First call returns unprocessed items
    first_response = {
        "UnprocessedItems": {
            dynamodb_table: [
                {
                    "PutRequest": {
                        "Item": {
                            "PK": {"S": "IMAGE#test"},
                            "SK": {"S": "RECEIPT#00001#ANALYSIS#LINE_ITEMS"},
                        }
                    }
                }
            ]
        }
    }

    # Second call returns empty unprocessed items
    second_response = {"UnprocessedItems": {}}

    mock_client.batch_write_item.side_effect = [
        first_response,
        second_response,
    ]

    # Act
    client.add_receipt_line_item_analyses([sample_receipt_line_item_analysis])

    # Assert
    # Should call batch_write_item twice: once for the initial request,
    # once for the retry with unprocessed items
    assert mock_client.batch_write_item.call_count == 2


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (None, "analyses cannot be None"),
        (
            "not-a-list",
            "analyses must be a list of ReceiptLineItemAnalysis instances.",
        ),
        (
            [123, "not-a-receipt-line-item-analysis"],
            "analyses must be a list of ReceiptLineItemAnalysis instances.",
        ),
    ],
)
def test_addReceiptLineItemAnalyses_invalid_parameters(
    dynamodb_table,
    sample_receipt_line_item_analysis,
    mocker,
    invalid_input,
    expected_error,
):
    """Test that invalid parameters raise appropriate errors."""
    # Arrange
    client = DynamoClient(table_name=dynamodb_table)

    # Mock boto3 client to prevent actual calls for invalid inputs
    mocker.patch.object(client, "_client")

    # Act & Assert
    with pytest.raises(ValueError) as excinfo:
        client.add_receipt_line_item_analyses(invalid_input)
    assert expected_error in str(excinfo.value)


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_error_message",
    [
        (
            "ResourceNotFoundException",
            "Table not found",
            "Table not found for operation add_receipt_line_item_analyses",
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
            "One or more parameters given were invalid",
            "One or more parameters given were invalid",
        ),
        (
            "AccessDeniedException",
            "Access denied",
            "Access denied",
        ),
        (
            "Exception",
            "Unknown error occurred",
            "Could not add receipt line item analyses to DynamoDB",
        ),
    ],
)
def test_addReceiptLineItemAnalyses_client_errors(
    dynamodb_table,
    sample_receipt_line_item_analysis,
    mocker,
    error_code,
    error_message,
    expected_error_message,
):
    """Test handling of various client errors when adding multiple ReceiptLineItemAnalyses."""
    # Arrange
    client = DynamoClient(table_name=dynamodb_table)

    # Mock boto3 client to simulate the error
    mock_client = mocker.patch.object(client, "_client")
    error_response = {"Error": {"Code": error_code, "Message": error_message}}
    mock_client.batch_write_item.side_effect = ClientError(
        error_response, "BatchWriteItem"
    )

    # Act & Assert
    with pytest.raises(Exception) as excinfo:
        client.add_receipt_line_item_analyses(
            [sample_receipt_line_item_analysis]
        )
    assert expected_error_message in str(excinfo.value)


@pytest.mark.integration
def test_updateReceiptLineItemAnalysis_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_line_item_analysis: ReceiptLineItemAnalysis,
):
    """Test successful update of a ReceiptLineItemAnalysis in DynamoDB."""
    # Arrange
    client = DynamoClient(table_name=dynamodb_table)

    # First add the item to be updated
    client.add_receipt_line_item_analysis(sample_receipt_line_item_analysis)

    # Modify the item
    updated_analysis = ReceiptLineItemAnalysis(
        image_id=sample_receipt_line_item_analysis.image_id,
        receipt_id=sample_receipt_line_item_analysis.receipt_id,
        timestamp_added=sample_receipt_line_item_analysis.timestamp_added,
        items=sample_receipt_line_item_analysis.items,
        reasoning="Updated reasoning",  # Changed reasoning
        version="1.1",  # Changed version
        subtotal=sample_receipt_line_item_analysis.subtotal,
        tax=sample_receipt_line_item_analysis.tax,
        total=sample_receipt_line_item_analysis.total,
    )

    # Act
    client.update_receipt_line_item_analysis(updated_analysis)

    # Assert
    # Verify the item was updated
    response = boto3.client("dynamodb", region_name="us-east-1").get_item(
        TableName=dynamodb_table,
        Key={
            "PK": {"S": f"IMAGE#{updated_analysis.image_id}"},
            "SK": {
                "S": f"RECEIPT#{updated_analysis.receipt_id:05d}#ANALYSIS#LINE_ITEMS"
            },
        },
    )
    assert "Item" in response
    assert response["Item"]["reasoning"]["S"] == "Updated reasoning"
    assert response["Item"]["version"]["S"] == "1.1"


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (None, "analysis cannot be None"),
        (
            "not a ReceiptLineItemAnalysis",
            "analysis must be an instance of the ReceiptLineItemAnalysis class.",
        ),
    ],
)
def test_updateReceiptLineItemAnalysis_invalid_parameters(
    dynamodb_table,
    sample_receipt_line_item_analysis,
    mocker,
    invalid_input,
    expected_error,
):
    """Test that invalid parameters raise appropriate errors."""
    # Arrange
    client = DynamoClient(table_name=dynamodb_table)

    # Mock boto3 client to prevent actual calls for invalid inputs
    mocker.patch.object(client, "_client")

    # Act & Assert
    with pytest.raises(ValueError) as excinfo:
        client.update_receipt_line_item_analysis(invalid_input)
    assert expected_error in str(excinfo.value)


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_error",
    [
        (
            "ConditionalCheckFailedException",
            "Item does not exist",
            "Entity does not exist: Receipt_Line_Item_Analysis",
        ),
        (
            "ProvisionedThroughputExceededException",
            "Provisioned throughput exceeded",
            "Provisioned throughput exceeded",
        ),
        (
            "InternalServerError",
            "Internal server error",
            "Internal server error",
        ),
        (
            "ResourceNotFoundException",
            "Table not found",
            "Table not found for operation update_receipt_line_item_analysis",
        ),
        (
            "ValidationException",
            "One or more parameters given were invalid",
            "One or more parameters given were invalid",
        ),
        (
            "AccessDeniedException",
            "Access denied",
            "Access denied",
        ),
        (
            "Exception",
            "Unknown error occurred",
            "Could not update receipt line item analysis in DynamoDB",
        ),
    ],
)
def test_updateReceiptLineItemAnalysis_client_errors(
    dynamodb_table,
    sample_receipt_line_item_analysis,
    mocker,
    error_code,
    error_message,
    expected_error,
):
    """Test handling of various client errors when updating a ReceiptLineItemAnalysis."""
    # Arrange
    client = DynamoClient(table_name=dynamodb_table)

    # Mock boto3 client to simulate the error
    mock_client = mocker.patch.object(client, "_client")
    error_response = {"Error": {"Code": error_code, "Message": error_message}}
    mock_client.put_item.side_effect = ClientError(error_response, "PutItem")

    # Act & Assert
    if error_code == "ConditionalCheckFailedException":
        with pytest.raises(EntityNotFoundError) as excinfo:
            client.update_receipt_line_item_analysis(
                sample_receipt_line_item_analysis
            )
        assert expected_error in str(excinfo.value)
    else:
        with pytest.raises(Exception) as excinfo:
            client.update_receipt_line_item_analysis(
                sample_receipt_line_item_analysis
            )
        assert expected_error in str(excinfo.value)


@pytest.mark.integration
def test_updateReceiptLineItemAnalyses_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_line_item_analysis: ReceiptLineItemAnalysis,
):
    """Test successful update of multiple ReceiptLineItemAnalyses in DynamoDB."""
    # Arrange
    client = DynamoClient(table_name=dynamodb_table)

    # Create two analyses
    second_analysis = ReceiptLineItemAnalysis(
        image_id=sample_receipt_line_item_analysis.image_id,
        receipt_id=2,
        timestamp_added=sample_receipt_line_item_analysis.timestamp_added,
        items=sample_receipt_line_item_analysis.items,
        reasoning=sample_receipt_line_item_analysis.reasoning,
        version=sample_receipt_line_item_analysis.version,
        subtotal=sample_receipt_line_item_analysis.subtotal,
        tax=sample_receipt_line_item_analysis.tax,
        total=sample_receipt_line_item_analysis.total,
    )

    # Add the items first
    client.add_receipt_line_item_analyses(
        [sample_receipt_line_item_analysis, second_analysis]
    )

    # Modify items for update
    updated_analysis1 = ReceiptLineItemAnalysis(
        image_id=sample_receipt_line_item_analysis.image_id,
        receipt_id=sample_receipt_line_item_analysis.receipt_id,
        timestamp_added=sample_receipt_line_item_analysis.timestamp_added,
        items=sample_receipt_line_item_analysis.items,
        reasoning="Updated reasoning 1",
        version="1.1",
        subtotal=sample_receipt_line_item_analysis.subtotal,
        tax=sample_receipt_line_item_analysis.tax,
        total=sample_receipt_line_item_analysis.total,
    )

    updated_analysis2 = ReceiptLineItemAnalysis(
        image_id=second_analysis.image_id,
        receipt_id=second_analysis.receipt_id,
        timestamp_added=second_analysis.timestamp_added,
        items=second_analysis.items,
        reasoning="Updated reasoning 2",
        version="1.1",
        subtotal=second_analysis.subtotal,
        tax=second_analysis.tax,
        total=second_analysis.total,
    )

    # Act
    client.update_receipt_line_item_analyses(
        [updated_analysis1, updated_analysis2]
    )

    # Assert
    # Verify the items were updated
    for analysis in [updated_analysis1, updated_analysis2]:
        response = boto3.client("dynamodb", region_name="us-east-1").get_item(
            TableName=dynamodb_table,
            Key={
                "PK": {"S": f"IMAGE#{analysis.image_id}"},
                "SK": {
                    "S": f"RECEIPT#{analysis.receipt_id:05d}#ANALYSIS#LINE_ITEMS"
                },
            },
        )
        assert "Item" in response
        assert "Updated reasoning" in response["Item"]["reasoning"]["S"]
        assert response["Item"]["version"]["S"] == "1.1"


@pytest.mark.integration
def test_updateReceiptLineItemAnalyses_with_large_batch(
    dynamodb_table, sample_receipt_line_item_analysis
):
    """Test update of a large batch of ReceiptLineItemAnalyses (requiring multiple batches)."""
    # Arrange
    client = DynamoClient(table_name=dynamodb_table)

    # Create and add 30 analyses (more than the batch size of 25)
    analyses = []
    for i in range(1, 31):
        analysis = ReceiptLineItemAnalysis(
            image_id=sample_receipt_line_item_analysis.image_id,
            receipt_id=i,
            timestamp_added=sample_receipt_line_item_analysis.timestamp_added,
            items=sample_receipt_line_item_analysis.items,
            reasoning=sample_receipt_line_item_analysis.reasoning,
            version=sample_receipt_line_item_analysis.version,
            subtotal=sample_receipt_line_item_analysis.subtotal,
            tax=sample_receipt_line_item_analysis.tax,
            total=sample_receipt_line_item_analysis.total,
        )
        analyses.append(analysis)

    # Add items first
    client.add_receipt_line_item_analyses(analyses)

    # Create updated versions
    updated_analyses = []
    for i, analysis in enumerate(analyses):
        updated_analysis = ReceiptLineItemAnalysis(
            image_id=analysis.image_id,
            receipt_id=analysis.receipt_id,
            timestamp_added=analysis.timestamp_added,
            items=analysis.items,
            reasoning=f"Updated reasoning {i}",
            version="1.1",
            subtotal=analysis.subtotal,
            tax=analysis.tax,
            total=analysis.total,
        )
        updated_analyses.append(updated_analysis)

    # Act
    client.update_receipt_line_item_analyses(updated_analyses)

    # Assert - check a few of the updated items
    for receipt_id in [1, 15, 30]:
        response = boto3.client("dynamodb", region_name="us-east-1").get_item(
            TableName=dynamodb_table,
            Key={
                "PK": {
                    "S": f"IMAGE#{sample_receipt_line_item_analysis.image_id}"
                },
                "SK": {"S": f"RECEIPT#{receipt_id:05d}#ANALYSIS#LINE_ITEMS"},
            },
        )
        assert "Item" in response
        assert "Updated reasoning" in response["Item"]["reasoning"]["S"]
        assert response["Item"]["version"]["S"] == "1.1"


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (None, "analyses cannot be None"),
        (
            "not-a-list",
            "analyses must be a list of ReceiptLineItemAnalysis instances.",
        ),
        (
            [123, "not-a-receipt-line-item-analysis"],
            "analyses must be a list of ReceiptLineItemAnalysis instances.",
        ),
    ],
)
def test_updateReceiptLineItemAnalyses_invalid_inputs(
    dynamodb_table,
    sample_receipt_line_item_analysis,
    mocker,
    invalid_input,
    expected_error,
):
    """Test that invalid inputs raise appropriate errors when updating multiple analyses."""
    # Arrange
    client = DynamoClient(table_name=dynamodb_table)

    # Mock boto3 client to prevent actual calls for invalid inputs
    mocker.patch.object(client, "_client")

    # Act & Assert
    with pytest.raises(ValueError) as excinfo:
        client.update_receipt_line_item_analyses(invalid_input)
    assert expected_error in str(excinfo.value)


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_error,expected_exception,cancellation_reasons",
    [
        (
            "ResourceNotFoundException",
            "Table not found",
            "Table not found for operation update_receipt_line_item_analyses",
            DynamoDBError,
            None,
        ),
        (
            "TransactionCanceledException",
            "Transaction canceled due to ConditionalCheckFailed",
            "One or more receipt line item analyses do not exist",
            ValueError,
            [{"Code": "ConditionalCheckFailed"}],
        ),
        (
            "InternalServerError",
            "Internal server error",
            "Internal server error",
            DynamoDBServerError,
            None,
        ),
        (
            "ProvisionedThroughputExceededException",
            "Provisioned throughput exceeded",
            "Provisioned throughput exceeded",
            DynamoDBThroughputError,
            None,
        ),
        (
            "ValidationException",
            "One or more parameters given were invalid",
            "One or more parameters given were invalid",
            DynamoDBValidationError,
            None,
        ),
        (
            "AccessDeniedException",
            "Access denied",
            "Access denied",
            DynamoDBAccessError,
            None,
        ),
        (
            "Exception",
            "Unknown error occurred",
            "Could not update receipt line item analyses in DynamoDB",
            DynamoDBError,
            None,
        ),
    ],
)
def test_updateReceiptLineItemAnalyses_client_errors(
    dynamodb_table,
    sample_receipt_line_item_analysis,
    mocker,
    error_code,
    error_message,
    expected_error,
    expected_exception,
    cancellation_reasons,
):
    """Test handling of various client errors when updating multiple ReceiptLineItemAnalyses."""
    # Arrange
    client = DynamoClient(table_name=dynamodb_table)

    # Mock boto3 client to simulate the error
    mock_client = mocker.patch.object(client, "_client")

    # For TransactionCanceledException, include the cancellation reasons
    if error_code == "TransactionCanceledException":
        error_response = {
            "Error": {
                "Code": error_code,
                "Message": error_message,
            },
            "CancellationReasons": cancellation_reasons,
        }
    else:
        error_response = {
            "Error": {"Code": error_code, "Message": error_message}
        }

    mock_client.batch_write_item.side_effect = ClientError(
        error_response, "BatchWriteItem"
    )

    # Act & Assert
    with pytest.raises(expected_exception) as excinfo:
        client.update_receipt_line_item_analyses(
            [sample_receipt_line_item_analysis]
        )
    assert expected_error in str(excinfo.value)


@pytest.mark.integration
def test_deleteReceiptLineItemAnalysis_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_line_item_analysis: ReceiptLineItemAnalysis,
):
    """Test successful deletion of a ReceiptLineItemAnalysis from DynamoDB."""
    # Arrange
    client = DynamoClient(table_name=dynamodb_table)

    # Add the analysis first
    client.add_receipt_line_item_analysis(sample_receipt_line_item_analysis)

    # Verify the analysis exists
    response = boto3.client("dynamodb", region_name="us-east-1").get_item(
        TableName=dynamodb_table,
        Key={
            "PK": {"S": f"IMAGE#{sample_receipt_line_item_analysis.image_id}"},
            "SK": {
                "S": f"RECEIPT#{sample_receipt_line_item_analysis.receipt_id:05d}#ANALYSIS#LINE_ITEMS"
            },
        },
    )
    assert "Item" in response

    # Act
    client.delete_receipt_line_item_analysis(sample_receipt_line_item_analysis)

    # Assert
    # Verify the analysis was deleted
    response = boto3.client("dynamodb", region_name="us-east-1").get_item(
        TableName=dynamodb_table,
        Key={
            "PK": {"S": f"IMAGE#{sample_receipt_line_item_analysis.image_id}"},
            "SK": {
                "S": f"RECEIPT#{sample_receipt_line_item_analysis.receipt_id}#ANALYSIS#LINE_ITEMS"
            },
        },
    )
    assert "Item" not in response


@pytest.mark.integration
@pytest.mark.parametrize(
    "image_id,receipt_id,expected_error",
    [
        (None, 1, "uuid must be a string"),
        (
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            None,
            "receipt_id must be an integer",
        ),
        (
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "not-an-int",
            "receipt_id must be an integer",
        ),
        ("invalid-uuid", 1, "uuid must be a valid UUIDv4"),
    ],
)
def test_deleteReceiptLineItemAnalysis_invalid_parameters(
    dynamodb_table, mocker, image_id, receipt_id, expected_error
):
    """Test that invalid parameters raise appropriate errors when deleting a ReceiptLineItemAnalysis."""
    # Arrange
    client = DynamoClient(table_name=dynamodb_table)

    # Mock boto3 client to prevent actual calls for invalid inputs
    mocker.patch.object(client, "_client")

    # Act & Assert
    # Try to create an invalid analysis object
    with pytest.raises(ValueError) as excinfo:
        # This will raise ValueError when trying to create the object with invalid parameters
        from datetime import datetime

        from receipt_dynamo.entities.receipt_line_item_analysis import (
            ReceiptLineItemAnalysis,
        )

        analysis = ReceiptLineItemAnalysis(
            image_id=image_id,
            receipt_id=receipt_id,
            timestamp_added=datetime.now(),
            items=[],
            reasoning="Test analysis",
            version="v1",
        )
        client.delete_receipt_line_item_analysis(analysis)
    assert expected_error in str(excinfo.value)


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_error",
    [
        (
            "ResourceNotFoundException",
            "Table not found",
            "Table not found for operation delete_receipt_line_item_analysis",
        ),
        (
            "ConditionalCheckFailedException",
            "The conditional request failed",
            "Entity does not exist: Receipt_Line_Item_Analysis",
        ),
        (
            "InternalServerError",
            "Internal server error",
            "Internal server error",
        ),
        (
            "ProvisionedThroughputExceededException",
            "Provisioned throughput exceeded",
            "Provisioned throughput exceeded",
        ),
        (
            "ValidationException",
            "One or more parameters given were invalid",
            "One or more parameters given were invalid",
        ),
        ("AccessDeniedException", "Access denied", "Access denied"),
        (
            "Exception",
            "Unknown error occurred",
            "Could not delete receipt line item analysis from DynamoDB",
        ),
    ],
)
def test_deleteReceiptLineItemAnalysis_client_errors(
    dynamodb_table,
    sample_receipt_line_item_analysis,
    mocker,
    error_code,
    error_message,
    expected_error,
):
    """Test handling of various client errors when deleting a ReceiptLineItemAnalysis."""
    # Arrange
    client = DynamoClient(table_name=dynamodb_table)

    # Mock boto3 client to simulate the error
    mock_client = mocker.patch.object(client, "_client")
    mock_client.delete_item.side_effect = ClientError(
        {"Error": {"Code": error_code, "Message": error_message}}, "DeleteItem"
    )

    # Act & Assert
    if error_code == "ConditionalCheckFailedException":
        with pytest.raises(EntityNotFoundError) as excinfo:
            client.delete_receipt_line_item_analysis(
                sample_receipt_line_item_analysis
            )
        assert expected_error in str(excinfo.value)
    else:
        with pytest.raises(Exception) as excinfo:
            client.delete_receipt_line_item_analysis(
                sample_receipt_line_item_analysis
            )
        assert expected_error in str(excinfo.value)


@pytest.mark.integration
def test_deleteReceiptLineItemAnalyses_not_found(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_line_item_analysis: ReceiptLineItemAnalysis,
):
    """Test deletion of non-existent ReceiptLineItemAnalyses (should not raise an error)."""
    # Arrange
    client = DynamoClient(table_name=dynamodb_table)

    # Create proper ReceiptLineItemAnalysis objects with non-existent IDs
    non_existent_analyses = [
        ReceiptLineItemAnalysis(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",  # Valid UUID
            receipt_id=999,  # Non-existent receipt ID
            timestamp_added=sample_receipt_line_item_analysis.timestamp_added,
            items=sample_receipt_line_item_analysis.items,
            reasoning="Non-existent analysis 1",
            version=sample_receipt_line_item_analysis.version,
            subtotal=sample_receipt_line_item_analysis.subtotal,
            tax=sample_receipt_line_item_analysis.tax,
            total=sample_receipt_line_item_analysis.total,
        ),
        ReceiptLineItemAnalysis(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",  # Valid UUID
            receipt_id=1000,  # Another non-existent receipt ID
            timestamp_added=sample_receipt_line_item_analysis.timestamp_added,
            items=sample_receipt_line_item_analysis.items,
            reasoning="Non-existent analysis 2",
            version=sample_receipt_line_item_analysis.version,
            subtotal=sample_receipt_line_item_analysis.subtotal,
            tax=sample_receipt_line_item_analysis.tax,
            total=sample_receipt_line_item_analysis.total,
        ),
    ]

    # Act - should not raise an error
    client.delete_receipt_line_item_analyses(non_existent_analyses)

    # Assert - The items weren't there to begin with, so no assertions are needed


@pytest.mark.integration
def test_deleteReceiptLineItemAnalyses_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_line_item_analysis: ReceiptLineItemAnalysis,
):
    """Test successful deletion of multiple ReceiptLineItemAnalyses from DynamoDB."""
    # Arrange
    client = DynamoClient(table_name=dynamodb_table)

    # Create analyses based on the keys
    analyses = []
    # Add first analysis
    analyses.append(sample_receipt_line_item_analysis)

    # Add second analysis
    second_analysis = ReceiptLineItemAnalysis(
        image_id=sample_receipt_line_item_analysis.image_id,
        receipt_id=2,
        timestamp_added=sample_receipt_line_item_analysis.timestamp_added,
        items=sample_receipt_line_item_analysis.items,
        reasoning=sample_receipt_line_item_analysis.reasoning,
        version=sample_receipt_line_item_analysis.version,
        subtotal=sample_receipt_line_item_analysis.subtotal,
        tax=sample_receipt_line_item_analysis.tax,
        total=sample_receipt_line_item_analysis.total,
    )
    analyses.append(second_analysis)

    # Add the analyses
    client.add_receipt_line_item_analyses(analyses)

    # Verify the analyses exist
    for analysis in analyses:
        response = boto3.client("dynamodb", region_name="us-east-1").get_item(
            TableName=dynamodb_table,
            Key={
                "PK": {"S": f"IMAGE#{analysis.image_id}"},
                "SK": {
                    "S": f"RECEIPT#{analysis.receipt_id:05d}#ANALYSIS#LINE_ITEMS"
                },
            },
        )
        assert "Item" in response

    # Act
    client.delete_receipt_line_item_analyses(analyses)

    # Assert
    # Verify the analyses were deleted
    for analysis in analyses:
        response = boto3.client("dynamodb", region_name="us-east-1").get_item(
            TableName=dynamodb_table,
            Key={
                "PK": {"S": f"IMAGE#{analysis.image_id}"},
                "SK": {
                    "S": f"RECEIPT#{analysis.receipt_id}#ANALYSIS#LINE_ITEMS"
                },
            },
        )
        assert "Item" not in response


@pytest.mark.integration
def test_deleteReceiptLineItemAnalyses_with_large_batch(
    dynamodb_table, sample_receipt_line_item_analysis
):
    """Test deletion of a large batch of ReceiptLineItemAnalyses (requiring multiple batches)."""
    # Arrange
    client = DynamoClient(table_name=dynamodb_table)

    # Create and add 30 analyses (more than the batch size of 25)
    analyses = []
    for i in range(1, 31):
        analysis = ReceiptLineItemAnalysis(
            image_id=sample_receipt_line_item_analysis.image_id,
            receipt_id=i,
            timestamp_added=sample_receipt_line_item_analysis.timestamp_added,
            items=sample_receipt_line_item_analysis.items,
            reasoning=sample_receipt_line_item_analysis.reasoning,
            version=sample_receipt_line_item_analysis.version,
            subtotal=sample_receipt_line_item_analysis.subtotal,
            tax=sample_receipt_line_item_analysis.tax,
            total=sample_receipt_line_item_analysis.total,
        )
        analyses.append(analysis)

    # Add items first
    client.add_receipt_line_item_analyses(analyses)

    # Verify a sample of analyses exist
    for receipt_id in [1, 15, 30]:
        response = boto3.client("dynamodb", region_name="us-east-1").get_item(
            TableName=dynamodb_table,
            Key={
                "PK": {
                    "S": f"IMAGE#{sample_receipt_line_item_analysis.image_id}"
                },
                "SK": {"S": f"RECEIPT#{receipt_id:05d}#ANALYSIS#LINE_ITEMS"},
            },
        )
        assert "Item" in response

    # Act
    client.delete_receipt_line_item_analyses(analyses)

    # Assert - check a sample of the deleted items
    for receipt_id in [1, 15, 30]:
        response = boto3.client("dynamodb", region_name="us-east-1").get_item(
            TableName=dynamodb_table,
            Key={
                "PK": {
                    "S": f"IMAGE#{sample_receipt_line_item_analysis.image_id}"
                },
                "SK": {"S": f"RECEIPT#{receipt_id}#ANALYSIS#LINE_ITEMS"},
            },
        )
        assert "Item" not in response


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (None, "analyses cannot be None"),
        (
            "not-a-list",
            "analyses must be a list of ReceiptLineItemAnalysis instances.",
        ),
        (
            [123, "not-an-analysis"],
            "analyses must be a list of ReceiptLineItemAnalysis instances.",
        ),
    ],
)
def test_deleteReceiptLineItemAnalyses_invalid_inputs(
    dynamodb_table,
    mocker,
    invalid_input,
    expected_error,
):
    """Test that invalid inputs raise appropriate errors when deleting multiple analyses."""
    # Arrange
    client = DynamoClient(table_name=dynamodb_table)

    # Mock boto3 client to prevent actual calls for invalid inputs
    mocker.patch.object(client, "_client")

    # Act & Assert
    with pytest.raises(ValueError) as excinfo:
        client.delete_receipt_line_item_analyses(invalid_input)
    assert expected_error in str(excinfo.value)


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_error",
    [
        (
            "ResourceNotFoundException",
            "Table not found",
            "Table not found for operation delete_receipt_line_item_analyses",
        ),
        (
            "InternalServerError",
            "Internal server error",
            "Internal server error",
        ),
        (
            "ProvisionedThroughputExceededException",
            "Provisioned throughput exceeded",
            "Provisioned throughput exceeded",
        ),
        (
            "ValidationException",
            "One or more parameters given were invalid",
            "One or more parameters given were invalid",
        ),
        ("AccessDeniedException", "Access denied", "Access denied"),
        (
            "Exception",
            "Unknown error occurred",
            "Could not delete receipt line item analyses from DynamoDB",
        ),
    ],
)
def test_deleteReceiptLineItemAnalyses_client_errors(
    dynamodb_table,
    sample_receipt_line_item_analysis,
    mocker,
    error_code,
    error_message,
    expected_error,
):
    """Test handling of various client errors when deleting multiple ReceiptLineItemAnalyses."""
    # Arrange
    client = DynamoClient(table_name=dynamodb_table)

    # Mock boto3 client to simulate the error
    mock_client = mocker.patch.object(client, "_client")
    mock_client.batch_write_item.side_effect = ClientError(
        {"Error": {"Code": error_code, "Message": error_message}},
        "BatchWriteItem",
    )

    # Act & Assert
    with pytest.raises(Exception) as excinfo:
        client.delete_receipt_line_item_analyses(
            [sample_receipt_line_item_analysis]
        )
    assert expected_error in str(excinfo.value)


@pytest.mark.integration
def test_deleteReceiptLineItemAnalyses_with_unprocessed_items_retries(
    dynamodb_table, sample_receipt_line_item_analysis, mocker
):
    """Test that unprocessed items are retried when deleting multiple analyses."""
    # Arrange
    client = DynamoClient(table_name=dynamodb_table)

    # Create a list of analyses to delete
    analyses = []
    for i in range(1, 4):
        analysis = ReceiptLineItemAnalysis(
            image_id=sample_receipt_line_item_analysis.image_id,
            receipt_id=i,
            timestamp_added=sample_receipt_line_item_analysis.timestamp_added,
            items=sample_receipt_line_item_analysis.items,
            reasoning=sample_receipt_line_item_analysis.reasoning,
            version=sample_receipt_line_item_analysis.version,
            subtotal=sample_receipt_line_item_analysis.subtotal,
            tax=sample_receipt_line_item_analysis.tax,
            total=sample_receipt_line_item_analysis.total,
        )
        analyses.append(analysis)

    # Mock boto3 client to simulate unprocessed items on first call, then success on second
    mock_client = mocker.patch.object(client, "_client")

    # First call returns unprocessed items
    unprocessed_item = {
        "PK": {"S": f"IMAGE#{sample_receipt_line_item_analysis.image_id}"},
        "SK": {"S": "RECEIPT#3#ANALYSIS#LINE_ITEMS"},
    }

    mock_client.batch_write_item.side_effect = [
        # First call returns unprocessed items
        {
            "UnprocessedItems": {
                dynamodb_table: [{"DeleteRequest": {"Key": unprocessed_item}}]
            }
        },
        # Second call succeeds
        {"UnprocessedItems": {}},
    ]

    # Act
    client.delete_receipt_line_item_analyses(analyses)

    # Assert - verify the batch_write_item was called twice
    assert mock_client.batch_write_item.call_count == 2


@pytest.mark.integration
def test_getReceiptLineItemAnalysis_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_line_item_analysis: ReceiptLineItemAnalysis,
):
    """Test successful retrieval of a ReceiptLineItemAnalysis from DynamoDB."""
    # Arrange
    client = DynamoClient(table_name=dynamodb_table)

    # Add the analysis first
    client.add_receipt_line_item_analysis(sample_receipt_line_item_analysis)

    # Act
    result = client.get_receipt_line_item_analysis(
        image_id=sample_receipt_line_item_analysis.image_id,
        receipt_id=sample_receipt_line_item_analysis.receipt_id,
    )

    # Assert
    assert result is not None
    assert isinstance(result, ReceiptLineItemAnalysis)
    assert result.image_id == sample_receipt_line_item_analysis.image_id
    assert result.receipt_id == sample_receipt_line_item_analysis.receipt_id
    assert (
        result.timestamp_added
        == sample_receipt_line_item_analysis.timestamp_added
    )
    assert result.items == sample_receipt_line_item_analysis.items
    assert result.reasoning == sample_receipt_line_item_analysis.reasoning
    assert result.version == sample_receipt_line_item_analysis.version
    assert result.subtotal == sample_receipt_line_item_analysis.subtotal
    assert result.tax == sample_receipt_line_item_analysis.tax
    assert result.total == sample_receipt_line_item_analysis.total


@pytest.mark.integration
def test_getReceiptLineItemAnalysis_not_found(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_line_item_analysis: ReceiptLineItemAnalysis,
):
    """Test retrieval of a non-existent ReceiptLineItemAnalysis (should return None)."""
    # Arrange
    client = DynamoClient(table_name=dynamodb_table)
    # Use a valid UUID but one that doesn't exist in the database
    valid_uuid = sample_receipt_line_item_analysis.image_id
    non_existent_receipt_id = 9999

    # Act & Assert
    # The implementation might either return None or raise an exception
    try:
        result = client.get_receipt_line_item_analysis(
            image_id=valid_uuid,
            receipt_id=non_existent_receipt_id,
        )
        assert result is None
    except ValueError as e:
        # If it raises an exception, make sure it's the expected one
        assert (
            f"Receipt Line Item Analysis for Image ID {valid_uuid} and Receipt ID {non_existent_receipt_id} does not exist"
            in str(e)
        )


@pytest.mark.integration
@pytest.mark.parametrize(
    "image_id,receipt_id,expected_error",
    [
        (None, 1, "image_id must be a string, got NoneType"),
        (
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            None,
            "receipt_id must be an integer, got NoneType",
        ),
        (
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "not-an-integer",
            "receipt_id must be an integer",
        ),
        ("", 1, "uuid must be a valid UUIDv4"),
        (
            "invalid-uuid",
            1,
            "uuid must be a valid UUIDv4",
        ),
    ],
)
def test_getReceiptLineItemAnalysis_invalid_parameters(
    dynamodb_table, mocker, image_id, receipt_id, expected_error
):
    """Test that invalid parameters raise appropriate errors when getting a ReceiptLineItemAnalysis."""
    # Arrange
    client = DynamoClient(table_name=dynamodb_table)

    # Mock boto3 client to prevent actual calls for invalid inputs
    mocker.patch.object(client, "_client")

    # Act & Assert
    with pytest.raises(ValueError) as excinfo:
        client.get_receipt_line_item_analysis(
            image_id=image_id, receipt_id=receipt_id
        )
    assert expected_error in str(excinfo.value)


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_error",
    [
        (
            "ResourceNotFoundException",
            "Table not found",
            "Table not found for operation get_receipt_line_item_analysis",
        ),
        (
            "InternalServerError",
            "Internal server error",
            "Internal server error",
        ),
        (
            "ProvisionedThroughputExceededException",
            "Provisioned throughput exceeded",
            "Provisioned throughput exceeded",
        ),
        (
            "ValidationException",
            "One or more parameters given were invalid",
            "One or more parameters given were invalid",
        ),
        ("AccessDeniedException", "Access denied", "Access denied"),
        (
            "Exception",
            "Unknown error occurred",
            "Could not get receipt line item analysis",
        ),
    ],
)
def test_getReceiptLineItemAnalysis_client_errors(
    dynamodb_table,
    sample_receipt_line_item_analysis,
    mocker,
    error_code,
    error_message,
    expected_error,
):
    """Test handling of various client errors when getting a ReceiptLineItemAnalysis."""
    # Arrange
    client = DynamoClient(table_name=dynamodb_table)

    # Mock boto3 client to simulate the error
    mock_client = mocker.patch.object(client, "_client")
    mock_client.get_item.side_effect = ClientError(
        {"Error": {"Code": error_code, "Message": error_message}}, "GetItem"
    )

    # Act & Assert
    with pytest.raises(Exception) as excinfo:
        client.get_receipt_line_item_analysis(
            image_id=sample_receipt_line_item_analysis.image_id,
            receipt_id=sample_receipt_line_item_analysis.receipt_id,
        )
    assert expected_error in str(excinfo.value)


@pytest.mark.integration
def test_getReceiptLineItemAnalysis_malformed_item(
    dynamodb_table, sample_receipt_line_item_analysis, mocker
):
    """Test handling of malformed items when getting a ReceiptLineItemAnalysis."""
    # Arrange
    client = DynamoClient(table_name=dynamodb_table)

    # Mock boto3 client to return a malformed item
    mock_client = mocker.patch.object(client, "_client")
    mock_client.get_item.return_value = {
        "Item": {
            # Missing required attributes
            "PK": {"S": f"IMAGE#{sample_receipt_line_item_analysis.image_id}"},
            "SK": {
                "S": f"RECEIPT#{sample_receipt_line_item_analysis.receipt_id}#ANALYSIS#LINE_ITEMS"
            },
        }
    }

    # Act & Assert
    with pytest.raises(Exception) as excinfo:
        client.get_receipt_line_item_analysis(
            image_id=sample_receipt_line_item_analysis.image_id,
            receipt_id=sample_receipt_line_item_analysis.receipt_id,
        )
    assert "Invalid item format" in str(excinfo.value)


@pytest.mark.integration
def test_listReceiptLineItemAnalyses_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_line_item_analysis: ReceiptLineItemAnalysis,
):
    """Test successful listing of all ReceiptLineItemAnalyses from DynamoDB."""
    # Arrange
    client = DynamoClient(table_name=dynamodb_table)

    # Create and add multiple analyses
    analyses = []
    for i in range(1, 4):
        analysis = ReceiptLineItemAnalysis(
            image_id=sample_receipt_line_item_analysis.image_id,  # Use the valid UUID from the sample
            receipt_id=i,
            timestamp_added=sample_receipt_line_item_analysis.timestamp_added,
            items=sample_receipt_line_item_analysis.items,
            reasoning=f"Reasoning for image{i}",
            version=sample_receipt_line_item_analysis.version,
            subtotal=sample_receipt_line_item_analysis.subtotal,
            tax=sample_receipt_line_item_analysis.tax,
            total=sample_receipt_line_item_analysis.total,
        )
        analyses.append(analysis)

    client.add_receipt_line_item_analyses(analyses)

    # Act
    result, last_evaluated_key = client.list_receipt_line_item_analyses()

    # Assert
    assert result is not None
    assert isinstance(result, list)
    assert len(result) >= 3  # At least the ones we added

    # Verify the analyses we added are in the results
    found_analyses = 0
    for analysis in result:
        assert isinstance(analysis, ReceiptLineItemAnalysis)
        if (
            analysis.image_id == sample_receipt_line_item_analysis.image_id
            and analysis.receipt_id in [1, 2, 3]
        ):
            found_analyses += 1

    assert found_analyses == 3


@pytest.mark.integration
def test_listReceiptLineItemAnalyses_empty(
    dynamodb_table: Literal["MyMockedTable"],
):
    """Test listing ReceiptLineItemAnalyses when the table is empty."""
    # Arrange
    client = DynamoClient(table_name=dynamodb_table)

    # Act - we assume the table has been cleared since our fixture runs for each test
    result, last_evaluated_key = client.list_receipt_line_item_analyses()

    # Assert
    assert result is not None
    assert isinstance(result, list)
    assert len(result) == 0


@pytest.mark.integration
def test_listReceiptLineItemAnalyses_with_prefix(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_line_item_analysis: ReceiptLineItemAnalysis,
):
    """Test listing ReceiptLineItemAnalyses with a specific image_id."""
    # Arrange
    client = DynamoClient(table_name=dynamodb_table)

    # Create and add analyses with different image_ids
    # Using valid UUIDs
    uuid_base = "3f52804b-2fad-4e00-92c8-b593da3a8e"
    image_id_1 = uuid_base + "d1"
    image_id_2 = uuid_base + "f1"

    analyses = []
    # Add 3 analyses with image_id_1
    for i in range(1, 4):
        analysis = ReceiptLineItemAnalysis(
            image_id=image_id_1,
            receipt_id=i,
            timestamp_added=sample_receipt_line_item_analysis.timestamp_added,
            items=sample_receipt_line_item_analysis.items,
            reasoning=f"Reasoning for {image_id_1}",
            version=sample_receipt_line_item_analysis.version,
            subtotal=sample_receipt_line_item_analysis.subtotal,
            tax=sample_receipt_line_item_analysis.tax,
            total=sample_receipt_line_item_analysis.total,
        )
        analyses.append(analysis)

    # Add 2 analyses with image_id_2
    for i in range(1, 3):
        analysis = ReceiptLineItemAnalysis(
            image_id=image_id_2,
            receipt_id=i,
            timestamp_added=sample_receipt_line_item_analysis.timestamp_added,
            items=sample_receipt_line_item_analysis.items,
            reasoning=f"Reasoning for {image_id_2}",
            version=sample_receipt_line_item_analysis.version,
            subtotal=sample_receipt_line_item_analysis.subtotal,
            tax=sample_receipt_line_item_analysis.tax,
            total=sample_receipt_line_item_analysis.total,
        )
        analyses.append(analysis)

    client.add_receipt_line_item_analyses(analyses)

    # Act - list all analyses and filter in memory
    result, _ = client.list_receipt_line_item_analyses()

    # Assert
    assert result is not None
    assert isinstance(result, list)

    # Filter for analyses with image_id_1
    filtered_result = [a for a in result if a.image_id == image_id_1]
    assert len(filtered_result) == 3

    # Verify all filtered results have the correct image_id
    for analysis in filtered_result:
        assert analysis.image_id == image_id_1


@pytest.mark.integration
def test_listReceiptLineItemAnalyses_with_pagination(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_line_item_analysis: ReceiptLineItemAnalysis,
    mocker,
):
    """Test pagination in listReceiptLineItemAnalyses."""
    # Arrange
    client = DynamoClient(table_name=dynamodb_table)

    # Create a non-mocked client to add real items
    real_client = boto3.client("dynamodb", region_name="us-east-1")

    # Using valid UUIDs with a common prefix
    uuid_base = "3f52804b-2fad-4e00-92c8-b593da3a8e"

    # Create and add 10 analyses
    analyses = []
    for i in range(1, 11):
        # Ensure we create valid UUIDs by using incrementing hex values
        uuid_suffix = format(i, "x").zfill(
            2
        )  # Convert i to hex, pad to 2 digits
        analysis = ReceiptLineItemAnalysis(
            image_id=f"{uuid_base}{uuid_suffix}",
            receipt_id=i,
            timestamp_added=sample_receipt_line_item_analysis.timestamp_added,
            items=sample_receipt_line_item_analysis.items,
            reasoning=f"Reasoning for pagination-image{i}",
            version=sample_receipt_line_item_analysis.version,
            subtotal=sample_receipt_line_item_analysis.subtotal,
            tax=sample_receipt_line_item_analysis.tax,
            total=sample_receipt_line_item_analysis.total,
        )
        analyses.append(analysis)

    client.add_receipt_line_item_analyses(analyses)

    # Mock the query method to return paginated results
    mock_client = mocker.patch.object(client, "_client")

    # First page has 5 items and a LastEvaluatedKey
    first_page_items = []
    for i in range(1, 6):
        uuid_suffix = format(i, "x").zfill(2)
        item = {
            "PK": {"S": f"IMAGE#{uuid_base}{uuid_suffix}"},
            "SK": {"S": f"RECEIPT#{i}#ANALYSIS#LINE_ITEMS"},
            "image_id": {"S": f"{uuid_base}{uuid_suffix}"},
            "receipt_id": {"N": str(i)},
            "timestamp_added": {
                "S": sample_receipt_line_item_analysis.timestamp_added
            },
            "items": {
                "L": [{"M": {"description": {"S": "Test item"}}}]
            },  # Simplified items structure
            "reasoning": {"S": f"Reasoning for pagination-image{i}"},
            "version": {"S": sample_receipt_line_item_analysis.version},
            "subtotal": {"N": str(sample_receipt_line_item_analysis.subtotal)},
            "tax": {"N": str(sample_receipt_line_item_analysis.tax)},
            "total": {"N": str(sample_receipt_line_item_analysis.total)},
            "total_found": {"N": "1"},
            "TYPE": {"S": "RECEIPT_LINE_ITEM_ANALYSIS"},
            "GSI1PK": {"S": "ANALYSIS_TYPE"},
            "GSI1SK": {
                "S": f"LINE_ITEMS#{sample_receipt_line_item_analysis.timestamp_added}"
            },
            "GSI2PK": {"S": "RECEIPT"},
            "GSI2SK": {"S": f"IMAGE#{uuid_base}{uuid_suffix}#RECEIPT#{i}"},
            "discrepancies": {"L": []},
        }
        first_page_items.append(item)

    last_evaluated_key = {
        "PK": {"S": f"IMAGE#{uuid_base}05"},
        "SK": {"S": "RECEIPT#5#ANALYSIS#LINE_ITEMS"},
    }

    # Second page has 5 items and no LastEvaluatedKey
    second_page_items = []
    for i in range(6, 11):
        uuid_suffix = format(i, "x").zfill(2)
        item = {
            "PK": {"S": f"IMAGE#{uuid_base}{uuid_suffix}"},
            "SK": {"S": f"RECEIPT#{i}#ANALYSIS#LINE_ITEMS"},
            "image_id": {"S": f"{uuid_base}{uuid_suffix}"},
            "receipt_id": {"N": str(i)},
            "timestamp_added": {
                "S": sample_receipt_line_item_analysis.timestamp_added
            },
            "items": {
                "L": [{"M": {"description": {"S": "Test item"}}}]
            },  # Simplified items structure
            "reasoning": {"S": f"Reasoning for pagination-image{i}"},
            "version": {"S": sample_receipt_line_item_analysis.version},
            "subtotal": {"N": str(sample_receipt_line_item_analysis.subtotal)},
            "tax": {"N": str(sample_receipt_line_item_analysis.tax)},
            "total": {"N": str(sample_receipt_line_item_analysis.total)},
            "total_found": {"N": "1"},
            "TYPE": {"S": "RECEIPT_LINE_ITEM_ANALYSIS"},
            "GSI1PK": {"S": "ANALYSIS_TYPE"},
            "GSI1SK": {
                "S": f"LINE_ITEMS#{sample_receipt_line_item_analysis.timestamp_added}"
            },
            "GSI2PK": {"S": "RECEIPT"},
            "GSI2SK": {"S": f"IMAGE#{uuid_base}{uuid_suffix}#RECEIPT#{i}"},
            "discrepancies": {"L": []},
        }
        second_page_items.append(item)

    # Set up the mock to return the paginated results
    mock_client.query.side_effect = [
        {
            "Items": first_page_items,
            "LastEvaluatedKey": last_evaluated_key,
        },
        {
            "Items": second_page_items,
        },
    ]

    # Act
    result, last_key = client.list_receipt_line_item_analyses()

    # Assert
    assert result is not None
    assert isinstance(result, list)
    assert len(result) == 10

    # Verify query was called twice (for pagination)
    assert mock_client.query.call_count == 2

    # Check the second call included the ExclusiveStartKey from the first response
    _, kwargs = mock_client.query.call_args_list[1]
    assert "ExclusiveStartKey" in kwargs
    assert kwargs["ExclusiveStartKey"] == last_evaluated_key


@pytest.mark.integration
@pytest.mark.parametrize(
    "limit,last_evaluated_key,expected_error",
    [
        ("not-an-integer", None, "limit must be an integer or None."),
        (
            5,
            "not-a-dictionary",
            "last_evaluated_key must be a dictionary or None.",
        ),
    ],
)
def test_listReceiptLineItemAnalyses_invalid_parameters(
    dynamodb_table: Literal["MyMockedTable"],
    mocker: MockerFixture,
    limit: int | None,
    last_evaluated_key: dict | None,
    expected_error: str,
):
    """Test handling of invalid parameters when listing ReceiptLineItemAnalyses."""
    # Arrange
    client = DynamoClient(table_name=dynamodb_table)

    # Mock boto3 client to simulate the error
    mock_client = mocker.patch.object(client, "_client")

    # Configure the mock to raise the error when query is called
    mock_client.query.side_effect = ValueError(expected_error)

    # Act & Assert
    with pytest.raises(ValueError) as excinfo:
        client.list_receipt_line_item_analyses(
            limit=limit, last_evaluated_key=last_evaluated_key
        )
    assert expected_error in str(excinfo.value)


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_error",
    [
        (
            "ResourceNotFoundException",
            "Table not found",
            "Table not found for operation list_receipt_line_item_analyses",
        ),
        (
            "InternalServerError",
            "Internal server error",
            "Internal server error",
        ),
        (
            "ProvisionedThroughputExceededException",
            "Provisioned throughput exceeded",
            "Provisioned throughput exceeded",
        ),
        (
            "ValidationException",
            "One or more parameters given were invalid",
            "One or more parameters given were invalid",
        ),
        ("AccessDeniedException", "Access denied", "Access denied"),
        (
            "UnknownError",
            "Unknown error occurred",
            "Could not list receipt line item analyses from DynamoDB",
        ),
    ],
)
def test_listReceiptLineItemAnalyses_client_errors(
    dynamodb_table, mocker, error_code, error_message, expected_error
):
    """Test handling of various client errors when listing ReceiptLineItemAnalyses."""
    # Arrange
    client = DynamoClient(table_name=dynamodb_table)

    # Mock boto3 client to simulate the error
    mock_client = mocker.patch.object(client, "_client")

    # Create a ClientError with the specified error code and message
    error = ClientError(
        {"Error": {"Code": error_code, "Message": error_message}}, "Query"
    )

    # Configure the mock to raise the error when query is called
    mock_client.query.side_effect = error

    # Act & Assert
    with pytest.raises(Exception) as excinfo:
        client.list_receipt_line_item_analyses()
    assert expected_error in str(excinfo.value)


@pytest.mark.integration
def test_listReceiptLineItemAnalysesForImage_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_line_item_analysis: ReceiptLineItemAnalysis,
):
    """Test successful listing of ReceiptLineItemAnalyses for a specific image from DynamoDB."""
    # Arrange
    client = DynamoClient(table_name=dynamodb_table)
    image_id = sample_receipt_line_item_analysis.image_id

    # Create and add multiple analyses for the same image
    analyses = []
    for i in range(1, 4):
        analysis = ReceiptLineItemAnalysis(
            image_id=image_id,
            receipt_id=i,
            timestamp_added=sample_receipt_line_item_analysis.timestamp_added,
            items=sample_receipt_line_item_analysis.items,
            reasoning=f"Reasoning for receipt {i}",
            version=sample_receipt_line_item_analysis.version,
            subtotal=sample_receipt_line_item_analysis.subtotal,
            tax=sample_receipt_line_item_analysis.tax,
            total=sample_receipt_line_item_analysis.total,
        )
        analyses.append(analysis)

    # Also add an analysis for a different image to ensure filtering works
    other_image_analysis = ReceiptLineItemAnalysis(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed4",  # Different but valid UUID
        receipt_id=1,
        timestamp_added=sample_receipt_line_item_analysis.timestamp_added,
        items=sample_receipt_line_item_analysis.items,
        reasoning=f"Reasoning for other image",
        version=sample_receipt_line_item_analysis.version,
        subtotal=sample_receipt_line_item_analysis.subtotal,
        tax=sample_receipt_line_item_analysis.tax,
        total=sample_receipt_line_item_analysis.total,
    )
    analyses.append(other_image_analysis)

    client.add_receipt_line_item_analyses(analyses)

    # Act
    result = client.list_receipt_line_item_analyses_for_image(
        image_id=image_id
    )

    # Assert
    assert result is not None
    assert isinstance(result, list)
    assert len(result) == 3

    # Verify all results are for the correct image
    for analysis in result:
        assert isinstance(analysis, ReceiptLineItemAnalysis)
        assert analysis.image_id == image_id


@pytest.mark.integration
def test_listReceiptLineItemAnalysesForImage_not_found(
    dynamodb_table: Literal["MyMockedTable"],
):
    """Test listing ReceiptLineItemAnalyses for a non-existent image (should return empty list)."""
    # Arrange
    client = DynamoClient(table_name=dynamodb_table)
    non_existent_image_id = (
        "3f52804b-2fad-4e00-92c8-b593da3a8ed5"  # Valid UUID but doesn't exist
    )

    # Act
    result = client.list_receipt_line_item_analyses_for_image(
        image_id=non_existent_image_id
    )

    # Assert
    assert result is not None
    assert isinstance(result, list)
    assert len(result) == 0


@pytest.mark.integration
@pytest.mark.parametrize(
    "image_id,expected_error",
    [
        (None, "image_id must be a string, got NoneType"),
        ("", "uuid must be a valid UUIDv4"),
    ],
)
def test_listReceiptLineItemAnalysesForImage_invalid_parameters(
    dynamodb_table, mocker, image_id, expected_error
):
    """Test that invalid parameters raise appropriate errors when listing analyses for an image."""
    # Arrange
    client = DynamoClient(table_name=dynamodb_table)

    # Mock boto3 client to prevent actual calls for invalid inputs
    mocker.patch.object(client, "_client")

    # Act & Assert
    with pytest.raises(ValueError) as excinfo:
        client.list_receipt_line_item_analyses_for_image(image_id=image_id)
    assert expected_error in str(excinfo.value)


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_error",
    [
        (
            "ResourceNotFoundException",
            "Table not found",
            "Table not found for operation list_receipt_line_item_analyses",
        ),
        (
            "InternalServerError",
            "Internal server error",
            "Internal server error",
        ),
        (
            "ProvisionedThroughputExceededException",
            "Provisioned throughput exceeded",
            "Provisioned throughput exceeded",
        ),
        (
            "ValidationException",
            "One or more parameters given were invalid",
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
            "Could not list receipt line item analyses from DynamoDB",
        ),
    ],
)
def test_listReceiptLineItemAnalysesForImage_client_errors(
    dynamodb_table: str,
    sample_receipt_line_item_analysis: ReceiptLineItemAnalysis,
    mocker: MockerFixture,
    error_code: str,
    error_message: str,
    expected_error: str,
):
    """Test that client errors are handled correctly when listing analyses for an image."""
    # Arrange
    client = DynamoClient(table_name=dynamodb_table)
    image_id = sample_receipt_line_item_analysis.image_id

    # Mock boto3 client to raise the specified error
    mock_client = mocker.patch.object(client, "_client")

    # Create a ClientError with the specified error code and message
    error = ClientError(
        {"Error": {"Code": error_code, "Message": error_message}}, "Query"
    )

    # Configure the mock to raise the error when query is called
    mock_client.query.side_effect = error

    # Act & Assert
    with pytest.raises(Exception) as excinfo:
        client.list_receipt_line_item_analyses_for_image(image_id=image_id)
    assert expected_error in str(excinfo.value)
