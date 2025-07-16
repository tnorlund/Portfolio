"""First pass validator node using LLM for schema and regex validation.

This node validates draft labels against CORE_LABELS schema and format patterns,
identifying invalid labels that need correction. It updates both state and
persistence layers.
"""

import logging
from typing import Dict, List, Set, Optional, Any
from datetime import datetime
import re

from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage

from ...constants import CORE_LABELS
from ...utils.ai_usage_context import ai_usage_context
from ..state import ReceiptProcessingState, LabelInfo

logger = logging.getLogger(__name__)


# Validation patterns for CORE_LABELS
LABEL_VALIDATION_PATTERNS = {
    "DATE": re.compile(r'^\\d{1,2}[/-]\\d{1,2}[/-]\\d{2,4}$|^\\d{4}[/-]\\d{1,2}[/-]\\d{1,2}$'),
    "TIME": re.compile(r'^\\d{1,2}:\\d{2}(:\\d{2})?(\\s*(AM|PM|am|pm))?$'),
    "PHONE_NUMBER": re.compile(r'^[\\d\\s\\-\\(\\)\\.]+$'),
    "WEBSITE": re.compile(r'^(www\\.)?[\\w\\-]+\\.(com|org|net|gov|edu|co|io)$', re.IGNORECASE),
    "POSTAL_CODE": re.compile(r'^\\d{5}(-\\d{4})?$'),  # US ZIP
    "QUANTITY": re.compile(r'^\\d+(\\.\\d+)?(\\s*(lb|oz|kg|g|ea|pcs?))?$', re.IGNORECASE),
    "UNIT_PRICE": re.compile(r'^\\$?\\d+\\.\\d{2}$'),
    "LINE_TOTAL": re.compile(r'^\\$?\\d+\\.\\d{2}$'),
    "SUBTOTAL": re.compile(r'^\\$?\\d+\\.\\d{2}$'),
    "TAX": re.compile(r'^\\$?\\d+\\.\\d{2}$'),
    "GRAND_TOTAL": re.compile(r'^\\$?\\d+\\.\\d{2}$'),
}


@tool
def validate_label_schema(labels: Dict[int, Dict]) -> Dict[str, List[Dict]]:
    """Validate labels against CORE_LABELS schema.
    
    Args:
        labels: Dict mapping word_id to label info
        
    Returns:
        Dict with 'valid' and 'invalid' label lists
    """
    valid_labels = []
    invalid_labels = []
    
    for word_id, label_info in labels.items():
        label_type = label_info.get("label")
        
        # Check if label exists in CORE_LABELS
        if label_type not in CORE_LABELS:
            invalid_labels.append({
                "word_id": word_id,
                "label": label_type,
                "reason": "Label not in CORE_LABELS taxonomy",
                "confidence": label_info.get("confidence", 0),
            })
        else:
            valid_labels.append({
                "word_id": word_id,
                "label": label_type,
                "confidence": label_info.get("confidence", 1.0),
            })
    
    return {
        "valid": valid_labels,
        "invalid": invalid_labels
    }


@tool
def validate_label_formats(
    labels: Dict[int, Dict],
    receipt_words: List[Dict]
) -> List[Dict]:
    """Validate label formats using regex patterns.
    
    Args:
        labels: Dict mapping word_id to label info
        receipt_words: All receipt words for text lookup
        
    Returns:
        List of format validation errors
    """
    # Create word_id to text mapping
    word_texts = {w["word_id"]: w["text"] for w in receipt_words}
    
    format_errors = []
    
    for word_id, label_info in labels.items():
        label_type = label_info.get("label")
        word_text = word_texts.get(word_id, "")
        
        # Skip if no validation pattern exists
        if label_type not in LABEL_VALIDATION_PATTERNS:
            continue
            
        pattern = LABEL_VALIDATION_PATTERNS[label_type]
        if not pattern.match(word_text):
            format_errors.append({
                "word_id": word_id,
                "label": label_type,
                "text": word_text,
                "reason": f"Format mismatch for {label_type}",
                "expected_pattern": pattern.pattern,
            })
    
    return format_errors


