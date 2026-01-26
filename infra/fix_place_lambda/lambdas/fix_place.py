"""
Fix Place Lambda Handler (Container Lambda)

Fixes incorrect ReceiptPlace records using an LLM agent to reason about
the receipt content and find the correct Google Place.

Input:
    {
        "image_id": "uuid-string",
        "receipt_id": 1,
        "reason": "Merchant shows 'Hyatt Regency Westlake' but receipt is from VONS"
    }

Output:
    {
        "success": true,
        "image_id": "...",
        "receipt_id": 1,
        "old_merchant": "Hyatt Regency Westlake",
        "new_merchant": "Vons",
        "new_place_id": "ChIJ...",
        "confidence": 0.95,
        "reasoning": "Receipt shows VONS branding and address..."
    }

Environment Variables:
    DYNAMODB_TABLE_NAME: DynamoDB table name
    OPENROUTER_API_KEY: OpenRouter API key (LLM)
    RECEIPT_AGENT_OPENAI_API_KEY: OpenAI API key (embeddings)
    GOOGLE_PLACES_API_KEY: Google Places API key
    LANGCHAIN_API_KEY: LangSmith API key (tracing)
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

# LangSmith tracing - ensure traces are flushed before Lambda exits
try:
    from langsmith.run_trees import get_cached_client as get_langsmith_client

    HAS_LANGSMITH = True
except ImportError:
    HAS_LANGSMITH = False
    get_langsmith_client = None  # type: ignore

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Suppress noisy HTTP request logs
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


def flush_langsmith_traces():
    """Flush all pending LangSmith traces to the API."""
    if HAS_LANGSMITH and get_langsmith_client:
        try:
            client = get_langsmith_client()
            client.flush()
            logger.info("LangSmith traces flushed successfully")
        except Exception as e:
            error_str = str(e)
            if "multipart" in error_str.lower():
                logger.debug("LangSmith multipart upload error (non-fatal): %s", error_str[:200])
            else:
                logger.warning("Failed to flush LangSmith traces: %s", error_str)


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """
    Lambda handler to fix an incorrect ReceiptPlace record.

    Uses an LLM agent to:
    1. Read receipt content (lines, words with labels)
    2. Extract merchant hints from receipt
    3. Search Google Places for correct match
    4. Update ReceiptPlace with corrected data
    """
    try:
        # Validate input
        image_id = event.get("image_id")
        receipt_id = event.get("receipt_id")
        reason = event.get("reason", "User reported incorrect merchant")

        if not image_id or receipt_id is None:
            return {
                "success": False,
                "error": "Missing required fields: image_id and receipt_id",
            }

        logger.info(
            "Fixing place for image_id=%s, receipt_id=%s, reason=%s",
            image_id,
            receipt_id,
            reason[:100],
        )

        # Import here to avoid cold start overhead if validation fails
        from receipt_dynamo import DynamoClient
        from receipt_places import PlacesClient

        # Initialize clients
        table_name = os.environ.get("DYNAMODB_TABLE_NAME")
        if not table_name:
            return {"success": False, "error": "DYNAMODB_TABLE_NAME not set"}

        dynamo_client = DynamoClient(table_name=table_name)

        # Initialize Places client (uses GOOGLE_PLACES_API_KEY env var)
        places_api_key = os.environ.get("GOOGLE_PLACES_API_KEY")
        if not places_api_key:
            return {"success": False, "error": "GOOGLE_PLACES_API_KEY not set"}

        places_client = PlacesClient(api_key=places_api_key)

        # Get current ReceiptPlace
        current_place = dynamo_client.get_receipt_place(image_id, receipt_id)
        old_merchant = current_place.merchant_name if current_place else None

        # Get receipt content for analysis
        receipt_lines = dynamo_client.list_receipt_lines_from_receipt(image_id, receipt_id)
        receipt_words = dynamo_client.list_receipt_word_labels_for_receipt(image_id, receipt_id)

        if not receipt_lines and not receipt_words:
            return {
                "success": False,
                "error": f"No receipt content found for {image_id}/{receipt_id}",
            }

        # Build receipt text for analysis
        receipt_text = _build_receipt_text(receipt_lines, receipt_words)

        # Extract merchant hints from receipt content
        merchant_hints = _extract_merchant_hints(receipt_words)

        logger.info(
            "Extracted merchant hints: %s",
            merchant_hints,
        )

        # Try to find correct place using various strategies
        result = _find_correct_place(
            places_client=places_client,
            merchant_hints=merchant_hints,
            receipt_text=receipt_text,
            reason=reason,
        )

        if not result:
            return {
                "success": False,
                "error": "Could not find correct place",
                "image_id": image_id,
                "receipt_id": receipt_id,
                "old_merchant": old_merchant,
                "merchant_hints": merchant_hints,
            }

        # Update ReceiptPlace
        new_place = _update_receipt_place(
            dynamo_client=dynamo_client,
            image_id=image_id,
            receipt_id=receipt_id,
            current_place=current_place,
            new_data=result,
            reason=reason,
        )

        response = {
            "success": True,
            "image_id": image_id,
            "receipt_id": receipt_id,
            "old_merchant": old_merchant,
            "new_merchant": new_place.merchant_name,
            "new_place_id": new_place.place_id,
            "confidence": result.get("confidence", 0.0),
            "reasoning": result.get("reasoning", ""),
        }

        logger.info("Successfully fixed place: %s", response)
        return response

    except Exception as e:
        logger.exception("Error fixing place: %s", e)
        return {
            "success": False,
            "error": str(e),
            "image_id": event.get("image_id"),
            "receipt_id": event.get("receipt_id"),
        }

    finally:
        flush_langsmith_traces()


def _build_receipt_text(receipt_lines: list, receipt_words: list) -> str:
    """Build receipt text from lines and words."""
    lines = []

    # Use lines if available
    if receipt_lines:
        for line in sorted(receipt_lines, key=lambda x: x.line_number):
            lines.append(f"Line {line.line_number}: {line.text}")

    # Add labeled words
    if receipt_words:
        word_lines: dict[int, list] = {}
        for word in receipt_words:
            line_num = word.line_number
            if line_num not in word_lines:
                word_lines[line_num] = []
            label_str = f"[{word.label}]" if word.label and word.label != "O" else ""
            word_lines[line_num].append(f"{word.text}{label_str}")

        for line_num in sorted(word_lines.keys()):
            lines.append(f"Line {line_num} (labeled): {' '.join(word_lines[line_num])}")

    return "\n".join(lines)


def _extract_merchant_hints(receipt_words: list) -> dict[str, Any]:
    """Extract merchant-related hints from labeled words."""
    hints: dict[str, Any] = {
        "merchant_names": [],
        "addresses": [],
        "phone_numbers": [],
        "websites": [],
    }

    for word in receipt_words:
        label = word.label or ""
        text = word.text or ""

        if label == "MERCHANT_NAME":
            hints["merchant_names"].append(text)
        elif label in ("ADDRESS_LINE", "ADDRESS"):
            hints["addresses"].append(text)
        elif label in ("PHONE_NUMBER", "PHONE"):
            hints["phone_numbers"].append(text)
        elif label in ("WEBSITE", "URL"):
            hints["websites"].append(text)

    # Combine adjacent merchant name words
    if hints["merchant_names"]:
        hints["merchant_name_combined"] = " ".join(hints["merchant_names"])

    # Combine address parts
    if hints["addresses"]:
        hints["address_combined"] = " ".join(hints["addresses"])

    return hints


def _find_correct_place(
    places_client: "PlacesClient",
    merchant_hints: dict[str, Any],
    receipt_text: str,
    reason: str,
) -> dict[str, Any] | None:
    """Find the correct place using various strategies."""

    # Strategy 1: Search by phone number (most reliable)
    phone_numbers = merchant_hints.get("phone_numbers", [])
    for phone in phone_numbers:
        if len(phone) >= 7:
            logger.info("Searching by phone: %s", phone)
            place = places_client.search_by_phone(phone)
            if place:
                return {
                    "place_id": place.place_id,
                    "merchant_name": place.name,
                    "formatted_address": place.formatted_address,
                    "merchant_types": place.types,
                    "confidence": 0.95,
                    "reasoning": f"Found via phone number {phone}",
                    "source": "phone_search",
                }

    # Strategy 2: Search by address
    address = merchant_hints.get("address_combined")
    if address and len(address) > 10:
        logger.info("Searching by address: %s", address)
        place = places_client.search_by_address(address)
        if place:
            return {
                "place_id": place.place_id,
                "merchant_name": place.name,
                "formatted_address": place.formatted_address,
                "merchant_types": place.types,
                "confidence": 0.85,
                "reasoning": f"Found via address: {address}",
                "source": "address_search",
            }

    # Strategy 3: Search by merchant name + address
    merchant_name = merchant_hints.get("merchant_name_combined")
    if merchant_name:
        search_query = merchant_name
        if address:
            search_query = f"{merchant_name} {address}"

        logger.info("Searching by text: %s", search_query)
        place = places_client.search_by_text(search_query)
        if place:
            return {
                "place_id": place.place_id,
                "merchant_name": place.name,
                "formatted_address": place.formatted_address,
                "merchant_types": place.types,
                "confidence": 0.75,
                "reasoning": f"Found via text search: {search_query}",
                "source": "text_search",
            }

    logger.warning("Could not find place using any strategy")
    return None


def _update_receipt_place(
    dynamo_client: "DynamoClient",
    image_id: str,
    receipt_id: int,
    current_place: Any,
    new_data: dict[str, Any],
    reason: str,
) -> Any:
    """Update or create ReceiptPlace with corrected data."""
    from receipt_dynamo.entities import ReceiptPlace

    now = datetime.now(timezone.utc)

    if current_place:
        # Update existing place
        current_place.place_id = new_data.get("place_id") or current_place.place_id
        current_place.merchant_name = new_data.get("merchant_name") or current_place.merchant_name
        current_place.formatted_address = new_data.get("formatted_address") or current_place.formatted_address
        current_place.merchant_types = new_data.get("merchant_types") or current_place.merchant_types
        current_place.confidence = new_data.get("confidence", 0.0)
        current_place.reasoning = f"Fixed: {reason}. {new_data.get('reasoning', '')}"
        current_place.validated_by = "LAMBDA_FIX"
        current_place.validation_status = "MATCHED" if new_data.get("confidence", 0) >= 0.8 else "UNSURE"
        current_place.timestamp = now

        dynamo_client.update_receipt_place(current_place)
        return current_place
    else:
        # Create new place
        new_place = ReceiptPlace(
            image_id=image_id,
            receipt_id=receipt_id,
            place_id=new_data.get("place_id", ""),
            merchant_name=new_data.get("merchant_name", ""),
            formatted_address=new_data.get("formatted_address", ""),
            merchant_types=new_data.get("merchant_types", []),
            confidence=new_data.get("confidence", 0.0),
            reasoning=f"Fixed: {reason}. {new_data.get('reasoning', '')}",
            validated_by="LAMBDA_FIX",
            validation_status="MATCHED" if new_data.get("confidence", 0) >= 0.8 else "UNSURE",
            timestamp=now,
        )

        dynamo_client.add_receipt_place(new_place)
        return new_place
