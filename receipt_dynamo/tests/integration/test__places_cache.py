from datetime import datetime
from typing import Literal

import pytest
from botocore.exceptions import ClientError

from receipt_dynamo import DynamoClient
from receipt_dynamo.data.shared_exceptions import (
    EntityAlreadyExistsError,
    EntityNotFoundError,
)
from receipt_dynamo.entities.places_cache import PlacesCache


@pytest.fixture
def sample_places_cache():
    """Create a sample PlacesCache for testing."""
    return PlacesCache(
        search_type="ADDRESS",
        search_value="123 Main St",
        place_id="ChIJN1t_tDeuEmsRUsoyG83frY4",
        places_response={
            "name": "Test Place",
            "formatted_address": "123 Main St",
            "rating": 4.5,
            "user_ratings_total": 100,
        },
        last_updated=datetime.now().isoformat(),
        query_count=1,
    )


@pytest.mark.integration
def test_addPlacesCache_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_places_cache: PlacesCache,
):
    """Test successful addition of a PlacesCache."""
    # Arrange
    dynamo = DynamoClient(dynamodb_table)

    # Act
    dynamo.add_places_cache(sample_places_cache)

    # Assert
    response = dynamo._client.get_item(
        TableName=dynamo.table_name, Key=sample_places_cache.key
    )
    assert "Item" in response


@pytest.mark.integration
def test_addPlacesCache_duplicate_raises(
    dynamodb_table: Literal["MyMockedTable"],
    sample_places_cache: PlacesCache,
):
    """Test adding a duplicate PlacesCache raises ValueError."""
    # Arrange
    dynamo = DynamoClient(dynamodb_table)
    dynamo.add_places_cache(sample_places_cache)

    # Act & Assert
    with pytest.raises(EntityAlreadyExistsError, match="already exists"):
        dynamo.add_places_cache(sample_places_cache)


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (None, "item cannot be None"),
        (
            "not-a-places-cache-item",
            "item must be an instance of PlacesCache",
        ),
    ],
)
def test_addPlacesCache_invalid_parameters(
    dynamodb_table,
    invalid_input,
    expected_error,
):
    """Test adding a PlacesCache with invalid parameters."""
    # Arrange
    dynamo = DynamoClient(dynamodb_table)

    # Act & Assert
    with pytest.raises(ValueError, match=expected_error):
        dynamo.add_places_cache(invalid_input)


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_error",
    [
        (
            "ResourceNotFoundException",
            "Table not found",
            "Table not found",
        ),
        (
            "ProvisionedThroughputExceededException",
            "Throughput exceeded",
            "Throughput exceeded",
        ),
        (
            "ValidationException",
            "Invalid parameters",
            "Validation error",
        ),
        (
            "AccessDeniedException",
            "Access denied",
            "Access denied",
        ),
        (
            "UnknownError",
            "Unknown error occurred",
            "DynamoDB error during add_places_cache",
        ),
    ],
)
def test_addPlacesCache_client_errors(
    dynamodb_table,
    sample_places_cache,
    mocker,
    error_code,
    error_message,
    expected_error,
):
    """Test handling of various client errors when adding a PlacesCache."""
    # Arrange
    dynamo = DynamoClient(dynamodb_table)
    mock_client = mocker.patch.object(dynamo, "_client")
    mock_client.put_item.side_effect = ClientError(
        error_response={
            "Error": {"Code": error_code, "Message": error_message}
        },
        operation_name="PutItem",
    )

    # Act & Assert
    with pytest.raises(Exception, match=expected_error):
        dynamo.add_places_cache(sample_places_cache)


@pytest.mark.integration
def test_updatePlacesCache_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_places_cache: PlacesCache,
):
    """Test successful update of a PlacesCache."""
    # Arrange
    dynamo = DynamoClient(dynamodb_table)
    dynamo.add_places_cache(sample_places_cache)

    # Modify the item
    updated_item = PlacesCache(
        search_type=sample_places_cache.search_type,
        search_value=sample_places_cache.search_value,
        place_id=sample_places_cache.place_id,
        places_response=sample_places_cache.places_response,
        last_updated=datetime.now().isoformat(),
        query_count=2,  # Updated value
    )

    # Act
    dynamo.update_places_cache(updated_item)

    # Assert
    response = dynamo._client.get_item(
        TableName=dynamo.table_name, Key=updated_item.key
    )
    assert response["Item"]["query_count"]["N"] == "2"


@pytest.mark.integration
def test_updatePlacesCache_nonexistent_raises(
    dynamodb_table: Literal["MyMockedTable"],
    sample_places_cache: PlacesCache,
):
    """Test updating a non-existent PlacesCache raises ValueError."""
    # Arrange
    dynamo = DynamoClient(dynamodb_table)

    # Act & Assert
    with pytest.raises(EntityNotFoundError, match="not found during update_places_cache"):
        dynamo.update_places_cache(sample_places_cache)


