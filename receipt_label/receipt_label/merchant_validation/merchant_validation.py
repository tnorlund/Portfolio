import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from json import JSONDecodeError
from typing import List, Optional, Tuple

from fuzzywuzzy import fuzz
from receipt_dynamo.entities import (
    Receipt,
    ReceiptLetter,
    ReceiptLine,
    ReceiptMetadata,
    ReceiptWord,
    ReceiptWordLabel,
    ReceiptWordTag,
)

from receipt_label.data.places_api import PlacesAPI
from receipt_label.utils import get_clients

dynamo_client, openai_client, _ = get_clients()

# Configurable timeout for OpenAI API calls
OPENAI_TIMEOUT_SECONDS = int(os.environ.get("OPENAI_TIMEOUT_SECONDS", 30))


def list_receipt_metadatas() -> List[ReceiptMetadata]:
    """
    Lists all receipt metadata entities from the DynamoDB table.
    """
    return dynamo_client.listReceiptMetadatas()[0]


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
    # Early reject Google results with empty name
    if not google_place.get("name"):
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
    # Normalize receipt_fields keys to ensure we have 'name', 'address', 'phone'
    normalized_fields = {
        "name": receipt_fields.get("name")
        or receipt_fields.get("merchant_name", ""),
        "address": receipt_fields.get("address")
        or receipt_fields.get("merchant_address", ""),
        "phone": receipt_fields.get("phone")
        or receipt_fields.get("merchant_phone", ""),
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
        "If the Google Places `name` appears to be a street address (e.g., contains numbers and street suffixes), treat it as an address rather than a business name. "
        "You use string similarity and common sense, and explain your decision clearly."
    )

    user_prompt = f""" 
    Compare the following two merchant records and decide whether they match.
 
    📄 Extracted from receipt:
    - Name: {normalized_fields['name']}
    - Address: {normalized_fields['address']}
    - Phone: {normalized_fields['phone']}
 
    📍 From Google Places:
    - Name: {google_place.get('name')}
    - Address: {google_place.get('formatted_address')}
    - Phone: {google_place.get('formatted_phone_number')}
 
    Note: if the 'Name' field appears to be an address rather than a business name, focus matching on the Address field.
 
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
        timeout=OPENAI_TIMEOUT_SECONDS,
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
            phone_field = normalized_fields.get("phone", "")
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
                field_matches.append("phone")
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


def query_google_places(
    extracted_dict: dict,
    google_places_api_key: str,
    all_receipt_words: List[ReceiptWord] = None,
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
        # Pass full receipt word list for business-name text search
        wrapped_words = [{"text": w.text} for w in (all_receipt_words or [])]
        address_match = places_api.search_by_address(address, wrapped_words)
        if address_match:
            return address_match

        # 3. No match found
        return None


def is_match_found(results: Optional[dict]) -> bool:
    """
    Checks whether the Google Places API query returned a valid business match.
    Returns True only if:
      - results is not None,
      - contains a non-empty 'name' and 'place_id',
      - and its types do not indicate an address-only result.
    """
    if not results:
        return False

    # Must have both a name and a place_id
    name = results.get("name")
    pid = results.get("place_id")
    if not name or not pid:
        return False

    # Exclude address-only place types
    address_only_types = {
        "street_address",
        "postal_code",
        "subpremise",
        "premise",
    }
    types = set(results.get("types", []))
    if types & address_only_types:
        return False

    return True


def is_valid_google_match(results, extracted_data):
    """
    Determines whether the Google Places API result is a valid match
    for the merchant fields you extracted.

    Args:
        results (dict or None): The dict returned by query_google_places, or None.
        extracted_data (dict): Your extracted merchant fields (e.g. name, phone).

    Returns:
        bool: True if this place is a valid match, False otherwise.
    """

    # Return False if Google Places API returned no results
    if results is None:
        return False

    # Assuming `results` is the place dict itself
    place = results

    # Reject address-only results based on Google Place types
    address_only_types = {
        "street_address",
        "postal_code",
        "subpremise",
        "premise",
    }
    place_types = set(place.get("types", []))
    if place_types & address_only_types:
        return False

    # Must have a place_id and an address
    if not place.get("place_id") or not place.get("formatted_address"):
        return False

    # If you extracted a phone number, compare it
    extracted_phone = extracted_data.get("phone_number")
    place_phone = place.get("formatted_phone_number")
    if extracted_phone and place_phone:
        # You might already have a normalize_phone() helper
        if normalize_phone(extracted_phone) != normalize_phone(place_phone):
            return False

    # If you extracted a business name, compare it (e.g. via fuzzy matching)
    extracted_name = extracted_data.get("business_name")
    place_name = place.get("name")
    if extracted_name and place_name:
        if not fuzzy_match(place_name, extracted_name):
            return False

    # (Any other checks you had—address similarity, category check, etc.—go here)

    return True


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
                    "decision": {
                        "type": "string",
                        "enum": ["YES", "NO"],
                        "description": "Whether the inference is confident enough to use.",
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
        "Provide output by calling the function. Return a 'decision' of YES if confidence ≥ 0.75, otherwise NO."
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
        timeout=OPENAI_TIMEOUT_SECONDS,
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
    Re-attempt the Google Places API search using GPT-inferred data with strict validation.

    The search steps are:
      1. Phone-based lookup: if a phone is provided, search by phone and return the first result
         that passes both `is_match_found` and `is_valid_google_match`.
      2. Nearby business search: if an address is provided, geocode to lat/lng and perform a
         nearby search (radius 50m), returning the first candidate that validates.
      3. Text-based search: if a merchant name is provided, perform a text search with optional
         location bias, returning the first result that validates.

    Each candidate is validated using `is_match_found` (requiring non-empty name/place_id
    and not an address-only type) and `is_valid_google_match` (comparing extracted fields).

    Args:
        gpt_merchant_data (dict): GPT inference output, containing keys
            "merchant_phone", "merchant_address", and "merchant_name".
        google_places_api_key (str): API key for Google Places access.

    Returns:
        dict or None: The validated Google Places result, or None if no valid match is found.
    """
    places_api = PlacesAPI(google_places_api_key)

    # 1) Phone-based retry, validate the match
    phone = gpt_merchant_data.get("merchant_phone")
    if phone:
        match = places_api.search_by_phone(phone)
        if (
            match
            and is_match_found(match)
            and is_valid_google_match(match, gpt_merchant_data)
        ):
            return match

    # 2) Geocode address and nearby search
    address = gpt_merchant_data.get("merchant_address")
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
    name = gpt_merchant_data.get("merchant_name")
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

    # No valid retry found
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
) -> ReceiptMetadata:
    """
    Builds a ReceiptMetadata object from a successful merchant match.

    Args:
        receipt_id (int): ID of the receipt.
        image_id (str): UUID of the image.
        gpt_result (dict): Output from infer_merchant_with_gpt or validation, may include fallback fields.
        google_place (dict): Google Places result.

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
    Builds a ReceiptMetadata object for the no-match path when no valid merchant was identified.

    Args:
        receipt_id (int): ID of the receipt.
        image_id (str): UUID of the image.
        gpt_result (dict, optional): Output from infer_merchant_with_gpt, may include fallback fields.

    Returns:
        ReceiptMetadata: The constructed metadata entity with status NO_MATCH.
    """
    # Use GPT inference if available, else leave blank
    merchant_name = gpt_result.get("merchant_name", "") if gpt_result else ""
    address = gpt_result.get("merchant_address", "") if gpt_result else ""
    phone = gpt_result.get("merchant_phone", "") if gpt_result else ""

    matched_fields = gpt_result.get("matched_fields", []) if gpt_result else []

    # Determine validated_by source
    validated_by = "GPT" if gpt_result else "None"

    # Reasoning message
    if gpt_result:
        reasoning = "No valid Google Places match; used GPT inference"
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
        matched_fields=matched_fields,
        validated_by=validated_by,
        timestamp=datetime.now(timezone.utc),
        reasoning=reasoning,
    )


