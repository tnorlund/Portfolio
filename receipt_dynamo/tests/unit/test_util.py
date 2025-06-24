"""Unit tests for util module."""

import pytest

from receipt_dynamo.constants import BatchStatus, ValidationMethod, ValidationStatus
from receipt_dynamo.entities.util import normalize_enum


@pytest.mark.unit
class TestNormalizeEnum:
    """Test cases for normalize_enum function."""

    def test_normalize_enum_with_enum_instance(self):
        """Test with actual enum instance."""
        result = normalize_enum(ValidationStatus.PENDING, ValidationStatus)
        assert result == "PENDING"

    def test_normalize_enum_with_valid_string(self):
        """Test with valid string value."""
        result = normalize_enum("PENDING", ValidationStatus)
        assert result == "PENDING"

    def test_normalize_enum_with_invalid_string(self):
        """Test with invalid string raises ValueError."""
        with pytest.raises(
            ValueError, match="ValidationStatus must be one of"
        ):
            normalize_enum("INVALID_VALUE", ValidationStatus)

    def test_normalize_enum_with_invalid_type(self):
        """Test with invalid type raises ValueError."""
        with pytest.raises(
            ValueError, match="must be a str or ValidationStatus instance"
        ):
            normalize_enum(123, ValidationStatus)

    def test_normalize_enum_with_different_enum_types(self):
        """Test with different enum types to ensure generics work."""
        # Test with BatchStatus
        result = normalize_enum(BatchStatus.COMPLETED, BatchStatus)
        assert result == "COMPLETED"

        result = normalize_enum("FAILED", BatchStatus)
        assert result == "FAILED"

        # Test with ValidationMethod
        result = normalize_enum(
            ValidationMethod.PHONE_LOOKUP, ValidationMethod
        )
        assert result == "PHONE_LOOKUP"

        result = normalize_enum("TEXT_SEARCH", ValidationMethod)
        assert result == "TEXT_SEARCH"

    def test_normalize_enum_error_message_contains_valid_options(self):
        """Test that error message contains all valid enum options."""
        with pytest.raises(ValueError) as exc_info:
            normalize_enum("INVALID", ValidationStatus)

        error_message = str(exc_info.value)
        assert "ValidationStatus must be one of:" in error_message
        assert "NONE" in error_message
        assert "PENDING" in error_message
        assert "VALID" in error_message
        assert "INVALID" in error_message
        assert "NEEDS_REVIEW" in error_message
        assert "Got: INVALID" in error_message

    def test_normalize_enum_error_message_shows_received_value(self):
        """Test that error message shows the received invalid value."""
        with pytest.raises(ValueError) as exc_info:
            normalize_enum("WRONG_VALUE", BatchStatus)

        error_message = str(exc_info.value)
        assert "Got: WRONG_VALUE" in error_message

    def test_normalize_enum_with_none_value(self):
        """Test with None value raises appropriate error."""
        with pytest.raises(
            ValueError, match="must be a str or ValidationStatus instance"
        ):
            normalize_enum(None, ValidationStatus)

    def test_normalize_enum_with_wrong_enum_instance(self):
        """Test with wrong enum instance type raises appropriate error."""
        with pytest.raises(
            ValueError, match="must be a str or ValidationStatus instance"
        ):
            normalize_enum(BatchStatus.PENDING, ValidationStatus)

    def test_normalize_enum_preserves_case_sensitivity(self):
        """Test that string matching is case sensitive."""
        with pytest.raises(ValueError):
            normalize_enum(
                "pending", ValidationStatus
            )  # lowercase should fail

        with pytest.raises(ValueError):
            normalize_enum(
                "Pending", ValidationStatus
            )  # mixed case should fail

    def test_normalize_enum_with_all_validation_status_values(self):
        """Test all ValidationStatus enum values work correctly."""
        test_cases = [
            (ValidationStatus.NONE, "NONE"),
            (ValidationStatus.PENDING, "PENDING"),
            (ValidationStatus.VALID, "VALID"),
            (ValidationStatus.INVALID, "INVALID"),
            (ValidationStatus.NEEDS_REVIEW, "NEEDS_REVIEW"),
            ("NONE", "NONE"),
            ("PENDING", "PENDING"),
            ("VALID", "VALID"),
            ("INVALID", "INVALID"),
            ("NEEDS_REVIEW", "NEEDS_REVIEW"),
        ]

        for input_value, expected_output in test_cases:
            result = normalize_enum(input_value, ValidationStatus)
            assert result == expected_output

    def test_normalize_enum_with_all_batch_status_values(self):
        """Test all BatchStatus enum values work correctly."""
        test_cases = [
            (BatchStatus.PENDING, "PENDING"),
            (BatchStatus.COMPLETED, "COMPLETED"),
            (BatchStatus.FAILED, "FAILED"),
            ("PENDING", "PENDING"),
            ("COMPLETED", "COMPLETED"),
            ("FAILED", "FAILED"),
        ]

        for input_value, expected_output in test_cases:
            result = normalize_enum(input_value, BatchStatus)
            assert result == expected_output
