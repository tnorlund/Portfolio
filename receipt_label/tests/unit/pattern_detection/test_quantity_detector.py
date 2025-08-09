"""Unit tests for quantity pattern detection."""

import pytest
from unittest.mock import Mock, patch

from receipt_label.pattern_detection.quantity import QuantityPatternDetector
from receipt_label.tests.markers import unit, fast, pattern_detection
from receipt_dynamo.entities import ReceiptWord


@unit
@fast
@pattern_detection
class TestQuantityPatternDetector:
    """Test quantity pattern detection functionality."""

    @pytest.fixture
    def detector(self):
        """Quantity detector fixture."""
        return QuantityPatternDetector()

    @pytest.mark.parametrize("text,expected_match,expected_label,min_confidence", [
        # Quantity with @ symbol (highest confidence)
        ("2 @ $5.99", True, "QUANTITY_PRICE", 0.95),
        ("1 @ $12.99", True, "QUANTITY_PRICE", 0.95),
        ("3 @ $2.50", True, "QUANTITY_PRICE", 0.95),
        ("10 @ $1.00", True, "QUANTITY_PRICE", 0.95),
        ("1.5 @ $3.99", True, "QUANTITY_PRICE", 0.90),  # Decimal quantity
        
        # Quantity patterns
        ("QTY: 3", True, "QUANTITY", 0.90),
        ("QTY 5", True, "QUANTITY", 0.85),
        ("Qty: 2", True, "QUANTITY", 0.85),
        ("QUANTITY: 4", True, "QUANTITY", 0.90),
        ("COUNT: 6", True, "QUANTITY", 0.80),
        
        # Weight measurements
        ("2.5 LBS", True, "WEIGHT", 0.85),
        ("1.2 lb", True, "WEIGHT", 0.85),
        ("3.0 pounds", True, "WEIGHT", 0.80),
        ("0.75 KG", True, "WEIGHT", 0.85),
        ("500 grams", True, "WEIGHT", 0.80),
        ("16 oz", True, "WEIGHT", 0.85),
        ("2 ounces", True, "WEIGHT", 0.80),
        
        # Volume measurements
        ("1 gallon", True, "VOLUME", 0.85),
        ("2.5 gal", True, "VOLUME", 0.85),
        ("16 fl oz", True, "VOLUME", 0.85),
        ("500 ml", True, "VOLUME", 0.85),
        ("2 liters", True, "VOLUME", 0.80),
        ("1 pint", True, "VOLUME", 0.80),
        ("2 quarts", True, "VOLUME", 0.80),
        
        # Count/piece indicators
        ("3 pieces", True, "COUNT", 0.80),
        ("5 pcs", True, "COUNT", 0.80),
        ("2 items", True, "COUNT", 0.75),
        ("1 each", True, "COUNT", 0.75),
        ("6 pack", True, "COUNT", 0.85),  # Common retail
        ("12-pack", True, "COUNT", 0.85),
        
        # Unit pricing patterns
        ("$2.99/lb", True, "UNIT_PRICE", 0.90),
        ("$1.50 per pound", True, "UNIT_PRICE", 0.85),
        ("$3.99/kg", True, "UNIT_PRICE", 0.90),
        ("$0.99 each", True, "UNIT_PRICE", 0.85),
        ("$5.00/gallon", True, "UNIT_PRICE", 0.90),
        
        # Size indicators
        ("Large", True, "SIZE", 0.70),
        ("Medium", True, "SIZE", 0.70),
        ("Small", True, "SIZE", 0.70),
        ("XL", True, "SIZE", 0.75),
        ("Size: L", True, "SIZE", 0.80),
        
        # Invalid patterns that should NOT match
        ("@ $5.99", False, None, 0.0),     # Missing quantity
        ("2 @", False, None, 0.0),         # Missing price
        ("random text", False, None, 0.0), # Not quantity-related
        ("", False, None, 0.0),            # Empty
        ("$5.99", False, None, 0.0),       # Just price, no quantity
        ("2", False, None, 0.0),           # Just number, no context
    ])
    def test_quantity_pattern_detection(self, detector, text, expected_match, expected_label, min_confidence):
        """Test quantity pattern detection with various formats."""
        word = ReceiptWord(
            image_id="IMG001", receipt_id=1, line_id=1, word_id=1,
            text=text, x1=100, y1=100, x2=200, y2=120
        )
        
        results = detector.detect_patterns([word])
        
        if expected_match:
            assert len(results) > 0, f"Expected match for '{text}' but got no results"
            result = results[0]
            assert result.confidence >= min_confidence, f"Confidence {result.confidence} < {min_confidence} for '{text}'"
            assert expected_label in result.suggested_labels, f"Expected {expected_label} in {result.suggested_labels} for '{text}'"
            assert result.text == text
        else:
            assert len(results) == 0, f"Expected no match for '{text}' but got {len(results)} results"

    def test_quantity_at_price_parsing(self, detector):
        """Test parsing of quantity @ price patterns."""
        at_price_cases = [
            ("2 @ $5.99", {"quantity": 2, "unit_price": 5.99, "total": 11.98}),
            ("1 @ $12.99", {"quantity": 1, "unit_price": 12.99, "total": 12.99}),
            ("3 @ $2.50", {"quantity": 3, "unit_price": 2.50, "total": 7.50}),
            ("0.5 @ $8.99", {"quantity": 0.5, "unit_price": 8.99, "total": 4.495}),
            ("10 @ $1.00", {"quantity": 10, "unit_price": 1.00, "total": 10.00}),
        ]
        
        for text, expected_values in at_price_cases:
            word = ReceiptWord(
                image_id="IMG001", receipt_id=1, line_id=1, word_id=1,
                text=text, x1=100, y1=100, x2=200, y2=120
            )
            
            results = detector.detect_patterns([word])
            assert len(results) > 0, f"Failed to detect: {text}"
            
            result = results[0]
            assert "QUANTITY_PRICE" in result.suggested_labels
            
            # If detector supports parsing, verify values
            if hasattr(result, 'parsed_values'):
                parsed = result.parsed_values
                assert parsed.get('quantity') == expected_values['quantity']
                assert parsed.get('unit_price') == expected_values['unit_price']
                assert abs(parsed.get('total', 0) - expected_values['total']) < 0.01

    def test_measurement_unit_recognition(self, detector):
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
            word = ReceiptWord(
                image_id="IMG001", receipt_id=1, line_id=1, word_id=1,
                text=text, x1=100, y1=100, x2=200, y2=120
            )
            
            results = detector.detect_patterns([word])
            assert len(results) > 0, f"Failed to detect measurement: {text}"
            
            result = results[0]
            assert expected_type in result.suggested_labels, f"Expected {expected_type} for {text}"
            
            # If detector supports unit extraction, verify
            if hasattr(result, 'unit_type'):
                assert expected_unit in result.unit_type.lower() or result.unit_type.lower() in expected_unit

    def test_size_classification(self, detector):
        """Test classification of size indicators."""
        size_cases = [
            # Standard sizes
            ("Small", True, 0.70),
            ("Medium", True, 0.70),
            ("Large", True, 0.70),
            ("Extra Large", True, 0.75),
            
            # Abbreviated sizes
            ("S", True, 0.65),
            ("M", True, 0.65),
            ("L", True, 0.65),
            ("XL", True, 0.75),
            ("XXL", True, 0.75),
            
            # Size with context
            ("Size: Large", True, 0.85),
            ("Size L", True, 0.80),
            ("Large Size", True, 0.80),
            
            # Non-size words that might be confused
            ("Regular", False, 0.0),  # Could be size but ambiguous
            ("Standard", False, 0.0), # Could be size but ambiguous
            ("Normal", False, 0.0),   # Not typically a size
        ]
        
        for text, should_match, min_confidence in size_cases:
            word = ReceiptWord(
                image_id="IMG001", receipt_id=1, line_id=1, word_id=1,
                text=text, x1=100, y1=100, x2=200, y2=120
            )
            
            results = detector.detect_patterns([word])
            
            if should_match:
                assert len(results) > 0, f"Should detect size indicator: {text}"
                result = results[0]
                assert result.confidence >= min_confidence
                assert "SIZE" in result.suggested_labels
            else:
                # Should either not detect or have low confidence
                if results:
                    assert results[0].confidence < 0.6, f"Should have low confidence for ambiguous size: {text}"

    def test_context_aware_quantity_detection(self, detector):
        """Test context-aware quantity detection using nearby words."""
        # Test with context words (mocked)
        context_cases = [
            # Number next to price indicator - likely quantity
            ("2", {"right": "$5.99"}, "QUANTITY", 0.80),
            ("3", {"right": "$12.99"}, "QUANTITY", 0.80),
            
            # Number next to item name - likely quantity  
            ("1", {"right": "Banana"}, "QUANTITY", 0.75),
            ("5", {"left": "Apple", "right": "count"}, "QUANTITY", 0.85),
            
            # Number in isolation - lower confidence
            ("2", {}, "QUANTITY", 0.50),
            
            # Number with measurement context
            ("2.5", {"right": "lbs"}, "WEIGHT_VALUE", 0.85),
            ("16", {"right": "oz"}, "WEIGHT_VALUE", 0.85),
        ]
        
        for text, context, expected_label, min_confidence in context_cases:
            word = ReceiptWord(
                image_id="IMG001", receipt_id=1, line_id=1, word_id=1,
                text=text, x1=100, y1=100, x2=200, y2=120
            )
            
            with patch.object(detector, '_get_context_metadata', return_value=context):
                results = detector.detect_patterns([word])
            
            if min_confidence > 0:
                assert len(results) > 0, f"Should detect with context: {text} + {context}"
                result = results[0]
                assert result.confidence >= min_confidence
                # Should have quantity-related label
                quantity_labels = ["QUANTITY", "WEIGHT_VALUE", "VOLUME_VALUE", expected_label]
                found_quantity = any(label in result.suggested_labels for label in quantity_labels)
                assert found_quantity, f"Expected quantity label for: {text}"

    def test_decimal_quantity_handling(self, detector):
        """Test handling of decimal quantities."""
        decimal_cases = [
            ("1.5 @ $3.99", True, 0.90),
            ("0.75 lbs", True, 0.85),
            ("2.25 kg", True, 0.85),
            ("1.33 gal", True, 0.80),
            ("0.5 each", True, 0.75),
            
            # Edge cases
            ("0.1 @ $10.00", True, 0.85),  # Very small quantity
            ("99.99 @ $0.01", True, 0.85), # Very large quantity
        ]
        
        for text, should_match, min_confidence in decimal_cases:
            word = ReceiptWord(
                image_id="IMG001", receipt_id=1, line_id=1, word_id=1,
                text=text, x1=100, y1=100, x2=200, y2=120
            )
            
            results = detector.detect_patterns([word])
            
            if should_match:
                assert len(results) > 0, f"Should detect decimal quantity: {text}"
                assert results[0].confidence >= min_confidence

    def test_unit_price_calculation_patterns(self, detector):
        """Test detection of unit price calculation patterns.""" 
        unit_price_cases = [
            # Per-unit pricing
            ("$2.99/lb", "UNIT_PRICE", 0.90),
            ("$1.50 per pound", "UNIT_PRICE", 0.85),
            ("$3.99/kg", "UNIT_PRICE", 0.90),
            ("$0.99 each", "UNIT_PRICE", 0.85),
            ("$5.00/gallon", "UNIT_PRICE", 0.90),
            ("$2.50/dozen", "UNIT_PRICE", 0.85),
            
            # Rate patterns
            ("$15/hour", "RATE", 0.80),  # Less common on receipts
            ("$25/day", "RATE", 0.75),   # Less common on receipts
            
            # Bulk pricing
            ("3 for $5.00", "BULK_PRICE", 0.85),
            ("2 for $3.99", "BULK_PRICE", 0.85),
            ("Buy 2 get 1", "BULK_PRICE", 0.80),
        ]
        
        for text, expected_label, min_confidence in unit_price_cases:
            word = ReceiptWord(
                image_id="IMG001", receipt_id=1, line_id=1, word_id=1,
                text=text, x1=100, y1=100, x2=200, y2=120
            )
            
            results = detector.detect_patterns([word])
            assert len(results) > 0, f"Should detect unit price pattern: {text}"
            
            result = results[0]
            assert result.confidence >= min_confidence
            # Should have pricing-related label
            price_labels = ["UNIT_PRICE", "RATE", "BULK_PRICE", expected_label]
            found_price = any(label in result.suggested_labels for label in price_labels)
            assert found_price, f"Expected price label for: {text}"

    def test_grocery_specific_quantities(self, detector):
        """Test grocery-specific quantity patterns."""
        grocery_cases = [
            # Produce items
            ("3.2 lbs Bananas", True, "PRODUCE_WEIGHT", 0.80),
            ("2.5 lb Apples", True, "PRODUCE_WEIGHT", 0.80),
            ("1 bunch Grapes", True, "PRODUCE_COUNT", 0.75),
            
            # Packaged goods
            ("12-pack Soda", True, "PACKAGE_COUNT", 0.85),
            ("6-pack Beer", True, "PACKAGE_COUNT", 0.85),
            ("1 dozen Eggs", True, "PACKAGE_COUNT", 0.85),
            ("2 bags Chips", True, "PACKAGE_COUNT", 0.80),
            
            # Deli/meat counter
            ("0.75 lb Sliced Ham", True, "DELI_WEIGHT", 0.80),
            ("1.2 pounds Turkey", True, "DELI_WEIGHT", 0.75),
        ]
        
        for text, should_match, expected_label, min_confidence in grocery_cases:
            word = ReceiptWord(
                image_id="IMG001", receipt_id=1, line_id=1, word_id=1,
                text=text, x1=100, y1=100, x2=200, y2=120
            )
            
            results = detector.detect_patterns([word])
            
            if should_match:
                assert len(results) > 0, f"Should detect grocery quantity: {text}"
                result = results[0]
                assert result.confidence >= min_confidence
                # Should have quantity-related labels
                quantity_labels = ["WEIGHT", "COUNT", "QUANTITY", expected_label]
                found_quantity = any(label in result.suggested_labels for label in quantity_labels)
                assert found_quantity, f"Expected quantity label for: {text}"

    def test_confidence_scoring_accuracy(self, detector):
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
                word = ReceiptWord(
                    image_id="IMG001", receipt_id=1, line_id=1, word_id=1,
                    text=text, x1=100, y1=100, x2=200, y2=120
                )
                
                results = detector.detect_patterns([word])
                if results:  # Some low confidence cases might not match
                    confidences.append(results[0].confidence)
            
            if confidences:
                avg_confidence = sum(confidences) / len(confidences)
                assert avg_confidence >= min_confidence * 0.9, f"Average confidence {avg_confidence:.2f} too low for tier with min {min_confidence}"

    def test_batch_processing_performance(self, detector, performance_timer):
        """Test efficient batch processing of quantity patterns."""
        # Create mixed batch of quantity-related content
        quantity_batch = [
            "2 @ $5.99", "QTY: 3", "2.5 lbs", "Large", 
            "1 gallon", "6-pack", "$2.99/lb", "Medium",
            "not-quantity", "random", "text", "123"  # Non-quantity items
        ] * 8  # 96 total items
        
        batch_words = []
        for i, text in enumerate(quantity_batch):
            batch_words.append(ReceiptWord(
                image_id="IMG001", receipt_id=1, line_id=i, word_id=1,
                text=text, x1=100, y1=100 + i * 5, x2=200, y2=120 + i * 5
            ))
        
        performance_timer.start()
        results = detector.detect_patterns(batch_words)
        elapsed = performance_timer.stop()
        
        # Should process efficiently
        assert elapsed < 2.0, f"Batch processing took {elapsed:.2f}s, should be <2s"
        
        # Should detect reasonable number of patterns (64 quantity items, 32 non-quantity)
        assert len(results) >= 40, f"Should detect most quantity patterns, got {len(results)}"
        assert len(results) <= 80, f"Should not over-detect, got {len(results)}"

    def test_edge_case_and_error_handling(self, detector):
        """Test handling of edge cases and malformed input."""
        edge_cases = [
            # Malformed quantity patterns
            ("@ $5.99", False),          # Missing quantity
            ("2 @", False),              # Missing price  
            ("QTY:", False),             # Missing quantity value
            ("lbs", False),              # Missing weight value
            
            # Boundary values
            ("0 @ $5.99", True),         # Zero quantity (valid)
            ("999999 @ $0.01", True),    # Very large quantity
            ("0.001 lbs", True),         # Very small weight
            
            # Special characters
            ("2½ lbs", True),            # Fraction character
            ("1¼ cups", True),           # Fraction in volume
            
            # Empty/invalid input
            ("", False),
            ("   ", False),
            ("@#$%", False),
        ]
        
        for text, should_have_result in edge_cases:
            word = ReceiptWord(
                image_id="IMG001", receipt_id=1, line_id=1, word_id=1,
                text=text, x1=100, y1=100, x2=200, y2=120
            )
            
            try:
                results = detector.detect_patterns([word])
                
                if should_have_result:
                    assert len(results) > 0, f"Should detect edge case: '{text}'"
                else:
                    assert len(results) == 0, f"Should not detect malformed pattern: '{text}'"
                    
            except Exception as e:
                pytest.fail(f"Should handle edge case gracefully: '{text}', got {e}")

    def test_quantity_pattern_types(self, detector):
        """Test that quantity patterns are correctly typed."""
        type_consistency_cases = [
            ("2 @ $5.99", "QUANTITY", ["QUANTITY_PRICE"]),
            ("2.5 lbs", "QUANTITY", ["WEIGHT"]),
            ("Large", "QUANTITY", ["SIZE"]),
            ("QTY: 3", "QUANTITY", ["QUANTITY"]),
        ]
        
        for text, expected_pattern_type, expected_labels in type_consistency_cases:
            word = ReceiptWord(
                image_id="IMG001", receipt_id=1, line_id=1, word_id=1,
                text=text, x1=100, y1=100, x2=200, y2=120
            )
            
            results = detector.detect_patterns([word])
            assert len(results) > 0, f"Failed to detect: {text}"
            
            result = results[0]
            
            # Should have correct pattern type
            assert result.pattern_type == expected_pattern_type or any(label in result.suggested_labels for label in expected_labels)
            
            # Should have valid structure
            assert isinstance(result.suggested_labels, list)
            assert len(result.suggested_labels) > 0
            assert 0 <= result.confidence <= 1