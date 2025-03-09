import pytest
from decimal import Decimal
from receipt_label.models.receipt import Receipt, ReceiptWord, ReceiptLine
from receipt_label.models.line_item import LineItem, Price, Quantity
from receipt_label.processors.progressive_processor import (
    ProgressiveReceiptProcessor,
    FastPatternMatcher,
    ProgressiveValidator,
    CurrencyMatch,
    ProcessingResult,
    LineItemAnalysis
)

@pytest.fixture
def sample_receipt():
    return Receipt(
        receipt_id="test_receipt",
        image_id="test_image",
        words=[],  # Will be populated by sample_receipt_words
        lines=[],  # Will be populated by sample_receipt_lines
        metadata={
            "merchant_name": "Test Store",
            "date": "2024-01-01",
            "time": "12:00:00",
            "total": str(Decimal("25.97")),
            "subtotal": str(Decimal("23.97")),
            "tax": str(Decimal("2.00"))
        }
    )

@pytest.fixture
def sample_receipt_lines():
    return [
        ReceiptLine(
            line_id="1",
            text="Item 1 $10.99",
            confidence=0.9,
            bounding_box={"x": 0, "y": 0, "width": 100, "height": 20},
            top_right={"x": 100, "y": 0},
            top_left={"x": 0, "y": 0},
            bottom_right={"x": 100, "y": 20},
            bottom_left={"x": 0, "y": 20},
            angle_degrees=0.0,
            angle_radians=0.0
        ),
        ReceiptLine(
            line_id="2",
            text="Item 2 2 @ $5.99",
            confidence=0.9,
            bounding_box={"x": 0, "y": 30, "width": 100, "height": 20},
            top_right={"x": 100, "y": 30},
            top_left={"x": 0, "y": 30},
            bottom_right={"x": 100, "y": 50},
            bottom_left={"x": 0, "y": 50},
            angle_degrees=0.0,
            angle_radians=0.0
        ),
        ReceiptLine(
            line_id="3",
            text="Subtotal $23.97",
            confidence=0.9,
            bounding_box={"x": 0, "y": 60, "width": 100, "height": 20},
            top_right={"x": 100, "y": 60},
            top_left={"x": 0, "y": 60},
            bottom_right={"x": 100, "y": 80},
            bottom_left={"x": 0, "y": 80},
            angle_degrees=0.0,
            angle_radians=0.0
        ),
        ReceiptLine(
            line_id="4",
            text="Tax $2.00",
            confidence=0.9,
            bounding_box={"x": 0, "y": 90, "width": 100, "height": 20},
            top_right={"x": 100, "y": 90},
            top_left={"x": 0, "y": 90},
            bottom_right={"x": 100, "y": 110},
            bottom_left={"x": 0, "y": 110},
            angle_degrees=0.0,
            angle_radians=0.0
        ),
        ReceiptLine(
            line_id="5",
            text="Total $25.97",
            confidence=0.9,
            bounding_box={"x": 0, "y": 120, "width": 100, "height": 20},
            top_right={"x": 100, "y": 120},
            top_left={"x": 0, "y": 120},
            bottom_right={"x": 100, "y": 140},
            bottom_left={"x": 0, "y": 140},
            angle_degrees=0.0,
            angle_radians=0.0
        )
    ]

