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


def _set_client_error(mock_client, method, error_code, operation, message):
    getattr(mock_client, method).side_effect = _client_error(
        error_code, operation, message
    )


def _expected_client_exception(
    error_code, conditional_exception=DynamoDBError
):
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
    client = DynamoClient(table_name=dynamodb_table)

    client.add_receipt_line_item_analysis(sample_receipt_line_item_analysis)

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
    client = DynamoClient(table_name=dynamodb_table)
    client.add_receipt_line_item_analysis(sample_receipt_line_item_analysis)

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
            "analysis must be an instance of ReceiptLineItemAnalysis",
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
    client = DynamoClient(table_name=dynamodb_table)

    mocker.patch.object(client, "_client")

    with pytest.raises(OperationError) as excinfo:
        client.add_receipt_line_item_analysis(invalid_input)
    assert expected_error in str(excinfo.value)


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
            "Provisioned throughput exceeded",
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
            "One or more parameters given were invalid",
            EntityValidationError,
        ),
        ("AccessDeniedException", "Access denied", DynamoDBError),
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
    client = DynamoClient(table_name=dynamodb_table)

    mock_client = mocker.patch.object(client, "_client")
    _set_client_error(
        mock_client, "put_item", error_code, "PutItem", error_message
    )

    error_match = (
        "already exists"
        if error_code == "ConditionalCheckFailedException"
        else error_message
    )
    with pytest.raises(expected_exception, match=error_match):
        client.add_receipt_line_item_analysis(
            sample_receipt_line_item_analysis
        )


@pytest.mark.integration
def test_addReceiptLineItemAnalyses_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_line_item_analysis: ReceiptLineItemAnalysis,
):
    """Test successful addition of multiple ReceiptLineItemAnalyses to DynamoDB."""
    client = DynamoClient(table_name=dynamodb_table)

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

    client.add_receipt_line_item_analyses(analyses)

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
    client = DynamoClient(table_name=dynamodb_table)

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

    client.add_receipt_line_item_analyses(analyses)

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
    client = DynamoClient(table_name=dynamodb_table)

    mock_client = mocker.patch.object(client, "_client")

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

    second_response = {"UnprocessedItems": {}}

    mock_client.batch_write_item.side_effect = [
        first_response,
        second_response,
    ]

    client.add_receipt_line_item_analyses([sample_receipt_line_item_analysis])

    assert mock_client.batch_write_item.call_count == 2


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (None, "analyses cannot be None"),
        (
            "not-a-list",
            "analyses must be a list",
        ),
        (
            [123, "not-a-receipt-line-item-analysis"],
            "All items in analyses must be instances of ReceiptLineItemAnalysis",
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
    client = DynamoClient(table_name=dynamodb_table)

    mocker.patch.object(client, "_client")

    with pytest.raises(OperationError) as excinfo:
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
    client = DynamoClient(table_name=dynamodb_table)

    mock_client = mocker.patch.object(client, "_client")
    error_response = {"Error": {"Code": error_code, "Message": error_message}}
    mock_client.batch_write_item.side_effect = ClientError(
        error_response, "BatchWriteItem"
    )

    with pytest.raises(
        _expected_client_exception(error_code), match=error_message
    ):
        client.add_receipt_line_item_analyses(
            [sample_receipt_line_item_analysis]
        )


@pytest.mark.integration
def test_updateReceiptLineItemAnalysis_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_line_item_analysis: ReceiptLineItemAnalysis,
):
    """Test successful update of a ReceiptLineItemAnalysis in DynamoDB."""
    client = DynamoClient(table_name=dynamodb_table)

    client.add_receipt_line_item_analysis(sample_receipt_line_item_analysis)

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

    client.update_receipt_line_item_analysis(updated_analysis)

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
            "analysis must be an instance of ReceiptLineItemAnalysis",
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
    client = DynamoClient(table_name=dynamodb_table)

    mocker.patch.object(client, "_client")

    with pytest.raises(OperationError) as excinfo:
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
    client = DynamoClient(table_name=dynamodb_table)

    mock_client = mocker.patch.object(client, "_client")
    _set_client_error(
        mock_client, "put_item", error_code, "PutItem", error_message
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
        client.update_receipt_line_item_analysis(
            sample_receipt_line_item_analysis
        )


@pytest.mark.integration
def test_updateReceiptLineItemAnalyses_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_line_item_analysis: ReceiptLineItemAnalysis,
):
    """Test successful update of multiple ReceiptLineItemAnalyses in DynamoDB."""
    client = DynamoClient(table_name=dynamodb_table)

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

    client.add_receipt_line_item_analyses(
        [sample_receipt_line_item_analysis, second_analysis]
    )

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

    client.update_receipt_line_item_analyses(
        [updated_analysis1, updated_analysis2]
    )

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
    client = DynamoClient(table_name=dynamodb_table)

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

    client.add_receipt_line_item_analyses(analyses)

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

    client.update_receipt_line_item_analyses(updated_analyses)

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
            "analyses must be a list",
        ),
        (
            [123, "not-a-receipt-line-item-analysis"],
            "All items in analyses must be instances of ReceiptLineItemAnalysis",
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
    client = DynamoClient(table_name=dynamodb_table)

    mocker.patch.object(client, "_client")

    with pytest.raises(OperationError) as excinfo:
        client.update_receipt_line_item_analyses(invalid_input)
    assert expected_error in str(excinfo.value)


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
            "Provisioned throughput exceeded",
            DynamoDBThroughputError,
            None,
        ),
        (
            "ValidationException",
            "One or more parameters given were invalid",
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
            "Exception",
            "Unknown error occurred",
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
    expected_exception,
    cancellation_reasons,
):
    """Test handling of various client errors when updating multiple ReceiptLineItemAnalyses."""
    client = DynamoClient(table_name=dynamodb_table)

    mock_client = mocker.patch.object(client, "_client")

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

    with pytest.raises(expected_exception, match=error_message):
        client.update_receipt_line_item_analyses(
            [sample_receipt_line_item_analysis]
        )