@pytest.mark.integration
def test_deletePlacesCache_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_places_cache: PlacesCache,
):
    """Test successful deletion of a PlacesCache."""
    # Arrange
    dynamo = DynamoClient(dynamodb_table)
    dynamo.add_places_cache(sample_places_cache)

    # Act
    dynamo.delete_places_cache(sample_places_cache)

    # Assert
    response = dynamo._client.get_item(
        TableName=dynamo.table_name, Key=sample_places_cache.key
    )
    assert "Item" not in response


@pytest.mark.integration
def test_deletePlacesCache_nonexistent_raises(
    dynamodb_table: Literal["MyMockedTable"],
    sample_places_cache: PlacesCache,
):
    """Test deleting a non-existent PlacesCache raises ValueError."""
    # Arrange
    dynamo = DynamoClient(dynamodb_table)

    # Act & Assert
    with pytest.raises(EntityNotFoundError, match="not found during delete_places_cache"):
        dynamo.delete_places_cache(sample_places_cache)


@pytest.mark.integration
def test_getPlacesCache_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_places_cache: PlacesCache,
):
    """Test successful retrieval of a PlacesCache."""
    # Arrange
    dynamo = DynamoClient(dynamodb_table)
    dynamo.add_places_cache(sample_places_cache)

    # Act
    result = dynamo.get_places_cache(
        sample_places_cache.search_type, sample_places_cache.search_value
    )

    # Assert
    assert result == sample_places_cache


@pytest.mark.integration
def test_getPlacesCache_nonexistent_returns_none(
    dynamodb_table: Literal["MyMockedTable"],
):
    """Test getting a non-existent PlacesCache returns None."""
    # Arrange
    dynamo = DynamoClient(dynamodb_table)

    # Act
    result = dynamo.get_places_cache("ADDRESS", "nonexistent")

    # Assert
    assert result is None


@pytest.mark.integration
def test_getPlacesCacheByPlaceId_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_places_cache: PlacesCache,
):
    """Test successful retrieval of a PlacesCache by place_id."""
    # Arrange
    dynamo = DynamoClient(dynamodb_table)
    dynamo.add_places_cache(sample_places_cache)

    # Act
    result = dynamo.get_places_cache_by_place_id(sample_places_cache.place_id)

    # Assert
    assert result == sample_places_cache


@pytest.mark.integration
def test_getPlacesCacheByPlaceId_nonexistent_returns_none(
    dynamodb_table: Literal["MyMockedTable"],
):
    """Test getting a non-existent PlacesCache by place_id returns None."""
    # Arrange
    dynamo = DynamoClient(dynamodb_table)

    # Act
    result = dynamo.get_places_cache_by_place_id("nonexistent")

    # Assert
    assert result is None


@pytest.mark.integration
def test_listPlacesCaches_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_places_cache: PlacesCache,
):
    """Test successful listing of PlacesCaches."""
    # Arrange
    dynamo = DynamoClient(dynamodb_table)
    dynamo.add_places_cache(sample_places_cache)

    # Create a second item
    second_item = PlacesCache(
        search_type="PHONE",
        search_value="(555) 123-4567",
        place_id="ChIJN1t_tDeuEmsRUsoyG83frY5",
        places_response={
            "name": "Test Place 2",
            "formatted_phone_number": "(555) 123-4567",
        },
        last_updated=datetime.now().isoformat(),
        query_count=1,
    )
    dynamo.add_places_cache(second_item)

    # Act
    items, last_key = dynamo.list_places_caches()

    # Assert
    assert len(items) == 2
    assert last_key is None
    assert sample_places_cache in items
    assert second_item in items


@pytest.mark.integration
def test_listPlacesCaches_with_pagination(
    dynamodb_table: Literal["MyMockedTable"],
    sample_places_cache: PlacesCache,
):
    """Test listing PlacesCaches with pagination."""
    # Arrange
    dynamo = DynamoClient(dynamodb_table)
    dynamo.add_places_cache(sample_places_cache)

    # Create a second item
    second_item = PlacesCache(
        search_type="PHONE",
        search_value="(555) 123-4567",
        place_id="ChIJN1t_tDeuEmsRUsoyG83frY5",
        places_response={
            "name": "Test Place 2",
            "formatted_phone_number": "(555) 123-4567",
        },
        last_updated=datetime.now().isoformat(),
        query_count=1,
    )
    dynamo.add_places_cache(second_item)

    # Act
    items, last_key = dynamo.list_places_caches(limit=1)
    second_page, final_key = dynamo.list_places_caches(
        limit=1, last_evaluated_key=last_key
    )

    # Assert
    assert len(items) == 1
    assert last_key is not None
    assert len(second_page) == 1
    assert final_key is None
    assert items[0] != second_page[0]