# --- Normalization Functions (from merchant_clustering.py) ---


def normalize_text(text):
    """Applies standard normalization for canonical display."""
    if not isinstance(text, str):
        return ""
    text = text.lower()
    # Remove redundant whitespace
    text = " ".join(text.split())
    return text


def normalize_address(address):
    """Applies more specific normalization for addresses."""
    if not isinstance(address, str):
        return ""
    address = address.lower()
    # Standardize street types
    address = re.sub(r"\bst\.?\b", "street", address)
    address = re.sub(r"\bave\.?\b", "avenue", address)
    address = re.sub(r"\bblvd\.?\b", "boulevard", address)
    address = re.sub(r"\brd\.?\b", "road", address)
    address = re.sub(r"\bdr\.?\b", "drive", address)
    address = re.sub(r"\bln\.?\b", "lane", address)
    address = re.sub(r"\bct\.?\b", "court", address)
    address = re.sub(r"\bsq\.?\b", "square", address)
    # Standardize state abbreviations (add more as needed)
    address = re.sub(
        r"\bca\b", "ca", address
    )  # Keep lowercase for consistency
    address = re.sub(r"\bmd\b", "md", address)
    address = re.sub(r"\bmn\b", "mn", address)
    # Remove punctuation
    address = re.sub(r'[.,!?;:\'"()]', "", address)
    # Standardize country
    address = re.sub(r"\busa\b", "usa", address)
    address = re.sub(r"\bunited states\b", "usa", address)
    # Remove extra whitespace
    address = " ".join(address.split())
    return address


