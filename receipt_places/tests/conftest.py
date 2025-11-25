"""
Pytest fixtures for receipt_places tests.

Uses moto to mock DynamoDB and responses to mock HTTP requests.
"""

import json
import os
from typing import Any, Generator

import boto3
import pytest
import responses
from moto import mock_aws

from receipt_places.cache import CacheManager
from receipt_places.client import PlacesClient
from receipt_places.config import PlacesConfig


# DynamoDB table schema that matches receipt_dynamo's PlacesCache
TABLE_SCHEMA = {
    "TableName": "test-receipts",
    "KeySchema": [
        {"AttributeName": "PK", "KeyType": "HASH"},
        {"AttributeName": "SK", "KeyType": "RANGE"},
    ],
    "AttributeDefinitions": [
        {"AttributeName": "PK", "AttributeType": "S"},
        {"AttributeName": "SK", "AttributeType": "S"},
        {"AttributeName": "GSI1PK", "AttributeType": "S"},
        {"AttributeName": "GSI1SK", "AttributeType": "S"},
    ],
    "GlobalSecondaryIndexes": [
        {
            "IndexName": "GSI1",
            "KeySchema": [
                {"AttributeName": "GSI1PK", "KeyType": "HASH"},
                {"AttributeName": "GSI1SK", "KeyType": "RANGE"},
            ],
            "Projection": {"ProjectionType": "ALL"},
            "ProvisionedThroughput": {
                "ReadCapacityUnits": 5,
                "WriteCapacityUnits": 5,
            },
        },
    ],
    "ProvisionedThroughput": {
        "ReadCapacityUnits": 5,
        "WriteCapacityUnits": 5,
    },
}


@pytest.fixture
def aws_credentials() -> None:
    """Set up mock AWS credentials for moto."""
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    os.environ["AWS_SECURITY_TOKEN"] = "testing"
    os.environ["AWS_SESSION_TOKEN"] = "testing"
    os.environ["AWS_DEFAULT_REGION"] = "us-west-2"


@pytest.fixture
def mock_dynamodb(aws_credentials: None) -> Generator[Any, None, None]:
    """Create a mock DynamoDB table."""
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-west-2")

        # Create the table
        client.create_table(**TABLE_SCHEMA)

        # Wait for table to be active
        waiter = client.get_waiter("table_exists")
        waiter.wait(TableName="test-receipts")

        yield client


@pytest.fixture
def test_config() -> PlacesConfig:
    """Create test configuration."""
    return PlacesConfig(
        api_key="test-api-key",
        table_name="test-receipts",
        aws_region="us-west-2",
        cache_ttl_days=30,
        cache_enabled=True,
        request_timeout=10,
    )


@pytest.fixture
def cache_manager(
    mock_dynamodb: Any,
    test_config: PlacesConfig,
) -> CacheManager:
    """Create a CacheManager with mocked DynamoDB."""
    return CacheManager(
        table_name="test-receipts",
        config=test_config,
        dynamodb_client=mock_dynamodb,
    )


@pytest.fixture
def places_client(
    cache_manager: CacheManager,
    test_config: PlacesConfig,
) -> PlacesClient:
    """Create a PlacesClient with mocked dependencies."""
    return PlacesClient(
        api_key="test-api-key",
        config=test_config,
        cache_manager=cache_manager,
    )


@pytest.fixture
def mock_places_api() -> Generator[responses.RequestsMock, None, None]:
    """Mock Google Places API responses."""
    with responses.RequestsMock() as rsps:
        yield rsps


# Sample response data for tests
SAMPLE_PLACE_DETAILS = {
    "place_id": "ChIJtest123",
    "name": "Test Business",
    "formatted_address": "123 Test St, City, ST 12345",
    "formatted_phone_number": "(555) 123-4567",
    "international_phone_number": "+1 555-123-4567",
    "types": ["restaurant", "food", "establishment"],
    "business_status": "OPERATIONAL",
    "rating": 4.5,
    "user_ratings_total": 100,
    "geometry": {
        "location": {"lat": 40.7128, "lng": -74.0060},
    },
}

SAMPLE_FIND_PLACE_RESPONSE = {
    "status": "OK",
    "candidates": [
        {
            "place_id": "ChIJtest123",
            "name": "Test Business",
            "formatted_address": "123 Test St, City, ST 12345",
            "types": ["restaurant"],
        }
    ],
}

SAMPLE_DETAILS_RESPONSE = {
    "status": "OK",
    "result": SAMPLE_PLACE_DETAILS,
}

SAMPLE_TEXT_SEARCH_RESPONSE = {
    "status": "OK",
    "results": [
        {
            "place_id": "ChIJtest123",
            "name": "Test Business",
            "formatted_address": "123 Test St, City, ST 12345",
        }
    ],
}


def add_places_api_mocks(rsps: responses.RequestsMock) -> None:
    """Add standard Places API mock responses."""
    base_url = "https://maps.googleapis.com/maps/api/place"

    # Find place from text (phone/address search)
    rsps.add(
        responses.GET,
        f"{base_url}/findplacefromtext/json",
        json=SAMPLE_FIND_PLACE_RESPONSE,
        status=200,
    )

    # Place details
    rsps.add(
        responses.GET,
        f"{base_url}/details/json",
        json=SAMPLE_DETAILS_RESPONSE,
        status=200,
    )

    # Text search
    rsps.add(
        responses.GET,
        f"{base_url}/textsearch/json",
        json=SAMPLE_TEXT_SEARCH_RESPONSE,
        status=200,
    )

    # Nearby search
    rsps.add(
        responses.GET,
        f"{base_url}/nearbysearch/json",
        json={"status": "OK", "results": [SAMPLE_PLACE_DETAILS]},
        status=200,
    )

    # Autocomplete
    rsps.add(
        responses.GET,
        f"{base_url}/autocomplete/json",
        json={
            "status": "OK",
            "predictions": [
                {"description": "123 Test St, City, ST 12345", "place_id": "ChIJtest123"}
            ],
        },
        status=200,
    )

