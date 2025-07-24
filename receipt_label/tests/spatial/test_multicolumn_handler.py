"""
Test suite for the MultiColumnHandler class.
"""

import pytest
from typing import List, Dict

from receipt_dynamo.entities.receipt_word import ReceiptWord
from receipt_label.pattern_detection.base import PatternMatch, PatternType
from receipt_label.spatial.multicolumn_handler import (
    MultiColumnHandler,
    ColumnType,
    ColumnClassification,
    MultiColumnLineItem,
    create_enhanced_line_item_detector
)


def create_receipt_word(
    receipt_id: int = 1,
    image_id: str = "12345678-1234-4567-8901-123456789012",
    line_id: int = 1,
    word_id: int = 1,
    text: str = "test",
    bounding_box: dict = None,
    top_left: dict = None,
    top_right: dict = None,
    bottom_left: dict = None,
    bottom_right: dict = None,
    angle_degrees: float = 0.0,
    angle_radians: float = 0.0,
    confidence: float = 0.9,
) -> ReceiptWord:
    """Helper function to create ReceiptWord objects for testing."""
    if bounding_box is None:
        bounding_box = {"x": 0.1, "y": 0.1, "width": 0.15, "height": 0.02}
    if top_left is None:
        top_left = {"x": bounding_box["x"], "y": bounding_box["y"]}
    if top_right is None:
        top_right = {"x": bounding_box["x"] + bounding_box["width"], "y": bounding_box["y"]}
    if bottom_left is None:
        bottom_left = {"x": bounding_box["x"], "y": bounding_box["y"] + bounding_box["height"]}
    if bottom_right is None:
        bottom_right = {"x": bounding_box["x"] + bounding_box["width"], "y": bounding_box["y"] + bounding_box["height"]}

    return ReceiptWord(
        receipt_id=receipt_id,
        image_id=image_id,
        line_id=line_id,
        word_id=word_id,
        text=text,
        bounding_box=bounding_box,
        top_left=top_left,
        top_right=top_right,
        bottom_left=bottom_left,
        bottom_right=bottom_right,
        angle_degrees=angle_degrees,
        angle_radians=angle_radians,
        confidence=confidence,
        extracted_data={},
        embedding_status="NONE"
    )


# Test fixtures
@pytest.fixture
def sample_receipt_words() -> List[ReceiptWord]:
    """Create sample receipt words for testing."""
    words = [
        # Line 1: Header
        create_receipt_word(
            word_id="w1", text="WALMART", line_id=1,
            bounding_box={"x": 0.1, "y": 0.05, "width": 0.2, "height": 0.02}
        ),
        
        # Line 2: Column headers
        create_receipt_word(word_id="w2", text="ITEM", line_id=2, bounding_box={"x": 0.1, "y": 0.1, "width": 0.1, "height": 0.02}),
        create_receipt_word(word_id="w3", text="QTY", line_id=2, bounding_box={"x": 0.4, "y": 0.1, "width": 0.05, "height": 0.02}),
        create_receipt_word(word_id="w4", text="PRICE", line_id=2, bounding_box={"x": 0.5, "y": 0.1, "width": 0.1, "height": 0.02}),
        create_receipt_word(word_id="w5", text="TOTAL", line_id=2, bounding_box={"x": 0.8, "y": 0.1, "width": 0.1, "height": 0.02}),
        
        # Line 3: Item 1
        create_receipt_word(word_id="w6", text="Apples", line_id=3, bounding_box={"x": 0.1, "y": 0.15, "width": 0.15, "height": 0.02}),
        create_receipt_word(word_id="w7", text="3", line_id=3, bounding_box={"x": 0.4, "y": 0.15, "width": 0.02, "height": 0.02}),
        create_receipt_word(word_id="w8", text="$1.99", line_id=3, bounding_box={"x": 0.5, "y": 0.15, "width": 0.08, "height": 0.02}),
        create_receipt_word(word_id="w9", text="$5.97", line_id=3, bounding_box={"x": 0.8, "y": 0.15, "width": 0.08, "height": 0.02}),
        
        # Line 4: Item 2
        create_receipt_word(word_id="w10", text="Bread", line_id=4, bounding_box={"x": 0.1, "y": 0.2, "width": 0.12, "height": 0.02}),
        create_receipt_word(word_id="w11", text="2", line_id=4, bounding_box={"x": 0.4, "y": 0.2, "width": 0.02, "height": 0.02}),
        create_receipt_word(word_id="w12", text="$2.49", line_id=4, bounding_box={"x": 0.5, "y": 0.2, "width": 0.08, "height": 0.02}),
        create_receipt_word(word_id="w13", text="$4.98", line_id=4, bounding_box={"x": 0.8, "y": 0.2, "width": 0.08, "height": 0.02}),
        
        # Line 5: Item 3 (with discount)
        create_receipt_word(word_id="w14", text="Milk", line_id=5, bounding_box={"x": 0.1, "y": 0.25, "width": 0.1, "height": 0.02}),
        create_receipt_word(word_id="w15", text="1", line_id=5, bounding_box={"x": 0.4, "y": 0.25, "width": 0.02, "height": 0.02}),
        create_receipt_word(word_id="w16", text="$3.99", line_id=5, bounding_box={"x": 0.5, "y": 0.25, "width": 0.08, "height": 0.02}),
        create_receipt_word(word_id="w17", text="$3.99", line_id=5, bounding_box={"x": 0.8, "y": 0.25, "width": 0.08, "height": 0.02}),
        
        # Line 6: Subtotal
        create_receipt_word(word_id="w18", text="SUBTOTAL", line_id=6, bounding_box={"x": 0.6, "y": 0.35, "width": 0.15, "height": 0.02}),
        create_receipt_word(word_id="w19", text="$14.94", line_id=6, bounding_box={"x": 0.8, "y": 0.35, "width": 0.1, "height": 0.02}),
    ]
    return words


