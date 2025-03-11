import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional, Type
from unittest.mock import MagicMock, patch
import copy

import pytest
from botocore.exceptions import ClientError, ParamValidationError

from receipt_dynamo import ReceiptStructureAnalysis, SpatialPattern, ContentPattern, ReceiptSection
from receipt_dynamo.data.dynamo_client import DynamoClient


@pytest.fixture
def sample_receipt_structure_analysis():
    """Create a sample ReceiptStructureAnalysis instance for testing."""
    spatial_pattern = SpatialPattern(
        pattern_type="alignment",
        description="left-aligned text",
        metadata={"confidence": 0.95}
    )
    
    content_pattern = ContentPattern(
        pattern_type="format",
        description="price pattern",
        examples=["$10.99", "20.50"],
        metadata={"reliability": 0.9}
    )
    
    section = ReceiptSection(
        name="header",
        line_ids=[1, 2, 3],
        spatial_patterns=[spatial_pattern],
        content_patterns=[content_pattern],
        reasoning="This section has typical header formatting",
        metadata={"confidence": 0.85}
    )
    
    return ReceiptStructureAnalysis(
        receipt_id=123,
        image_id=str(uuid.uuid4()),
        sections=[section],
        overall_reasoning="Clear structure with distinct sections",
        version="1.0.0",
        metadata={"source": "test"},
        timestamp_added=datetime.now(timezone.utc),
        timestamp_updated=datetime.now(timezone.utc),
        processing_metrics={"processing_time_ms": 150},
        source_info={"model": "structure-analyzer-v1"},
        processing_history=[{"event": "created", "timestamp": datetime.now(timezone.utc).isoformat()}]
    )


@pytest.mark.integration
def test_addReceiptStructureAnalysis_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_structure_analysis: ReceiptStructureAnalysis,
):
    """Test successful addition of a ReceiptStructureAnalysis."""
    # Arrange
    client = DynamoClient(dynamodb_table)
    
    # Act
    client.addReceiptStructureAnalysis(sample_receipt_structure_analysis)
    
    # Assert - Verify item was added by retrieving it
    response = client._client.get_item(
        TableName=dynamodb_table,
        Key={
            "PK": {"S": f"IMAGE#{sample_receipt_structure_analysis.image_id}"},
            "SK": {"S": f"RECEIPT#{sample_receipt_structure_analysis.receipt_id}#ANALYSIS#STRUCTURE#{sample_receipt_structure_analysis.version}"},
        },
    )
    assert "Item" in response
    item = response["Item"]
    assert item["image_id"]["S"] == sample_receipt_structure_analysis.image_id
    assert int(item["receipt_id"]["N"]) == sample_receipt_structure_analysis.receipt_id
    assert item["overall_reasoning"]["S"] == sample_receipt_structure_analysis.overall_reasoning


@pytest.mark.integration
def test_addReceiptStructureAnalysis_duplicate_raises(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_structure_analysis: ReceiptStructureAnalysis,
):
    """Test adding a duplicate ReceiptStructureAnalysis raises an error."""
    # Arrange
    client = DynamoClient(dynamodb_table)
    client.addReceiptStructureAnalysis(sample_receipt_structure_analysis)
    
    # Act & Assert
    with pytest.raises(ValueError, match="already exists"):
        client.addReceiptStructureAnalysis(sample_receipt_structure_analysis)


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (None, "analysis parameter is required and cannot be None."),
        (
            "not-a-receipt-structure-analysis",
            "analysis must be an instance of the ReceiptStructureAnalysis class.",
        ),
    ],
)
def test_addReceiptStructureAnalysis_invalid_parameters(
    dynamodb_table,
    sample_receipt_structure_analysis,
    mocker,
    invalid_input,
    expected_error,
):
    """Test adding a ReceiptStructureAnalysis with invalid parameters."""
    # Arrange
    client = DynamoClient(dynamodb_table)
    mock_put_item = mocker.patch.object(client._client, "put_item")
    
    # Act & Assert
    with pytest.raises(ValueError, match=expected_error):
        client.addReceiptStructureAnalysis(invalid_input)
    
    # Verify put_item wasn't called
    mock_put_item.assert_not_called()


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
            "Could not add receipt structure analysis to DynamoDB",
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
            "Could not add receipt structure analysis to DynamoDB",
        ),
        (
            "ValidationException",
            "One or more parameters were invalid",
            "One or more parameters given were invalid",
        ),
        ("AccessDeniedException", "Access denied", "Access denied"),
    ],
)
def test_addReceiptStructureAnalysis_client_errors(
    dynamodb_table,
    sample_receipt_structure_analysis,
    mocker,
    error_code,
    error_message,
    expected_exception,
):
    """Test client errors when adding a ReceiptStructureAnalysis."""
    # Arrange
    client = DynamoClient(dynamodb_table)
    error_response = {"Error": {"Code": error_code, "Message": error_message}}
    mock_put_item = mocker.patch.object(
        client._client, "put_item", side_effect=ClientError(error_response, "PutItem")
    )
    
    # Act & Assert
    with pytest.raises(Exception, match=expected_exception):
        client.addReceiptStructureAnalysis(sample_receipt_structure_analysis)
    
    # Verify put_item was called
    mock_put_item.assert_called_once()


@pytest.mark.integration
def test_addReceiptStructureAnalyses_success(
    dynamodb_table: Literal["MyMockedTable"],
):
    """Test successful addition of multiple ReceiptStructureAnalyses."""
    # Arrange
    client = DynamoClient(dynamodb_table)
    section1 = ReceiptSection(
        name="header",
        line_ids=[1, 2],
        spatial_patterns=[],
        content_patterns=[],
        reasoning="Test section 1",
    )
    section2 = ReceiptSection(
        name="body",
        line_ids=[3, 4],
        spatial_patterns=[],
        content_patterns=[],
        reasoning="Test section 2",
    )
    
    analyses = [
        ReceiptStructureAnalysis(
            receipt_id=101,
            image_id=str(uuid.uuid4()),
            sections=[section1],
            version="1.0.0",
            overall_reasoning="Test reasoning 1",
        ),
        ReceiptStructureAnalysis(
            receipt_id=102,
            image_id=str(uuid.uuid4()),
            sections=[section2],
            version="1.0.0",
            overall_reasoning="Test reasoning 2",
        ),
    ]

    # Act
    client.addReceiptStructureAnalyses(analyses)

    # Assert - Verify items were added
    for analysis in analyses:
        response = client._client.get_item(
            TableName=dynamodb_table,
            Key={
                "PK": {"S": f"IMAGE#{analysis.image_id}"},
                "SK": {"S": f"RECEIPT#{analysis.receipt_id}#ANALYSIS#STRUCTURE#{analysis.version}"},
            },
        )
        assert "Item" in response


