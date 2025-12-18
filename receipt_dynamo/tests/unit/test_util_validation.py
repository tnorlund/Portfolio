"""Unit tests for entity validation utilities."""

import pytest

from receipt_dynamo.entities.util import (
    validate_confidence_range,
    validate_non_negative_int,
    validate_positive_dimensions,
    validate_positive_int,
)


class TestValidatePositiveInt:
    """Test cases for validate_positive_int utility function."""

    @pytest.mark.unit
    def test_validate_positive_int_valid(self):
        """Test validation passes for valid positive integers."""
        # Should not raise any exceptions
        validate_positive_int("receipt_id", 1)
        validate_positive_int("receipt_id", 100)
        validate_positive_int("receipt_id", 999999)

    @pytest.mark.unit
    def test_validate_positive_int_zero(self):
        """Test validation fails for zero."""
        with pytest.raises(ValueError, match="receipt_id must be positive"):
            validate_positive_int("receipt_id", 0)

    @pytest.mark.unit
    def test_validate_positive_int_negative(self):
        """Test validation fails for negative integers."""
        with pytest.raises(ValueError, match="receipt_id must be positive"):
            validate_positive_int("receipt_id", -1)
        with pytest.raises(ValueError, match="receipt_id must be positive"):
            validate_positive_int("receipt_id", -100)

    @pytest.mark.unit
    def test_validate_positive_int_not_integer(self):
        """Test validation fails for non-integer types."""
        with pytest.raises(ValueError, match="receipt_id must be an integer"):
            validate_positive_int("receipt_id", "1")
        with pytest.raises(ValueError, match="receipt_id must be an integer"):
            validate_positive_int("receipt_id", 1.5)
        with pytest.raises(ValueError, match="receipt_id must be an integer"):
            validate_positive_int("receipt_id", None)
        with pytest.raises(ValueError, match="receipt_id must be an integer"):
            validate_positive_int("receipt_id", [1])

    @pytest.mark.unit
    def test_validate_positive_int_custom_field_name(self):
        """Test validation uses custom field name in error messages."""
        with pytest.raises(ValueError, match="line_id must be an integer"):
            validate_positive_int("line_id", "invalid")
        with pytest.raises(ValueError, match="word_id must be positive"):
            validate_positive_int("word_id", 0)


class TestValidateNonNegativeInt:
    """Test cases for validate_non_negative_int utility function."""

    @pytest.mark.unit
    def test_validate_non_negative_int_valid(self):
        """Test validation passes for valid non-negative integers."""
        # Should not raise any exceptions
        validate_non_negative_int("line_id", 0)
        validate_non_negative_int("line_id", 1)
        validate_non_negative_int("line_id", 100)
        validate_non_negative_int("line_id", 999999)

    @pytest.mark.unit
    def test_validate_non_negative_int_negative(self):
        """Test validation fails for negative integers."""
        with pytest.raises(ValueError, match="line_id must be non-negative"):
            validate_non_negative_int("line_id", -1)
        with pytest.raises(ValueError, match="line_id must be non-negative"):
            validate_non_negative_int("line_id", -100)

    @pytest.mark.unit
    def test_validate_non_negative_int_not_integer(self):
        """Test validation fails for non-integer types."""
        with pytest.raises(ValueError, match="line_id must be an integer"):
            validate_non_negative_int("line_id", "0")
        with pytest.raises(ValueError, match="line_id must be an integer"):
            validate_non_negative_int("line_id", 0.5)
        with pytest.raises(ValueError, match="line_id must be an integer"):
            validate_non_negative_int("line_id", None)
        with pytest.raises(ValueError, match="line_id must be an integer"):
            validate_non_negative_int("line_id", [0])

    @pytest.mark.unit
    def test_validate_non_negative_int_custom_field_name(self):
        """Test validation uses custom field name in error messages."""
        with pytest.raises(ValueError, match="word_id must be an integer"):
            validate_non_negative_int("word_id", "invalid")
        with pytest.raises(ValueError, match="letter_id must be non-negative"):
            validate_non_negative_int("letter_id", -1)


