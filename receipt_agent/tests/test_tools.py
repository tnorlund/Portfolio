"""Tests for agent tools."""

import pytest
from unittest.mock import MagicMock

from receipt_agent.tools.chroma import (
    query_similar_lines,
    query_similar_words,
    search_by_merchant_name,
    search_by_place_id,
)
from receipt_agent.tools.dynamo import (
    get_receipt_context,
    get_receipt_metadata,
    get_receipts_by_merchant,
)
from receipt_agent.tools.places import (
    compare_metadata_with_places,
    verify_with_google_places,
)
from receipt_agent.tools.registry import ToolRegistry, create_tool_registry


class TestQuerySimilarLines:
    """Tests for query_similar_lines tool."""

    def test_returns_error_without_client(self):
        result = query_similar_lines.func(
            query_text="test query",
            _chroma_client=None,
            _embed_fn=None,
        )
        assert len(result) == 1
        assert "error" in result[0]

    def test_returns_error_without_embed_fn(self, mock_chroma_client):
        result = query_similar_lines.func(
            query_text="test query",
            _chroma_client=mock_chroma_client,
            _embed_fn=None,
        )
        assert len(result) == 1
        assert "error" in result[0]

    def test_returns_similar_lines(self, mock_chroma_client, mock_embed_fn):
        result = query_similar_lines.func(
            query_text="Test Merchant 123 Main St",
            n_results=10,
            min_similarity=0.5,
            _chroma_client=mock_chroma_client,
            _embed_fn=mock_embed_fn,
        )

        assert len(result) > 0
        assert "similarity_score" in result[0]
        assert result[0]["similarity_score"] > 0.5


class TestSearchByMerchantName:
    """Tests for search_by_merchant_name tool."""

    def test_returns_merchant_info(self, mock_chroma_client, mock_embed_fn):
        result = search_by_merchant_name.func(
            merchant_name="Test Merchant",
            _chroma_client=mock_chroma_client,
            _embed_fn=mock_embed_fn,
        )

        assert "merchant_name" in result
        assert "receipt_count" in result


class TestGetReceiptMetadata:
    """Tests for get_receipt_metadata tool."""

    def test_returns_error_without_client(self):
        result = get_receipt_metadata.func(
            image_id="test",
            receipt_id=1,
            _dynamo_client=None,
        )
        assert "error" in result

    def test_returns_metadata(self, mock_dynamo_client):
        result = get_receipt_metadata.func(
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


class TestCompareMetadataWithPlaces:
    """Tests for compare_metadata_with_places tool."""

    def test_compares_matching_data(self):
        result = compare_metadata_with_places.func(
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
        result = compare_metadata_with_places.func(
            current_name="Starbucks",
            current_address="123 Main St",
            current_phone="555-123-4567",
            places_name="Dunkin Donuts",
            places_address="456 Other Ave",
            places_phone="555-999-8888",
        )

        assert result["name_comparison"]["match"] is False
        assert len(result["recommendations"]) > 0


class TestToolRegistry:
    """Tests for ToolRegistry."""

    def test_create_registry(
        self,
        mock_chroma_client,
        mock_dynamo_client,
        mock_embed_fn,
        mock_places_api,
    ):
        registry = create_tool_registry(
            chroma_client=mock_chroma_client,
            dynamo_client=mock_dynamo_client,
            places_api=mock_places_api,
            embed_fn=mock_embed_fn,
        )

        assert isinstance(registry, ToolRegistry)

    def test_get_all_tools(
        self,
        mock_chroma_client,
        mock_dynamo_client,
        mock_embed_fn,
        mock_places_api,
    ):
        registry = create_tool_registry(
            chroma_client=mock_chroma_client,
            dynamo_client=mock_dynamo_client,
            places_api=mock_places_api,
            embed_fn=mock_embed_fn,
        )

        tools = registry.get_all_tools()

        # Should have ChromaDB + DynamoDB + Places tools
        assert len(tools) >= 7  # At least 7 tools

    def test_get_chroma_tools_only(self, mock_chroma_client, mock_embed_fn):
        registry = create_tool_registry(
            chroma_client=mock_chroma_client,
            embed_fn=mock_embed_fn,
        )

        chroma_tools = registry.get_chroma_tools()
        assert len(chroma_tools) == 4  # 4 ChromaDB tools

    def test_get_tool_descriptions(
        self,
        mock_chroma_client,
        mock_dynamo_client,
        mock_embed_fn,
    ):
        registry = create_tool_registry(
            chroma_client=mock_chroma_client,
            dynamo_client=mock_dynamo_client,
            embed_fn=mock_embed_fn,
        )

        descriptions = registry.get_tool_descriptions()

        assert "query_similar_lines" in descriptions
        assert "get_receipt_metadata" in descriptions

