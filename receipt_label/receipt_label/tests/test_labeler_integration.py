import os
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from receipt_label.core.labeler import LabelingResult, ReceiptLabeler
from receipt_label.models.label import LabelAnalysis
from receipt_label.models.line_item import (
    LineItem,
    LineItemAnalysis,
    Price,
    Quantity,
)
from receipt_label.models.receipt import Receipt, ReceiptLine, ReceiptWord
from receipt_label.models.structure import StructureAnalysis

# Test constants
TEST_API_KEY = "test-api-key"
TEST_DYNAMO_TABLE = "TestTable"
TEST_RECEIPT_ID = "test-receipt-001"
TEST_IMAGE_ID = "test-image-001"
TEST_PRICE = Decimal("9.99")


@pytest.fixture
def sample_receipt_data():
    """Fixture providing sample receipt data for testing."""
    receipt_words = [
        ReceiptWord(text="Total", line_id=1, word_id=1, confidence=1.0),
        ReceiptWord(text=f"${TEST_PRICE}", line_id=1, word_id=2, confidence=1.0),
    ]
    receipt_lines = [
        ReceiptLine(
            line_id=1,
            text=f"Total ${TEST_PRICE}",
            confidence=1.0,
            bounding_box={"x": 0, "y": 0, "width": 50, "height": 10},
            top_right={"x": 50, "y": 0},
            top_left={"x": 0, "y": 0},
            bottom_right={"x": 50, "y": 10},
            bottom_left={"x": 0, "y": 10},
            angle_degrees=0.0,
            angle_radians=0.0,
        )
    ]
    receipt = Receipt(
        receipt_id=TEST_RECEIPT_ID,
        image_id=TEST_IMAGE_ID,
        words=receipt_words,
        lines=receipt_lines,
    )
    return receipt, receipt_words, receipt_lines


@pytest.mark.integration
def test_label_receipt_returns_labeling_result(mocker, sample_receipt_data):
    """Test that ReceiptLabeler.label_receipt returns a properly structured LabelingResult.
    
    This integration test validates that the ReceiptLabeler correctly orchestrates
    all its dependencies (ReceiptAnalyzer, LineItemProcessor, BatchPlacesProcessor)
    and returns a complete LabelingResult with all expected fields populated.
    """
    receipt, receipt_words, receipt_lines = sample_receipt_data

    structure_analysis = StructureAnalysis(sections=[], overall_reasoning="ok")
    field_analysis = LabelAnalysis(labels=[], metadata={})
    line_item = LineItem(
        description="item",
        quantity=Quantity(amount=Decimal("1")),
        price=Price(
            unit_price=TEST_PRICE, extended_price=TEST_PRICE
        ),
        line_ids=[1],
        reasoning="good",
        metadata={},
    )
    line_item_analysis = LineItemAnalysis(
        items=[line_item],
        total_found=1,
        subtotal=TEST_PRICE,
        tax=Decimal("0"),
        total=TEST_PRICE,
        discrepancies=[],
        reasoning="ok",
    )

    mock_analyzer = MagicMock()
    mock_analyzer.analyze_structure.return_value = structure_analysis
    mock_analyzer.label_fields.return_value = field_analysis

    mock_line_processor = MagicMock()
    mock_line_processor.analyze_line_items.return_value = line_item_analysis

    mock_places_processor = MagicMock()
    mock_places_processor.process_receipt_batch.return_value = [
        {"places_api_match": {"name": "Test"}}
    ]

    with patch.dict(os.environ, {
        "DYNAMO_TABLE_NAME": TEST_DYNAMO_TABLE,
        "PINECONE_API_KEY": TEST_API_KEY,
        "OPENAI_API_KEY": TEST_API_KEY,
        "PINECONE_INDEX_NAME": "test-index",
        "PINECONE_HOST": "test-host.pinecone.io",
    }):
        labeler = ReceiptLabeler(
            places_api_key=TEST_API_KEY,
            gpt_api_key=TEST_API_KEY,
            dynamodb_table_name=os.environ.get("DYNAMO_TABLE_NAME", "Test"),
            validation_level="none",
        )
        # Replace the processors with mocks
        labeler.receipt_analyzer = mock_analyzer
        labeler.line_item_processor = mock_line_processor
        labeler.places_processor = mock_places_processor
        
        result = labeler.label_receipt(receipt, receipt_words, receipt_lines)

    # Verify result type and structure
    assert isinstance(result, LabelingResult)
    assert result.receipt_id == TEST_RECEIPT_ID
    
    # Validate structure analysis
    assert result.structure_analysis == structure_analysis
    assert result.structure_analysis.overall_reasoning == "ok"
    assert isinstance(result.structure_analysis.sections, list)
    
    # Validate field analysis
    assert result.field_analysis == field_analysis
    assert isinstance(result.field_analysis.metadata, dict)
    assert isinstance(result.field_analysis.labels, list)
    
    # Validate line item analysis
    assert result.line_item_analysis == line_item_analysis
    assert len(result.line_item_analysis.items) == 1
    assert result.line_item_analysis.items[0].description == "item"
    assert result.line_item_analysis.items[0].quantity.amount == Decimal("1")
    assert result.line_item_analysis.items[0].price.unit_price == TEST_PRICE
    assert result.line_item_analysis.total == TEST_PRICE
    assert result.line_item_analysis.subtotal == TEST_PRICE
    assert result.line_item_analysis.tax == Decimal("0")
    
    # Validate Places API data
    assert result.places_api_data == {"name": "Test"}
    
    # Validate execution times
    assert "places_api" in result.execution_times
    assert "line_item_processing" in result.execution_times
    assert isinstance(result.execution_times["places_api"], (int, float))
    assert isinstance(result.execution_times["line_item_processing"], (int, float))
    assert result.execution_times["places_api"] >= 0
    assert result.execution_times["line_item_processing"] >= 0
    
    # Validate no errors occurred
    assert isinstance(result.errors, dict)
    assert len(result.errors) == 0


