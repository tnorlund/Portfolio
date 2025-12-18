"""Pytest fixtures for receipt_agent tests."""

from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def mock_dynamo_client() -> MagicMock:
    """Create a mock DynamoDB client."""
    client = MagicMock()

    # Mock metadata
    mock_metadata = MagicMock()
    mock_metadata.image_id = "test-image-id"
    mock_metadata.receipt_id = 1
    mock_metadata.merchant_name = "Test Merchant"
    mock_metadata.place_id = "ChIJtest123"
    mock_metadata.address = "123 Test St"
    mock_metadata.phone_number = "555-123-4567"
    mock_metadata.merchant_category = "Restaurant"
    mock_metadata.matched_fields = ["name", "phone"]
    mock_metadata.validated_by = "google_places"
    mock_metadata.validation_status = "MATCHED"
    mock_metadata.reasoning = "Test reasoning"
    mock_metadata.canonical_merchant_name = "Test Merchant"
    mock_metadata.canonical_place_id = "ChIJtest123"
    mock_metadata.canonical_address = "123 Test St"
    mock_metadata.canonical_phone_number = "555-123-4567"

    client.get_receipt_metadata.return_value = mock_metadata

    # Mock receipt details
    mock_receipt = MagicMock()
    mock_line = MagicMock()
    mock_line.line_id = 1
    mock_line.text = "Test Merchant"

    mock_word = MagicMock()
    mock_word.word_id = 1
    mock_word.text = "Test"
    mock_word.extracted_data = {"type": "merchant_name", "value": "Test Merchant"}

    client.get_receipt_details.return_value = (
        MagicMock(),  # image
        mock_receipt,  # receipt
        [mock_word],  # words
        [mock_line],  # lines
        None,  # ocr_results
        [],  # labels
    )

    # Mock get_receipt_metadatas_by_merchant
    client.get_receipt_metadatas_by_merchant.return_value = ([mock_metadata], None)

    return client


@pytest.fixture
def mock_chroma_client() -> MagicMock:
    """Create a mock ChromaDB client."""
    client = MagicMock()

    # Mock query results
    client.query.return_value = {
        "ids": [["doc1", "doc2"]],
        "documents": [["Test line 1", "Test line 2"]],
        "metadatas": [
            [
                {
                    "image_id": "other-image",
                    "receipt_id": 2,
                    "line_id": 1,
                    "merchant_name": "Test Merchant",
                    "normalized_phone_10": "5551234567",
                    "normalized_full_address": "123 Test St",
                    "place_id": "ChIJtest123",
                },
                {
                    "image_id": "other-image-2",
                    "receipt_id": 1,
                    "line_id": 2,
                    "merchant_name": "Test Merchant",
                    "normalized_phone_10": "5551234567",
                    "normalized_full_address": "123 Test St",
                    "place_id": "ChIJtest123",
                },
            ]
        ],
        "distances": [[0.2, 0.3]],  # Low distance = high similarity
    }

    # Mock get results
    client.get.return_value = {
        "ids": ["doc1"],
        "metadatas": [
            {
                "image_id": "other-image",
                "receipt_id": 2,
                "merchant_name": "Test Merchant",
            }
        ],
    }

    return client


@pytest.fixture
def mock_embed_fn():
    """Create a mock embedding function."""

    def embed_fn(texts: List[str]) -> List[List[float]]:
        # Return fake 1536-dim embeddings
        return [[0.1] * 1536 for _ in texts]

    return embed_fn


@pytest.fixture
def mock_places_api() -> MagicMock:
    """Create a mock Google Places API client."""
    api = MagicMock()

    api.get_place_details.return_value = {
        "place_id": "ChIJtest123",
        "name": "Test Merchant",
        "formatted_address": "123 Test St, City, ST 12345",
        "formatted_phone_number": "(555) 123-4567",
        "types": ["restaurant"],
        "business_status": "OPERATIONAL",
        "rating": 4.5,
    }

    api.search_by_phone.return_value = api.get_place_details.return_value
    api.search_by_text.return_value = api.get_place_details.return_value

    return api


@pytest.fixture
def sample_validation_state() -> Dict[str, Any]:
    """Create sample validation state for testing."""
    return {
        "image_id": "test-image-id",
        "receipt_id": 1,
        "current_merchant_name": "Test Merchant",
        "current_place_id": "ChIJtest123",
        "current_address": "123 Test St",
        "current_phone": "555-123-4567",
        "current_validation_status": "MATCHED",
    }
