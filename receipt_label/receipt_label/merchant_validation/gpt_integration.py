"""OpenAI GPT integration for merchant validation."""

import json
import logging
import os
from json import JSONDecodeError
from typing import Any, Dict, List

from openai import OpenAIError
from receipt_label.utils import get_client_manager
from receipt_label.utils.client_manager import ClientManager

# Initialize logger
logger = logging.getLogger(__name__)

# Configurable timeout for OpenAI API calls
OPENAI_TIMEOUT_SECONDS = int(os.environ.get("OPENAI_TIMEOUT_SECONDS", 30))


def validate_match_with_gpt(
    receipt_fields: Dict[str, Any],
    google_place: Dict[str, Any],
    client_manager: ClientManager = None,
) -> Dict[str, Any]:
    """
    Uses GPT function calling to determine if the Google Place result matches the extracted receipt fields.

    Leverages GPT's reasoning capabilities to perform intelligent matching that goes
    beyond simple field comparisons, considering context and business logic.

    Args:
        receipt_fields: Extracted name, address, phone from the receipt.
                       Expected keys: 'name', 'address', 'phone_number'
        google_place: Google Places API result with 'name', 'formatted_address',
                     and 'formatted_phone_number'

    Returns:
        Dictionary containing:
            - decision: "YES" | "NO" | "UNSURE"
            - confidence: float (0.0 to 1.0)
            - matched_fields: List[str] of fields that matched
            - reason: str explaining the decision

    Raises:
        ValueError: If input parameters are invalid
        OpenAIError: If GPT API calls fail

    Example:
        >>> receipt = {'name': 'Starbucks', 'phone_number': '555-0123'}
        >>> google = {'name': 'Starbucks Coffee', 'formatted_phone_number': '+1 555-0123'}
        >>> result = validate_match_with_gpt(receipt, google)
        >>> print(f"Decision: {result['decision']}, Confidence: {result['confidence']}")
    """
    # Input validation
    if not receipt_fields or not isinstance(receipt_fields, dict):
        raise ValueError("receipt_fields must be a non-empty dictionary")
    if not google_place or not isinstance(google_place, dict):
        raise ValueError("google_place must be a non-empty dictionary")

    # Early reject Google results with empty name
    if not google_place.get("name"):
        logger.debug("Rejecting Google result with empty name")
        return {
            "decision": "NO",
            "confidence": 0.0,
            "matched_fields": [],
            "reason": "Empty Google Places name; treating as no match",
        }
    # Reject address-only place types before GPT validation
    address_only_types = {
        "street_address",
        "postal_code",
        "subpremise",
        "premise",
    }
    place_types = set(google_place.get("types", []))
    if place_types & address_only_types:
        return {
            "decision": "NO",
            "confidence": 0.0,
            "matched_fields": [],
            "reason": "Rejected address-only place type: "
            + ", ".join(sorted(place_types & address_only_types)),
        }
    # Normalize receipt_fields keys to ensure we have 'name', 'address', 'phone_number'
    normalized_fields = {
        "name": receipt_fields.get("name", ""),
        "address": receipt_fields.get("address", ""),
        "phone_number": receipt_fields.get("phone_number", ""),
    }
    # Confidence threshold for field-based matching
    CONFIDENCE_THRESHOLD = 0.70
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
                            "enum": ["name", "address", "phone_number"],
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
        "If the Google Places `name` appears to be a street address (e.g., contains numbers and street suffixes), treat it as an address rather than a business name. "
        "You use string similarity and common sense, and explain your decision clearly."
    )

    user_prompt = f"""
    Compare the following two merchant records and decide whether they match.

    📄 Extracted from receipt:
    - Name: {normalized_fields['name']}
    - Address: {normalized_fields['address']}
    - Phone: {normalized_fields['phone_number']}

    📍 From Google Places:
    - Name: {google_place.get('name')}
    - Address: {google_place.get('formatted_address')}
    - Phone: {google_place.get('formatted_phone_number')}

    Note: if the 'Name' field appears to be an address rather than a business name, focus matching on the Address field.

    Only return structured output by calling the `validateMatch` function.
    """

    if client_manager is None:
        client_manager = get_client_manager()

    response = client_manager.openai.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        functions=functions,
        function_call={"name": "validateMatch"},
        timeout=OPENAI_TIMEOUT_SECONDS,
    )

    # Default result structure
    result = {
        "decision": "UNSURE",
        "confidence": 0.0,
        "matched_fields": [],
        "reason": "No function call received from GPT",
    }

    # Parse GPT response
    message = response.choices[0].message
    if hasattr(message, "function_call") and message.function_call:
        try:
            args = json.loads(message.function_call.arguments)
            result.update(args)
            # Generic field-based matching: 2-of-3 or 1-of-3 + confidence threshold
            field_matches = []
            # Name match
            if (
                normalized_fields["name"]
                and normalized_fields["name"].lower()
                in google_place.get("name", "").lower()
            ):
                field_matches.append("name")
            # Phone match
            google_phone = google_place.get("formatted_phone_number", "") or ""

            # Normalize receipt phone field to a string, handling list inputs
            phone_field = normalized_fields.get("phone_number", "")
            if isinstance(phone_field, list):
                receipt_phone_str = "".join(str(p) for p in phone_field)
            else:
                receipt_phone_str = str(phone_field)
            # Strip spaces and hyphens
            receipt_phone_str = receipt_phone_str.replace(" ", "").replace(
                "-", ""
            )

            # Normalize Google phone
            cleaned_google_phone = google_phone.replace(" ", "").replace(
                "-", ""
            )

            # Compare normalized phone strings
            if receipt_phone_str and receipt_phone_str == cleaned_google_phone:
                field_matches.append("phone_number")
            # Address match: ignore numeric differences, focus on street name tokens
            google_addr = google_place.get("formatted_address", "").lower()

            # Normalize receipt address field to a string, handling list inputs
            address_field = normalized_fields.get("address", "")
            if isinstance(address_field, list):
                address_str = " ".join(str(a) for a in address_field)
            else:
                address_str = str(address_field)
            # Lowercase and tokenize
            address_str = address_str.lower()
            addr_tokens = address_str.split()
            alpha_tokens = [
                tok for tok in addr_tokens if any(c.isalpha() for c in tok)
            ]
            if alpha_tokens and all(
                tok in google_addr for tok in alpha_tokens
            ):
                field_matches.append("address")

            # Apply override rules
            if len(field_matches) >= 2 or (
                len(field_matches) == 1
                and result["confidence"] >= CONFIDENCE_THRESHOLD
            ):
                result["decision"] = "YES"
                result["matched_fields"] = field_matches
                # Boost confidence if only one field matched
                if len(field_matches) == 1:
                    result["confidence"] = max(
                        result["confidence"], CONFIDENCE_THRESHOLD
                    )
                result["reason"] = (
                    f"Validated by field matching: {field_matches}"
                )
        except JSONDecodeError:
            pass

    return result