@pytest.mark.integration
def test_label_receipt_handles_processor_failures(mocker, sample_receipt_data):
    """Test that ReceiptLabeler gracefully handles failures in dependent processors.
    
    This test ensures that when one of the processors (ReceiptAnalyzer, LineItemProcessor,
    or BatchPlacesProcessor) raises an exception, the labeler handles it gracefully and
    continues processing what it can.
    """
    receipt, receipt_words, receipt_lines = sample_receipt_data

    # Mock analyzer that raises an exception during structure analysis
    mock_analyzer = MagicMock()
    mock_analyzer.analyze_structure.side_effect = Exception("Structure analysis failed")
    mock_analyzer.label_fields.return_value = LabelAnalysis(labels=[], metadata={})

    mock_line_processor = MagicMock()
    mock_line_processor.analyze_line_items.side_effect = Exception("Line item processing failed")

    mock_places_processor = MagicMock()
    mock_places_processor.process_receipt_batch.return_value = [
        {"places_api_match": {"name": "Test"}}
    ]

    with patch.dict(os.environ, {
        "DYNAMO_TABLE_NAME": TEST_DYNAMO_TABLE,
        "PINECONE_API_KEY": TEST_API_KEY,
        "OPENAI_API_KEY": TEST_API_KEY,
        "PINECONE_INDEX_NAME": "test-index",
        "PINECONE_HOST": "test-host.pinecone.io",
    }):
        labeler = ReceiptLabeler(
            places_api_key=TEST_API_KEY,
            gpt_api_key=TEST_API_KEY,
            dynamodb_table_name=os.environ.get("DYNAMO_TABLE_NAME", "Test"),
            validation_level="none",
        )
        # Replace the processors with mocks
        labeler.receipt_analyzer = mock_analyzer
        labeler.line_item_processor = mock_line_processor
        labeler.places_processor = mock_places_processor
        
        result = labeler.label_receipt(receipt, receipt_words, receipt_lines)

    # The labeler should return a result even with failures
    assert isinstance(result, LabelingResult)
    assert result.receipt_id == TEST_RECEIPT_ID
    
    # Check that errors were captured
    assert "structure_analysis" in result.errors or result.structure_analysis is None
    assert "line_item_analysis" in result.errors or result.line_item_analysis is None
    
    # If errors were captured, validate error content
    if "structure_analysis" in result.errors:
        assert "Structure analysis failed" in str(result.errors["structure_analysis"])
    if "line_item_analysis" in result.errors:
        assert "Line item processing failed" in str(result.errors["line_item_analysis"])
    
    # Validate that field analysis still succeeded (it didn't throw an error)
    assert result.field_analysis is not None
    assert isinstance(result.field_analysis, LabelAnalysis)
    
    # Validate Places API still ran successfully
    assert result.places_api_data == {"name": "Test"}
    
    # Validate execution times are still tracked
    assert isinstance(result.execution_times, dict)
    assert "places_api" in result.execution_times