@pytest.fixture
def sample_receipt_words():
    return [
        ReceiptWord(
            word_id="1",
            text="Item",
            confidence=0.9,
            line_id="1",
            bounding_box={"x": 0, "y": 0, "width": 30, "height": 20},
            angle_degrees=0.0,
            angle_radians=0.0
        ),
        ReceiptWord(
            word_id="2",
            text="1",
            confidence=0.9,
            line_id="1",
            bounding_box={"x": 35, "y": 0, "width": 10, "height": 20},
            angle_degrees=0.0,
            angle_radians=0.0
        ),
        ReceiptWord(
            word_id="3",
            text="$10.99",
            confidence=0.9,
            line_id="1",
            bounding_box={"x": 50, "y": 0, "width": 40, "height": 20},
            angle_degrees=0.0,
            angle_radians=0.0
        ),
        # Line 2
        ReceiptWord(
            word_id="4",
            text="Item",
            confidence=0.9,
            line_id="2",
            bounding_box={"x": 0, "y": 30, "width": 30, "height": 20},
            angle_degrees=0.0,
            angle_radians=0.0
        ),
        ReceiptWord(
            word_id="5",
            text="2",
            confidence=0.9,
            line_id="2",
            bounding_box={"x": 35, "y": 30, "width": 10, "height": 20},
            angle_degrees=0.0,
            angle_radians=0.0
        ),
        ReceiptWord(
            word_id="6",
            text="2",
            confidence=0.9,
            line_id="2",
            bounding_box={"x": 50, "y": 30, "width": 10, "height": 20},
            angle_degrees=0.0,
            angle_radians=0.0
        ),
        ReceiptWord(
            word_id="7",
            text="@",
            confidence=0.9,
            line_id="2",
            bounding_box={"x": 65, "y": 30, "width": 10, "height": 20},
            angle_degrees=0.0,
            angle_radians=0.0
        ),
        ReceiptWord(
            word_id="8",
            text="$5.99",
            confidence=0.9,
            line_id="2",
            bounding_box={"x": 80, "y": 30, "width": 40, "height": 20},
            angle_degrees=0.0,
            angle_radians=0.0
        ),
        # Subtotal line
        ReceiptWord(
            word_id="9",
            text="Subtotal",
            confidence=0.9,
            line_id="3",
            bounding_box={"x": 0, "y": 60, "width": 60, "height": 20},
            angle_degrees=0.0,
            angle_radians=0.0
        ),
        ReceiptWord(
            word_id="10",
            text="$23.97",
            confidence=0.9,
            line_id="3",
            bounding_box={"x": 70, "y": 60, "width": 40, "height": 20},
            angle_degrees=0.0,
            angle_radians=0.0
        ),
        # Tax line
        ReceiptWord(
            word_id="11",
            text="Tax",
            confidence=0.9,
            line_id="4",
            bounding_box={"x": 0, "y": 90, "width": 30, "height": 20},
            angle_degrees=0.0,
            angle_radians=0.0
        ),
        ReceiptWord(
            word_id="12",
            text="$2.00",
            confidence=0.9,
            line_id="4",
            bounding_box={"x": 40, "y": 90, "width": 40, "height": 20},
            angle_degrees=0.0,
            angle_radians=0.0
        ),
        # Total line
        ReceiptWord(
            word_id="13",
            text="Total",
            confidence=0.9,
            line_id="5",
            bounding_box={"x": 0, "y": 120, "width": 40, "height": 20},
            angle_degrees=0.0,
            angle_radians=0.0
        ),
        ReceiptWord(
            word_id="14",
            text="$25.97",
            confidence=0.9,
            line_id="5",
            bounding_box={"x": 50, "y": 120, "width": 40, "height": 20},
            angle_degrees=0.0,
            angle_radians=0.0
        )
    ]

@pytest.mark.unit
def test_fast_pattern_matcher(sample_receipt, sample_receipt_lines, sample_receipt_words):
    matcher = FastPatternMatcher()
    result = matcher.process(sample_receipt, sample_receipt_lines, sample_receipt_words)
    
    assert isinstance(result, ProcessingResult)
    # The pattern matcher finds all lines with currency amounts
    assert len(result.line_items) == 5  # All lines with currency amounts
    assert result.subtotal == Decimal("23.97")
    assert result.total == Decimal("25.97")
    
    # Check line items
    assert result.line_items[0].price.extended_price == Decimal("10.99")  # Item 1
    assert result.line_items[1].price.extended_price == Decimal("11.98")  # Item 2 (2 @ $5.99)
    assert result.line_items[2].price.extended_price == Decimal("23.97")  # Subtotal
    assert result.line_items[3].price.extended_price == Decimal("2.00")   # Tax
    assert result.line_items[4].price.extended_price == Decimal("25.97")  # Total

