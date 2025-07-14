"""
API stubbing fixtures for local development and testing.

This module provides pytest fixtures that stub external API calls,
allowing tests to run without incurring costs or requiring network access.
"""

import os
import json
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime
from typing import Dict, List, Optional

from receipt_label.models.label import LabelAnalysis, WordLabel
from receipt_label.models.validation import ValidationAnalysis
from receipt_label.models.structure import StructureAnalysis
from receipt_label.models.line_item import LineItemAnalysis


@pytest.fixture
def use_stub_apis():
    """Check if API stubbing is enabled via environment variable."""
    return os.environ.get("USE_STUB_APIS", "false").lower() == "true"


@pytest.fixture
def stub_label_analysis():
    """Provide a canned LabelAnalysis response."""
    return LabelAnalysis(
        labels=[
            WordLabel(word_id=1, label="MERCHANT_NAME", confidence=0.95),
            WordLabel(word_id=5, label="DATE", confidence=0.90),
            WordLabel(word_id=10, label="SUBTOTAL", confidence=0.92),
            WordLabel(word_id=11, label="TAX", confidence=0.91),
            WordLabel(word_id=12, label="TOTAL", confidence=0.95)
        ],
        receipt_id="1",
        image_id="550e8400-e29b-41d4-a716-446655440001",
        sections=[],
        total_labeled_words=5,
        requires_review=False,
        review_reasons=[],
        analysis_reasoning="Stubbed analysis for testing",
        metadata={
            "confidence": 0.92,
            "processing_time_ms": 150,
            "model": "gpt-4-stub",
            "stub_response": True
        }
    )


@pytest.fixture
def stub_validation_analysis():
    """Provide a canned ValidationAnalysis response."""
    return ValidationAnalysis(
        version="1.0.0",
        timestamp=datetime.utcnow().isoformat() + "Z",
        is_valid=True,
        issues=[],
        warnings=[
            {
                "type": "MINOR_DISCREPANCY",
                "message": "Tax calculation differs by $0.01",
                "severity": "warning"
            }
        ],
        metadata={
            "validation_time_ms": 75,
            "stub_response": True
        }
    )


@pytest.fixture
def stub_structure_analysis():
    """Provide a canned StructureAnalysis response."""
    return StructureAnalysis(
        version="1.0.0",
        timestamp=datetime.utcnow().isoformat() + "Z",
        sections=[
            {
                "type": "HEADER",
                "line_ids": [1, 2, 3],
                "confidence": 0.95
            },
            {
                "type": "LINE_ITEMS",
                "line_ids": [4, 5, 6, 7, 8],
                "confidence": 0.90
            },
            {
                "type": "TOTALS",
                "line_ids": [9, 10, 11, 12],
                "confidence": 0.93
            },
            {
                "type": "FOOTER",
                "line_ids": [13, 14, 15],
                "confidence": 0.88
            }
        ],
        line_relationships=[],
        metadata={
            "processing_time_ms": 100,
            "model": "gpt-4-stub",
            "stub_response": True
        }
    )


@pytest.fixture
def stub_line_item_analysis():
    """Provide a canned LineItemAnalysis response."""
    return LineItemAnalysis(
        version="1.0.0",
        timestamp=datetime.utcnow().isoformat() + "Z",
        line_items=[
            {
                "line_id": 4,
                "item_name": "Product A",
                "quantity": 2,
                "unit_price": 9.99,
                "total_price": 19.98,
                "confidence": 0.92
            },
            {
                "line_id": 5,
                "item_name": "Product B",
                "quantity": 1,
                "unit_price": 6.01,
                "total_price": 6.01,
                "confidence": 0.89
            }
        ],
        metadata={
            "processing_time_ms": 125,
            "model": "gpt-4-stub", 
            "stub_response": True
        }
    )


@pytest.fixture
def stub_openai_client(
    stub_label_analysis,
    stub_validation_analysis,
    stub_structure_analysis,
    stub_line_item_analysis
):
    """Create a stubbed OpenAI client that returns canned responses."""
    mock_client = MagicMock()
    
    # Stub the main API methods
    mock_client.generate_label_analysis.return_value = stub_label_analysis
    mock_client.validate_analysis.return_value = stub_validation_analysis
    mock_client.analyze_structure.return_value = stub_structure_analysis
    mock_client.analyze_line_items.return_value = stub_line_item_analysis
    
    # Stub cost tracking
    mock_client.get_usage_stats.return_value = {
        "total_tokens": 1000,
        "prompt_tokens": 800,
        "completion_tokens": 200,
        "estimated_cost": 0.03
    }
    
    return mock_client


