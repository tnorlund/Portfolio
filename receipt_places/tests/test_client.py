"""
Tests for PlacesClient with mocked HTTP requests and DynamoDB.
"""

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import responses
from responses import matchers

from receipt_places.cache import CacheManager
from receipt_places.client import PlacesClient, PlacesAPIError
from receipt_places.config import PlacesConfig
from tests.conftest import (
    SAMPLE_DETAILS_RESPONSE,
    SAMPLE_FIND_PLACE_RESPONSE,
    SAMPLE_PLACE_DETAILS,
    SAMPLE_TEXT_SEARCH_RESPONSE,
    add_places_api_mocks,
)


class TestPlacesClientInit:
    """Test PlacesClient initialization."""

    def test_init_with_api_key(
        self,
        cache_manager: CacheManager,
        test_config: PlacesConfig,
    ) -> None:
        """Test client initialization with API key."""
        client = PlacesClient(
            api_key="my-api-key",
            config=test_config,
            cache_manager=cache_manager,
        )
        assert client is not None

    def test_init_without_api_key_raises(
        self,
        cache_manager: CacheManager,
    ) -> None:
        """Test that initialization without API key raises error."""
        config = PlacesConfig(
            api_key="",
            table_name="test",
            cache_enabled=False,
        )

        with pytest.raises(ValueError, match="API key required"):
            PlacesClient(api_key="", config=config, cache_manager=cache_manager)


class TestSearchByPhone:
    """Test phone number search functionality."""

    @responses.activate
    def test_search_by_phone_cache_miss(
        self,
        places_client: PlacesClient,
    ) -> None:
        """Test phone search with cache miss makes API call."""
        add_places_api_mocks(responses)

        result = places_client.search_by_phone("555-123-4567")

        assert result is not None
        assert result["place_id"] == "ChIJtest123"
        assert result["name"] == "Test Business"

        # Verify API was called
        assert len(responses.calls) >= 1

    @responses.activate
    def test_search_by_phone_cache_hit(
        self,
        places_client: PlacesClient,
    ) -> None:
        """Test phone search with cache hit skips API call."""
        # First call - API
        add_places_api_mocks(responses)
        result1 = places_client.search_by_phone("555-123-4567")

        # Record API call count
        first_call_count = len(responses.calls)

        # Second call - should hit cache
        result2 = places_client.search_by_phone("555-123-4567")

        assert result1 is not None
        assert result2 is not None
        assert result2["place_id"] == result1["place_id"]

        # No additional API calls
        assert len(responses.calls) == first_call_count

    def test_search_by_phone_empty(
        self,
        places_client: PlacesClient,
    ) -> None:
        """Test empty phone number returns None."""
        result = places_client.search_by_phone("")
        assert result is None

    def test_search_by_phone_too_short(
        self,
        places_client: PlacesClient,
    ) -> None:
        """Test short phone number returns None."""
        result = places_client.search_by_phone("123")
        assert result is None

    @responses.activate
    def test_search_by_phone_no_results(
        self,
        places_client: PlacesClient,
    ) -> None:
        """Test phone search with no results."""
        responses.add(
            responses.GET,
            "https://maps.googleapis.com/maps/api/place/findplacefromtext/json",
            json={"status": "ZERO_RESULTS", "candidates": []},
            status=200,
        )
        responses.add(
            responses.GET,
            "https://maps.googleapis.com/maps/api/place/textsearch/json",
            json={"status": "ZERO_RESULTS", "results": []},
            status=200,
        )

        result = places_client.search_by_phone("999-999-9999")
        assert result is None


class TestSearchByAddress:
    """Test address search functionality."""

    @responses.activate
    def test_search_by_address_cache_miss(
        self,
        places_client: PlacesClient,
    ) -> None:
        """Test address search with cache miss makes API call."""
        add_places_api_mocks(responses)

        result = places_client.search_by_address("123 Test St, City, ST 12345")

        assert result is not None
        assert result["place_id"] == "ChIJtest123"

    @responses.activate
    def test_search_by_address_cache_hit(
        self,
        places_client: PlacesClient,
    ) -> None:
        """Test address search with cache hit skips API call."""
        add_places_api_mocks(responses)

        # First call
        result1 = places_client.search_by_address("123 Test St, City, ST 12345")
        first_call_count = len(responses.calls)

        # Second call - should hit cache
        result2 = places_client.search_by_address("123 Test St, City, ST 12345")

        assert result1 is not None
        assert result2 is not None
        assert len(responses.calls) == first_call_count

    def test_search_by_address_empty(
        self,
        places_client: PlacesClient,
    ) -> None:
        """Test empty address returns None."""
        result = places_client.search_by_address("")
        assert result is None

    @responses.activate
    def test_search_by_address_skips_route_results(
        self,
        places_client: PlacesClient,
    ) -> None:
        """Test that route-level results are skipped."""
        # Return a route-level result
        responses.add(
            responses.GET,
            "https://maps.googleapis.com/maps/api/place/findplacefromtext/json",
            json={
                "status": "OK",
                "candidates": [
                    {
                        "place_id": "ChIJroute",
                        "types": ["route"],
                        "formatted_address": "Main St",
                    }
                ],
            },
            status=200,
        )

        result = places_client.search_by_address("Main St, City, ST")
        assert result is None


