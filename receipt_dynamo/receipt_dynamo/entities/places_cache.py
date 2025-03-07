import json
from datetime import datetime
from typing import Dict, Any, Literal

SEARCH_TYPES = Literal["ADDRESS", "PHONE", "URL"]


class PlacesCache:
    """
    Represents a cached Google Places API response stored in DynamoDB.

    This class caches responses from the Google Places API to minimize API calls and costs.
    The cache is keyed by the type of search (address, phone, URL) and the search value.

    Attributes:
        search_type (str): Type of search performed (ADDRESS, PHONE, URL)
        search_value (str): The value used for the search
        place_id (str): The Google Places place_id
        places_response (Dict): The response from the Google Places API
        last_updated (str): ISO format timestamp of last update
        query_count (int): Number of times this cache entry has been accessed
    """
    # Maximum field lengths based on Google Places API
    _MAX_ADDRESS_LENGTH = 400  # Allow for future growth beyond current 315 max
    _MAX_PHONE_LENGTH = 30    # International format with extra padding
    _MAX_URL_LENGTH = 100     # Standard URLs with some padding

    def __init__(
        self,
        search_type: SEARCH_TYPES,
        search_value: str,
        place_id: str,
        places_response: Dict[str, Any],
        last_updated: str,
        query_count: int = 0
    ):
        """
        Initialize a new PlacesCache entry.

        Args:
            search_type (str): Type of search performed (ADDRESS, PHONE, URL)
            search_value (str): The value used for the search
            place_id (str): The Google Places place_id
            places_response (Dict): The response from the Google Places API
            last_updated (str): ISO format timestamp of last update
            query_count (int, optional): Times this cache entry was accessed. Defaults to 0.

        Raises:
            ValueError: If any of the parameters are invalid
        """
        # Validate search_type
        if search_type not in ["ADDRESS", "PHONE", "URL"]:
            raise ValueError(f"search_type must be one of: ADDRESS, PHONE, URL")
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
        except (ValueError, TypeError):
            raise ValueError("last_updated must be a valid ISO format timestamp")
        self.last_updated = last_updated

        # Validate query_count
        if not isinstance(query_count, int) or query_count < 0:
            raise ValueError("query_count must be non-negative")
        self.query_count = query_count

    def _pad_search_value(self, value: str) -> str:
        """
        Pad search value with underscores for consistent DynamoDB sort keys.
        Different padding lengths for different types based on expected max lengths.
        Replaces spaces with underscores and right-aligns the value with underscore padding.
        
        Args:
            value: Value to pad
            
        Returns:
            Padded value with appropriate length
        """
        value = value.strip()
        
        if self.search_type == "ADDRESS":
            # Replace spaces with underscores before padding
            value = value.replace(" ", "_")
            return f"{value:_>{self._MAX_ADDRESS_LENGTH}}"
        elif self.search_type == "PHONE":
            # Keep only digits and basic formatting characters
            value = "".join(c for c in value if c.isdigit() or c in "()+-")
            return f"{value:_>{self._MAX_PHONE_LENGTH}}"
        elif self.search_type == "URL":
            # Replace spaces with underscores and lowercase
            value = value.lower().replace(" ", "_")
            return f"{value:_>{self._MAX_URL_LENGTH}}"
        else:
            raise ValueError(f"Invalid search type: {self.search_type}")

    def key(self) -> Dict[str, Dict[str, str]]:
        """
        Generate the primary key for DynamoDB.
        
        Returns:
            Dict: The primary key attributes
        """
        padded_value = self._pad_search_value(self.search_value)
        return {
            "PK": {"S": f"PLACES#{self.search_type}"},
            "SK": {"S": f"VALUE#{padded_value}"}
        }

    def gsi1_key(self) -> Dict[str, Dict[str, Any]]:
        """
        Generate the GSI1 key for DynamoDB.
        
        Returns:
            Dict: The GSI1 key attributes
        """ 
        return {
            "GSI1PK": {"S": "PLACE_ID"},
            "GSI1SK": {"S": f"PLACE_ID#{self.place_id}"}
        }

    def gsi2_key(self) -> Dict[str, Dict[str, Any]]:
        """
        Generate the GSI2 key for DynamoDB.
        
        Returns:
            Dict: The GSI2 key attributes
        """
        return {
            "GSI2PK": {"S": "LAST_USED"},
            "GSI2SK": {"S": self.last_updated}
        }

    def to_item(self) -> Dict[str, Dict[str, Any]]:
        """
        Convert to a DynamoDB item format.
        Includes all necessary attributes for the base table and GSIs:
        - Base table: PK (PLACES#<search_type>), SK (VALUE#<padded_search_value>)
        - GSI1: GSI1PK (PLACE_ID), GSI1SK (PLACE_ID#<place_id>) - For place_id lookups
        - GSI2: GSI2PK (LAST_USED), GSI2SK (<timestamp>) - For cache invalidation
        
        Returns:
            Dict: The DynamoDB item representation with all required attributes
        """
        key = self.key()
        return {
            **key,  # Base table keys (PK, SK)
            **self.gsi1_key(),  # GSI1 keys
            **self.gsi2_key(),  # GSI2 keys
            "TYPE": {"S": "PLACES_CACHE"},
            # Item attributes
            "place_id": {"S": self.place_id},
            "places_response": {"S": json.dumps(self.places_response)},
            "last_updated": {"S": self.last_updated},
            "query_count": {"N": str(self.query_count)},
            "search_type": {"S": self.search_type},
            "search_value": {"S": self.search_value}
        }

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
            self.search_type == other.search_type and
            self.search_value == other.search_value and
            self.place_id == other.place_id and
            self.places_response == other.places_response and
            self.last_updated == other.last_updated and
            self.query_count == other.query_count
        )

    def __repr__(self) -> str:
        """
        Get string representation of the PlacesCache entry.
        
        Returns:
            str: String representation
        """
        return (
            f"PlacesCache(search_type='{self.search_type}', "
            f"search_value='{self.search_value}', "
            f"place_id='{self.place_id}', "
            f"query_count={self.query_count})"
        )


def itemToPlacesCache(item: Dict[str, Dict[str, Any]]) -> "PlacesCache":
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
        "PK", "SK", "search_type", "place_id", "places_response",
        "last_updated", "query_count"
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
            padded_value = item["SK"]["S"].split("#")[1]  # Get the value after VALUE#
            if search_type == "ADDRESS":
                # Right-aligned, so strip leading underscores and replace remaining with spaces
                search_value = padded_value.lstrip("_").replace("_", " ")
            elif search_type == "PHONE":
                # Just strip padding for phone numbers since we don't replace anything
                search_value = padded_value.lstrip("_")
            elif search_type == "URL":
                # For URLs, strip padding and keep underscores (they're part of the URL)
                search_value = padded_value.lstrip("_")
            else:
                raise ValueError(f"Invalid search type: {search_type}")
        
        return PlacesCache(
            search_type=search_type,
            search_value=search_value,
            place_id=item["place_id"]["S"],
            places_response=places_response,
            last_updated=item["last_updated"]["S"],
            query_count=int(item["query_count"]["N"])
        )
    except (json.JSONDecodeError, ValueError, KeyError) as e:
        raise ValueError(f"Error converting item to PlacesCache: {str(e)}") 