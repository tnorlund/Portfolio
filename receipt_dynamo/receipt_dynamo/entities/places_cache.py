import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Generator, Literal

from receipt_dynamo.entities.util import normalize_address

SearchTypes = Literal["ADDRESS", "PHONE", "URL"]


@dataclass(eq=True, unsafe_hash=False)
class PlacesCache:
    """
    Represents a cached Google Places API response stored in DynamoDB.

    This class caches responses from the Google Places API to minimize API
    calls and costs. The cache is keyed by the type of search (address, phone,
    URL) and the search value.

    Attributes:
        search_type (str): Type of search performed (ADDRESS, PHONE, URL)
        search_value (str): The value used for the search
        place_id (str): The Google Places place_id
        places_response (Dict): The response from the Google Places API
        last_updated (str): ISO format timestamp of last update
        query_count (int): Number of times this cache entry has been accessed
        normalized_value (str): Normalized version of the search value (for
            addresses)
        value_hash (str): Hash of the original search value (for addresses)
    """

    REQUIRED_KEYS = {
        "PK",
        "SK",
        "TYPE",
        "GSI1PK",
        "GSI1SK",
        "search_type",
        "place_id",
        "places_response",
        "last_updated",
        "query_count",
    }

    # Maximum field lengths based on Google Places API
    _MAX_ADDRESS_LENGTH = 400  # Allow for future growth beyond current 315 max
    _MAX_PHONE_LENGTH = 30  # International format with extra padding
    _MAX_URL_LENGTH = 100  # Standard URLs with some padding

    search_type: SearchTypes
    search_value: str
    place_id: str
    places_response: dict[str, Any]
    last_updated: str
    query_count: int = 0
    normalized_value: str | None = None
    value_hash: str | None = None
    time_to_live: int | None = None

    def __post_init__(self):
        """
        Validate and process the dataclass fields after initialization.

        Raises:
            ValueError: If any of the parameters are invalid
        """
        self._validate_fields()
        self.places_response = deepcopy(self.places_response)
        if self.search_type == "ADDRESS":
            value = self.search_value.strip()
            self.value_hash = hashlib.md5(
                value.encode(), usedforsecurity=False
            ).hexdigest()[:8]
            self.normalized_value = normalize_address(value)

    def _validate_fields(self) -> None:
        """Validate fields at construction and before persistence."""
        # Validate search_type
        if self.search_type not in ["ADDRESS", "PHONE", "URL"]:
            raise ValueError("search_type must be one of: ADDRESS, PHONE, URL")

        # Validate search_value
        if (
            not isinstance(self.search_value, str)
            or not self.search_value.strip()
        ):
            raise ValueError("search_value cannot be empty")

        # Validate place_id
        if not isinstance(self.place_id, str) or not self.place_id:
            raise ValueError("place_id cannot be empty")

        # Validate places_response
        if not isinstance(self.places_response, dict):
            raise ValueError("places_response must be a dictionary")
        try:
            json.dumps(self.places_response, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "places_response must be JSON serializable"
            ) from exc

        # Validate last_updated
        try:
            datetime.fromisoformat(self.last_updated)
        except (ValueError, TypeError) as e:
            raise ValueError(
                "last_updated must be a valid ISO format timestamp"
            ) from e

        # Validate query_count
        if (
            isinstance(self.query_count, bool)
            or not isinstance(self.query_count, int)
            or self.query_count < 0
        ):
            raise ValueError("query_count must be non-negative")

        # Validate time_to_live
        if self.time_to_live is not None:
            if (
                isinstance(self.time_to_live, bool)
                or not isinstance(self.time_to_live, int)
                or self.time_to_live < 0
            ):
                raise ValueError("time_to_live must be non-negative")

        for field_name in ("normalized_value", "value_hash"):
            value = getattr(self, field_name)
            if value is not None and not isinstance(value, str):
                raise ValueError(f"{field_name} must be a string or None")

    def _pad_search_value(self, value: str) -> str:
        """Pad the search value to a fixed length.

        Args:
            value: Value to pad (original OCR text)

        Returns:
            Padded value with appropriate length and hash
        """
        value = value.strip()

        if self.search_type == "ADDRESS":
            if len(value) > self._MAX_ADDRESS_LENGTH:
                raise ValueError("ADDRESS search_value is too long")
            # Create a hash of the original OCR text
            value_hash = hashlib.md5(
                value.encode(), usedforsecurity=False
            ).hexdigest()[:8]
            # Store hash
            self.value_hash = value_hash

            # Normalize the address for storage
            normalized = normalize_address(value)
            # Store normalized value
            self.normalized_value = normalized

            # Return padded original value with hash
            return f"{value_hash}_{value:_>{self._MAX_ADDRESS_LENGTH}}"
        if self.search_type == "PHONE":
            self.normalized_value = None
            self.value_hash = None
            # Keep only digits and basic formatting characters
            value = "".join(c for c in value if c.isdigit() or c in "()+-")
            if not value or len(value) > self._MAX_PHONE_LENGTH:
                raise ValueError("PHONE search_value is empty or too long")
            return f"{value:_>{self._MAX_PHONE_LENGTH}}"
        if self.search_type == "URL":
            self.normalized_value = None
            self.value_hash = None
            # Replace spaces with underscores and lowercase
            value = value.lower().replace(" ", "_")
            if len(value) > self._MAX_URL_LENGTH:
                raise ValueError("URL search_value is too long")
            return f"{value:_>{self._MAX_URL_LENGTH}}"
        raise ValueError(f"Invalid search type: {self.search_type}")

    @property
    def key(self) -> dict[str, dict[str, str]]:
        """
        Generate the primary key for DynamoDB.

        Returns:
            Dict: The primary key attributes
        """
        padded_value = self._pad_search_value(self.search_value)
        return {
            "PK": {"S": f"PLACES#{self.search_type}"},
            "SK": {"S": f"VALUE#{padded_value}"},
        }

    def gsi1_key(self) -> dict[str, dict[str, Any]]:
        """
        Generate the GSI1 key for DynamoDB.

        Returns:
            Dict: The GSI1 key attributes
        """
        return {
            "GSI1PK": {"S": "PLACE_ID"},
            "GSI1SK": {"S": f"PLACE_ID#{self.place_id}"},
        }

    def to_item(self) -> dict[str, dict[str, Any]]:
        """
        Convert to a DynamoDB item format.
        Includes all necessary attributes for the base table and GSIs:
        - Base table: PK (PLACES#<search_type>), SK
            (VALUE#<padded_search_value>)
        - GSI1: GSI1PK (PLACE_ID), GSI1SK (PLACE_ID#<place_id>) - For place_id
            lookups

        Returns:
            Dict: The DynamoDB item representation with all required attributes
        """
        self._validate_fields()
        key = self.key
        item = {
            **key,  # Base table keys (PK, SK)
            **self.gsi1_key(),  # GSI1 keys
            "TYPE": {"S": "PLACES_CACHE"},
            # Item attributes
            "place_id": {"S": self.place_id},
            "places_response": {
                "S": json.dumps(self.places_response, allow_nan=False)
            },
            "last_updated": {"S": self.last_updated},
            "query_count": {"N": str(self.query_count)},
            "search_type": {"S": self.search_type},
            "search_value": {"S": self.search_value},
        }

        # Add normalized value and hash if they exist
        if self.normalized_value:
            item["normalized_value"] = {"S": self.normalized_value}
        if self.value_hash:
            item["value_hash"] = {"S": self.value_hash}
        # Include time_to_live only when it’s set
        if self.time_to_live is not None:
            item["time_to_live"] = {"N": str(self.time_to_live)}

        return item

    def __iter__(self) -> Generator[tuple[str, Any], None, None]:
        """
        Returns an iterator over the PlacesCache object's attributes.

        Returns:
            Generator[tuple[str, Any], None, None]: An iterator over the
                PlacesCache object's attribute name/value pairs.
        """
        yield "search_type", self.search_type
        yield "search_value", self.search_value
        yield "place_id", self.place_id
        yield "places_response", self.places_response
        yield "last_updated", self.last_updated
        yield "query_count", self.query_count
        if self.normalized_value:
            yield "normalized_value", self.normalized_value
        if self.value_hash:
            yield "value_hash", self.value_hash
        if self.time_to_live is not None:
            yield "time_to_live", self.time_to_live

    def __repr__(self) -> str:
        """
        Get string representation of the PlacesCache entry.

        Returns:
            str: String representation
        """
        base = (
            f"PlacesCache(search_type='{self.search_type}', "
            f"search_value='{self.search_value}', "
            f"place_id='{self.place_id}', "
            f"query_count={self.query_count}"
        )
        if self.normalized_value:
            base += f", normalized_value='{self.normalized_value}'"
        if self.value_hash:
            base += f", value_hash='{self.value_hash}'"
        if self.time_to_live is not None:
            base += f", time_to_live={self.time_to_live}"
        return base + ")"

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> "PlacesCache":
        """Converts a DynamoDB item to a PlacesCache object.

        Args:
            item: The DynamoDB item to convert.

        Returns:
            PlacesCache: The PlacesCache object.

        Raises:
            ValueError: When the item format is invalid.
        """
        if not all(key in item for key in cls.REQUIRED_KEYS):
            raise ValueError("Item is missing required keys")

        try:
            if item["TYPE"].get("S") != "PLACES_CACHE":
                raise ValueError("Invalid PlacesCache TYPE")
            places_response = json.loads(item["places_response"]["S"])
            search_type = item["search_type"]["S"]

            # Try to get the original search_value first
            if "search_value" in item and "S" in item["search_value"]:
                search_value = item["search_value"]["S"]
            else:
                # Fall back to extracting from SK if search_value is missing
                padded_value = item["SK"]["S"].split("#", 1)[1]
                if search_type == "ADDRESS":
                    # Address keys are <8-char-hash>_<left-padded-original>.
                    search_value = padded_value[9:].lstrip("_")
                elif search_type == "PHONE":
                    search_value = padded_value.lstrip("_")
                    search_value = "".join(
                        c for c in search_value if c.isdigit() or c in "()+-"
                    )
                elif search_type == "URL":
                    search_value = padded_value.lstrip("_")
                else:
                    raise ValueError(f"Invalid search type: {search_type}")

            # Extract normalized value and hash if they exist
            normalized_value = None
            value_hash = None

            if "normalized_value" in item and "S" in item["normalized_value"]:
                normalized_value = item["normalized_value"]["S"]
            elif search_type == "ADDRESS":
                parts = item["SK"]["S"].split("#", 1)[1].split("_")
                if len(parts) >= 2:
                    value_hash = parts[0]

            if "value_hash" in item and "S" in item["value_hash"]:
                value_hash = item["value_hash"]["S"]

            if "time_to_live" in item and "N" in item["time_to_live"]:
                time_to_live = int(item["time_to_live"]["N"])
            else:
                time_to_live = None

            place_id = item["place_id"]["S"]
            last_updated = item["last_updated"]["S"]
            query_count = int(item["query_count"]["N"])

            cache = cls(
                search_type=search_type,
                search_value=search_value,
                place_id=place_id,
                places_response=places_response,
                last_updated=last_updated,
                query_count=query_count,
                normalized_value=normalized_value,
                value_hash=value_hash,
                time_to_live=time_to_live,
            )
            expected = cache.to_item()
            for key in ("PK", "SK", "TYPE", "GSI1PK", "GSI1SK"):
                if item.get(key) != expected.get(key):
                    raise ValueError("Invalid PlacesCache keys")
            return cache
        except (json.JSONDecodeError, ValueError, KeyError) as e:
            raise ValueError(
                f"Error converting item to PlacesCache: {str(e)}"
            ) from e


def item_to_places_cache(item: dict[str, Any]) -> "PlacesCache":
    """Converts a DynamoDB item to a PlacesCache object.

    Args:
        item (dict): The DynamoDB item to convert.

    Returns:
        PlacesCache: The PlacesCache object.

    Raises:
        ValueError: When the item format is invalid.
    """
    return PlacesCache.from_item(item)