@pytest.mark.integration
def test_addReceiptStructureAnalyses_with_large_batch(
    dynamodb_table: Literal["MyMockedTable"],
):
    """Test adding a large batch of ReceiptStructureAnalyses."""
    # Arrange
    client = DynamoClient(dynamodb_table)
    section = ReceiptSection(
        name="test section",
        line_ids=[1, 2],
        spatial_patterns=[],
        content_patterns=[],
        reasoning="Test section",
    )
    
    analyses = [
        ReceiptStructureAnalysis(
            receipt_id=i,
            image_id=str(uuid.uuid4()),
            sections=[section],
            version="1.0.0",
            overall_reasoning=f"Test reasoning for receipt {i}",
        )
        for i in range(30)  # Create 30 analyses
    ]

    # Act
    with patch.object(client._client, "batch_write_item") as mock_batch_write:
        mock_batch_write.return_value = {"UnprocessedItems": {}}
        client.addReceiptStructureAnalyses(analyses)

        # Assert - Verify batch_write_item was called at least twice (30 items / max 25 per batch)
        assert mock_batch_write.call_count >= 2


@pytest.mark.integration
def test_addReceiptStructureAnalyses_with_unprocessed_items_retries(
    dynamodb_table, sample_receipt_structure_analysis, mocker
):
    """Test retry behavior when there are unprocessed items."""
    # Arrange
    client = DynamoClient(dynamodb_table)
    
    # Create a second analysis with different receipt_id
    second_analysis = ReceiptStructureAnalysis(
        receipt_id=456,
        image_id=sample_receipt_structure_analysis.image_id,
        sections=sample_receipt_structure_analysis.sections,
        overall_reasoning="Second analysis",
        version=sample_receipt_structure_analysis.version,
        metadata=sample_receipt_structure_analysis.metadata
    )
    analyses = [sample_receipt_structure_analysis, second_analysis]
    
    # Mock batch_write_item to return unprocessed items on first call, and none on second
    mock_batch_write_item = mocker.patch.object(client._client, "batch_write_item")
    
    # Set up side effects for the mock
    unprocessed_items = {
        dynamodb_table: [
            {"PutRequest": {"Item": second_analysis.to_item()}}
        ]
    }
    mock_batch_write_item.side_effect = [
        {"UnprocessedItems": unprocessed_items},  # First call - has unprocessed items
        {"UnprocessedItems": {}}  # Second call - all processed
    ]
    
    # Act
    client.addReceiptStructureAnalyses(analyses)
    
    # Assert - Verify batch_write_item was called twice
    assert mock_batch_write_item.call_count == 2
    
    # First call with all items
    first_call_args = mock_batch_write_item.call_args_list[0][1]
    assert len(first_call_args["RequestItems"][dynamodb_table]) == 2
    
    # Second call with only unprocessed items
    second_call_args = mock_batch_write_item.call_args_list[1][1]
    assert second_call_args["RequestItems"] == unprocessed_items


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (None, "analyses parameter is required and cannot be None."),
        ("not-a-list", "analyses must be a list of ReceiptStructureAnalysis instances."),
        (
            ["not-a-receipt-structure-analysis"],
            "All analyses must be instances of the ReceiptStructureAnalysis class.",
        ),
    ],
)
def test_addReceiptStructureAnalyses_invalid_parameters(
    dynamodb_table,
    sample_receipt_structure_analysis,
    mocker,
    invalid_input,
    expected_error,
):
    """Test adding ReceiptStructureAnalyses with invalid parameters."""
    # Arrange
    client = DynamoClient(dynamodb_table)
    mock_batch_write_item = mocker.patch.object(client._client, "batch_write_item")
    
    # Act & Assert
    with pytest.raises(ValueError, match=expected_error):
        client.addReceiptStructureAnalyses(invalid_input)
    
    # Verify batch_write_item wasn't called
    mock_batch_write_item.assert_not_called()


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_error_message",
    [
        (
            "ResourceNotFoundException",
            "Table not found",
            "Could not add ReceiptStructureAnalyses to the database",
        ),
        (
            "ProvisionedThroughputExceededException",
            "Throughput exceeded",
            "Provisioned throughput exceeded",
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
            "Could not add ReceiptStructureAnalyses to the database",
        ),
    ],
)
def test_addReceiptStructureAnalyses_client_errors(
    dynamodb_table,
    sample_receipt_structure_analysis,
    mocker,
    error_code,
    error_message,
    expected_error_message,
):
    """Test client errors when adding multiple ReceiptStructureAnalyses."""
    # Arrange
    client = DynamoClient(dynamodb_table)
    
    # Create a second analysis with different receipt_id
    second_analysis = ReceiptStructureAnalysis(
        receipt_id=456,
        image_id=sample_receipt_structure_analysis.image_id,
        sections=sample_receipt_structure_analysis.sections,
        overall_reasoning="Second analysis",
        version=sample_receipt_structure_analysis.version,
        metadata=sample_receipt_structure_analysis.metadata
    )
    analyses = [sample_receipt_structure_analysis, second_analysis]
    
    # Mock batch_write_item to raise a ClientError
    error_response = {"Error": {"Code": error_code, "Message": error_message}}
    mock_batch_write_item = mocker.patch.object(
        client._client, "batch_write_item", side_effect=ClientError(error_response, "BatchWriteItem")
    )
    
    # Act & Assert
    with pytest.raises(Exception, match=expected_error_message):
        client.addReceiptStructureAnalyses(analyses)
    
    # Verify batch_write_item was called
    mock_batch_write_item.assert_called_once()


