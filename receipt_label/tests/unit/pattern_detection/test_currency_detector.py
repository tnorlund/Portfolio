"""Unit tests for currency pattern detection."""

import pytest
from unittest.mock import Mock

from receipt_label.pattern_detection.currency import CurrencyPatternDetector
from receipt_label.tests.markers import unit, fast, pattern_detection
from receipt_dynamo.entities import ReceiptWord


@unit
@fast 
@pattern_detection
class TestCurrencyPatternDetector:
    """Test currency pattern detection functionality."""

    @pytest.fixture
    def detector(self):
        """Currency detector fixture."""
        return CurrencyPatternDetector()

    @pytest.fixture
    def mock_receipt_words(self):
        """Mock receipt words for testing."""
        return [
            ReceiptWord(
                image_id="IMG001",
                receipt_id=1,
                line_id=1,
                word_id=1,
                text="$12.99",
                x1=100, y1=100, x2=150, y2=120
            ),
            ReceiptWord(
                image_id="IMG001",
                receipt_id=1,
                line_id=2,
                word_id=1,
                text="not-currency",
                x1=100, y1=130, x2=180, y2=150
            )
        ]

    @pytest.mark.parametrize("text,expected_match,min_confidence", [
        # Standard currency formats
        ("$12.99", True, 0.90),
        ("€45.50", True, 0.85),
        ("£100.00", True, 0.85),
        ("¥1,500", True, 0.80),
        
        # Complex formats
        ("$1,234.56", True, 0.90),
        ("$1,234,567.89", True, 0.90),
        ("12.99", True, 0.70),  # No symbol, lower confidence
        
        # Edge cases
        ("$0.01", True, 0.80),
        ("$0.00", True, 0.80),
        ("$999,999.99", True, 0.90),
        
        # Invalid formats
        ("$", False, 0.0),
        ("not-currency", False, 0.0),
        ("", False, 0.0),
        ("$12.999", False, 0.0),  # Too many decimal places
        ("$12,34", False, 0.0),   # Wrong comma placement
    ])
    def test_currency_pattern_detection(self, detector, text, expected_match, min_confidence):
        """Test currency pattern detection with various formats."""
        # Create mock word
        word = ReceiptWord(
            image_id="IMG001",
            receipt_id=1,
            line_id=1,
            word_id=1,
            text=text,
            x1=100, y1=100, x2=150, y2=120
        )
        
        # Detect patterns
        results = detector.detect_patterns([word])
        
        if expected_match:
            assert len(results) > 0
            result = results[0]
            assert result.confidence >= min_confidence
            assert result.pattern_type == "CURRENCY"
            assert result.text == text
        else:
            assert len(results) == 0

    @pytest.mark.parametrize("position_percentile,context,expected_label", [
        # Bottom of receipt = likely totals
        (0.85, {"right": "TOTAL"}, "GRAND_TOTAL"),
        (0.90, {"left": "TAX"}, "TAX_AMOUNT"), 
        (0.80, {"right": "SUBTOTAL"}, "SUBTOTAL"),
        
        # Middle of receipt = likely items/prices
        (0.50, {"left": "Big Mac"}, "UNIT_PRICE"),
        (0.40, {"right": "QTY 2"}, "UNIT_PRICE"),
        
        # Top of receipt = likely change/balance
        (0.15, {"left": "CHANGE"}, "CHANGE_AMOUNT"),
        
        # No context = generic currency
        (0.50, {}, "CURRENCY"),
    ])
    def test_currency_context_analysis(self, detector, position_percentile, context, expected_label):
        """Test currency label assignment based on position and context."""
        # Create word with position data
        word = ReceiptWord(
            image_id="IMG001",
            receipt_id=1,
            line_id=1,
            word_id=1,
            text="$12.99",
            x1=100, y1=int(position_percentile * 1000), x2=150, y2=int(position_percentile * 1000) + 20
        )
        
        # Mock context data
        with pytest.mock.patch.object(detector, '_get_context_metadata', return_value=context):
            with pytest.mock.patch.object(detector, '_calculate_position_percentile', return_value=position_percentile):
                results = detector.detect_patterns([word])
        
        assert len(results) == 1
        result = results[0]
        assert expected_label in result.suggested_labels

    def test_batch_currency_detection(self, detector, mock_receipt_words):
        """Test batch processing of multiple words."""
        results = detector.detect_patterns(mock_receipt_words)
        
        # Should only find currency in "$12.99"
        assert len(results) == 1
        assert results[0].text == "$12.99"
        assert results[0].confidence > 0.9

    def test_performance_timing(self, detector, performance_timer):
        """Test detection performance meets requirements."""
        # Create large batch of words
        words = []
        for i in range(100):
            words.append(ReceiptWord(
                image_id="IMG001",
                receipt_id=1,
                line_id=i,
                word_id=1,
                text=f"${i}.99",
                x1=100, y1=100 + i * 20, x2=150, y2=120 + i * 20
            ))
        
        # Time the detection
        performance_timer.start()
        results = detector.detect_patterns(words)
        elapsed = performance_timer.stop()
        
        # Should process 100 words in < 1 second
        assert elapsed < 1.0
        assert len(results) == 100  # All should match

    def test_confidence_scoring(self, detector):
        """Test confidence scores for different currency formats."""
        test_cases = [
            ("$12.99", 0.95),    # Perfect format
            ("€45.50", 0.90),    # International
            ("12.99", 0.75),     # No symbol
            ("$1,234.56", 0.95), # With comma
        ]
        
        for text, expected_min_confidence in test_cases:
            word = ReceiptWord(
                image_id="IMG001", receipt_id=1, line_id=1, word_id=1,
                text=text, x1=100, y1=100, x2=150, y2=120
            )
            
            results = detector.detect_patterns([word])
            assert len(results) == 1
            assert results[0].confidence >= expected_min_confidence

    @pytest.mark.parametrize("invalid_input", [
        None,
        [],
        [None],
        ["not a ReceiptWord object"]
    ])
    def test_invalid_input_handling(self, detector, invalid_input):
        """Test handling of invalid inputs."""
        if invalid_input is None:
            with pytest.raises(TypeError):
                detector.detect_patterns(invalid_input)
        else:
            # Should return empty results for invalid input
            results = detector.detect_patterns(invalid_input)
            assert results == []

    def test_unicode_currency_symbols(self, detector):
        """Test detection of various Unicode currency symbols."""
        unicode_currencies = [
            ("₹1,234.56", "INR"),  # Indian Rupee
            ("¥1,500", "JPY"),     # Japanese Yen
            ("₽1,000", "RUB"),     # Russian Ruble
            ("₩50,000", "KRW"),    # Korean Won
        ]
        
        for text, currency_code in unicode_currencies:
            word = ReceiptWord(
                image_id="IMG001", receipt_id=1, line_id=1, word_id=1,
                text=text, x1=100, y1=100, x2=150, y2=120
            )
            
            results = detector.detect_patterns([word])
            assert len(results) == 1
            result = results[0]
            assert result.confidence > 0.8
            # Could add currency_code to metadata if needed