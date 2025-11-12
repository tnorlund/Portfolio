"""Search Google Places API for merchant information.

This module provides two approaches:
1. Direct API calls (simple, fast, deterministic)
2. Agent-based search (intelligent, adaptive, can evaluate multiple results)

The direct approach is used by default. For more intelligent search, use
search_places_agent.py with AgentExecutor.
"""

from typing import Dict, Any, List, Optional
from receipt_label.data.places_api import PlacesAPI

from receipt_label.langchain.state.metadata_creation import MetadataCreationState


async def search_places_for_merchant(
    state: MetadataCreationState,
    google_places_api_key: str,
    openai_api_key: str,  # Not used here, but kept for consistency with graph signature
) -> Dict[str, Any]:
    """Search Google Places API for merchant using direct API calls.

    This uses a simple strategy:
    1. Try phone lookup first (if phone available)
    2. Try address lookup (if address available)
    3. Try text search (if merchant name available)

    Args:
        state: Current workflow state
        google_places_api_key: Google Places API key
        openai_api_key: OpenAI API key (not used, kept for signature consistency)

    Returns:
        Dictionary with places search results and selected place
    """
    print(f"🔎 Searching Google Places for merchant...")

    places_api = PlacesAPI(api_key=google_places_api_key)
    search_results: List[Dict[str, Any]] = []
    selected_place: Optional[Dict[str, Any]] = None

    try:
        # Strategy 1: Try phone lookup first
        if state.extracted_phone:
            print(f"   📞 Trying phone lookup: {state.extracted_phone}")
            phone_result = places_api.search_by_phone(state.extracted_phone)
            if phone_result and phone_result.get("place_id"):
                print(f"   ✅ Found match via phone: {phone_result.get('name')}")
                search_results.append(phone_result)
                selected_place = phone_result
                return {
                    "places_search_results": search_results,
                    "selected_place": selected_place,
                }

        # Strategy 2: Try address lookup
        if state.extracted_address and not selected_place:
            print(f"   📍 Trying address lookup: {state.extracted_address}")
            address_result = places_api.search_by_address(state.extracted_address)
            if address_result and address_result.get("place_id"):
                print(f"   ✅ Found match via address: {address_result.get('name')}")
                search_results.append(address_result)
                selected_place = address_result
                return {
                    "places_search_results": search_results,
                    "selected_place": selected_place,
                }

        # Strategy 3: Try text search with merchant name
        if state.extracted_merchant_name and not selected_place:
            print(f"   🔤 Trying text search: {state.extracted_merchant_name}")
            text_result = places_api.search_by_text(state.extracted_merchant_name)
            if text_result and text_result.get("place_id"):
                print(f"   ✅ Found match via text search: {text_result.get('name')}")
                search_results.append(text_result)
                selected_place = text_result
                return {
                    "places_search_results": search_results,
                    "selected_place": selected_place,
                }

        # No match found
        print(f"   ⚠️ No match found in Google Places")
        return {
            "places_search_results": search_results,
            "selected_place": None,
        }

    except Exception as e:
        print(f"   ❌ Error searching Places API: {e}")
        return {
            "places_search_results": [],
            "selected_place": None,
            "error_count": state.error_count + 1,
            "last_error": str(e),
        }
