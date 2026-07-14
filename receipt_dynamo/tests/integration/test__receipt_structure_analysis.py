import re
import uuid
from datetime import datetime, timezone
from typing import Any, Literal, Mapping

import pytest
from botocore.exceptions import ClientError

from receipt_dynamo import (
    ContentPattern,
    ReceiptStructureAnalysis,
    SpatialPattern,
)
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.data.shared_exceptions import (
    DynamoDBError,
    DynamoDBServerError,
    DynamoDBThroughputError,
    EntityValidationError,
    OperationError,
)
from receipt_dynamo.entities.receipt_structure_analysis import ReceiptSection

# This entity is not used in production infrastructure
pytestmark = [pytest.mark.integration, pytest.mark.unused_in_production]


def _make_analysis(
    *,
    receipt_id: int,
    image_id: str,
    section_index: int,
    overall_reasoning: str,
    version: str = "1.0.0",
    timestamp_second: int | None = None,
) -> ReceiptStructureAnalysis:
    """Build a minimal analysis for persistence tests."""
    analysis = ReceiptStructureAnalysis(
        receipt_id=receipt_id,
        image_id=image_id,
        sections=[
            ReceiptSection(
                name=f"section-{section_index}",
                line_ids=[section_index],
                spatial_patterns=[],
                content_patterns=[],
                reasoning="Test reasoning",
            )
        ],
        overall_reasoning=overall_reasoning,
        version=version,
    )
    if timestamp_second is not None:
        analysis.timestamp_added = datetime(
            2023, 1, 1, 12, 0, timestamp_second
        )
        analysis.timestamp_updated = datetime(
            2023, 1, 2, 12, 0, timestamp_second
        )
    return analysis


def _make_related_analysis(
    analysis: ReceiptStructureAnalysis,
) -> ReceiptStructureAnalysis:
    """Build a second analysis sharing the sample's image and metadata."""
    return ReceiptStructureAnalysis(
        receipt_id=456,
        image_id=analysis.image_id,
        sections=analysis.sections,
        overall_reasoning="Second analysis",
        version=analysis.version,
        metadata=analysis.metadata,
    )


def _get_stored_item(
    client: DynamoClient,
    table_name: str,
    analysis: ReceiptStructureAnalysis,
) -> Mapping[str, Any]:
    """Fetch the DynamoDB item for an analysis."""
    return client._client.get_item(
        TableName=table_name,
        Key={
            "PK": {"S": f"IMAGE#{analysis.image_id}"},
            "SK": {
                "S": (
                    f"RECEIPT#{analysis.receipt_id:05d}"
                    f"#ANALYSIS#STRUCTURE#{analysis.version}"
                )
            },
        },
    )


def _expected_client_error(
    operation: str, error_code: str, error_message: str
) -> str:
    """Return the complete domain error message for a client failure."""
    if error_code == "ResourceNotFoundException":
        return (
            f"DynamoDB resource not found during {operation}: {error_message}"
        )
    if error_code == "ValidationException":
        return f"Validation error: {error_message}"
    if error_code == "ProvisionedThroughputExceededException":
        return f"Throughput exceeded for {operation}: {error_message}"
    if error_code == "InternalServerError":
        return f"DynamoDB server error during {operation}: {error_message}"
    return f"DynamoDB error during {operation}: {error_code} - {error_message}"


@pytest.fixture
def sample_receipt_structure_analysis() -> ReceiptStructureAnalysis:
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


def test_addReceiptStructureAnalysis_success(
    dynamodb_table: Literal["MyMockedTable"],
):
    """Test successful addition of a ReceiptStructureAnalysis."""
    client = DynamoClient(dynamodb_table)
    analysis = _make_analysis(
        receipt_id=123,
        image_id=str(uuid.uuid4()),
        section_index=1,
        overall_reasoning="Test analysis",
    )

    client.add_receipt_structure_analysis(analysis)

    response = _get_stored_item(client, dynamodb_table, analysis)
    assert "Item" in response, "Item should exist in DynamoDB"


