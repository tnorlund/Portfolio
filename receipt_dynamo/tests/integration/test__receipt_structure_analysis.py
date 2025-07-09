import copy
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional, Type
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError, ParamValidationError

from receipt_dynamo import (
    ContentPattern,
    ReceiptStructureAnalysis,
    SpatialPattern,
)
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.entities.receipt_structure_analysis import ReceiptSection


@pytest.fixture
def sample_receipt_structure_analysis():
    """Create a sample ReceiptStructureAnalysis instance for testing."""
    spatial_pattern = SpatialPattern(
        pattern_type="alignment",
        description="left-aligned text",
        metadata={"confidence": 0.95},
    )

    content_pattern = ContentPattern(
        pattern_type="format",
        description="price pattern",
        examples=["$10.99", "20.50"],
        metadata={"reliability": 0.9},
    )

    section = ReceiptSection(
        name="header",
        line_ids=[1, 2, 3],
        spatial_patterns=[spatial_pattern],
        content_patterns=[content_pattern],
        reasoning="This section has typical header formatting",
        metadata={"confidence": 0.85},
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
        processing_history=[
            {
                "event": "created",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        ],
    )


@pytest.mark.integration
def test_addReceiptStructureAnalysis_success(
    dynamodb_table: Literal["MyMockedTable"],
):
    """Test successful addition of a ReceiptStructureAnalysis."""
    # Arrange
    client = DynamoClient(dynamodb_table)
    analysis = ReceiptStructureAnalysis(
        receipt_id=123,
        image_id=str(uuid.uuid4()),
        sections=[
            ReceiptSection(
                name="section-1",
                line_ids=[1],
                spatial_patterns=[],
                content_patterns=[],
                reasoning="Test reasoning",
            )
        ],
        overall_reasoning="Test analysis",
        version="1.0.0",
    )

    # Act
    client.add_receipt_structure_analysis(analysis)

    # Assert - Verify the item was added by checking it
    response = client._client.get_item(
        TableName=dynamodb_table,
        Key={
            "PK": {"S": f"IMAGE#{analysis.image_id}"},
            "SK": {
                "S": f"RECEIPT#{analysis.receipt_id:05d}#ANALYSIS#STRUCTURE#{analysis.version}"
            },
        },
    )
    assert "Item" in response, "Item should exist in DynamoDB"


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_error",
    [
        (
            "ResourceNotFoundException",
            "Table not found",
            "Table not found for operation add_receipt_structure_analysis",
        ),
        (
            "UnknownError",
            "Unknown error",
            "Unknown error in add_receipt_structure_analysis",
        ),
    ],
)
def test_addReceiptStructureAnalysis_client_errors(
    dynamodb_table,
    sample_receipt_structure_analysis,
    mocker,
    error_code,
    error_message,
    expected_error,
):
    """Test client errors when adding a ReceiptStructureAnalysis."""
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Mock put_item to raise an error
    error_response = {"Error": {"Code": error_code, "Message": error_message}}
    mock_put_item = mocker.patch.object(
        client._client,
        "put_item",
        side_effect=ClientError(error_response, "PutItem"),
    )

    # Act & Assert
    with pytest.raises(Exception, match=re.escape(expected_error)):
        client.add_receipt_structure_analysis(
            sample_receipt_structure_analysis
        )

    # Verify put_item was called
    mock_put_item.assert_called_once()


