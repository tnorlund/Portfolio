"""
Google Places API client with automatic DynamoDB caching.

This module provides a clean interface to the Google Places API
with built-in caching to minimize API costs.
"""

import logging
import re
from typing import Any, cast

import requests  # type: ignore[import-untyped]  # types-requests in dev dependencies
from tenacity import (
    RetryError,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from receipt_places.cache import CacheManager
from receipt_places.config import PlacesConfig, get_config
from receipt_places.parsers import (
    APIError,
    ParseError,
    parse_place_autocomplete_response,
    parse_place_candidates_response,
    parse_place_details_response,
    parse_place_search_response,
)
from receipt_places.types import Place

logger = logging.getLogger(__name__)


class PlacesAPIError(Exception):
    """Exception raised for Places API errors."""

    def __init__(self, message: str, status: str | None = None):
        super().__init__(message)
        self.status = status


class PlacesClient:
    """
    Google Places API client with DynamoDB caching.

    This client automatically caches responses to minimize API costs:
    - Phone searches: ~70-90% cache hit rate
    - Address searches: ~40-60% cache hit rate

    Cache TTL is configurable (default 30 days).

    Example:
        ```python
        from receipt_places import PlacesClient

        client = PlacesClient(api_key="your-api-key")

        # Search by phone (uses cache)
        result = client.search_by_phone("555-123-4567")

        # Search by address (uses cache)
        result = client.search_by_address("123 Main St, City, ST")

        # Direct place lookup (no cache needed)
        result = client.get_place_details("ChIJ...")
        ```
    """

    BASE_URL = "https://maps.googleapis.com/maps/api/place"

    def __init__(
        self,
        api_key: str | None = None,
        config: PlacesConfig | None = None,
        cache_manager: CacheManager | None = None,
    ):
        """
        Initialize the Places client.

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
            "PlacesClient initialized (cache=%s)",
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
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Make an HTTP request to the Places API with automatic retries on timeout.

        Retries on timeout exceptions up to 3 times with exponential backoff.
        HTTP errors and API errors are not retried.
        """
        url = f"{self.BASE_URL}/{endpoint}"
        params["key"] = self._api_key

        response = requests.get(url, params=params, timeout=self._timeout)
        response.raise_for_status()

        data = cast(dict[str, Any], response.json())
        status = data.get("status", "UNKNOWN")

        if status not in ("OK", "ZERO_RESULTS"):
            error_msg = data.get(
                "error_message", f"API returned status: {status}"
            )
            logger.warning("Places API error: %s (status=%s)", error_msg, status)

        return data

    def search_by_phone(self, phone_number: str) -> Place | None:
        """
        Search for a place by phone number.

        This method checks the DynamoDB cache first, only making
        an API call on cache miss. Returns a typed Place object with
        guaranteed fields: place_id, name, location, types.

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
        expected_fields = {"place_id", "name", "types", "business_status"}
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
    ) -> Place | None:
        """Try to retrieve and parse cached phone search result."""
        cached_dict = self._cache.get("PHONE", digits)
        if not cached_dict or not cached_dict.get("result"):
            return None

        try:
            return parse_place_details_response(
                cached_dict, expected_fields=expected_fields
            )
        except (APIError, ParseError) as e:
            logger.warning("Failed to parse cached phone search result: %s", e)
            return None

    def _try_phone_api_search(
        self, phone_number: str, digits: str, expected_fields: set[str]
    ) -> Place | None:
        """Make API call for phone search, fetch details, and cache result."""
        logger.info("🔍 Places API: search_by_phone(%s)", phone_number)

        try:
            api_phone = self._format_phone_for_api(digits)
            data = self._make_request(
                "findplacefromtext/json",
                {
                    "input": api_phone,
                    "inputtype": "phonenumber",
                    "fields": "formatted_address,name,place_id,types,business_status",
                },
            )

            # Parse the findplacefromtext response
            try:
                place = parse_place_candidates_response(
                    data, expected_fields=expected_fields
                )
                if not place:
                    return None

                # Get full details
                details = self.get_place_details(place.place_id)
                if not details:
                    return None

                # Cache the full details response
                details_response = {
                    "status": "OK",
                    "result": details.model_dump(),
                }
                self._cache.put(
                    "PHONE", digits, place.place_id, details_response
                )
                return details

            except (APIError, ParseError) as e:
                logger.warning("Error parsing phone search response: %s", e)
                return None

        except (requests.exceptions.RequestException, RetryError) as e:
            logger.error("Error searching by phone: %s", e)
            return None

    def _format_phone_for_api(self, digits: str) -> str:
        """Format phone number to E.164 format for API."""
        if len(digits) == 10:
            return f"+1{digits}"  # Assume US
        return f"+{digits}"

    def _try_text_search_fallback(self, phone_number: str, digits: str) -> Place | None:
        """Fallback to text search if phone search fails."""
        text_result = self.search_by_text(phone_number)
        if not text_result or not text_result.place_id:
            return None

        # Get full details and cache
        details = self.get_place_details(text_result.place_id)
        if not details:
            return None

        # Cache with phone digits as key
        details_response = {
            "status": "OK",
            "result": details.model_dump(),
        }
        self._cache.put("PHONE", digits, text_result.place_id, details_response)
        return details

    def search_by_address(
        self,
        address: str,
        receipt_words: list[Any] | None = None,
    ) -> Place | None:
        """
        Search for a place by address.

        This method checks the DynamoDB cache first, only making
        an API call on cache miss. Returns a typed Place object with
        guaranteed fields: place_id, name, location, types.

        Args:
            address: Address to search for
            receipt_words: Optional list of words from receipt for context

        Returns:
            Typed Place object if found, None otherwise
        """
        if not address or not address.strip():
            logger.debug("Empty address, skipping search")
            return None

        address = address.strip()

        # Expected fields from findplacefromtext address search
        expected_fields = {"place_id", "name", "types", "geometry"}

        # Check cache first
        cached_dict = self._cache.get("ADDRESS", address)
        if cached_dict and cached_dict.get("result"):
            try:
                cached_place = parse_place_details_response(
                    cached_dict, expected_fields=expected_fields
                )
                return cached_place
            except (APIError, ParseError) as e:
                logger.warning(
                    "Failed to parse cached address search result: %s", e
                )
                # Fall through to API call

        # Make API call
        logger.info("🔍 Places API: search_by_address(%s)", address[:50])

        try:
            # If we have receipt words, try to extract business name
            search_input = address
            if receipt_words:
                business_name = self._extract_business_name(receipt_words)
                if business_name:
                    search_input = f"{business_name} {address}"
                    logger.debug(
                        "Using business name in search: %s", business_name
                    )

            data = self._make_request(
                "findplacefromtext/json",
                {
                    "input": search_input,
                    "inputtype": "textquery",
                    "fields": "formatted_address,name,place_id,types,geometry",
                },
            )

            try:
                place = parse_place_candidates_response(
                    data, expected_fields=expected_fields
                )
                if place:
                    # Skip route-level results
                    if place.types == ["route"]:
                        logger.info(
                            "Skipping route-level result for: %s", address[:50]
                        )
                        return None

                    # Get full details
                    details = self.get_place_details(place.place_id)
                    if details:
                        # Cache the full details response
                        details_response = {
                            "status": "OK",
                            "result": details.model_dump(),
                        }
                        self._cache.put(
                            "ADDRESS",
                            address,
                            place.place_id,
                            details_response,
                        )
                        return details
            except (APIError, ParseError) as e:
                logger.warning("Error parsing address search response: %s", e)

            return None

        except (requests.exceptions.RequestException, RetryError) as e:
            logger.error("Error searching by address: %s", e)
            return None

    def search_by_text(
        self,
        query: str,
        lat: float | None = None,
        lng: float | None = None,
    ) -> Place | None:
        """
        Search for a place using free-form text.

        NOTE: Text searches are NOT cached due to query variability.
        Returns a typed Place object with guaranteed fields: place_id, name.

        Args:
            query: Search query
            lat: Optional latitude for location bias
            lng: Optional longitude for location bias

        Returns:
            Typed Place object if found, None otherwise
        """
        if not query:
            return None

        logger.info(
            "🔍 Places API: search_by_text(%s) [NOT CACHED]", query[:50]
        )

        try:
            fields = (
                "place_id,formatted_address,name,formatted_phone_number,"
                "types,business_status"
            )
            params: dict[str, Any] = {
                "query": query,
                "fields": fields,
            }

            if lat is not None and lng is not None:
                params["location"] = f"{lat},{lng}"
                params["radius"] = 1000

            data = self._make_request("textsearch/json", params)

            try:
                places = parse_place_search_response(
                    data, expected_fields={"place_id", "name"}
                )
                return places[0] if places else None
            except (APIError, ParseError) as e:
                logger.warning("Error parsing text search response: %s", e)
                return None

        except (requests.exceptions.RequestException, RetryError) as e:
            logger.error("Error in text search: %s", e)
            return None

    def search_nearby(
        self,
        lat: float,
        lng: float,
        radius: int = 100,
        keyword: str | None = None,
    ) -> list[Place]:
        """
        Search for places near a location.

        Returns a list of typed Place objects.

        Args:
            lat: Latitude
            lng: Longitude
            radius: Search radius in meters
            keyword: Optional keyword filter

        Returns:
            List of nearby Place objects
        """
        logger.info(
            "🔍 Places API: search_nearby(%.4f, %.4f, r=%d)",
            lat,
            lng,
            radius,
        )

        try:
            params: dict[str, Any] = {
                "location": f"{lat},{lng}",
                "radius": radius,
            }

            if keyword:
                params["keyword"] = keyword

            data = self._make_request("nearbysearch/json", params)

            try:
                return parse_place_search_response(
                    data, expected_fields={"place_id", "name"}
                )
            except (APIError, ParseError) as e:
                logger.warning("Error parsing nearby search response: %s", e)
                return []

        except (requests.exceptions.RequestException, RetryError) as e:
            logger.error("Error in nearby search: %s", e)
            return []

    def get_place_details(self, place_id: str) -> Place | None:
        """
        Get detailed information about a place.

        This method does NOT use caching since place_id lookups
        are already efficient and details may change. Returns a typed
        Place object with comprehensive field validation.

        Args:
            place_id: Google Places place_id

        Returns:
            Typed Place object if found, None otherwise
        """
        if not place_id:
            return None

        logger.debug("🔍 Places API: get_place_details(%s)", place_id[:20])

        try:
            fields = [
                "name",
                "formatted_address",
                "place_id",
                "formatted_phone_number",
                "international_phone_number",
                "website",
                "geometry",
                "opening_hours",
                "rating",
                "types",
                "business_status",
                "vicinity",
                "user_ratings_total",
            ]

            expected_fields = set(fields)

            data = self._make_request(
                "details/json",
                {
                    "place_id": place_id,
                    "fields": ",".join(fields),
                },
            )

            try:
                place = parse_place_details_response(
                    data, expected_fields=expected_fields
                )
                return place
            except (APIError, ParseError) as e:
                logger.error("Error parsing place details response: %s", e)
                return None

        except (requests.exceptions.RequestException, RetryError) as e:
            logger.error("Error getting place details: %s", e)
            return None

    def autocomplete_address(self, input_text: str) -> list[dict[str, Any]]:
        """
        Get address predictions using Places Autocomplete.

        Returns parsed predictions with at least 'description' and 'place_id' fields.

        Args:
            input_text: Partial address text

        Returns:
            List of prediction dicts with 'description', 'place_id', and 'types'
        """
        if not input_text:
            return []

        try:
            data = self._make_request(
                "autocomplete/json",
                {
                    "input": input_text,
                    "types": "address",
                },
            )

            try:
                return parse_place_autocomplete_response(data)
            except (APIError, ParseError) as e:
                logger.warning("Error parsing autocomplete response: %s", e)
                return []

        except (requests.exceptions.RequestException, RetryError) as e:
            logger.error("Error in autocomplete: %s", e)
            return []

    def _extract_business_name(self, receipt_words: list[Any]) -> str | None:
        """Extract potential business name from receipt words."""
        for word in receipt_words[:10]:
            text = ""
            if isinstance(word, str):
                text = word
            elif hasattr(word, "text"):
                text = word.text
            elif isinstance(word, dict):
                text = word.get("text", "")

            # Look for all-caps text that's likely a business name
            if text and text.isupper() and not any(c.isdigit() for c in text):
                return text

        return None

    def is_area_search(self, address: str) -> bool:
        """
        Check if an address appears to be an area search (not specific).

        Args:
            address: Address to check

        Returns:
            True if this appears to be an area search
        """
        area_patterns = [
            r"^[A-Za-z\s]+,\s*[A-Za-z\s]+,\s*[A-Z]{2}(?:\s+USA)?$",
            r"^[A-Za-z\s]+,\s*[A-Z]{2}$",
            r"^[A-Za-z\s]+$",
            r"^[A-Za-z\s]+\s+[A-Z]{2}$",
        ]

        return any(
            re.match(pattern, address.strip()) for pattern in area_patterns
        )

    @property
    def cache(self) -> CacheManager:
        """Get the cache manager instance."""
        return self._cache

    def get_cache_stats(self) -> dict[str, Any]:
        """Get cache statistics."""
        return self._cache.get_stats()


def create_places_client(
    api_key: str | None = None,
    config: PlacesConfig | None = None,
    cache_manager: CacheManager | None = None,
) -> PlacesClient:
    """
    Factory function to create a Places client.

    Automatically selects between v1 and legacy API based on config.

    Args:
        api_key: Google Places API key (optional, defaults to config)
        config: Configuration object (optional, defaults to environment config)
        cache_manager: Pre-configured cache manager (optional)

    Returns:
        PlacesClientV1 if config.use_v1_api is True, else PlacesClient (legacy)

    Example:
        ```python
        from receipt_places import create_places_client

        # Uses legacy API by default
        client = create_places_client()

        # Enable v1 API via environment variable
        # RECEIPT_PLACES_USE_V1_API=true python script.py
        # Or via config:
        from receipt_places.config import PlacesConfig
        config = PlacesConfig(use_v1_api=True)
        client = create_places_client(config=config)
        ```
    """
    config = config or get_config()

    if config.use_v1_api:
        # Import here to avoid circular imports and only load v1 if needed
        from receipt_places.client_v1 import PlacesClientV1

        logger.info("Creating PlacesClientV1 (API v1)")
        return PlacesClientV1(api_key=api_key, config=config, cache_manager=cache_manager)  # type: ignore
    else:
        logger.info("Creating PlacesClient (Legacy API)")
        return PlacesClient(api_key=api_key, config=config, cache_manager=cache_manager)
