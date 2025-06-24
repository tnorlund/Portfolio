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


@pytest.mark.integration
def test_label_receipt_returns_labeling_result(mocker):
    receipt_words = [
        ReceiptWord(text="Total", line_id=1, word_id=1, confidence=1.0),
        ReceiptWord(text="$9.99", line_id=1, word_id=2, confidence=1.0),
    ]
    receipt_lines = [
        ReceiptLine(
            line_id=1,
            text="Total $9.99",
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
        receipt_id="r1",
        image_id="img1",
        words=receipt_words,
        lines=receipt_lines,
    )

    structure_analysis = StructureAnalysis(sections=[], overall_reasoning="ok")
    field_analysis = LabelAnalysis(labels=[], metadata={})
    line_item = LineItem(
        description="item",
        quantity=Quantity(amount=Decimal("1")),
        price=Price(
            unit_price=Decimal("9.99"), extended_price=Decimal("9.99")
        ),
        line_ids=[1],
        reasoning="good",
        metadata={},
    )
    line_item_analysis = LineItemAnalysis(
        items=[line_item],
        total_found=1,
        subtotal=Decimal("9.99"),
        tax=Decimal("0"),
        total=Decimal("9.99"),
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
    ):
        labeler = ReceiptLabeler(
            places_api_key="k",
            gpt_api_key="k",
            dynamodb_table_name=os.environ.get("DYNAMO_TABLE_NAME", "Test"),
            validation_level="none",
        )
        result = labeler.label_receipt(receipt, receipt_words, receipt_lines)

    assert isinstance(result, LabelingResult)
    assert result.structure_analysis is structure_analysis
    assert result.field_analysis is field_analysis
    assert result.line_item_analysis is line_item_analysis
    assert result.places_api_data == {"name": "Test"}
    assert "places_api" in result.execution_times
    assert "line_item_processing" in result.execution_times