class TestSearchByText:
    """Test text search functionality."""

    @responses.activate
    def test_search_by_text(
        self,
        places_client: PlacesClient,
    ) -> None:
        """Test text search."""
        add_places_api_mocks(responses)

        result = places_client.search_by_text("Coffee Shop Seattle")

        assert result is not None
        assert result["place_id"] == "ChIJtest123"

    @responses.activate
    def test_search_by_text_with_location(
        self,
        places_client: PlacesClient,
    ) -> None:
        """Test text search with location bias."""
        add_places_api_mocks(responses)

        result = places_client.search_by_text(
            "Coffee Shop",
            lat=47.6062,
            lng=-122.3321,
        )

        assert result is not None

    def test_search_by_text_empty(
        self,
        places_client: PlacesClient,
    ) -> None:
        """Test empty text returns None."""
        result = places_client.search_by_text("")
        assert result is None


class TestSearchNearby:
    """Test nearby search functionality."""

    @responses.activate
    def test_search_nearby(
        self,
        places_client: PlacesClient,
    ) -> None:
        """Test nearby search."""
        add_places_api_mocks(responses)

        results = places_client.search_nearby(
            lat=47.6062,
            lng=-122.3321,
            radius=500,
        )

        assert len(results) > 0
        assert results[0]["place_id"] == "ChIJtest123"

    @responses.activate
    def test_search_nearby_with_keyword(
        self,
        places_client: PlacesClient,
    ) -> None:
        """Test nearby search with keyword."""
        add_places_api_mocks(responses)

        results = places_client.search_nearby(
            lat=47.6062,
            lng=-122.3321,
            radius=100,
            keyword="restaurant",
        )

        assert len(results) > 0


class TestGetPlaceDetails:
    """Test place details retrieval."""

    @responses.activate
    def test_get_place_details(
        self,
        places_client: PlacesClient,
    ) -> None:
        """Test getting place details."""
        responses.add(
            responses.GET,
            "https://maps.googleapis.com/maps/api/place/details/json",
            json=SAMPLE_DETAILS_RESPONSE,
            status=200,
        )

        result = places_client.get_place_details("ChIJtest123")

        assert result is not None
        assert result["place_id"] == "ChIJtest123"
        assert result["name"] == "Test Business"
        assert result["formatted_phone_number"] == "(555) 123-4567"

    def test_get_place_details_empty(
        self,
        places_client: PlacesClient,
    ) -> None:
        """Test empty place_id returns None."""
        result = places_client.get_place_details("")
        assert result is None


class TestAutocomplete:
    """Test address autocomplete."""

    @responses.activate
    def test_autocomplete_address(
        self,
        places_client: PlacesClient,
    ) -> None:
        """Test address autocomplete."""
        add_places_api_mocks(responses)

        result = places_client.autocomplete_address("123 Test")

        assert result is not None
        assert "description" in result

    def test_autocomplete_empty(
        self,
        places_client: PlacesClient,
    ) -> None:
        """Test empty input returns None."""
        result = places_client.autocomplete_address("")
        assert result is None


class TestIsAreaSearch:
    """Test area search detection."""

    def test_is_area_search_true(
        self,
        places_client: PlacesClient,
    ) -> None:
        """Test area searches are detected."""
        area_searches = [
            "Seattle, WA",
            "New York, NY",
            "Los Angeles",
            "Chicago IL",  # City + state abbreviation (no comma)
        ]

        for address in area_searches:
            assert places_client.is_area_search(address), f"{address} should be area"

    def test_is_area_search_false(
        self,
        places_client: PlacesClient,
    ) -> None:
        """Test specific addresses are not area searches."""
        specific_addresses = [
            "123 Main St, Seattle, WA",
            "456 Broadway Ave, New York, NY 10001",
            "789 Wilshire Blvd, Los Angeles, CA",
        ]

        for address in specific_addresses:
            assert not places_client.is_area_search(address), f"{address} not area"


class TestCacheAccess:
    """Test direct cache access."""

    def test_cache_property(
        self,
        places_client: PlacesClient,
        cache_manager: CacheManager,
    ) -> None:
        """Test cache property returns the cache manager."""
        assert places_client.cache is cache_manager

    @responses.activate
    def test_get_cache_stats(
        self,
        places_client: PlacesClient,
    ) -> None:
        """Test getting cache stats through client."""
        add_places_api_mocks(responses)

        # Add some cached entries
        places_client.search_by_phone("555-111-1111")
        places_client.search_by_phone("555-222-2222")

        stats = places_client.get_cache_stats()

        assert "total_entries" in stats
        assert "entries_by_type" in stats
        assert stats["cache_enabled"] is True


class TestErrorHandling:
    """Test error handling."""

    @responses.activate
    def test_api_timeout_retry(
        self,
        places_client: PlacesClient,
    ) -> None:
        """Test API timeout triggers retry."""
        from requests.exceptions import Timeout

        # First call times out, second succeeds
        responses.add(
            responses.GET,
            "https://maps.googleapis.com/maps/api/place/findplacefromtext/json",
            body=Timeout("Connection timed out"),
        )
        responses.add(
            responses.GET,
            "https://maps.googleapis.com/maps/api/place/findplacefromtext/json",
            json=SAMPLE_FIND_PLACE_RESPONSE,
            status=200,
        )
        responses.add(
            responses.GET,
            "https://maps.googleapis.com/maps/api/place/details/json",
            json=SAMPLE_DETAILS_RESPONSE,
            status=200,
        )

        result = places_client.search_by_phone("555-123-4567")

        # Should succeed after retry
        assert result is not None

    @responses.activate
    def test_network_error_returns_none(
        self,
        places_client: PlacesClient,
    ) -> None:
        """Test network errors return None gracefully."""
        from requests.exceptions import ConnectionError

        responses.add(
            responses.GET,
            "https://maps.googleapis.com/maps/api/place/findplacefromtext/json",
            body=ConnectionError("Network error"),
        )

        result = places_client.search_by_phone("555-123-4567")
        assert result is None