def infer_merchant_with_gpt(
    raw_text: List[str],
    extracted_dict: dict,
    client_manager: ClientManager = None,
) -> dict:
    """
    Uses ChatGPT function calling to infer merchant metadata when no Google match is found.

    Args:
        raw_text (List[str]): OCR'd receipt lines.
        extracted_dict (dict): Extracted address/phone/url ReceiptWord lists.

    Returns:
        dict: {
            "name": str,
            "address": str,
            "phone_number": str,
            "confidence": float,
            "decision": str
        }
    """
    # Define the function schema for GPT
    functions = [
        {
            "name": "inferMerchant",
            "description": "Infer merchant info and indicate whether the confidence is sufficient to accept the inference.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The business name inferred from the receipt.",
                    },
                    "address": {
                        "type": "string",
                        "description": "The business address inferred from the receipt.",
                    },
                    "phone_number": {
                        "type": "string",
                        "description": "The business phone number inferred from the receipt.",
                    },
                    "confidence": {
                        "type": "number",
                        "description": "Confidence score between 0 and 1.",
                    },
                    "decision": {
                        "type": "string",
                        "enum": ["YES", "NO"],
                        "description": "Whether the inference is confident enough to use.",
                    },
                },
                "required": [
                    "name",
                    "address",
                    "phone_number",
                    "confidence",
                ],
            },
        }
    ]

    # Construct messages
    system_prompt = (
        "You are an assistant that infers merchant information from OCR'd receipt text. "
        "Provide output by calling the function. Return a 'decision' of YES if confidence ≥ 0.75, otherwise NO."
    )
    user_prompt = (
        "Here are the top lines of a receipt:\n" + "\n".join(raw_text) + "\n\n"
        "Extract the merchant's name, address, and phone number."
    )

    # Call OpenAI with function calling
    if client_manager is None:
        client_manager = get_client_manager()

    response = client_manager.openai.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        functions=functions,
        function_call={"name": "inferMerchant"},
        timeout=OPENAI_TIMEOUT_SECONDS,
    )

    # Parse function call result
    message = response.choices[0].message
    result = {
        "name": "",
        "address": "",
        "phone_number": "",
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
