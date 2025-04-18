from typing import Tuple, List, Optional
import json
from json import JSONDecodeError
from datetime import datetime, timezone

from receipt_dynamo.entities import (
    ReceiptWordLabel,
    ReceiptWord,
    Receipt,
    ReceiptLine,
    ReceiptWord,
    ReceiptLetter,
    ReceiptWordTag,
    ReceiptMetadata,
)
from receipt_label.data.places_api import PlacesAPI
from receipt_label.utils import get_clients

dynamo_client, openai_client, _ = get_clients()


def list_receipts_for_merchant_validation() -> List[Tuple[str, int]]:
    """
    Lists all receipts that do not have receipt metadata.

    Returns:
        List[Tuple[str, int]]: A list of tuples containing the image_id and
            receipt_id of the receipts that do not have receipt metadata.
    """
    receipts, lek = dynamo_client.listReceipts(limit=25)
    while lek:
        next_receipts, lek = dynamo_client.listReceipts(
            limit=25, lastEvaluatedKey=lek
        )
        receipts.extend(next_receipts)
    # Filter out receipts that have receipt metadata
    receipt_metadatas = dynamo_client.getReceiptMetadatas(
        [
            {
                "PK": {"S": f"IMAGE#{receipt.image_id}"},
                "SK": {"S": f"RECEIPT#{receipt.receipt_id:05d}#METADATA"},
            }
            for receipt in receipts
        ]
    )
    # Create a set of tuples with (image_id, receipt_id) from metadata for efficient lookup
    metadata_keys = {
        (metadata.image_id, metadata.receipt_id)
        for metadata in receipt_metadatas
    }

    # Return receipts that don't have corresponding metadata
    return [
        (receipt.image_id, receipt.receipt_id)
        for receipt in receipts
        if (receipt.image_id, receipt.receipt_id) not in metadata_keys
    ]


def get_receipt_details(image_id: str, receipt_id: int) -> Tuple[
    Receipt,
    list[ReceiptLine],
    list[ReceiptWord],
    list[ReceiptLetter],
    list[ReceiptWordTag],
    list[ReceiptWordLabel],
]:
    """Get a receipt with its details"""
    (
        receipt,
        lines,
        words,
        letters,
        tags,
        labels,
    ) = dynamo_client.getReceiptDetails(image_id, receipt_id)
    return (
        receipt,
        lines,
        words,
        letters,
        tags,
        labels,
    )


def extract_candidate_merchant_fields(words: List[ReceiptWord]) -> dict:
    """
    Extracts all possible `address`, `url`, and `phone` values from `ReceiptWord` entities.

    Returns:
        dict: {
            "address": list[ReceiptWord],
            "phone": list[ReceiptWord],
            "url": list[ReceiptWord]
        }
    """
    result = {"address": [], "phone": [], "url": []}

    for word in words:
        data = word.extracted_data
        if not data:
            continue
        value = data.get("value")
        if data["type"] == "address":
            result["address"].append(word)
        elif data["type"] == "phone":
            result["phone"].append(word)
        elif data["type"] == "url":
            result["url"].append(word)

    return result