@pytest.mark.integration
def test_addReceiptStructureAnalyses_success(
    dynamodb_table: Literal["MyMockedTable"],
):
    """Test successful addition of multiple ReceiptStructureAnalyses."""
    # Arrange
    client = DynamoClient(dynamodb_table)
    image_id = str(uuid.uuid4())

    # Create 3 analyses
    analyses = []
    for i in range(3):
        analysis = ReceiptStructureAnalysis(
            receipt_id=i + 1,
            image_id=image_id,
            sections=[
                ReceiptSection(
                    name=f"section-{i}",
                    line_ids=[i],
                    spatial_patterns=[],
                    content_patterns=[],
                    reasoning="Test reasoning",
                )
            ],
            overall_reasoning=f"Analysis {i}",
            version="1.0.0",
        )
        analyses.append(analysis)

    # Act
    client.add_receipt_structure_analyses(analyses)

    # Assert - Verify some items were added by checking them
    for idx in [0, 1, 2]:
        analysis = analyses[idx]
        response = client._client.get_item(
            TableName=dynamodb_table,
            Key={
                "PK": {"S": f"IMAGE#{analysis.image_id}"},
                "SK": {
                    "S": f"RECEIPT#{analysis.receipt_id:05d}#ANALYSIS#STRUCTURE#{analysis.version}"
                },
            },
        )
        assert "Item" in response, f"Item {idx} should exist in DynamoDB"


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_error",
    [
        (
            "ResourceNotFoundException",
            "Table not found",
            "Table not found for operation add_receipt_structure_analyses",
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
            "Unknown error in add_receipt_structure_analyses",
        ),
    ],
)
def test_addReceiptStructureAnalyses_client_errors(
    dynamodb_table,
    sample_receipt_structure_analysis,
    mocker,
    error_code,
    error_message,
    expected_error,
):
    """Test client errors when adding multiple ReceiptStructureAnalyses."""
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Create a second analysis
    second_analysis = ReceiptStructureAnalysis(
        receipt_id=456,
        image_id=sample_receipt_structure_analysis.image_id,
        sections=sample_receipt_structure_analysis.sections,
        overall_reasoning="Second analysis",
        version=sample_receipt_structure_analysis.version,
        metadata=sample_receipt_structure_analysis.metadata,
    )

    analyses = [sample_receipt_structure_analysis, second_analysis]

    # Mock batch_write_item to raise an error
    error_response = {"Error": {"Code": error_code, "Message": error_message}}
    mock_batch_write = mocker.patch.object(
        client._client,
        "batch_write_item",
        side_effect=ClientError(error_response, "BatchWriteItem"),
    )

    # Act & Assert
    with pytest.raises(Exception, match=re.escape(expected_error)):
        client.add_receipt_structure_analyses(analyses)

    # Verify batch_write_item was called
    mock_batch_write.assert_called_once()


@pytest.mark.integration
def test_updateReceiptStructureAnalysis_success(
    dynamodb_table: Literal["MyMockedTable"],
):
    """Test successful update of a ReceiptStructureAnalysis."""
    # Arrange
    client = DynamoClient(dynamodb_table)
    analysis = ReceiptStructureAnalysis(
        receipt_id=123,
        image_id=str(uuid.uuid4()),
        sections=[
            ReceiptSection(
                name="section-1",
                line_ids=[1],
                spatial_patterns=[],
                content_patterns=[],
                reasoning="Test reasoning",
            )
        ],
        overall_reasoning="Test analysis",
        version="1.0.0",
    )

    # Add the analysis first
    client.add_receipt_structure_analysis(analysis)

    # Update the analysis
    analysis.overall_reasoning = "Updated analysis"
    client.update_receipt_structure_analysis(analysis)

    # Assert - Verify the item was updated by checking it
    response = client._client.get_item(
        TableName=dynamodb_table,
        Key={
            "PK": {"S": f"IMAGE#{analysis.image_id}"},
            "SK": {
                "S": f"RECEIPT#{analysis.receipt_id:05d}#ANALYSIS#STRUCTURE#{analysis.version}"
            },
        },
    )
    assert "Item" in response, "Item should exist in DynamoDB"
    assert response["Item"]["overall_reasoning"]["S"] == "Updated analysis"


