"""Google Places API integration for merchant validation."""

import logging
from typing import Any, Dict, List, Optional

from receipt_label.data.places_api import PlacesAPI

from receipt_dynamo.entities import ReceiptWord

from .utils import get_name_similarity, normalize_phone

# Initialize logger
logger = logging.getLogger(__name__)


def query_google_places(
    extracted_dict: Dict[str, Any],
    google_places_api_key: str,
    all_receipt_words: Optional[List[ReceiptWord]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Query Google Places API for merchant information.

    Args:
        extracted_dict: Dictionary containing extracted receipt fields
        google_places_api_key: API key for Google Places
        all_receipt_words: All receipt words for context (optional)

    Returns:
        Google Places result dict or None if no match found
    """
    places_api = PlacesAPI(google_places_api_key)

    # Try different search strategies
    # 1. Search by phone number if available
    if "phone" in extracted_dict and extracted_dict["phone"]:
        phone_results = places_api.search_by_phone(extracted_dict["phone"])
        if phone_results and is_match_found(phone_results):
            return phone_results

    # 2. Search by address if available
    if "address" in extracted_dict and extracted_dict["address"]:
        address_results = places_api.search_by_address(
            extracted_dict["address"]
        )
        if address_results and is_match_found(address_results):
            return address_results

    # 3. Search by business name if available
    if "name" in extracted_dict and extracted_dict["name"]:
        name_results = places_api.search_by_text(extracted_dict["name"])
        if name_results and is_match_found(name_results):
            return name_results

    return None


def is_match_found(results: Optional[Dict[str, Any]]) -> bool:
    """
    Check if Google Places results represent a valid business match.

    Validates that the result contains essential business information and
    filters out address-only results that are not actual businesses.

    Args:
        results: Google Places API result dict

    Returns:
        True if results contain a valid business match, False otherwise

    Example:
        >>> business_result = {'place_id': 'ChIJ...', 'name': 'Starbucks', 'types': ['cafe']}
        >>> is_match_found(business_result)
        True
        >>> address_result = {'place_id': 'ChIJ...', 'name': '123 Main St', 'types': ['street_address']}
        >>> is_match_found(address_result)
        False
    """
    if not results:
        return False

    # Must have a place_id and name
    if not results.get("place_id") or not results.get("name"):
        return False

    # Reject address-only place types
    address_only_types = {
        "street_address",
        "postal_code",
        "subpremise",
        "premise",
        "route",
        "neighborhood",
        "locality",
        "administrative_area_level_1",
        "administrative_area_level_2",
        "country",
    }

    place_types = set(results.get("types", []))
    if place_types & address_only_types:
        return False

    return True


def is_valid_google_match(
    results: Optional[Dict[str, Any]], extracted_data: Dict[str, Any]
) -> bool:
    """
    Validate if Google Places result matches extracted receipt data.

    Performs cross-validation between Google Places data and extracted receipt
    fields to ensure the match is legitimate.

    Args:
        results: Google Places API result dict
        extracted_data: Extracted receipt data dict with keys like
                       'name', 'phone_number', 'address'

    Returns:
        True if the match is valid based on field comparisons

    Raises:
        ValueError: If extracted_data is invalid

    Example:
        >>> google_result = {
        ...     'name': 'Starbucks Coffee',
        ...     'formatted_phone_number': '+1 555-0123'
        ... }
        >>> receipt_data = {
        ...     'name': 'Starbucks',
        ...     'phone_number': '5550123'
        ... }
        >>> is_valid_google_match(google_result, receipt_data)
        True
    """
    # Input validation
    if not results or not isinstance(results, dict):
        logger.debug("Invalid Google Places result provided")
        return False

    if not extracted_data or not isinstance(extracted_data, dict):
        raise ValueError("extracted_data must be a non-empty dictionary")

    validation_checks = []

    # Compare phone numbers if both are available
    extracted_phone = extracted_data.get("phone_number")
    place_phone = results.get("formatted_phone_number")

    if extracted_phone and place_phone:
        try:
            normalized_extracted = normalize_phone(str(extracted_phone))
            normalized_place = normalize_phone(str(place_phone))

            if normalized_extracted and normalized_place:
                phone_match = normalized_extracted == normalized_place
                validation_checks.append(("phone", phone_match))

                if not phone_match:
                    logger.debug(
                        f"Phone mismatch: extracted={normalized_extracted}, "
                        f"google={normalized_place}"
                    )
                    return False
        except Exception as e:
            logger.warning("Phone comparison failed: %s", e)

    # Compare merchant names using fuzzy matching
    extracted_name = extracted_data.get("name")
    place_name = results.get("name")

    if extracted_name and place_name:
        try:
            name_similarity = get_name_similarity(
                str(place_name), str(extracted_name)
            )
            name_threshold = 80
            name_match = name_similarity >= name_threshold
            validation_checks.append(("name", name_match, name_similarity))

            if not name_match:
                logger.debug(
                    f"Name similarity too low: {name_similarity}% "
                    f"(threshold: {name_threshold}%) for '{extracted_name}' vs '{place_name}'"
                )
                return False
        except Exception as e:
            logger.warning("Name comparison failed: %s", e)
            return False

    # Log successful validation
    if validation_checks:
        check_summary = ", ".join(
            [
                f"{check[0]}:{'✓' if check[1] else '✗'}"
                + (f"({check[2]}%)" if len(check) > 2 else "")
                for check in validation_checks
            ]
        )
        logger.debug("Validation checks: %s", check_summary)

    return True


def retry_google_search_with_inferred_data(
    gpt_merchant_data: dict, google_places_api_key: str
) -> Optional[dict]:
    """
    Re-attempt the Google Places API search using GPT-inferred data with strict validation.

    The search steps are:
    1. Phone-based search using inferred phone number
    2. Geocode the inferred address, then nearby search within 50m radius
    3. Text-based search using inferred merchant name (with location bias if available)

    Each candidate is validated using `is_match_found` (requiring non-empty name/place_id
    and not an address-only type) and `is_valid_google_match` (comparing extracted fields).

    Args:
        gpt_merchant_data (dict): GPT inference output, containing keys
            "phone_number", "address", and "name".
        google_places_api_key (str): API key for Google Places access.

    Returns:
        dict or None: The validated Google Places result, or None if no valid match is found.
    """
    places_api = PlacesAPI(google_places_api_key)

    # 1) Phone-based retry, validate the match
    phone = gpt_merchant_data.get("phone_number")
    if phone:
        match = places_api.search_by_phone(phone)
        if (
            match
            and is_match_found(match)
            and is_valid_google_match(match, gpt_merchant_data)
        ):
            return match

    # 2) Geocode address and nearby search
    address = gpt_merchant_data.get("address")
    lat_lng = None
    if address:
        try:
            geo = places_api.geocode(address)
            if geo and "lat" in geo and "lng" in geo:
                lat_lng = (geo["lat"], geo["lng"])
                nearby_results = places_api.search_nearby(
                    location=lat_lng, radius=50
                )
                for candidate in nearby_results:
                    if is_match_found(candidate) and is_valid_google_match(
                        candidate, gpt_merchant_data
                    ):
                        return candidate
        except Exception:
            pass

    # 3) Text-search on inferred merchant name
    name = gpt_merchant_data.get("name")
    if name:
        try:
            # include location bias if available
            if lat_lng:
                text_match = places_api.search_by_text(name, location=lat_lng)
            else:
                text_match = places_api.search_by_text(name)
            if (
                text_match
                and is_match_found(text_match)
                and is_valid_google_match(text_match, gpt_merchant_data)
            ):
                return text_match
        except Exception:
            pass

    return None