@pytest.mark.integration
def test_updateReceiptStructureAnalysis_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_structure_analysis: ReceiptStructureAnalysis,
):
    """Test successful update of a ReceiptStructureAnalysis."""
    # Arrange
    client = DynamoClient(dynamodb_table)
    
    # First add the item to ensure it exists
    client.addReceiptStructureAnalysis(sample_receipt_structure_analysis)
    
    # Verify the item was added
    response = client._client.get_item(
        TableName=dynamodb_table,
        Key={
            "PK": {"S": f"IMAGE#{sample_receipt_structure_analysis.image_id}"},
            "SK": {"S": f"RECEIPT#{sample_receipt_structure_analysis.receipt_id}#ANALYSIS#STRUCTURE#{sample_receipt_structure_analysis.version}"},
        },
    )
    assert "Item" in response
    
    # Create an updated version
    updated_analysis = copy.deepcopy(sample_receipt_structure_analysis)
    updated_analysis.overall_reasoning = "Updated reasoning"
    
    # Act
    client.updateReceiptStructureAnalysis(updated_analysis)
    
    # Assert - Verify item was updated
    response = client._client.get_item(
        TableName=dynamodb_table,
        Key={
            "PK": {"S": f"IMAGE#{updated_analysis.image_id}"},
            "SK": {"S": f"RECEIPT#{updated_analysis.receipt_id}#ANALYSIS#STRUCTURE#{updated_analysis.version}"},
        },
    )
    assert "Item" in response
    item = response["Item"]
    assert item["overall_reasoning"]["S"] == "Updated reasoning"


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (None, "analysis parameter is required and cannot be None."),
        (
            "not a ReceiptStructureAnalysis",
            "analysis must be an instance of the ReceiptStructureAnalysis class.",
        ),
    ],
)
def test_updateReceiptStructureAnalysis_invalid_parameters(
    dynamodb_table,
    sample_receipt_structure_analysis,
    mocker,
    invalid_input,
    expected_error,
):
    """Test updating a ReceiptStructureAnalysis with invalid parameters."""
    # Arrange
    client = DynamoClient(dynamodb_table)
    mock_put_item = mocker.patch.object(client._client, "put_item")
    
    # Act & Assert
    with pytest.raises(ValueError, match=expected_error):
        client.updateReceiptStructureAnalysis(invalid_input)
    
    # Verify put_item wasn't called
    mock_put_item.assert_not_called()


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_error",
    [
        (
            "ConditionalCheckFailedException",
            "Item does not exist",
            "ReceiptStructureAnalysis for receipt",
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
            "Could not update ReceiptStructureAnalysis in the database",
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
            "Could not update ReceiptStructureAnalysis in the database",
        ),
    ],
)
def test_updateReceiptStructureAnalysis_client_errors(
    dynamodb_table,
    sample_receipt_structure_analysis,
    mocker,
    error_code,
    error_message,
    expected_error,
):
    """Test client errors when updating a ReceiptStructureAnalysis."""
    # Arrange
    client = DynamoClient(dynamodb_table)
    error_response = {"Error": {"Code": error_code, "Message": error_message}}
    mock_put_item = mocker.patch.object(
        client._client, "put_item", side_effect=ClientError(error_response, "PutItem")
    )
    
    # Act & Assert
    with pytest.raises(Exception, match=expected_error):
        client.updateReceiptStructureAnalysis(sample_receipt_structure_analysis)
    
    # Verify put_item was called
    mock_put_item.assert_called_once()


@pytest.mark.integration
def test_updateReceiptStructureAnalyses_success(
    dynamodb_table: Literal["MyMockedTable"],
):
    """Test successful update of multiple ReceiptStructureAnalyses."""
    # Arrange
    client = DynamoClient(dynamodb_table)
    section1 = ReceiptSection(
        name="header",
        line_ids=[1, 2],
        spatial_patterns=[],
        content_patterns=[],
        reasoning="Test section 1",
    )
    section2 = ReceiptSection(
        name="body",
        line_ids=[3, 4],
        spatial_patterns=[],
        content_patterns=[],
        reasoning="Test section 2",
    )
    
    analyses = [
        ReceiptStructureAnalysis(
            receipt_id=201,
            image_id=str(uuid.uuid4()),
            sections=[section1],
            version="1.0.0",
            overall_reasoning="Initial reasoning 1",
        ),
        ReceiptStructureAnalysis(
            receipt_id=202,
            image_id=str(uuid.uuid4()),
            sections=[section2],
            version="1.0.0",
            overall_reasoning="Initial reasoning 2",
        ),
    ]
    
    # Add the initial analyses
    client.addReceiptStructureAnalyses(analyses)
    
    # Create updated versions
    updated_analyses = []
    for analysis in analyses:
        updated = copy.deepcopy(analysis)
        updated.overall_reasoning = f"Updated reasoning for {analysis.receipt_id}"
        updated_analyses.append(updated)
    
    # Act
    client.updateReceiptStructureAnalyses(updated_analyses)
    
    # Assert - Verify items were updated
    for analysis in updated_analyses:
        response = client._client.get_item(
            TableName=dynamodb_table,
            Key={
                "PK": {"S": f"IMAGE#{analysis.image_id}"},
                "SK": {"S": f"RECEIPT#{analysis.receipt_id}#ANALYSIS#STRUCTURE#{analysis.version}"},
            },
        )
        assert "Item" in response
        item = response["Item"]
        assert item["overall_reasoning"]["S"] == analysis.overall_reasoning


