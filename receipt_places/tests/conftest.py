"""
Pytest fixtures for receipt_places tests.

Uses moto to mock DynamoDB and responses to mock HTTP requests.
"""

import os
from collections.abc import Generator
from typing import Any

import boto3
import pytest
import responses
from moto import mock_aws

from receipt_dynamo import DynamoClient
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
        {"AttributeName": "TYPE", "AttributeType": "S"},
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
        {
            "IndexName": "GSITYPE",
            "KeySchema": [
                {"AttributeName": "TYPE", "KeyType": "HASH"},
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


@pytest.fixture(scope="function")
def mock_aws_context() -> Generator[None, None, None]:
    """Create a mock AWS context for each test."""
    with mock_aws():
        yield


@pytest.fixture(scope="function")
def mock_dynamodb(mock_aws_context: None) -> Any:  # noqa: ARG001
    """Create a mock DynamoDB table client for each test."""
    # Create a raw boto3 client for direct access
    client = boto3.client("dynamodb", region_name="us-west-2")

    # Create the table
    client.create_table(**TABLE_SCHEMA)

    # Wait for table to be active
    waiter = client.get_waiter("table_exists")
    waiter.wait(TableName="test-receipts")

    return client


@pytest.fixture(scope="function")
def dynamo_client(mock_aws_context: None) -> DynamoClient:  # noqa: ARG001
    """Create a DynamoClient with mocked DynamoDB for each test."""
    # Create the DynamoClient within the mock_aws context
    # It will verify the table exists
    client = boto3.client("dynamodb", region_name="us-west-2")

    # Create the table first
    client.create_table(**TABLE_SCHEMA)
    waiter = client.get_waiter("table_exists")
    waiter.wait(TableName="test-receipts")

    # Now create the DynamoClient - it will verify the table exists
    return DynamoClient(table_name="test-receipts", region="us-west-2")


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
    dynamo_client: DynamoClient,
    test_config: PlacesConfig,
) -> CacheManager:
    """Create a CacheManager with mocked DynamoDB."""
    return CacheManager(
        table_name="test-receipts",
        config=test_config,
        dynamodb_client=dynamo_client,
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
    "website": "https://example.com",
    "vicinity": "123 Test St, City",
    "types": ["restaurant", "food", "establishment"],
    "business_status": "OPERATIONAL",
    "rating": 4.5,
    "user_ratings_total": 100,
    "geometry": {
        "location": {"lat": 40.7128, "lng": -74.0060},
    },
    "opening_hours": {
        "open_now": True,
        "weekday_text": [
            "Monday: 10:00 AM – 10:00 PM",
            "Tuesday: 10:00 AM – 10:00 PM",
            "Wednesday: 10:00 AM – 10:00 PM",
            "Thursday: 10:00 AM – 10:00 PM",
            "Friday: 10:00 AM – 11:00 PM",
            "Saturday: 10:00 AM – 11:00 PM",
            "Sunday: 10:00 AM – 10:00 PM",
        ],
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
            "business_status": "OPERATIONAL",
            "geometry": {
                "location": {"lat": 40.7128, "lng": -74.0060},
            },
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
            "types": ["restaurant", "food"],
            "geometry": {
                "location": {"lat": 40.7128, "lng": -74.0060},
            },
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
                {
                    "description": "123 Test St, City, ST 12345",
                    "place_id": "ChIJtest123",
                }
            ],
        },
        status=200,
    )
