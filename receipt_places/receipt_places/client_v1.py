"""
Google Places API v1 client with automatic DynamoDB caching.

This module provides a clean interface to the new Google Places API (v1)
with built-in caching to minimize API costs. It maintains the same public
interface as the legacy client for backward compatibility.

Key differences from legacy API:
- Base URL: https://places.googleapis.com/v1/
- Auth via header: X-Goog-Api-Key
- Field masks control response (only request fields you need)
- Direct Place object responses (no status wrapper)
"""

import logging
import re
from typing import Any, Optional, cast

import requests  # type: ignore[import-untyped]
from tenacity import (
    RetryError,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from receipt_places.adapter import adapt_v1_to_candidate, adapt_v1_to_legacy
from receipt_places.cache import CacheManager
from receipt_places.config import PlacesConfig, get_config
from receipt_places.parsers import APIError, ParseError
from receipt_places.types import Place, Candidate
from receipt_places.types_v1 import PlaceV1, SearchTextResponse, SearchNearbyResponse

logger = logging.getLogger(__name__)


class PlacesClientV1:
    """
    Google Places API v1 client with DynamoDB caching.

    This client automatically caches responses to minimize API costs:
    - Phone searches: ~70-90% cache hit rate
    - Address searches: ~40-60% cache hit rate

    Cache TTL is configurable (default 30 days).

    Example:
        ```python
        from receipt_places import PlacesClientV1

        client = PlacesClientV1(api_key="your-api-key")

        # Search by phone (uses cache)
        result = client.search_by_phone("555-123-4567")

        # Search by address (uses cache)
        result = client.search_by_address("123 Main St, City, ST")

        # Direct place lookup (no cache needed)
        result = client.get_place_details("ChIJ...")
        ```
    """

    BASE_URL = "https://places.googleapis.com/v1"

    # Field mask for cost optimization (only request what we need)
    DEFAULT_FIELD_MASK = [
        "id",
        "displayName",
        "formattedAddress",
        "shortFormattedAddress",
        "location",
        "viewport",
        "plusCode",
        "types",
        "businessStatus",
        "nationalPhoneNumber",
        "internationalPhoneNumber",
        "websiteUri",
        "googleMapsUri",
        "rating",
        "userRatingCount",
        "currentOpeningHours",
        "photos",
    ]

    def __init__(
        self,
        api_key: Optional[str] = None,
        config: Optional[PlacesConfig] = None,
        cache_manager: Optional[CacheManager] = None,
    ):
        """
        Initialize the v1 Places client.

        Args:
            api_key: Google Places API key (defaults to config)
            config: Configuration settings
            cache_manager: Optional pre-configured cache manager
        """
        self._config = config or get_config()
        self._api_key = api_key or self._config.api_key.get_secret_value()

        if not self._api_key:
            raise ValueError(
                "Google Places API key required. "
                "Set RECEIPT_PLACES_API_KEY environment variable."
            )

        # Initialize cache manager
        if cache_manager is not None:
            self._cache = cache_manager
        else:
            self._cache = CacheManager(config=self._config)

        self._timeout = self._config.request_timeout

        logger.info(
            "PlacesClientV1 initialized (cache=%s, api_version=v1)",
            "enabled" if self._config.cache_enabled else "disabled",
        )

    @retry(
        retry=retry_if_exception_type(requests.exceptions.Timeout),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
    )
    def _make_request(
        self,
        endpoint: str,
        method: str = "GET",
        field_mask: Optional[list[str]] = None,
        headers: Optional[dict[str, str]] = None,
        json_body: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """
        Make an HTTP request to the Places API v1 with automatic retries on timeout.

        Args:
            endpoint: API endpoint (e.g., "places/{place_id}")
            method: HTTP method (GET or POST)
            field_mask: List of fields to request (uses default if not provided)
            headers: Additional headers to include
            json_body: Request body for POST requests

        Returns:
            Parsed JSON response

        Raises:
            APIError: If the API returns an error
            ParseError: If the response format is invalid
            requests.exceptions.HTTPError: If HTTP status is not 2xx
        """
        url = f"{self.BASE_URL}/{endpoint}"

        # Build request headers
        request_headers = {
            "X-Goog-Api-Key": self._api_key,
            "Content-Type": "application/json",
        }

        # Add field mask header (only for GET requests, v1 requires this)
        if method == "GET":
            field_mask = field_mask or self.DEFAULT_FIELD_MASK
            request_headers["X-Goog-FieldMask"] = ",".join(field_mask)
        elif method == "POST":
            # For POST requests, field mask is part of the body
            if field_mask is None:
                field_mask = self.DEFAULT_FIELD_MASK
            if json_body is None:
                json_body = {}
            # Field mask in v1 POST is specified differently than GET
            # Some endpoints put it in the request body
            request_headers["X-Goog-FieldMask"] = ",".join(field_mask)

        if headers:
            request_headers.update(headers)

        logger.debug(
            "Making %s request to %s (timeout=%s)",
            method,
            endpoint,
            self._timeout,
        )

        try:
            if method == "GET":
                response = requests.get(
                    url,
                    headers=request_headers,
                    timeout=self._timeout,
                )
            else:  # POST
                response = requests.post(
                    url,
                    headers=request_headers,
                    json=json_body,
                    timeout=self._timeout,
                )

            response.raise_for_status()
            data = cast(dict[str, Any], response.json())
            return data

        except requests.exceptions.Timeout as e:
            logger.warning("API request timeout: %s", e)
            raise
        except requests.exceptions.HTTPError as e:
            # Parse error response from v1 API
            try:
                error_data = e.response.json()
                error_msg = error_data.get("error", {}).get("message", str(e))
            except Exception:
                error_msg = str(e)
            logger.error("API HTTP error: %s", error_msg)
            raise APIError(f"Places API error: {error_msg}") from e

    def get_place_details(self, place_id: str) -> Optional[Place]:
        """
        Get place details by place ID.

        This method calls the v1 API to get full details for a specific place.
        The result is adapted to the legacy Place format for compatibility.

        Args:
            place_id: Google Place ID (e.g., "ChIJ...")

        Returns:
            Typed Place object if successful, None on error

        Raises:
            APIError: If the API returns an error
        """
        if not place_id:
            logger.warning("Empty place_id provided to get_place_details")
            return None

        logger.info("🔍 Places API v1: get_place_details(%s)", place_id)

        try:
            # Make request to v1 API
            data = self._make_request(f"places/{place_id}")

            # Parse v1 response
            place_v1 = PlaceV1.model_validate(data)

            # Adapt to legacy format
            place = adapt_v1_to_legacy(place_v1)

            logger.debug("✅ Retrieved place details: %s", place.name)
            return place

        except (APIError, ParseError) as e:
            logger.warning("Failed to get place details for %s: %s", place_id, e)
            return None

    def search_by_phone(self, phone_number: str) -> Optional[Place]:
        """
        Search for a place by phone number.

        This method checks the DynamoDB cache first, only making
        an API call on cache miss. Returns a typed Place object.

        Args:
            phone_number: Phone number to search for

        Returns:
            Typed Place object if found, None otherwise
        """
        # Validate input
        if not self._is_valid_phone(phone_number):
            return None

        # Extract digits for cache key
        digits = self._extract_digits(phone_number)

        # Try cache first
        expected_fields = {"id", "displayName", "types", "businessStatus"}
        cached_result = self._try_cached_phone_result(digits, expected_fields)
        if cached_result:
            return cached_result

        # Try API search
        api_result = self._try_phone_api_search(phone_number, digits, expected_fields)
        if api_result:
            return api_result

        # Fallback to text search
        return self._try_text_search_fallback(phone_number, digits)

    def _extract_digits(self, phone_number: str) -> str:
        """Extract only digits from phone number."""
        return "".join(c for c in phone_number if c.isdigit())

    def _is_valid_phone(self, phone_number: str) -> bool:
        """Check if phone number has valid format (non-empty, enough digits)."""
        if not phone_number:
            logger.debug("Empty phone number, skipping search")
            return False

        digits = self._extract_digits(phone_number)
        if len(digits) < 7:
            logger.debug("Phone number too short: %s", phone_number)
            return False

        return True

    def _try_cached_phone_result(
        self, digits: str, expected_fields: set[str]
    ) -> Optional[Place]:
        """Try to retrieve and parse cached phone search result."""
        cached_dict = self._cache.get("PHONE", digits)
        if not cached_dict:
            return None

        try:
            # Handle both v1 responses (direct dict) and legacy (with 'result' wrapper)
            place_data = cached_dict.get("result", cached_dict)
            place_v1 = PlaceV1.model_validate(place_data)
            place = adapt_v1_to_legacy(place_v1)
            logger.debug("✅ Cache hit for phone: %s", digits)
            return place
        except (APIError, ParseError) as e:
            logger.warning("Failed to parse cached phone result: %s", e)
            return None

    def _try_phone_api_search(
        self, phone_number: str, digits: str, expected_fields: set[str]
    ) -> Optional[Place]:
        """Make API call for phone search, fetch details, and cache result."""
        logger.info("📱 Places API v1: search_by_phone(%s)", phone_number)

        try:
            # Format phone number for API
            # v1 searchText expects phone in E.164 format or local format
            api_phone = self._format_phone_for_api(digits)

            # Search for place by phone using searchText endpoint
            body = {
                "textQuery": api_phone,
                "regionCode": "US",  # Helps with phone search accuracy
            }

            data = self._make_request(
                "places:searchText",
                method="POST",
                json_body=body,
            )

            # Parse response
            response = SearchTextResponse.model_validate(data)
            if not response.places or len(response.places) == 0:
                logger.debug("No places found for phone: %s", phone_number)
                return None

            # Get first result
            place_v1 = response.places[0]
            if not place_v1.id:
                logger.warning("Search result has no place_id")
                return None

            # Fetch full details
            details = self.get_place_details(place_v1.id)
            if not details:
                logger.warning("Could not fetch full details for place: %s", place_v1.id)
                return None

            # Cache the full details response
            # Store in v1 format with marker
            details_response = details.model_dump()
            details_response["_api_version"] = "v1"
            self._cache.put("PHONE", digits, place_v1.id, details_response)

            logger.debug("✅ Found and cached place for phone: %s", phone_number)
            return details

        except (APIError, ParseError, RetryError) as e:
            logger.warning("Phone search failed: %s", e)
            return None

    def _format_phone_for_api(self, digits: str) -> str:
        """Format phone number for API search."""
        # E.164 format: +1{10-digit} for US
        if len(digits) == 10:
            return f"+1{digits}"
        elif len(digits) == 11 and digits[0] == "1":
            return f"+{digits}"
        else:
            # For other lengths, prefix with +1 and hope for best
            return f"+1{digits}"

    def _try_text_search_fallback(
        self, phone_number: str, digits: str
    ) -> Optional[Place]:
        """Fallback to text search if phone search fails."""
        logger.info("📝 Falling back to text search for phone: %s", phone_number)

        try:
            body = {
                "textQuery": phone_number,
                "regionCode": "US",
            }

            data = self._make_request(
                "places:searchText",
                method="POST",
                json_body=body,
            )

            response = SearchTextResponse.model_validate(data)
            if not response.places or len(response.places) == 0:
                return None

            place_v1 = response.places[0]
            if not place_v1.id:
                return None

            # Fetch full details
            return self.get_place_details(place_v1.id)

        except (APIError, ParseError) as e:
            logger.warning("Text search fallback failed: %s", e)
            return None

    def search_by_address(
        self,
        address: str,
        receipt_words: Optional[list[Any]] = None,
    ) -> Optional[Place]:
        """
        Search for a place by address.

        This method checks the DynamoDB cache first, only making
        an API call on cache miss.

        Args:
            address: Street address to search for
            receipt_words: Optional receipt word data (unused, for API compatibility)

        Returns:
            Typed Place object if found, None otherwise
        """
        if not address:
            logger.warning("Empty address provided to search_by_address")
            return None

        # Create normalized cache key
        cache_key = self._normalize_address_key(address)

        # Try cache first
        expected_fields = {"id", "displayName", "formattedAddress"}
        cached_result = self._try_cached_address_result(cache_key, expected_fields)
        if cached_result:
            return cached_result

        # Try API search
        api_result = self._try_address_api_search(address, cache_key, expected_fields)
        return api_result

    def _normalize_address_key(self, address: str) -> str:
        """Normalize address for cache key."""
        # Remove extra whitespace and convert to uppercase
        normalized = " ".join(address.split()).upper()
        # Replace special characters
        normalized = re.sub(r"[^A-Z0-9\s]", "", normalized)
        return normalized

    def _try_cached_address_result(
        self, cache_key: str, expected_fields: set[str]
    ) -> Optional[Place]:
        """Try to retrieve cached address search result."""
        cached_dict = self._cache.get("ADDRESS", cache_key)
        if not cached_dict:
            return None

        try:
            place_data = cached_dict.get("result", cached_dict)
            place_v1 = PlaceV1.model_validate(place_data)
            place = adapt_v1_to_legacy(place_v1)
            logger.debug("✅ Cache hit for address: %s", cache_key)
            return place
        except (APIError, ParseError) as e:
            logger.warning("Failed to parse cached address result: %s", e)
            return None

    def _try_address_api_search(
        self, address: str, cache_key: str, expected_fields: set[str]
    ) -> Optional[Place]:
        """Make API call for address search and cache result."""
        logger.info("📍 Places API v1: search_by_address(%s)", address)

        try:
            # Search using searchText endpoint
            body = {
                "textQuery": address,
                "regionCode": "US",
            }

            data = self._make_request(
                "places:searchText",
                method="POST",
                json_body=body,
            )

            response = SearchTextResponse.model_validate(data)
            if not response.places or len(response.places) == 0:
                logger.debug("No places found for address: %s", address)
                return None

            place_v1 = response.places[0]
            if not place_v1.id:
                logger.warning("Search result has no place_id")
                return None

            # Fetch full details
            details = self.get_place_details(place_v1.id)
            if not details:
                return None

            # Cache the result
            details_response = details.model_dump()
            details_response["_api_version"] = "v1"
            self._cache.put("ADDRESS", cache_key, place_v1.id, details_response)

            logger.debug("✅ Found and cached place for address: %s", address)
            return details

        except (APIError, ParseError) as e:
            logger.warning("Address search failed: %s", e)
            return None