@tool
def check_duplicate_labels(
    labels: Dict[int, Dict]
) -> List[Dict]:
    """Check for duplicate non-line-item labels.
    
    Args:
        labels: Dict mapping word_id to label info
        
    Returns:
        List of duplicate label conflicts
    """
    # Labels that should be unique (not line items)
    unique_labels = {
        "MERCHANT_NAME", "DATE", "TIME", "GRAND_TOTAL",
        "SUBTOTAL", "PHONE_NUMBER", "WEBSITE", "ADDRESS_LINE",
        "LOYALTY_ID", "PAYMENT_METHOD"
    }
    
    # Track occurrences of each label type
    label_occurrences = {}
    for word_id, label_info in labels.items():
        label_type = label_info["label"]
        if label_type in unique_labels:
            if label_type not in label_occurrences:
                label_occurrences[label_type] = []
            label_occurrences[label_type].append({
                "word_id": word_id,
                "confidence": label_info.get("confidence", 0)
            })
    
    # Find duplicates
    duplicates = []
    for label_type, occurrences in label_occurrences.items():
        if len(occurrences) > 1:
            # Sort by confidence to identify which to keep
            occurrences.sort(key=lambda x: x["confidence"], reverse=True)
            
            # Mark all but highest confidence as duplicates
            for occurrence in occurrences[1:]:
                duplicates.append({
                    "word_id": occurrence["word_id"],
                    "label": label_type,
                    "reason": f"Duplicate {label_type} (lower confidence)",
                    "preferred_word_id": occurrences[0]["word_id"],
                })
    
    return duplicates


@tool
async def analyze_with_llm(
    invalid_labels: List[Dict],
    receipt_context: Dict
) -> List[Dict]:
    """Use LLM to analyze and suggest corrections for invalid labels.
    
    Args:
        invalid_labels: List of invalid label dictionaries
        receipt_context: Context including merchant name and receipt text
        
    Returns:
        List of correction suggestions
    """
    if not invalid_labels:
        return []
    
    # Format invalid labels for prompt
    invalid_list = "\n".join([
        f"- Word {inv['word_id']}: '{inv.get('text', 'N/A')}' labeled as '{inv['label']}' - {inv['reason']}"
        for inv in invalid_labels[:20]  # Limit to 20 for prompt size
    ])
    
    # Format CORE_LABELS for reference
    label_list = "\n".join([
        f"- {label}: {desc}"
        for label, desc in CORE_LABELS.items()
    ])
    
    prompt = f"""Review these invalid receipt labels and suggest corrections.

Valid labels (ONLY use these):
{label_list}

Invalid labels found:
{invalid_list}

Merchant: {receipt_context.get('merchant_name', 'Unknown')}

For each invalid label, suggest:
1. Whether to remove it (if it's noise)
2. A valid CORE_LABEL replacement
3. Confidence in the suggestion (0.0-1.0)

Format: word_id|action|new_label|confidence|reasoning
Example: 
45|replace|GRAND_TOTAL|0.85|Currency at receipt bottom
23|remove|N/A|0.95|Single punctuation mark"""

    with ai_usage_context("first_pass_validator_llm") as tracker:
        # Simulate LLM response
        # In production, this would call OpenAI API
        corrections = []
        
        for inv in invalid_labels[:5]:  # Mock corrections for first 5
            if inv["reason"] == "Label not in CORE_LABELS taxonomy":
                # Try to map to valid label
                if "TOTAL" in inv["label"].upper():
                    corrections.append({
                        "word_id": inv["word_id"],
                        "action": "replace",
                        "new_label": "GRAND_TOTAL",
                        "confidence": 0.75,
                        "reasoning": "Invalid TOTAL variant mapped to GRAND_TOTAL",
                    })
                else:
                    corrections.append({
                        "word_id": inv["word_id"],
                        "action": "remove",
                        "new_label": None,
                        "confidence": 0.9,
                        "reasoning": "Unknown label type, likely noise",
                    })
        
        return corrections


