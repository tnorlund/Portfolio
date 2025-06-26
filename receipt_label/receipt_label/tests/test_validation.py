from datetime import datetime

import pytest
from receipt_label.models.receipt import ReceiptLine
from receipt_label.utils.validation import (
    validate_address,
    validate_amounts,
    validate_business_name,
    validate_datetime,
    validate_phone_number,
    validate_receipt_data,
    validate_receipt_format,
)


@pytest.mark.unit
class TestValidationUtils:
    def test_validate_business_name(self):
        """Test business name validation."""
        # Test exact match
        is_valid, message, confidence = validate_business_name(
            "Walmart", "Walmart"
        )
        assert is_valid
        assert confidence == 1.0

        # Test similar names
        is_valid, message, confidence = validate_business_name(
            "Walmart", "Walmart Supercenter"
        )
        assert is_valid
        assert confidence >= 0.8

        # Test different names
        is_valid, message, confidence = validate_business_name(
            "Walmart", "Target"
        )
        assert not is_valid
        assert confidence < 0.8

        # Test empty inputs
        is_valid, message, confidence = validate_business_name("", "")
        assert not is_valid
        assert confidence == 0.0

    def test_validate_phone_number(self):
        """Test phone number validation."""
        # Test exact match
        is_valid, message, confidence = validate_phone_number(
            "(555) 123-4567", "555-123-4567"
        )
        assert is_valid
        assert confidence == 1.0

        # Test different formats
        is_valid, message, confidence = validate_phone_number(
            "5551234567", "(555) 123-4567"
        )
        assert is_valid
        assert confidence == 1.0

        # Test different numbers
        is_valid, message, confidence = validate_phone_number(
            "(555) 123-4567", "(555) 123-4568"
        )
        assert not is_valid
        assert confidence == 0.0

        # Test empty inputs
        is_valid, message, confidence = validate_phone_number("", "")
        assert not is_valid
        assert confidence == 0.0

    def test_validate_address(self):
        """Test address validation."""
        # Test exact match
        is_valid, message, confidence = validate_address(
            "123 Main St, Boston, MA", "123 Main Street, Boston, MA"
        )
        assert is_valid
        assert confidence >= 0.8

        # Test similar addresses
        is_valid, message, confidence = validate_address(
            "123 Main St Suite 4, Boston, MA", "123 Main Street, Boston, MA"
        )
        assert is_valid
        assert confidence >= 0.8

        # Test different addresses
        is_valid, message, confidence = validate_address(
            "123 Main St, Boston, MA", "456 Oak Ave, Chicago, IL"
        )
        assert not is_valid
        assert confidence < 0.8

        # Test empty inputs
        is_valid, message, confidence = validate_address("", "")
        assert not is_valid
        assert confidence == 0.0

    def test_validate_datetime(self):
        """Test datetime validation."""
        # Test valid date only
        is_valid, message, confidence = validate_datetime("2024-03-15")
        assert is_valid
        assert confidence == 1.0

        # Test valid date and time
        is_valid, message, confidence = validate_datetime(
            "2024-03-15", "14:30"
        )
        assert is_valid
        assert confidence == 1.0

        # Test invalid date
        is_valid, message, confidence = validate_datetime("invalid")
        assert not is_valid
        assert confidence == 0.0

        # Test valid date with invalid time
        is_valid, message, confidence = validate_datetime(
            "2024-03-15", "25:00"
        )
        assert not is_valid
        assert confidence == 0.0

        # Test with max age
        old_date = (
            datetime.now().replace(year=datetime.now().year - 2)
        ).strftime("%Y-%m-%d")
        is_valid, message, confidence = validate_datetime(
            old_date, max_age_days=365
        )
        assert not is_valid
        assert confidence == 0.0

    def test_validate_amounts(self):
        """Test amount validation."""
        # Test exact match
        is_valid, message, confidence = validate_amounts(100.00, 10.00, 110.00)
        assert is_valid
        assert confidence == 1.0

        # Test with tolerance
        is_valid, message, confidence = validate_amounts(
            100.00, 10.00, 110.001, tolerance=0.01
        )
        assert is_valid
        assert confidence == 1.0

        # Test mismatch
        is_valid, message, confidence = validate_amounts(100.00, 10.00, 115.00)
        assert not is_valid
        assert confidence == 0.0

        # Test negative amounts
        is_valid, message, confidence = validate_amounts(
            -100.00, 10.00, -90.00
        )
        assert not is_valid
        assert confidence == 0.0

        # Test string inputs
        is_valid, message, confidence = validate_amounts(
            "100.00", "10.00", "110.00"
        )
        assert is_valid
        assert confidence == 1.0

    def test_validate_receipt_data(self):
        """Test receipt data validation."""
        # Test complete data
        complete_data = {
            "business_name": "Test Store",
            "address": "123 Main St",
            "date": "2024-03-15",
            "subtotal": "100.00",
            "tax": "10.00",
            "total": "110.00",
        }
        is_valid, missing, confidence = validate_receipt_data(complete_data)
        assert is_valid
        assert len(missing) == 0
        assert confidence == 1.0

        # Test missing fields
        incomplete_data = {
            "business_name": "Test Store",
            "address": "123 Main St",
        }
        is_valid, missing, confidence = validate_receipt_data(incomplete_data)
        assert not is_valid
        assert len(missing) > 0
        assert confidence < 1.0

        # Test custom required fields
        custom_fields = ["business_name", "total"]
        is_valid, missing, confidence = validate_receipt_data(
            {"business_name": "Test Store", "total": "110.00"},
            required_fields=custom_fields,
        )
        assert is_valid
        assert len(missing) == 0
        assert confidence == 1.0

    def test_validate_receipt_format(self):
        """Test receipt format validation."""
        # Create sample receipt data and lines
        receipt_data = {"sections": ["header", "items", "totals"]}
        receipt_lines = [
            ReceiptLine(
                line_id="1",
                text="Test Store",
                confidence=0.9,
                bounding_box={"x": 0, "y": 0, "width": 100, "height": 20},
                top_right={"x": 100, "y": 0},
                top_left={"x": 0, "y": 0},
                bottom_right={"x": 100, "y": 20},
                bottom_left={"x": 0, "y": 20},
                angle_degrees=0.0,
                angle_radians=0.0,
            )
        ]

        # Test with default rules
        is_valid, violations, confidence = validate_receipt_format(
            receipt_data, receipt_lines
        )
        assert is_valid
        assert len(violations) == 0
        assert confidence > 0.0

        # Test with custom rules
        custom_rules = {
            "max_line_length": 50,
            "required_sections": ["header", "items"],
        }
        is_valid, violations, confidence = validate_receipt_format(
            receipt_data, receipt_lines, format_rules=custom_rules
        )
        assert is_valid
        assert len(violations) == 0
        assert confidence > 0.0

        # Test with violations
        invalid_data = {"sections": ["unknown"]}
        is_valid, violations, confidence = validate_receipt_format(
            invalid_data, receipt_lines
        )
        assert not is_valid
        assert len(violations) > 0
        assert confidence < 1.0
