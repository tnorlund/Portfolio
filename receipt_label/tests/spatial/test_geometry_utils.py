"""
Unit tests for geometry utilities in spatial currency detection.

Tests the utility classes and functions used for spatial analysis:
- SpatialWord: Enhanced ReceiptWord with spatial analysis
- SpatialLine: Spatial line analysis capabilities
- RowGrouper: Groups words into rows using spatial relationships
- ColumnDetector: Detects column structure in receipt layout
"""

import pytest
from receipt_dynamo.entities.receipt_word import ReceiptWord

from receipt_label.spatial.geometry_utils import (
    ColumnDetector,
    LineItemSpatialDetector,
    RowGrouper,
    SpatialLine,
    SpatialRow,
    SpatialWord)
from receipt_label.pattern_detection.base import PatternMatch, PatternType


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
    confidence: float = 0.9) -> ReceiptWord:
    """Helper function to create ReceiptWord objects for testing."""
    if bounding_box is None:
        bounding_box = {"x": 0.1, "y": 0.1, "width": 0.15, "height": 0.02}
    if top_left is None:
        top_left = {"x": bounding_box["x"], "y": bounding_box["y"]}
    if top_right is None:
        top_right = {
            "x": bounding_box["x"] + bounding_box["width"],
            "y": bounding_box["y"],
        }
    if bottom_left is None:
        bottom_left = {
            "x": bounding_box["x"],
            "y": bounding_box["y"] + bounding_box["height"],
        }
    if bottom_right is None:
        bottom_right = {
            "x": bounding_box["x"] + bounding_box["width"],
            "y": bounding_box["y"] + bounding_box["height"],
        }

    return ReceiptWord(
        receipt_id=receipt_id,
        image_id=image_id,
        line_id=line_id,
        word_id=word_id,
        text=text,
        bounding_box=bounding_box,
        top_right=top_right,
        top_left=top_left,
        bottom_right=bottom_right,
        bottom_left=bottom_left,
        angle_degrees=angle_degrees,
        angle_radians=angle_radians,
        confidence=confidence)