@tool
async def update_validation_status_dynamodb(
    valid_labels: List[Dict],
    invalid_labels: List[Dict],
    receipt_id: int,
    image_id: str
) -> Dict[str, int]:
    """Update validation status in DynamoDB for labels.
    
    Args:
        valid_labels: List of validated label dicts
        invalid_labels: List of invalid label dicts
        receipt_id: Receipt identifier
        image_id: Image UUID
        
    Returns:
        Dict with counts of updates
    """
    # In production, this would update ReceiptWordLabel entities
    # Setting validation_status field
    
    updates = []
    
    # Mark valid labels
    for label in valid_labels:
        updates.append({
            "word_id": label["word_id"],
            "validation_status": "VALID",
            "validation_timestamp": datetime.now().isoformat(),
        })
    
    # Mark invalid labels
    for label in invalid_labels:
        updates.append({
            "word_id": label["word_id"],
            "validation_status": "INVALID",
            "validation_reason": label["reason"],
            "validation_timestamp": datetime.now().isoformat(),
        })
    
    logger.info(f"Would update {len(updates)} validation statuses in DynamoDB")
    
    return {
        "valid_updated": len(valid_labels),
        "invalid_updated": len(invalid_labels)
    }


@tool
async def update_pinecone_validation(
    valid_labels: List[Dict],
    invalid_labels: List[Dict],
    corrections: List[Dict],
    receipt_id: int,
    image_id: str
) -> Dict[str, int]:
    """Update Pinecone metadata with validation results.
    
    Args:
        valid_labels: List of validated labels
        invalid_labels: List of invalid labels
        corrections: List of correction suggestions
        receipt_id: Receipt identifier
        image_id: Image UUID
        
    Returns:
        Dict with count of vectors updated
    """
    updates = []
    
    # Create correction lookup
    correction_map = {c["word_id"]: c for c in corrections}
    
    # Update valid labels
    for label in valid_labels:
        vector_id = f"IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#WORD#{label['word_id']:05d}"
        updates.append({
            "id": vector_id,
            "metadata": {
                "validation_status": "valid",
                "validation_timestamp": datetime.now().isoformat(),
            }
        })
    
    # Update invalid labels with corrections
    for label in invalid_labels:
        word_id = label["word_id"]
        vector_id = f"IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#WORD#{word_id:05d}"
        
        metadata = {
            "validation_status": "invalid",
            "validation_reason": label["reason"],
            "validation_timestamp": datetime.now().isoformat(),
        }
        
        # Add correction if available
        if word_id in correction_map:
            correction = correction_map[word_id]
            if correction["action"] == "replace":
                metadata["suggested_label"] = correction["new_label"]
                metadata["suggestion_confidence"] = correction["confidence"]
        
        updates.append({"id": vector_id, "metadata": metadata})
    
    logger.info(f"Would update {len(updates)} vectors in Pinecone")
    
    return {"vectors_updated": len(updates)}


