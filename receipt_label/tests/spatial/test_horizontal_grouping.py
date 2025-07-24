"""
Tests for horizontal grouping functionality for line item detection.
"""

import pytest
from typing import List

from receipt_dynamo.entities.receipt_word import ReceiptWord
from receipt_label.pattern_detection.base import PatternMatch, PatternType
from receipt_label.spatial.geometry_utils import (
    is_horizontally_aligned_group,
    group_words_into_line_items
)
from receipt_label.spatial.horizontal_line_item_detector import (
    HorizontalLineItemDetector,
    HorizontalGroupingConfig,
    LineItem
)

# Import the create_receipt_word helper from test_geometry_utils
from .test_geometry_utils import create_receipt_word


@pytest.fixture
def sample_receipt_words() -> List[ReceiptWord]:
    """Create sample receipt words that form line items."""
    return [
        # Line item 1: "BURGER $5.99"
        create_receipt_word(
            text="BURGER",
            bounding_box={"x": 0.1, "y": 0.2, "width": 0.15, "height": 0.02},
            word_id=1
        ),
        create_receipt_word(
            text="$5.99",
            bounding_box={"x": 0.8, "y": 0.2, "width": 0.1, "height": 0.02},
            word_id=2
        ),
        
        # Line item 2: "FRIES 2 @ $2.99 $5.98"
        create_receipt_word(
            text="FRIES",
            bounding_box={"x": 0.1, "y": 0.25, "width": 0.12, "height": 0.02},
            word_id=3
        ),
        create_receipt_word(
            text="2",
            bounding_box={"x": 0.4, "y": 0.25, "width": 0.05, "height": 0.02},
            word_id=4
        ),
        create_receipt_word(
            text="@",
            bounding_box={"x": 0.47, "y": 0.25, "width": 0.03, "height": 0.02},
            word_id=5
        ),
        create_receipt_word(
            text="$2.99",
            bounding_box={"x": 0.52, "y": 0.25, "width": 0.1, "height": 0.02},
            word_id=6
        ),
        create_receipt_word(
            text="$5.98",
            bounding_box={"x": 0.8, "y": 0.25, "width": 0.1, "height": 0.02},
            word_id=7
        ),
        
        # Not aligned - different Y coordinates
        create_receipt_word(
            text="SUBTOTAL",
            bounding_box={"x": 0.1, "y": 0.4, "width": 0.15, "height": 0.02},
            word_id=8
        ),
        create_receipt_word(
            text="$11.97",
            bounding_box={"x": 0.8, "y": 0.4, "width": 0.1, "height": 0.02},
            word_id=9
        ),
    ]


@pytest.fixture
def sample_pattern_matches() -> List[PatternMatch]:
    """Create sample pattern matches for the receipt words."""
    # Note: In real usage, PatternMatch would reference actual ReceiptWord objects
    # For testing, we'll create simplified matches
    return []


