"""
This module provides systematic batch processing for enriching receipt data using the Google Places API.
It handles different combinations of available data (address, phone, url, date) and implements
appropriate validation and fallback strategies.
"""

from typing import List, Dict, Optional, Tuple, Set
from dataclasses import dataclass
from enum import Enum
import logging
import requests
import re

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
    
    def __init__(self, api_key: str):
        """Initialize the Places API client."""
        self.api_key = api_key
        logger.debug(f"Initializing Places API with key: {api_key[:6]}...")
    
    def search_by_phone(self, phone_number: str) -> Optional[Dict]:
        """Search for a place using a phone number."""
        phone_number = ''.join(filter(str.isdigit, phone_number))
        url = f"{self.BASE_URL}/findplacefromtext/json"
        
        params = {
            "input": phone_number,
            "inputtype": "phonenumber",
            "fields": "formatted_address,name,place_id,types,business_status",
            "key": self.api_key
        }
        
        try:
            response = requests.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            
            if data["status"] == "OK" and data["candidates"]:
                place_id = data["candidates"][0]["place_id"]
                return self.get_place_details(place_id)
            return None
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Error searching by phone: {e}")
            return None
    
    def autocomplete_address(self, input_text: str) -> Optional[Dict]:
        """Get address predictions using the Places Autocomplete API."""
        url = f"{self.BASE_URL}/autocomplete/json"
        
        params = {
            "input": input_text,
            "types": "address",
            "key": self.api_key
        }
        
        try:
            response = requests.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            
            if data["status"] == "OK" and data["predictions"]:
                return data["predictions"][0]
            return None
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Error in autocomplete: {e}")
            return None

    def search_by_address(self, address: str, receipt_words: list = None) -> Optional[Dict]:
        """Search for a place using an address."""
        # Try to get a complete address suggestion
        match = re.match(r'\d+[^,]*', address)
        if match:
            street_address = match.group(0).strip()
            completion = self.autocomplete_address(street_address)
            if completion:
                address = completion['description']
        
        url = f"{self.BASE_URL}/findplacefromtext/json"
        params = {
            "input": address,
            "inputtype": "textquery",
            "fields": "formatted_address,name,place_id,types,geometry",
            "key": self.api_key
        }
        
        try:
            response = requests.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            
            if data["status"] == "OK" and data["candidates"]:
                place = data["candidates"][0]
                
                # If we only got a subpremise, try searching nearby
                if place["types"] == ["subpremise"] and "geometry" in place:
                    lat = place["geometry"]["location"]["lat"]
                    lng = place["geometry"]["location"]["lng"]
                    nearby_result = self.search_nearby(lat, lng, radius=100, receipt_words=receipt_words)
                    if nearby_result:
                        return nearby_result
                
                return self.get_place_details(place["place_id"])
            return None
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Error searching by address: {e}")
            return None
    
    def search_nearby(self, lat: float, lng: float, keyword: str = None, radius: int = 100, receipt_words: list = None) -> Optional[Dict]:
        """Search for places near a specific location."""
        url = f"{self.BASE_URL}/nearbysearch/json"
        
        params = {
            "location": f"{lat},{lng}",
            "radius": radius,
            "type": "grocery_or_supermarket",
            "key": self.api_key
        }
        
        if keyword:
            params["keyword"] = keyword
        
        try:
            response = requests.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            
            if data["status"] == "OK" and data["results"]:
                if receipt_words:
                    best_match = None
                    highest_score = 0
                    
                    for place in data["results"]:
                        score = self._compare_with_receipt(place["name"], receipt_words)
                        if score > highest_score:
                            highest_score = score
                            best_match = place
                    
                    if best_match:
                        return self.get_place_details(best_match["place_id"])
                
                return self.get_place_details(data["results"][0]["place_id"])
            return None
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Error in nearby search: {e}")
            return None

    def _compare_with_receipt(self, business_name: str, receipt_words: list) -> float:
        """Compare a business name with words from the receipt to find matches."""
        business_words = set(business_name.lower().split())
        receipt_words = [word['text'].lower() for word in receipt_words]
        
        total_score = 0
        max_possible_score = 0
        
        for business_word in business_words:
            max_possible_score += 1
            for i, receipt_word in enumerate(receipt_words):
                if business_word == receipt_word:
                    position_weight = 1.0 if i < 10 else 0.5
                    total_score += position_weight
                    break
        
        return total_score / max_possible_score if max_possible_score > 0 else 0

    def get_place_details(self, place_id: str) -> Optional[Dict]:
        """Get detailed information about a place using its place_id."""
        url = f"{self.BASE_URL}/details/json"
        
        fields = [
            "name", "formatted_address", "formatted_phone_number",
            "international_phone_number", "website", "rating",
            "price_level", "business_status", "types",
            "opening_hours", "geometry", "vicinity",
            "plus_code", "user_ratings_total"
        ]
        
        params = {
            "place_id": place_id,
            "fields": ",".join(fields),
            "key": self.api_key
        }
        
        try:
            response = requests.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            
            if data["status"] == "OK":
                return data["result"]
            return None
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Error getting place details: {e}")
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

    def __init__(self, api_key: str):
        """Initialize the batch processor.

        Args:
            api_key (str): Google Places API key
        """
        self.places_api = PlacesAPI(api_key)
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
                # Add the original receipt with error flag
                enriched_receipts.append(
                    {
                        **receipt,
                        "processing_error": str(e),
                        "requires_manual_review": True,
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
                if data_type and data_value:
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
        """Process receipt with 2 data points.
        
        Strategy:
        1. Try address + phone (highest confidence combination)
        2. Try address + url (can validate business website)
        3. Try phone + date (phone is more reliable than date)
        4. Try address + date (lowest confidence combination)
        
        Args:
            receipt: Receipt dictionary
            available_data: Dictionary of available data by type
            
        Returns:
            ValidationResult with match details and confidence score
        """
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
                receipt
            )
            if result:
                return result
        
        # Strategy 2: Address + URL
        if has_data["address"] and has_data["url"]:
            # Search by address first
            place_result = self.places_api.search_by_address(
                available_data["address"][0],
                receipt.get("words", [])
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
                        requires_manual_review=False
                    )
        
        # Strategy 3: Phone + Date
        if has_data["phone"] and has_data["date"]:
            place_result = self.places_api.search_by_phone(available_data["phone"][0])
            if place_result:
                return ValidationResult(
                    confidence=ConfidenceLevel.MEDIUM,
                    matched_fields={"phone", "date"},
                    place_details=place_result,
                    validation_score=0.7,  # Good score for phone match
                    requires_manual_review=True  # Date doesn't validate business
                )
        
        # Strategy 4: Address + Date (lowest confidence)
        if has_data["address"] and has_data["date"]:
            place_result = self.places_api.search_by_address(
                available_data["address"][0],
                receipt.get("words", [])
            )
            if place_result:
                return ValidationResult(
                    confidence=ConfidenceLevel.MEDIUM,
                    matched_fields={"address", "date"},
                    place_details=place_result,
                    validation_score=0.6,  # Lower score due to date not validating
                    requires_manual_review=True
                )
        
        # If no strategies worked, return low confidence result
        return ValidationResult(
            confidence=ConfidenceLevel.LOW,
            matched_fields=set(),
            place_details={},
            validation_score=0.0,
            requires_manual_review=True
        )

    def _process_low_priority_receipt(
        self, receipt: Dict, available_data: Dict[str, List]
    ) -> ValidationResult:
        """Process receipt with 1 data point."""
        logger.debug(  # Changed from warning to debug
            f"Processing low priority receipt {receipt.get('receipt_id')} with "
            f"data types: {[k for k, v in available_data.items() if v]}"
        )
        return ValidationResult(
            confidence=ConfidenceLevel.LOW,
            matched_fields=set(),
            place_details={},
            validation_score=0.0,
            requires_manual_review=True
        )

    def _process_no_data_receipt(self, receipt: Dict) -> ValidationResult:
        """Handle receipt with no extractable data."""
        logger.debug(  # Changed from warning to debug
            f"Processing receipt {receipt.get('receipt_id')} with no extractable data"
        )
        return ValidationResult(
            confidence=ConfidenceLevel.NONE,
            matched_fields=set(),
            place_details={},
            validation_score=0.0,
            requires_manual_review=True
        )

    def _validate_with_address_and_phone(
        self, address: str, phone: str, receipt: Dict
    ) -> ValidationResult:
        """Validate a place using both address and phone number.
        
        Strategy:
        1. Search by address first (more specific)
        2. If found, validate phone number matches
        3. If no match, try searching by phone
        4. If found by phone, validate address matches
        
        Args:
            address: Address string from receipt
            phone: Phone number from receipt
            receipt: Full receipt data for additional context
            
        Returns:
            ValidationResult with match details and confidence score
        """
        # Clean phone number to just digits for comparison
        clean_phone = ''.join(filter(str.isdigit, phone))
        
        # Strategy 1: Search by address first
        logger.debug(f"Searching by address: {address}")  # Changed from info to debug
        address_result = self.places_api.search_by_address(
            address,
            receipt.get("words", [])
        )
        
        if address_result:
            # Get phone from result and clean it
            api_phone = ''.join(filter(
                str.isdigit, 
                address_result.get("formatted_phone_number", "")
            ))
            
            # Check if phones match
            if api_phone and clean_phone in api_phone or api_phone in clean_phone:
                logger.debug("Found match by address with matching phone")  # Changed from info to debug
                return ValidationResult(
                    confidence=ConfidenceLevel.HIGH,
                    matched_fields={"address", "phone"},
                    place_details=address_result,
                    validation_score=1.0,  # Perfect match
                    requires_manual_review=False
                )
        
        # Strategy 2: Try searching by phone
        logger.debug(f"Searching by phone: {phone}")  # Changed from info to debug
        phone_result = self.places_api.search_by_phone(phone)
        
        if phone_result:
            # Get address from result
            api_address = phone_result.get("formatted_address", "").lower()
            receipt_address = address.lower()
            
            # Check if addresses have significant overlap
            address_words = set(receipt_address.split())
            api_address_words = set(api_address.split())
            matching_words = address_words.intersection(api_address_words)
            
            # Calculate address similarity score
            similarity_score = len(matching_words) / max(len(address_words), len(api_address_words))
            
            if similarity_score >= 0.5:  # At least 50% of words match
                logger.debug("Found match by phone with similar address")  # Changed from info to debug
                return ValidationResult(
                    confidence=ConfidenceLevel.HIGH,
                    matched_fields={"address", "phone"},
                    place_details=phone_result,
                    validation_score=0.8 + (similarity_score * 0.2),  # Score between 0.9-1.0
                    requires_manual_review=False
                )
            else:
                logger.debug("Found by phone but addresses don't match well")  # Changed from info to debug
                return ValidationResult(
                    confidence=ConfidenceLevel.MEDIUM,
                    matched_fields={"phone"},
                    place_details=phone_result,
                    validation_score=0.6,  # Lower score due to address mismatch
                    requires_manual_review=True
                )
        
        logger.debug("No conclusive match found by address or phone")  # Changed from info to debug
        return None  # No match found

    def _try_fallback_strategies(
        self, receipt: Dict, available_data: Dict[str, List]
    ) -> ValidationResult:
        """Try alternative strategies when primary validation fails."""
        logger.debug(  # Changed from warning to debug
            f"Using fallback strategy for receipt {receipt.get('receipt_id')} with "
            f"data types: {[k for k, v in available_data.items() if v]}"
        )
        return ValidationResult(
            confidence=ConfidenceLevel.LOW,
            matched_fields=set(),
            place_details={},
            validation_score=0.0,
            requires_manual_review=True
        )