class TestValidatePositiveDimensions:
    """Test cases for validate_positive_dimensions utility function."""

    @pytest.mark.unit
    def test_validate_positive_dimensions_valid(self):
        """Test validation passes for valid positive dimensions."""
        # Should not raise any exceptions
        validate_positive_dimensions(1, 1)
        validate_positive_dimensions(100, 200)
        validate_positive_dimensions(1920, 1080)
        validate_positive_dimensions(9999, 9999)

    @pytest.mark.unit
    def test_validate_positive_dimensions_zero_width(self):
        """Test validation fails when width is zero."""
        with pytest.raises(
            ValueError, match="width and height must be positive integers"
        ):
            validate_positive_dimensions(0, 100)

    @pytest.mark.unit
    def test_validate_positive_dimensions_zero_height(self):
        """Test validation fails when height is zero."""
        with pytest.raises(
            ValueError, match="width and height must be positive integers"
        ):
            validate_positive_dimensions(100, 0)

    @pytest.mark.unit
    def test_validate_positive_dimensions_both_zero(self):
        """Test validation fails when both dimensions are zero."""
        with pytest.raises(
            ValueError, match="width and height must be positive integers"
        ):
            validate_positive_dimensions(0, 0)

    @pytest.mark.unit
    def test_validate_positive_dimensions_negative_width(self):
        """Test validation fails when width is negative."""
        with pytest.raises(
            ValueError, match="width and height must be positive integers"
        ):
            validate_positive_dimensions(-100, 200)

    @pytest.mark.unit
    def test_validate_positive_dimensions_negative_height(self):
        """Test validation fails when height is negative."""
        with pytest.raises(
            ValueError, match="width and height must be positive integers"
        ):
            validate_positive_dimensions(100, -200)

    @pytest.mark.unit
    def test_validate_positive_dimensions_both_negative(self):
        """Test validation fails when both dimensions are negative."""
        with pytest.raises(
            ValueError, match="width and height must be positive integers"
        ):
            validate_positive_dimensions(-100, -200)

    @pytest.mark.unit
    def test_validate_positive_dimensions_non_integer_width(self):
        """Test validation fails when width is not an integer."""
        with pytest.raises(
            ValueError, match="width and height must be positive integers"
        ):
            validate_positive_dimensions("100", 200)
        with pytest.raises(
            ValueError, match="width and height must be positive integers"
        ):
            validate_positive_dimensions(100.5, 200)
        with pytest.raises(
            ValueError, match="width and height must be positive integers"
        ):
            validate_positive_dimensions(None, 200)

    @pytest.mark.unit
    def test_validate_positive_dimensions_non_integer_height(self):
        """Test validation fails when height is not an integer."""
        with pytest.raises(
            ValueError, match="width and height must be positive integers"
        ):
            validate_positive_dimensions(100, "200")
        with pytest.raises(
            ValueError, match="width and height must be positive integers"
        ):
            validate_positive_dimensions(100, 200.5)
        with pytest.raises(
            ValueError, match="width and height must be positive integers"
        ):
            validate_positive_dimensions(100, None)

    @pytest.mark.unit
    def test_validate_positive_dimensions_both_non_integer(self):
        """Test validation fails when both dimensions are not integers."""
        with pytest.raises(
            ValueError, match="width and height must be positive integers"
        ):
            validate_positive_dimensions("100", "200")
        with pytest.raises(
            ValueError, match="width and height must be positive integers"
        ):
            validate_positive_dimensions(100.5, 200.5)


class TestValidateConfidenceRange:
    """Test cases for validate_confidence_range utility function."""

    @pytest.mark.unit
    def test_validate_confidence_range_valid_float(self):
        """Test validation passes and returns valid float confidence values."""
        assert validate_confidence_range("confidence", 0.5) == 0.5
        assert validate_confidence_range("confidence", 0.1) == 0.1
        assert validate_confidence_range("confidence", 0.99) == 0.99
        assert validate_confidence_range("confidence", 1.0) == 1.0
        assert validate_confidence_range("confidence", 0.0001) == 0.0001

    @pytest.mark.unit
    def test_validate_confidence_range_int_conversion(self):
        """Test validation converts integers to floats."""
        # Integer input should be converted to float
        result = validate_confidence_range("confidence", 1)
        assert result == 1.0
        assert isinstance(result, float)

    @pytest.mark.unit
    def test_validate_confidence_range_zero(self):
        """Test validation fails for zero confidence."""
        with pytest.raises(ValueError, match="confidence must be between 0 and 1"):
            validate_confidence_range("confidence", 0.0)
        with pytest.raises(ValueError, match="confidence must be between 0 and 1"):
            validate_confidence_range("confidence", 0)

    @pytest.mark.unit
    def test_validate_confidence_range_negative(self):
        """Test validation fails for negative confidence values."""
        with pytest.raises(ValueError, match="confidence must be between 0 and 1"):
            validate_confidence_range("confidence", -0.1)
        with pytest.raises(ValueError, match="confidence must be between 0 and 1"):
            validate_confidence_range("confidence", -1.0)
        with pytest.raises(ValueError, match="confidence must be between 0 and 1"):
            validate_confidence_range("confidence", -5)

    @pytest.mark.unit
    def test_validate_confidence_range_greater_than_one(self):
        """Test validation fails for confidence values greater than 1."""
        with pytest.raises(ValueError, match="confidence must be between 0 and 1"):
            validate_confidence_range("confidence", 1.1)
        with pytest.raises(ValueError, match="confidence must be between 0 and 1"):
            validate_confidence_range("confidence", 2.0)
        with pytest.raises(ValueError, match="confidence must be between 0 and 1"):
            validate_confidence_range("confidence", 5)

    @pytest.mark.unit
    def test_validate_confidence_range_invalid_type(self):
        """Test validation fails for non-numeric types."""
        with pytest.raises(ValueError, match="confidence must be a float"):
            validate_confidence_range("confidence", "0.5")
        with pytest.raises(ValueError, match="confidence must be a float"):
            validate_confidence_range("confidence", None)
        with pytest.raises(ValueError, match="confidence must be a float"):
            validate_confidence_range("confidence", [0.5])
        with pytest.raises(ValueError, match="confidence must be a float"):
            validate_confidence_range("confidence", {"value": 0.5})

    @pytest.mark.unit
    def test_validate_confidence_range_custom_field_name(self):
        """Test validation uses custom field name in error messages."""
        with pytest.raises(ValueError, match="detection_confidence must be a float"):
            validate_confidence_range("detection_confidence", "invalid")
        with pytest.raises(ValueError, match="ocr_confidence must be between 0 and 1"):
            validate_confidence_range("ocr_confidence", 1.5)

    @pytest.mark.unit
    def test_validate_confidence_range_edge_cases(self):
        """Test validation with edge case values."""
        # Very small positive number should pass
        result = validate_confidence_range("confidence", 1e-10)
        assert result == 1e-10

        # Exactly 1.0 should pass
        result = validate_confidence_range("confidence", 1.0)
        assert result == 1.0

        # Very close to 1 but not quite should pass
        result = validate_confidence_range("confidence", 0.9999999)
        assert result == 0.9999999


