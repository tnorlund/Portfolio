"""
Google Places API client with automatic DynamoDB caching.

This module provides a clean interface to the Google Places API
with built-in caching to minimize API costs.
"""

import logging
import re
from typing import Any, Optional

import requests
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from receipt_places.cache import CacheManager, SearchType
from receipt_places.config import PlacesConfig, get_config

logger = logging.getLogger(__name__)


class PlacesAPIError(Exception):
    """Exception raised for Places API errors."""

    def __init__(self, message: str, status: Optional[str] = None):
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
        api_key: Optional[str] = None,
        config: Optional[PlacesConfig] = None,
        cache_manager: Optional[CacheManager] = None,
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
        """Make an HTTP request to the Places API."""
        url = f"{self.BASE_URL}/{endpoint}"
        params["key"] = self._api_key

        response = requests.get(url, params=params, timeout=self._timeout)
        response.raise_for_status()

        data = response.json()
        status = data.get("status", "UNKNOWN")

        if status not in ("OK", "ZERO_RESULTS"):
            error_msg = data.get("error_message", f"API returned status: {status}")
            logger.warning("Places API error: %s (status=%s)", error_msg, status)

        return data

    def search_by_phone(self, phone_number: str) -> Optional[dict[str, Any]]:
        """
        Search for a place by phone number.

        This method checks the DynamoDB cache first, only making
        an API call on cache miss.

        Args:
            phone_number: Phone number to search for

        Returns:
            Place details dict if found, None otherwise
        """
        if not phone_number:
            logger.debug("Empty phone number, skipping search")
            return None

        # Normalize to digits only for cache lookup
        digits = "".join(c for c in phone_number if c.isdigit())

        if len(digits) < 7:
            logger.debug("Phone number too short: %s", phone_number)
            return None

        # Check cache first
        cached = self._cache.get("PHONE", digits)
        if cached and cached.get("place_id"):
            return cached

        # Make API call
        logger.info("🔍 Places API: search_by_phone(%s)", phone_number)

        # Format for API (E.164 format)
        if len(digits) == 10:
            api_phone = f"+1{digits}"  # Assume US
        elif len(digits) == 11 and digits.startswith("1"):
            api_phone = f"+{digits}"
        else:
            api_phone = f"+{digits}"

        try:
            data = self._make_request(
                "findplacefromtext/json",
                {
                    "input": api_phone,
                    "inputtype": "phonenumber",
                    "fields": "formatted_address,name,place_id,types,business_status",
                },
            )

            if data.get("status") == "OK" and data.get("candidates"):
                place_id = data["candidates"][0]["place_id"]

                # Get full details
                details = self.get_place_details(place_id)
                if details:
                    # Cache the result
                    self._cache.put("PHONE", digits, place_id, details)
                    return details

            # Try text search as fallback
            text_result = self.search_by_text(phone_number)
            if text_result and text_result.get("place_id"):
                # Get full details and cache
                details = self.get_place_details(text_result["place_id"])
                if details:
                    self._cache.put("PHONE", digits, details["place_id"], details)
                    return details

            # Cache the negative result to prevent repeated API calls
            self._cache.put(
                "PHONE",
                digits,
                "NO_RESULTS",
                {"status": "NO_RESULTS", "message": "No results found"},
            )
            return None

        except requests.exceptions.RequestException as e:
            logger.error("Error searching by phone: %s", e)
            return None

    def search_by_address(
        self,
        address: str,
        receipt_words: Optional[list[Any]] = None,
    ) -> Optional[dict[str, Any]]:
        """
        Search for a place by address.

        This method checks the DynamoDB cache first, only making
        an API call on cache miss.

        Args:
            address: Address to search for
            receipt_words: Optional list of words from receipt for context

        Returns:
            Place details dict if found, None otherwise
        """
        if not address or not address.strip():
            logger.debug("Empty address, skipping search")
            return None

        address = address.strip()

        # Check cache first
        cached = self._cache.get("ADDRESS", address)
        if cached and cached.get("place_id"):
            return cached

        # Make API call
        logger.info("🔍 Places API: search_by_address(%s)", address[:50])

        try:
            # If we have receipt words, try to extract business name
            search_input = address
            if receipt_words:
                business_name = self._extract_business_name(receipt_words)
                if business_name:
                    search_input = f"{business_name} {address}"
                    logger.debug("Using business name in search: %s", business_name)

            data = self._make_request(
                "findplacefromtext/json",
                {
                    "input": search_input,
                    "inputtype": "textquery",
                    "fields": "formatted_address,name,place_id,types,geometry",
                },
            )

            if data.get("status") == "OK" and data.get("candidates"):
                place = data["candidates"][0]

                # Skip route-level results
                if place.get("types") == ["route"]:
                    logger.info("Skipping route-level result for: %s", address[:50])
                    return None

                # Get full details
                details = self.get_place_details(place["place_id"])
                if details:
                    self._cache.put("ADDRESS", address, place["place_id"], details)
                    return details

            return None

        except requests.exceptions.RequestException as e:
            logger.error("Error searching by address: %s", e)
            return None

    def search_by_text(
        self,
        query: str,
        lat: Optional[float] = None,
        lng: Optional[float] = None,
    ) -> Optional[dict[str, Any]]:
        """
        Search for a place using free-form text.

        NOTE: Text searches are NOT cached due to query variability.

        Args:
            query: Search query
            lat: Optional latitude for location bias
            lng: Optional longitude for location bias

        Returns:
            Place details dict if found, None otherwise
        """
        if not query:
            return None

        logger.info("🔍 Places API: search_by_text(%s) [NOT CACHED]", query[:50])

        try:
            params: dict[str, Any] = {
                "query": query,
                "fields": "place_id,formatted_address,name,formatted_phone_number,types,business_status",
            }

            if lat is not None and lng is not None:
                params["location"] = f"{lat},{lng}"
                params["radius"] = 1000

            data = self._make_request("textsearch/json", params)

            if data.get("status") == "OK" and data.get("results"):
                return data["results"][0]

            return None

        except requests.exceptions.RequestException as e:
            logger.error("Error in text search: %s", e)
            return None

    def search_nearby(
        self,
        lat: float,
        lng: float,
        radius: int = 100,
        keyword: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """
        Search for places near a location.

        Args:
            lat: Latitude
            lng: Longitude
            radius: Search radius in meters
            keyword: Optional keyword filter

        Returns:
            List of nearby places
        """
        logger.info(
            "🔍 Places API: search_nearby(%.4f, %.4f, r=%d)",
            lat, lng, radius,
        )

        try:
            params: dict[str, Any] = {
                "location": f"{lat},{lng}",
                "radius": radius,
            }

            if keyword:
                params["keyword"] = keyword

            data = self._make_request("nearbysearch/json", params)

            if data.get("status") == "OK":
                return data.get("results", [])

            return []

        except requests.exceptions.RequestException as e:
            logger.error("Error in nearby search: %s", e)
            return []

    def get_place_details(self, place_id: str) -> Optional[dict[str, Any]]:
        """
        Get detailed information about a place.

        This method does NOT use caching since place_id lookups
        are already efficient and details may change.

        Args:
            place_id: Google Places place_id

        Returns:
            Place details dict if found, None otherwise
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
                "price_level",
                "types",
                "business_status",
                "vicinity",
                "user_ratings_total",
            ]

            data = self._make_request(
                "details/json",
                {
                    "place_id": place_id,
                    "fields": ",".join(fields),
                },
            )

            if data.get("status") == "OK":
                return data.get("result")

            return None

        except requests.exceptions.RequestException as e:
            logger.error("Error getting place details: %s", e)
            return None

    def autocomplete_address(self, input_text: str) -> Optional[dict[str, Any]]:
        """
        Get address predictions using Places Autocomplete.

        Args:
            input_text: Partial address text

        Returns:
            Best prediction if found, None otherwise
        """
        if not input_text:
            return None

        try:
            data = self._make_request(
                "autocomplete/json",
                {
                    "input": input_text,
                    "types": "address",
                },
            )

            if data.get("status") == "OK" and data.get("predictions"):
                return data["predictions"][0]

            return None

        except requests.exceptions.RequestException as e:
            logger.error("Error in autocomplete: %s", e)
            return None

    def _extract_business_name(self, receipt_words: list[Any]) -> Optional[str]:
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
            re.match(pattern, address.strip())
            for pattern in area_patterns
        )

    @property
    def cache(self) -> CacheManager:
        """Get the cache manager instance."""
        return self._cache

    def get_cache_stats(self) -> dict[str, Any]:
        """Get cache statistics."""
        return self._cache.get_stats()

