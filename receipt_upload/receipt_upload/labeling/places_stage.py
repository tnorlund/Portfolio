"""
Google Places Validation Stage

Uses Google Places API to validate and refine merchant metadata labels.
Provides ground truth for merchant name, address, and phone number.
"""

import asyncio
import logging
import re
import time
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional

from .types import (
    LabelCorrection,
    PipelineContext,
    PlacesResult,
    WordLabel,
)

logger = logging.getLogger(__name__)

# Similarity thresholds for matching
NAME_SIMILARITY_THRESHOLD = 0.75
ADDRESS_SIMILARITY_THRESHOLD = 0.70


async def run_places_validation(
    ctx: PipelineContext,
    word_labels: List[WordLabel],
) -> PlacesResult:
    """
    Validate merchant metadata using Google Places API.

    1. Extract merchant name, address, phone from labels
    2. Search Google Places by phone or address
    3. Compare and propose corrections for mismatched labels

    Args:
        ctx: Pipeline context with PlacesClient
        word_labels: Labels from previous stages

    Returns:
        PlacesResult with validated metadata and corrections
    """
    start_time = time.perf_counter()

    if ctx.places_client is None:
        logger.warning("Places client not configured, skipping validation")
        return PlacesResult(validation_time_ms=0.0)

    try:
        # Extract current labeled values
        merchant_name = _extract_text_by_label(word_labels, "MERCHANT_NAME")
        address = _extract_text_by_label(word_labels, "ADDRESS")
        phone = _extract_phone(word_labels)

        # Run Places lookup in executor
        loop = asyncio.get_event_loop()
        places_data = await loop.run_in_executor(
            None,
            lambda: _lookup_place(ctx.places_client, phone, address, merchant_name),
        )

        if not places_data:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            return PlacesResult(
                validation_time_ms=elapsed_ms,
                lookup_method="none",
            )

        # Compare with labels and generate corrections
        corrections = _compare_with_labels(
            word_labels,
            merchant_name,
            address,
            phone,
            places_data,
        )

        elapsed_ms = (time.perf_counter() - start_time) * 1000

        return PlacesResult(
            place_id=places_data.get("place_id"),
            merchant_name=places_data.get("name"),
            formatted_address=places_data.get("formatted_address"),
            phone_number=places_data.get("phone_number"),
            business_types=places_data.get("types", []),
            label_corrections=corrections,
            lookup_method=places_data.get("lookup_method"),
            validation_time_ms=elapsed_ms,
        )

    except Exception as e:
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        logger.exception("Places validation failed: %s", e)
        return PlacesResult(validation_time_ms=elapsed_ms)


def _extract_text_by_label(
    word_labels: List[WordLabel],
    label_type: str,
) -> Optional[str]:
    """Extract concatenated text for a given label type."""
    words = [wl for wl in word_labels if wl.label == label_type]
    if not words:
        return None
    # Sort by position
    words.sort(key=lambda w: (w.line_id, w.word_id))
    return " ".join(w.text for w in words)


def _extract_phone(word_labels: List[WordLabel]) -> Optional[str]:
    """Extract and normalize phone number from labels."""
    phone_text = _extract_text_by_label(word_labels, "PHONE")
    if not phone_text:
        return None
    # Extract digits only
    digits = re.sub(r"\D", "", phone_text)
    # Return 10-digit US phone or original
    if len(digits) == 10:
        return digits
    if len(digits) == 11 and digits.startswith("1"):
        return digits[1:]
    return digits if digits else None


def _lookup_place(
    places_client: Any,
    phone: Optional[str],
    address: Optional[str],
    merchant_name: Optional[str],
) -> Optional[Dict[str, Any]]:
    """Look up place using phone, address, or merchant name."""
    result: Optional[Dict[str, Any]] = None

    # Try phone first (most reliable)
    if phone and len(phone) >= 10:
        try:
            logger.info("Places lookup by phone: %s", phone)
            place = places_client.search_by_phone(phone)
            if place and place.get("name"):
                result = _format_place(place)
                result["lookup_method"] = "phone"
                return result
        except Exception as e:
            logger.warning("Phone lookup failed: %s", e)

    # Try address
    if address and len(address) > 10:
        try:
            logger.info("Places lookup by address: %s...", address[:50])
            place = places_client.search_by_address(address)
            if place and place.get("name"):
                result = _format_place(place)
                result["lookup_method"] = "address"
                return result
        except Exception as e:
            logger.warning("Address lookup failed: %s", e)

    # Try text search with merchant name
    if merchant_name:
        try:
            logger.info("Places text search: %s", merchant_name)
            place = places_client.search_by_text(merchant_name)
            if place and place.get("name"):
                result = _format_place(place)
                result["lookup_method"] = "text_search"
                return result
        except Exception as e:
            logger.warning("Text search failed: %s", e)

    return None


