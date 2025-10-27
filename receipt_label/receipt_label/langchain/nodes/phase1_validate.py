"""Phase 1.5: Metadata Validation

Validates ReceiptMetadata (from Google Places API) against labels extracted
by Phase 1 Context from the receipt text.
"""

from __future__ import annotations

import logging
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Tuple

from receipt_label.langchain.state.currency_validation import (
    CurrencyAnalysisState,
)

logger = logging.getLogger(__name__)


def find_label_by_type(labels: List, label_type: str) -> Optional[Dict]:
    """Find a label by its type."""
    for label in labels:
        if hasattr(label, "label_type") and label.label_type.value == label_type:
            return {
                "word_text": getattr(label, "word_text", ""),
                "confidence": getattr(label, "confidence", 0.0),
                "reasoning": getattr(label, "reasoning", ""),
            }
    return None


def compare_merchant_names(receipt_text: str, metadata_name: str) -> float:
    """Compare merchant names using sequence matching.
    
    Also handles cases where receipt text is a subset of metadata name
    (e.g., "COSTCO" vs "Costco Wholesale").
    """
    if not receipt_text or not metadata_name:
        return 0.0
    
    receipt_lower = receipt_text.lower().strip()
    metadata_lower = metadata_name.lower().strip()
    
    # Check if receipt text is contained in metadata name (subset match)
    if receipt_lower in metadata_lower:
        # Give partial credit for subset matches
        subset_ratio = len(receipt_lower) / len(metadata_lower)
        # Minimum 0.7 for any subset match, boost if ratio is high
        if subset_ratio > 0.8:
            return 1.0  # Very good subset match
        else:
            return max(0.7, subset_ratio)  # At least 0.7 for any subset
    
    # Standard sequence matching
    return SequenceMatcher(None, receipt_lower, metadata_lower).ratio()


def normalize_phone(phone: str) -> str:
    """Extract digits only from phone number."""
    return ''.join(filter(str.isdigit, phone or ""))


def compare_phone_numbers(receipt_phone: str, metadata_phone: str) -> float:
    """Compare phone numbers by digit sequence."""
    receipt_digits = normalize_phone(receipt_phone)
    metadata_digits = normalize_phone(metadata_phone)
    
    if not receipt_digits or not metadata_digits:
        return 0.0
    
    # Check if one contains the other (partial match)
    if receipt_digits in metadata_digits or metadata_digits in receipt_digits:
        return 1.0
    
    # Check sequence similarity
    return SequenceMatcher(None, receipt_digits, metadata_digits).ratio()


def compare_addresses(receipt_address: str, metadata_address: str) -> float:
    """Compare addresses using token overlap (Jaccard similarity)."""
    if not receipt_address or not metadata_address:
        return 0.0
    
    # Normalize to token sets
    receipt_tokens = set(token.lower() for token in receipt_address.split() if token.strip())
    metadata_tokens = set(token.lower() for token in metadata_address.split() if token.strip())
    
    if not receipt_tokens or not metadata_tokens:
        return 0.0
    
    intersection = receipt_tokens & metadata_tokens
    union = receipt_tokens | metadata_tokens
    
    return len(intersection) / len(union) if union else 0.0


def _extract_receipt_text_for_label(words: List, transaction_labels: List, label_type: str) -> Optional[str]:
    """Extract full receipt text for a label type by finding all matching words.
    
    This handles cases where labels span multiple words (e.g., "COSTCO EWHOLESALE").
    
    Args:
        words: List of ReceiptWord entities from the receipt
        transaction_labels: List of TransactionLabel entities from Phase 1 Context
        label_type: The label type to extract (e.g., "MERCHANT_NAME", "PHONE_NUMBER")
    
    Returns:
        Combined text from all words that match the label, or None if no match
    """
    # Find the label(s) of this type
    matching_labels = [
        label for label in transaction_labels
        if hasattr(label, "label_type") and label.label_type.value == label_type
    ]
    
    if not matching_labels:
        return None
    
    # For now, use the first high-confidence label's word_text
    # TODO: In the future, we could aggregate all words with this label
    best_label = max(matching_labels, key=lambda l: getattr(l, "confidence", 0.0))
    
    return getattr(best_label, "word_text", "")


