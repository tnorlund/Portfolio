import json
import time
from datetime import datetime
from typing import Any, Dict, Generator, Literal, Optional, Tuple

from receipt_dynamo.entities.util import normalize_address

SEARCH_TYPES = Literal["ADDRESS", "PHONE", "URL"]


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

    # Maximum field lengths based on Google Places API
    _MAX_ADDRESS_LENGTH = 400  # Allow for future growth beyond current 315 max
    _MAX_PHONE_LENGTH = 30  # International format with extra padding
    _MAX_URL_LENGTH = 100  # Standard URLs with some padding

    def __init__(
        self,
        search_type: SEARCH_TYPES,
        search_value: str,
        place_id: str,
        places_response: Dict[str, Any],
        last_updated: str,
        query_count: int = 0,
        normalized_value: Optional[str] = None,
        value_hash: Optional[str] = None,
        time_to_live: Optional[int] = None,
    ):
        """
        Initialize a new PlacesCache entry.

        Args:
            search_type (str): Type of search performed (ADDRESS, PHONE, URL)
            search_value (str): The value used for the search
            place_id (str): The Google Places place_id
            places_response (Dict): The response from the Google Places API
            last_updated (str): ISO format timestamp of last update
            query_count (int, optional): Times this cache entry was accessed.
                Defaults to 0.
            normalized_value (str, optional): Normalized version of the search
                value. Defaults to None.
            value_hash (str, optional): Hash of the original search value.
                Defaults to None.
            time_to_live (int, optional): Time to live for the cache entry.
                Defaults to None.
        Raises:
            ValueError: If any of the parameters are invalid
        """
        # Validate search_type
        if search_type not in ["ADDRESS", "PHONE", "URL"]:
            raise ValueError(
                f"search_type must be one of: ADDRESS, PHONE, URL"
            )
        self.search_type = search_type

        # Validate search_value
        if not search_value or not isinstance(search_value, str):
            raise ValueError("search_value cannot be empty")
        self.search_value = search_value

        # Validate place_id
        if not place_id or not isinstance(place_id, str):
            raise ValueError("place_id cannot be empty")
        self.place_id = place_id

        # Validate places_response
        if not isinstance(places_response, dict):
            raise ValueError("places_response must be a dictionary")
        self.places_response = places_response

        # Validate last_updated
        try:
            datetime.fromisoformat(last_updated)
        except (ValueError, TypeError) as e:
            raise ValueError(
                "last_updated must be a valid ISO format timestamp"
            ) from e
        self.last_updated = last_updated

        # Validate query_count
        if not isinstance(query_count, int) or query_count < 0:
            raise ValueError("query_count must be non-negative")
        self.query_count: int = query_count

        # Store normalized value and hash if provided
        self.normalized_value: Optional[str] = normalized_value
        self.value_hash = value_hash
        self.time_to_live: Optional[int]
        if time_to_live is not None:
            if not isinstance(time_to_live, int) or time_to_live < 0:
                raise ValueError("time_to_live must be non-negative")
            now = int(time.time())
            if time_to_live < now:
                raise ValueError("time_to_live must be in the future")
            self.time_to_live = time_to_live
        else:
            self.time_to_live = None

    def _pad_search_value(self, value: str) -> str:
        """Pad the search value to a fixed length.

        Args:
            value: Value to pad (original OCR text)

        Returns:
            Padded value with appropriate length and hash
        """
        value = value.strip()

        if self.search_type == "ADDRESS":
            # Create a hash of the original OCR text
            import hashlib

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
            # Keep only digits and basic formatting characters
            value = "".join(c for c in value if c.isdigit() or c in "()+-")
            return f"{value:_>{self._MAX_PHONE_LENGTH}}"
        if self.search_type == "URL":
            # Replace spaces with underscores and lowercase
            value = value.lower().replace(" ", "_")
            return f"{value:_>{self._MAX_URL_LENGTH}}"
        raise ValueError(f"Invalid search type: {self.search_type}")

    @property
    def key(self) -> Dict[str, Dict[str, str]]:
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

    def gsi1_key(self) -> Dict[str, Dict[str, Any]]:
        """
        Generate the GSI1 key for DynamoDB.

        Returns:
            Dict: The GSI1 key attributes
        """
        return {
            "GSI1PK": {"S": "PLACE_ID"},
            "GSI1SK": {"S": f"PLACE_ID#{self.place_id}"},
        }

    def to_item(self) -> Dict[str, Dict[str, Any]]:
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
        key = self.key
        item = {
            **key,  # Base table keys (PK, SK)
            **self.gsi1_key(),  # GSI1 keys
            "TYPE": {"S": "PLACES_CACHE"},
            # Item attributes
            "place_id": {"S": self.place_id},
            "places_response": {"S": json.dumps(self.places_response)},
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

    def __eq__(self, other: object) -> bool:
        """
        Compare two PlacesCache entries for equality.

        Args:
            other: The object to compare with

        Returns:
            bool: True if equal, False otherwise
        """
        if not isinstance(other, PlacesCache):
            return False
        return (
            self.search_type == other.search_type
            and self.search_value == other.search_value
            and self.place_id == other.place_id
            and self.places_response == other.places_response
            and self.last_updated == other.last_updated
            and self.query_count == other.query_count
            and self.normalized_value == other.normalized_value
            and self.value_hash == other.value_hash
            and self.time_to_live == other.time_to_live
        )

    def __iter__(self) -> Generator[Tuple[str, Any], None, None]:
        """
        Returns an iterator over the PlacesCache object's attributes.

        Returns:
            Generator[Tuple[str, Any], None, None]: An iterator over the
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
        if self.time_to_live:
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
        if self.time_to_live:
            base += f", time_to_live={self.time_to_live}"
        return base + ")"


def item_to_places_cache(item: Dict[str, Any]) -> "PlacesCache":
    """
    Convert a DynamoDB item to a PlacesCache object.

    Args:
        item (Dict): The DynamoDB item

    Returns:
        PlacesCache: The converted item

    Raises:
        ValueError: If the item is missing required keys or has invalid data
    """
    required_keys = [
        "PK",
        "SK",
        "search_type",
        "place_id",
        "places_response",
        "last_updated",
        "query_count",
    ]

    if not all(key in item for key in required_keys):
        raise ValueError("Item is missing required keys")

    try:
        places_response = json.loads(item["places_response"]["S"])
        search_type = item["search_type"]["S"]

        # Try to get the original search_value first
        if "search_value" in item and "S" in item["search_value"]:
            search_value = item["search_value"]["S"]
        else:
            # Fall back to extracting from SK if search_value is missing
            padded_value = item["SK"]["S"].split("#")[
                1
            ]  # Get the value after VALUE#
            if search_type == "ADDRESS":
                # Extract the original value after the hash
                parts = padded_value.split("_")
                if len(parts) >= 2:  # We have hash and original value
                    # The original value is after the hash, strip padding
                    search_value = parts[1].lstrip("_").replace("_", " ")
                else:
                    # Fallback for old format
                    search_value = padded_value.lstrip("_").replace("_", " ")
            elif search_type == "PHONE":
                # For phone numbers, strip padding and normalize
                search_value = padded_value.lstrip("_")
                # Keep only digits and basic formatting
                search_value = "".join(
                    c for c in search_value if c.isdigit() or c in "()+-"
                )
            elif search_type == "URL":
                # For URLs, strip padding and keep underscores (they're part
                # of the URL)
                search_value = padded_value.lstrip("_")
            else:
                raise ValueError(f"Invalid search type: {search_type}")

        # Extract normalized value and hash if they exist
        normalized_value = None
        value_hash = None

        if "normalized_value" in item and "S" in item["normalized_value"]:
            normalized_value = item["normalized_value"]["S"]
        elif search_type == "ADDRESS":
            # Try to extract from SK if not in item
            parts = item["SK"]["S"].split("#")[1].split("_")
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

        return PlacesCache(
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
    except (json.JSONDecodeError, ValueError, KeyError) as e:
        raise ValueError(
            f"Error converting item to PlacesCache: {str(e)}"
        ) from e