def preprocess_for_comparison(text):
    """Minimal preprocessing used only for similarity checks."""
    if not isinstance(text, str):
        return ""
    text = text.lower()
    text = re.sub(r'[.,!?;:\'"()]', "", text)
    text = " ".join(text.split())
    return text


# --- Similarity Functions (from merchant_clustering.py) ---


def format_canonical_merchant_name(name: str) -> str:
    name = name.strip().title()
    name = re.sub(r"\s*-\s*", " ", name)  # Remove dashes surrounded by space
    name = re.sub(r"\s+", " ", name)
    return name


def get_name_similarity(name1, name2):
    p_name1 = preprocess_for_comparison(name1)
    p_name2 = preprocess_for_comparison(name2)
    if not p_name1 or not p_name2:
        return 0
    return fuzz.token_set_ratio(p_name1, p_name2)


def get_address_similarity(addr1, addr2):
    p_addr1 = normalize_address(addr1)
    p_addr2 = normalize_address(addr2)
    if not p_addr1 or not p_addr2:
        return 0
    return fuzz.token_set_ratio(p_addr1, p_addr2)


def get_phone_similarity(ph1, ph2):
    p_ph1 = re.sub(r"\D", "", ph1) if ph1 else ""
    p_ph2 = re.sub(r"\D", "", ph2) if ph2 else ""
    if not p_ph1 or not p_ph2 or len(p_ph1) < 7 or len(p_ph2) < 7:
        return 0
    return 100 if p_ph1 == p_ph2 else 0


# --- Clustering Logic (from merchant_clustering.py) ---


def cluster_by_metadata(metadata_list: List[ReceiptMetadata]):
    # (Copy the exact implementation of cluster_by_metadata here)
    clusters_by_place_id = defaultdict(list)
    records_without_place_id = []

    # Pass 1: Group by place_id
    for record in metadata_list:
        place_id = getattr(record, "place_id", None)
        if place_id and isinstance(place_id, str) and place_id.strip():
            clusters_by_place_id[place_id.strip()].append(record)
        else:
            records_without_place_id.append(record)

    final_clusters = list(clusters_by_place_id.values())

    # Pass 2: Cluster remaining records by name + address/phone
    processed_indices = set()
    remaining_clusters = []
    for i in range(len(records_without_place_id)):
        if i in processed_indices:
            continue
        current_cluster = [records_without_place_id[i]]
        processed_indices.add(i)
        for j in range(i + 1, len(records_without_place_id)):
            if j in processed_indices:
                continue
            record1 = records_without_place_id[i]
            record2 = records_without_place_id[j]
            name_sim = get_name_similarity(
                normalize_text(record1.merchant_name),
                normalize_text(record2.merchant_name),
            )
            if name_sim < 90:
                continue
            addr_sim = get_address_similarity(record1.address, record2.address)
            phone_sim = get_phone_similarity(
                record1.phone_number, record2.phone_number
            )
            if addr_sim >= 85 or phone_sim == 100:
                current_cluster.append(record2)
                processed_indices.add(j)
        remaining_clusters.append(current_cluster)

    final_clusters.extend(remaining_clusters)
    return final_clusters


# Source validation priority scoring for canonical record selection
SOURCE_PRIORITY = {
    "ADDRESS_LOOKUP": 5,
    "NEARBY_LOOKUP": 4,
    "TEXT_SEARCH": 3,
    "PHONE_LOOKUP": 2,
    "": 0,  # Handle empty string
    None: 0,  # Handle None
}
DEFAULT_PRIORITY = 1  # For any other source