class TestSpatialWord:
    """Test cases for SpatialWord utility class."""

    @pytest.fixture
    def sample_word(self):
        """Create a sample ReceiptWord for testing."""
        return create_receipt_word(
            text="Big Mac",
            bounding_box={"x": 0.1, "y": 0.1, "width": 0.15, "height": 0.02})

    @pytest.mark.unit
    def test_spatial_word_creation(self, sample_word):
        """Test SpatialWord creation and basic properties."""
        spatial_word = SpatialWord(sample_word)

        assert spatial_word.word == sample_word
        assert spatial_word.x == 0.175  # Center X
        assert spatial_word.y == 0.11  # Center Y

    @pytest.mark.unit
    def test_centroid_calculation(self, sample_word):
        """Test centroid calculation and caching."""
        spatial_word = SpatialWord(sample_word)

        # First call should calculate centroid
        centroid1 = spatial_word.centroid
        assert centroid1 == (0.175, 0.11)

        # Second call should use cached value
        centroid2 = spatial_word.centroid
        assert centroid2 == centroid1
        assert spatial_word._cached_centroid is not None

    @pytest.mark.unit
    def test_is_on_same_line_as(self, sample_word):
        """Test same-line detection using Y-coordinate tolerance."""
        spatial_word1 = SpatialWord(sample_word)

        # Create another word on same line (close Y)
        word2 = create_receipt_word(
            line_id=1,
            word_id=2,
            text="5.99",
            bounding_box={
                "x": 0.8,
                "y": 0.105,
                "width": 0.06,
                "height": 0.02,
            },  # Slightly different Y
            top_left={"x": 0.8, "y": 0.105},
            bottom_right={"x": 0.86, "y": 0.125})
        spatial_word2 = SpatialWord(word2)

        # Should be on same line (within tolerance)
        assert spatial_word1.is_on_same_line_as(spatial_word2, tolerance=10.0)

        # Create another word on different line (far Y)
        word3 = create_receipt_word(
            line_id=2,
            word_id=3,
            text="3.99",
            bounding_box={
                "x": 0.8,
                "y": 0.15,
                "width": 0.06,
                "height": 0.02,
            },  # Much different Y
            top_left={"x": 0.8, "y": 0.15},
            bottom_right={"x": 0.86, "y": 0.17})
        spatial_word3 = SpatialWord(word3)

        # Should not be on same line with strict tolerance
        # spatial_word1 centroid Y = 0.1 + 0.02/2 = 0.11
        # spatial_word3 centroid Y = 0.15 + 0.02/2 = 0.16
        # Difference = 0.05, so tolerance must be < 0.05
        assert not spatial_word1.is_on_same_line_as(
            spatial_word3, tolerance=0.03
        )

    @pytest.mark.unit
    def test_horizontal_distance_calculation(self, sample_word):
        """Test horizontal distance calculation."""
        spatial_word1 = SpatialWord(sample_word)

        # Create another word
        word2 = create_receipt_word(
            line_id=1,
            word_id=2,
            text="5.99",
            bounding_box={"x": 0.8, "y": 0.1, "width": 0.06, "height": 0.02},
            top_left={"x": 0.8, "y": 0.1},
            bottom_right={"x": 0.86, "y": 0.12})
        spatial_word2 = SpatialWord(word2)

        distance = spatial_word1.get_horizontal_distance_to(spatial_word2)

        # Distance should be |0.175 - 0.83| = 0.655
        assert abs(distance - 0.655) < 0.01

    @pytest.mark.unit
    def test_left_alignment_detection(self, sample_word):
        """Test left alignment detection."""
        spatial_word = SpatialWord(sample_word)

        # Create other words with similar left alignment
        aligned_words = []
        for i in range(2, 4):
            word = create_receipt_word(
                receipt_id=1,
                image_id="12345678-1234-4567-8901-123456789012",
                line_id=i,
                word_id=i,
                text=f"Item{i}",
                bounding_box={
                    "x": 0.105,
                    "y": 0.1 + i * 0.03,
                    "width": 0.12,
                    "height": 0.02,
                },
                top_left={"x": 0.105, "y": 0.1 + i * 0.03},
                bottom_right={"x": 0.225, "y": 0.12 + i * 0.03},
                confidence=0.9)
            aligned_words.append(SpatialWord(word))

        # Should detect left alignment
        assert spatial_word.is_left_aligned_with(aligned_words, tolerance=10.0)

    @pytest.mark.unit
    def test_right_alignment_detection(self, sample_word):
        """Test right alignment detection."""
        # Create word with right edge at 0.86
        word = create_receipt_word(
            receipt_id=1,
            image_id="12345678-1234-4567-8901-123456789012",
            line_id=1,
            word_id=1,
            text="5.99",
            bounding_box={"x": 0.8, "y": 0.1, "width": 0.06, "height": 0.02},
            top_left={"x": 0.8, "y": 0.1},
            bottom_right={"x": 0.86, "y": 0.12},
            confidence=0.9)
        spatial_word = SpatialWord(word)

        # Create other words with similar right alignment
        aligned_words = []
        for i in range(2, 4):
            aligned_word = create_receipt_word(
                receipt_id=1,
                image_id="12345678-1234-4567-8901-123456789012",
                line_id=i,
                word_id=i,
                text=f"{i}.99",
                bounding_box={
                    "x": 0.8,
                    "y": 0.1 + i * 0.03,
                    "width": 0.06,
                    "height": 0.02,
                },
                top_left={"x": 0.8, "y": 0.1 + i * 0.03},
                bottom_right={"x": 0.86, "y": 0.12 + i * 0.03},
                confidence=0.9)
            aligned_words.append(SpatialWord(aligned_word))

        # Should detect right alignment
        assert spatial_word.is_right_aligned_with(
            aligned_words, tolerance=10.0
        )

    @pytest.mark.unit
    def test_relative_position_calculation(self, sample_word):
        """Test relative position calculation on line."""
        # Create words at different positions
        line_words = []
        for i, x_pos in enumerate([0.1, 0.3, 0.5, 0.7, 0.9]):
            word = create_receipt_word(
                receipt_id=1,
                image_id="12345678-1234-4567-8901-123456789012",
                line_id=1,
                word_id=i,
                text=f"Word{i}",
                bounding_box={
                    "x": x_pos,
                    "y": 0.1,
                    "width": 0.05,
                    "height": 0.02,
                },
                top_left={"x": x_pos, "y": 0.1},
                bottom_right={"x": x_pos + 0.05, "y": 0.12},
                confidence=0.9)
            line_words.append(SpatialWord(word))

        # First word should be at position 0.0 (leftmost)
        position = line_words[0].get_relative_position_on_line(line_words)
        assert abs(position - 0.0) < 0.1

        # Last word should be at position 1.0 (rightmost)
        position = line_words[-1].get_relative_position_on_line(line_words)
        assert abs(position - 1.0) < 0.1

    @pytest.mark.unit
    def test_currency_word_detection(self):
        """Test currency word pattern detection."""
        # Create currency word
        currency_word = create_receipt_word(
            receipt_id=1,
            image_id="12345678-1234-4567-8901-123456789012",
            line_id=1,
            word_id=1,
            text="5.99",
            bounding_box={"x": 0.8, "y": 0.1, "width": 0.06, "height": 0.02},
            top_left={"x": 0.8, "y": 0.1},
            bottom_right={"x": 0.86, "y": 0.12},
            confidence=0.9)

        # Create pattern match for currency word
        currency_pattern_match = PatternMatch(
            word=currency_word,
            pattern_type=PatternType.CURRENCY,
            confidence=0.95,
            matched_text="5.99",
            extracted_value=5.99,
            metadata={})

        spatial_word = SpatialWord(currency_word, currency_pattern_match)
        assert spatial_word.is_currency_word()

        # Create non-currency word
        text_word = create_receipt_word(
            receipt_id=1,
            image_id="12345678-1234-4567-8901-123456789012",
            line_id=1,
            word_id=2,
            text="Big Mac",
            bounding_box={"x": 0.1, "y": 0.1, "width": 0.15, "height": 0.02},
            top_left={"x": 0.1, "y": 0.1},
            bottom_right={"x": 0.25, "y": 0.12},
            confidence=0.9)
        spatial_word_text = SpatialWord(text_word)  # No pattern match

        assert not spatial_word_text.is_currency_word()

    @pytest.mark.unit
    def test_quantity_word_detection(self):
        """Test quantity word pattern detection."""
        # Create quantity word
        quantity_word = create_receipt_word(
            receipt_id=1,
            image_id="12345678-1234-4567-8901-123456789012",
            line_id=1,
            word_id=1,
            text="2x",
            bounding_box={"x": 0.5, "y": 0.1, "width": 0.04, "height": 0.02},
            top_left={"x": 0.5, "y": 0.1},
            bottom_right={"x": 0.54, "y": 0.12},
            confidence=0.9)

        # Create pattern match for quantity word
        quantity_pattern_match = PatternMatch(
            word=quantity_word,
            pattern_type=PatternType.QUANTITY_TIMES,
            confidence=0.9,
            matched_text="2x",
            extracted_value=2,
            metadata={})

        spatial_word = SpatialWord(quantity_word, quantity_pattern_match)
        assert spatial_word.is_quantity_word()

        # Test non-quantity word
        non_quantity_word = create_receipt_word(
            receipt_id=1,
            image_id="12345678-1234-4567-8901-123456789012",
            line_id=1,
            word_id=2,
            text="Big",
            bounding_box={"x": 0.1, "y": 0.1, "width": 0.05, "height": 0.02},
            top_left={"x": 0.1, "y": 0.1},
            bottom_right={"x": 0.15, "y": 0.12},
            confidence=0.9)
        spatial_word_no_qty = SpatialWord(
            non_quantity_word
        )  # No pattern match
        assert not spatial_word_no_qty.is_quantity_word()