@pytest.fixture
def currency_patterns(sample_receipt_words) -> List[PatternMatch]:
    """Create currency pattern matches."""
    patterns = []
    
    # Unit prices (center column)
    patterns.append(PatternMatch(
        pattern_type=PatternType.CURRENCY,
        matched_text="$1.99",
        confidence=0.9,
        word=sample_receipt_words[7],  # w8
        metadata={}
    ))
    patterns.append(PatternMatch(
        pattern_type=PatternType.CURRENCY,
        matched_text="$2.49",
        confidence=0.9,
        word=sample_receipt_words[11],  # w12
        metadata={}
    ))
    patterns.append(PatternMatch(
        pattern_type=PatternType.CURRENCY,
        matched_text="$3.99",
        confidence=0.9,
        word=sample_receipt_words[15],  # w16
        metadata={}
    ))
    
    # Line totals (right column)
    patterns.append(PatternMatch(
        pattern_type=PatternType.CURRENCY,
        matched_text="$5.97",
        confidence=0.9,
        word=sample_receipt_words[8],  # w9
        metadata={}
    ))
    patterns.append(PatternMatch(
        pattern_type=PatternType.CURRENCY,
        matched_text="$4.98",
        confidence=0.9,
        word=sample_receipt_words[12],  # w13
        metadata={}
    ))
    patterns.append(PatternMatch(
        pattern_type=PatternType.CURRENCY,
        matched_text="$3.99",
        confidence=0.9,
        word=sample_receipt_words[16],  # w17
        metadata={}
    ))
    patterns.append(PatternMatch(
        pattern_type=PatternType.CURRENCY,
        matched_text="$14.94",
        confidence=0.9,
        word=sample_receipt_words[18],  # w19
        metadata={}
    ))
    
    return patterns


@pytest.fixture
def quantity_patterns(sample_receipt_words) -> List[PatternMatch]:
    """Create quantity pattern matches."""
    patterns = []
    
    patterns.append(PatternMatch(
        pattern_type=PatternType.QUANTITY,
        matched_text="3",
        confidence=0.8,
        word=sample_receipt_words[6],  # w7
        metadata={"value": 3}
    ))
    patterns.append(PatternMatch(
        pattern_type=PatternType.QUANTITY,
        matched_text="2",
        confidence=0.8,
        word=sample_receipt_words[10],  # w11
        metadata={"value": 2}
    ))
    patterns.append(PatternMatch(
        pattern_type=PatternType.QUANTITY,
        matched_text="1",
        confidence=0.8,
        word=sample_receipt_words[14],  # w15
        metadata={"value": 1}
    ))
    
    return patterns


