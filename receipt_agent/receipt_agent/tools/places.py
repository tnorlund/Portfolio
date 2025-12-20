"""
Google Places API tools for merchant verification.

These tools enable the agent to verify merchant information
against Google Places API as a ground truth source.

Uses receipt_places.PlacesClient for automatic DynamoDB caching:
- Stores results in DynamoDB with configurable TTL (default 30 days)
- Uses normalized keys for address/phone lookups
- Tracks query counts for analytics
- Skips caching for area searches and route-level results

Access Patterns (PlacesCache in DynamoDB):
- PK: PLACES#<search_type>  (ADDRESS, PHONE, URL)
- SK: VALUE#<padded_search_value>
- GSI1: PLACE_ID -> PLACE_ID#<place_id>  (lookup by place_id)
"""

import logging
from typing import Any, Optional, Union

from langchain_core.tools import tool
from pydantic import BaseModel, Field

# Import PlacesClient from receipt_places (standalone package)
try:
    from receipt_places import PlacesClient
    PLACES_CLIENT_AVAILABLE = True
except ImportError:
    PlacesClient = None  # type: ignore
    PLACES_CLIENT_AVAILABLE = False

logger = logging.getLogger(__name__)


class VerifyWithPlacesInput(BaseModel):
    """Input schema for verify_with_google_places tool."""

    place_id: Optional[str] = Field(
        default=None,
        description="Google Place ID to verify directly",
    )
    phone_number: Optional[str] = Field(
        default=None,
        description="Phone number to search for (uses DynamoDB cache)",
    )
    address: Optional[str] = Field(
        default=None,
        description="Address to geocode and search (uses DynamoDB cache)",
    )
    merchant_name: Optional[str] = Field(
        default=None,
        description="Merchant name for text search",
    )


@tool(args_schema=VerifyWithPlacesInput)
def verify_with_google_places(
    place_id: Optional[str] = None,
    phone_number: Optional[str] = None,
    address: Optional[str] = None,
    merchant_name: Optional[str] = None,
    # Injected at runtime - accepts PlacesClient from receipt_places
    _places_client: Optional[Union["PlacesClient", Any]] = None,
) -> dict[str, Any]:
    """
    Verify merchant information using Google Places API with DynamoDB caching.

    CACHING: Uses receipt_places.PlacesClient which automatically:
    - Checks DynamoDB cache before API calls
    - Caches phone and address searches for 30 days
    - Tracks query counts for analytics

    Search priority:
    1. place_id: Direct lookup (fastest, no cache needed)
    2. phone_number: Cached phone search
    3. address: Cached address search
    4. merchant_name: Text search (not cached)

    Returns verified business details including:
    - Official business name
    - Formatted address
    - Phone number
    - Business type/category
    - Place ID for future reference

    Use this as ground truth when validating metadata.
    """
    if _places_client is None:
        return {"error": "Google Places client not configured. Install receipt_places."}

    try:
        result: dict[str, Any] = {
            "search_method": None,
            "found": False,
            "place": None,
        }

        # Try place_id first (most reliable, direct lookup)
        if place_id:
            result["search_method"] = "place_id"
            logger.info("🔍 Places lookup by place_id: %s...", place_id[:20])
            place_data = _places_client.get_place_details(place_id)
            if place_data and place_data.get("name"):
                result["found"] = True
                result["place"] = _format_place_result(place_data)
                return result

        # Try phone search (uses DynamoDB cache via PlacesClient)
        if phone_number and not result["found"]:
            result["search_method"] = "phone"
            logger.info("🔍 Places lookup by phone: %s (cached)", phone_number)
            place_data = _places_client.search_by_phone(phone_number)
            if place_data and place_data.get("name"):
                result["found"] = True
                result["place"] = _format_place_result(place_data)
                return result

        # Try address geocoding (uses DynamoDB cache via PlacesClient)
        if address and not result["found"]:
            result["search_method"] = "address"
            logger.info("🔍 Places lookup by address: %s... (cached)", address[:50])
            place_data = _places_client.search_by_address(address)
            if place_data and place_data.get("name"):
                result["found"] = True
                result["place"] = _format_place_result(place_data)
                return result

        # Try text search with merchant name (not cached)
        if merchant_name and not result["found"]:
            result["search_method"] = "text_search"
            logger.info("🔍 Places text search: %s (NOT cached)", merchant_name)
            place_data = _places_client.search_by_text(merchant_name)
            if place_data and place_data.get("name"):
                result["found"] = True
                result["place"] = _format_place_result(place_data)
                return result

        result["message"] = "No matching business found in Google Places"
        return result

    except (ValueError, TypeError, KeyError) as e:
        logger.error("Error verifying with Google Places: %s", e)
        return {"error": str(e)}


