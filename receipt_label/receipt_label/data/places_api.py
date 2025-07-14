"""
This module provides systematic batch processing for enriching receipt data using the Google Places API.
It handles different combinations of available data (address, phone, url, date) and implements
appropriate validation and fallback strategies.
"""

import logging
import re
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional, Set, Tuple

import requests
from receipt_dynamo.entities.places_cache import PlacesCache

from receipt_label.utils import get_client_manager
from receipt_label.utils.client_manager import ClientManager

# Configure module logger
logger = logging.getLogger(__name__)


class PlacesAPI:
    """A client for interacting with the Google Places API.

    Attributes:
        api_key (str): The API key for the Places API.

    Methods:
        search_by_phone(phone_number: str) -> Optional[Dict]:
            Search for a place using a phone number.
        autocomplete_address(input_text: str) -> Optional[Dict]:
            Get address predictions using the Places Autocomplete API.
        search_by_address(address: str, receipt_words: list = None) -> Optional[Dict]:
            Search for a place using an address.
    """

    BASE_URL = "https://maps.googleapis.com/maps/api/place"

    def __init__(self, api_key: str, client_manager: ClientManager = None):
        """Initialize the Places API client.

        Args:
            api_key (str): Your Google Places API key
            client_manager (ClientManager): Client manager for external services
        """
        self.api_key = api_key
        if client_manager is None:
            client_manager = get_client_manager()
        self.client_manager = client_manager
        logger.debug(
            f"Initializing Places API with key: {api_key[:6] if api_key else 'None'}..."
        )  # Only show first 6 chars for security

    def _get_cached_place(
        self, search_type: str, search_value: str
    ) -> Optional[Dict]:
        """Get a place from cache if it exists.

        Args:
            search_type (str): Type of search (ADDRESS, PHONE, URL)
            search_value (str): The search value

        Returns:
            Optional[Dict]: Cached place details if found, None otherwise
        """
        if not search_value:
            logger.debug("Skipping cache lookup for empty %s", search_type)
            return None
        try:
            # Skip cache for area searches
            if search_type == "ADDRESS":
                if self._is_area_search(search_value):
                    logger.info(
                        f"SKIPPING CACHE (area search): {search_type} - {search_value}"
                    )
                    return None

                # Skip cache for route-level results
                try:
                    cached_item = self.client_manager.dynamo.getPlacesCache(
                        search_type, search_value
                    )
                    if cached_item and cached_item.places_response.get(
                        "types"
                    ) == ["route"]:
                        logger.info(
                            f"SKIPPING CACHE (route-level result): {search_type} - {search_value}"
                        )
                        return None
                except Exception:
                    pass  # Continue with normal cache lookup if error

            # Try to get from cache
            try:
                cached_item = self.client_manager.dynamo.getPlacesCache(
                    search_type, search_value
                )
                if cached_item:
                    logger.info(
                        f"🔍 CACHE HIT: {search_type} - {search_value}"
                    )
                    logger.info(
                        "   Last updated: %s", cached_item.last_updated
                    )
                    logger.info("   Query count: %s", cached_item.query_count)
                    # Increment query count
                    try:
                        self.client_manager.dynamo.incrementQueryCount(
                            cached_item
                        )
                        logger.info(
                            f"   Incremented query count to: {cached_item.query_count + 1}"
                        )
                    except Exception as e:
                        logger.error("Error incrementing query count: %s", e)
                        logger.error("Stack trace: %s", traceback.format_exc())
                    return cached_item.places_response
                logger.info(
                    "❌ CACHE MISS: %s - %s", search_type, search_value
                )
                return None
            except Exception as e:
                logger.error("Error accessing DynamoDB cache: %s", e)
                logger.error("Stack trace: %s", traceback.format_exc())
                return None

        except Exception as e:
            logger.error("Error in _get_cached_place: %s", e)
            logger.error("Stack trace: %s", traceback.format_exc())
            return None

    def _is_area_search(self, search_value: str) -> bool:
        """Check if a search value represents an area search rather than a specific address.

        Args:
            search_value (str): The search value to check

        Returns:
            bool: True if this appears to be an area search, False if it's a specific address
        """
        # Common patterns that indicate an area search
        area_patterns = [
            r"^[A-Za-z\s]+,\s*[A-Za-z\s]+,\s*[A-Z]{2}(?:\s+USA)?$",  # City, State, Country format
            r"^[A-Za-z\s]+,\s*[A-Z]{2}$",  # City, State format
            r"^[A-Za-z\s]+$",  # Just a city or area name
            r"^[A-Za-z\s]+\s+[A-Z]{2}$",  # City State format
        ]

        return any(
            re.match(pattern, search_value.strip())
            for pattern in area_patterns
        )

    def _cache_place(
        self,
        search_type: str,
        search_value: str,
        place_id: str,
        places_response: Dict,
    ) -> None:
        """Cache a place response.

        Args:
            search_type (str): Type of search (ADDRESS, PHONE, URL)
            search_value (str): The search value
            place_id (str): The Google Places place_id
            places_response (Dict): The Places API response
        """
        if not search_value:
            logger.debug("Skipping cache write for empty %s", search_type)
            return
        try:
            if search_type == "ADDRESS":
                # Skip caching if this is an area search
                if self._is_area_search(search_value):
                    logger.info(
                        f"SKIPPED CACHE (area search): {search_type} - {search_value}"
                    )
                    return

                # Skip caching if the result is just a route/street
                if places_response.get("types") == ["route"]:
                    logger.info(
                        f"SKIPPED CACHE (route-level result): {search_type} - {search_value}"
                    )
                    return

                # Skip caching if the result is too general (no street number)
                formatted_address = places_response.get(
                    "formatted_address", ""
                )
                if not re.match(r"^\d+", formatted_address):
                    logger.info(
                        f"SKIPPED CACHE (no street number): {search_type} - {search_value}"
                    )
                    return

            logger.info("CACHING: %s - %s", search_type, search_value)

            # Set TTL for 30 days
            ttl_seconds = 30 * 24 * 60 * 60
            expires_at = int(time.time()) + ttl_seconds

            # Create cache item - normalized_value and value_hash will be set automatically
            # by _pad_search_value when the key is generated
            cache_item = PlacesCache(
                search_type=search_type,
                search_value=search_value,
                place_id=place_id,
                places_response=places_response,
                last_updated=datetime.now(timezone.utc).isoformat(),
                query_count=0,
                time_to_live=expires_at,
            )

            try:
                self.client_manager.dynamo.addPlacesCache(cache_item)
                logger.info(
                    "SUCCESS: Cached %s - %s", search_type, search_value
                )
                if search_type == "ADDRESS":
                    logger.info(
                        f"   Normalized: {cache_item.normalized_value}"
                    )
                    logger.info("   Hash: %s", cache_item.value_hash)
            except Exception as e:
                logger.error("Error adding to DynamoDB cache: %s", e)
                logger.error("Stack trace: %s", traceback.format_exc())
        except Exception as e:
            logger.error("Error in _cache_place: %s", e)
            logger.error("Stack trace: %s", traceback.format_exc())
            logger.error("Failed to cache %s: %s", search_type, search_value)

    def search_by_phone(self, phone_number: str) -> Optional[Dict]:
        """Search for a place using a phone number.

        Args:
            phone_number (str): The phone number to search for

        Returns:
            Optional[Dict]: The place details if found, None otherwise
        """
        # Normalize phone number - keep only digits and basic formatting
        clean_phone = "".join(
            c for c in phone_number if c.isdigit() or c in "()+-"
        )
        logger.info("Searching by phone: %s", clean_phone)
        if not clean_phone:
            logger.info("Empty phone—skipping Places API search and cache")
            return None
        # Check cache first
        cached_result = self._get_cached_place("PHONE", clean_phone)
        if cached_result:
            logger.info("Found cached result for phone: %s", clean_phone)
            return cached_result

        # Validate phone number format
        digits_only = "".join(filter(str.isdigit, clean_phone))
        if len(digits_only) < 10 or len(digits_only) > 15:
            logger.info(
                f"Invalid phone number format (length {len(digits_only)}): {clean_phone}"
            )
            # Cache the invalid result to prevent repeated API calls
            self._cache_place(
                "PHONE",
                clean_phone,
                "INVALID",
                {
                    "status": "INVALID",
                    "message": "Invalid phone number format",
                },
            )
            return None

        logger.info("No cache hit for phone: %s, making API call", clean_phone)
        url = f"{self.BASE_URL}/findplacefromtext/json"

        # For API call, strip all non-numeric characters
        api_phone = "".join(filter(str.isdigit, clean_phone))
        params = {
            "input": api_phone,
            "inputtype": "phonenumber",
            "fields": "formatted_address,name,place_id,types,business_status",
            "key": self.api_key,
        }

        try:
            response = requests.get(url, params=params)
            response.raise_for_status()
            data = response.json()

            if data["status"] == "OK" and data["candidates"]:
                # Get more details using the place_id
                place_id = data["candidates"][0]["place_id"]
                place_details = self.get_place_details(place_id)

                # If this is a genuine business listing, return it
                if place_details and "establishment" in place_details.get(
                    "types", []
                ):
                    logger.info(
                        "Caching new result for phone: %s", clean_phone
                    )
                    self._cache_place(
                        "PHONE", clean_phone, place_id, place_details
                    )
                    return place_details

                # Otherwise, fallback to text search using the phone number
                logger.info(
                    "Phone lookup did not yield an establishment, trying text search fallback"
                )
                text_res = self.search_by_text(clean_phone)
                if text_res:
                    return text_res

                # Cache the non-establishment result
                logger.info(
                    f"Caching non-establishment result for phone: {clean_phone}"
                )
                self._cache_place(
                    "PHONE", clean_phone, place_id, place_details or {}
                )
                return place_details
            else:
                logger.info(
                    f"No results found for phone: {clean_phone} (status: {data['status']})"
                )
                # Cache the no-results case to prevent repeated API calls
                self._cache_place(
                    "PHONE",
                    clean_phone,
                    "NO_RESULTS",
                    {"status": "NO_RESULTS", "message": "No results found"},
                )
            return None

        except requests.exceptions.RequestException as e:
            logger.error("Error searching by phone: %s", e)
            return None

    def autocomplete_address(self, input_text: str) -> Optional[Dict]:
        """Get address predictions using the Places Autocomplete API.

        Args:
            input_text (str): The partial address text

        Returns:
            Optional[Dict]: Address predictions if found, None otherwise
        """
        url = f"{self.BASE_URL}/autocomplete/json"

        params = {
            "input": input_text,
            "types": "address",  # Focus on address predictions
            "key": self.api_key,
        }

        try:
            response = requests.get(url, params=params)
            response.raise_for_status()
            data = response.json()

            if data["status"] == "OK" and data["predictions"]:
                # Return the first (most relevant) prediction
                prediction = data["predictions"][0]
                return prediction
            return None

        except requests.exceptions.RequestException as e:
            logger.error("Error in autocomplete: %s", e)
            return None

    def search_by_address(
        self, address: str, receipt_words: list = None
    ) -> Optional[Dict]:
        """Search for a place using an address.

        Args:
            address (str): The address to search for
            receipt_words (list, optional): List of words from the receipt to help identify the business

        Returns:
            Optional[Dict]: The place details if found, None otherwise
        """
        if not address or not address.strip():
            logger.info("Empty address—skipping Places API search and cache")
            return None
        try:
            # If we have a business name from the receipt, try a text-based business search first
            business_name = None
            if receipt_words:
                for word in receipt_words[:10]:
                    text = getattr(word, "text", "")
                    if text.isupper() and not any(c.isdigit() for c in text):
                        business_name = text
                        break
            if business_name:
                logger.debug(
                    f"Attempting text search for business: {business_name} {address}"
                )
                text_res = self.search_by_text(f"{business_name} {address}")
                if text_res:
                    return text_res

            # First try to get a complete address suggestion
            # Extract the street address part (assuming it starts with numbers)
            street_address = None
            match = re.match(r"\d+[^,]*", address)
            if match:
                street_address = match.group(0).strip()
                logger.debug(
                    f"Trying autocomplete with street address: {street_address}"
                )
                completion = self.autocomplete_address(street_address)
                if completion:
                    address = completion["description"]
                    logger.debug("Using completed address: %s", address)

            # Check cache first
            cached_result = self._get_cached_place("ADDRESS", address)
            if cached_result:
                logger.debug("Found cached result for address: %s", address)
                return cached_result

            url = f"{self.BASE_URL}/findplacefromtext/json"

            # Include geometry to get location coordinates
            params = {
                "input": address,
                "inputtype": "textquery",
                "fields": "formatted_address,name,place_id,types,geometry",
                "key": self.api_key,
            }

            # If we have receipt words, try to find business name and include it in search
            if receipt_words:
                # Look for business name in first few lines
                business_name = None
                for word in receipt_words[:10]:  # Check first 10 words
                    if word and isinstance(
                        word, dict
                    ):  # Add null check for word
                        # Look for business name in text field (all caps, no numbers)
                        text = word.get("text", "")
                        if text.isupper() and not any(
                            c.isdigit() for c in text
                        ):
                            business_name = text
                            break

                if business_name:
                    # Add business name to search query
                    params["input"] = f"{business_name} {address}"
                    logger.debug(
                        f"Using business name in search: {business_name}"
                    )

            logger.debug("Making Places API request to: %s", url)
            logger.debug("With params: %s", params)
            response = requests.get(url, params=params)
            logger.debug("Response status code: %s", response.status_code)
            response.raise_for_status()
            data = response.json()

            if data.get("status") == "OK" and data.get("candidates"):
                place = data["candidates"][0]

                # Skip if this is just a route-level result
                if place.get("types") == ["route"]:
                    logger.info(
                        f"Skipping route-level result for address: {address}"
                    )
                    return None

                # If we only got a subpremise (address) result, try searching nearby
                if place.get("types") == ["subpremise"] and place.get(
                    "geometry"
                ):
                    logger.debug(
                        "Only found address location, searching for businesses nearby..."
                    )
                    lat = place["geometry"]["location"]["lat"]
                    lng = place["geometry"]["location"]["lng"]

                    # Search nearby with receipt words to help identify the business
                    nearby_result = self.search_nearby(
                        lat, lng, radius=100, receipt_words=receipt_words
                    )
                    if nearby_result:
                        # Cache the nearby result
                        self._cache_place(
                            "ADDRESS",
                            address,
                            nearby_result["place_id"],
                            nearby_result,
                        )
                        return nearby_result

                # Get full details for the place
                place_details = self.get_place_details(place["place_id"])
                if place_details:
                    # Cache the result
                    self._cache_place(
                        "ADDRESS", address, place["place_id"], place_details
                    )
                return place_details
            return None

        except requests.exceptions.RequestException as e:
            logger.error("Error searching by address: %s", e)
            return None
        except Exception as e:
            logger.error("Unexpected error in search_by_address: %s", e)
            logger.error("Stack trace: %s", traceback.format_exc())
            return None

    def search_nearby(
        self,
        lat: float,
        lng: float,
        keyword: str = None,
        radius: int = 100,
        receipt_words: list = None,
    ) -> Optional[Dict]:
        """Search for places near a specific location."""
        # Create a more specific cache key that includes business type and radius
        cache_key = f"{lat},{lng}:{radius}"
        if keyword:
            cache_key = f"{cache_key}:{keyword}"
        if receipt_words:
            # Include first few words from receipt to make cache key more specific
            # Ensure each element is a string (e.g. the word.text or its repr)
            texts = []
            for w in receipt_words[:3]:
                if isinstance(w, str):
                    texts.append(w)
                elif hasattr(w, "text"):
                    texts.append(str(w.text))
                else:
                    texts.append(str(w))
            receipt_key = "_".join(texts)
            cache_key = f"{cache_key}:{receipt_key}"

        # Check cache first
        cached_result = self._get_cached_place("ADDRESS", cache_key)
        if cached_result:
            logger.debug("Found cached result for location: %s", cache_key)
            return cached_result

        url = f"{self.BASE_URL}/nearbysearch/json"

        params = {
            "location": f"{lat},{lng}",
            "radius": radius,
            "type": "grocery_or_supermarket",  # Focus on grocery stores
            "key": self.api_key,
        }

        if keyword:
            params["keyword"] = keyword

        try:
            logger.debug("Searching nearby locations: %s", url)
            logger.debug("With params: %s", params)
            response = requests.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            logger.debug("Nearby search response: %s", data)

            if data["status"] == "OK" and data["results"]:
                # If we have receipt words, try to find the best matching business
                if receipt_words:
                    logger.debug(
                        "Comparing nearby businesses with receipt text..."
                    )
                    best_match = None
                    highest_score = 0

                    for place in data["results"]:
                        # Skip route-level results
                        if place.get("types") == ["route"]:
                            continue

                        score = self._compare_with_receipt(
                            place["name"], receipt_words
                        )
                        logger.debug(
                            f"Business: {place['name']}, Match score: {score}"
                        )
                        if score > highest_score:
                            highest_score = score
                            best_match = place

                    if best_match:
                        logger.debug(
                            f"Best matching business: {best_match['name']} (score: {highest_score})"
                        )
                        place_details = self.get_place_details(
                            best_match["place_id"]
                        )
                        if place_details:
                            # Cache the result
                            self._cache_place(
                                "ADDRESS",
                                cache_key,
                                best_match["place_id"],
                                place_details,
                            )
                        return place_details

                # If no receipt words or no match found, return the first non-route result
                for place in data["results"]:
                    if place.get("types") != ["route"]:
                        place_details = self.get_place_details(
                            place["place_id"]
                        )
                        if place_details:
                            # Cache the result
                            self._cache_place(
                                "ADDRESS",
                                cache_key,
                                place["place_id"],
                                place_details,
                            )
                        return place_details

                # If all results were routes, return None
                logger.debug("All nearby results were routes, skipping")
                return None

            return None

        except requests.exceptions.RequestException as e:
            logger.error("Error in nearby search: %s", e)
            return None

    def _compare_with_receipt(
        self, business_name: str, receipt_words: list
    ) -> float:
        """Compare a business name with words from the receipt to find matches.

        Args:
            business_name (str): Name of the business to compare
            receipt_words (list): List of words from the receipt, in order from top to bottom

        Returns:
            float: Score between 0 and 1 indicating how well the business name matches
        """
        # Convert everything to lowercase for comparison
        business_words = set(business_name.lower().split())
        receipt_words = [word.lower() for word in receipt_words]

        # Calculate weighted matches based on word position
        total_score = 0
        max_possible_score = 0

        for business_word in business_words:
            max_possible_score += (
                1  # Each word can contribute max of 1 to the score
            )

            # Check each receipt word for a match
            for i, receipt_word in enumerate(receipt_words):
                if business_word == receipt_word:
                    # Words in first 10 positions get higher weight
                    position_weight = 1.0 if i < 10 else 0.5
                    total_score += position_weight
                    break  # Only count first occurrence of the word

        # Return normalized score
        return (
            total_score / max_possible_score if max_possible_score > 0 else 0
        )

    def get_place_details(self, place_id: str) -> Optional[Dict]:
        """Get detailed information about a place using its place_id.

        Args:
            place_id (str): The Google Places ID

        Returns:
            Optional[Dict]: Detailed place information if found, None otherwise
        """
        url = f"{self.BASE_URL}/details/json"

        # Enhanced fields to get more business information
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
            "plus_code",
            "user_ratings_total",
        ]

        params = {
            "place_id": place_id,
            "fields": ",".join(fields),
            "key": self.api_key,
        }

        try:
            response = requests.get(url, params=params)
            response.raise_for_status()
            data = response.json()

            if data["status"] == "OK":
                return data["result"]
            return None

        except requests.exceptions.RequestException as e:
            logger.error("Error getting place details: %s", e)
            return None

    def search_by_text(
        self,
        query: str,
        lat: Optional[float] = None,
        lng: Optional[float] = None,
    ) -> Optional[Dict]:
        """
        Public text search for a business by free-form query, with optional lat/lng bias.
        """
        url = f"{self.BASE_URL}/textsearch/json"
        params = {
            "query": query,
            "key": self.api_key,
            "fields": "place_id,formatted_address,name,formatted_phone_number,types,business_status",
        }
        # Add location bias if provided
        if lat is not None and lng is not None:
            params["location"] = f"{lat},{lng}"
            params["radius"] = 1000  # meters; adjust as needed
        response = requests.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        if data.get("status") == "OK" and data.get("results"):
            return data["results"][0]
        return None


