"""LangChain tools for Google Places API."""

from typing import Any, Dict, List, Optional
from langchain_core.tools import tool
from receipt_label.data.places_api import PlacesAPI


def create_places_api_tools(google_places_api_key: str) -> List:
    """Create LangChain tools for Google Places API.

    Args:
        google_places_api_key: Google Places API key

    Returns:
        List of LangChain tool instances
    """
    places_api = PlacesAPI(api_key=google_places_api_key)

    @tool
    def search_by_phone(phone: str) -> Dict[str, Any]:
        """Search Google Places by a phone number.

        Args:
            phone: Phone number to search (can include formatting like (555) 123-4567)

        Returns:
            Dictionary with place_id, name, formatted_address, formatted_phone_number, etc.
            Returns empty dict if no match found.
        """
        result = places_api.search_by_phone(phone)
        return result or {}

    @tool
    def search_by_address(address: str) -> Dict[str, Any]:
        """Search Google Places by address and return the full place details payload.

        Args:
            address: Address string to search

        Returns:
            Dictionary with place_id, name, formatted_address, geometry (lat/lng), etc.
            Returns empty dict if no match found.
        """
        result = places_api.search_by_address(address)
        return result or {}

    @tool
    def search_nearby(lat: float, lng: float, radius: float = 100.0) -> List[Dict[str, Any]]:
        """Find nearby businesses given latitude, longitude, and radius.

        Args:
            lat: Latitude
            lng: Longitude
            radius: Search radius in meters (default: 100.0)

        Returns:
            List of dictionaries with place information. Returns empty list if no matches.
        """
        results = places_api.search_nearby(lat=lat, lng=lng, radius=radius)
        return results or []

    @tool
    def search_by_text(
        query: str,
        lat: Optional[float] = None,
        lng: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Text-search for a business name, with optional location bias.

        Args:
            query: Business name or search query
            lat: Optional latitude for location bias
            lng: Optional longitude for location bias

        Returns:
            Dictionary with place_id, name, formatted_address, etc.
            Returns empty dict if no match found.
        """
        result = places_api.search_by_text(query, lat, lng)
        return result or {}

    @tool
    def get_place_details(place_id: str) -> Dict[str, Any]:
        """Get detailed information about a place using its place_id.

        This includes phone number, website, hours, rating, and other details.
        Use this after finding a place to get complete information.

        Args:
            place_id: The Google Places place_id

        Returns:
            Dictionary with complete place details including formatted_phone_number,
            website, opening_hours, rating, etc. Returns empty dict if not found.
        """
        result = places_api.get_place_details(place_id)
        return result or {}

    return [
        search_by_phone,
        search_by_address,
        search_nearby,
        search_by_text,
        get_place_details,
    ]