@pytest.mark.integration
def test_updateReceiptStructureAnalyses_with_large_batch(
    dynamodb_table, sample_receipt_structure_analysis
):
    """Test updating a large batch of ReceiptStructureAnalyses (over 25 items)."""
    # Arrange
    client = DynamoClient(dynamodb_table)
    
    # Create and add 30 analyses
    analyses = []
    for i in range(30):
        analysis = ReceiptStructureAnalysis(
            receipt_id=1000 + i,
            image_id=sample_receipt_structure_analysis.image_id,
            sections=[
                ReceiptSection(
                    name=f"section-{i}",
                    line_ids=[i],
                    spatial_patterns=[],
                    content_patterns=[],
                    reasoning="Test reasoning"
                )
            ],
            overall_reasoning=f"Analysis {i}",
            version=sample_receipt_structure_analysis.version,
            metadata=sample_receipt_structure_analysis.metadata
        )
        analyses.append(analysis)
        client.addReceiptStructureAnalysis(analysis)
    
    # Update all analyses - keep the same version to ensure we can find them
    updated_analyses = []
    for i, analysis in enumerate(analyses):
        updated = ReceiptStructureAnalysis(
            receipt_id=analysis.receipt_id,
            image_id=analysis.image_id,
            sections=analysis.sections,
            overall_reasoning=f"Updated analysis {i}",
            version=analysis.version,  # Keep the same version
            metadata=analysis.metadata
        )
        updated_analyses.append(updated)
    
    # Act - Update the analyses
    client.updateReceiptStructureAnalyses(updated_analyses)
    
    # Verify some items were updated by checking them
    for idx in [0, 15, 29]:
        analysis = updated_analyses[idx]
        response = client._client.get_item(
            TableName=dynamodb_table,
            Key={
                "PK": {"S": f"IMAGE#{analysis.image_id}"},
                "SK": {"S": f"RECEIPT#{analysis.receipt_id}#ANALYSIS#STRUCTURE#{analysis.version}"},
            },
        )
        assert "Item" in response
        item = response["Item"]
        assert item["overall_reasoning"]["S"] == f"Updated analysis {idx}"


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (None, "analyses parameter is required and cannot be None"),
        ("not-a-list", "analyses must be a list of ReceiptStructureAnalysis instances"),
        (
            [123, "not-a-receipt-structure-analysis"],
            "All analyses must be instances of the ReceiptStructureAnalysis class",
        ),
    ],
)
def test_updateReceiptStructureAnalyses_invalid_inputs(
    dynamodb_table,
    sample_receipt_structure_analysis,
    mocker,
    invalid_input,
    expected_error,
):
    """Test updating ReceiptStructureAnalyses with invalid inputs."""
    # Arrange
    client = DynamoClient(dynamodb_table)
    mock_transact_write = mocker.patch.object(client._client, "transact_write_items")
    
    # Act & Assert
    with pytest.raises(ValueError, match=expected_error):
        client.updateReceiptStructureAnalyses(invalid_input)
    
    # Verify transact_write_items wasn't called
    mock_transact_write.assert_not_called()


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_error,cancellation_reasons",
    [
        (
            "ResourceNotFoundException",
            "Table not found",
            "Could not update ReceiptStructureAnalyses in the database",
            None,
        ),
        (
            "TransactionCanceledException",
            "Transaction canceled due to ConditionalCheckFailed",
            "Error updating receipt structure analyses",
            [{"Code": "ConditionalCheckFailed"}],
        ),
        (
            "InternalServerError",
            "Internal server error",
            "Internal server error",
            None,
        ),
        (
            "ProvisionedThroughputExceededException",
            "Provisioned throughput exceeded",
            "Provisioned throughput exceeded",
            None,
        ),
        (
            "ValidationException",
            "One or more parameters were invalid",
            "One or more parameters given were invalid",
            None,
        ),
        (
            "AccessDeniedException",
            "Access denied",
            "Access denied",
            None,
        ),
        (
            "UnknownError",
            "Unknown error occurred",
            "Could not update ReceiptStructureAnalyses in the database",
            None,
        ),
    ],
)
def test_updateReceiptStructureAnalyses_client_errors(
    dynamodb_table,
    sample_receipt_structure_analysis,
    mocker,
    error_code,
    error_message,
    expected_error,
    cancellation_reasons,
):
    """Test client errors when updating multiple ReceiptStructureAnalyses."""
    # Arrange
    client = DynamoClient(dynamodb_table)
    
    # Create second analysis
    second_analysis = ReceiptStructureAnalysis(
        receipt_id=456,
        image_id=sample_receipt_structure_analysis.image_id,
        sections=sample_receipt_structure_analysis.sections,
        overall_reasoning="Second analysis",
        version=sample_receipt_structure_analysis.version,
        metadata=sample_receipt_structure_analysis.metadata
    )
    
    analyses = [sample_receipt_structure_analysis, second_analysis]
    
    # Set up the mock error
    error_response = {"Error": {"Code": error_code, "Message": error_message}}
    if cancellation_reasons:
        error_response["CancellationReasons"] = cancellation_reasons
    
    mock_transact_write = mocker.patch.object(
        client._client, 
        "transact_write_items", 
        side_effect=ClientError(error_response, "TransactWriteItems")
    )
    
    # Act & Assert
    with pytest.raises(Exception, match=expected_error):
        client.updateReceiptStructureAnalyses(analyses)
    
    # Verify transact_write_items was called
    mock_transact_write.assert_called_once()


@pytest.mark.integration
def test_deleteReceiptStructureAnalysis_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_structure_analysis: ReceiptStructureAnalysis,
):
    """Test successful deletion of a ReceiptStructureAnalysis."""
    # Arrange
    client = DynamoClient(dynamodb_table)
    client.addReceiptStructureAnalysis(sample_receipt_structure_analysis)
    
    # Act
    client.deleteReceiptStructureAnalysis(
        analysis=sample_receipt_structure_analysis
    )
    
    # Assert - Verify item was deleted
    response = client._client.get_item(
        TableName=dynamodb_table,
        Key={
            "PK": {"S": f"IMAGE#{sample_receipt_structure_analysis.image_id}"},
            "SK": {"S": f"RECEIPT#{sample_receipt_structure_analysis.receipt_id}#ANALYSIS#STRUCTURE#{sample_receipt_structure_analysis.version}"},
        },
    )
    assert "Item" not in response


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (None, "analysis parameter is required and cannot be None"),
        (
            "not-a-receipt-structure-analysis",
            "analysis must be an instance of the ReceiptStructureAnalysis class",
        ),
    ],
)
def test_deleteReceiptStructureAnalysis_invalid_parameters(
    dynamodb_table,
    sample_receipt_structure_analysis,
    mocker,
    invalid_input,
    expected_error,
):
    """Test deleting a ReceiptStructureAnalysis with invalid parameters."""
    # Arrange
    client = DynamoClient(dynamodb_table)
    mock_delete_item = mocker.patch.object(client._client, "delete_item")
    
    # Act & Assert
    with pytest.raises(ValueError, match=expected_error):
        client.deleteReceiptStructureAnalysis(invalid_input)
    
    # Verify delete_item wasn't called
    mock_delete_item.assert_not_called()


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_error",
    [
        (
            "ConditionalCheckFailedException",
            "Item does not exist",
            "ReceiptStructureAnalysis for receipt",
        ),
        (
            "ResourceNotFoundException",
            "Table not found",
            "Could not delete ReceiptStructureAnalysis from the database",
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
            "ValidationException",
            "One or more parameters were invalid",
            "One or more parameters given were invalid",
        ),
        ("AccessDeniedException", "Access denied", "Access denied"),
        (
            "UnknownError",
            "Unknown error occurred",
            "Could not delete ReceiptStructureAnalysis from the database",
        ),
    ],
)
def test_deleteReceiptStructureAnalysis_client_errors(
    dynamodb_table,
    sample_receipt_structure_analysis,
    mocker,
    error_code,
    error_message,
    expected_error,
):
    """Test client errors when deleting a ReceiptStructureAnalysis."""
    # Arrange
    client = DynamoClient(dynamodb_table)
    error_response = {"Error": {"Code": error_code, "Message": error_message}}
    mock_delete_item = mocker.patch.object(
        client._client, "delete_item", side_effect=ClientError(error_response, "DeleteItem")
    )
    
    # Act & Assert
    with pytest.raises(Exception, match=expected_error):
        client.deleteReceiptStructureAnalysis(
            analysis=sample_receipt_structure_analysis
        )
    
    # Verify delete_item was called
    mock_delete_item.assert_called_once()