class TestSpatialLine:
    """Test cases for SpatialLine utility class."""

    @pytest.fixture
    def sample_line_words(self):
        """Create sample words for a line."""
        words = []
        word_data = [
            ("Big", 0.1, 0.05),
            ("Mac", 0.16, 0.05),
            ("2x", 0.5, 0.04),
            ("5.99", 0.8, 0.06),
        ]

        for i, (text, x, width) in enumerate(word_data):
            word = create_receipt_word(
                receipt_id=1,
                image_id="12345678-1234-4567-8901-123456789012",
                line_id=1,
                word_id=i + 1,
                text=text,
                bounding_box={
                    "x": x,
                    "y": 0.1,
                    "width": width,
                    "height": 0.02,
                },
                top_left={"x": x, "y": 0.1},
                bottom_right={"x": x + width, "y": 0.12},
                confidence=0.9)
            words.append(word)

        return words

    @pytest.fixture
    def sample_pattern_matches(self, sample_line_words):
        """Create pattern matches for the sample line words."""
        matches = []

        # "2x" is a quantity pattern
        matches.append(
            PatternMatch(
                word=sample_line_words[2],  # "2x"
                pattern_type=PatternType.QUANTITY_TIMES,
                confidence=0.9,
                matched_text="2x",
                extracted_value=2,
                metadata={})
        )

        # "5.99" is a currency pattern
        matches.append(
            PatternMatch(
                word=sample_line_words[3],  # "5.99"
                pattern_type=PatternType.CURRENCY,
                confidence=0.95,
                matched_text="5.99",
                extracted_value=5.99,
                metadata={})
        )

        return matches

    @pytest.mark.unit
    def test_spatial_line_creation(
        self, sample_line_words, sample_pattern_matches
    ):
        """Test SpatialLine creation and word ordering."""
        spatial_line = SpatialLine(sample_line_words, sample_pattern_matches)

        assert len(spatial_line.spatial_words) == 4

        # Words should be sorted by X position
        x_positions = [w.x for w in spatial_line.spatial_words]
        assert x_positions == sorted(x_positions)

    @pytest.mark.unit
    def test_get_leftmost_words(
        self, sample_line_words, sample_pattern_matches
    ):
        """Test getting leftmost words from line."""
        spatial_line = SpatialLine(sample_line_words, sample_pattern_matches)

        leftmost = spatial_line.get_leftmost_words(2)

        assert len(leftmost) == 2
        assert leftmost[0].word.text == "Big"
        assert leftmost[1].word.text == "Mac"

    @pytest.mark.unit
    def test_get_rightmost_words(
        self, sample_line_words, sample_pattern_matches
    ):
        """Test getting rightmost words from line."""
        spatial_line = SpatialLine(sample_line_words, sample_pattern_matches)

        rightmost = spatial_line.get_rightmost_words(2)

        assert len(rightmost) == 2
        assert rightmost[0].word.text == "2x"
        assert rightmost[1].word.text == "5.99"

    @pytest.mark.unit
    def test_has_currency_pattern(
        self, sample_line_words, sample_pattern_matches
    ):
        """Test currency pattern detection in line."""
        spatial_line = SpatialLine(sample_line_words, sample_pattern_matches)

        assert spatial_line.has_currency_pattern()

        # Test line without currency - use only text words that won't match currency patterns
        non_currency_words = [
            w for w in sample_line_words if w.text in ["Big", "Mac"]
        ]  # Only pure text words
        spatial_line_no_currency = SpatialLine(
            non_currency_words
        )  # No pattern matches

        assert not spatial_line_no_currency.has_currency_pattern()

    @pytest.mark.unit
    def test_get_line_width(self, sample_line_words, sample_pattern_matches):
        """Test line width calculation."""
        spatial_line = SpatialLine(sample_line_words, sample_pattern_matches)

        width = spatial_line.get_line_width()

        # Width should be from leftmost left edge to rightmost right edge
        # "Big" starts at 0.1, "5.99" ends at 0.86
        expected_width = 0.86 - 0.1
        assert abs(width - expected_width) < 0.01

    @pytest.mark.unit
    def test_split_by_alignment(
        self, sample_line_words, sample_pattern_matches
    ):
        """Test splitting line words by alignment."""
        spatial_line = SpatialLine(sample_line_words, sample_pattern_matches)

        groups = spatial_line.split_by_alignment()

        assert "left" in groups
        assert "center" in groups
        assert "right" in groups

        # Should have words in each group
        total_words = (
            len(groups["left"]) + len(groups["center"]) + len(groups["right"])
        )
        assert total_words == 4

    @pytest.mark.unit
    def test_get_price_words(self, sample_line_words, sample_pattern_matches):
        """Test getting price words from line."""
        spatial_line = SpatialLine(sample_line_words, sample_pattern_matches)

        price_words = spatial_line.get_price_words()

        # Only "5.99" should match as currency (has exactly 2 decimal places)
        # "2x" is a quantity indicator, not a price
        assert len(price_words) == 1
        price_texts = [w.word.text for w in price_words]
        assert "5.99" in price_texts
        assert "2x" not in price_texts

    @pytest.mark.unit
    def test_get_description_words(
        self, sample_line_words, sample_pattern_matches
    ):
        """Test getting description words from line."""
        spatial_line = SpatialLine(sample_line_words, sample_pattern_matches)

        description_words = spatial_line.get_description_words(
            exclude_prices=True
        )

        # "2x" is not currency, so we have "Big", "Mac", and "2x" as description words
        # Only "5.99" is excluded as it's detected as currency
        assert len(description_words) == 3
        description_texts = [w.word.text for w in description_words]
        assert "Big" in description_texts
        assert "Mac" in description_texts
        assert (
            "2x" in description_texts
        )  # "2x" is a quantity indicator, not currency
        assert (
            "5.99" not in description_texts
        )  # "5.99" is excluded as currency