@pytest.mark.integration
def test_deleteReceiptLineItemAnalysis_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_line_item_analysis: ReceiptLineItemAnalysis,
):
    """Test successful deletion of a ReceiptLineItemAnalysis from DynamoDB."""
    client = DynamoClient(table_name=dynamodb_table)

    client.add_receipt_line_item_analysis(sample_receipt_line_item_analysis)

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

    client.delete_receipt_line_item_analysis(sample_receipt_line_item_analysis)

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
    image_id, receipt_id, expected_error
):
    """Invalid entity keys are rejected before a delete can be attempted."""
    with pytest.raises(ValueError, match=expected_error):
        ReceiptLineItemAnalysis(
            image_id=image_id,
            receipt_id=receipt_id,
            timestamp_added=datetime.now(),
            items=[],
            reasoning="Test analysis",
            version="v1",
        )


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
    client = DynamoClient(table_name=dynamodb_table)

    mock_client = mocker.patch.object(client, "_client")
    _set_client_error(
        mock_client, "delete_item", error_code, "DeleteItem", error_message
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
        client.delete_receipt_line_item_analysis(
            sample_receipt_line_item_analysis
        )


@pytest.mark.integration
def test_deleteReceiptLineItemAnalyses_not_found(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_line_item_analysis: ReceiptLineItemAnalysis,
):
    """Test deletion of non-existent ReceiptLineItemAnalyses (should not raise an error)."""
    client = DynamoClient(table_name=dynamodb_table)

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

    client.delete_receipt_line_item_analyses(non_existent_analyses)


@pytest.mark.integration
def test_deleteReceiptLineItemAnalyses_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_line_item_analysis: ReceiptLineItemAnalysis,
):
    """Test successful deletion of multiple ReceiptLineItemAnalyses from DynamoDB."""
    client = DynamoClient(table_name=dynamodb_table)

    analyses = []
    analyses.append(sample_receipt_line_item_analysis)

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

    client.add_receipt_line_item_analyses(analyses)

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

    client.delete_receipt_line_item_analyses(analyses)

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
    client = DynamoClient(table_name=dynamodb_table)

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

    client.add_receipt_line_item_analyses(analyses)

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

    client.delete_receipt_line_item_analyses(analyses)

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
            "analyses must be a list",
        ),
        (
            [123, "not-an-analysis"],
            "All items in analyses must be instances of ReceiptLineItemAnalysis",
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
    client = DynamoClient(table_name=dynamodb_table)

    mocker.patch.object(client, "_client")

    with pytest.raises(OperationError) as excinfo:
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
    client = DynamoClient(table_name=dynamodb_table)

    mock_client = mocker.patch.object(client, "_client")
    mock_client.batch_write_item.side_effect = ClientError(
        {"Error": {"Code": error_code, "Message": error_message}},
        "BatchWriteItem",
    )

    with pytest.raises(
        _expected_client_exception(error_code), match=error_message
    ):
        client.delete_receipt_line_item_analyses(
            [sample_receipt_line_item_analysis]
        )


@pytest.mark.integration
def test_deleteReceiptLineItemAnalyses_with_unprocessed_items_retries(
    dynamodb_table, sample_receipt_line_item_analysis, mocker
):
    """Test that unprocessed items are retried when deleting multiple analyses."""
    client = DynamoClient(table_name=dynamodb_table)

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

    mock_client = mocker.patch.object(client, "_client")

    unprocessed_item = {
        "PK": {"S": f"IMAGE#{sample_receipt_line_item_analysis.image_id}"},
        "SK": {"S": "RECEIPT#3#ANALYSIS#LINE_ITEMS"},
    }

    mock_client.batch_write_item.side_effect = [
        {
            "UnprocessedItems": {
                dynamodb_table: [{"DeleteRequest": {"Key": unprocessed_item}}]
            }
        },
        {"UnprocessedItems": {}},
    ]

    client.delete_receipt_line_item_analyses(analyses)

    assert mock_client.batch_write_item.call_count == 2


@pytest.mark.integration
def test_getReceiptLineItemAnalysis_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_line_item_analysis: ReceiptLineItemAnalysis,
):
    """Test successful retrieval of a ReceiptLineItemAnalysis from DynamoDB."""
    client = DynamoClient(table_name=dynamodb_table)

    client.add_receipt_line_item_analysis(sample_receipt_line_item_analysis)

    result = client.get_receipt_line_item_analysis(
        image_id=sample_receipt_line_item_analysis.image_id,
        receipt_id=sample_receipt_line_item_analysis.receipt_id,
    )

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
    client = DynamoClient(table_name=dynamodb_table)
    valid_uuid = sample_receipt_line_item_analysis.image_id
    non_existent_receipt_id = 9999

    try:
        result = client.get_receipt_line_item_analysis(
            image_id=valid_uuid,
            receipt_id=non_existent_receipt_id,
        )
        assert result is None
    except ValueError as e:
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
    client = DynamoClient(table_name=dynamodb_table)

    mocker.patch.object(client, "_client")

    exception_type = (
        OperationError
        if image_id in ("", "invalid-uuid")
        else EntityValidationError
    )
    with pytest.raises(exception_type) as excinfo:
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
    client = DynamoClient(table_name=dynamodb_table)

    mock_client = mocker.patch.object(client, "_client")
    _set_client_error(
        mock_client, "get_item", error_code, "GetItem", error_message
    )

    with pytest.raises(
        _expected_client_exception(error_code), match=error_message
    ):
        client.get_receipt_line_item_analysis(
            image_id=sample_receipt_line_item_analysis.image_id,
            receipt_id=sample_receipt_line_item_analysis.receipt_id,
        )