@pytest.fixture
def stub_pinecone_results():
    """Provide canned Pinecone query results."""
    return {
        "matches": [
            {
                "id": "receipt_123_word_5",
                "score": 0.95,
                "metadata": {
                    "merchant_name": "WALMART",
                    "label": "PRODUCT_NAME",
                    "text": "BANANAS",
                    "confidence": 0.90
                }
            },
            {
                "id": "receipt_456_word_10",
                "score": 0.92,
                "metadata": {
                    "merchant_name": "WALMART", 
                    "label": "PRODUCT_CODE",
                    "text": "4011",
                    "confidence": 0.88
                }
            }
        ],
        "namespace": "test"
    }


@pytest.fixture
def stub_pinecone_client(stub_pinecone_results):
    """Create a stubbed Pinecone client."""
    mock_index = MagicMock()
    mock_index.query.return_value = stub_pinecone_results
    mock_index.upsert.return_value = {"upserted_count": 1}
    
    mock_client = MagicMock()
    mock_client.Index.return_value = mock_index
    
    return mock_client


@pytest.fixture
def stub_all_apis(use_stub_apis, stub_openai_client, stub_pinecone_client):
    """
    Conditionally stub all external APIs based on USE_STUB_APIS environment variable.
    
    Usage:
        def test_something(stub_all_apis):
            # If USE_STUB_APIS=true, all external APIs are stubbed
            # Otherwise, real APIs are used
    """
    if not use_stub_apis:
        yield None
        return
    
    with patch('receipt_label.services.openai_client.OpenAIClient') as mock_openai_class:
        with patch('pinecone.Index') as mock_pinecone_class:
            # Configure the mocks
            mock_openai_class.return_value = stub_openai_client
            mock_pinecone_class.return_value = stub_pinecone_client.Index()
            
            yield {
                'openai': stub_openai_client,
                'pinecone': stub_pinecone_client
            }


@pytest.fixture
def stub_places_api():
    """Stub Google Places API responses."""
    def mock_search_nearby(query: str, location: Optional[Dict] = None):
        """Mock nearby search results."""
        return [
            {
                "place_id": "ChIJtest123",
                "name": "Test Store #123",
                "vicinity": "123 Test Street, Test City",
                "geometry": {
                    "location": {
                        "lat": 40.7128,
                        "lng": -74.0060
                    }
                },
                "types": ["store", "point_of_interest"]
            }
        ]
    
    def mock_get_details(place_id: str):
        """Mock place details."""
        return {
            "place_id": place_id,
            "name": "Test Store #123",
            "formatted_address": "123 Test Street, Test City, TS 12345",
            "formatted_phone_number": "(555) 123-4567",
            "website": "https://teststore.example.com",
            "opening_hours": {
                "weekday_text": [
                    "Monday: 9:00 AM – 9:00 PM",
                    "Tuesday: 9:00 AM – 9:00 PM"
                ]
            }
        }
    
    mock_client = MagicMock()
    mock_client.search_nearby = mock_search_nearby
    mock_client.get_place_details = mock_get_details
    
    return mock_client


@pytest.fixture
def enable_cost_free_testing(stub_all_apis, monkeypatch):
    """
    Enable complete cost-free testing by stubbing all external services.
    
    This fixture:
    1. Stubs all API calls (OpenAI, Pinecone, Places)
    2. Prevents any AWS service calls
    3. Disables real HTTP requests
    
    Usage:
        def test_expensive_operation(enable_cost_free_testing):
            # All external calls are stubbed
            # No costs will be incurred
    """
    if not stub_all_apis:
        yield
        return
    
    # Also prevent any AWS calls
    def mock_boto_client(service_name, *args, **kwargs):
        mock = MagicMock()
        mock.service_name = service_name
        return mock
    
    monkeypatch.setattr("boto3.client", mock_boto_client)
    
    # Prevent any real HTTP requests
    def mock_requests(*args, **kwargs):
        raise RuntimeError("Real HTTP request attempted in cost-free mode!")
    
    monkeypatch.setattr("requests.get", mock_requests)
    monkeypatch.setattr("requests.post", mock_requests)
    
    yield stub_all_apis