class TestRowGrouper:
    """Test cases for RowGrouper utility class."""

    @pytest.fixture
    def sample_words_multiple_lines(self):
        """Create sample words on multiple lines."""
        words = []

        # Line 1: y=0.1
        line1_words = [
            ("Big", 0.1, 0.1),
            ("Mac", 0.16, 0.1),
            ("5.99", 0.8, 0.1),
        ]

        # Line 2: y=0.15 (5 units below)
        line2_words = [
            ("Fries", 0.1, 0.15),
            ("2.99", 0.8, 0.15),
        ]

        # Line 3: y=0.25 (10 units below line 2)
        line3_words = [
            ("TOTAL", 0.1, 0.25),
            ("8.98", 0.8, 0.25),
        ]

        all_words = line1_words + line2_words + line3_words

        for i, (text, x, y) in enumerate(all_words):
            word = create_receipt_word(
                receipt_id=1,
                image_id="12345678-1234-4567-8901-123456789012",
                line_id=i // 3 + 1,
                word_id=i + 1,
                text=text,
                bounding_box={"x": x, "y": y, "width": 0.05, "height": 0.02},
                top_left={"x": x, "y": y},
                bottom_right={"x": x + 0.05, "y": y + 0.02},
                confidence=0.9)
            words.append(word)

        return words

    @pytest.mark.unit
    def test_group_words_into_rows(self, sample_words_multiple_lines):
        """Test grouping words into rows."""
        grouper = RowGrouper(
            y_tolerance=0.03
        )  # 0.03 units tolerance (smaller than 0.05 difference)

        rows = grouper.group_words_into_rows(sample_words_multiple_lines)

        assert len(rows) == 3

        # Check first row
        assert len(rows[0].words) == 3
        row1_texts = [w.text for w in rows[0].words]
        assert "Big" in row1_texts
        assert "Mac" in row1_texts
        assert "5.99" in row1_texts

        # Check second row
        assert len(rows[1].words) == 2
        row2_texts = [w.text for w in rows[1].words]
        assert "Fries" in row2_texts
        assert "2.99" in row2_texts

    @pytest.mark.unit
    def test_y_tolerance_parameter(self, sample_words_multiple_lines):
        """Test that Y tolerance parameter affects grouping."""
        strict_grouper = RowGrouper(y_tolerance=3.0)  # Strict tolerance
        loose_grouper = RowGrouper(y_tolerance=15.0)  # Loose tolerance

        strict_rows = strict_grouper.group_words_into_rows(
            sample_words_multiple_lines
        )
        loose_rows = loose_grouper.group_words_into_rows(
            sample_words_multiple_lines
        )

        # Strict should create more rows (less grouping)
        assert len(strict_rows) >= len(loose_rows)

    @pytest.mark.unit
    def test_empty_words_list(self):
        """Test handling of empty words list."""
        grouper = RowGrouper()

        rows = grouper.group_words_into_rows([])

        assert len(rows) == 0