async def phase1_validate_metadata(state: CurrencyAnalysisState) -> dict:
    """Validate ReceiptMetadata against Phase 1 Context labels.
    
    Compares the canonical ReceiptMetadata (from Google Places API) with the actual
    text extracted from the receipt by LangGraph to ensure they match.
    
    This catches issues like:
    - Wrong merchant matched from Google Places
    - OCR errors in metadata creation
    - Receipt metadata from a different location/store
    """
    
    metadata = getattr(state, "receipt_metadata", None)
    transaction_labels = getattr(state, "transaction_labels", []) or []
    words = getattr(state, "words", []) or []
    
    if not metadata:
        logger.warning("⚠️ No ReceiptMetadata available for validation")
        return {"metadata_validation": None}
    
    logger.info(f"🔍 Validating metadata for {metadata.merchant_name}")
    
    # Extract receipt text for each label type
    # We need to build the full text from all words that match each label
    merchant_name_text = _extract_receipt_text_for_label(words, transaction_labels, "MERCHANT_NAME")
    phone_text = _extract_receipt_text_for_label(words, transaction_labels, "PHONE_NUMBER")
    address_text = _extract_receipt_text_for_label(words, transaction_labels, "ADDRESS_LINE")
    
    matches = []
    mismatches = []
    
    # 1. Validate MERCHANT_NAME
    if merchant_name_text:
        if not metadata.merchant_name:
            logger.info("ℹ️ No MERCHANT_NAME in metadata (skipping comparison)")
        else:
            similarity = compare_merchant_names(merchant_name_text, metadata.merchant_name)
            
            # Merchant name is CRITICAL - flag serious mismatches
            matches.append({
                "field": "merchant_name",
                "receipt_value": merchant_name_text,
                "metadata_value": metadata.merchant_name,
                "similarity": similarity,
                "status": "MATCH" if similarity >= 0.7 else "MISMATCH",
                "critical": True,  # Merchant name is the most important field
            })
            
            if similarity >= 0.7:
                logger.info(f"✅ MERCHANT_NAME match: {similarity:.2f} ({merchant_name_text} ≈ {metadata.merchant_name})")
            elif similarity >= 0.3:
                logger.warning(f"⚠️ MERCHANT_NAME PARTIAL match: {similarity:.2f} ({merchant_name_text} vs {metadata.merchant_name}) - Possible variation")
            else:
                logger.error(f"❌ MERCHANT_NAME CRITICAL MISMATCH: {similarity:.2f} ({merchant_name_text} ≠ {metadata.merchant_name}) - ReceiptMetadata likely INCORRECT!")
    else:
        logger.info("ℹ️ No MERCHANT_NAME label found on receipt")
    
    # 2. Validate PHONE_NUMBER
    if phone_text:
        if not metadata.phone_number:
            logger.info(f"ℹ️ Phone found on receipt ({phone_text}) but not in metadata (skip comparison)")
        else:
            similarity = compare_phone_numbers(phone_text, metadata.phone_number)
            
            matches.append({
                "field": "phone_number",
                "receipt_value": phone_text,
                "metadata_value": metadata.phone_number,
                "similarity": similarity,
                "status": "MATCH" if similarity >= 0.8 else "MISMATCH"
            })
            
            if similarity >= 0.8:
                logger.info(f"✅ PHONE_NUMBER match: {similarity:.2f} ({phone_text} ≈ {metadata.phone_number})")
            else:
                logger.warning(f"⚠️ PHONE_NUMBER mismatch: {similarity:.2f} ({phone_text} ≠ {metadata.phone_number})")
    else:
        logger.info("ℹ️ No PHONE_NUMBER label found on receipt")
    
    # 3. Validate ADDRESS_LINE
    if address_text:
        if not metadata.address:
            logger.info("ℹ️ No address in metadata (skipping comparison)")
        else:
            similarity = compare_addresses(address_text, metadata.address)
            
            matches.append({
                "field": "address",
                "receipt_value": address_text,
                "metadata_value": metadata.address,
                "similarity": similarity,
                "status": "MATCH" if similarity >= 0.6 else "MISMATCH"
            })
            
            if similarity >= 0.6:
                logger.info(f"✅ ADDRESS match: {similarity:.2f} ({address_text} ≈ {metadata.address})")
            else:
                logger.warning(f"⚠️ ADDRESS mismatch: {similarity:.2f} ({address_text} ≠ {metadata.address})")
    else:
        logger.info("ℹ️ No ADDRESS_LINE label found on receipt")
    
    # Separate matches and mismatches based on status
    successful_matches = [m for m in matches if m.get("status") == "MATCH"]
    failed_matches = [m for m in matches if m.get("status") == "MISMATCH"]
    
    # Calculate overall confidence as weighted average of similarities
    if matches:
        avg_similarity = sum(m.get("similarity", 0) for m in matches) / len(matches)
    else:
        avg_similarity = None
    
    # Determine overall validation status
    # Check if merchant name (critical field) has issues
    merchant_mismatch = any(
        m.get("field") == "merchant_name" and m.get("status") == "MISMATCH" and m.get("similarity", 1.0) < 0.3
        for m in matches
    )
    
    if avg_similarity is None:
        validation_status = "NO_DATA"
    elif merchant_mismatch:
        validation_status = "CRITICAL_MISMATCH"  # Merchant name doesn't match - ReceiptMetadata likely wrong
    elif avg_similarity >= 0.85:
        validation_status = "VALID"
    elif avg_similarity >= 0.60:
        validation_status = "PARTIAL"
    else:
        validation_status = "INVALID"
    
    validation_results = {
        "matches": successful_matches,
        "mismatches": failed_matches,
        "overall_similarity": avg_similarity,
        "validation_status": validation_status,
        "total_comparable_fields": len(matches),
    }
    
    avg_similarity_str = f"{avg_similarity:.2f}" if avg_similarity is not None else "N/A"
    
    # Log critical issues for merchant name
    if validation_status == "CRITICAL_MISMATCH":
        logger.error(
            f"🚨 CRITICAL: ReceiptMetadata merchant name doesn't match receipt text! "
            f"ReceiptMetadata likely contains wrong merchant."
        )
        
        # Store correction info in state for later application
        # Don't update DynamoDB here - will be done after graph completes
        # (respects dry_run flag and save_labels flag)
        logger.info(f"🔄 Storing ReceiptMetadata correction: '{merchant_name_text}' (will apply after graph)")
        
        validation_results["requires_metadata_update"] = True
        validation_results["corrected_merchant_name"] = merchant_name_text
        validation_results["original_merchant_name"] = metadata.merchant_name
    
    logger.info(
        f"📊 Validation results: {len(successful_matches)} matches, {len(failed_matches)} mismatches, "
        f"overall similarity: {avg_similarity_str}, "
        f"status: {validation_status}"
    )
    
    return {"metadata_validation": validation_results}