class FindBusinessesAtAddressInput(BaseModel):
    """Input schema for find_businesses_at_address tool."""

    address: str = Field(
        description="Address to search for businesses at (e.g., '166 W Hillcrest Dr, Thousand Oaks, CA')"
    )


@tool(args_schema=FindBusinessesAtAddressInput)
def find_businesses_at_address(
    address: str,
    # Injected at runtime - accepts PlacesClient from receipt_places
    _places_client: Optional[Union["PlacesClient", Any]] = None,
) -> dict[str, Any]:
    """
    Find businesses at a specific address using Google Places API.

    Use this when Google Places returns an address as the merchant name
    (e.g., "166 W Hillcrest Dr" instead of a business name). This tool
    searches for actual businesses at that address so you can identify
    the correct merchant.

    CACHING: Uses receipt_places.PlacesClient which automatically caches
    address searches for 30 days.

    Returns a list of businesses found at the address, each with:
    - place_id: Google Place ID
    - name: Business name
    - formatted_address: Full address
    - phone_number: Phone number if available
    - types: Business types (restaurant, store, etc.)

    You can then reason about which business matches the receipt based on:
    - Business name matching receipt content
    - Business type matching receipt type
    - Phone number matching receipt phone
    """
    if _places_client is None:
        return {"error": "Google Places client not configured. Install receipt_places."}

    if not address:
        return {"error": "Address is required"}

    try:
        logger.info("🔍 Places search for businesses at address: %s...", address[:50])

        # First, geocode the address to get lat/lng
        geocode_result = _places_client.search_by_address(address)
        if not geocode_result:
            return {
                "found": False,
                "businesses": [],
                "message": f"Could not geocode address: {address}",
            }

        # Extract location from geocode result
        geometry = geocode_result.get("geometry", {})
        location = geometry.get("location", {})
        lat = location.get("lat")
        lng = location.get("lng")

        if not lat or not lng:
            return {
                "found": False,
                "businesses": [],
                "message": f"Could not get coordinates for address: {address}",
            }

        # Now search for nearby businesses (within 50 meters of the address)
        nearby_businesses = _places_client.search_nearby(
            lat=lat,
            lng=lng,
            radius=50,  # 50 meters - very close to the address
        )

        if not nearby_businesses:
            return {
                "found": False,
                "businesses": [],
                "address_searched": address,
                "coordinates": {"lat": lat, "lng": lng},
                "message": f"No businesses found within 50m of address",
            }

        # Format results
        businesses = []
        for business in nearby_businesses[:10]:  # Limit to 10
            formatted = _format_place_result(business)
            businesses.append(formatted)

        return {
            "found": True,
            "businesses": businesses,
            "address_searched": address,
            "coordinates": {"lat": lat, "lng": lng},
            "count": len(businesses),
            "message": f"Found {len(businesses)} business(es) at address",
        }

    except Exception as e:
        logger.error("Error finding businesses at address: %s", e)
        return {"error": str(e)}