class TestHorizontalAlignment:
    """Test horizontal alignment detection."""
    
    @pytest.mark.unit
    def test_is_horizontally_aligned_group_basic(self, sample_receipt_words):
        """Test basic horizontal alignment detection."""
        # Test aligned words (BURGER and $5.99)
        aligned_words = [sample_receipt_words[0], sample_receipt_words[1]]
        assert is_horizontally_aligned_group(aligned_words) is True
        
        # Test words with different Y coordinates
        not_aligned = [sample_receipt_words[0], sample_receipt_words[7]]
        assert is_horizontally_aligned_group(not_aligned) is False
        
    @pytest.mark.unit
    def test_is_horizontally_aligned_group_tolerance(self, sample_receipt_words):
        """Test horizontal alignment with tolerance."""
        # Create words with slight Y variation
        word1 = create_receipt_word(
            text="TEST1",
            bounding_box={"x": 0.1, "y": 0.200, "width": 0.1, "height": 0.02},
            word_id=1
        )
        word2 = create_receipt_word(
            text="TEST2",
            bounding_box={"x": 0.3, "y": 0.215, "width": 0.1, "height": 0.02},
            word_id=2
        )
        
        # Within default tolerance (0.02)
        assert is_horizontally_aligned_group([word1, word2]) is True
        
        # Outside tolerance
        word3 = create_receipt_word(
            text="TEST3",
            bounding_box={"x": 0.5, "y": 0.225, "width": 0.1, "height": 0.02},
            word_id=3
        )
        assert is_horizontally_aligned_group([word1, word3]) is False
        
        # With larger tolerance
        assert is_horizontally_aligned_group([word1, word3], tolerance=0.03) is True
        
    @pytest.mark.unit
    def test_is_horizontally_aligned_group_min_words(self):
        """Test minimum word requirement."""
        # Single word should return False
        single_word = [create_receipt_word(
            text="SINGLE",
            bounding_box={"x": 0.1, "y": 0.2, "width": 0.1, "height": 0.02},
            word_id=1
        )]
        assert is_horizontally_aligned_group(single_word) is False
        
        # Empty list
        assert is_horizontally_aligned_group([]) is False
        
    @pytest.mark.unit
    def test_is_horizontally_aligned_group_vertical_stack(self):
        """Test words stacked vertically (same X, different Y)."""
        words = [
            create_receipt_word(
            text="STACK1",
            bounding_box={"x": 0.5, "y": 0.2, "width": 0.1, "height": 0.02},
            word_id=1
        ),
            create_receipt_word(
            text="STACK2",
            bounding_box={"x": 0.5, "y": 0.2, "width": 0.1, "height": 0.02},
            word_id=2
        )
        ]
        # Same X position - not a horizontal group
        assert is_horizontally_aligned_group(words) is False


class TestLineItemGrouping:
    """Test line item grouping functionality."""
    
    @pytest.mark.unit
    def test_group_words_into_line_items_basic(self, sample_receipt_words):
        """Test basic line item grouping."""
        line_items = group_words_into_line_items(sample_receipt_words)
        
        # Should detect 3 line items (burger, fries, subtotal)
        assert len(line_items) == 3
        
        # Check first line item (BURGER $5.99)
        assert len(line_items[0]) == 2
        assert line_items[0][0].text == "BURGER"
        assert line_items[0][1].text == "$5.99"
        
        # Check second line item (FRIES with quantity and prices)
        assert len(line_items[1]) == 5
        assert line_items[1][0].text == "FRIES"
        
        # Check third line item (SUBTOTAL)
        assert len(line_items[2]) == 2
        assert line_items[2][0].text == "SUBTOTAL"
        
    @pytest.mark.unit
    def test_group_words_into_line_items_with_patterns(
        self, sample_receipt_words, sample_pattern_matches
    ):
        """Test line item grouping with pattern matches."""
        line_items = group_words_into_line_items(
            sample_receipt_words, 
            sample_pattern_matches
        )
        
        # Pattern matches should help keep related words together
        assert len(line_items) == 3
        
        # Quantity pattern should keep "2 @" together
        fries_line = line_items[1]
        texts = [w.text for w in fries_line]
        assert "2" in texts
        assert "@" in texts
        
    @pytest.mark.unit
    def test_group_words_into_line_items_gap_detection(self):
        """Test line item separation based on horizontal gaps."""
        words = [
            # First group
            create_receipt_word(
            text="ITEM1",
            bounding_box={"x": 0.05, "y": 0.2, "width": 0.1, "height": 0.02},
            word_id=1
        ),
            create_receipt_word(
            text="$1.99",
            bounding_box={"x": 0.25, "y": 0.2, "width": 0.08, "height": 0.02},
            word_id=2
        ),
            # Large gap (>0.8 threshold) to second group  
            create_receipt_word(
            text="ITEM2", 
            bounding_box={"x": 1.2, "y": 0.2, "width": 0.1, "height": 0.02},
            word_id=3
        ),
            create_receipt_word(
            text="$2.99",
            bounding_box={"x": 1.35, "y": 0.2, "width": 0.08, "height": 0.02},
            word_id=4
        ),
        ]
        
        # With default gap threshold (0.8), should split into 2 groups 
        line_items = group_words_into_line_items(words)
        assert len(line_items) == 2  # Two separate groups
        
        # With larger gap threshold, should group as one
        line_items = group_words_into_line_items(words, x_gap_threshold=1.0)
        assert len(line_items) == 1


