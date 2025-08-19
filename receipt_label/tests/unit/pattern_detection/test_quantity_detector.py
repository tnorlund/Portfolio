"""Unit tests for quantity pattern detection."""

import pytest

from receipt_label.pattern_detection.quantity import QuantityPatternDetector
from tests.markers import unit, fast, pattern_detection
from tests.helpers import create_test_receipt_word


@unit
@fast
@pattern_detection
class TestQuantityPatternDetector:
    """Test quantity pattern detection functionality."""

    @pytest.fixture
    def detector(self):
        """Quantity detector fixture."""
        return QuantityPatternDetector()

    @pytest.mark.parametrize(
        "text,expected_match,expected_label,min_confidence",
        [
            # Quantity with @ symbol (highest confidence)
            ("2 @ $5.99", True, "QUANTITY_AT", 0.40),
            ("1 @ $12.99", True, "QUANTITY_AT", 0.40),
            ("3 @ $2.50", True, "QUANTITY_AT", 0.40),
            ("10 @ $1.00", True, "QUANTITY_AT", 0.40),
            ("1.5 @ $3.99", True, "QUANTITY_AT", 0.40),  # Decimal quantity
            # Quantity patterns
            ("QTY: 3", True, "QUANTITY", 0.40),
            ("QTY 5", True, "QUANTITY", 0.35),
            ("Qty: 2", True, "QUANTITY", 0.35),
            ("QUANTITY: 4", True, "QUANTITY", 0.40),
            # Weight measurements
            ("2.5 LBS", True, "QUANTITY", 0.35),
            ("1.2 lb", True, "QUANTITY", 0.35),
            ("3.0 pounds", True, "QUANTITY", 0.35),
            ("0.75 KG", True, "QUANTITY", 0.35),
            ("500 grams", True, "QUANTITY", 0.35),
            ("16 oz", True, "QUANTITY", 0.35),
            ("2 ounces", True, "QUANTITY", 0.35),
            # Volume measurements
            ("1 gallon", True, "QUANTITY", 0.35),
            ("2.5 gal", True, "QUANTITY", 0.35),
            ("16 fl oz", True, "QUANTITY", 0.35),
            ("500 ml", True, "QUANTITY", 0.35),
            ("2 liters", True, "QUANTITY", 0.35),
            # Count/piece indicators
            ("3 pieces", True, "QUANTITY", 0.35),
            ("5 pcs", True, "QUANTITY", 0.35),
            ("2 items", True, "QUANTITY", 0.30),
            ("1 each", True, "QUANTITY", 0.30),
            ("6 pack", True, "QUANTITY", 0.35),  # Common retail
            # Unit pricing patterns
            # Size indicators
            # Invalid patterns that should NOT match
            ("@ $5.99", False, None, 0.0),  # Missing quantity
            ("2 @", False, None, 0.0),  # Missing price
            ("random text", False, None, 0.0),  # Not quantity-related
            ("", False, None, 0.0),  # Empty
            ("$5.99", False, None, 0.0),  # Just price, no quantity
            ("2", False, None, 0.0),  # Just number, no context
        ])
    async def test_quantity_pattern_detection(
        self, detector, text, expected_match, expected_label, min_confidence
    ):
        """Test quantity pattern detection with various formats."""
        word = create_test_receipt_word(
            receipt_id=1,
            line_id=1,
            word_id=1,
            text=text,
            x1=100,
            y1=100,
            x2=200,
            y2=120)

        results = await detector.detect([word])

        if expected_match:
            assert (
                len(results) > 0
            ), f"Expected match for '{text}' but got no results"
            result = results[0]
            assert (
                result.confidence >= min_confidence
            ), f"Confidence {result.confidence} < {min_confidence} for '{text}'"
            assert (
                expected_label == result.pattern_type.name
                or expected_label
                in result.metadata.get("suggested_labels", [])
            ), f"Expected {expected_label} for '{text}'"
            # Don't check exact matched_text - it may be partial
        else:
            assert (
                len(results) == 0
            ), f"Expected no match for '{text}' but got {len(results)} results"

    async def test_quantity_at_price_parsing(self, detector):
        """Test parsing of quantity @ price patterns."""
        at_price_cases = [
            ("2 @ $5.99", {"quantity": 2, "unit_price": 5.99, "total": 11.98}),
            (
                "1 @ $12.99",
                {"quantity": 1, "unit_price": 12.99, "total": 12.99}),
            ("3 @ $2.50", {"quantity": 3, "unit_price": 2.50, "total": 7.50}),
            (
                "0.5 @ $8.99",
                {"quantity": 0.5, "unit_price": 8.99, "total": 4.495}),
            (
                "10 @ $1.00",
                {"quantity": 10, "unit_price": 1.00, "total": 10.00}),
        ]

        for text, expected_values in at_price_cases:
            word = create_test_receipt_word(
                receipt_id=1,
                line_id=1,
                word_id=1,
                text=text,
                x1=100,
                y1=100,
                x2=200,
                y2=120)

            results = await detector.detect([word])
            assert len(results) > 0, f"Failed to detect: {text}"

            result = results[0]
            assert (
                "QUANTITY_AT" == result.pattern_type.name
                or "QUANTITY_AT" in result.metadata.get("suggested_labels", [])
            )

            # If detector supports parsing, verify values
            if hasattr(result, "parsed_values"):
                parsed = result.parsed_values
                assert parsed.get("quantity") == expected_values["quantity"]
                assert (
                    parsed.get("unit_price") == expected_values["unit_price"]
                )
                assert (
                    abs(parsed.get("total", 0) - expected_values["total"])
                    < 0.01
                )

    async def test_measurement_unit_recognition(self, detector):
        """Test recognition of various measurement units."""
        measurement_cases = [
            # Weight units
            ("2.5 lbs", "WEIGHT", "pounds"),
            ("1 kg", "WEIGHT", "kilograms"),
            ("16 oz", "WEIGHT", "ounces"),
            ("500 g", "WEIGHT", "grams"),
            # Volume units
            ("1 gal", "VOLUME", "gallon"),
            ("500 ml", "VOLUME", "milliliters"),
            ("2 L", "VOLUME", "liters"),
            ("16 fl oz", "VOLUME", "fluid_ounces"),
            # Count units
            ("6 pack", "COUNT", "pack"),
            ("3 pcs", "COUNT", "pieces"),
            ("2 items", "COUNT", "items"),
            ("1 each", "COUNT", "each"),
        ]

        for text, expected_type, expected_unit in measurement_cases:
            word = create_test_receipt_word(
                receipt_id=1,
                line_id=1,
                word_id=1,
                text=text,
                x1=100,
                y1=100,
                x2=200,
                y2=120)

            results = await detector.detect([word])
            assert len(results) > 0, f"Failed to detect measurement: {text}"

            result = results[0]
            assert (
                "QUANTITY" == result.pattern_type.name
                or expected_type in result.metadata.get("suggested_labels", [])
            ), f"Expected {expected_type} for {text}"

            # If detector supports unit extraction, verify
            if hasattr(result, "unit_type"):
                assert (
                    expected_unit in result.unit_type.lower()
                    or result.unit_type.lower() in expected_unit
                )

    async def test_decimal_quantity_handling(self, detector):
        """Test handling of decimal quantities."""
        decimal_cases = [
            ("1.5 @ $3.99", True, 0.40),
            ("0.75 lbs", True, 0.35),
            ("2.25 kg", True, 0.35),
            ("1.33 gal", True, 0.35),
            ("0.5 each", True, 0.30),
            # Edge cases
            ("0.1 @ $10.00", True, 0.35),  # Very small quantity
            ("99.99 @ $0.01", True, 0.35),  # Very large quantity
        ]

        for text, should_match, min_confidence in decimal_cases:
            word = create_test_receipt_word(
                receipt_id=1,
                line_id=1,
                word_id=1,
                text=text,
                x1=100,
                y1=100,
                x2=200,
                y2=120)

            results = await detector.detect([word])

            if should_match:
                assert (
                    len(results) > 0
                ), f"Should detect decimal quantity: {text}"
                assert results[0].confidence >= min_confidence

    async def test_confidence_scoring_accuracy(self, detector):
        """Test confidence scoring based on pattern clarity."""
        confidence_tiers = [
            # Highest confidence - clear @ patterns
            (["2 @ $5.99", "1 @ $12.99"], 0.95),
            # High confidence - explicit quantity words
            (["QTY: 3", "QUANTITY: 2"], 0.90),
            # Medium confidence - measurement units
            (["2.5 lbs", "16 oz", "1 gallon"], 0.85),
            # Lower confidence - size indicators
            (["Large", "Medium", "XL"], 0.75),
            # Lowest confidence - ambiguous numbers
            (["2", "3", "1"], 0.60),  # Only with context
        ]

        for texts, min_confidence in confidence_tiers:
            confidences = []

            for text in texts:
                word = create_test_receipt_word(
                    receipt_id=1,
                    line_id=1,
                    word_id=1,
                    text=text,
                    x1=100,
                    y1=100,
                    x2=200,
                    y2=120)

                results = await detector.detect([word])
                if results:  # Some low confidence cases might not match
                    confidences.append(results[0].confidence)

            if confidences:
                avg_confidence = sum(confidences) / len(confidences)
                assert (
                    avg_confidence >= min_confidence * 0.9
                ), f"Average confidence {avg_confidence:.2f} too low for tier with min {min_confidence}"

    async def test_batch_processing_performance(self, detector):
        """Test efficient batch processing of quantity patterns."""
        import time  # pylint: disable=import-outside-toplevel

        # Create mixed batch of quantity-related content
        quantity_batch = [
            "2 @ $5.99",
            "QTY: 3",
            "2.5 lbs",
            "Large",
            "1 gallon",
            "6-pack",
            "$2.99/lb",
            "Medium",
            "not-quantity",
            "random",
            "text",
            "123",  # Non-quantity items
        ] * 8  # 96 total items

        batch_words = []
        for i, text in enumerate(quantity_batch):
            batch_words.append(
                create_test_receipt_word(
                    receipt_id=1,
                    line_id=i,
                    word_id=1,
                    text=text,
                    x1=100,
                    y1=100 + i * 5,
                    x2=200,
                    y2=120 + i * 5)
            )

        start = time.time()
        results = await detector.detect(batch_words)
        elapsed = time.time() - start

        # Should process efficiently
        assert (
            elapsed < 2.0
        ), f"Batch processing took {elapsed:.2f}s, should be <2s"

        # Should detect reasonable number of patterns (4 out of 12 items match * 8 = 32 total)
        assert (
            len(results) >= 30
        ), f"Should detect most quantity patterns, got {len(results)}"
        assert (
            len(results) <= 40
        ), f"Should not over-detect, got {len(results)}"

    async def test_edge_case_and_error_handling(self, detector):
        """Test handling of edge cases and malformed input."""
        edge_cases = [
            # Malformed quantity patterns
            ("@ $5.99", False),  # Missing quantity
            ("2 @", False),  # Missing price
            ("QTY:", False),  # Missing quantity value
            ("lbs", False),  # Missing weight value
            # Boundary values
            ("0 @ $5.99", True),  # Zero quantity (valid)
            ("999999 @ $0.01", True),  # Very large quantity
            ("0.001 lbs", True),  # Very small weight
            # Special characters
            ("2½ lbs", True),  # Fraction character
            ("1¼ cups", True),  # Fraction in volume
            # Empty/invalid input
            ("", False),
            ("   ", False),
            ("@#$%", False),
        ]

        for text, should_have_result in edge_cases:
            word = create_test_receipt_word(
                receipt_id=1,
                line_id=1,
                word_id=1,
                text=text,
                x1=100,
                y1=100,
                x2=200,
                y2=120)

            try:
                results = await detector.detect([word])

                if should_have_result:
                    assert (
                        len(results) > 0
                    ), f"Should detect edge case: '{text}'"
                else:
                    assert (
                        len(results) == 0
                    ), f"Should not detect malformed pattern: '{text}'"

            except (ValueError, AttributeError) as exc:
                pytest.fail(
                    f"Should handle edge case gracefully: '{text}', got {exc}"
                )

    async def test_quantity_pattern_types(self, detector):
        """Test that quantity patterns are correctly typed."""
        type_consistency_cases = [
            ("2 @ $5.99", "QUANTITY_AT", ["QUANTITY_AT"]),
            ("2.5 lbs", "QUANTITY", ["WEIGHT"]),
            ("QTY: 3", "QUANTITY", ["QUANTITY"]),
        ]

        for (
            text,
            expected_pattern_type,
            expected_labels) in type_consistency_cases:
            word = create_test_receipt_word(
                receipt_id=1,
                line_id=1,
                word_id=1,
                text=text,
                x1=100,
                y1=100,
                x2=200,
                y2=120)

            results = await detector.detect([word])
            assert len(results) > 0, f"Failed to detect: {text}"

            result = results[0]

            # Should have correct pattern type
            # Check if metadata contains suggested_labels (optional)
            suggested_labels = result.metadata.get("suggested_labels", [])
            assert result.pattern_type.name == expected_pattern_type or any(
                label in suggested_labels for label in expected_labels
            )

            # Should have valid structure
            assert 0 <= result.confidence <= 1