@pytest.mark.integration
@pytest.mark.parametrize("validation_level", ["basic", "strict", "none"])
def test_label_receipt_validation_levels(mocker, sample_receipt_data, validation_level):
    """Test that ReceiptLabeler respects different validation levels.
    
    This test ensures that the labeler correctly applies different validation
    strictness levels when processing receipts.
    """
    receipt, receipt_words, receipt_lines = sample_receipt_data

    structure_analysis = StructureAnalysis(sections=[], overall_reasoning="ok")
    field_analysis = LabelAnalysis(labels=[], metadata={})
    line_item = LineItem(
        description="item",
        quantity=Quantity(amount=Decimal("1")),
        price=Price(
            unit_price=TEST_PRICE, extended_price=TEST_PRICE
        ),
        line_ids=[1],
        reasoning="good",
        metadata={},
    )
    line_item_analysis = LineItemAnalysis(
        items=[line_item],
        total_found=1,
        subtotal=TEST_PRICE,
        tax=Decimal("0"),
        total=TEST_PRICE,
        discrepancies=[],
        reasoning="ok",
    )

    mock_analyzer = MagicMock()
    mock_analyzer.analyze_structure.return_value = structure_analysis
    mock_analyzer.label_fields.return_value = field_analysis

    mock_line_processor = MagicMock()
    mock_line_processor.analyze_line_items.return_value = line_item_analysis

    mock_places_processor = MagicMock()
    mock_places_processor.process_receipt_batch.return_value = [
        {"places_api_match": {"name": "Test"}}
    ]

    with patch(
        "receipt_label.core.labeler.ReceiptAnalyzer",
        return_value=mock_analyzer,
    ), patch(
        "receipt_label.core.labeler.LineItemProcessor",
        return_value=mock_line_processor,
    ), patch(
        "receipt_label.core.labeler.BatchPlacesProcessor",
        return_value=mock_places_processor,
    ), patch.dict(os.environ, {"DYNAMO_TABLE_NAME": TEST_DYNAMO_TABLE}):
        labeler = ReceiptLabeler(
            places_api_key=TEST_API_KEY,
            gpt_api_key=TEST_API_KEY,
            dynamodb_table_name=os.environ.get("DYNAMO_TABLE_NAME", "Test"),
            validation_level=validation_level,
        )
        result = labeler.label_receipt(receipt, receipt_words, receipt_lines)

    assert isinstance(result, LabelingResult)
    # Validation should be performed based on the level
    if validation_level != "none":
        assert result.validation_analysis is not None


@pytest.mark.integration
def test_label_receipt_places_api_disabled(mocker, sample_receipt_data):
    """Test that ReceiptLabeler works correctly when Places API is disabled.
    
    This test ensures that the labeler can process receipts without Places API
    enrichment when enable_places_api=False is specified.
    """
    receipt, receipt_words, receipt_lines = sample_receipt_data

    structure_analysis = StructureAnalysis(sections=[], overall_reasoning="ok")
    field_analysis = LabelAnalysis(labels=[], metadata={})
    line_item_analysis = LineItemAnalysis(
        items=[],
        total_found=0,
        subtotal=TEST_PRICE,
        tax=Decimal("0"),
        total=TEST_PRICE,
        discrepancies=[],
        reasoning="ok",
    )

    mock_analyzer = MagicMock()
    mock_analyzer.analyze_structure.return_value = structure_analysis
    mock_analyzer.label_fields.return_value = field_analysis

    mock_line_processor = MagicMock()
    mock_line_processor.analyze_line_items.return_value = line_item_analysis

    mock_places_processor = MagicMock()

    with patch(
        "receipt_label.core.labeler.ReceiptAnalyzer",
        return_value=mock_analyzer,
    ), patch(
        "receipt_label.core.labeler.LineItemProcessor",
        return_value=mock_line_processor,
    ), patch(
        "receipt_label.core.labeler.BatchPlacesProcessor",
        return_value=mock_places_processor,
    ), patch.dict(os.environ, {"DYNAMO_TABLE_NAME": TEST_DYNAMO_TABLE}):
        labeler = ReceiptLabeler(
            places_api_key=TEST_API_KEY,
            gpt_api_key=TEST_API_KEY,
            dynamodb_table_name=os.environ.get("DYNAMO_TABLE_NAME", "Test"),
            validation_level="none",
        )
        result = labeler.label_receipt(
            receipt, receipt_words, receipt_lines, enable_places_api=False
        )

    # Verify result structure
    assert isinstance(result, LabelingResult)
    assert result.receipt_id == TEST_RECEIPT_ID
    
    # Places API should not have been called
    mock_places_processor.process_receipt_batch.assert_not_called()
    
    # No places data should be in the result
    assert result.places_api_data is None or result.places_api_data == {}
    
    # But other analyses should still be present and valid
    assert result.structure_analysis is not None
    assert result.structure_analysis.overall_reasoning == "ok"
    assert isinstance(result.structure_analysis.sections, list)
    
    assert result.field_analysis is not None
    assert isinstance(result.field_analysis.metadata, dict)
    assert isinstance(result.field_analysis.labels, list)
    
    assert result.line_item_analysis is not None
    assert result.line_item_analysis.total == TEST_PRICE
    assert result.line_item_analysis.subtotal == TEST_PRICE
    
    # Execution times should not include places_api
    assert "places_api" not in result.execution_times
    assert "line_item_processing" in result.execution_times
    
    # No errors should have occurred
    assert len(result.errors) == 0
