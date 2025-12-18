"""
Cache manager for Google Places API responses.

This module handles DynamoDB operations for caching Places API responses,
using the PlacesCache entity and DynamoClient from receipt_dynamo.
"""

import logging
import re
import time
from datetime import UTC, datetime
from typing import Any, Literal

from receipt_dynamo import DynamoClient, PlacesCache
from receipt_dynamo.data.shared_exceptions import OperationError, ReceiptDynamoError
from receipt_places.config import PlacesConfig, get_config

logger = logging.getLogger(__name__)

SearchType = Literal["ADDRESS", "PHONE", "URL"]


class CacheManager:
    """
    Manages Google Places API response caching in DynamoDB.

    Uses the PlacesCache table schema:
    - PK: PLACES#<search_type>
    - SK: VALUE#<padded_value>
    - GSI1: PLACE_ID -> PLACE_ID#<place_id>

    Features:
    - Automatic cache lookup before API calls
    - TTL-based expiration (30 days default)
    - Query count tracking for analytics
    - Smart cache exclusions (area searches, route-level results)

    Known Limitation (Issue #4):
    - Address cache keys are built from raw input (with hash), not normalized form
    - This means "123 Main St" and "123 MAIN ST" create separate cache entries
    - Future enhancement: Migrate to normalized keys with backward compatibility layer
    - Would require: Cache key format change + lookup fallback for old entries
    """

    def __init__(
        self,
        table_name: str | None = None,
        config: PlacesConfig | None = None,
        dynamodb_client: Any | None = None,
    ):
        """
        Initialize the cache manager.

        Args:
            table_name: DynamoDB table name (defaults to config)
            config: Configuration settings
            dynamodb_client: Optional pre-configured DynamoClient instance
        """
        self._config = config or get_config()
        self._table_name = table_name or self._config.table_name

        if dynamodb_client is not None:
            self._client = dynamodb_client
        else:
            # Create DynamoClient with proper config
            self._client = DynamoClient(
                table_name=self._table_name,
                region=self._config.aws_region,
            )

        logger.info("CacheManager initialized for table: %s", self._table_name)

    def _build_key(
        self, search_type: SearchType, search_value: str
    ) -> dict[str, dict[str, str]]:
        """Build DynamoDB primary key using PlacesCache key format."""
        temp_cache = PlacesCache(
            search_type=search_type,  # type: ignore[arg-type]
            search_value=search_value,
            place_id="temp",
            places_response={},
            last_updated="2021-01-01T00:00:00",
        )
        return temp_cache.key

    def get(
        self,
        search_type: SearchType,
        search_value: str,
    ) -> dict[str, Any] | None:
        """
        Get cached Places API response.

        Args:
            search_type: Type of search (ADDRESS, PHONE, URL)
            search_value: The search value

        Returns:
            Cached response dict if found and valid, None otherwise
        """
        # Skip cache if not enabled or invalid input
        if not self._should_use_cache(search_value):
            return None

        try:
            # Try to retrieve cache item
            cache_item = self._try_get_cache_item(search_type, search_value)
            if cache_item is None:
                return None

            # Validate cache: TTL and response status
            if not self._is_cache_valid(cache_item, search_type, search_value):
                return None

            # Cache is valid - log hit and increment counter
            logger.info(
                "✅ CACHE HIT: %s - %s (queries=%s)",
                search_type,
                search_value[:50],
                cache_item.query_count,
            )
            self._increment_query_count(cache_item)

            return cache_item.places_response

        except OperationError as e:
            logger.error("DynamoDB error during cache lookup: %s", e)
            return None
        except ReceiptDynamoError as e:
            logger.error("Unexpected DynamoDB error during cache lookup: %s", e)
            return None

    def _should_use_cache(self, search_value: str) -> bool:
        """Check if cache should be used for this request."""
        if not self._config.cache_enabled:
            logger.debug("Cache disabled, skipping lookup")
            return False

        if not search_value or not search_value.strip():
            logger.debug("Empty search value, skipping cache lookup")
            return False

        return True

    def _try_get_cache_item(
        self, search_type: SearchType, search_value: str
    ) -> Any:
        """Try to retrieve cache item from DynamoDB."""
        cache_item = self._client.get_places_cache(search_type, search_value)

        if cache_item is None:
            logger.info(
                "❌ CACHE MISS: %s - %s",
                search_type,
                search_value[:50],
            )
            return None

        return cache_item

    def _is_cache_valid(
        self, cache_item: Any, search_type: SearchType, search_value: str
    ) -> bool:
        """Check if cache item is valid (TTL and response status)."""
        # Check TTL
        if cache_item.time_to_live and int(cache_item.time_to_live) < int(
            time.time()
        ):
            logger.info(
                "⏰ CACHE EXPIRED: %s - %s",
                search_type,
                search_value[:50],
            )
            return False

        # Check for invalid cached responses (Issue #2 - no results sentinel)
        status = cache_item.places_response.get("status")
        if status in ("NO_RESULTS", "INVALID"):
            logger.info(
                "⚠️ CACHE HIT (invalid): %s - %s (status=%s)",
                search_type,
                search_value[:50],
                status,
            )
            return False

        return True

    def put(
        self,
        search_type: SearchType,
        search_value: str,
        place_id: str,
        places_response: dict[str, Any],
    ) -> bool:
        """
        Cache a Places API response.

        Args:
            search_type: Type of search
            search_value: The search value
            place_id: Google Places place_id
            places_response: The API response to cache

        Returns:
            True if cached successfully, False otherwise
        """
        if not self._config.cache_enabled:
            logger.debug("Cache disabled, skipping write")
            return False

        if not search_value or not search_value.strip():
            logger.debug("Empty search value, skipping cache write")
            return False

        # Skip caching certain responses
        if not self._should_cache(search_type, search_value, places_response):
            return False

        try:
            # Calculate TTL
            ttl_seconds = self._config.cache_ttl_days * 24 * 60 * 60
            expires_at = int(time.time()) + ttl_seconds

            # Create PlacesCache entity - PlacesCache handles padding and normalization
            cache_item = PlacesCache(
                search_type=search_type,  # type: ignore[arg-type]
                search_value=search_value,
                place_id=place_id,
                places_response=places_response,
                last_updated=datetime.now(UTC).isoformat(),
                time_to_live=expires_at,
            )

            # Use new put_places_cache() method - allows refreshing stale entries
            # (Issue #1 fix)
            self._client.put_places_cache(cache_item)

            logger.info(
                "💾 CACHED: %s - %s (place_id=%s)",
                search_type,
                search_value[:50],
                place_id[:20] if place_id else "N/A",
            )
            return True

        except OperationError as e:
            logger.error("DynamoDB error during cache write: %s", e)
            return False
        except ReceiptDynamoError as e:
            logger.error("Unexpected DynamoDB error during cache write: %s", e)
            return False

    def _should_cache(
        self,
        search_type: SearchType,
        search_value: str,
        places_response: dict[str, Any],
    ) -> bool:
        """
        Determine if a response should be cached.

        Excludes:
        - Area searches (city/state only)
        - Route-level results (street without building number)
        - Results without street numbers
        """
        if search_type == "ADDRESS":
            # Skip area searches
            area_patterns = [
                r"^[A-Za-z\s]+,\s*[A-Za-z\s]+,\s*[A-Z]{2}(?:\s+USA)?$",
                r"^[A-Za-z\s]+,\s*[A-Z]{2}$",
                r"^[A-Za-z\s]+$",
            ]
            for pattern in area_patterns:
                if re.match(pattern, search_value.strip()):
                    logger.info(
                        "⏭️ SKIP CACHE (area search): %s",
                        search_value[:50],
                    )
                    return False

            # Skip route-level results
            if places_response.get("types") == ["route"]:
                logger.info(
                    "⏭️ SKIP CACHE (route-level): %s",
                    search_value[:50],
                )
                return False

            # Skip results without street number
            formatted = places_response.get("formatted_address", "")
            if formatted and not re.match(r"^\d+", formatted):
                logger.info(
                    "⏭️ SKIP CACHE (no street number): %s",
                    search_value[:50],
                )
                return False

        return True

    def _increment_query_count(self, cache_item: PlacesCache) -> None:
        """Increment the query count for a cache entry."""
        # Non-critical, don't fail the request
        try:
            self._client.increment_query_count(cache_item)
        except (OperationError, ReceiptDynamoError) as e:
            logger.debug("Failed to increment query count (non-critical): %s", e)

    def delete(
        self,
        search_type: SearchType,
        search_value: str,
    ) -> bool:
        """Delete a cache entry."""
        try:
            # Create a temporary PlacesCache object just to get the key
            temp_cache = PlacesCache(
                search_type=search_type,  # type: ignore[arg-type]
                search_value=search_value,
                place_id="temp",
                places_response={},
                last_updated="2021-01-01T00:00:00",
            )
            self._client.delete_places_cache(temp_cache)
            logger.info(
                "🗑️ DELETED: %s - %s",
                search_type,
                search_value[:50],
            )
            return True
        except OperationError as e:
            logger.error("Error deleting cache entry: %s", e)
            return False
        except ReceiptDynamoError as e:
            logger.error("Unexpected DynamoDB error deleting cache entry: %s", e)
            return False

    def get_by_place_id(self, place_id: str) -> dict[str, Any] | None:
        """
        Get cached response by Google Place ID.

        Uses GSI1 for efficient lookup.
        """
        try:
            cache_item = self._client.get_places_cache_by_place_id(place_id)
            if cache_item is None:
                return None
            return cache_item.places_response
        except OperationError as e:
            logger.error("Error querying by place_id: %s", e)
            return None
        except ReceiptDynamoError as e:
            logger.error("Unexpected DynamoDB error querying by place_id: %s", e)
            return None

    def get_stats(self) -> dict[str, Any]:
        """
        Get cache statistics.

        Returns count of entries by search type (sampled up to 1000 items).

        NOTE: For large caches, this returns sampled results and includes
        a "sampled" flag. For accurate total counts, use CloudWatch metrics
        or DynamoDB Capacity Insights.
        """
        try:
            # list_places_caches returns items from GSI2 (LAST_USED index)
            # We sample up to 1000 items to count by search_type
            stats: dict[str, int] = {
                "ADDRESS": 0,
                "PHONE": 0,
                "URL": 0,
            }

            caches, _ = self._client.list_places_caches(limit=1000)
            for cache in caches:
                if cache.search_type in stats:
                    stats[cache.search_type] += 1

            return {
                "entries_by_type": stats,
                "total_entries": sum(stats.values()),
                "sampled": True,  # Data is sampled up to 1000 items
                "cache_enabled": self._config.cache_enabled,
                "ttl_days": self._config.cache_ttl_days,
            }
        except ReceiptDynamoError as e:
            logger.warning("Error getting cache stats: %s", e)
            return {
                "entries_by_type": {"ADDRESS": 0, "PHONE": 0, "URL": 0},
                "total_entries": 0,
                "sampled": True,
                "cache_enabled": self._config.cache_enabled,
                "ttl_days": self._config.cache_ttl_days,
                "error": str(e),
            }