@pytest.mark.parametrize(
    "error_code,error_message,expected_exception",
    [
        ("ResourceNotFoundException", "Table not found", OperationError),
        ("UnknownError", "Unknown error", DynamoDBError),
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

    # Mock put_item to raise an error
    error_response = {"Error": {"Code": error_code, "Message": error_message}}
    mock_put_item = mocker.patch.object(
        client._client,
        "put_item",
        side_effect=ClientError(error_response, "PutItem"),
    )

    expected_error = _expected_client_error(
        "add_entity", error_code, error_message
    )
    with pytest.raises(
        expected_exception, match=f"^{re.escape(expected_error)}$"
    ):
        client.add_receipt_structure_analysis(
            sample_receipt_structure_analysis
        )

    # Verify put_item was called
    mock_put_item.assert_called_once()


def test_addReceiptStructureAnalyses_success(
    dynamodb_table: Literal["MyMockedTable"],
):
    """Test successful addition of multiple ReceiptStructureAnalyses."""
    client = DynamoClient(dynamodb_table)
    image_id = str(uuid.uuid4())
    analyses = [
        _make_analysis(
            receipt_id=index + 1,
            image_id=image_id,
            section_index=index,
            overall_reasoning=f"Analysis {index}",
        )
        for index in range(3)
    ]

    client.add_receipt_structure_analyses(analyses)

    for index, analysis in enumerate(analyses):
        response = _get_stored_item(client, dynamodb_table, analysis)
        assert "Item" in response, f"Item {index} should exist in DynamoDB"


@pytest.mark.parametrize(
    (
        "operation_name,error_code,error_message,expected_exception,"
        "cancellation_reasons"
    ),
    [
        (
            "add_receipt_structure_analyses",
            "ResourceNotFoundException",
            "Table not found",
            OperationError,
            None,
        ),
        (
            "add_receipt_structure_analyses",
            "ProvisionedThroughputExceededException",
            "Provisioned throughput exceeded",
            DynamoDBThroughputError,
            None,
        ),
        (
            "add_receipt_structure_analyses",
            "InternalServerError",
            "Internal server error",
            DynamoDBServerError,
            None,
        ),
        (
            "add_receipt_structure_analyses",
            "ValidationException",
            "One or more parameters were invalid",
            EntityValidationError,
            None,
        ),
        (
            "add_receipt_structure_analyses",
            "AccessDeniedException",
            "Access denied",
            DynamoDBError,
            None,
        ),
        (
            "add_receipt_structure_analyses",
            "UnknownError",
            "Unknown error occurred",
            DynamoDBError,
            None,
        ),
        (
            "update_receipt_structure_analyses",
            "ResourceNotFoundException",
            "Table not found",
            OperationError,
            None,
        ),
        (
            "update_receipt_structure_analyses",
            "TransactionCanceledException",
            "Transaction canceled due to ConditionalCheckFailed",
            DynamoDBError,
            [{"Code": "ConditionalCheckFailed"}],
        ),
        (
            "update_receipt_structure_analyses",
            "InternalServerError",
            "Internal server error",
            DynamoDBServerError,
            None,
        ),
        (
            "update_receipt_structure_analyses",
            "ProvisionedThroughputExceededException",
            "Provisioned throughput exceeded",
            DynamoDBThroughputError,
            None,
        ),
        (
            "update_receipt_structure_analyses",
            "ValidationException",
            "One or more parameters were invalid",
            EntityValidationError,
            None,
        ),
        (
            "update_receipt_structure_analyses",
            "AccessDeniedException",
            "Access denied",
            DynamoDBError,
            None,
        ),
        (
            "update_receipt_structure_analyses",
            "UnknownError",
            "Unknown error occurred",
            DynamoDBError,
            None,
        ),
    ],
)
def test_receipt_structure_analyses_client_errors(
    dynamodb_table,
    sample_receipt_structure_analysis,
    mocker,
    operation_name,
    error_code,
    error_message,
    expected_exception,
    cancellation_reasons,
):
    """Test client errors for batch write operations."""
    client = DynamoClient(dynamodb_table)
    analyses = [
        sample_receipt_structure_analysis,
        _make_related_analysis(sample_receipt_structure_analysis),
    ]
    error_response = {"Error": {"Code": error_code, "Message": error_message}}
    if cancellation_reasons:
        error_response["CancellationReasons"] = cancellation_reasons

    mock_batch_write = mocker.patch.object(
        client._client,
        "batch_write_item",
        side_effect=ClientError(error_response, "BatchWriteItem"),
    )

    expected_error = _expected_client_error(
        operation_name, error_code, error_message
    )
    with pytest.raises(
        expected_exception, match=f"^{re.escape(expected_error)}$"
    ):
        getattr(client, operation_name)(analyses)

    mock_batch_write.assert_called_once()


def test_updateReceiptStructureAnalysis_success(
    dynamodb_table: Literal["MyMockedTable"],
):
    """Test successful update of a ReceiptStructureAnalysis."""
    client = DynamoClient(dynamodb_table)
    analysis = _make_analysis(
        receipt_id=123,
        image_id=str(uuid.uuid4()),
        section_index=1,
        overall_reasoning="Test analysis",
    )

    client.add_receipt_structure_analysis(analysis)
    analysis.overall_reasoning = "Updated analysis"
    client.update_receipt_structure_analysis(analysis)

    response = _get_stored_item(client, dynamodb_table, analysis)
    assert "Item" in response, "Item should exist in DynamoDB"
    assert response["Item"]["overall_reasoning"]["S"] == "Updated analysis"


def test_updateReceiptStructureAnalyses_success(
    dynamodb_table: Literal["MyMockedTable"],
):
    """Test successful update of multiple ReceiptStructureAnalyses."""
    client = DynamoClient(dynamodb_table)
    image_id = str(uuid.uuid4())
    analyses = [
        _make_analysis(
            receipt_id=index + 1,
            image_id=image_id,
            section_index=index,
            overall_reasoning=f"Analysis {index}",
        )
        for index in range(3)
    ]
    for analysis in analyses:
        client.add_receipt_structure_analysis(analysis)

    for analysis in analyses:
        analysis.overall_reasoning = f"Updated {analysis.overall_reasoning}"

    client.update_receipt_structure_analyses(analyses)

    for index, analysis in enumerate(analyses):
        response = _get_stored_item(client, dynamodb_table, analysis)
        assert "Item" in response, f"Item {index} should exist in DynamoDB"
        assert (
            response["Item"]["overall_reasoning"]["S"]
            == f"Updated Analysis {index}"
        )


def test_listReceiptStructureAnalysesFromReceipt_success(
    dynamodb_table: Literal["MyMockedTable"],
):
    """Test successful listing of ReceiptStructureAnalyses for a receipt."""
    client = DynamoClient(dynamodb_table)
    image_id = str(uuid.uuid4())
    receipt_id = 123
    analyses = [
        _make_analysis(
            receipt_id=receipt_id,
            image_id=image_id,
            section_index=index,
            overall_reasoning=f"Analysis version {index}",
            version=f"1.0.{index}",
            timestamp_second=index,
        )
        for index in range(3)
    ]

    existing_analyses = client.list_receipt_structure_analyses_from_receipt(
        receipt_id=receipt_id, image_id=image_id
    )
    assert existing_analyses == []

    client.add_receipt_structure_analyses(analyses)
    result = client.list_receipt_structure_analyses_from_receipt(
        receipt_id=receipt_id, image_id=image_id
    )

    assert len(result) == 3, "Should return all 3 analyses"
    versions = sorted(analysis.version for analysis in result)
    assert versions == ["1.0.0", "1.0.1", "1.0.2"], "Should have all versions"
    for index, analysis in enumerate(
        sorted(result, key=lambda item: item.version)
    ):
        assert analysis.receipt_id == receipt_id
        assert analysis.image_id == image_id
        assert analysis.version == f"1.0.{index}"
        assert analysis.overall_reasoning == f"Analysis version {index}"