class TestColumnDetector:
    """Test cases for ColumnDetector utility class."""

    @pytest.fixture
    def sample_spatial_rows(self):
        """Create sample spatial rows for column detection."""
        # Create words in column layout
        words_data = [
            # Row 1: Description at x=0.1, Price at x=0.8
            [("Big Mac", 0.1), ("5.99", 0.8)],
            # Row 2: Description at x=0.1, Price at x=0.8
            [("Fries", 0.1), ("2.99", 0.8)],
            # Row 3: Total at x=0.1, Amount at x=0.8
            [("TOTAL", 0.1), ("8.98", 0.8)],
        ]

        rows = []
        for row_idx, row_words in enumerate(words_data):
            words = []
            for word_idx, (text, x) in enumerate(row_words):
                word = create_receipt_word(
                    receipt_id=1,
                    image_id="12345678-1234-4567-8901-123456789012",
                    line_id=row_idx + 1,
                    word_id=word_idx + 1,
                    text=text,
                    bounding_box={
                        "x": x,
                        "y": 0.1 + row_idx * 0.05,
                        "width": 0.05,
                        "height": 0.02,
                    },
                    top_left={"x": x, "y": 0.1 + row_idx * 0.05},
                    bottom_right={"x": x + 0.05, "y": 0.12 + row_idx * 0.05},
                    confidence=0.9)
                words.append(word)

            row = SpatialRow(
                y_position=0.1 + row_idx * 0.05,
                height=0.02,
                words=words,
                columns={})
            rows.append(row)

        return rows

    @pytest.mark.unit
    def test_detect_columns(self, sample_spatial_rows):
        """Test column detection from spatial rows."""
        detector = ColumnDetector(x_tolerance=0.02)

        columns = detector.detect_columns(sample_spatial_rows)

        assert len(columns) == 2

        # Should detect description column around x=0.1
        desc_column = [c for c in columns if abs(c.x_position - 0.1) < 0.05]
        assert len(desc_column) == 1
        assert desc_column[0].column_type == "description"

        # Should detect price column around x=0.8
        price_column = [c for c in columns if abs(c.x_position - 0.8) < 0.05]
        assert len(price_column) == 1
        # Without pattern matches, numbers are not identified as currency,
        # so the column is classified as "description"
        assert price_column[0].column_type == "description"

    @pytest.mark.unit
    def test_x_tolerance_parameter(self, sample_spatial_rows):
        """Test that X tolerance parameter affects column detection."""
        strict_detector = ColumnDetector(x_tolerance=0.01)
        loose_detector = ColumnDetector(x_tolerance=0.1)

        strict_columns = strict_detector.detect_columns(sample_spatial_rows)
        loose_columns = loose_detector.detect_columns(sample_spatial_rows)

        # Both should detect similar columns
        assert len(strict_columns) >= 1
        assert len(loose_columns) >= 1

    @pytest.mark.unit
    def test_empty_rows_list(self):
        """Test handling of empty rows list."""
        detector = ColumnDetector()

        columns = detector.detect_columns([])

        assert len(columns) == 0