class ConfidenceLevel(Enum):
    HIGH = "high"  # 3+ matching data points
    MEDIUM = "medium"  # 2 matching data points
    LOW = "low"  # 1 matching data point
    NONE = "none"  # No matching data points


@dataclass
class ValidationResult:
    """Stores the validation results for a Places API match."""

    confidence: ConfidenceLevel
    matched_fields: Set[str]
    place_details: Dict
    validation_score: float
    requires_manual_review: bool


class BatchPlacesProcessor:
    """Handles batch processing of receipts through Places API with validation and fallback strategies."""

    def __init__(self, api_key: str, client_manager: ClientManager = None):
        """Initialize the batch processor.

        Args:
            api_key (str): Google Places API key
            client_manager (ClientManager): Client manager for external services
        """
        self.places_api = PlacesAPI(api_key, client_manager)
        self.logger = logging.getLogger(__name__)

    def process_receipt_batch(self, receipts_data: List[Dict]) -> List[Dict]:
        """Process a batch of receipts, enriching them with Places API data.

        Args:
            receipts_data: List of receipt dictionaries containing extracted data

        Returns:
            List of enriched receipt dictionaries with Places API matches and confidence scores
        """
        enriched_receipts = []

        for receipt in receipts_data:
            try:
                # Classify the receipt based on available data
                available_data = self._classify_receipt_data(receipt)

                # Process based on data availability strategy
                enriched_receipt = self._process_single_receipt(
                    receipt, available_data
                )
                enriched_receipts.append(enriched_receipt)

            except Exception as e:
                self.logger.error(
                    f"Error processing receipt {receipt.get('receipt_id')}: {str(e)}"
                )
                # Add the original receipt with error flag and empty places_api_match
                enriched_receipts.append(
                    {
                        **receipt,
                        "processing_error": str(e),
                        "requires_manual_review": True,
                        "places_api_match": None,  # Add empty places_api_match
                        "confidence_level": ConfidenceLevel.NONE.value,
                        "validation_score": 0.0,
                        "matched_fields": [],
                    }
                )

        return enriched_receipts

    def _classify_receipt_data(self, receipt: Dict) -> Dict[str, List]:
        """Classify available data in the receipt for processing strategy.

        Args:
            receipt: Receipt dictionary with extracted data

        Returns:
            Dictionary of available data types and their values
        """
        available_data = {"address": [], "phone": [], "url": [], "date": []}

        # Extract data from receipt words
        for word in receipt.get("words", []):
            if word.get("extracted_data"):
                data_type = word["extracted_data"].get("type")
                data_value = word["extracted_data"].get("value")
                if data_type and data_value and data_type in available_data:
                    available_data[data_type].append(data_value)

        return available_data

    def _process_single_receipt(
        self, receipt: Dict, available_data: Dict[str, List]
    ) -> Dict:
        """Process a single receipt based on available data.

        Args:
            receipt: Original receipt dictionary
            available_data: Classified available data

        Returns:
            Enriched receipt dictionary with Places API match and confidence score
        """
        # Determine processing strategy based on available data
        num_data_types = sum(
            1 for data_list in available_data.values() if data_list
        )

        if num_data_types >= 3:
            # High priority case - full or nearly full data set
            result = self._process_high_priority_receipt(
                receipt, available_data
            )
        elif num_data_types == 2:
            # Medium priority case - partial data set
            result = self._process_medium_priority_receipt(
                receipt, available_data
            )
        elif num_data_types == 1:
            # Low priority case - minimal data
            result = self._process_low_priority_receipt(
                receipt, available_data
            )
        else:
            # No extractable data
            result = self._process_no_data_receipt(receipt)

        # Combine original receipt with enriched data
        return {
            **receipt,
            "places_api_match": result.place_details if result else None,
            "confidence_level": (
                result.confidence.value
                if result
                else ConfidenceLevel.NONE.value
            ),
            "validation_score": result.validation_score if result else 0.0,
            "matched_fields": list(result.matched_fields) if result else [],
            "requires_manual_review": (
                result.requires_manual_review if result else True
            ),
        }

    def _process_high_priority_receipt(
        self, receipt: Dict, available_data: Dict[str, List]
    ) -> ValidationResult:
        """Process receipt with 3 or more data points."""
        # Try address + phone combination first
        if available_data["address"] and available_data["phone"]:
            result = self._validate_with_address_and_phone(
                available_data["address"][0],
                available_data["phone"][0],
                receipt,
            )
            if result and result.confidence in [
                ConfidenceLevel.HIGH,
                ConfidenceLevel.MEDIUM,
            ]:
                return result

        # Fallback to other combinations
        return self._try_fallback_strategies(receipt, available_data)

    def _process_medium_priority_receipt(
        self, receipt: Dict, available_data: Dict[str, List]
    ) -> ValidationResult:
        """Process receipt with 2 data points."""
        # Get available data types
        has_data = {
            data_type: bool(values)
            for data_type, values in available_data.items()
        }

        # Strategy 1: Address + Phone (highest confidence)
        if has_data["address"] and has_data["phone"]:
            result = self._validate_with_address_and_phone(
                available_data["address"][0],
                available_data["phone"][0],
                receipt,
            )
            if result:
                return result

        # Strategy 2: Address + URL
        if has_data["address"] and has_data["url"]:
            # Search by address first
            place_result = self.places_api.search_by_address(
                available_data["address"][0], receipt.get("words", [])
            )
            if place_result:
                # Validate URL matches
                api_url = place_result.get("website", "").lower()
                receipt_url = available_data["url"][0].lower()
                url_match = receipt_url in api_url or api_url in receipt_url

                if url_match:
                    return ValidationResult(
                        confidence=ConfidenceLevel.MEDIUM,
                        matched_fields={"address", "url"},
                        place_details=place_result,
                        validation_score=0.8,  # High score for URL match
                        requires_manual_review=False,
                    )

        # Strategy 3: Phone + Date
        if has_data["phone"] and has_data["date"]:
            place_result = self.places_api.search_by_phone(
                available_data["phone"][0]
            )
            if place_result:
                return ValidationResult(
                    confidence=ConfidenceLevel.MEDIUM,
                    matched_fields={"phone", "date"},
                    place_details=place_result,
                    validation_score=0.7,  # Good score for phone match
                    requires_manual_review=True,  # Date doesn't validate business
                )

        # Strategy 4: Address + Date (lowest confidence)
        if has_data["address"] and has_data["date"]:
            place_result = self.places_api.search_by_address(
                available_data["address"][0], receipt.get("words", [])
            )
            if place_result:
                return ValidationResult(
                    confidence=ConfidenceLevel.MEDIUM,
                    matched_fields={"address", "date"},
                    place_details=place_result,
                    validation_score=0.6,  # Lower score due to date not validating
                    requires_manual_review=True,
                )

        # If no strategies worked, return low confidence result
        return ValidationResult(
            confidence=ConfidenceLevel.LOW,
            matched_fields=set(),
            place_details={},
            validation_score=0.0,
            requires_manual_review=True,
        )

    def _process_low_priority_receipt(
        self, receipt: Dict, available_data: Dict[str, List]
    ) -> ValidationResult:
        """Process receipt with 1 data point."""
        self.logger.debug(
            f"Processing low priority receipt {receipt.get('receipt_id')} with "
            f"data types: {[k for k, v in available_data.items() if v]}"
        )
        return ValidationResult(
            confidence=ConfidenceLevel.LOW,
            matched_fields=set(),
            place_details={},
            validation_score=0.0,
            requires_manual_review=True,
        )

    def _process_no_data_receipt(self, receipt: Dict) -> ValidationResult:
        """Handle receipt with no extractable data."""
        self.logger.debug(
            f"Processing receipt {receipt.get('receipt_id')} with no extractable data"
        )
        return ValidationResult(
            confidence=ConfidenceLevel.NONE,
            matched_fields=set(),
            place_details={},
            validation_score=0.0,
            requires_manual_review=True,
        )

    def _validate_with_address_and_phone(
        self, address: str, phone: str, receipt: Dict
    ) -> ValidationResult:
        """Validate a place using both address and phone number."""
        # Clean phone number to just digits for comparison
        clean_phone = "".join(filter(str.isdigit, phone))

        # Strategy 1: Search by address first
        self.logger.debug("Searching by address: %s", address)
        address_result = self.places_api.search_by_address(
            address, receipt.get("words", [])
        )

        if address_result:
            # Get and validate business name
            api_name = address_result.get("name", "")
            if not api_name:
                self.logger.debug(
                    "Found match by address but no business name in API data"
                )
                return ValidationResult(
                    confidence=ConfidenceLevel.MEDIUM,
                    matched_fields={"address"},
                    place_details=address_result,
                    validation_score=0.7,
                    requires_manual_review=True,
                )

            # Get and validate phone number
            api_phone = address_result.get("formatted_phone_number", "")
            if not api_phone:
                self.logger.debug(
                    "Found match by address but no phone number in API data"
                )
                return ValidationResult(
                    confidence=ConfidenceLevel.MEDIUM,
                    matched_fields={"address", "name"},
                    place_details=address_result,
                    validation_score=0.7,
                    requires_manual_review=True,
                )

            # Clean and compare phone numbers
            api_phone_clean = "".join(filter(str.isdigit, api_phone))

            # Check if phones match
            if (
                api_phone_clean
                and clean_phone in api_phone_clean
                or api_phone_clean in clean_phone
            ):
                self.logger.debug("Found match by address with matching phone")
                return ValidationResult(
                    confidence=ConfidenceLevel.HIGH,
                    matched_fields={"address", "phone", "name"},
                    place_details=address_result,
                    validation_score=1.0,
                    requires_manual_review=False,
                )

        # Strategy 2: Try searching by phone
        self.logger.debug("Searching by phone: %s", phone)
        phone_result = self.places_api.search_by_phone(phone)

        if phone_result:
            # Get and validate business name
            api_name = phone_result.get("name", "")
            if not api_name:
                self.logger.debug(
                    "Found match by phone but no business name in API data"
                )
                return ValidationResult(
                    confidence=ConfidenceLevel.MEDIUM,
                    matched_fields={"phone"},
                    place_details=phone_result,
                    validation_score=0.7,
                    requires_manual_review=True,
                )

            # Get and validate address
            api_address = phone_result.get("formatted_address", "")
            if not api_address:
                self.logger.debug(
                    "Found match by phone but no address in API data"
                )
                return ValidationResult(
                    confidence=ConfidenceLevel.MEDIUM,
                    matched_fields={"phone", "name"},
                    place_details=phone_result,
                    validation_score=0.7,
                    requires_manual_review=True,
                )

            # Compare addresses with better normalization
            def normalize_address(addr):
                # Convert to lowercase
                addr = addr.lower()
                # Replace common abbreviations
                addr = addr.replace("blva", "blvd").replace("blv", "blvd")
                addr = addr.replace("st.", "street").replace("st", "street")
                addr = addr.replace("ave.", "avenue").replace("ave", "avenue")
                addr = addr.replace("rd.", "road").replace("rd", "road")
                # Remove punctuation and extra spaces
                addr = re.sub(r"[^\w\s]", " ", addr)
                addr = " ".join(addr.split())
                return addr

            api_address_norm = normalize_address(api_address)
            receipt_address_norm = normalize_address(address)

            # Check if addresses have significant overlap
            address_words = set(receipt_address_norm.split())
            api_address_words = set(api_address_norm.split())
            matching_words = address_words.intersection(api_address_words)

            # Calculate address similarity score
            similarity_score = len(matching_words) / max(
                len(address_words), len(api_address_words)
            )

            if similarity_score >= 0.5:  # At least 50% of words match
                self.logger.debug("Found match by phone with similar address")
                return ValidationResult(
                    confidence=ConfidenceLevel.HIGH,
                    matched_fields={"address", "phone", "name"},
                    place_details=phone_result,
                    validation_score=0.8 + (similarity_score * 0.2),
                    requires_manual_review=False,
                )
            else:
                self.logger.debug(
                    "Found by phone but addresses don't match well"
                )
                return ValidationResult(
                    confidence=ConfidenceLevel.MEDIUM,
                    matched_fields={"phone", "name"},
                    place_details=phone_result,
                    validation_score=0.6,
                    requires_manual_review=True,
                )

        self.logger.debug("No conclusive match found by address or phone")
        return None  # No match found

    def _validate_business_name(
        self, receipt_name: str, api_name: str
    ) -> tuple[bool, str, float]:
        """Validate business name from receipt against Places API name.

        Args:
            receipt_name (str): Business name from receipt
            api_name (str): Business name from Places API

        Returns:
            tuple[bool, str, float]: (is_valid, message, confidence_score)
        """

        # Check if API name looks like an address
        def is_address_like(text):
            # Common address patterns
            address_patterns = [
                r"\d+\s+[a-z\s]+(?:street|st|avenue|ave|road|rd|boulevard|blvd|lane|ln|drive|dr|way|court|ct|circle|cir)",
                r"\d+\s+[a-z\s]+(?:unit|suite|apt)\s+[a-z0-9]+",
                r"[a-z\s]+,\s*[a-z]{2}\s+\d{5}(?:-\d{4})?",
            ]
            return any(
                re.search(pattern, text.lower())
                for pattern in address_patterns
            )

        # If API name looks like an address, it's likely a mismatch
        if is_address_like(api_name):
            return (
                False,
                f"Business name appears to be an address: Receipt '{receipt_name}' vs API '{api_name}'",
                0.5,
            )

        # Compare names
        receipt_name = receipt_name.lower()
        api_name = api_name.lower()

        if receipt_name in api_name or api_name in receipt_name:
            return True, "", 1.0
        else:
            return (
                False,
                f"Business name mismatch: Receipt '{receipt_name}' vs API '{api_name}'",
                0.7,
            )

    def _try_fallback_strategies(
        self, receipt: Dict, available_data: Dict[str, List]
    ) -> ValidationResult:
        """Try alternative strategies when primary validation fails."""
        self.logger.debug(
            f"Using fallback strategy for receipt {receipt.get('receipt_id')} with "
            f"data types: {[k for k, v in available_data.items() if v]}"
        )
        return ValidationResult(
            confidence=ConfidenceLevel.LOW,
            matched_fields=set(),
            place_details={},
            validation_score=0.0,
            requires_manual_review=True,
        )