@pytest.mark.unit
def test_progressive_validator(sample_receipt, sample_receipt_lines, sample_receipt_words):
    validator = ProgressiveValidator()
    
    # Test with valid results
    valid_result = ProcessingResult(
        line_items=[
            LineItem(
                description="Item 1",
                quantity=None,
                price=Price(unit_price=None, extended_price=Decimal("10.99")),
                confidence=0.9,
                line_ids=["1"]
            ),
            LineItem(
                description="Item 2",
                quantity=Quantity(amount=Decimal("2"), unit=None),
                price=Price(unit_price=Decimal("5.99"), extended_price=Decimal("11.98")),
                confidence=0.9,
                line_ids=["2"]
            )
        ],
        subtotal=Decimal("23.97"),
        tax=Decimal("2.00"),
        total=Decimal("25.97"),
        confidence=0.9,
        uncertain_items=[]
    )
    
    validation_result = validator.validate_full_receipt(valid_result)
    assert validation_result["status"] == "valid"
    assert not validation_result["errors"]
    assert not validation_result["warnings"]
    
    # Test with missing components
    invalid_result = ProcessingResult(
        line_items=[],
        subtotal=None,
        tax=None,
        total=None,
        confidence=0.5,
        uncertain_items=[]
    )
    
    validation_result = validator.validate_full_receipt(invalid_result)
    assert validation_result["status"] == "invalid"
    assert len(validation_result["errors"]) > 0

@pytest.mark.integration
@pytest.mark.asyncio
async def test_progressive_receipt_processor(sample_receipt, sample_receipt_lines, sample_receipt_words, mocker):
    """Test the progressive receipt processor with mocked dependencies."""
    # Mock any async dependencies
    mock_pattern_matcher = mocker.Mock()
    mock_pattern_matcher.process.return_value = ProcessingResult(
        line_items=[
            LineItem(
                description="Item 1",
                quantity=None,
                price=Price(unit_price=None, extended_price=Decimal("10.99")),
                confidence=0.9,
                line_ids=["1"]
            ),
            LineItem(
                description="Item 2",
                quantity=Quantity(amount=Decimal("2"), unit=None),
                price=Price(unit_price=Decimal("5.99"), extended_price=Decimal("11.98")),
                confidence=0.9,
                line_ids=["2"]
            ),
            LineItem(
                description="Subtotal",
                quantity=None,
                price=Price(unit_price=None, extended_price=Decimal("23.97")),
                confidence=0.9,
                line_ids=["3"]
            ),
            LineItem(
                description="Tax",
                quantity=None,
                price=Price(unit_price=None, extended_price=Decimal("2.00")),
                confidence=0.9,
                line_ids=["4"]
            ),
            LineItem(
                description="Total",
                quantity=None,
                price=Price(unit_price=None, extended_price=Decimal("25.97")),
                confidence=0.9,
                line_ids=["5"]
            )
        ],
        subtotal=Decimal("23.97"),
        tax=Decimal("2.00"),
        total=Decimal("25.97"),
        confidence=0.9,
        uncertain_items=[]
    )
    
    # Create an instance with the mocked dependency
    processor = ProgressiveReceiptProcessor()
    processor.fast_processor = mock_pattern_matcher
    
    # Call the async method
    result = await processor.process_receipt(
        sample_receipt,
        sample_receipt_lines,
        sample_receipt_words
    )
    
    # Verify the result is now a LineItemAnalysis
    assert isinstance(result, LineItemAnalysis)
    assert len(result.items) == 5  # All lines are included
    assert result.subtotal == Decimal("23.97")
    assert result.confidence >= 0.7
    
    # Verify the mock was called correctly
    mock_pattern_matcher.process.assert_called_once_with(
        sample_receipt,
        sample_receipt_lines,
        sample_receipt_words
    )

@pytest.mark.unit
def test_currency_match():
    match = CurrencyMatch(
        amount=Decimal("10.99"),
        confidence=0.9,
        text="$10.99",
        position={"x": 0, "y": 0},
        line_id="1"
    )
    
    assert match.amount == Decimal("10.99")
    assert match.confidence == 0.9
    assert match.text == "$10.99"
    assert match.position == {"x": 0, "y": 0}
    assert match.line_id == "1" 