def validate_match_with_gpt(receipt_fields: dict, google_place: dict) -> dict:
    """
    Uses GPT function calling to determine if the Google Place result matches the extracted receipt fields.

    Args:
        receipt_fields (dict): Extracted name, address, phone from the receipt.
        google_place (dict): Google Places API result with 'name', 'formatted_address', and 'formatted_phone_number'.

    Returns:
        dict: {
            "decision": "YES" | "NO" | "UNSURE",
            "confidence": float,
            "matched_fields": list[str],
            "reason": str
        }
    """
    functions = [
        {
            "name": "validateMatch",
            "description": "Validate if the extracted merchant info matches the Google Places result.",
            "parameters": {
                "type": "object",
                "properties": {
                    "decision": {
                        "type": "string",
                        "enum": ["YES", "NO", "UNSURE"],
                        "description": "Is the Google result a match?",
                    },
                    "matched_fields": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": ["name", "address", "phone"],
                        },
                        "description": "Which fields match",
                    },
                    "confidence": {
                        "type": "number",
                        "description": "Confidence in the match, from 0 to 1",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Explain the decision clearly",
                    },
                },
                "required": ["decision", "confidence", "reason"],
            },
        }
    ]

    system_prompt = (
        "You are an assistant that validates whether a Google Places result matches merchant data extracted from a receipt. "
        "You use string similarity and common sense, and explain your decision clearly."
    )

    user_prompt = f""" 
    Compare the following two merchant records and decide whether they match.
 
    📄 Extracted from receipt:
    - Name: {receipt_fields.get('name')}
    - Address: {receipt_fields.get('address')}
    - Phone: {receipt_fields.get('phone')}
 
    📍 From Google Places:
    - Name: {google_place.get('name')}
    - Address: {google_place.get('formatted_address')}
    - Phone: {google_place.get('formatted_phone_number')}
 
    Only return structured output by calling the `validateMatch` function.
    """

    response = openai_client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        functions=functions,
        function_call={"name": "validateMatch"},
    )

    result = {
        "decision": "UNSURE",
        "confidence": 0.0,
        "matched_fields": [],
        "reason": "Failed to parse GPT response",
    }

    message = response.choices[0].message
    if hasattr(message, "function_call") and message.function_call:
        try:
            args = json.loads(message.function_call.arguments)
            result.update(args)
            if result["decision"] == "YES" and not result["matched_fields"]:
                matched_fields = []
                if (
                    receipt_fields.get("name", "").lower()
                    in google_place.get("name", "").lower()
                ):
                    matched_fields.append("name")
                if receipt_fields.get("phone", "").replace(" ", "").replace(
                    "-", ""
                ) in google_place.get("formatted_phone_number", "").replace(
                    " ", ""
                ).replace(
                    "-", ""
                ):
                    matched_fields.append("phone")
                if (
                    receipt_fields.get("address", "").lower().split()[0]
                    in google_place.get("formatted_address", "").lower()
                ):
                    matched_fields.append("address")
                result["matched_fields"] = matched_fields
        except JSONDecodeError:
            pass

    return result


def query_google_places(
    extracted_dict: dict, google_places_api_key: str
) -> Optional[dict]:
    """
    Queries the Google Places API using available merchant data extracted from the receipt.

    Args:
        extracted_dict (dict): Dictionary with lists of ReceiptWord entities grouped by type:
            "address", "phone", "url"
        google_places_api_key (str): API key for accessing Google Places

    Returns:
        dict or None: Google Places match result (place details) or None
    """
    places_api = PlacesAPI(google_places_api_key)

    # 1. Try phone-based match
    phone_words = extracted_dict.get("phone", [])
    if phone_words:
        phone = phone_words[0].extracted_data["value"]
        phone_match = places_api.search_by_phone(phone)
        # Only accept a valid phone match (ignore NO_RESULTS)
        if phone_match and phone_match.get("status") != "NO_RESULTS":
            return phone_match

    # 2. Try address-based match
    address_words = extracted_dict.get("address", [])
    if address_words:
        address = address_words[0].extracted_data["value"]
        receipt_words = [{"text": w.text} for w in address_words]
        address_match = places_api.search_by_address(address, receipt_words)
        if address_match:
            return address_match

        # 3. No match found
        return None


def is_valid_google_match(place: dict, extracted_dict: dict) -> bool:
    """
    Returns True if the place details represent a valid merchant match based on:
    1) presence of place_id and formatted_address
    2) business_status is OPERATIONAL or missing
    3) not purely a route, street_address, or subpremise
    4) at least one extracted address fragment appears in the formatted_address
    """
    # 1. Must have place_id and formatted_address
    if not place.get("place_id") or not place.get("formatted_address"):
        return False

    # 2. Only accept operational or unspecified status
    status = place.get("business_status")
    if status and status != "OPERATIONAL":
        return False

    # 3. Exclude raw-address types
    bad_types = {"route", "street_address", "subpremise"}
    if set(place.get("types", [])) & bad_types:
        return False

    # 4. Address containment check
    formatted = place.get("formatted_address", "").lower()
    for word in extracted_dict.get("address", []):
        fragment = word.extracted_data.get("value", "").lower()
        if fragment and fragment in formatted:
            return True

    return False


