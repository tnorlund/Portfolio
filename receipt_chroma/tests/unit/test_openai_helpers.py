"""Unit tests for OpenAI helper functions."""

import pytest

from receipt_chroma.embedding.openai.helpers import (
    get_unique_receipt_and_image_ids,
)


@pytest.mark.unit
class TestGetUniqueReceiptAndImageIds:
    """Test getting unique receipt and image IDs."""

    def test_basic_extraction(self):
        """Test basic extraction of receipt and image IDs."""
        results = [
            {"custom_id": "WORD#img1#line1#123"},
            {"custom_id": "WORD#img2#line2#456"},
        ]
        unique_ids = get_unique_receipt_and_image_ids(results)
        assert (123, "img1") in unique_ids
        assert (456, "img2") in unique_ids
        assert len(unique_ids) == 2

    def test_duplicate_removal(self):
        """Test that duplicates are removed."""
        results = [
            {"custom_id": "WORD#img1#line1#123"},
            {"custom_id": "WORD#img1#line2#123"},
            {"custom_id": "WORD#img2#line1#456"},
        ]
        unique_ids = get_unique_receipt_and_image_ids(results)
        assert len(unique_ids) == 2
        assert (123, "img1") in unique_ids
        assert (456, "img2") in unique_ids

    def test_empty_results(self):
        """Test with empty results."""
        unique_ids = get_unique_receipt_and_image_ids([])
        assert unique_ids == []

    def test_custom_id_format(self):
        """Test parsing different custom_id formats."""
        results = [
            {"custom_id": "LINE#img1#line1#789"},
            {"custom_id": "WORD#img2#line2#101"},
        ]
        unique_ids = get_unique_receipt_and_image_ids(results)
        assert (789, "img1") in unique_ids
        assert (101, "img2") in unique_ids
