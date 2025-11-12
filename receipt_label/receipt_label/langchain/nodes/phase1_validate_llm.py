"""Phase 1.5: Metadata Validation using LLM with Chain of Verification.

Validates ReceiptMetadata using LLM with CoVe, and optionally updates via Google Places API.
"""

import logging
from typing import Dict, Optional

from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage

from receipt_label.langchain.models.metadata_validation import MetadataValidationResponse
from receipt_label.langchain.state.currency_validation import CurrencyAnalysisState
from receipt_label.langchain.utils.cove import apply_chain_of_verification

logger = logging.getLogger(__name__)


async def phase1_validate_metadata_llm(
    state: CurrencyAnalysisState,
    ollama_api_key: str,
    enable_cove: bool = True,
    google_places_api_key: Optional[str] = None,
    update_metadata: bool = False,
) -> dict:
    """Validate ReceiptMetadata using LLM with Chain of Verification.

    Optionally searches Google Places API and updates metadata if validation fails.

    Args:
        state: Current workflow state with receipt data and metadata
        ollama_api_key: Ollama API key for LLM inference
        enable_cove: Whether to apply Chain of Verification (default: True)
        google_places_api_key: Google Places API key (required if update_metadata=True)
        update_metadata: Whether to search Google Places and update metadata if invalid (default: False)

    Returns:
        Dict with metadata_validation results and optionally updated metadata
    """

    metadata = getattr(state, "receipt_metadata", None)
    transaction_labels = getattr(state, "transaction_labels", []) or []
    words = getattr(state, "words", []) or []

    if not metadata:
        logger.warning("⚠️ No ReceiptMetadata available for validation")
        return {"metadata_validation": None}

    if not transaction_labels:
        logger.warning("⚠️ No transaction labels available for validation")
        return {"metadata_validation": None}

    logger.info(f"🔍 Validating metadata for merchant: {metadata.merchant_name}")

    # Extract all available fields from receipt text
    def extract_field_value(label_type: str) -> str:
        """Extract field value from transaction labels."""
        for label in transaction_labels:
            if hasattr(label, "label_type") and label.label_type.value == label_type:
                return getattr(label, "word_text", "")
        return ""

    receipt_merchant = extract_field_value("MERCHANT_NAME")
    receipt_phone = extract_field_value("PHONE_NUMBER")
    receipt_address = extract_field_value("ADDRESS_LINE")

    # At minimum, we need merchant_name to validate
    if not receipt_merchant:
        logger.warning("⚠️ No merchant name found in receipt text")
        return {"metadata_validation": {"is_valid": True}}  # Assume valid if can't validate

    # Build full receipt text for context
    # ReceiptWord has 'text' attribute, not 'word_text'
    receipt_text = " ".join([w.text for w in words[:200]])  # First 200 words for better context

    # Initialize LLM
    llm = ChatOllama(
        model="gpt-oss:20b",
        base_url="https://ollama.com",
        client_kwargs={
            "headers": {"Authorization": f"Bearer {ollama_api_key}"},
            "timeout": 120,
        },
        format="json",
        temperature=0.0,
    )

    # Build comprehensive validation prompt with all available fields
    fields_to_validate = []
    if receipt_merchant:
        fields_to_validate.append(f"- merchant_name: ReceiptMetadata='{metadata.merchant_name}' vs Receipt='{receipt_merchant}'")
    if receipt_phone:
        fields_to_validate.append(f"- phone_number: ReceiptMetadata='{metadata.phone_number or 'N/A'}' vs Receipt='{receipt_phone}'")
    if receipt_address:
        fields_to_validate.append(f"- address: ReceiptMetadata='{metadata.address or 'N/A'}' vs Receipt='{receipt_address}'")

    prompt = f"""You are a receipt validation expert. Validate ReceiptMetadata fields against the actual receipt text.

ReceiptMetadata from Google Places API:
- merchant_name: "{metadata.merchant_name}"
- address: "{metadata.address or 'N/A'}"
- phone_number: "{metadata.phone_number or 'N/A'}"

Values extracted from receipt text:
{chr(10).join(fields_to_validate) if fields_to_validate else "- No fields found on receipt"}

Full receipt text context:
{receipt_text[:1000]}...

Task: Validate each field by comparing ReceiptMetadata values with the actual receipt text.

Validation Rules:
1. MERCHANT_NAME (CRITICAL):
   - Must match (allowing for typographical variations like "COSTCO" vs "Costco Wholesale")
   - Allow abbreviations vs full names
   - Allow location variations (e.g., "IN-N-OUT WESTL.. - VILLAGE" vs "IN-N-OUT")
   - Flag complete mismatches as INVALID
   - If invalid, provide the correct merchant name from receipt

2. PHONE_NUMBER:
   - Normalize formats (remove spaces, dashes, parentheses)
   - Compare digits only
   - Allow for formatting differences (e.g., "(555) 123-4567" vs "555-123-4567")
   - If invalid, provide the correct phone number from receipt

3. ADDRESS:
   - Compare normalized addresses (ignore case, punctuation variations)
   - Allow for abbreviations (e.g., "St" vs "Street", "Ave" vs "Avenue")
   - Partial matches are acceptable if core address matches
   - If invalid, provide the correct address from receipt

Return a JSON object with:
- overall_is_valid: boolean (false if merchant_name is invalid, true otherwise)
- reasoning: overall explanation
- merchant_name: FieldValidationResult (if merchant_name was found on receipt)
- phone_number: FieldValidationResult (if phone_number was found on receipt, null otherwise)
- address: FieldValidationResult (if address was found on receipt, null otherwise)

Each FieldValidationResult should have:
- is_valid: boolean
- reasoning: explanation for this specific field
- recommended_value: correct value from receipt if invalid (null if valid or not found)
"""

    # Get structured output
    structured_llm = llm.with_structured_output(MetadataValidationResponse)

    try:
        # Initial validation
        initial_response: MetadataValidationResponse = await structured_llm.ainvoke(
            [HumanMessage(content=prompt)]
        )

        # Apply Chain of Verification if enabled
        if enable_cove:
            logger.info("   🔍 Applying Chain of Verification to metadata validation...")
            task_desc = f"Metadata validation: comparing ReceiptMetadata (merchant='{metadata.merchant_name}', phone='{metadata.phone_number or 'N/A'}', address='{metadata.address or 'N/A'}') to receipt text"
            response, _ = await apply_chain_of_verification(
                initial_answer=initial_response,
                receipt_text=receipt_text,
                task_description=task_desc,
                response_model=MetadataValidationResponse,
                llm=llm,
                enable_cove=True,
            )
        else:
            response = initial_response

        logger.info(f"   LLM Validation: overall_is_valid={response.overall_is_valid}")
        logger.info(f"   Reasoning: {response.reasoning}")

        # Log field-specific results
        if response.merchant_name:
            status = "✅" if response.merchant_name.is_valid else "❌"
            logger.info(f"   {status} MERCHANT_NAME: {response.merchant_name.reasoning}")
        if response.phone_number:
            status = "✅" if response.phone_number.is_valid else "❌"
            logger.info(f"   {status} PHONE_NUMBER: {response.phone_number.reasoning}")
        if response.address:
            status = "✅" if response.address.is_valid else "❌"
            logger.info(f"   {status} ADDRESS: {response.address.reasoning}")

        result = {
            "metadata_validation": {
                "is_valid": response.overall_is_valid,  # Backward compatibility
                "overall_is_valid": response.overall_is_valid,
                "reasoning": response.reasoning,
                "recommended_merchant_name": response.recommended_merchant_name or "",
                "original_merchant_name": metadata.merchant_name,
                # Include field-specific results
                "merchant_name_validation": response.merchant_name.dict() if response.merchant_name else None,
                "phone_number_validation": response.phone_number.dict() if response.phone_number else None,
                "address_validation": response.address.dict() if response.address else None,
            }
        }

        # If invalid and update_metadata is enabled, search Google Places API
        # Use merchant_name validation result if available, otherwise fall back to recommended_merchant_name
        recommended_name = (
            response.merchant_name.recommended_value
            if response.merchant_name and response.merchant_name.recommended_value
            else response.recommended_merchant_name
        )

        if not response.overall_is_valid and recommended_name and update_metadata and google_places_api_key:
            logger.info(f"   🔍 Searching Google Places API for corrected merchant name...")
            try:
                from receipt_label.langchain.nodes.metadata_creation.search_places_agent import (
                    search_places_for_merchant_with_agent,
                )
                from receipt_label.langchain.state.metadata_creation import (
                    MetadataCreationState,
                )
                from receipt_label.langchain.nodes.metadata_creation.create_metadata import (
                    build_receipt_metadata_from_result,
                )
                from receipt_label.merchant_validation.metadata_builder import (
                    build_receipt_metadata_from_result as build_metadata,
                )

                # Create a temporary state for Places API search
                # We need formatted_text and extracted merchant info
                temp_state = MetadataCreationState(
                    receipt_id=f"{metadata.image_id}/{metadata.receipt_id}",
                    image_id=metadata.image_id,  # Required field
                    formatted_text=receipt_text,
                    extracted_merchant_name=recommended_name,
                    extracted_merchant_words=[recommended_name],
                    dynamo_client=state.dynamo_client,
                )

                # Search Google Places with the corrected merchant name
                places_result = await search_places_for_merchant_with_agent(
                    state=temp_state,
                    google_places_api_key=google_places_api_key,
                    ollama_api_key=ollama_api_key,
                    thinking_strength="medium",
                )

                if places_result.get("selected_place") and places_result["selected_place"].get("place_id"):
                    selected_place = places_result["selected_place"]
                    logger.info(f"   ✅ Found Google Places match: {selected_place.get('name')}")

                    # Build updated metadata from Google Places result
                    updated_metadata = build_metadata(
                        image_id=metadata.image_id,
                        receipt_id=metadata.receipt_id,
                        google_place=selected_place,
                        gpt_result=None,
                    )

                    # Update reasoning to indicate it was corrected
                    updated_metadata.reasoning = (
                        f"Auto-corrected by CoVe validation. "
                        f"Original: '{metadata.merchant_name}', "
                        f"Corrected: '{recommended_name}', "
                        f"Google Places: '{updated_metadata.merchant_name}'. "
                        f"CoVe reasoning: {response.reasoning}"
                    )

                    result["updated_metadata"] = updated_metadata
                    result["metadata_validation"]["google_places_updated"] = True
                    result["metadata_validation"]["new_place_id"] = updated_metadata.place_id
                    result["metadata_validation"]["new_merchant_name"] = updated_metadata.merchant_name
                else:
                    logger.warning(f"   ⚠️ No Google Places match found for '{recommended_name}'")
                    result["metadata_validation"]["google_places_updated"] = False

            except Exception as e:
                logger.error(f"   ⚠️ Failed to search Google Places API: {e}")
                result["metadata_validation"]["google_places_updated"] = False
                result["metadata_validation"]["google_places_error"] = str(e)

        if not response.overall_is_valid and recommended_name:
            logger.error(f"   ❌ INVALID: Should be '{recommended_name}', got '{metadata.merchant_name}'")
        else:
            logger.info(f"   ✅ VALID: Metadata matches receipt")

        return result

    except Exception as e:
        logger.error(f"⚠️ LLM validation failed: {e}")
        return {"metadata_validation": {"is_valid": True}}  # Assume valid on error