def _format_place_result(place_data: dict[str, Any]) -> dict[str, Any]:
    """Format Google Places result into a consistent structure."""
    return {
        "place_id": place_data.get("place_id"),
        "name": place_data.get("name"),
        "formatted_address": place_data.get("formatted_address"),
        "phone_number": place_data.get("formatted_phone_number")
        or place_data.get("international_phone_number"),
        "types": place_data.get("types", []),
        "business_status": place_data.get("business_status"),
        "rating": place_data.get("rating"),
        "user_ratings_total": place_data.get("user_ratings_total"),
        "geometry": place_data.get("geometry", {}).get("location"),
    }


class ComparePlaceInput(BaseModel):
    """Input schema for compare_place_with_google tool."""

    current_name: str = Field(description="Current merchant name in place")
    current_address: Optional[str] = Field(
        default=None, description="Current address in place"
    )
    current_phone: Optional[str] = Field(
        default=None, description="Current phone in place"
    )
    places_name: str = Field(description="Name from Google Places")
    places_address: Optional[str] = Field(
        default=None, description="Address from Google Places"
    )
    places_phone: Optional[str] = Field(
        default=None, description="Phone from Google Places"
    )


@tool(args_schema=ComparePlaceInput)
def compare_place_with_google(
    current_name: str,
    current_address: Optional[str],
    current_phone: Optional[str],
    places_name: str,
    places_address: Optional[str],
    places_phone: Optional[str],
) -> dict[str, Any]:
    """
    Compare current place data against Google Places data.

    Use this tool to understand the differences between
    what's stored in place and what Google Places reports.

    Returns a detailed comparison showing:
    - Which fields match
    - Which fields differ
    - Similarity scores for each field
    - Recommendations for updates
    """
    from difflib import SequenceMatcher

    def similarity(a: Optional[str], b: Optional[str]) -> float:
        if not a or not b:
            return 0.0
        return SequenceMatcher(
            None, a.lower().strip(), b.lower().strip()
        ).ratio()

    def normalize_phone(phone: Optional[str]) -> str:
        if not phone:
            return ""
        return "".join(c for c in phone if c.isdigit())

    # Compare fields
    name_sim = similarity(current_name, places_name)
    addr_sim = similarity(current_address, places_address)

    # Phone comparison (digits only)
    current_phone_digits = normalize_phone(current_phone)
    places_phone_digits = normalize_phone(places_phone)
    phone_match = (
        current_phone_digits == places_phone_digits
        if current_phone_digits and places_phone_digits
        else None
    )

    # Generate recommendations
    recommendations: list[str] = []
    if name_sim < 0.85:
        recommendations.append(
            f"Consider updating merchant_name to '{places_name}'"
        )
    if addr_sim < 0.75 and places_address:
        recommendations.append(
            f"Consider updating address to '{places_address}'"
        )
    if phone_match is False:
        recommendations.append(
            f"Consider updating phone to '{places_phone}'"
        )

    # Overall assessment
    matched_fields: list[str] = []
    if name_sim > 0.85:
        matched_fields.append("name")
    if addr_sim > 0.75:
        matched_fields.append("address")
    if phone_match:
        matched_fields.append("phone")

    # Build comparison result with explicit type
    result: dict[str, Any] = {
        "name_comparison": {
            "current": current_name,
            "places": places_name,
            "similarity": round(name_sim, 3),
            "match": name_sim > 0.85,
        },
        "address_comparison": {
            "current": current_address,
            "places": places_address,
            "similarity": round(addr_sim, 3),
            "match": addr_sim > 0.75,
        },
        "phone_comparison": {
            "current": current_phone,
            "current_digits": current_phone_digits,
            "places": places_phone,
            "places_digits": places_phone_digits,
            "match": phone_match,
        },
        "matched_fields": matched_fields,
        "match_count": len(matched_fields),
        "recommendations": recommendations,
        "overall_confidence": round(
            (name_sim + addr_sim + (1.0 if phone_match else 0.0)) / 3, 3
        ),
    }

    return result

