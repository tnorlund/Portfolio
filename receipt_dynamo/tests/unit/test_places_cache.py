from datetime import datetime

import pytest

from receipt_dynamo.entities.places_cache import (
    PlacesCache,
    item_to_places_cache,
)


@pytest.fixture
def example_places_cache():
    """Create a sample PlacesCache for testing."""
    return PlacesCache(
        search_type="ADDRESS",
        search_value="123 Main St",
        place_id="ChIJN1t_tDeuEmsRUsoyG83frY4",
        places_response={
            "name": "Test Business",
            "formatted_address": "123 Main St",
            "rating": 4.5,
            "user_ratings_total": 100,
        },
        last_updated=datetime.now().isoformat(),
        query_count=1,
    )


@pytest.mark.unit
def test_places_cache_init_valid(example_places_cache):
    """Test valid initialization of PlacesCache."""
    assert example_places_cache.search_type == "ADDRESS"
    assert example_places_cache.search_value == "123 Main St"
    assert example_places_cache.place_id == "ChIJN1t_tDeuEmsRUsoyG83frY4"
    assert example_places_cache.places_response["name"] == "Test Business"
    assert example_places_cache.query_count == 1


@pytest.mark.unit
def test_places_cache_init_invalid_search_type():
    """Test initialization with invalid search type."""
    with pytest.raises(ValueError, match="search_type must be one of:"):
        PlacesCache(
            search_type="INVALID",
            search_value="123 Main St",
            place_id="test123",
            places_response={"name": "Test"},
            last_updated=datetime.now().isoformat(),
            query_count=1,
        )


@pytest.mark.unit
def test_places_cache_init_invalid_search_value():
    """Test initialization with invalid search value."""
    with pytest.raises(ValueError, match="search_value cannot be empty"):
        PlacesCache(
            search_type="ADDRESS",
            search_value="",
            place_id="test123",
            places_response={"name": "Test"},
            last_updated=datetime.now().isoformat(),
            query_count=1,
        )


@pytest.mark.unit
def test_places_cache_init_invalid_place_id():
    """Test initialization with invalid place_id."""
    with pytest.raises(ValueError, match="place_id cannot be empty"):
        PlacesCache(
            search_type="ADDRESS",
            search_value="123 Main St",
            place_id="",
            places_response={"name": "Test"},
            last_updated=datetime.now().isoformat(),
            query_count=1,
        )


@pytest.mark.unit
def test_places_cache_init_invalid_places_response():
    """Test initialization with invalid places_response."""
    with pytest.raises(
        ValueError, match="places_response must be a dictionary"
    ):
        PlacesCache(
            search_type="ADDRESS",
            search_value="123 Main St",
            place_id="test123",
            places_response="not a dict",
            last_updated=datetime.now().isoformat(),
            query_count=1,
        )


@pytest.mark.unit
def test_places_cache_init_invalid_last_updated():
    """Test initialization with invalid last_updated."""
    with pytest.raises(
        ValueError, match="last_updated must be a valid ISO format"
    ):
        PlacesCache(
            search_type="ADDRESS",
            search_value="123 Main St",
            place_id="test123",
            places_response={"name": "Test"},
            last_updated="invalid date",
            query_count=1,
        )


@pytest.mark.unit
def test_places_cache_init_invalid_query_count():
    """Test initialization with invalid query_count."""
    with pytest.raises(ValueError, match="query_count must be non-negative"):
        PlacesCache(
            search_type="ADDRESS",
            search_value="123 Main St",
            place_id="test123",
            places_response={"name": "Test"},
            last_updated=datetime.now().isoformat(),
            query_count=-1,
        )


