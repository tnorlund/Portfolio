"""Phase 1.5: Metadata Validation using LLM.

Simple boolean validation: LLM decides if ReceiptMetadata matches receipt text.
"""

import logging
from typing import Dict

from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage

from receipt_label.langchain.models.metadata_validation import MetadataValidationResponse
from receipt_label.langchain.state.currency_validation import CurrencyAnalysisState

logger = logging.getLogger(__name__)


async def phase1_validate_metadata_llm(
    state: CurrencyAnalysisState, ollama_api_key: str
) -> dict:
    """Validate ReceiptMetadata using LLM for simple boolean validation.
    
    Args:
        state: Current workflow state with receipt data and metadata
        ollama_api_key: Ollama API key for LLM inference
    
    Returns:
        Dict with metadata_validation results
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
    
    # Extract merchant name from receipt text
    receipt_merchant = None
    for label in transaction_labels:
        if hasattr(label, "label_type") and label.label_type.value == "MERCHANT_NAME":
            receipt_merchant = getattr(label, "word_text", "")
            break
    
    if not receipt_merchant:
        logger.warning("⚠️ No merchant name found in receipt text")
        return {"metadata_validation": {"is_valid": True}}  # Assume valid if can't validate
    
    # Build full receipt text for context
    receipt_text = " ".join([w.word_text for w in words[:100]])  # First 100 words
    
    # Create prompt for LLM
    prompt = f"""You are a receipt validation expert.

ReceiptMetadata from Google Places API:
- merchant_name: "{metadata.merchant_name}"

Actual merchant name extracted from receipt text:
- merchant_name: "{receipt_merchant}"

Receipt text context (first 100 words):
{receipt_text[:500]}...

Question: Does the ReceiptMetadata merchant name match the actual merchant name on the receipt?

Consider:
1. Typographical variations (e.g., "COSTCO" vs "Costco Wholesale")
2. Abbreviations vs full names
3. Different location names (e.g., "IN-N-OUT WESTL.. - VILLAGE" vs "IN-N-OUT")
4. Complete mismatches (e.g., "Martin Tax" vs "IN-N-OUT")

Return a JSON object with:
- is_valid: boolean (true if they match, false if they don't)
- reasoning: short explanation
- recommended_merchant_name: if invalid, the correct merchant name from the receipt (empty string if valid)
"""
    
    # Initialize LLM
    llm = ChatOllama(
        model="gpt-oss:20b",
        format="json",
        temperature=0.0,
        timeout=30,
        api_key=ollama_api_key,
    )
    
    # Get structured output
    structured_llm = llm.with_structured_output(MetadataValidationResponse)
    
    try:
        # Call LLM
        response: MetadataValidationResponse = await structured_llm.ainvoke(
            [HumanMessage(content=prompt)]
        )
        
        logger.info(f"   LLM Validation: is_valid={response.is_valid}")
        logger.info(f"   Reasoning: {response.reasoning}")
        
        if not response.is_valid and response.recommended_merchant_name:
            logger.error(f"   ❌ INVALID: Should be '{response.recommended_merchant_name}', got '{metadata.merchant_name}'")
            
            # Store validation results for handler to use
            return {
                "metadata_validation": {
                    "is_valid": False,
                    "recommended_merchant_name": response.recommended_merchant_name,
                    "original_merchant_name": metadata.merchant_name,
                    "reasoning": response.reasoning,
                }
            }
        else:
            logger.info(f"   ✅ VALID: Merchant name matches")
            return {
                "metadata_validation": {
                    "is_valid": True,
                    "reasoning": response.reasoning,
                }
            }
            
    except Exception as e:
        logger.error(f"⚠️ LLM validation failed: {e}")
        return {"metadata_validation": {"is_valid": True}}  # Assume valid on error