@pytest.mark.integration
def test_deleteReceiptStructureAnalyses_success(
    dynamodb_table: Literal["MyMockedTable"],
):
    """Test successful deletion of multiple ReceiptStructureAnalyses."""
    # Arrange
    client = DynamoClient(dynamodb_table)
    analyses = [
        ReceiptStructureAnalysis(
            receipt_id=301,
            image_id=str(uuid.uuid4()),
            sections=[
                ReceiptSection(
                    name="section-1",
                    line_ids=[1],
                    spatial_patterns=[],
                    content_patterns=[],
                    reasoning="Test reasoning"
                )
            ],
            overall_reasoning="Test reasoning for analysis 301",
            version="1.0.0",
        ),
        ReceiptStructureAnalysis(
            receipt_id=302,
            image_id=str(uuid.uuid4()),
            sections=[
                ReceiptSection(
                    name="section-2",
                    line_ids=[2],
                    spatial_patterns=[],
                    content_patterns=[],
                    reasoning="Test reasoning"
                )
            ],
            overall_reasoning="Test reasoning for analysis 302",
            version="1.0.0",
        ),
    ]
    client.addReceiptStructureAnalyses(analyses)
    
    # Act
    client.deleteReceiptStructureAnalyses(analyses)
    
    # Assert - Verify items were deleted
    for analysis in analyses:
        response = client._client.get_item(
            TableName=dynamodb_table,
            Key={
                "PK": {"S": f"IMAGE#{analysis.image_id}"},
                "SK": {"S": f"RECEIPT#{analysis.receipt_id}#ANALYSIS#STRUCTURE#{analysis.version}"},
            },
        )
        assert "Item" not in response


@pytest.mark.integration
def test_deleteReceiptStructureAnalyses_with_large_batch(dynamodb_table):
    """Test deleting a large batch of ReceiptStructureAnalyses (over 25 items)."""
    # Arrange
    client = DynamoClient(dynamodb_table)
    
    # Create and add 30 analyses
    analyses = []
    image_id = str(uuid.uuid4())
    for i in range(30):
        analysis = ReceiptStructureAnalysis(
            receipt_id=1000 + i,
            image_id=image_id,
            sections=[
                ReceiptSection(
                    name=f"section-{i}",
                    line_ids=[i],
                    spatial_patterns=[],
                    content_patterns=[],
                    reasoning="Test reasoning"
                )
            ],
            overall_reasoning=f"Analysis {i}",
            version="1.0.0"
        )
        analyses.append(analysis)
        client.addReceiptStructureAnalysis(analysis)
    
    # Act
    client.deleteReceiptStructureAnalyses(analyses)
    
    # Assert - Verify items were deleted by checking a few of them
    for idx in [0, 15, 29]:
        analysis = analyses[idx]
        response = client._client.get_item(
            TableName=dynamodb_table,
            Key={
                "PK": {"S": f"IMAGE#{analysis.image_id}"},
                "SK": {"S": f"RECEIPT#{analysis.receipt_id}#ANALYSIS#STRUCTURE#{analysis.version}"},
            },
        )
        assert "Item" not in response


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (None, "analyses parameter is required and cannot be None."),
        ("not a list", "analyses must be a list of ReceiptStructureAnalysis instances."),
        (
            [1, 2, 3],
            "All analyses must be instances of the ReceiptStructureAnalysis class.",
        ),
    ],
)
def test_deleteReceiptStructureAnalyses_invalid_parameters(
    dynamodb_table,
    mocker,
    invalid_input,
    expected_error,
):
    """Test deleting ReceiptStructureAnalyses with invalid parameters."""
    # Arrange
    client = DynamoClient(dynamodb_table)
    mock_transact_write = mocker.patch.object(client._client, "transact_write_items")
    
    # Act & Assert
    with pytest.raises(ValueError, match=expected_error):
        client.deleteReceiptStructureAnalyses(invalid_input)
    
    # Verify transact_write_items wasn't called
    mock_transact_write.assert_not_called()


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_error",
    [
        (
            "ResourceNotFoundException",
            "Table not found",
            "Could not delete ReceiptStructureAnalyses from the database",
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
            "ValidationException",
            "One or more parameters were invalid",
            "One or more parameters given were invalid",
        ),
        ("AccessDeniedException", "Access denied", "Access denied"),
        (
            "UnknownError",
            "Unknown error occurred",
            "Could not delete ReceiptStructureAnalyses from the database",
        ),
    ],
)
def test_deleteReceiptStructureAnalyses_client_errors(
    dynamodb_table,
    sample_receipt_structure_analysis,
    mocker,
    error_code,
    error_message,
    expected_error,
):
    """Test client errors when deleting multiple ReceiptStructureAnalyses."""
    # Arrange
    client = DynamoClient(dynamodb_table)
    
    # Create a second analysis
    second_analysis = ReceiptStructureAnalysis(
        receipt_id=456,
        image_id=sample_receipt_structure_analysis.image_id,
        sections=sample_receipt_structure_analysis.sections,
        overall_reasoning="Second analysis",
        version=sample_receipt_structure_analysis.version,
        metadata=sample_receipt_structure_analysis.metadata
    )
    
    analyses = [sample_receipt_structure_analysis, second_analysis]
    
    # Mock transact_write_items to raise an error
    error_response = {"Error": {"Code": error_code, "Message": error_message}}
    mock_transact_write = mocker.patch.object(
        client._client, 
        "transact_write_items", 
        side_effect=ClientError(error_response, "TransactWriteItems")
    )
    
    # Act & Assert
    with pytest.raises(Exception, match=expected_error):
        client.deleteReceiptStructureAnalyses(analyses)
    
    # Verify transact_write_items was called
    mock_transact_write.assert_called_once()


@pytest.mark.integration
def test_getReceiptStructureAnalysis_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_structure_analysis: ReceiptStructureAnalysis,
):
    """Test successful retrieval of a ReceiptStructureAnalysis."""
    # Arrange
    client = DynamoClient(dynamodb_table)
    client.addReceiptStructureAnalysis(sample_receipt_structure_analysis)
    
    # Act
    result = client.getReceiptStructureAnalysis(
        receipt_id=sample_receipt_structure_analysis.receipt_id,
        image_id=sample_receipt_structure_analysis.image_id
    )
    
    # Assert
    assert isinstance(result, ReceiptStructureAnalysis)
    assert result.receipt_id == sample_receipt_structure_analysis.receipt_id
    assert result.image_id == sample_receipt_structure_analysis.image_id
    assert result.overall_reasoning == sample_receipt_structure_analysis.overall_reasoning
    assert len(result.sections) == len(sample_receipt_structure_analysis.sections)
    assert result.version == sample_receipt_structure_analysis.version