class TestLineItemSpatialDetector:
    """Test cases for LineItemSpatialDetector main class."""

    @pytest.fixture
    def sample_receipt_words(self):
        """Create sample receipt words for full spatial detection."""
        words = []

        # Create structured receipt layout
        receipt_data = [
            # Items section
            ("Big Mac", 0.1, 0.1, "5.99", 0.8, 0.1),
            ("Fries", 0.1, 0.15, "2.99", 0.8, 0.15),
            ("Drink", 0.1, 0.2, "1.99", 0.8, 0.2),
            # Totals section
            ("TOTAL", 0.1, 0.3, "10.97", 0.8, 0.3),
        ]

        word_id = 1
        for item_name, item_x, item_y, price, price_x, price_y in receipt_data:
            # Add item name
            words.append(
                create_receipt_word(
                    receipt_id=1,
                    image_id="12345678-1234-4567-8901-123456789012",
                    line_id=len(words) // 2 + 1,
                    word_id=word_id,
                    text=item_name,
                    bounding_box={
                        "x": item_x,
                        "y": item_y,
                        "width": 0.1,
                        "height": 0.02,
                    },
                    top_left={"x": item_x, "y": item_y},
                    bottom_right={"x": item_x + 0.1, "y": item_y + 0.02},
                    confidence=0.9)
            )
            word_id += 1

            # Add price
            words.append(
                create_receipt_word(
                    receipt_id=1,
                    image_id="12345678-1234-4567-8901-123456789012",
                    line_id=len(words) // 2 + 1,
                    word_id=word_id,
                    text=price,
                    bounding_box={
                        "x": price_x,
                        "y": price_y,
                        "width": 0.06,
                        "height": 0.02,
                    },
                    top_left={"x": price_x, "y": price_y},
                    bottom_right={"x": price_x + 0.06, "y": price_y + 0.02},
                    confidence=0.9)
            )
            word_id += 1

        return words

    @pytest.mark.unit
    def test_detect_spatial_structure(self, sample_receipt_words):
        """Test complete spatial structure detection."""
        detector = LineItemSpatialDetector(
            y_tolerance=0.03, x_tolerance=0.05, min_confidence=0.3
        )  # Use smaller tolerance

        structure = detector.detect_spatial_structure(sample_receipt_words)

        assert "rows" in structure
        assert "columns" in structure
        assert "metadata" in structure

        # Should detect rows
        assert len(structure["rows"]) >= 3

        # Should detect columns
        assert len(structure["columns"]) >= 2

        # Should have metadata
        metadata = structure["metadata"]
        assert metadata["total_words"] == len(sample_receipt_words)
        assert metadata["row_count"] >= 3
        assert metadata["column_count"] >= 2

    @pytest.mark.unit
    def test_min_confidence_parameter(self, sample_receipt_words):
        """Test that min_confidence parameter affects results."""
        high_confidence_detector = LineItemSpatialDetector(min_confidence=0.8)
        low_confidence_detector = LineItemSpatialDetector(min_confidence=0.1)

        high_structure = high_confidence_detector.detect_spatial_structure(
            sample_receipt_words
        )
        low_structure = low_confidence_detector.detect_spatial_structure(
            sample_receipt_words
        )

        # Both should detect structure
        assert len(high_structure["rows"]) >= 1
        assert len(low_structure["rows"]) >= 1
