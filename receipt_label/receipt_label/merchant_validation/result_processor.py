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
    Handles complex cases including nested quotes, mixed quote types, and malformed JSON.
    
    Args:
        value: The string to sanitize
        
    Returns:
        Sanitized string
    """
    import json
    import re
    
    if not isinstance(value, str):
        return str(value) if value is not None else ""
    
    # Store original for fallback
    original_value = value
    
    try:
        # First, try to decode as JSON if it looks like a JSON string
        if value.startswith('"') and value.endswith('"') and len(value) > 1:
            try:
                decoded = json.loads(value)
                if isinstance(decoded, str):
                    value = decoded
            except (json.JSONDecodeError, ValueError):
                # Not valid JSON, continue with other methods
                pass
        
        # Handle multiple levels of quoting (e.g., '""text""' or '"\'text\'"')
        max_iterations = 3  # Prevent infinite loops
        iteration = 0
        while iteration < max_iterations and len(value) >= 2:
            # Check for matching quotes at start and end
            if (value.startswith('"') and value.endswith('"')) or \
               (value.startswith("'") and value.endswith("'")):
                inner = value[1:-1]
                quote_char = value[0]
                
                # Count unescaped quotes in the inner content
                # Use regex to find unescaped quotes
                escaped_quote_pattern = re.escape(f'\\{quote_char}')
                unescaped_quotes = re.sub(escaped_quote_pattern, '', inner)
                
                # Only strip if there are no unmatched quotes inside
                if quote_char not in unescaped_quotes:
                    value = inner
                    iteration += 1
                else:
                    # Found unmatched quotes, stop stripping
                    break
            else:
                # No matching quotes, stop
                break
        
        # Handle mixed quote types (e.g., '"text' or 'text")
        # Only strip if it's clearly a quoting error
        if len(value) >= 2:
            start_quotes = ["'", '"', '"', '"', ''', ''']  # Include unicode quotes
            end_quotes = ["'", '"', '"', '"', ''', ''']
            
            for sq in start_quotes:
                for eq in end_quotes:
                    if value.startswith(sq) and value.endswith(eq):
                        # Strip mismatched quotes
                        value = value[len(sq):-len(eq)]
                        break
        
        # Clean up escaped quotes
        value = value.replace(r'\"', '"').replace(r"\'", "'")
        
        # Strip extra whitespace
        value = value.strip()
        
        # Final validation: if the result is empty or just whitespace, 
        # return the original (minus outer whitespace)
        if not value:
            return original_value.strip()
        
        return value
        
    except Exception as e:
        # If anything goes wrong, log and return the original trimmed value
        logger.warning(f"Error sanitizing string: {e}")
        return original_value.strip()