@pytest.mark.integration
def test_getReceiptStructureAnalysis_not_found(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_structure_analysis: ReceiptStructureAnalysis,
):
    """Test getting a non-existent ReceiptStructureAnalysis."""
    # Arrange
    client = DynamoClient(dynamodb_table)
    
    # Act & Assert
    with pytest.raises(ValueError, match="No ReceiptStructureAnalysis found for receipt"):
        client.getReceiptStructureAnalysis(
            receipt_id=999,  # Non-existent receipt_id
            image_id=sample_receipt_structure_analysis.image_id
        )


@pytest.mark.integration
@pytest.mark.parametrize(
    "param_name,invalid_value,expected_error,sample_override",
    [
        # Invalid type tests
        ("receipt_id", "not-an-integer", "receipt_id must be an integer", {}),
        ("image_id", 123, "image_id must be a string", {}),
        ("image_id", "not-a-valid-uuid", "Invalid image_id format", {}),
    ],
)
def test_getReceiptStructureAnalysis_invalid_parameters(
    dynamodb_table,
    sample_receipt_structure_analysis,
    mocker,
    param_name,
    invalid_value,
    expected_error,
    sample_override,
):
    """Test getting a ReceiptStructureAnalysis with invalid parameters."""
    # Arrange
    client = DynamoClient(dynamodb_table)
    mock_get_item = mocker.patch.object(client._client, "get_item")
    
    # Prepare params
    params = {
        "receipt_id": sample_receipt_structure_analysis.receipt_id,
        "image_id": sample_receipt_structure_analysis.image_id,
    }
    params.update(sample_override)
    params[param_name] = invalid_value
    
    # Act & Assert
    with pytest.raises(Exception, match=expected_error):
        client.getReceiptStructureAnalysis(**params)
    
    # Verify get_item wasn't called
    mock_get_item.assert_not_called()


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_error",
    [
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
            "ValidationException",
            "One or more parameters were invalid",
            "One or more parameters given were invalid",
        ),
        ("AccessDeniedException", "Access denied", "Access denied"),
        (
            "ResourceNotFoundException",
            "Table not found",
            "Could not get ReceiptStructureAnalysis from the database",
        ),
    ],
)
def test_getReceiptStructureAnalysis_client_errors(
    dynamodb_table,
    sample_receipt_structure_analysis,
    mocker,
    error_code,
    error_message,
    expected_error,
):
    """Test client errors when getting a ReceiptStructureAnalysis."""
    # Arrange
    client = DynamoClient(dynamodb_table)
    error_response = {"Error": {"Code": error_code, "Message": error_message}}
    mock_query = mocker.patch.object(
        client._client, "query", side_effect=ClientError(error_response, "Query")
    )
    
    # Act & Assert
    with pytest.raises(Exception, match=expected_error):
        client.getReceiptStructureAnalysis(
            receipt_id=sample_receipt_structure_analysis.receipt_id,
            image_id=sample_receipt_structure_analysis.image_id
        )
    
    # Verify query was called
    mock_query.assert_called_once()


@pytest.mark.integration
def test_listReceiptStructureAnalyses_success(dynamodb_table: Literal["MyMockedTable"]):
    """Test successful listing of ReceiptStructureAnalyses."""
    # Arrange
    client = DynamoClient(dynamodb_table)
    image_id = str(uuid.uuid4())
    
    # Create and add 5 analyses
    analyses = []
    for i in range(5):
        analysis = ReceiptStructureAnalysis(
            receipt_id=1000 + i,
            image_id=image_id,
            sections=[
                ReceiptSection(
                    name=f"section-{i}",
                    line_ids=[i],
                    spatial_patterns=[],
                    content_patterns=[],
                    reasoning="Test reasoning"
                )
            ],
            overall_reasoning=f"Analysis {i}",
            version="1.0.0"
        )
        analyses.append(analysis)
        client.addReceiptStructureAnalysis(analysis)
    
    # Act
    result_analyses, last_evaluated_key = client.listReceiptStructureAnalyses()
    
    # Assert
    assert last_evaluated_key is None
    assert len(result_analyses) == 5
    
    # Verify all expected analyses are in the results
    receipt_ids = [analysis.receipt_id for analysis in result_analyses]
    for analysis in analyses:
        assert analysis.receipt_id in receipt_ids


@pytest.mark.integration
def test_listReceiptStructureAnalyses_with_limit(
    dynamodb_table: Literal["MyMockedTable"],
):
    """Test listing ReceiptStructureAnalyses with a limit."""
    # Arrange
    client = DynamoClient(dynamodb_table)
    image_id = str(uuid.uuid4())
    
    # Create and add 5 analyses
    analyses = []
    for i in range(5):
        analysis = ReceiptStructureAnalysis(
            receipt_id=1000 + i,
            image_id=image_id,
            sections=[
                ReceiptSection(
                    name=f"section-{i}",
                    line_ids=[i],
                    spatial_patterns=[],
                    content_patterns=[],
                    reasoning="Test reasoning"
                )
            ],
            overall_reasoning=f"Analysis {i}",
            version="1.0.0"
        )
        analyses.append(analysis)
        client.addReceiptStructureAnalysis(analysis)
    
    # Act
    result_analyses, last_evaluated_key = client.listReceiptStructureAnalyses(limit=2)
    
    # Assert
    assert last_evaluated_key is not None
    assert len(result_analyses) == 2
    
    # Get the next page
    next_page_analyses, next_last_evaluated_key = client.listReceiptStructureAnalyses(
        limit=2, lastEvaluatedKey=last_evaluated_key
    )
    
    # Assert second page
    assert len(next_page_analyses) == 2
    assert next_last_evaluated_key is not None
    
    # Get the final page
    final_page_analyses, final_last_evaluated_key = client.listReceiptStructureAnalyses(
        limit=2, lastEvaluatedKey=next_last_evaluated_key
    )
    
    # Assert final page
    assert len(final_page_analyses) == 1
    assert final_last_evaluated_key is None
    
    # Verify all analyses are different
    all_analyses = result_analyses + next_page_analyses + final_page_analyses
    assert len(all_analyses) == 5
    receipt_ids = [analysis.receipt_id for analysis in all_analyses]
    assert len(set(receipt_ids)) == 5