class TestValidationUtilitiesIntegration:
    """Integration tests for validation utilities with real entity usage patterns."""

    @pytest.mark.unit
    def test_validation_utilities_match_original_behavior(self):
        """Test that utilities produce same validation behavior as original hardcoded validation."""
        # Test patterns found in receipt entities

        # Receipt ID validation (positive int)
        validate_positive_int("receipt_id", 123)
        with pytest.raises(ValueError):
            validate_positive_int("receipt_id", 0)

        # Line ID validation (non-negative int)
        validate_non_negative_int("line_id", 0)
        validate_non_negative_int("line_id", 5)
        with pytest.raises(ValueError):
            validate_non_negative_int("line_id", -1)

        # Dimensions validation
        validate_positive_dimensions(1920, 1080)
        with pytest.raises(ValueError):
            validate_positive_dimensions(0, 1080)

        # Confidence validation with conversion
        result = validate_confidence_range("confidence", 0.95)
        assert result == 0.95
        assert isinstance(result, float)

    @pytest.mark.unit
    def test_validation_utilities_eliminate_duplication(self):
        """Test that utilities can replace multiple lines of duplicate validation code."""
        # Original pattern from letter.py (9 lines):
        # if not isinstance(self.line_id, int):
        #     raise ValueError("line_id must be an integer")
        # if self.line_id <= 0:
        #     raise ValueError("line_id must be positive")
        # (repeated for word_id, letter_id)

        # New pattern (3 lines):
        validate_non_negative_int("line_id", 5)
        validate_non_negative_int("word_id", 3)
        validate_non_negative_int("letter_id", 1)

        # Original confidence pattern (5 lines):
        # if isinstance(self.confidence, int):
        #     self.confidence = float(self.confidence)
        # if not isinstance(self.confidence, float):
        #     raise ValueError("confidence must be a float")
        # if self.confidence <= 0.0 or self.confidence > 1.0:
        #     raise ValueError("confidence must be between 0 and 1")

        # New pattern (1 line):
        confidence = validate_confidence_range("confidence", 0.85)
        assert confidence == 0.85

        # Dimensions pattern reduces from 6 lines to 1 line
        validate_positive_dimensions(800, 600)

    @pytest.mark.unit
    def test_validation_utilities_error_message_consistency(self):
        """Test that utilities provide consistent error messages across entities."""
        # All positive int validations should have consistent messages
        patterns = [
            ("receipt_id", ValueError, "receipt_id must be an integer"),
            ("line_id", ValueError, "line_id must be an integer"),
            ("word_id", ValueError, "word_id must be an integer"),
        ]

        for field_name, exception_type, expected_message in patterns:
            with pytest.raises(exception_type, match=expected_message):
                validate_positive_int(field_name, "invalid")

        # All confidence validations should have consistent messages
        confidence_patterns = [
            ("confidence", ValueError, "confidence must be between 0 and 1"),
            (
                "detection_confidence",
                ValueError,
                "detection_confidence must be between 0 and 1",
            ),
            (
                "ocr_confidence",
                ValueError,
                "ocr_confidence must be between 0 and 1",
            ),
        ]

        for (
            field_name,
            exception_type,
            expected_message,
        ) in confidence_patterns:
            with pytest.raises(exception_type, match=expected_message):
                validate_confidence_range(field_name, 1.5)
