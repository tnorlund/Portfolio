"""Tests for agent tools."""

from receipt_agent.tools.dynamo import (
    get_receipt_context,
    get_receipt_place,
)
from receipt_agent.tools.places import compare_place_with_google


class TestGetReceiptPlace:
    """Tests for get_receipt_place tool."""

    def test_returns_error_without_client(self):
        result = get_receipt_place.func(
            image_id="test",
            receipt_id=1,
            _dynamo_client=None,
        )
        assert "error" in result

    def test_returns_place(self, mock_dynamo_client):
        result = get_receipt_place.func(
            image_id="test-image",
            receipt_id=1,
            _dynamo_client=mock_dynamo_client,
        )

        assert result["found"] is True
        assert result["merchant_name"] == "Test Merchant"
        assert result["place_id"] == "ChIJtest123"


class TestGetReceiptContext:
    """Tests for get_receipt_context tool."""

    def test_returns_context(self, mock_dynamo_client):
        result = get_receipt_context.func(
            image_id="test-image",
            receipt_id=1,
            _dynamo_client=mock_dynamo_client,
        )

        assert result["found"] is True
        assert "raw_lines" in result
        assert "extracted_data" in result


class TestComparePlaceWithGoogle:
    """Tests for compare_place_with_google tool."""

    def test_compares_matching_data(self):
        result = compare_place_with_google.func(
            current_name="Starbucks Coffee",
            current_address="123 Main St",
            current_phone="555-123-4567",
            places_name="Starbucks Coffee",
            places_address="123 Main Street",
            places_phone="(555) 123-4567",
        )

        assert "name_comparison" in result
        assert result["name_comparison"]["match"] is True
        assert result["match_count"] >= 2

    def test_detects_mismatches(self):
        result = compare_place_with_google.func(
            current_name="Starbucks",
            current_address="123 Main St",
            current_phone="555-123-4567",
            places_name="Dunkin Donuts",
            places_address="456 Other Ave",
            places_phone="555-999-8888",
        )

        assert result["name_comparison"]["match"] is False
        assert len(result["recommendations"]) > 0
