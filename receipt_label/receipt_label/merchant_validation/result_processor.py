"""
Process and extract best matches from merchant validation results.

This module handles partial results from failed agent attempts and
extracts the best possible merchant match.
"""
import logging
from typing import Any, Dict, List, Optional

from receipt_dynamo.constants import ValidationMethod
from receipt_dynamo.entities.receipt_metadata import ReceiptMetadata
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def extract_best_partial_match(
    partial_results: List[Dict[str, Any]], 
    user_input: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """
    Extract the best match from partial results when agent fails.
    
    Prioritizes results in order:
    1. Phone lookup results (most reliable)
    2. Address lookup results  
    3. Text search results
    
    Args:
        partial_results: List of partial results from failed agent attempts
        user_input: The original user input with extracted data
        
    Returns:
        Dict with best match data or None if no usable results
    """
    if not partial_results:
        return None
    
    # Group results by function type
    phone_results = []
    address_results = []
    text_results = []
    
    for result in partial_results:
        if result.get("function") == "search_by_phone" and result.get("result"):
            phone_results.append(result["result"])
        elif result.get("function") == "search_by_address" and result.get("result"):
            address_results.append(result["result"])
        elif result.get("function") == "search_by_text" and result.get("result"):
            text_results.append(result["result"])
    
    # Check phone results first (most reliable)
    for result in phone_results:
        if result.get("place_id") and result.get("name"):
            return {
                "place_id": result.get("place_id", ""),
                "merchant_name": result.get("name", ""),
                "address": result.get("formatted_address", ""),
                "phone_number": result.get("formatted_phone_number", ""),
                "source": "phone_lookup",
                "matched_fields": ["phone"]  # We know phone matched
            }
    
    # Then check address results
    for result in address_results:
        if result.get("place_id") and result.get("name"):
            return {
                "place_id": result.get("place_id", ""),
                "merchant_name": result.get("name", ""),
                "address": result.get("formatted_address", ""),
                "phone_number": result.get("formatted_phone_number", ""),
                "source": "address_lookup",
                "matched_fields": ["address"]  # We know address matched
            }
    
    # Finally check text search results
    for result in text_results:
        if result.get("place_id") and result.get("name"):
            return {
                "place_id": result.get("place_id", ""),
                "merchant_name": result.get("name", ""),
                "address": result.get("formatted_address", ""),
                "phone_number": result.get("formatted_phone_number", ""),
                "source": "text_search",
                "matched_fields": []  # Text search is less certain
            }
    
    return None


def build_receipt_metadata_from_partial_result(
    image_id: str,
    receipt_id: int, 
    partial_match: Dict[str, Any],
    raw_text: List[str]
) -> ReceiptMetadata:
    """
    Build ReceiptMetadata from a partial result when agent fails.
    
    Args:
        image_id: Image UUID
        receipt_id: Receipt ID
        partial_match: Best partial match extracted from failed attempts
        raw_text: Original receipt text
        
    Returns:
        ReceiptMetadata object with partial data
    """
    # Map source to ValidationMethod
    source_to_method = {
        "phone_lookup": ValidationMethod.PHONE_LOOKUP,
        "address_lookup": ValidationMethod.ADDRESS_LOOKUP,
        "text_search": ValidationMethod.TEXT_SEARCH
    }
    
    validated_by = source_to_method.get(
        partial_match.get("source", ""), 
        ValidationMethod.INFERENCE
    )
    
    return ReceiptMetadata(
        image_id=image_id,
        receipt_id=receipt_id,
        place_id=partial_match.get("place_id", ""),
        merchant_name=partial_match.get("merchant_name", ""),
        address=partial_match.get("address", ""),
        phone_number=partial_match.get("phone_number", ""),
        merchant_category="",  # Not available in partial results
        matched_fields=partial_match.get("matched_fields", []),
        timestamp=datetime.now(timezone.utc),
        validated_by=validated_by,
        reasoning=f"Extracted from partial {partial_match.get('source', 'unknown')} results after agent failure"
    )


def sanitize_metadata_strings(metadata: Dict[str, Any]) -> Dict[str, Any]:
    """
    Sanitize string values in metadata dictionary.
    
    Args:
        metadata: Raw metadata dictionary
        
    Returns:
        Sanitized metadata dictionary
    """
    sanitized = metadata.copy()
    
    string_fields = [
        "place_id", "merchant_name", "address", 
        "phone_number", "merchant_category", "reasoning"
    ]
    
    for field in string_fields:
        if field in sanitized:
            sanitized[field] = sanitize_string(sanitized[field])
    
    return sanitized


def sanitize_string(value: str) -> str:
    """
    Safely sanitize string values by removing quotes and extra whitespace.
    
    Args:
        value: The string to sanitize
        
    Returns:
        Sanitized string
    """
    import json
    
    if not isinstance(value, str):
        return str(value) if value is not None else ""
    
    # Handle potential JSON escaping
    try:
        if value.startswith('"') and value.endswith('"') and len(value) > 1:
            decoded = json.loads(value)
            if isinstance(decoded, str):
                value = decoded
    except (json.JSONDecodeError, ValueError):
        pass
    
    # Remove leading/trailing quotes
    if len(value) >= 2:
        if (value.startswith('"') and value.endswith('"')) or \
           (value.startswith("'") and value.endswith("'")):
            inner = value[1:-1]
            quote_char = value[0]
            # Only strip if the inner content doesn't have unescaped quotes
            if quote_char not in inner.replace(f'\\{quote_char}', ''):
                value = inner
    
    return value.strip()