class TestMultiColumnHandler:
    """Test the MultiColumnHandler class."""
    
    def test_initialization(self):
        """Test handler initialization."""
        handler = MultiColumnHandler()
        assert handler.alignment_tolerance == 0.02
        assert handler.horizontal_tolerance == 0.05
        assert handler.min_column_items == 3
        assert handler.validation_threshold == 0.01
    
    def test_detect_column_structure(self, sample_receipt_words, currency_patterns, quantity_patterns):
        """Test column structure detection."""
        handler = MultiColumnHandler()
        
        columns = handler.detect_column_structure(
            sample_receipt_words,
            currency_patterns,
            quantity_patterns
        )
        
        # Should detect at least 3 columns (description, unit price, line total)
        assert len(columns) >= 3
        
        # Check column types
        column_types = {col.column_type for col in columns.values()}
        assert ColumnType.UNIT_PRICE in column_types
        assert ColumnType.LINE_TOTAL in column_types
        
        # Check positions
        for col in columns.values():
            if col.column_type == ColumnType.LINE_TOTAL:
                assert col.x_position > 0.7  # Should be on the right
            elif col.column_type == ColumnType.DESCRIPTION:
                assert col.x_position < 0.3  # Should be on the left
    
    def test_currency_column_classification(self, currency_patterns):
        """Test classification of currency columns."""
        handler = MultiColumnHandler()
        
        # Create a mock price column
        from receipt_label.spatial.vertical_alignment_detector import PriceColumn
        
        # Right-aligned column (should be line total)
        right_column = PriceColumn(
            column_id=1,
            x_center=0.84,
            x_min=0.8,
            x_max=0.88,
            prices=currency_patterns[3:6],  # Line total patterns
            confidence=0.8
        )
        
        classification = handler._classify_currency_column(right_column, [])
        assert classification.column_type == ColumnType.LINE_TOTAL
        assert classification.confidence >= 0.6
        assert classification.x_position == 0.84
        
        # Center column (should be unit price)
        center_column = PriceColumn(
            column_id=2,
            x_center=0.54,
            x_min=0.5,
            x_max=0.58,
            prices=currency_patterns[0:3],  # Unit price patterns
            confidence=0.8
        )
        
        classification = handler._classify_currency_column(center_column, [])
        assert classification.column_type == ColumnType.UNIT_PRICE
        assert classification.confidence >= 0.5
    
    def test_assemble_line_items(self, sample_receipt_words, currency_patterns, quantity_patterns):
        """Test line item assembly from multiple columns."""
        handler = MultiColumnHandler()
        
        # First detect columns
        columns = handler.detect_column_structure(
            sample_receipt_words,
            currency_patterns,
            quantity_patterns
        )
        
        # Assemble line items
        pattern_matches = {
            'currency': currency_patterns,
            'quantity': quantity_patterns
        }
        
        line_items = handler.assemble_line_items(
            columns,
            sample_receipt_words,
            pattern_matches
        )
        
        # Should find 3 line items
        assert len(line_items) >= 3
        
        # Check first item (Apples)
        apple_item = next((item for item in line_items if "Apples" in item.description), None)
        assert apple_item is not None
        assert apple_item.quantity == 3.0
        assert apple_item.unit_price == 1.99
        assert apple_item.line_total == 5.97
        
        # Check validation
        assert apple_item.validation_status.get('quantity_price_total', False)
    
    def test_mathematical_validation(self):
        """Test mathematical relationship validation."""
        handler = MultiColumnHandler()
        
        # Create a line item with correct math
        item1 = MultiColumnLineItem(
            description="Test Item",
            description_words=[],
            quantity=3.0,
            unit_price=1.99,
            line_total=5.97,
            line_number=1
        )
        
        validated_items = handler._validate_line_items([item1])
        assert len(validated_items) == 1
        assert validated_items[0].validation_status['quantity_price_total'] is True
        assert validated_items[0].confidence > 0.5  # Boosted for validation
        
        # Create a line item with incorrect math
        item2 = MultiColumnLineItem(
            description="Bad Item",
            description_words=[],
            quantity=2.0,
            unit_price=3.00,
            line_total=10.00,  # Should be 6.00
            line_number=2
        )
        
        validated_items = handler._validate_line_items([item2])
        assert validated_items[1].validation_status['quantity_price_total'] is False
    
    def test_extract_currency_value(self):
        """Test currency value extraction."""
        handler = MultiColumnHandler()
        
        # Create mock pattern
        word = create_receipt_word(word_id="test", text="$12.99")
        pattern = PatternMatch(
            pattern_type=PatternType.CURRENCY,
            matched_text="$12.99",
            confidence=0.9,
            word=word,
            metadata={}
        )
        
        value = handler._extract_currency_value(pattern)
        assert value == 12.99
        
        # Test with comma
        pattern.matched_text = "$1,234.56"
        value = handler._extract_currency_value(pattern)
        assert value == 1234.56
    
    def test_extract_quantity_value(self):
        """Test quantity value extraction."""
        handler = MultiColumnHandler()
        
        # Test various quantity formats
        test_cases = [
            ("3", 3.0),
            ("2x", 2.0),
            ("5 @", 5.0),
            ("1.5", 1.5),
            ("10x", 10.0)
        ]
        
        for text, expected in test_cases:
            word = create_receipt_word(word_id="test", text=text)
            pattern = PatternMatch(
                pattern_type=PatternType.QUANTITY,
                matched_text=text,
                confidence=0.8,
                word=word,
                metadata={}
            )
            
            value = handler._extract_quantity_value(pattern)
            assert value == expected
    
    def test_completeness_score(self):
        """Test line item completeness scoring."""
        handler = MultiColumnHandler()
        
        # Empty item
        item = MultiColumnLineItem(
            description="",
            description_words=[]
        )
        score = handler._calculate_completeness_score(item)
        assert score == 0.0
        
        # Item with description and total
        item.description = "Test Item"
        item.line_total = 5.99
        score = handler._calculate_completeness_score(item)
        assert score >= 0.6
        
        # Complete item with validation
        item.quantity = 2.0
        item.unit_price = 2.995  # Will validate to 5.99
        item.validation_status = {'quantity_price_total': True}
        score = handler._calculate_completeness_score(item)
        assert score >= 0.9
    
    def test_factory_function(self):
        """Test the factory function."""
        handler = create_enhanced_line_item_detector()
        assert isinstance(handler, MultiColumnHandler)
        assert handler.alignment_tolerance == 0.02


