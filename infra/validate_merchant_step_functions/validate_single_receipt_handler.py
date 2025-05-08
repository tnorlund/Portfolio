MAX_AGENT_ATTEMPTS = 2
import os
import json
from typing import List, Optional, Dict, Any, Literal
import enum
from datetime import datetime, timezone
from logging import getLogger, StreamHandler, Formatter, INFO
from agents import Agent, Runner, function_tool
from receipt_dynamo.entities.receipt_metadata import ReceiptMetadata
from receipt_dynamo.constants import ValidationMethod
from receipt_label.merchant_validation import (
    get_receipt_details,
    write_receipt_metadata_to_dynamo,
    extract_candidate_merchant_fields,
    query_google_places,
    is_match_found,
    is_valid_google_match,
    validate_match_with_gpt,
    infer_merchant_with_gpt,
    retry_google_search_with_inferred_data,
    build_receipt_metadata_from_result,
    build_receipt_metadata_from_result_no_match,
)

# Build a Literal type from the ValidationMethod enum values
ValidatedBy = Literal[tuple(m.value for m in ValidationMethod)]

logger = getLogger()
logger.setLevel(INFO)

GOOGLE_PLACES_API_KEY = os.environ["GOOGLE_PLACES_API_KEY"]

if len(logger.handlers) == 0:
    handler = StreamHandler()
    handler.setFormatter(
        Formatter(
            "[%(levelname)s] %(asctime)s.%(msecs)dZ %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    logger.addHandler(handler)


def calculate_final_confidence(
    base_confidence: float, num_matched_fields: int
) -> float:
    """
    Compute final confidence by bumping the base confidence
    according to number of matched fields.
    """
    if not 0.0 <= base_confidence <= 1.0:
        raise ValueError(
            f"base_confidence must be between 0 and 1, got {base_confidence}"
        )
    # Bump: 0.1 for each field above 2, cap at 1.0
    extra = max(0, (num_matched_fields - 2) * 0.1)
    return min(1.0, base_confidence + extra)


# Final metadata return tool
@function_tool
def tool_return_metadata(
    place_id: str,
    merchant_name: str,
    address: str,
    phone_number: str,
    match_confidence: float,
    merchant_category: str,
    matched_fields: List[str],
    validated_by: ValidatedBy,
    reasoning: str,
) -> Dict[str, Any]:
    """
    Return the final merchant metadata as a structured object.

    Args:
        place_id (str): Google Places place_id for the matched business.
        merchant_name (str): The canonical merchant name.
        address (str): The merchant's address.
        phone_number (str): The merchant's phone number.
        match_confidence (float): Confidence score for the match (0.0-1.0).
        merchant_category (str): The merchant's category.
        matched_fields (List[str]): List of receipt fields that were used to validate the match (e.g., ["name", "phone"]).
        validated_by (ValidatedBy): The method used for validation; one of the defined ValidationMethod enum values.
        reasoning (str): Explanation of how the match was determined.
    """
    # Normalize validated_by to a allowed enum value
    try:
        vb_enum = ValidationMethod(validated_by)
    except ValueError:
        valid_vals = [e.value for e in ValidationMethod]
        raise ValueError(
            f"validated_by must be one of: {valid_vals}. Got: {validated_by!r}"
        )
    validated_by = vb_enum.value

    return {
        "place_id": place_id,
        "merchant_name": merchant_name,
        "address": address,
        "phone_number": phone_number,
        "match_confidence": match_confidence,
        "merchant_category": merchant_category,
        "matched_fields": matched_fields,
        "validated_by": validated_by,
        "reasoning": reasoning,
    }


# 2. Phone lookup
@function_tool
def tool_search_by_phone(phone: str) -> Dict[str, Any]:
    """
    Search Google Places by a phone number.
    """
    from receipt_label.merchant_validation.merchant_validation import PlacesAPI

    return PlacesAPI(GOOGLE_PLACES_API_KEY).search_by_phone(phone)


# 3. Address geocode & nearby
@function_tool
def tool_search_by_address(address: str) -> Dict[str, Any]:
    """
    Search Google Places by address and return the full place details payload.
    """
    from receipt_label.merchant_validation.merchant_validation import PlacesAPI

    # Return the full Places API result for the address search
    result = PlacesAPI(GOOGLE_PLACES_API_KEY).search_by_address(address)
    return result


@function_tool
def tool_search_nearby(lat: float, lng: float, radius: float) -> List[Dict[str, Any]]:
    """
    Find nearby businesses given latitude, longitude, and radius.
    """
    from receipt_label.merchant_validation.merchant_validation import PlacesAPI

    return PlacesAPI(GOOGLE_PLACES_API_KEY).search_nearby(
        lat=lat, lng=lng, radius=radius
    )


@function_tool
def tool_search_by_text(
    query: str, lat: Optional[float] = None, lng: Optional[float] = None
) -> Dict[str, Any]:
    """
    Text‐search for a business name, with optional location bias.
    """
    from receipt_label.data.places_api import PlacesAPI

    return PlacesAPI(GOOGLE_PLACES_API_KEY).search_by_text(query, lat, lng)


TOOLS = [
    tool_search_by_phone,
    tool_search_by_address,
    tool_search_nearby,
    tool_search_by_text,
    tool_return_metadata,
]

agent = Agent(
    name="ReceiptMerchantAgent",
    instructions="""
You are ReceiptMerchantAgent. Your goal is to assign the correct merchant to this receipt.
You may call the following tools in any order:

1. **search_by_phone**: to look up a business by phone.
2. **search_by_address**: to geocode the receipt's address.
3. **search_nearby**: to find businesses near a lat/lng.
4. **search_by_text**: to text‐search for a business name, biased by location.

**Policy**:
- First try phone lookup. If you get a business (non‐empty name & place_id), validate on name/address/phone.
- If that fails, geocode the address and do a nearby search; validate again.
- If still no match, perform a text search with the extracted or inferred merchant name; validate the result.
- Only if text search yields no usable match, then infer the merchant details via GPT inference.
- After deciding on the final metadata, call the function `tool_return_metadata`
  with the exact values for each field (place_id, merchant_name, etc.) and then stop.
""",
    model="gpt-3.5-turbo",
    tools=TOOLS,
)


def validate_handler(event, context):
    logger.info("Starting validate_single_receipt_handler")
    image_id = event["image_id"]
    receipt_id = event["receipt_id"]
    (
        receipt,
        receipt_lines,
        receipt_words,
        receipt_letters,
        receipt_word_tags,
        receipt_word_labels,
    ) = get_receipt_details(image_id, receipt_id)
    logger.info(f"Got Receipt details for {image_id} {receipt_id}")

    # Prepare the user input
    user_input = {
        "image_id": image_id,
        "receipt_id": receipt_id,
        "raw_text": [line.text for line in receipt_lines],
        "extracted_data": {
            key: [w.text for w in words]
            for key, words in extract_candidate_merchant_fields(receipt_words).items()
        },
    }

    # Run the agent with retries
    metadata = None
    user_messages = [{"role": "user", "content": json.dumps(user_input)}]
    for attempt in range(1, MAX_AGENT_ATTEMPTS + 1):
        run_result = Runner.run_sync(agent, user_messages)
        # Extract structured metadata by parsing the function call arguments
        for item in run_result.new_items:
            raw = getattr(item, "raw_item", None)
            if hasattr(raw, "name") and raw.name == "tool_return_metadata":
                try:
                    metadata = json.loads(raw.arguments)
                except Exception:
                    metadata = getattr(item, "output", None)
                break
        if metadata is not None:
            break
        logger.warning(
            f"Agent attempt {attempt} did not call tool_return_metadata; retrying."
        )
    # If still no metadata, fallback to no match
    if metadata is None:
        logger.error("All agent attempts failed; using no-match fallback.")
        no_match_meta = build_receipt_metadata_from_result_no_match(
            image_id, receipt_id, user_input["raw_text"]
        )
        write_receipt_metadata_to_dynamo(no_match_meta)
        # Return the receipt info for the next step even in the no-match case
        return {"image_id": image_id, "receipt_id": receipt_id, "status": "no_match"}

    # Right after parsing `metadata` and before you build the ReceiptMetadata:
    raw_vb = metadata.get("validated_by", "").upper().replace(" ", "_")
    if raw_vb in ValidationMethod.__members__:
        vb_enum = ValidationMethod[raw_vb]
    else:
        vb_enum = ValidationMethod.INFERENCE
    matched_fields = list({f.strip() for f in metadata["matched_fields"] if f.strip()})
    logger.info(f"matched_fields: {matched_fields}")
    base = float(metadata["match_confidence"])
    final_score = calculate_final_confidence(base, len(matched_fields))
    merchant_category = metadata.get("merchant_category", "")

    meta = ReceiptMetadata(
        image_id=image_id,
        receipt_id=receipt_id,
        place_id=metadata["place_id"],
        merchant_name=metadata["merchant_name"].strip('"').strip(),
        address=metadata["address"].strip('"').strip(),
        phone_number=metadata["phone_number"].strip('"').strip(),
        merchant_category=merchant_category.strip('"').strip(),
        match_confidence=final_score,
        matched_fields=matched_fields,
        timestamp=datetime.now(timezone.utc),
        validated_by=vb_enum,
        reasoning=metadata["reasoning"].strip('"').strip(),
    )
    logger.info(f"Got metadata for {image_id} {receipt_id}\n{dict(meta)}")

    logger.info(f"Writing metadata to DynamoDB for {image_id} {receipt_id}")

    write_receipt_metadata_to_dynamo(meta)

    # Return the receipt information for the next step in the workflow
    return {
        "image_id": image_id,
        "receipt_id": receipt_id,
        "status": "processed",
        "place_id": metadata["place_id"],
        "merchant_name": metadata["merchant_name"].strip('"').strip(),
    }
