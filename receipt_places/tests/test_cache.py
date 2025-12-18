"""
Tests for CacheManager with moto-mocked DynamoDB.
"""

from typing import Any

from receipt_places.cache import CacheManager
from receipt_places.config import PlacesConfig


class TestCacheManagerBasics:
    """Basic cache operations tests."""

    def test_cache_manager_initialization(
        self,
        cache_manager: CacheManager,
    ) -> None:
        """Test CacheManager initializes correctly."""
        assert cache_manager is not None

    def test_put_and_get_phone(
        self,
        cache_manager: CacheManager,
    ) -> None:
        """Test caching and retrieving a phone search result."""
        phone = "5551234567"
        place_id = "ChIJtest123"
        response = {
            "place_id": place_id,
            "name": "Test Business",
            "formatted_address": "123 Test St",
        }

        # Put in cache
        result = cache_manager.put("PHONE", phone, place_id, response)
        assert result is True

        # Get from cache
        cached = cache_manager.get("PHONE", phone)
        assert cached is not None
        assert cached["place_id"] == place_id
        assert cached["name"] == "Test Business"

    def test_put_and_get_address(
        self,
        cache_manager: CacheManager,
    ) -> None:
        """Test caching and retrieving an address search result."""
        address = "123 Main Street, City, ST 12345"
        place_id = "ChIJtest456"
        response = {
            "place_id": place_id,
            "name": "Another Business",
            "formatted_address": "123 Main Street, City, ST 12345",
        }

        # Put in cache
        result = cache_manager.put("ADDRESS", address, place_id, response)
        assert result is True

        # Get from cache
        cached = cache_manager.get("ADDRESS", address)
        assert cached is not None
        assert cached["place_id"] == place_id

    def test_cache_miss(
        self,
        cache_manager: CacheManager,
    ) -> None:
        """Test cache miss returns None."""
        cached = cache_manager.get("PHONE", "9999999999")
        assert cached is None

    def test_empty_search_value_skipped(
        self,
        cache_manager: CacheManager,
    ) -> None:
        """Test empty search values are not cached."""
        result = cache_manager.put("PHONE", "", "place_id", {"data": "test"})
        assert result is False

        cached = cache_manager.get("PHONE", "")
        assert cached is None


class TestCacheExclusions:
    """Test cache exclusion logic."""

    def test_area_search_not_cached(
        self,
        cache_manager: CacheManager,
    ) -> None:
        """Test that area searches (city only) are not cached."""
        area_searches = [
            "Seattle, WA",
            "New York, NY",
            "Los Angeles",
            "Chicago, Illinois, USA",
        ]

        response = {
            "place_id": "ChIJarea",
            "name": "City",
            "formatted_address": "Seattle, WA, USA",
        }

        for address in area_searches:
            result = cache_manager.put(
                "ADDRESS", address, "ChIJarea", response
            )
            # Should return False for area searches
            assert result is False, f"Should not cache area search: {address}"

    def test_route_level_not_cached(
        self,
        cache_manager: CacheManager,
    ) -> None:
        """Test that route-level results are not cached."""
        address = "123 Main Street"
        route_response = {
            "place_id": "ChIJroute",
            "types": ["route"],  # Route-level result
            "formatted_address": "Main Street, City, ST",
        }

        result = cache_manager.put(
            "ADDRESS", address, "ChIJroute", route_response
        )
        assert result is False

    def test_no_street_number_not_cached(
        self,
        cache_manager: CacheManager,
    ) -> None:
        """Test that results without street numbers are not cached."""
        address = "123 Main Street"
        response = {
            "place_id": "ChIJtest",
            "name": "Test",
            # No street number in formatted_address
            "formatted_address": "Main Street, City, ST 12345",
        }

        result = cache_manager.put("ADDRESS", address, "ChIJtest", response)
        assert result is False

    def test_valid_address_is_cached(
        self,
        cache_manager: CacheManager,
    ) -> None:
        """Test that valid addresses with street numbers are cached."""
        address = "123 Main Street, City, ST"
        response = {
            "place_id": "ChIJvalid",
            "name": "Valid Business",
            "formatted_address": "123 Main Street, City, ST 12345",
            "types": ["establishment"],
        }

        result = cache_manager.put("ADDRESS", address, "ChIJvalid", response)
        assert result is True

        cached = cache_manager.get("ADDRESS", address)
        assert cached is not None