def get_score(record):
    """
    Calculate a priority score for a ReceiptMetadata record to determine which should be
    the canonical representation in a cluster.

    Scoring is based on:
    1. Source validation method (ADDRESS_LOOKUP > NEARBY_LOOKUP > TEXT_SEARCH > PHONE_LOOKUP)
    2. Match confidence score
    3. Presence of address
    4. Presence of phone number
    5. Shorter merchant names preferred (less likely to have extra information)

    Returns:
        tuple: A tuple of scores for sorting, with higher values being preferred
    """
    validated_by = getattr(record, "validated_by", None)
    source_score = SOURCE_PRIORITY.get(validated_by, DEFAULT_PRIORITY)
    confidence_score = getattr(record, "match_confidence", 0.0)
    has_address = bool(getattr(record, "address", ""))
    has_phone = bool(getattr(record, "phone_number", ""))
    name_len_score = -len(normalize_text(getattr(record, "merchant_name", "")))
    return (
        source_score,
        confidence_score,
        has_address,
        has_phone,
        name_len_score,
    )


def choose_canonical_metadata(cluster_members: List[ReceiptMetadata]):
    """
    Choose the best record from a cluster to use as the canonical representation.

    Args:
        cluster_members: List of ReceiptMetadata records in the cluster

    Returns:
        ReceiptMetadata: The best record to use as canonical, or None if cluster is empty
    """
    if not cluster_members:
        return None

    # Prefer records with place_id
    with_place_id = [
        m for m in cluster_members if getattr(m, "place_id", None)
    ]
    without_place_id = [
        m for m in cluster_members if not getattr(m, "place_id", None)
    ]
    candidates = with_place_id if with_place_id else without_place_id

    if not candidates:
        return None

    # Use get_score function to find best record
    best_record = max(candidates, key=get_score)
    return best_record


# --- DynamoDB Interaction ---


def list_all_receipt_metadatas() -> (
    List[ReceiptMetadata]
):  # Renamed and updated
    """Lists ALL receipt metadata entities from DynamoDB, handling pagination."""
    all_metadatas = []
    lek = None
    while True:
        metadatas_page, lek = dynamo_client.listReceiptMetadatas(
            limit=1000, lastEvaluatedKey=lek
        )  # Use limit=1000 for efficiency
        all_metadatas.extend(metadatas_page)
        if not lek:
            break
    return all_metadatas


def update_items_with_canonical(
    cluster_members: List[ReceiptMetadata], canonical_details: dict
):
    """
    Updates all items in a cluster with the canonical details using ReceiptMetadata
    methods and dynamo_client update functions.

    Args:
        cluster_members: List of ReceiptMetadata records to update
        canonical_details: Dictionary with canonical values to set
                          ('canonical_place_id', 'canonical_merchant_name',
                           'canonical_address', 'canonical_phone_number')

    Returns:
        int: Count of successfully updated records
    """
    # Validate the canonical_details dictionary contains required fields
    required_keys = [
        "canonical_place_id",
        "canonical_merchant_name",
        "canonical_address",
        "canonical_phone_number",
    ]

    for key in required_keys:
        if key not in canonical_details:
            print(f"Warning: Missing required key in canonical_details: {key}")
            canonical_details[key] = ""

    updated_count = 0
    failed_count = 0
    updated_records = []

    # First update in-memory objects
    for record in cluster_members:
        try:
            # Update the in-memory ReceiptMetadata object
            record.canonical_place_id = canonical_details.get(
                "canonical_place_id", ""
            )
            record.canonical_merchant_name = canonical_details.get(
                "canonical_merchant_name", ""
            )
            record.canonical_address = canonical_details.get(
                "canonical_address", ""
            )
            record.canonical_phone_number = canonical_details.get(
                "canonical_phone_number", ""
            )

            if "cluster_id" in canonical_details:
                record.cluster_id = canonical_details["cluster_id"]

            updated_records.append(record)

        except Exception as e:
            failed_count += 1
            print(
                f"Error preparing record {record.image_id}/{record.receipt_id} for update: {e}"
            )

    # Now update in DynamoDB in batches
    if updated_records:
        try:
            # Use the batch update method for efficiency
            if len(updated_records) > 1:
                dynamo_client.updateReceiptMetadatas(updated_records)
            else:
                # Use single update for just one record
                dynamo_client.updateReceiptMetadata(updated_records[0])

            updated_count = len(updated_records)

        except Exception as e:
            print(f"Error batch updating records in DynamoDB: {e}")

            # Fall back to individual updates if batch update fails
            for record in updated_records:
                try:
                    dynamo_client.updateReceiptMetadata(record)
                    updated_count += 1
                except Exception as inner_e:
                    failed_count += 1
                    print(
                        f"Error updating item {record.image_id}/{record.receipt_id}: {inner_e}"
                    )

    if failed_count > 0:
        print(
            f"Warning: Failed to update {failed_count} out of {len(cluster_members)} items"
        )

    return updated_count