@pytest.mark.unit
def test_pad_search_value(example_places_cache):
    """Test search value padding for different types."""
    # Test ADDRESS padding
    address_cache = PlacesCache(
        search_type="ADDRESS",
        search_value="123 Main St",
        place_id="test123",
        places_response={"name": "Test"},
        last_updated=datetime.now().isoformat(),
    )
    padded_address = address_cache._pad_search_value("123 Main St")
    print(f"Padded address: {padded_address}")  # Debug output

    # The padded address should be in format: {hash}_{original_value}
    # First split on underscores to get all parts
    parts = [
        p for p in padded_address.split("_") if p
    ]  # Filter out empty strings
    print(f"Parts: {parts}")  # Debug output

    # The first part should be the hash
    value_hash = parts[0]
    assert len(value_hash) == 8, f"Hash should be 8 chars, got {value_hash}"

    # The last part should be the original value
    original_value = parts[-1]
    assert original_value == "123 Main St"

    # Verify normalized value was stored
    assert address_cache.normalized_value == "123 main street"

    # Verify total length (400 chars for the original value, plus hash and underscore)
    expected_length = 400 + 8 + 1  # 8 for hash, 1 for underscore
    assert (
        len(padded_address) == expected_length
    ), f"Expected length {expected_length}, got {len(padded_address)}"

    # Test PHONE padding
    phone_cache = PlacesCache(
        search_type="PHONE",
        search_value="(555) 123-4567",
        place_id="test123",
        places_response={"name": "Test"},
        last_updated=datetime.now().isoformat(),
    )
    padded_phone = phone_cache._pad_search_value("(555) 123-4567")
    assert padded_phone.startswith("_" * (30 - len("(555)123-4567")))
    assert padded_phone.endswith("(555)123-4567")
    assert len(padded_phone) == 30

    # Test URL padding
    url_cache = PlacesCache(
        search_type="URL",
        search_value="www.Example.com",
        place_id="test123",
        places_response={"name": "Test"},
        last_updated=datetime.now().isoformat(),
    )
    padded_url = url_cache._pad_search_value("www.Example.com")
    assert padded_url.startswith("_" * (100 - len("www.example.com")))
    assert padded_url.endswith("www.example.com")
    assert len(padded_url) == 100

    # Test invalid type
    with pytest.raises(ValueError, match="Invalid search type"):
        invalid_cache = PlacesCache(
            search_type="ADDRESS",  # Valid for init but we'll test invalid in _pad_search_value
            search_value="test",
            place_id="test123",
            places_response={"name": "Test"},
            last_updated=datetime.now().isoformat(),
        )
        invalid_cache.search_type = "INVALID"  # Change after init for testing
        invalid_cache._pad_search_value("test")


@pytest.mark.unit
def test_to_item(example_places_cache):
    """Test conversion to DynamoDB item."""
    item = example_places_cache.to_item()
    assert item["PK"]["S"].startswith("PLACES#ADDRESS")
    assert item["SK"]["S"].startswith("VALUE#")
    assert item["TYPE"]["S"] == "PLACES_CACHE"
    assert item["GSI1PK"]["S"] == "PLACE_ID"
    assert item["GSI1SK"]["S"].startswith("PLACE_ID#")
    assert item["place_id"]["S"] == example_places_cache.place_id
    assert "places_response" in item
    assert int(item["query_count"]["N"]) == example_places_cache.query_count


@pytest.mark.unit
def test_eq(example_places_cache):
    """Test equality comparison."""
    same_item = PlacesCache(
        search_type=example_places_cache.search_type,
        search_value=example_places_cache.search_value,
        place_id=example_places_cache.place_id,
        places_response=example_places_cache.places_response,
        last_updated=example_places_cache.last_updated,
        query_count=example_places_cache.query_count,
    )
    assert example_places_cache == same_item
    assert example_places_cache != "not a PlacesCache"


@pytest.mark.unit
def test_repr(example_places_cache):
    """Test string representation."""
    repr_str = repr(example_places_cache)
    assert "PlacesCache" in repr_str
    assert example_places_cache.search_type in repr_str
    assert example_places_cache.search_value in repr_str
    assert example_places_cache.place_id in repr_str
    assert str(example_places_cache.query_count) in repr_str


@pytest.mark.unit
def test_item_to_places_cache(example_places_cache):
    """Test conversion from DynamoDB item."""
    dynamo_item = example_places_cache.to_item()
    converted_item = item_to_places_cache(dynamo_item)
    assert converted_item == example_places_cache


@pytest.mark.unit
def test_item_to_places_cache_missing_keys():
    """Test conversion with missing keys."""
    with pytest.raises(ValueError, match="Item is missing required keys"):
        item_to_places_cache({})


@pytest.mark.unit
def test_item_to_places_cache_invalid_json():
    """Test conversion with invalid JSON in places_response."""
    item = {
        "PK": {"S": "PLACES#ADDRESS"},
        "SK": {"S": "VALUE#test"},
        "TYPE": {"S": "PLACES_CACHE"},
        "place_id": {"S": "test123"},
        "places_response": {"S": "invalid json"},
        "last_updated": {"S": datetime.now().isoformat()},
        "query_count": {"N": "1"},
        "search_type": {"S": "ADDRESS"},
        "search_value": {"S": "test"},
    }
    with pytest.raises(
        ValueError, match="Error converting item to PlacesCache"
    ):
        item_to_places_cache(item)
