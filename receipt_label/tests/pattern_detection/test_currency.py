"""Tests for currency pattern detection."""

import pytest
from receipt_dynamo.entities import ReceiptWord

from receipt_label.pattern_detection import (
    CurrencyPatternDetector,
    PatternType)


class TestCurrencyPatternDetector:
    """Test currency pattern detection functionality."""

    @pytest.fixture
    def detector(self):
        """Create a currency pattern detector."""
        return CurrencyPatternDetector()

    @pytest.fixture
    def sample_words(self):
        """Create sample receipt words for testing."""
        words_data = [
            # Line 1: Product and price
            {"text": "BURGER", "y": 100},
            {"text": "$5.99", "y": 100},
            # Line 2: Quantity and price
            {"text": "2", "y": 120},
            {"text": "@", "y": 120},
            {"text": "$3.50", "y": 120},
            # Line 3: Tax line
            {"text": "TAX", "y": 200},
            {"text": "$0.89", "y": 200},
            # Line 4: Total line
            {"text": "TOTAL", "y": 220},
            {"text": "$15.99", "y": 220},
            # Various formats
            {"text": "€10.50", "y": 300},
            {"text": "5.99", "y": 320},  # Plain number
            {"text": "($2.00)", "y": 340},  # Negative/discount
            {"text": "1,234.56", "y": 360},  # With thousands
        ]

        words = []
        for i, data in enumerate(words_data):
            word = ReceiptWord(
                receipt_id=1,
                image_id="550e8400-e29b-41d4-a716-446655440000",
                line_id=data["y"] // 20,  # Group by y position
                word_id=i,
                text=data["text"],
                bounding_box={
                    "x": 0,
                    "y": data["y"],
                    "width": 50,
                    "height": 20,
                },
                top_left={"x": 0, "y": data["y"]},
                top_right={"x": 50, "y": data["y"]},
                bottom_left={"x": 0, "y": data["y"] + 20},
                bottom_right={"x": 50, "y": data["y"] + 20},
                angle_degrees=0.0,
                angle_radians=0.0,
                confidence=0.95)
            words.append(word)

        return words

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_detect_currency_formats(self, detector, sample_words):
        """Test detection of various currency formats."""
        matches = await detector.detect(sample_words)

        # Should detect all currency patterns
        assert len(matches) > 0

        # Check specific formats
        matched_texts = [m.matched_text for m in matches]
        assert "$5.99" in matched_texts
        assert "$3.50" in matched_texts
        assert "$0.89" in matched_texts
        assert "$15.99" in matched_texts
        assert "€10.50" in matched_texts
        assert "($2.00)" in matched_texts
        assert "1,234.56" in matched_texts

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_currency_classification(self, detector, sample_words):
        """Test smart classification of currency types."""
        matches = await detector.detect(sample_words)

        # Find specific matches
        tax_match = next(m for m in matches if m.matched_text == "$0.89")
        total_match = next(m for m in matches if m.matched_text == "$15.99")

        # Check classifications
        assert tax_match.pattern_type == PatternType.TAX
        assert total_match.pattern_type == PatternType.GRAND_TOTAL

        # Check confidence
        assert tax_match.confidence >= 0.8
        assert total_match.confidence >= 0.8

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_negative_amounts(self, detector, sample_words):
        """Test detection of negative/discount amounts."""
        matches = await detector.detect(sample_words)

        # Find negative amount
        negative_match = next(
            m for m in matches if m.matched_text == "($2.00)"
        )

        assert negative_match.metadata["is_negative"] is True
        assert negative_match.extracted_value == -2.0

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_position_context(self, detector, sample_words):
        """Test position-based classification."""
        matches = await detector.detect(sample_words)

        # Total should be identified as bottom 20%
        total_match = next(m for m in matches if "$15.99" in m.matched_text)

        # In our test data, y=220 out of max ~360, so not actually bottom 20%
        # But it should still be classified as GRAND_TOTAL due to keyword
        assert total_match.pattern_type == PatternType.GRAND_TOTAL

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_quantity_context(self, detector, sample_words):
        """Test detection near quantity patterns."""
        matches = await detector.detect(sample_words)

        # The $3.50 appears after "2 @" so should be detected with quantity context
        unit_price_match = next(
            m for m in matches if m.matched_text == "$3.50"
        )

        # Should have high confidence due to clear pattern
        assert unit_price_match.confidence >= 0.5

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_plain_number_detection(self, detector, sample_words):
        """Test detection of plain numbers as currency."""
        matches = await detector.detect(sample_words)

        # Should detect "5.99" as currency
        plain_match = next(
            (m for m in matches if m.matched_text == "5.99"), None
        )
        assert plain_match is not None
        assert plain_match.extracted_value == 5.99

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_skip_noise_words(self, detector):
        """Test that noise words are skipped."""
        words = [
            ReceiptWord(
                receipt_id=1,
                image_id="550e8400-e29b-41d4-a716-446655440000",
                line_id=1,
                word_id=1,
                text="$5.99",
                bounding_box={"x": 0, "y": 0, "width": 50, "height": 20},
                top_left={"x": 0, "y": 0},
                top_right={"x": 50, "y": 0},
                bottom_left={"x": 0, "y": 20},
                bottom_right={"x": 50, "y": 20},
                angle_degrees=0.0,
                angle_radians=0.0,
                confidence=0.95)
        ]

        # Mark word as noise
        words[0].is_noise = True

        matches = await detector.detect(words)
        assert len(matches) == 0  # Should skip noise word

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_currency_symbols(self, detector):
        """Test various currency symbols."""
        test_cases = [
            ("$10.00", "$", 10.0),
            ("€20.50", "€", 20.5),
            ("£15.99", "£", 15.99),
            ("¥1000", "¥", 1000.0),
            ("₹500", "₹", 500.0),
        ]

        for text, expected_symbol, expected_value in test_cases:
            word = ReceiptWord(
                receipt_id=1,
                image_id="550e8400-e29b-41d4-a716-446655440000",
                line_id=1,
                word_id=1,
                text=text,
                bounding_box={"x": 0, "y": 0, "width": 50, "height": 20},
                top_left={"x": 0, "y": 0},
                top_right={"x": 50, "y": 0},
                bottom_left={"x": 0, "y": 20},
                bottom_right={"x": 50, "y": 20},
                angle_degrees=0.0,
                angle_radians=0.0,
                confidence=0.95)

            matches = await detector.detect([word])
            assert len(matches) == 1
            assert (
                matches[0].metadata.get("currency_symbol") == expected_symbol
            )
            assert matches[0].extracted_value == expected_value