class TestCacheInvalidResponses:
    """Test handling of invalid cached responses."""

    def test_no_results_response_skipped(
        self,
        cache_manager: CacheManager,
    ) -> None:
        """Test that NO_RESULTS responses are treated as misses."""
        phone = "5559999999"

        # Cache a NO_RESULTS response
        cache_manager.put(
            "PHONE",
            phone,
            "NO_RESULTS",
            {"status": "NO_RESULTS", "message": "No results found"},
        )

        # Should return None (treat as miss)
        cached = cache_manager.get("PHONE", phone)
        assert cached is None

    def test_invalid_response_skipped(
        self,
        cache_manager: CacheManager,
    ) -> None:
        """Test that INVALID responses are treated as misses."""
        phone = "1234"

        # Cache an INVALID response
        cache_manager.put(
            "PHONE",
            phone,
            "INVALID",
            {"status": "INVALID", "message": "Invalid phone"},
        )

        # Should return None (treat as miss)
        cached = cache_manager.get("PHONE", phone)
        assert cached is None


class TestCacheQueryCount:
    """Test query count tracking."""

    def test_query_count_incremented(
        self,
        cache_manager: CacheManager,
    ) -> None:
        """Test that query count is incremented on cache hits."""
        phone = "5551234567"
        place_id = "ChIJtest123"
        response = {"place_id": place_id, "name": "Test"}

        # Put in cache
        cache_manager.put("PHONE", phone, place_id, response)

        # Get multiple times
        for _ in range(3):
            cache_manager.get("PHONE", phone)

        # Check query count in DynamoDB using the client from cache_manager
        key = cache_manager._build_key("PHONE", phone)
        item = cache_manager._client._client.get_item(
            TableName="test-receipts",
            Key=key,
        )

        # Query count should be > 0
        query_count = int(item["Item"]["query_count"]["N"])
        assert query_count >= 1  # At least 1 from the gets


class TestCacheDelete:
    """Test cache deletion."""

    def test_delete_entry(
        self,
        cache_manager: CacheManager,
    ) -> None:
        """Test deleting a cache entry."""
        phone = "5551234567"
        response = {"place_id": "ChIJtest", "name": "Test"}

        # Put and verify
        cache_manager.put("PHONE", phone, "ChIJtest", response)
        assert cache_manager.get("PHONE", phone) is not None

        # Delete
        result = cache_manager.delete("PHONE", phone)
        assert result is True

        # Verify deleted
        assert cache_manager.get("PHONE", phone) is None


class TestCacheByPlaceId:
    """Test GSI1 lookup by place_id."""

    def test_get_by_place_id(
        self,
        cache_manager: CacheManager,
    ) -> None:
        """Test retrieving cache entry by place_id."""
        phone = "5551234567"
        place_id = "ChIJunique123"
        response = {"place_id": place_id, "name": "Unique Business"}

        # Put in cache
        cache_manager.put("PHONE", phone, place_id, response)

        # Get by place_id
        cached = cache_manager.get_by_place_id(place_id)
        assert cached is not None
        assert cached["name"] == "Unique Business"

    def test_get_by_place_id_not_found(
        self,
        cache_manager: CacheManager,
    ) -> None:
        """Test GSI1 lookup when place_id doesn't exist."""
        cached = cache_manager.get_by_place_id("ChIJnonexistent")
        assert cached is None


class TestCacheStats:
    """Test cache statistics."""

    def test_get_stats(
        self,
        cache_manager: CacheManager,
    ) -> None:
        """Test getting cache statistics."""
        # Add some entries
        cache_manager.put(
            "PHONE", "5551111111", "ChIJ1", {"place_id": "ChIJ1", "name": "A"}
        )
        cache_manager.put(
            "PHONE", "5552222222", "ChIJ2", {"place_id": "ChIJ2", "name": "B"}
        )
        cache_manager.put(
            "ADDRESS",
            "100 Main St",
            "ChIJ3",
            {
                "place_id": "ChIJ3",
                "name": "C",
                "formatted_address": "100 Main St",
            },
        )

        stats = cache_manager.get_stats()

        assert "entries_by_type" in stats
        assert stats["entries_by_type"]["PHONE"] == 2
        assert stats["entries_by_type"]["ADDRESS"] == 1
        assert stats["total_entries"] == 3
        assert stats["cache_enabled"] is True


class TestCacheDisabled:
    """Test behavior when caching is disabled."""

    def test_cache_disabled_skips_operations(
        self,
        mock_dynamodb: Any,
    ) -> None:
        """Test that cache operations are skipped when disabled."""
        config = PlacesConfig(
            api_key="test",
            table_name="test-receipts",
            cache_enabled=False,
        )
        manager = CacheManager(
            table_name="test-receipts",
            config=config,
            dynamodb_client=mock_dynamodb,
        )

        # Put should return False
        result = manager.put(
            "PHONE", "5551234567", "ChIJ", {"place_id": "ChIJ"}
        )
        assert result is False

        # Get should return None
        cached = manager.get("PHONE", "5551234567")
        assert cached is None