def query_records_by_place_id(place_id: str) -> list[ReceiptMetadata]:
    """
    Query DynamoDB for records with the given place_id that have been
    previously canonicalized (have canonical_* fields filled).

    Uses dynamo_client.listReceiptMetadatasWithPlaceId which leverages GSI2 for efficient place_id queries.

    Args:
        place_id (str): The Google Places ID to search for

    Returns:
        List[ReceiptMetadata]: List of matching records with canonical fields
    """
    if not place_id or not isinstance(place_id, str):
        return []

    try:
        # Use the listReceiptMetadatasWithPlaceId function which uses GSI2 internally
        metadatas, _ = dynamo_client.listReceiptMetadatasWithPlaceId(
            place_id=place_id,
            limit=10,  # Reasonable limit - we just need one with canonical fields
        )

        # Filter to only include records that have canonical fields
        return [
            metadata for metadata in metadatas if metadata.canonical_place_id
        ]

    except Exception as e:
        logger.error(f"Error querying by place_id {place_id}: {e}")
        return []


def collapse_canonical_aliases(
    records: List[ReceiptMetadata],
) -> List[ReceiptMetadata]:
    """
    Collapses near-duplicate canonical merchant names within the same place_id.

    Args:
        records: List of ReceiptMetadata records already canonicalized

    Returns:
        List[ReceiptMetadata]: Records whose canonical names were modified
    """

    updated_records = []
    grouped = defaultdict(list)
    for rec in records:
        pid = getattr(rec, "canonical_place_id", "")
        if pid:
            grouped[pid].append(rec)

    for pid, group in grouped.items():
        name_counter = Counter()
        name_map = {}
        for rec in group:
            name = getattr(rec, "canonical_merchant_name", "").strip()
            if name:
                key = (rec.image_id, rec.receipt_id)
                name_map[key] = (rec, name)
                name_counter[name] += 1

        if not name_counter:
            continue

        # Pick the most common name, or shortest if tied
        preferred = sorted(
            name_counter.items(), key=lambda x: (-x[1], len(x[0]))
        )[0][0]

        for _, (rec, current_name) in name_map.items():
            if current_name != preferred:
                rec.canonical_merchant_name = preferred
                updated_records.append(rec)

    return updated_records


# --- Alias and Clustering Utilities ---


def merge_place_id_aliases_by_address(records: List[ReceiptMetadata]) -> int:
    """
    Merges place_ids that point to the same canonical address and similar merchant names.

    Args:
        records: List of ReceiptMetadata records

    Returns:
        int: Number of records that had their canonical place_id and name updated
    """
    updates = 0
    grouped_by_address = defaultdict(list)

    # Group by normalized canonical address
    for rec in records:
        addr = getattr(rec, "canonical_address", "").strip().lower()
        if addr:
            grouped_by_address[addr].append(rec)

    for group in grouped_by_address.values():
        if len(group) < 2:
            continue

        place_ids = [
            r.canonical_place_id for r in group if r.canonical_place_id
        ]
        names = [
            r.canonical_merchant_name
            for r in group
            if r.canonical_merchant_name
        ]

        if not place_ids or not names:
            continue

        preferred_pid = Counter(place_ids).most_common(1)[0][0]
        preferred_name = Counter(names).most_common(1)[0][0]

        for rec in group:
            if rec.canonical_place_id != preferred_pid:
                rec.canonical_place_id = preferred_pid
                updates += 1
            if rec.canonical_merchant_name != preferred_name:
                rec.canonical_merchant_name = preferred_name
                updates += 1

    return updates


def persist_alias_updates(records: List[ReceiptMetadata]):
    """
    Persists alias updates to DynamoDB.

    Args:
        records: List of ReceiptMetadata records with updated canonical merchant names
    """
    if not records:
        return

    try:
        # Use the batch update method for efficiency
        dynamo_client.updateReceiptMetadatas(records)
    except Exception as e:
        logger.error(f"Error persisting alias updates: {e}")