async def first_pass_validator_node(state: ReceiptProcessingState) -> ReceiptProcessingState:
    """Validate draft labels using schema, regex, and LLM analysis.
    
    This node:
    1. Validates labels against CORE_LABELS schema
    2. Checks format using regex patterns  
    3. Detects duplicate unique labels
    4. Uses LLM to analyze invalid labels
    5. Updates both DynamoDB and Pinecone with validation status
    
    Context Engineering:
    - ISOLATE: Validation logic separated from labeling
    - WRITE: invalid_labels list to state
    - SELECT: Only invalid labels sent to LLM
    """
    logger.info("Starting first-pass validation of draft labels")
    
    draft_labels = state.get("draft_labels", {})
    receipt_words = state.get("receipt_words", [])
    
    if not draft_labels:
        logger.warning("No draft labels to validate")
        state["invalid_labels"] = []
        state["valid_labels"] = {}
        return state
    
    # 1. Schema validation
    schema_validation = validate_label_schema.invoke({"labels": draft_labels})
    valid_by_schema = {v["word_id"]: v for v in schema_validation["valid"]}
    invalid_by_schema = schema_validation["invalid"]
    
    # 2. Format validation (only for schema-valid labels)
    format_errors = validate_label_formats.invoke({
        "labels": {k: v for k, v in draft_labels.items() if k in valid_by_schema},
        "receipt_words": receipt_words
    })
    
    # 3. Duplicate detection
    duplicates = check_duplicate_labels.invoke({"labels": draft_labels})
    
    # Compile all invalid labels
    all_invalid = []
    invalid_word_ids = set()
    
    # Add schema violations
    for inv in invalid_by_schema:
        all_invalid.append(inv)
        invalid_word_ids.add(inv["word_id"])
    
    # Add format errors
    for err in format_errors:
        if err["word_id"] not in invalid_word_ids:
            all_invalid.append(err)
            invalid_word_ids.add(err["word_id"])
    
    # Add duplicates
    for dup in duplicates:
        if dup["word_id"] not in invalid_word_ids:
            all_invalid.append(dup)
            invalid_word_ids.add(dup["word_id"])
    
    # 4. LLM analysis for corrections
    corrections = []
    if all_invalid:
        # Add text to invalid labels for LLM
        word_text_map = {w["word_id"]: w["text"] for w in receipt_words}
        for inv in all_invalid:
            inv["text"] = word_text_map.get(inv["word_id"], "")
        
        corrections = await analyze_with_llm.ainvoke({
            "invalid_labels": all_invalid,
            "receipt_context": {
                "merchant_name": state.get("merchant_name"),
            }
        })
    
    # 5. Update persistence layers
    valid_labels = [
        {"word_id": wid, "label": label_info["label"], "confidence": label_info.get("confidence", 1.0)}
        for wid, label_info in draft_labels.items()
        if wid not in invalid_word_ids
    ]
    
    await update_validation_status_dynamodb.ainvoke({
        "valid_labels": valid_labels,
        "invalid_labels": all_invalid,
        "receipt_id": state["receipt_id"],
        "image_id": state["image_id"]
    })
    
    await update_pinecone_validation.ainvoke({
        "valid_labels": valid_labels,
        "invalid_labels": all_invalid,
        "corrections": corrections,
        "receipt_id": state["receipt_id"],
        "image_id": state["image_id"]
    })
    
    # 6. Update state
    state["invalid_labels"] = all_invalid
    state["label_corrections"] = corrections
    state["validated_labels"] = {
        wid: label_info
        for wid, label_info in draft_labels.items()
        if wid not in invalid_word_ids
    }
    
    # Calculate validation metrics
    total_labels = len(draft_labels)
    valid_count = len(valid_labels)
    invalid_count = len(all_invalid)
    validation_rate = valid_count / total_labels if total_labels > 0 else 0
    
    # Add decision tracking
    state["decisions"].append({
        "node": "first_pass_validator",
        "action": "validated_draft_labels",
        "total_labels": total_labels,
        "valid_count": valid_count,
        "invalid_count": invalid_count,
        "schema_violations": len(invalid_by_schema),
        "format_errors": len(format_errors),
        "duplicates": len(duplicates),
        "corrections_suggested": len(corrections),
        "validation_rate": f"{validation_rate:.1%}",
        "timestamp": datetime.now().isoformat(),
    })
    
    logger.info(
        f"First-pass validation complete: {valid_count} valid, "
        f"{invalid_count} invalid ({validation_rate:.1%} validation rate)"
    )
    
    return state