@pytest.mark.integration
def test_listReceiptStructureAnalyses_multiple_pages(dynamodb_table, mocker):
    """Test listing multiple pages of ReceiptStructureAnalyses automatically."""
    # Arrange
    client = DynamoClient(dynamodb_table)
    
    # Create mock query responses for pagination
    mock_query = mocker.patch.object(client._client, "query")
    
    # Prepare two pages of results
    page1_items = [
        {
            "PK": {"S": "IMAGE#image1"},
            "SK": {"S": "RECEIPT#1001#ANALYSIS#STRUCTURE"},
            "image_id": {"S": "image1"},
            "receipt_id": {"N": "1001"},
            "overall_reasoning": {"S": "Analysis 1"},
            "sections": {"L": []}
        },
        {
            "PK": {"S": "IMAGE#image1"},
            "SK": {"S": "RECEIPT#1002#ANALYSIS#STRUCTURE"},
            "image_id": {"S": "image1"},
            "receipt_id": {"N": "1002"},
            "overall_reasoning": {"S": "Analysis 2"},
            "sections": {"L": []}
        }
    ]
    
    page2_items = [
        {
            "PK": {"S": "IMAGE#image1"},
            "SK": {"S": "RECEIPT#1003#ANALYSIS#STRUCTURE"},
            "image_id": {"S": "image1"},
            "receipt_id": {"N": "1003"},
            "overall_reasoning": {"S": "Analysis 3"},
            "sections": {"L": []}
        }
    ]
    
    # Set up the mock side effects for pagination
    mock_query.side_effect = [
        {
            "Items": page1_items,
            "LastEvaluatedKey": {"PK": {"S": "IMAGE#image1"}, "SK": {"S": "RECEIPT#1002#ANALYSIS#STRUCTURE"}}
        },
        {
            "Items": page2_items,
            "Count": 1
        }
    ]
    
    # Act
    result_analyses, last_evaluated_key = client.listReceiptStructureAnalyses()
    
    # Assert
    assert mock_query.call_count == 2
    assert len(result_analyses) == 3
    assert last_evaluated_key is None
    
    # Check first pagination call
    first_call_args = mock_query.call_args_list[0][1]
    assert first_call_args["TableName"] == dynamodb_table
    assert first_call_args["IndexName"] == "GSI1"
    assert "ExclusiveStartKey" not in first_call_args
    
    # Check second pagination call
    second_call_args = mock_query.call_args_list[1][1]
    assert second_call_args["TableName"] == dynamodb_table
    assert second_call_args["IndexName"] == "GSI1"
    assert "ExclusiveStartKey" in second_call_args


@pytest.mark.integration
@pytest.mark.parametrize(
    "param_name,invalid_value,expected_error,expected_exception",
    [
        ("limit", "invalid", "limit must be an integer or None", ValueError),
        (
            "lastEvaluatedKey",
            "invalid",
            "lastEvaluatedKey must be a dictionary or None",
            ValueError,
        ),
        ("limit", -1, "Parameter validation failed", ParamValidationError),
        ("limit", 0, "Parameter validation failed", ParamValidationError),
        (
            "lastEvaluatedKey",
            [],
            "lastEvaluatedKey must be a dictionary or None",
            ValueError,
        ),
        (
            "lastEvaluatedKey",
            123,
            "lastEvaluatedKey must be a dictionary or None",
            ValueError,
        ),
    ],
)
def test_listReceiptStructureAnalyses_invalid_parameters(
    dynamodb_table: Literal["MyMockedTable"],
    param_name: str,
    invalid_value: Any,
    expected_error: str,
    expected_exception: Type[Exception],
):
    """Test listing ReceiptStructureAnalyses with invalid parameters."""
    # Arrange
    client = DynamoClient(dynamodb_table)
    
    # Prepare kwargs
    kwargs = {}
    kwargs[param_name] = invalid_value
    
    # Act & Assert
    with pytest.raises(expected_exception, match=expected_error):
        client.listReceiptStructureAnalyses(**kwargs)


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_error,cancellation_reasons",
    [
        (
            "ResourceNotFoundException",
            "Table not found",
            "Could not list ReceiptStructureAnalyses from the database",
            None,
        ),
        (
            "ValidationException",
            "One or more parameters were invalid",
            "One or more parameters given were invalid",
            None,
        ),
        (
            "InternalServerError",
            "Internal server error",
            "Internal server error",
            None,
        ),
        (
            "ProvisionedThroughputExceededException",
            "Throughput exceeded",
            "Provisioned throughput exceeded",
            None,
        ),
        (
            "AccessDeniedException",
            "Access denied",
            "Access denied",
            None,
        ),
        (
            "UnknownError",
            "Unknown error occurred",
            "Could not list ReceiptStructureAnalyses from the database",
            None,
        ),
    ],
)
def test_listReceiptStructureAnalyses_client_errors(
    dynamodb_table: Literal["MyMockedTable"],
    mocker,
    error_code: str,
    error_message: str,
    expected_error: str,
    cancellation_reasons: Optional[List[Dict[str, str]]],
):
    """Test client errors when listing ReceiptStructureAnalyses."""
    # Arrange
    client = DynamoClient(dynamodb_table)
    
    # Mock query to raise a ClientError
    error_response = {"Error": {"Code": error_code, "Message": error_message}}
    if cancellation_reasons:
        error_response["CancellationReasons"] = cancellation_reasons
        
    mock_query = mocker.patch.object(
        client._client, "query", side_effect=ClientError(error_response, "Query")
    )
    
    # Act & Assert
    with pytest.raises(Exception, match=expected_error):
        client.listReceiptStructureAnalyses()
    
    # Verify query was called
    mock_query.assert_called_once()


@pytest.mark.integration
def test_listReceiptStructureAnalysesFromReceipt_success(
    dynamodb_table: Literal["MyMockedTable"],
):
    """Test successful listing of ReceiptStructureAnalyses for a receipt."""
    # Arrange
    client = DynamoClient(dynamodb_table)
    image_id = str(uuid.uuid4())
    receipt_id = 123
    
    # Create and add 3 analyses for the same receipt and image
    analyses = []
    for i in range(3):
        analysis = ReceiptStructureAnalysis(
            receipt_id=receipt_id,
            image_id=image_id,
            sections=[
                ReceiptSection(
                    name=f"section-{i}",
                    line_ids=[i],
                    spatial_patterns=[],
                    content_patterns=[],
                    reasoning="Test reasoning"
                )
            ],
            overall_reasoning=f"Analysis version {i}",
            version=f"1.0.{i}"
        )
        analyses.append(analysis)
        client.addReceiptStructureAnalysis(analysis)
    
    # Also add one analysis for a different receipt to make sure it's not returned
    different_receipt_analysis = ReceiptStructureAnalysis(
        receipt_id=456,
        image_id=image_id,
        sections=[
            ReceiptSection(
                name="different-section",
                line_ids=[1],
                spatial_patterns=[],
                content_patterns=[],
                reasoning="Different reasoning"
            )
        ],
        overall_reasoning="Different analysis",
        version="1.0.0"
    )
    client.addReceiptStructureAnalysis(different_receipt_analysis)
    
    # Act
    result = client.listReceiptStructureAnalysesFromReceipt(receipt_id=receipt_id, image_id=image_id)
    
    # Assert
    assert len(result) == 3  # Should return all analyses for the receipt
    
    # Verify that all analyses for the specified receipt are included
    for analysis in result:
        assert analysis.receipt_id == receipt_id
        assert analysis.image_id == image_id
        assert analysis.overall_reasoning.startswith("Analysis version")
    
    # Verify that the different receipt analysis is not included
    different_receipt_ids = [a.receipt_id for a in result]
    assert 456 not in different_receipt_ids