def infer_merchant_with_gpt(raw_text: List[str], extracted_dict: dict) -> dict:
    """
    Uses ChatGPT function calling to infer merchant metadata when no Google match is found.

    Args:
        raw_text (List[str]): OCR'd receipt lines.
        extracted_dict (dict): Extracted address/phone/url ReceiptWord lists.

    Returns:
        dict: {
            "merchant_name": str,
            "merchant_address": str,
            "merchant_phone": str,
            "confidence": float
        }
    """
    # Define the function schema for GPT
    functions = [
        {
            "name": "inferMerchant",
            "description": "Infer merchant name, address, and phone from receipt lines and extracted fields.",
            "parameters": {
                "type": "object",
                "properties": {
                    "merchant_name": {
                        "type": "string",
                        "description": "The business name inferred from the receipt.",
                    },
                    "merchant_address": {
                        "type": "string",
                        "description": "The business address inferred from the receipt.",
                    },
                    "merchant_phone": {
                        "type": "string",
                        "description": "The business phone number inferred from the receipt.",
                    },
                    "confidence": {
                        "type": "number",
                        "description": "Confidence score between 0 and 1.",
                    },
                },
                "required": [
                    "merchant_name",
                    "merchant_address",
                    "merchant_phone",
                    "confidence",
                ],
            },
        }
    ]

    # Construct messages
    system_prompt = (
        "You are an assistant that infers merchant information from OCR'd receipt text. "
        "Provide output by calling the function."
    )
    user_prompt = (
        "Here are the top lines of a receipt:\n" + "\n".join(raw_text) + "\n\n"
        "Extract the merchant's name, address, and phone number."
    )

    # Call OpenAI with function calling
    response = openai_client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        functions=functions,
        function_call={"name": "inferMerchant"},
    )

    # Parse function call result
    message = response.choices[0].message
    result = {
        "merchant_name": "",
        "merchant_address": "",
        "merchant_phone": "",
        "confidence": 0.0,
    }
    if hasattr(message, "function_call") and message.function_call:
        args = message.function_call.arguments
        try:
            payload = json.loads(args)
            result.update(payload)
        except JSONDecodeError:
            pass

    return result


def retry_google_search_with_inferred_data(
    gpt_merchant_data: dict, google_places_api_key: str
) -> Optional[dict]:
    """
    Re-attempts a Google Places API search using GPT-inferred address or phone.

    Args:
        gpt_merchant_data (dict): Output from `infer_merchant_with_gpt(...)`, must include address and phone.
        google_places_api_key (str): API key for accessing Google Places.

    Returns:
        dict or None: Google match result or None.
    """
    places_api = PlacesAPI(google_places_api_key)

    phone = gpt_merchant_data.get("merchant_phone")
    if phone:
        match = places_api.search_by_phone(phone)
        if match and match.get("status") != "NO_RESULTS":
            return match

    address = gpt_merchant_data.get("merchant_address")
    if address:
        match = places_api.search_by_address(address)
        if match:
            return match

    return None


def write_receipt_metadata_to_dynamo(metadata: ReceiptMetadata) -> None:
    """
    Stores a ReceiptMetadata entity into DynamoDB using the DynamoDB client.

    Args:
        metadata (ReceiptMetadata): The metadata object to persist.
    """
    if metadata is None:
        raise ValueError("metadata cannot be None")
    if not isinstance(metadata, ReceiptMetadata):
        raise ValueError("metadata must be a ReceiptMetadata")
    dynamo_client.addReceiptMetadata(metadata)