@pytest.mark.integration
def test_getReceiptLineItemAnalysis_malformed_item(
    dynamodb_table, sample_receipt_line_item_analysis, mocker
):
    """Test handling of malformed items when getting a ReceiptLineItemAnalysis."""
    client = DynamoClient(table_name=dynamodb_table)

    mock_client = mocker.patch.object(client, "_client")
    mock_client.get_item.return_value = {
        "Item": {
            "PK": {"S": f"IMAGE#{sample_receipt_line_item_analysis.image_id}"},
            "SK": {
                "S": f"RECEIPT#{sample_receipt_line_item_analysis.receipt_id}#ANALYSIS#LINE_ITEMS"
            },
        }
    }

    with pytest.raises(OperationError) as excinfo:
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
    client = DynamoClient(table_name=dynamodb_table)

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

    result, last_evaluated_key = client.list_receipt_line_item_analyses()

    assert result is not None
    assert isinstance(result, list)
    assert len(result) >= 3  # At least the ones we added

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
    client = DynamoClient(table_name=dynamodb_table)

    result, last_evaluated_key = client.list_receipt_line_item_analyses()

    assert result is not None
    assert isinstance(result, list)
    assert len(result) == 0


@pytest.mark.integration
def test_listReceiptLineItemAnalyses_with_prefix(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_line_item_analysis: ReceiptLineItemAnalysis,
):
    """Test listing ReceiptLineItemAnalyses with a specific image_id."""
    client = DynamoClient(table_name=dynamodb_table)

    uuid_base = "3f52804b-2fad-4e00-92c8-b593da3a8e"
    image_id_1 = uuid_base + "d1"
    image_id_2 = uuid_base + "f1"

    analyses = []
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

    result, _ = client.list_receipt_line_item_analyses()

    assert result is not None
    assert isinstance(result, list)

    filtered_result = [a for a in result if a.image_id == image_id_1]
    assert len(filtered_result) == 3

    for analysis in filtered_result:
        assert analysis.image_id == image_id_1


