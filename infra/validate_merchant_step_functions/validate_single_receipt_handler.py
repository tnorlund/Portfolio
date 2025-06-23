MAX_AGENT_ATTEMPTS = 2
AGENT_TIMEOUT_SECONDS = 300  # 5 minutes per agent attempt
import enum
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from datetime import datetime, timezone
from logging import INFO, Formatter, StreamHandler, getLogger
from typing import Any, Dict, List, Literal, Optional

from agents import Agent, Runner, function_tool
from receipt_dynamo.constants import ValidationMethod
from receipt_dynamo.entities.receipt_metadata import ReceiptMetadata
from receipt_label.merchant_validation import (
    build_receipt_metadata_from_result,
    build_receipt_metadata_from_result_no_match,
    extract_candidate_merchant_fields,
    get_receipt_details,
    infer_merchant_with_gpt,
    is_match_found,
    is_valid_google_match,
    query_google_places,
    retry_google_search_with_inferred_data,
    validate_match_with_gpt,
    write_receipt_metadata_to_dynamo,
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


# Final metadata return tool
@function_tool
def tool_return_metadata(
    place_id: str,
    merchant_name: str,
    address: str,
    phone_number: str,
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
def tool_search_nearby(
    lat: float, lng: float, radius: float
) -> List[Dict[str, Any]]:
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


def run_agent_with_retries(
    agent: Agent,
    user_input: Dict[str, Any],
    max_attempts: int = MAX_AGENT_ATTEMPTS,
    timeout_seconds: int = AGENT_TIMEOUT_SECONDS
) -> tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Run the agent with retry logic, timeout, and collect partial results.
    
    Args:
        agent: The Agent instance to run
        user_input: The user input dictionary
        max_attempts: Maximum number of retry attempts
        timeout_seconds: Timeout in seconds for each agent attempt
        
    Returns:
        tuple: (metadata dict or None, list of partial results)
    """
    metadata = None
    partial_results = []
    user_messages = [{"role": "user", "content": json.dumps(user_input)}]
    
    for attempt in range(1, max_attempts + 1):
        logger.info(f"Starting agent attempt {attempt}/{max_attempts} for receipt {user_input['image_id']}#{user_input['receipt_id']} with {timeout_seconds}s timeout")
        try:
            # Run the agent with timeout using ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    Runner.run_sync,
                    agent,
                    user_messages,
                    max_turns=10  # Also limit max turns to prevent infinite loops
                )
                
                try:
                    run_result = future.result(timeout=timeout_seconds)
                    
                    # Process agent results
                    metadata, partial = extract_agent_results(run_result, attempt)
                    partial_results.extend(partial)
                    
                    if metadata is not None:
                        break
                        
                except FutureTimeoutError:
                    logger.error(f"Agent attempt {attempt} timed out after {timeout_seconds} seconds")
                    # Cancel the future to clean up resources
                    future.cancel()
                    
        except Exception as e:
            logger.error(f"Agent attempt {attempt} failed with error: {type(e).__name__}: {e}")
            
        logger.warning(
            f"Agent attempt {attempt} did not call tool_return_metadata; retrying." +
            (f" Partial results collected: {len(partial_results)}" if partial_results else "")
        )
    
    return metadata, partial_results


# Import the improved sanitize_string from result_processor
from receipt_label.merchant_validation.result_processor import sanitize_string


def extract_agent_results(
    run_result,
    attempt: int
) -> tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Extract metadata and partial results from agent run result.
    
    Args:
        run_result: The agent run result
        attempt: Current attempt number for logging
        
    Returns:
        tuple: (metadata dict or None, list of partial results)
    """
    metadata = None
    partial_results = []
    
    # Log all function calls made by the agent for debugging
    function_calls = []
    for item in run_result.new_items:
        raw = getattr(item, "raw_item", None)
        if hasattr(raw, "name") and raw.name:
            function_calls.append(raw.name)
    
    if function_calls:
        logger.info(f"Agent made {len(function_calls)} function calls: {', '.join(function_calls)}")
    
    # Extract structured metadata by parsing the function call arguments
    for item in run_result.new_items:
        raw = getattr(item, "raw_item", None)
        if hasattr(raw, "name") and raw.name == "tool_return_metadata":
            try:
                metadata = json.loads(raw.arguments)
                logger.info(f"Successfully parsed metadata on attempt {attempt}")
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse metadata JSON on attempt {attempt}: {e}")
                # Try to get output as fallback
                metadata = getattr(item, "output", None)
                if metadata:
                    logger.info(f"Retrieved metadata from output on attempt {attempt}")
            except Exception as e:
                logger.error(f"Unexpected error parsing metadata on attempt {attempt}: {type(e).__name__}: {e}")
                metadata = getattr(item, "output", None)
            break
        
        # Collect partial results from other function calls
        elif hasattr(raw, "name") and raw.name in ["tool_search_by_phone", "tool_search_by_address", "tool_search_by_text"]:
            try:
                partial_result = {
                    "function": raw.name,
                    "result": getattr(item, "output", None)
                }
                partial_results.append(partial_result)
                logger.info(f"Captured partial result from {raw.name}")
            except Exception as e:
                logger.warning(f"Failed to capture partial result: {e}")
    
    return metadata, partial_results


def extract_best_partial_match(partial_results: List[Dict[str, Any]], user_input: Dict[str, Any]) -> Optional[Dict[str, Any]]:
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
        if result.get("function") == "tool_search_by_phone" and result.get("result"):
            phone_results.append(result["result"])
        elif result.get("function") == "tool_search_by_address" and result.get("result"):
            address_results.append(result["result"])
        elif result.get("function") == "tool_search_by_text" and result.get("result"):
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
            for key, words in extract_candidate_merchant_fields(
                receipt_words
            ).items()
        },
    }

    # Run the agent with retries using the helper function
    metadata, partial_results = run_agent_with_retries(agent, user_input)
    
    # If still no metadata, try to use partial results or fallback to no match
    if metadata is None:
        logger.error(
            "Agent validation failed",
            extra={
                "image_id": image_id,
                "receipt_id": receipt_id,
                "attempts": MAX_AGENT_ATTEMPTS,
                "partial_results_count": len(partial_results),
                "partial_result_types": [r.get("function") for r in partial_results] if partial_results else [],
                "user_input_keys": list(user_input.keys()),
                "extracted_data_types": list(user_input.get("extracted_data", {}).keys())
            }
        )
        
        # Try to extract useful data from partial results
        best_partial_match = extract_best_partial_match(partial_results, user_input)
        
        if best_partial_match:
            logger.info(f"Using best partial match from {best_partial_match.get('source', 'unknown')}")
            # Build metadata from the best partial result
            no_match_meta = build_receipt_metadata_from_partial_result(
                image_id, receipt_id, best_partial_match, user_input["raw_text"]
            )
        else:
            # Use build_receipt_metadata_from_result_no_match as fallback
            no_match_meta = build_receipt_metadata_from_result_no_match(
                image_id, receipt_id, user_input["raw_text"]
            )
        
        # Add context about the failure to the reasoning
        failure_context = f"Agent validation failed after {MAX_AGENT_ATTEMPTS} attempts."
        if partial_results:
            failure_context += f" Partial results were collected from: {', '.join([r['function'] for r in partial_results])}"
        no_match_meta.reasoning = f"{failure_context} {no_match_meta.reasoning}"
        
        write_receipt_metadata_to_dynamo(no_match_meta)
        
        # Return the receipt info for the next step even in the no-match case
        return {
            "image_id": image_id,
            "receipt_id": receipt_id,
            "status": "no_match",
            "failure_reason": "agent_attempts_exhausted",
            "partial_data_used": bool(best_partial_match)
        }

    # The validated_by field has already been validated by tool_return_metadata
    # so we can use it directly (defaulting to INFERENCE if missing)
    validated_by = metadata.get("validated_by", ValidationMethod.INFERENCE.value)
    matched_fields = list(
        {f.strip() for f in metadata.get("matched_fields", []) if f.strip()}
    )
    logger.info(f"matched_fields: {matched_fields}")

    meta = ReceiptMetadata(
        image_id=image_id,
        receipt_id=receipt_id,
        place_id=sanitize_string(metadata.get("place_id", "")),
        merchant_name=sanitize_string(metadata.get("merchant_name", "")),
        address=sanitize_string(metadata.get("address", "")),
        phone_number=sanitize_string(metadata.get("phone_number", "")),
        merchant_category=sanitize_string(metadata.get("merchant_category", "")),
        matched_fields=matched_fields,
        timestamp=datetime.now(timezone.utc),
        validated_by=validated_by,
        reasoning=sanitize_string(metadata.get("reasoning", "")),
    )
    logger.info(f"Got metadata for {image_id} {receipt_id}\n{dict(meta)}")

    logger.info(f"Writing metadata to DynamoDB for {image_id} {receipt_id}")

    write_receipt_metadata_to_dynamo(meta)

    # Return the receipt information for the next step in the workflow
    return {
        "image_id": image_id,
        "receipt_id": receipt_id,
        "status": "processed",
        "place_id": sanitize_string(metadata.get("place_id", "")),
        "merchant_name": sanitize_string(metadata.get("merchant_name", "")),
    }