class TestMultiColumnEdgeCases:
    """Test edge cases and error handling."""
    
    def test_no_patterns(self):
        """Test handling when no patterns are found."""
        handler = MultiColumnHandler()
        columns = handler.detect_column_structure([], [], [])
        assert len(columns) == 0
    
    def test_single_column_receipt(self, sample_receipt_words):
        """Test handling of receipt with only one price column."""
        handler = MultiColumnHandler()
        
        # Create patterns for only one column
        single_word = create_receipt_word(
            word_id="single", text="$5.99", line_id=1,
            bounding_box={"x": 0.8, "y": 0.2, "width": 0.1, "height": 0.02}
        )
        
        patterns = [
            PatternMatch(
                pattern_type=PatternType.CURRENCY,
                matched_text="$5.99",
                confidence=0.9,
                word=single_word,
                metadata={}
            )
        ]
        
        # Should not detect any columns (needs min 3 items)
        columns = handler.detect_column_structure(sample_receipt_words, patterns, [])
        assert len(columns) == 0
    
    def test_missing_coordinates(self):
        """Test handling of words with missing coordinates."""
        handler = MultiColumnHandler()
        
        # Word without coordinates
        word = create_receipt_word(
            word_id="no_coords", text="$5.99",
            bounding_box={}  # Empty bounding box
        )
        
        x_center = handler._get_x_center(word)
        assert x_center is None
    
    def test_invalid_currency_values(self):
        """Test extraction of invalid currency values."""
        handler = MultiColumnHandler()
        
        word = create_receipt_word(word_id="bad", text="ABC")
        pattern = PatternMatch(
            pattern_type=PatternType.CURRENCY,
            matched_text="ABC",  # Not a valid currency
            confidence=0.5,
            word=word,
            metadata={}
        )
        
        value = handler._extract_currency_value(pattern)
        assert value is None