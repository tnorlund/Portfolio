"""ReceiptMetadata construction utilities for merchant validation."""

from datetime import datetime, timezone
from typing import Optional

from receipt_dynamo.entities import ReceiptMetadata


def build_receipt_metadata_from_result(
    image_id: str,
    receipt_id: int,
    google_place: dict,
    gpt_result: Optional[dict] = None,
) -> ReceiptMetadata:
    """
    Build ReceiptMetadata from a successful Google Places match.

    Args:
        image_id: The image ID of the receipt
        receipt_id: The receipt ID
        google_place: Google Places API result dict
        gpt_result: Optional GPT validation result

    Returns:
        ReceiptMetadata: The constructed metadata entity.
    """
    # Core fields from Google
    place_id = google_place.get("place_id", "")
    merchant_name = google_place.get("name", "")
    address = google_place.get("formatted_address", "")
    # Phone from Google or fallback to GPT
    phone = google_place.get("formatted_phone_number") or (
        gpt_result.get("phone_number", "") if gpt_result else ""
    )

    matched_fields = (
        gpt_result.get("matched_fields", [])
        if gpt_result and "matched_fields" in gpt_result
        else []
    )

    # Determine source of validation
    validated_by = "TEXT_SEARCH"  # Google Places uses text search
    if gpt_result and matched_fields:
        validated_by = "INFERENCE"  # Combined Google + GPT uses inference

    # Basic reasoning
    reasoning = f"Selected merchant based on Google Places"
    if validated_by == "INFERENCE":
        reasoning += " with GPT validation"

    # Optional category: use first Google type if available
    types = google_place.get("types", [])
    merchant_category = types[0] if types else ""

    return ReceiptMetadata(
        image_id=image_id,
        receipt_id=receipt_id,
        place_id=place_id,
        merchant_name=merchant_name,
        merchant_category=merchant_category,
        address=address,
        phone_number=phone,
        matched_fields=matched_fields,
        validated_by=validated_by,
        timestamp=datetime.now(timezone.utc),
        reasoning=reasoning,
    )


def build_receipt_metadata_from_result_no_match(
    receipt_id: int,
    image_id: str,
    gpt_result: Optional[dict] = None,
) -> ReceiptMetadata:
    """
    Build ReceiptMetadata when no Google Places match is found.

    Args:
        receipt_id: The receipt ID
        image_id: The image ID of the receipt
        gpt_result: Optional GPT inference result

    Returns:
        ReceiptMetadata: The constructed metadata entity.
    """
    # Use GPT inference if available, else leave blank
    merchant_name = gpt_result.get("name", "") if gpt_result else ""
    address = gpt_result.get("address", "") if gpt_result else ""
    phone = gpt_result.get("phone_number", "") if gpt_result else ""

    matched_fields = gpt_result.get("matched_fields", []) if gpt_result else []

    # Determine validated_by source
    validated_by = "INFERENCE" if gpt_result else "TEXT_SEARCH"

    # Reasoning message
    if gpt_result:
        reasoning = "No valid Google Places match; used GPT inference"
    else:
        reasoning = (
            "No valid Google Places match and no GPT inference was performed"
        )

    # Use empty placeholders for place_id and category
    # Note: place_id must be a string (empty string is valid, None is not)
    place_id = ""  # Empty string is valid for "no match" cases
    merchant_category = ""

    return ReceiptMetadata(
        image_id=image_id,
        receipt_id=receipt_id,
        place_id=place_id,
        merchant_name=merchant_name,
        merchant_category=merchant_category,
        address=address,
        phone_number=phone,
        matched_fields=matched_fields,
        validated_by=validated_by,
        timestamp=datetime.now(timezone.utc),
        reasoning=reasoning,
    )