@pytest.mark.integration
def test_listReceiptStructureAnalysesFromReceipt_returns_empty_list_when_not_found(
    dynamodb_table: Literal["MyMockedTable"],
):
    """Test listing ReceiptStructureAnalyses for a receipt that doesn't exist."""
    # Arrange
    client = DynamoClient(dynamodb_table)
    image_id = str(uuid.uuid4())
    
    # Act
    result = client.listReceiptStructureAnalysesFromReceipt(receipt_id=999, image_id=image_id)
    
    # Assert
    assert isinstance(result, list)
    assert len(result) == 0


@pytest.mark.integration
def test_listReceiptStructureAnalysesFromReceipt_with_pagination(
    dynamodb_table: Literal["MyMockedTable"], mocker
):
    """Test listing ReceiptStructureAnalyses with pagination."""
    # Arrange
    client = DynamoClient(dynamodb_table)
    image_id = str(uuid.uuid4())
    receipt_id = 123
    
    # Mock the query method to simulate pagination
    mock_query = mocker.patch.object(client._client, "query")
    
    # Prepare two pages of results
    page1_items = [
        {
            "PK": {"S": f"IMAGE#{image_id}"},
            "SK": {"S": f"RECEIPT#{receipt_id}#ANALYSIS#STRUCTURE"},
            "image_id": {"S": image_id},
            "receipt_id": {"N": str(receipt_id)},
            "overall_reasoning": {"S": "Analysis 1"},
            "sections": {"L": []},
            "version": {"S": "1.0.1"}
        }
    ]
    
    page2_items = [
        {
            "PK": {"S": f"IMAGE#{image_id}"},
            "SK": {"S": f"RECEIPT#{receipt_id}#ANALYSIS#STRUCTURE"},
            "image_id": {"S": image_id},
            "receipt_id": {"N": str(receipt_id)},
            "overall_reasoning": {"S": "Analysis 2"},
            "sections": {"L": []},
            "version": {"S": "1.0.2"}
        }
    ]
    
    # Set up the mock side effects for pagination
    mock_query.side_effect = [
        {
            "Items": page1_items,
            "LastEvaluatedKey": {"PK": {"S": f"IMAGE#{image_id}"}, "SK": {"S": f"RECEIPT#{receipt_id}#ANALYSIS#STRUCTURE"}}
        },
        {
            "Items": page2_items,
            "Count": 1
        }
    ]
    
    # Act
    result = client.listReceiptStructureAnalysesFromReceipt(receipt_id=receipt_id, image_id=image_id)
    
    # Assert
    assert mock_query.call_count == 2
    assert len(result) == 2
    
    # Check the query was properly constructed
    first_call_args = mock_query.call_args_list[0][1]
    assert first_call_args["TableName"] == dynamodb_table
    assert first_call_args["IndexName"] == "GSI2"
    assert first_call_args["ExpressionAttributeValues"][":g2pk"]["S"] == "RECEIPT"
    assert first_call_args["ExpressionAttributeValues"][":g2sk_prefix"]["S"].startswith(f"IMAGE#{image_id}#RECEIPT#{receipt_id}")


@pytest.mark.integration
@pytest.mark.parametrize(
    "param_name,invalid_value,expected_error",
    [
        # Invalid type tests
        (
            "receipt_id",
            "not_an_integer",
            "receipt_id must be an integer",
        ),
        (
            "image_id",
            "not_a_valid_uuid",
            "Invalid image_id format",
        ),
        (
            "image_id",
            123,
            "image_id must be a string",
        ),
    ],
)
def test_listReceiptStructureAnalysesFromReceipt_invalid_parameters(
    dynamodb_table: Literal["MyMockedTable"],
    param_name: str,
    invalid_value: Any,
    expected_error: str,
):
    """Test listing ReceiptStructureAnalyses with invalid parameters."""
    # Arrange
    client = DynamoClient(dynamodb_table)
    
    # Prepare params
    params = {
        "receipt_id": 123,
        "image_id": str(uuid.uuid4())
    }
    params[param_name] = invalid_value
    
    # Act & Assert
    with pytest.raises(Exception, match=expected_error):
        client.listReceiptStructureAnalysesFromReceipt(**params)


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_error",
    [
        (
            "ResourceNotFoundException",
            "Table not found",
            "Could not list ReceiptStructureAnalyses for the receipt",
        ),
        (
            "ProvisionedThroughputExceededException",
            "Throughput exceeded",
            "Provisioned throughput exceeded",
        ),
        (
            "ValidationException",
            "Invalid parameters",
            "One or more parameters given were invalid",
        ),
        (
            "InternalServerError",
            "Internal server error",
            "Internal server error",
        ),
        (
            "AccessDeniedException",
            "Access denied",
            "Access denied",
        ),
        (
            "UnknownError",
            "Unknown error occurred",
            "Could not list ReceiptStructureAnalyses for the receipt",
        ),
    ],
)
def test_listReceiptStructureAnalysesFromReceipt_client_errors(
    dynamodb_table: Literal["MyMockedTable"],
    mocker,
    error_code: str,
    error_message: str,
    expected_error: str,
):
    """Test client errors when listing ReceiptStructureAnalyses for a receipt."""
    # Arrange
    client = DynamoClient(dynamodb_table)
    
    # Mock query to raise a ClientError
    error_response = {"Error": {"Code": error_code, "Message": error_message}}
    mock_query = mocker.patch.object(
        client._client, "query", side_effect=ClientError(error_response, "Query")
    )
    
    # Act & Assert
    with pytest.raises(Exception, match=expected_error):
        client.listReceiptStructureAnalysesFromReceipt(receipt_id=123, image_id=str(uuid.uuid4()))
    
    # Verify query was called
    mock_query.assert_called_once()
