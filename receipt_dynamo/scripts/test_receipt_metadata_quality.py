"""
Test cases for ReceiptMetadata field quality validation.
"""

from datetime import datetime, timezone

import pytest

from receipt_dynamo.constants import MerchantValidationStatus, ValidationMethod
from receipt_dynamo.entities.receipt_metadata import ReceiptMetadata


class TestReceiptMetadataFieldQuality:
    """Test cases for the field quality validation in ReceiptMetadata."""

    def test_high_quality_fields_all_valid(self):
        """Test when all matched fields are high quality."""
        metadata = ReceiptMetadata(
            image_id="550e8400-e29b-41d4-a716-446655440000",
            receipt_id=1,
            place_id="test_place",
            merchant_name="Starbucks Coffee",
            address="123 Main Street Suite 100",
            phone_number="(555) 123-4567",
            merchant_category="Coffee Shop",
            matched_fields=["name", "address", "phone"],
            timestamp=datetime.now(timezone.utc),
            validated_by=ValidationMethod.PHONE_LOOKUP,
        )

        # Should be MATCHED with 3 high-quality fields
        assert metadata.validation_status == MerchantValidationStatus.MATCHED.value
        assert len(metadata._get_high_quality_matched_fields()) == 3

    def test_low_quality_name_field(self):
        """Test when name field is too short to be reliable."""
        metadata = ReceiptMetadata(
            image_id="550e8400-e29b-41d4-a716-446655440001",
            receipt_id=2,
            place_id="test_place",
            merchant_name="AB",  # Too short
            address="123 Main Street",
            phone_number="5551234567",
            merchant_category="Store",
            matched_fields=["name", "address", "phone"],
            timestamp=datetime.now(timezone.utc),
            validated_by=ValidationMethod.ADDRESS_LOOKUP,
        )

        # Should be MATCHED with only 2 high-quality fields (address, phone)
        assert metadata.validation_status == MerchantValidationStatus.MATCHED.value
        high_quality = metadata._get_high_quality_matched_fields()
        assert len(high_quality) == 2
        assert "name" not in high_quality
        assert "address" in high_quality
        assert "phone" in high_quality

    def test_low_quality_phone_field(self):
        """Test when phone field has insufficient digits."""
        metadata = ReceiptMetadata(
            image_id="550e8400-e29b-41d4-a716-446655440002",
            receipt_id=3,
            place_id="test_place",
            merchant_name="Target Store",
            address="456 Oak Avenue",
            phone_number="555-CALL",  # Not enough digits
            merchant_category="Retail",
            matched_fields=["name", "phone"],
            timestamp=datetime.now(timezone.utc),
            validated_by=ValidationMethod.TEXT_SEARCH,
        )

        # Should be UNSURE with only 1 high-quality field (name)
        assert metadata.validation_status == MerchantValidationStatus.UNSURE.value
        high_quality = metadata._get_high_quality_matched_fields()
        assert len(high_quality) == 1
        assert "name" in high_quality
        assert "phone" not in high_quality

    def test_low_quality_address_field(self):
        """Test when address field lacks meaningful components."""
        metadata = ReceiptMetadata(
            image_id="550e8400-e29b-41d4-a716-446655440003",
            receipt_id=4,
            place_id="test_place",
            merchant_name="Walmart Supercenter",
            address="123",  # Too minimal
            phone_number="",
            merchant_category="Retail",
            matched_fields=["name", "address"],
            timestamp=datetime.now(timezone.utc),
            validated_by=ValidationMethod.NEARBY_LOOKUP,
        )

        # Should be UNSURE with only 1 high-quality field (name)
        assert metadata.validation_status == MerchantValidationStatus.UNSURE.value
        high_quality = metadata._get_high_quality_matched_fields()
        assert len(high_quality) == 1
        assert "name" in high_quality
        assert "address" not in high_quality

    def test_empty_fields_no_match(self):
        """Test when fields are empty or missing."""
        metadata = ReceiptMetadata(
            image_id="550e8400-e29b-41d4-a716-446655440004",
            receipt_id=5,
            place_id="test_place",
            merchant_name="",  # Empty
            address="",  # Empty
            phone_number="",  # Empty
            merchant_category="Unknown",
            matched_fields=["name", "address", "phone"],
            timestamp=datetime.now(timezone.utc),
            validated_by=ValidationMethod.INFERENCE,
        )

        # Should be NO_MATCH with 0 high-quality fields
        assert metadata.validation_status == MerchantValidationStatus.NO_MATCH.value
        assert len(metadata._get_high_quality_matched_fields()) == 0

    def test_mixed_quality_fields(self):
        """Test with a mix of high and low quality fields."""
        metadata = ReceiptMetadata(
            image_id="550e8400-e29b-41d4-a716-446655440005",
            receipt_id=6,
            place_id="test_place",
            merchant_name="McDonald's Restaurant",  # Good
            address="1 A",  # Too short
            phone_number="(800) 244-6227",  # Good
            merchant_category="Fast Food",
            matched_fields=["name", "address", "phone"],
            timestamp=datetime.now(timezone.utc),
            validated_by=ValidationMethod.PHONE_LOOKUP,
        )

        # Should be MATCHED with 2 high-quality fields (name, phone)
        assert metadata.validation_status == MerchantValidationStatus.MATCHED.value
        high_quality = metadata._get_high_quality_matched_fields()
        assert len(high_quality) == 2
        assert "name" in high_quality
        assert "phone" in high_quality
        assert "address" not in high_quality

    def test_international_phone_numbers(self):
        """Test handling of international phone numbers."""
        metadata = ReceiptMetadata(
            image_id="550e8400-e29b-41d4-a716-446655440006",
            receipt_id=7,
            place_id="test_place",
            merchant_name="International Store",
            address="London Street",
            phone_number="+44 20 7946 0958",  # UK number
            merchant_category="Retail",
            matched_fields=["phone"],
            timestamp=datetime.now(timezone.utc),
            validated_by=ValidationMethod.PHONE_LOOKUP,
        )

        # Should be UNSURE with 1 high-quality field (has 11 digits)
        assert metadata.validation_status == MerchantValidationStatus.UNSURE.value
        high_quality = metadata._get_high_quality_matched_fields()
        assert len(high_quality) == 1
        assert "phone" in high_quality

    def test_whitespace_only_fields(self):
        """Test fields containing only whitespace."""
        metadata = ReceiptMetadata(
            image_id="550e8400-e29b-41d4-a716-446655440007",
            receipt_id=8,
            place_id="test_place",
            merchant_name="   ",  # Only spaces
            address="\t\n",  # Only whitespace
            phone_number="   ",  # Only spaces
            merchant_category="Unknown",
            matched_fields=["name", "address"],
            timestamp=datetime.now(timezone.utc),
            validated_by=ValidationMethod.INFERENCE,
        )

        # Should be NO_MATCH with 0 high-quality fields
        assert metadata.validation_status == MerchantValidationStatus.NO_MATCH.value
        assert len(metadata._get_high_quality_matched_fields()) == 0

    def test_future_field_types(self):
        """Test that unknown field types are preserved for future
        compatibility."""
        metadata = ReceiptMetadata(
            image_id="550e8400-e29b-41d4-a716-446655440008",
            receipt_id=9,
            place_id="test_place",
            merchant_name="Future Store",
            address="123 Future Ave",
            phone_number="5551234567",
            merchant_category="Tech",
            matched_fields=[
                "name",
                "email",
                "website",
            ],  # email and website are future fields
            timestamp=datetime.now(timezone.utc),
            validated_by=ValidationMethod.TEXT_SEARCH,
        )

        # Should be MATCHED - unknown fields are kept as-is
        assert metadata.validation_status == MerchantValidationStatus.MATCHED.value
        high_quality = metadata._get_high_quality_matched_fields()
        assert len(high_quality) == 3
        assert "email" in high_quality  # Future fields preserved
        assert "website" in high_quality  # Future fields preserved


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
