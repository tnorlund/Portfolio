"""
Tests for enhanced pattern analyzer.
"""

from decimal import Decimal

import pytest

from receipt_label.data.pattern_analyzer import (
    EnhancedCurrencyAnalyzer,
    gpt_request_spatial_currency_analysis,
)


class TestEnhancedCurrencyAnalyzer:
    """Test the enhanced currency analyzer functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        self.analyzer = EnhancedCurrencyAnalyzer()

    def test_quantity_pattern_at_symbol(self):
        """Test detection of '2 @ $5.99' pattern."""
        context = {
            "amount": 11.98,
            "full_line": "PIZZA SLICE 2 @ $5.99",
            "left_text": "PIZZA SLICE",
            "line_id": 1,
            "x_position": 100,
            "y_position": 50,
        }

        result = self.analyzer._classify_currency_context(context, [], [])

        assert result["classification"] == "line_item"
        assert result["quantity"] == 2
        assert result["unit_price"] == 5.99
        assert result["quantity_pattern"] == "at_symbol"
        assert result["confidence"] == 0.90

    def test_quantity_pattern_qty_x(self):
        """Test detection of 'Qty: 3 x $4.50' pattern."""
        context = {
            "amount": 13.50,
            "full_line": "SODA Qty: 3 x $4.50",
            "left_text": "SODA",
            "line_id": 2,
            "x_position": 100,
            "y_position": 70,
        }

        result = self.analyzer._classify_currency_context(context, [], [])

        assert result["classification"] == "line_item"
        assert result["quantity"] == 3
        assert result["unit_price"] == 4.50
        assert result["quantity_pattern"] == "qty_x"

    def test_quantity_pattern_simple_x(self):
        """Test detection of '4 x $2.99' pattern."""
        context = {
            "amount": 11.96,
            "full_line": "CHIPS 4 x $2.99",
            "left_text": "CHIPS",
            "line_id": 3,
            "x_position": 100,
            "y_position": 90,
        }

        result = self.analyzer._classify_currency_context(context, [], [])

        assert result["classification"] == "line_item"
        assert result["quantity"] == 4
        assert result["unit_price"] == 2.99
        assert result["quantity_pattern"] == "simple_x"

    def test_quantity_pattern_slash(self):
        """Test detection of '2/$5.00' pattern."""
        context = {
            "amount": 5.00,
            "full_line": "CANDY 2/$5.00",
            "left_text": "CANDY",
            "line_id": 4,
            "x_position": 100,
            "y_position": 110,
        }

        result = self.analyzer._classify_currency_context(context, [], [])

        assert result["classification"] == "line_item"
        assert result["quantity"] == 2
        assert result["unit_price"] == 2.50  # $5.00 / 2
        assert result["quantity_pattern"] == "slash"

    def test_quantity_pattern_for_price(self):
        """Test detection of '3 for $10.00' pattern."""
        context = {
            "amount": 10.00,
            "full_line": "SPECIAL DEAL 3 for $10.00",
            "left_text": "SPECIAL DEAL",
            "line_id": 5,
            "x_position": 100,
            "y_position": 130,
        }

        result = self.analyzer._classify_currency_context(context, [], [])

        assert result["classification"] == "line_item"
        assert result["quantity"] == 3
        assert result["unit_price"] == pytest.approx(3.33, rel=0.01)
        assert result["quantity_pattern"] == "for_price"

    def test_subtotal_classification(self):
        """Test correct classification of subtotal."""
        context = {
            "amount": 45.50,
            "full_line": "SUBTOTAL 45.50",
            "left_text": "SUBTOTAL",
            "line_id": 10,
            "x_position": 100,
            "y_position": 200,
        }

        result = self.analyzer._classify_currency_context(context, [], [])

        assert result["classification"] == "subtotal"
        assert result["confidence"] == 0.85
        assert "quantity" not in result

    def test_tax_classification(self):
        """Test correct classification of tax."""
        context = {
            "amount": 3.64,
            "full_line": "SALES TAX 3.64",
            "left_text": "SALES TAX",
            "line_id": 11,
            "x_position": 100,
            "y_position": 220,
        }

        result = self.analyzer._classify_currency_context(context, [], [])

        assert result["classification"] == "tax"
        assert result["confidence"] == 0.85

    def test_total_classification(self):
        """Test correct classification of total."""
        context = {
            "amount": 49.14,
            "full_line": "TOTAL 49.14",
            "left_text": "TOTAL",
            "line_id": 12,
            "x_position": 100,
            "y_position": 240,
        }

        result = self.analyzer._classify_currency_context(context, [], [])

        assert result["classification"] == "total"
        assert result["confidence"] == 0.85

    def test_discount_classification(self):
        """Test correct classification of discount."""
        context = {
            "amount": -5.00,
            "full_line": "COUPON DISCOUNT -5.00",
            "left_text": "COUPON DISCOUNT",
            "line_id": 9,
            "x_position": 100,
            "y_position": 180,
        }

        result = self.analyzer._classify_currency_context(context, [], [])

        assert result["classification"] == "discount"
        assert result["confidence"] == 0.85

    def test_spatial_pattern_detection(self):
        """Test spatial pattern detection for aligned prices."""
        currency_contexts = [
            {"x_position": 450, "y_position": 50},
            {"x_position": 448, "y_position": 70},
            {"x_position": 452, "y_position": 90},
            {"x_position": 449, "y_position": 110},
        ]

        patterns = self.analyzer._detect_spatial_patterns(
            currency_contexts, []
        )

        assert len(patterns) > 0
        assert any(p["type"] == "right_aligned_prices" for p in patterns)

    def test_full_analysis_integration(self):
        """Test full spatial currency analysis integration."""
        currency_contexts = [
            {
                "amount": 15.98,
                "full_line": "BURGER 2 @ $7.99",
                "left_text": "BURGER",
                "line_id": 1,
                "x_position": 100,
                "y_position": 50,
            },
            {
                "amount": 8.97,
                "full_line": "FRIES 3 x $2.99",
                "left_text": "FRIES",
                "line_id": 2,
                "x_position": 100,
                "y_position": 70,
            },
            {
                "amount": 24.95,
                "full_line": "SUBTOTAL 24.95",
                "left_text": "SUBTOTAL",
                "line_id": 3,
                "x_position": 100,
                "y_position": 100,
            },
            {
                "amount": 2.00,
                "full_line": "TAX 2.00",
                "left_text": "TAX",
                "line_id": 4,
                "x_position": 100,
                "y_position": 120,
            },
            {
                "amount": 26.95,
                "full_line": "TOTAL 26.95",
                "left_text": "TOTAL",
                "line_id": 5,
                "x_position": 100,
                "y_position": 140,
            },
        ]

        result, query_desc, details = self.analyzer.analyze_spatial_currency(
            receipt=None,
            receipt_lines=[],
            receipt_words=[],
            currency_contexts=currency_contexts,
        )

        # Check line items
        assert len(result["line_items"]) == 2
        assert result["line_items"][0]["quantity"] == 2
        assert result["line_items"][0]["unit_price"] == 7.99
        assert result["line_items"][1]["quantity"] == 3
        assert result["line_items"][1]["unit_price"] == 2.99

        # Check financial fields
        assert result["subtotal"]["amount"] == 24.95
        assert result["tax"]["amount"] == 2.00
        assert result["total"]["amount"] == 26.95

        # Check analysis details
        assert "Enhanced Pattern Analysis Results" in details
        assert "quantity patterns detected" in details.lower()


class TestGPTReplacementFunction:
    """Test the GPT replacement function."""

    def test_gpt_function_uses_pattern_analyzer(self):
        """Test that the GPT function now uses pattern analyzer."""
        currency_contexts = [
            {
                "amount": 10.00,
                "full_line": "ITEM 2 @ $5.00",
                "left_text": "ITEM",
                "line_id": 1,
                "x_position": 100,
                "y_position": 50,
            }
        ]

        result, query_desc, details = gpt_request_spatial_currency_analysis(
            receipt_lines=[],
            receipt_words=[],
            currency_contexts=currency_contexts,
        )

        assert result is not None
        assert "line_items" in result
        assert len(result["line_items"]) == 1
        assert result["line_items"][0]["quantity"] == 2
        assert "Enhanced" in query_desc or "pattern" in query_desc

    def test_backwards_compatibility(self):
        """Test that old calling patterns still work."""
        # Test with minimal arguments (as it might be called from old code)
        result, query_desc, details = gpt_request_spatial_currency_analysis(
            receipt_lines=[], receipt_words=[]
        )

        assert result is not None
        assert "currency_amounts" in result
        assert isinstance(result["currency_amounts"], list)


class TestDescriptionCleaning:
    """Test description cleaning functionality."""

    def test_clean_description_removes_patterns(self):
        """Test that quantity patterns are removed from descriptions."""
        analyzer = EnhancedCurrencyAnalyzer()

        test_cases = [
            ("PIZZA 2 @ $5.99", "Pizza"),
            ("SODA Qty: 3 x $2.50", "Soda"),
            ("CHIPS 4 x $1.99", "Chips"),
            ("CANDY 2/$5.00", "Candy"),
            ("3 for $10.00 SPECIAL", "Special"),
            ("$5.99 BURGER MEAL", "Burger Meal"),
        ]

        for input_text, expected in test_cases:
            result = analyzer._clean_description(input_text)
            assert result == expected, f"Failed for input: {input_text}"


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_empty_contexts(self):
        """Test handling of empty currency contexts."""
        analyzer = EnhancedCurrencyAnalyzer()

        result, _, _ = analyzer.analyze_spatial_currency(
            receipt=None,
            receipt_lines=[],
            receipt_words=[],
            currency_contexts=[],
        )

        assert result["line_items"] == []
        assert result["subtotal"] is None
        assert result["tax"] is None
        assert result["total"] is None

    def test_malformed_quantity_patterns(self):
        """Test handling of malformed quantity patterns."""
        analyzer = EnhancedCurrencyAnalyzer()

        context = {
            "amount": 10.00,
            "full_line": "ITEM @ $abc",  # Invalid price
            "left_text": "ITEM",
            "line_id": 1,
            "x_position": 100,
            "y_position": 50,
        }

        result = analyzer._classify_currency_context(context, [], [])

        assert result["classification"] == "line_item"
        assert (
            "quantity" not in result
        )  # Should not crash, just not detect quantity
        assert (
            result["confidence"] == 0.75
        )  # Lower confidence without quantity
