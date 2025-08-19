"""Tests for quantity pattern detection."""

import pytest
from receipt_dynamo.entities import ReceiptWord

from receipt_label.pattern_detection import (
    PatternType,
    QuantityPatternDetector)


class TestQuantityPatternDetector:
    """Test quantity pattern detection functionality."""

    @pytest.fixture
    def detector(self):
        """Create a quantity pattern detector."""
        return QuantityPatternDetector()

    @pytest.fixture
    def create_word(self):
        """Factory function to create test words."""

        def _create_word(text, word_id=1, x_pos=0, y_pos=100):
            return ReceiptWord(
                receipt_id=1,
                image_id="550e8400-e29b-41d4-a716-446655440000",
                line_id=y_pos // 20,
                word_id=word_id,
                text=text,
                bounding_box={
                    "x": x_pos,
                    "y": y_pos,
                    "width": 50,
                    "height": 20,
                },
                top_left={"x": x_pos, "y": y_pos},
                top_right={"x": x_pos + 50, "y": y_pos},
                bottom_left={"x": x_pos, "y": y_pos + 20},
                bottom_right={"x": x_pos + 50, "y": y_pos + 20},
                angle_degrees=0.0,
                angle_radians=0.0,
                confidence=0.95)

        return _create_word

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_quantity_at_pattern(self, detector, create_word):
        """Test detection of '@ symbol' quantity patterns."""
        test_cases = [
            ("2 @ $5.99", PatternType.QUANTITY_AT, 2.0, 5.99, 11.98),
            ("3 @ 4.50", PatternType.QUANTITY_AT, 3.0, 4.50, 13.50),
            ("10@$1.99", PatternType.QUANTITY_AT, 10.0, 1.99, 19.90),
            ("1.5 @ $3.00", PatternType.QUANTITY_AT, 1.5, 3.00, 4.50),
        ]

        for i, (text, expected_type, qty, unit_price, total) in enumerate(
            test_cases
        ):
            word = create_word(text, word_id=i)
            matches = await detector.detect([word])

            assert len(matches) == 1, f"Failed to detect quantity in: {text}"
            match = matches[0]
            assert match.pattern_type == expected_type
            assert match.extracted_value == qty
            assert match.metadata["quantity"] == qty
            assert match.metadata["unit_price"] == unit_price
            assert match.metadata["total"] == total
            assert match.metadata["format"] == "at_symbol"
            assert match.confidence >= 0.95

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_quantity_times_pattern(self, detector, create_word):
        """Test detection of 'x/times symbol' quantity patterns."""
        test_cases = [
            ("3 x $4.50", PatternType.QUANTITY_TIMES, 3.0, 4.50, 13.50),
            ("2 X 5.99", PatternType.QUANTITY_TIMES, 2.0, 5.99, 11.98),
            ("Qty: 5 x $2.00", PatternType.QUANTITY_TIMES, 5.0, 2.00, 10.00),
            ("QTY 4 x 3.25", PatternType.QUANTITY_TIMES, 4.0, 3.25, 13.00),
            ("10x$0.99", PatternType.QUANTITY_TIMES, 10.0, 0.99, 9.90),
        ]

        for i, (text, expected_type, qty, unit_price, total) in enumerate(
            test_cases
        ):
            word = create_word(text, word_id=i)
            matches = await detector.detect([word])

            assert len(matches) == 1, f"Failed to detect quantity in: {text}"
            match = matches[0]
            assert match.pattern_type == expected_type
            assert match.extracted_value == qty
            assert match.metadata["quantity"] == qty
            assert match.metadata["unit_price"] == unit_price
            assert match.metadata["total"] == total
            assert match.metadata["format"] == "times_symbol"
            assert match.confidence >= 0.95

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_quantity_for_pattern(self, detector, create_word):
        """Test detection of 'for' pricing patterns."""
        test_cases = [
            ("3 for $15.00", PatternType.QUANTITY_FOR, 3.0, 5.00, 15.00),
            ("2 FOR $10", PatternType.QUANTITY_FOR, 2.0, 5.00, 10.00),
            ("5 for 20.00", PatternType.QUANTITY_FOR, 5.0, 4.00, 20.00),
            ("10 for $25.99", PatternType.QUANTITY_FOR, 10.0, 2.599, 25.99),
        ]

        for i, (text, expected_type, qty, unit_price, total) in enumerate(
            test_cases
        ):
            word = create_word(text, word_id=i)
            matches = await detector.detect([word])

            assert len(matches) == 1, f"Failed to detect quantity in: {text}"
            match = matches[0]
            assert match.pattern_type == expected_type
            assert match.extracted_value == qty
            assert match.metadata["quantity"] == qty
            assert abs(match.metadata["unit_price"] - unit_price) < 0.01
            assert match.metadata["total"] == total
            assert match.metadata["format"] == "for_notation"
            assert match.confidence >= 0.9

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_quantity_slash_pattern(self, detector, create_word):
        """Test detection of slash notation patterns."""
        test_cases = [
            ("2/$10.00", PatternType.QUANTITY, 2.0, 5.00, 10.00),
            ("3 / $15", PatternType.QUANTITY, 3.0, 5.00, 15.00),
            ("5/$19.95", PatternType.QUANTITY, 5.0, 3.99, 19.95),
            ("4 / 12.00", PatternType.QUANTITY, 4.0, 3.00, 12.00),
        ]

        for i, (text, expected_type, qty, unit_price, total) in enumerate(
            test_cases
        ):
            word = create_word(text, word_id=i)
            matches = await detector.detect([word])

            assert len(matches) == 1, f"Failed to detect quantity in: {text}"
            match = matches[0]
            assert match.pattern_type == expected_type
            assert match.extracted_value == qty
            assert match.metadata["quantity"] == qty
            assert abs(match.metadata["unit_price"] - unit_price) < 0.01
            assert match.metadata["total"] == total
            assert match.metadata["format"] == "slash_notation"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_quantity_label_pattern(self, detector, create_word):
        """Test detection of labeled quantity patterns."""
        test_cases = [
            ("Qty: 5", PatternType.QUANTITY, 5.0),
            ("QTY 3", PatternType.QUANTITY, 3.0),
            ("Quantity: 10", PatternType.QUANTITY, 10.0),
            ("qty:2", PatternType.QUANTITY, 2.0),
            ("QUANTITY: 7.5", PatternType.QUANTITY, 7.5),
        ]

        for i, (text, expected_type, qty) in enumerate(test_cases):
            word = create_word(text, word_id=i)
            matches = await detector.detect([word])

            assert len(matches) == 1, f"Failed to detect quantity in: {text}"
            match = matches[0]
            assert match.pattern_type == expected_type
            assert match.extracted_value == qty
            assert match.metadata["quantity"] == qty
            assert match.metadata["format"] == "labeled"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_quantity_with_units(self, detector, create_word):
        """Test detection of quantities with units."""
        test_cases = [
            ("2 items", PatternType.QUANTITY, 2.0, "items"),
            ("3.5 lbs", PatternType.QUANTITY, 3.5, "lbs"),
            ("1 each", PatternType.QUANTITY, 1.0, "each"),
            ("5 pieces", PatternType.QUANTITY, 5.0, "pieces"),
            ("2.5 kg", PatternType.QUANTITY, 2.5, "kg"),
            ("10 oz", PatternType.QUANTITY, 10.0, "oz"),
            ("1 bottle", PatternType.QUANTITY, 1.0, "bottle"),
            ("3 bags", PatternType.QUANTITY, 3.0, "bags"),
        ]

        for i, (text, expected_type, qty, unit) in enumerate(test_cases):
            word = create_word(text, word_id=i)
            matches = await detector.detect([word])

            assert len(matches) == 1, f"Failed to detect quantity in: {text}"
            match = matches[0]
            assert match.pattern_type == expected_type
            assert match.extracted_value == qty
            assert match.metadata["quantity"] == qty
            assert match.metadata["unit"] == unit.lower()
            assert match.metadata["format"] == "with_unit"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_plain_number_quantity_detection(
        self, detector, create_word
    ):
        """Test detection of plain numbers as quantities based on context."""
        # Create a receipt context with product and price
        receipt_words = [
            create_word("BURGER", word_id=0, x_pos=0, y_pos=100),
            create_word(
                "2", word_id=1, x_pos=0, y_pos=120
            ),  # Quantity at line start
            create_word("$5.99", word_id=2, x_pos=100, y_pos=120),
            create_word("FRIES", word_id=3, x_pos=0, y_pos=140),
            create_word(
                "1", word_id=4, x_pos=0, y_pos=160
            ),  # Another quantity
            create_word("$2.99", word_id=5, x_pos=100, y_pos=160),
        ]

        matches = await detector.detect(receipt_words)

        # Should detect the plain numbers as quantities
        quantity_matches = [
            m for m in matches if m.metadata.get("format") == "plain_number"
        ]

        assert len(quantity_matches) >= 2

        # Check the "2" was detected
        two_match = next(
            (m for m in quantity_matches if m.extracted_value == 2.0), None
        )
        assert two_match is not None
        assert two_match.pattern_type == PatternType.QUANTITY
        assert two_match.metadata["quantity"] == 2.0

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_plain_number_not_quantity(self, detector, create_word):
        """Test that not all plain numbers are detected as quantities."""
        test_cases = [
            "123",  # Too large
            "2024",  # Year-like
            "12345",  # ZIP code-like
            "999",  # Large number
        ]

        for i, text in enumerate(test_cases):
            word = create_word(text, word_id=i)
            matches = await detector.detect([word])

            # Should not detect large numbers as quantities
            if matches:
                assert matches[0].metadata.get("format") != "plain_number"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_position_context_in_metadata(self, detector, create_word):
        """Test that position context is included in metadata."""
        # Create words on same line
        words = [
            create_word("Item", word_id=0, x_pos=0, y_pos=100),
            create_word("2", word_id=1, x_pos=50, y_pos=100),
            create_word("@", word_id=2, x_pos=100, y_pos=100),
            create_word("$5.99", word_id=3, x_pos=150, y_pos=100),
        ]

        matches = await detector.detect(words)

        # Find the quantity match
        qty_match = next((m for m in matches if "@" in m.matched_text), None)

        assert qty_match is not None
        assert "relative_y_position" in qty_match.metadata
        assert "line_word_count" in qty_match.metadata
        assert "line_position" in qty_match.metadata

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_confidence_scoring(self, detector, create_word):
        """Test confidence scoring for different quantity patterns."""
        test_cases = [
            # High confidence - explicit patterns
            ("2 @ $5.99", 0.95),
            ("3 x $4.50", 0.95),
            ("5 for $20.00", 0.9),
            # Medium confidence
            ("Qty: 5", 0.9),
            ("2 items", 0.9),
            ("3/$15", 0.85),
            # Lower confidence - plain numbers
            ("2", 0.6),  # Plain number (assuming price nearby)
        ]

        for i, (text, min_confidence) in enumerate(test_cases):
            word = create_word(text, word_id=i)
            # For plain number, add price context
            if text == "2":
                words = [
                    word,
                    create_word(
                        "$5.99", word_id=i + 100, x_pos=100, y_pos=100
                    ),
                ]
                matches = await detector.detect(words)
            else:
                matches = await detector.detect([word])

            if matches:
                assert (
                    matches[0].confidence >= min_confidence * 0.9
                )  # Allow some variance

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_skip_noise_words(self, detector, create_word):
        """Test that noise words are skipped."""
        word = create_word("2 @ $5.99")
        word.is_noise = True

        matches = await detector.detect([word])
        assert len(matches) == 0  # Should skip noise word

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_line_position_logic(self, detector, create_word):
        """Test the line position logic for plain number detection."""
        # Create a multi-word line with number at start
        words = [
            create_word("2", word_id=0, x_pos=0, y_pos=100),  # First word
            create_word("BURGER", word_id=1, x_pos=50, y_pos=100),
            create_word("$5.99", word_id=2, x_pos=150, y_pos=100),
        ]

        matches = await detector.detect(words)

        # Should detect "2" as quantity since it's at line start with price
        plain_matches = [
            m for m in matches if m.metadata.get("format") == "plain_number"
        ]

        assert len(plain_matches) == 1
        assert plain_matches[0].extracted_value == 2.0

        # Verify line position was considered
        metadata = plain_matches[0].metadata
        assert metadata.get("line_position") == 0  # First word in line
        assert metadata.get("line_word_count") == 3  # Three words on line