@pytest.mark.integration
def test_listReceiptLineItemAnalyses_with_pagination(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_line_item_analysis: ReceiptLineItemAnalysis,
    mocker,
):
    """Test pagination in listReceiptLineItemAnalyses."""
    client = DynamoClient(table_name=dynamodb_table)

    uuid_base = "3f52804b-2fad-4e00-92c8-b593da3a8e"

    analyses = [
        ReceiptLineItemAnalysis(
            image_id=f"{uuid_base}{i:02x}",
            receipt_id=i,
            timestamp_added=sample_receipt_line_item_analysis.timestamp_added,
            items=sample_receipt_line_item_analysis.items,
            reasoning=f"Reasoning for pagination-image{i}",
            version=sample_receipt_line_item_analysis.version,
            subtotal=sample_receipt_line_item_analysis.subtotal,
            tax=sample_receipt_line_item_analysis.tax,
            total=sample_receipt_line_item_analysis.total,
        )
        for i in range(1, 11)
    ]

    client.add_receipt_line_item_analyses(analyses)

    mock_client = mocker.patch.object(client, "_client")

    first_page_items = [analysis.to_item() for analysis in analyses[:5]]
    second_page_items = [analysis.to_item() for analysis in analyses[5:]]
    last_evaluated_key = analyses[4].key

    mock_client.query.side_effect = [
        {
            "Items": first_page_items,
            "LastEvaluatedKey": last_evaluated_key,
        },
        {
            "Items": second_page_items,
        },
    ]

    result, last_key = client.list_receipt_line_item_analyses()

    assert result is not None
    assert isinstance(result, list)
    assert len(result) == 10

    assert mock_client.query.call_count == 2

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
    client = DynamoClient(table_name=dynamodb_table)

    mock_client = mocker.patch.object(client, "_client")

    with pytest.raises(EntityValidationError, match=expected_error):
        client.list_receipt_line_item_analyses(
            limit=limit, last_evaluated_key=last_evaluated_key
        )

    mock_client.query.assert_not_called()


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
    client = DynamoClient(table_name=dynamodb_table)

    mock_client = mocker.patch.object(client, "_client")

    _set_client_error(mock_client, "query", error_code, "Query", error_message)

    with pytest.raises(
        _expected_client_exception(error_code), match=error_message
    ):
        client.list_receipt_line_item_analyses()


@pytest.mark.integration
def test_listReceiptLineItemAnalysesForImage_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_line_item_analysis: ReceiptLineItemAnalysis,
):
    """Test successful listing of ReceiptLineItemAnalyses for a specific image from DynamoDB."""
    client = DynamoClient(table_name=dynamodb_table)
    image_id = sample_receipt_line_item_analysis.image_id

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

    result = client.list_receipt_line_item_analyses_for_image(
        image_id=image_id
    )

    assert result is not None
    assert isinstance(result, list)
    assert len(result) == 3

    for analysis in result:
        assert isinstance(analysis, ReceiptLineItemAnalysis)
        assert analysis.image_id == image_id


@pytest.mark.integration
def test_listReceiptLineItemAnalysesForImage_not_found(
    dynamodb_table: Literal["MyMockedTable"],
):
    """Test listing ReceiptLineItemAnalyses for a non-existent image (should return empty list)."""
    client = DynamoClient(table_name=dynamodb_table)
    non_existent_image_id = (
        "3f52804b-2fad-4e00-92c8-b593da3a8ed5"  # Valid UUID but doesn't exist
    )

    result = client.list_receipt_line_item_analyses_for_image(
        image_id=non_existent_image_id
    )

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
    client = DynamoClient(table_name=dynamodb_table)

    mocker.patch.object(client, "_client")

    exception_type = (
        EntityValidationError if image_id is None else OperationError
    )
    with pytest.raises(exception_type) as excinfo:
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
    client = DynamoClient(table_name=dynamodb_table)
    image_id = sample_receipt_line_item_analysis.image_id

    mock_client = mocker.patch.object(client, "_client")

    _set_client_error(mock_client, "query", error_code, "Query", error_message)

    with pytest.raises(
        _expected_client_exception(error_code), match=error_message
    ):
        client.list_receipt_line_item_analyses_for_image(image_id=image_id)