def _format_place(place_data: Dict[str, Any]) -> Dict[str, Any]:
    """Format place data into consistent structure."""
    return {
        "place_id": place_data.get("place_id"),
        "name": place_data.get("name"),
        "formatted_address": place_data.get("formatted_address"),
        "phone_number": (
            place_data.get("formatted_phone_number")
            or place_data.get("international_phone_number")
        ),
        "types": place_data.get("types", []),
    }


def _compare_with_labels(
    word_labels: List[WordLabel],
    current_name: Optional[str],
    current_address: Optional[str],
    current_phone: Optional[str],
    places_data: Dict[str, Any],
) -> List[LabelCorrection]:
    """Compare current labels with Places data and generate corrections."""
    corrections: List[LabelCorrection] = []

    places_name = places_data.get("name")
    places_address = places_data.get("formatted_address")
    places_phone = places_data.get("phone_number")

    # Check merchant name
    if places_name and current_name:
        name_similarity = _similarity(current_name, places_name)
        if name_similarity < NAME_SIMILARITY_THRESHOLD:
            # Find which words have MERCHANT_NAME label
            merchant_words = [wl for wl in word_labels if wl.label == "MERCHANT_NAME"]
            if merchant_words:
                # Flag as needing review (we don't know which words to correct)
                corrections.append(
                    LabelCorrection(
                        line_id=merchant_words[0].line_id,
                        word_id=merchant_words[0].word_id,
                        original_label="MERCHANT_NAME",
                        corrected_label="MERCHANT_NAME",  # Same label, different text
                        confidence=0.85,
                        reason=f"Google Places name '{places_name}' differs from labeled '{current_name}' (similarity: {name_similarity:.2f})",
                        source="places",
                    )
                )

    # Check if address words are correctly labeled
    if places_address and not current_address:
        # Address might be labeled as O or something else
        # Look for words that might be address components
        potential_address_words = _find_potential_address_words(
            word_labels, places_address
        )
        for wl in potential_address_words:
            if wl.label != "ADDRESS":
                corrections.append(
                    LabelCorrection(
                        line_id=wl.line_id,
                        word_id=wl.word_id,
                        original_label=wl.label,
                        corrected_label="ADDRESS",
                        confidence=0.8,
                        reason=f"Word '{wl.text}' appears in Google Places address",
                        source="places",
                    )
                )

    # Check phone
    if places_phone and current_phone:
        places_digits = re.sub(r"\D", "", places_phone)
        current_digits = re.sub(r"\D", "", current_phone)
        if places_digits != current_digits:
            phone_words = [wl for wl in word_labels if wl.label == "PHONE"]
            if phone_words:
                corrections.append(
                    LabelCorrection(
                        line_id=phone_words[0].line_id,
                        word_id=phone_words[0].word_id,
                        original_label="PHONE",
                        corrected_label="PHONE",
                        confidence=0.9,
                        reason=f"Google Places phone '{places_phone}' differs from labeled '{current_phone}'",
                        source="places",
                    )
                )

    return corrections


def _find_potential_address_words(
    word_labels: List[WordLabel],
    places_address: str,
) -> List[WordLabel]:
    """Find words that might be part of an address based on Places data."""
    potential: List[WordLabel] = []

    # Extract address components
    address_lower = places_address.lower()
    address_words = set(address_lower.split())

    # Common address patterns
    street_suffixes = {"st", "street", "ave", "avenue", "rd", "road", "dr", "drive", "blvd", "boulevard", "ln", "lane", "ct", "court", "way", "pl", "place"}
    state_abbrevs = {"ca", "ny", "tx", "fl", "wa", "or", "az", "nv", "co", "il", "oh", "pa", "ma", "ga", "nc", "mi", "nj", "va"}

    for wl in word_labels:
        text_lower = wl.text.lower().strip(".,")

        # Check if word appears in address
        if text_lower in address_words:
            potential.append(wl)
            continue

        # Check for street suffix
        if text_lower in street_suffixes:
            potential.append(wl)
            continue

        # Check for state abbreviation
        if text_lower in state_abbrevs:
            potential.append(wl)
            continue

        # Check for zip code pattern
        if re.match(r"^\d{5}(-\d{4})?$", wl.text):
            potential.append(wl)
            continue

    return potential


def _similarity(a: str, b: str) -> float:
    """Calculate string similarity using SequenceMatcher."""
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()