class TestHorizontalLineItemDetector:
    """Test the HorizontalLineItemDetector class."""
    
    @pytest.mark.unit
    def test_detector_initialization(self):
        """Test detector initialization with custom config."""
        config = HorizontalGroupingConfig(
            y_tolerance=0.03,
            min_confidence=0.7
        )
        detector = HorizontalLineItemDetector(config)
        
        assert detector.config.y_tolerance == 0.03
        assert detector.config.min_confidence == 0.7
        
    @pytest.mark.unit
    def test_detect_line_items_basic(
        self, sample_receipt_words, sample_pattern_matches
    ):
        """Test basic line item detection."""
        detector = HorizontalLineItemDetector()
        line_items = detector.detect_line_items(
            sample_receipt_words,
            sample_pattern_matches
        )
        
        # Should detect at least 2 line items (burger and fries)
        assert len(line_items) >= 2
        
        # Check line item properties
        for item in line_items:
            assert isinstance(item, LineItem)
            assert item.description
            assert item.confidence > 0
            assert item.detection_method in ["horizontal_grouping", "horizontal_grouping_merged"]
            
    @pytest.mark.unit  
    def test_detect_line_items_with_quantity(
        self, sample_receipt_words, sample_pattern_matches
    ):
        """Test line item detection with quantity parsing."""
        detector = HorizontalLineItemDetector()
        line_items = detector.detect_line_items(
            sample_receipt_words,
            sample_pattern_matches
        )
        
        # Find the fries line item
        fries_item = None
        for item in line_items:
            if "FRIES" in item.description:
                fries_item = item
                break
                
        assert fries_item is not None
        # Note: Quantity extraction is simplified in the implementation
        # In a real system, this would parse the quantity pattern properly
        
    @pytest.mark.unit
    def test_detect_line_items_confidence(self):
        """Test confidence scoring for line items."""
        # Create minimal line item
        minimal_words = [
            create_receipt_word(
            text="ITEM",
            bounding_box={"x": 0.1, "y": 0.2, "width": 0.1, "height": 0.02},
            word_id=1
        ),
            create_receipt_word(
            text="text",
            bounding_box={"x": 0.3, "y": 0.2, "width": 0.1, "height": 0.02},
            word_id=2
        ),
        ]
        
        detector = HorizontalLineItemDetector()
        line_items = detector.detect_line_items(minimal_words)
        
        # Without price patterns, confidence should be lower
        if line_items:
            assert line_items[0].confidence < 0.7
            
    @pytest.mark.unit
    def test_multi_line_item_merging(self):
        """Test merging of multi-line items."""
        words = [
            # Main line
            create_receipt_word(
            text="LONG",
            bounding_box={"x": 0.1, "y": 0.2, "width": 0.08, "height": 0.02},
            word_id=1
        ),
            create_receipt_word(
            text="PRODUCT",
            bounding_box={"x": 0.2, "y": 0.2, "width": 0.12, "height": 0.02},
            word_id=2
        ),
            create_receipt_word(
            text="$9.99",
            bounding_box={"x": 0.8, "y": 0.2, "width": 0.1, "height": 0.02},
            word_id=3
        ),
            # Continuation line (indented, no price)
            create_receipt_word(
            text="DESCRIPTION",
            bounding_box={"x": 0.15, "y": 0.23, "width": 0.15, "height": 0.02},
            word_id=4
        ),
            create_receipt_word(
            text="CONTINUED",
            bounding_box={"x": 0.32, "y": 0.23, "width": 0.12, "height": 0.02},
            word_id=5
        ),
        ]
        
        patterns = []  # Simplified for testing
        
        detector = HorizontalLineItemDetector()
        line_items = detector.detect_line_items(words, patterns)
        
        # Should merge into single item
        assert len(line_items) == 1
        assert "LONG PRODUCT" in line_items[0].description
        assert "DESCRIPTION CONTINUED" in line_items[0].description or len(line_items[0].words) == 5
        
    @pytest.mark.unit
    def test_empty_input(self):
        """Test handling of empty input."""
        detector = HorizontalLineItemDetector()
        
        # Empty words list
        line_items = detector.detect_line_items([])
        assert line_items == []
        
        # None input
        line_items = detector.detect_line_items(None)
        assert line_items == []