@pytest.mark.integration
def test_updateReceiptStructureAnalyses_success(
    dynamodb_table: Literal["MyMockedTable"],
):
    """Test successful update of multiple ReceiptStructureAnalyses."""
    # Arrange
    client = DynamoClient(dynamodb_table)
    image_id = str(uuid.uuid4())

    # Create and add 3 analyses
    analyses = []
    for i in range(3):
        analysis = ReceiptStructureAnalysis(
            receipt_id=i + 1,
            image_id=image_id,
            sections=[
                ReceiptSection(
                    name=f"section-{i}",
                    line_ids=[i],
                    spatial_patterns=[],
                    content_patterns=[],
                    reasoning="Test reasoning",
                )
            ],
            overall_reasoning=f"Analysis {i}",
            version="1.0.0",
        )
        analyses.append(analysis)
        client.add_receipt_structure_analysis(analysis)

    # Update the analyses
    updated_analyses = []
    for analysis in analyses:
        analysis.overall_reasoning = f"Updated {analysis.overall_reasoning}"
        updated_analyses.append(analysis)

    # Act - Update the analyses
    client.update_receipt_structure_analyses(updated_analyses)

    # Assert - Verify some items were updated by checking them
    for idx in [0, 1, 2]:
        analysis = updated_analyses[idx]
        response = client._client.get_item(
            TableName=dynamodb_table,
            Key={
                "PK": {"S": f"IMAGE#{analysis.image_id}"},
                "SK": {
                    "S": f"RECEIPT#{analysis.receipt_id:05d}#ANALYSIS#STRUCTURE#{analysis.version}"
                },
            },
        )
        assert "Item" in response, f"Item {idx} should exist in DynamoDB"
        assert (
            response["Item"]["overall_reasoning"]["S"]
            == f"Updated Analysis {idx}"
        )


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_error,cancellation_reasons",
    [
        (
            "ResourceNotFoundException",
            "Table not found",
            "Table not found for operation update_receipt_structure_analyses",
            None,
        ),
        (
            "TransactionCanceledException",
            "Transaction canceled due to ConditionalCheckFailed",
            "One or more entities do not exist or conditions failed",
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
            "Unknown error in update_receipt_structure_analyses",
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
        metadata=sample_receipt_structure_analysis.metadata,
    )

    analyses = [sample_receipt_structure_analysis, second_analysis]

    # Set up the mock error
    error_response = {"Error": {"Code": error_code, "Message": error_message}}
    if cancellation_reasons:
        error_response["CancellationReasons"] = cancellation_reasons

    mock_batch_write = mocker.patch.object(
        client._client,
        "batch_write_item",
        side_effect=ClientError(error_response, "BatchWriteItem"),
    )

    # Act & Assert
    with pytest.raises(Exception, match=re.escape(expected_error)):
        client.update_receipt_structure_analyses(analyses)

    # Verify batch_write_item was called
    mock_batch_write.assert_called_once()


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
                    reasoning="Test reasoning",
                )
            ],
            overall_reasoning=f"Analysis version {i}",
            version=f"1.0.{i}",
            timestamp_added=datetime(
                2023, 1, 1, 12, 0, i
            ),  # Different timestamps
            timestamp_updated=datetime(
                2023, 1, 2, 12, 0, i
            ),  # Different timestamps
        )
        analyses.append(analysis)

    # Clean up any existing analyses
    existing_analyses = client.list_receipt_structure_analyses_from_receipt(
        receipt_id=receipt_id, image_id=image_id
    )
    if existing_analyses:
        client.delete_receipt_structure_analyses(existing_analyses)

    # Add analyses using batch operation as originally intended
    client.add_receipt_structure_analyses(analyses)

    # Act
    result = client.list_receipt_structure_analyses_from_receipt(
        receipt_id=receipt_id, image_id=image_id
    )

    # Assert
    assert len(result) == 3, "Should return all 3 analyses"
    versions = sorted([analysis.version for analysis in result])
    assert versions == ["1.0.0", "1.0.1", "1.0.2"], "Should have all versions"
    for i, analysis in enumerate(sorted(result, key=lambda x: x.version)):
        assert analysis.receipt_id == receipt_id
        assert analysis.image_id == image_id
        assert analysis.version == f"1.0.{i}"
        assert analysis.overall_reasoning == f"Analysis version {i}"