def build_receipt_metadata_from_result(
    receipt_id: int,
    image_id: str,
    gpt_result: dict,
    google_place: dict,
    raw_receipt_fields: dict,
) -> ReceiptMetadata:
    """
    Builds a ReceiptMetadata object from a successful merchant match.

    Args:
        receipt_id (int): ID of the receipt.
        image_id (str): UUID of the image.
        gpt_result (dict): Output from infer_merchant_with_gpt or validation, may include fallback fields.
        google_place (dict): Google Places result.
        raw_receipt_fields (dict): Original extracted fields for context.

    Returns:
        ReceiptMetadata: The constructed metadata entity.
    """
    # Core fields from Google
    place_id = google_place.get("place_id", "")
    merchant_name = google_place.get("name", "")
    address = google_place.get("formatted_address", "")
    # Phone from Google or fallback to GPT
    phone = google_place.get("formatted_phone_number") or gpt_result.get(
        "merchant_phone", ""
    )

    # Confidence: prefer GPT confidence if provided, else default to 1.0
    match_confidence = (
        gpt_result.get("confidence")
        if gpt_result and "confidence" in gpt_result
        else 1.0
    )

    # Matched fields: from GPT validation result if present
    matched_fields = (
        gpt_result.get("matched_fields", [])
        if gpt_result and "matched_fields" in gpt_result
        else []
    )

    # Determine source of validation
    validated_by = "GooglePlaces"
    if gpt_result and matched_fields:
        validated_by = "GPT+GooglePlaces"

    # Basic reasoning
    reasoning = f"Selected merchant based on Google Places"
    if validated_by == "GPT+GooglePlaces":
        reasoning += " with GPT validation"

    # Optional category: use first Google type if available
    merchant_category = google_place.get("types", [None])[0] or ""

    return ReceiptMetadata(
        image_id=image_id,
        receipt_id=receipt_id,
        place_id=place_id,
        merchant_name=merchant_name,
        merchant_category=merchant_category,
        address=address,
        phone_number=phone,
        match_confidence=match_confidence,
        matched_fields=matched_fields,
        validated_by=validated_by,
        timestamp=datetime.now(timezone.utc),
        reasoning=reasoning,
    )


def build_receipt_metadata_from_result_no_match(
    receipt_id: int,
    image_id: str,
    raw_receipt_fields: dict,
    gpt_result: Optional[dict] = None,
) -> ReceiptMetadata:
    """
    Builds a ReceiptMetadata object for the no-match path when no valid merchant was identified.

    Args:
        receipt_id (int): ID of the receipt.
        image_id (str): UUID of the image.
        raw_receipt_fields (dict): Original extracted fields for context.
        gpt_result (dict, optional): Output from infer_merchant_with_gpt, may include fallback fields.

    Returns:
        ReceiptMetadata: The constructed metadata entity with status NO_MATCH.
    """
    # Use GPT inference if available, else leave blank
    merchant_name = gpt_result.get("merchant_name", "") if gpt_result else ""
    address = gpt_result.get("merchant_address", "") if gpt_result else ""
    phone = gpt_result.get("merchant_phone", "") if gpt_result else ""

    # Confidence and matched fields default to 0.0 and empty list
    match_confidence = gpt_result.get("confidence", 0.0) if gpt_result else 0.0
    matched_fields = gpt_result.get("matched_fields", []) if gpt_result else []

    # Determine validated_by source
    validated_by = "GPT" if gpt_result else "None"

    # Reasoning message
    if gpt_result:
        reasoning = (
            f"No valid Google Places match; used GPT inference with confidence "
            f"{match_confidence:.2f}"
        )
    else:
        reasoning = (
            "No valid Google Places match and no GPT inference was performed"
        )

    # Use empty placeholders for place_id and category
    place_id = ""
    merchant_category = ""

    return ReceiptMetadata(
        image_id=image_id,
        receipt_id=receipt_id,
        place_id=place_id,
        merchant_name=merchant_name,
        merchant_category=merchant_category,
        address=address,
        phone_number=phone,
        match_confidence=match_confidence,
        matched_fields=matched_fields,
        validated_by=validated_by,
        timestamp=datetime.now(timezone.utc),
        reasoning=reasoning,
    )
