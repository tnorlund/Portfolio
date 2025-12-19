"""
Cache manager for Google Places API responses.

This module handles DynamoDB operations for caching Places API responses,
using the PlacesCache entity from receipt_dynamo.
"""

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Literal, Optional, cast

import boto3
from botocore.exceptions import ClientError

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
    """

    # Maximum field lengths for padding
    _MAX_ADDRESS_LENGTH = 400
    _MAX_PHONE_LENGTH = 30
    _MAX_URL_LENGTH = 100

    def __init__(
        self,
        table_name: Optional[str] = None,
        config: Optional[PlacesConfig] = None,
        dynamodb_client: Optional[Any] = None,
    ):
        """
        Initialize the cache manager.

        Args:
            table_name: DynamoDB table name (defaults to config)
            config: Configuration settings
            dynamodb_client: Optional pre-configured DynamoDB client
        """
        self._config = config or get_config()
        self._table_name = table_name or self._config.table_name

        if dynamodb_client is not None:
            self._client = dynamodb_client
        else:
            # Create boto3 client
            client_kwargs: dict[str, Any] = {
                "region_name": self._config.aws_region,
            }
            if self._config.endpoint_url:
                client_kwargs["endpoint_url"] = self._config.endpoint_url

            self._client = boto3.client("dynamodb", **client_kwargs)

        logger.info(
            "CacheManager initialized for table: %s", self._table_name
        )

    def _normalize_phone(self, phone: str) -> str:
        """Normalize phone number to digits only."""
        return "".join(c for c in phone if c.isdigit())

    def _normalize_address(self, address: str) -> str:
        """
        Normalize address for consistent cache lookups.

        Converts to uppercase and removes extra whitespace.
        """
        import re

        if not address:
            return ""

        # Uppercase and normalize whitespace
        normalized = " ".join(address.upper().split())

        # Common abbreviation expansions
        replacements = [
            (r"\bST\b", "STREET"),
            (r"\bAVE\b", "AVENUE"),
            (r"\bBLVD\b", "BOULEVARD"),
            (r"\bRD\b", "ROAD"),
            (r"\bDR\b", "DRIVE"),
            (r"\bLN\b", "LANE"),
            (r"\bCT\b", "COURT"),
            (r"\bPL\b", "PLACE"),
            (r"\bAPT\b", "APARTMENT"),
            (r"\bSTE\b", "SUITE"),
        ]

        for pattern, replacement in replacements:
            normalized = re.sub(pattern, replacement, normalized)

        return normalized

    def _pad_search_value(
        self,
        search_type: SearchType,
        value: str,
    ) -> tuple[str, Optional[str], Optional[str]]:
        """
        Pad the search value for consistent DynamoDB keys.

        Args:
            search_type: Type of search (ADDRESS, PHONE, URL)
            value: The search value

        Returns:
            Tuple of (padded_value, normalized_value, value_hash)
        """
        import hashlib

        value = value.strip()
        normalized_value: Optional[str] = None
        value_hash: Optional[str] = None

        if search_type == "ADDRESS":
            # Create hash of original value
            value_hash = hashlib.md5(
                value.encode(), usedforsecurity=False
            ).hexdigest()[:8]

            # Normalize address
            normalized_value = self._normalize_address(value)

            # Return padded with hash prefix
            padded = f"{value_hash}_{value:_>{self._MAX_ADDRESS_LENGTH}}"
            return padded, normalized_value, value_hash

        elif search_type == "PHONE":
            # Normalize to digits only
            digits = self._normalize_phone(value)
            padded = f"{digits:_>{self._MAX_PHONE_LENGTH}}"
            return padded, None, None

        elif search_type == "URL":
            # Lowercase and replace spaces
            clean = value.lower().replace(" ", "_")
            padded = f"{clean:_>{self._MAX_URL_LENGTH}}"
            return padded, None, None

        raise ValueError(f"Invalid search type: {search_type}")

    def _build_key(
        self,
        search_type: SearchType,
        search_value: str,
    ) -> dict[str, dict[str, str]]:
        """Build DynamoDB primary key."""
        padded, _, _ = self._pad_search_value(search_type, search_value)
        return {
            "PK": {"S": f"PLACES#{search_type}"},
            "SK": {"S": f"VALUE#{padded}"},
        }

    def get(
        self,
        search_type: SearchType,
        search_value: str,
    ) -> Optional[dict[str, Any]]:
        """
        Get cached Places API response.

        Args:
            search_type: Type of search (ADDRESS, PHONE, URL)
            search_value: The search value

        Returns:
            Cached response dict if found and valid, None otherwise
        """
        if not self._config.cache_enabled:
            logger.debug("Cache disabled, skipping lookup")
            return None

        if not search_value or not search_value.strip():
            logger.debug("Empty search value, skipping cache lookup")
            return None

        try:
            key = self._build_key(search_type, search_value)

            response = self._client.get_item(
                TableName=self._table_name,
                Key=key,
            )

            item = response.get("Item")
            if not item:
                logger.info(
                    "❌ CACHE MISS: %s - %s",
                    search_type,
                    search_value[:50],
                )
                return None

            # Check TTL
            ttl = item.get("time_to_live", {}).get("N")
            if ttl:
                if int(ttl) < int(time.time()):
                    logger.info(
                        "⏰ CACHE EXPIRED: %s - %s",
                        search_type,
                        search_value[:50],
                    )
                    return None

            # Parse response
            places_response = cast(
                dict[str, Any],
                json.loads(item["places_response"]["S"]),
            )

            # Check for invalid cached responses
            status = places_response.get("status")
            if status in ("NO_RESULTS", "INVALID"):
                logger.info(
                    "⚠️ CACHE HIT (invalid): %s - %s (status=%s)",
                    search_type,
                    search_value[:50],
                    status,
                )
                return None

            logger.info(
                "✅ CACHE HIT: %s - %s (queries=%s)",
                search_type,
                search_value[:50],
                item.get("query_count", {}).get("N", "0"),
            )

            # Increment query count asynchronously (fire and forget)
            self._increment_query_count(key)

            return places_response

        except ClientError as e:
            logger.error(
                "DynamoDB error during cache lookup: %s",
                e.response["Error"]["Message"],
            )
            return None
        except (json.JSONDecodeError, KeyError) as e:
            logger.error("Error parsing cached response: %s", e)
            return None

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
            padded, normalized, value_hash = self._pad_search_value(
                search_type, search_value
            )

            # Calculate TTL
            ttl_seconds = self._config.cache_ttl_days * 24 * 60 * 60
            expires_at = int(time.time()) + ttl_seconds

            # Build item
            item: dict[str, dict[str, Any]] = {
                "PK": {"S": f"PLACES#{search_type}"},
                "SK": {"S": f"VALUE#{padded}"},
                "GSI1PK": {"S": "PLACE_ID"},
                "GSI1SK": {"S": f"PLACE_ID#{place_id}"},
                "TYPE": {"S": "PLACES_CACHE"},
                "place_id": {"S": place_id},
                "places_response": {"S": json.dumps(places_response)},
                "last_updated": {"S": datetime.now(timezone.utc).isoformat()},
                "query_count": {"N": "0"},
                "search_type": {"S": search_type},
                "search_value": {"S": search_value},
                "time_to_live": {"N": str(expires_at)},
            }

            # Add optional fields
            if normalized:
                item["normalized_value"] = {"S": normalized}
            if value_hash:
                item["value_hash"] = {"S": value_hash}

            self._client.put_item(
                TableName=self._table_name,
                Item=item,
                ConditionExpression="attribute_not_exists(PK)",
            )

            logger.info(
                "💾 CACHED: %s - %s (place_id=%s)",
                search_type,
                search_value[:50],
                place_id[:20] if place_id else "N/A",
            )
            return True

        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            if error_code == "ConditionalCheckFailedException":
                # Item already exists, that's fine
                logger.debug("Cache entry already exists")
                return True
            logger.error(
                "DynamoDB error during cache write: %s",
                e.response["Error"]["Message"],
            )
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
        import re

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

    def _increment_query_count(self, key: dict[str, dict[str, str]]) -> None:
        """Increment the query count for a cache entry."""
        try:
            self._client.update_item(
                TableName=self._table_name,
                Key=key,
                UpdateExpression=(
                    "SET query_count = if_not_exists(query_count, :zero) + :inc, "
                    "last_updated = :now"
                ),
                ExpressionAttributeValues={
                    ":inc": {"N": "1"},
                    ":zero": {"N": "0"},
                    ":now": {"S": datetime.now(timezone.utc).isoformat()},
                },
            )
        except ClientError:
            # Non-critical, don't fail the request
            pass

    def delete(
        self,
        search_type: SearchType,
        search_value: str,
    ) -> bool:
        """Delete a cache entry."""
        try:
            key = self._build_key(search_type, search_value)
            self._client.delete_item(
                TableName=self._table_name,
                Key=key,
            )
            logger.info(
                "🗑️ DELETED: %s - %s",
                search_type,
                search_value[:50],
            )
            return True
        except ClientError as e:
            logger.error("Error deleting cache entry: %s", e)
            return False

    def get_by_place_id(self, place_id: str) -> Optional[dict[str, Any]]:
        """
        Get cached response by Google Place ID.

        Uses GSI1 for efficient lookup.
        """
        try:
            response = self._client.query(
                TableName=self._table_name,
                IndexName="GSI1",
                KeyConditionExpression="GSI1PK = :pk AND GSI1SK = :sk",
                ExpressionAttributeValues={
                    ":pk": {"S": "PLACE_ID"},
                    ":sk": {"S": f"PLACE_ID#{place_id}"},
                },
                Limit=1,
            )

            items = response.get("Items", [])
            if not items:
                return None

            return cast(
                dict[str, Any],
                json.loads(items[0]["places_response"]["S"]),
            )

        except ClientError as e:
            logger.error("Error querying by place_id: %s", e)
            return None

    def get_stats(self) -> dict[str, Any]:
        """
        Get cache statistics.

        Returns count of entries by search type.
        """
        stats: dict[str, int] = {
            "ADDRESS": 0,
            "PHONE": 0,
            "URL": 0,
        }

        for search_type in stats:
            try:
                response = self._client.query(
                    TableName=self._table_name,
                    KeyConditionExpression="PK = :pk",
                    ExpressionAttributeValues={
                        ":pk": {"S": f"PLACES#{search_type}"},
                    },
                    Select="COUNT",
                )
                stats[search_type] = response.get("Count", 0)
            except ClientError:
                pass

        return {
            "entries_by_type": stats,
            "total_entries": sum(stats.values()),
            "cache_enabled": self._config.cache_enabled,
            "ttl_days": self._config.cache_ttl_days,
        }

