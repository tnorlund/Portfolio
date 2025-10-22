"""
Unit tests for stream processor dataclasses.

Tests the dataclass structures used in the stream processor.
"""

import pytest
from datetime import datetime

from receipt_dynamo.entities.receipt_metadata import ReceiptMetadata
from receipt_dynamo.constants import ValidationMethod

from ...lambdas.stream_processor import (
    LambdaResponse,
    FieldChange,
    ParsedStreamRecord,
)


def create_test_receipt_metadata(
    merchant_name: str = "Test Merchant",
    canonical_merchant_name: str = "",
    **kwargs,
) -> ReceiptMetadata:
    """Create a test ReceiptMetadata entity with sensible defaults."""
    defaults = {
        "image_id": "550e8400-e29b-41d4-a716-446655440000",
        "receipt_id": 1,
        "place_id": "place123",
        "merchant_name": merchant_name,
        "canonical_merchant_name": canonical_merchant_name,
        "matched_fields": ["name"],
        "validated_by": ValidationMethod.PHONE_LOOKUP,
        "timestamp": datetime.fromisoformat("2024-01-01T00:00:00"),
    }
    defaults.update(kwargs)
    return ReceiptMetadata(**defaults)


class TestLambdaResponse:
    """Test LambdaResponse dataclass."""

    def test_lambda_response_to_dict(self):
        """Test LambdaResponse to_dict conversion."""
        response = LambdaResponse(
            status_code=200, processed_records=5, queued_messages=3
        )

        result = response.to_dict()

        assert result == {
            "statusCode": 200,
            "processed_records": 5,
            "queued_messages": 3,
        }

    def test_lambda_response_creation(self):
        """Test LambdaResponse creation with different values."""
        response = LambdaResponse(
            status_code=500, processed_records=0, queued_messages=0
        )

        assert response.status_code == 500
        assert response.processed_records == 0
        assert response.queued_messages == 0

    def test_lambda_response_frozen(self):
        """Test that LambdaResponse is immutable."""
        response = LambdaResponse(
            status_code=200, processed_records=1, queued_messages=1
        )

        # Should raise error when trying to modify
        with pytest.raises(Exception):  # FrozenInstanceError or AttributeError
            response.status_code = 404


class TestFieldChange:
    """Test FieldChange dataclass."""

    def test_field_change_creation(self):
        """Test FieldChange dataclass creation."""
        change = FieldChange(old="old_value", new="new_value")

        assert change.old == "old_value"
        assert change.new == "new_value"

    def test_field_change_with_none(self):
        """Test FieldChange with None values."""
        # Old value None (INSERT)
        change1 = FieldChange(old=None, new="new_value")
        assert change1.old is None
        assert change1.new == "new_value"

        # New value None (REMOVE)
        change2 = FieldChange(old="old_value", new=None)
        assert change2.old == "old_value"
        assert change2.new is None

    def test_field_change_frozen(self):
        """Test that FieldChange is immutable."""
        change = FieldChange(old="A", new="B")

        with pytest.raises(Exception):  # FrozenInstanceError or AttributeError
            change.old = "C"


class TestParsedStreamRecord:
    """Test ParsedStreamRecord dataclass."""

    def test_parsed_stream_record_creation(self):
        """Test ParsedStreamRecord dataclass creation."""
        old_entity = create_test_receipt_metadata(merchant_name="Old Merchant")

        entity_key = old_entity.key
        pk = entity_key["PK"]["S"]
        sk = entity_key["SK"]["S"]

        parsed = ParsedStreamRecord(
            entity_type="RECEIPT_METADATA",
            old_entity=old_entity,
            new_entity=None,
            pk=pk,
            sk=sk,
        )

        assert parsed.entity_type == "RECEIPT_METADATA"
        assert parsed.old_entity == old_entity
        assert parsed.new_entity is None
        assert parsed.pk == pk
        assert parsed.sk == sk

    def test_parsed_stream_record_with_both_entities(self):
        """Test ParsedStreamRecord with both old and new entities."""
        old_entity = create_test_receipt_metadata(merchant_name="Old Merchant")
        new_entity = create_test_receipt_metadata(merchant_name="New Merchant")

        entity_key = old_entity.key
        pk = entity_key["PK"]["S"]
        sk = entity_key["SK"]["S"]

        parsed = ParsedStreamRecord(
            entity_type="RECEIPT_METADATA",
            old_entity=old_entity,
            new_entity=new_entity,
            pk=pk,
            sk=sk,
        )

        assert parsed.old_entity == old_entity
        assert parsed.new_entity == new_entity
        assert parsed.entity_type == "RECEIPT_METADATA"

    def test_parsed_stream_record_frozen(self):
        """Test that ParsedStreamRecord is immutable."""
        old_entity = create_test_receipt_metadata(merchant_name="Test")
        entity_key = old_entity.key

        parsed = ParsedStreamRecord(
            entity_type="RECEIPT_METADATA",
            old_entity=old_entity,
            new_entity=None,
            pk=entity_key["PK"]["S"],
            sk=entity_key["SK"]["S"],
        )

        with pytest.raises(Exception):  # FrozenInstanceError or AttributeError
            parsed.entity_type = "DIFFERENT_TYPE"


if __name__ == "__main__":
    pytest.main([__file__])

