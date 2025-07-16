"""Enhanced workflow with CORE_LABELS validation and multi-pass checking.

This builds on workflow_v2.py by adding:
1. CORE_LABELS corpus integration
2. First-pass validation (schema/regex checks)
3. Second-pass validation (with similar terms)
4. More sophisticated label validation
"""

import logging
from typing import Dict, Any, Optional, List, Set
from datetime import datetime
import re

from langgraph.graph import StateGraph, END

from ..constants import CORE_LABELS
from .state import (
    ReceiptProcessingState,
    LabelInfo,
    ValidationResult,
    DecisionOutcome,
    ConfidenceLevel,
)
from .workflow_v2 import (
    load_merchant_node,
    spatial_context_node,
    decision_engine_node,
)
from .nodes import audit_trail_node

logger = logging.getLogger(__name__)


# Validation patterns for CORE_LABELS
LABEL_VALIDATION_PATTERNS = {
    "DATE": re.compile(r'^\d{1,2}[/-]\d{1,2}[/-]\d{2,4}$|^\d{4}[/-]\d{1,2}[/-]\d{1,2}$'),
    "TIME": re.compile(r'^\d{1,2}:\d{2}(:\d{2})?(\s*(AM|PM|am|pm))?$'),
    "PHONE_NUMBER": re.compile(r'^[\d\s\-\(\)\.]+$'),
    "WEBSITE": re.compile(r'^(www\.)?[\w\-]+\.(com|org|net|gov|edu|co|io)$', re.IGNORECASE),
    "POSTAL_CODE": re.compile(r'^\d{5}(-\d{4})?$'),  # US ZIP
    "QUANTITY": re.compile(r'^\d+(\.\d+)?(\s*(lb|oz|kg|g|ea|pcs?))?$', re.IGNORECASE),
    "UNIT_PRICE": re.compile(r'^\$?\d+\.\d{2}$'),
    "LINE_TOTAL": re.compile(r'^\$?\d+\.\d{2}$'),
    "SUBTOTAL": re.compile(r'^\$?\d+\.\d{2}$'),
    "TAX": re.compile(r'^\$?\d+\.\d{2}$'),
    "GRAND_TOTAL": re.compile(r'^\$?\d+\.\d{2}$'),
}


async def draft_labeling_node(state: ReceiptProcessingState) -> ReceiptProcessingState:
    """Apply initial labels using patterns and CORE_LABELS taxonomy.
    
    This combines:
    1. Pattern matches from Phase 2
    2. CORE_LABELS taxonomy for validation
    3. Spatial heuristics
    
    Context Engineering:
    - ISOLATE: Pure labeling logic
    - WRITE: draft_labels (not final labels yet)
    """
    logger.info("Creating draft labels using CORE_LABELS taxonomy")
    
    # Initialize draft labels
    draft_labels = {}
    pattern_matches = state.get("pattern_matches", {})
    
    # Map pattern types to CORE_LABELS
    pattern_to_core_label = {
        "DATE": "DATE",
        "TIME": "TIME",
        "MERCHANT_NAME": "MERCHANT_NAME",
        "PHONE_NUMBER": "PHONE_NUMBER",
        "EMAIL": "WEBSITE",  # Email patterns map to WEBSITE
        "WEBSITE": "WEBSITE",
        "CURRENCY": None,  # Need context to determine which financial label
        "GRAND_TOTAL": "GRAND_TOTAL",
        "SUBTOTAL": "SUBTOTAL",
        "TAX": "TAX",
        "QUANTITY": "QUANTITY",
        "QUANTITY_AT": "QUANTITY",
        "QUANTITY_TIMES": "QUANTITY",
    }
    
    # Apply pattern matches with CORE_LABELS mapping
    for pattern_type, matches in pattern_matches.items():
        core_label = pattern_to_core_label.get(pattern_type)
        if not core_label:
            continue  # Skip if no mapping
            
        for match in matches:
            word_id = match["word_id"]
            draft_labels[word_id] = {
                "label": core_label,
                "confidence": match["confidence"],
                "source": "pattern",
                "validation_status": "draft",
                "assigned_at": datetime.now().isoformat(),
                "reasoning": f"Pattern match: {pattern_type}",
                "proposed_by": "draft_labeling",
                "group_id": None,
                "pattern_matched": pattern_type,
                "core_label_desc": CORE_LABELS.get(core_label, ""),
            }
    
    # Apply spatial heuristics for currency values
    currency_columns = state.get("currency_columns", [])
    receipt_words = state.get("receipt_words", [])
    
    if currency_columns and receipt_words:
        # Apply currency labeling with context
        currency_labels = apply_currency_labels_with_context(
            currency_columns, receipt_words, draft_labels
        )
        draft_labels.update(currency_labels)
    
    # Store draft labels separately from final labels
    state["draft_labels"] = draft_labels
    
    # Calculate draft coverage
    total_words = len(receipt_words)
    draft_labeled = len(draft_labels)
    state["draft_coverage"] = (draft_labeled / total_words * 100) if total_words > 0 else 0
    
    logger.info(f"Created {len(draft_labels)} draft labels, coverage: {state['draft_coverage']:.1f}%")
    
    state["decisions"].append({
        "node": "draft_labeling",
        "action": "created_draft_labels",
        "count": len(draft_labels),
        "coverage": f"{state['draft_coverage']:.1f}%",
        "timestamp": datetime.now().isoformat(),
    })
    
    return state


def apply_currency_labels_with_context(
    currency_columns: List[Dict],
    receipt_words: List[Dict],
    existing_labels: Dict[int, Dict]
) -> Dict[int, LabelInfo]:
    """Apply CORE_LABELS to currency values based on context."""
    
    currency_labels = {}
    
    # Get all currency values with their positions
    all_prices = []
    for column in currency_columns:
        for price in column.get("prices", []):
            all_prices.append(price)
    
    if not all_prices:
        return currency_labels
    
    # Sort by Y position (top to bottom)
    all_prices.sort(key=lambda p: p["y_position"])
    
    # Find context words for each price
    for price in all_prices:
        word_id = price["word_id"]
        y_pos = price["y_position"]
        
        # Skip if already labeled
        if word_id in existing_labels:
            continue
        
        # Find nearby words on same line
        nearby_words = [
            w for w in receipt_words
            if abs(w["y"] - y_pos) < 0.02  # Same line threshold
        ]
        
        # Look for context keywords
        nearby_text = " ".join(w["text"].upper() for w in nearby_words)
        
        # Determine appropriate CORE_LABEL
        if any(keyword in nearby_text for keyword in ["TOTAL", "AMOUNT DUE", "BALANCE"]):
            label = "GRAND_TOTAL"
            reasoning = "Currency near TOTAL keyword"
        elif any(keyword in nearby_text for keyword in ["SUBTOTAL", "SUB TOTAL", "MERCHANDISE"]):
            label = "SUBTOTAL"
            reasoning = "Currency near SUBTOTAL keyword"
        elif any(keyword in nearby_text for keyword in ["TAX", "SALES TAX", "VAT"]):
            label = "TAX"
            reasoning = "Currency near TAX keyword"
        elif any(keyword in nearby_text for keyword in ["@", "EA", "EACH"]):
            label = "UNIT_PRICE"
            reasoning = "Currency with unit indicator"
        elif len(nearby_words) > 3:  # Likely a line item
            label = "LINE_TOTAL"
            reasoning = "Currency at end of product line"
        else:
            continue  # Can't determine with confidence
        
        currency_labels[word_id] = {
            "label": label,
            "confidence": 0.75,  # Lower confidence for heuristic
            "source": "spatial_heuristic",
            "validation_status": "draft",
            "assigned_at": datetime.now().isoformat(),
            "reasoning": reasoning,
            "proposed_by": "currency_context",
            "group_id": None,
            "core_label_desc": CORE_LABELS.get(label, ""),
        }
    
    return currency_labels


async def first_pass_validator_node(state: ReceiptProcessingState) -> ReceiptProcessingState:
    """Validate draft labels against CORE_LABELS schema and patterns.
    
    This node:
    1. Checks label validity against CORE_LABELS
    2. Validates format with regex patterns
    3. Checks for duplicate/conflicting labels
    4. Identifies invalid labels for correction
    
    Context Engineering:
    - ISOLATE: Pure validation logic
    - WRITE: invalid_labels list
    """
    logger.info("Running first-pass validation on draft labels")
    
    draft_labels = state.get("draft_labels", {})
    invalid_labels = []
    validated_labels = {}
    
    # Track label counts for duplicate detection
    label_counts = {}
    
    for word_id, label_info in draft_labels.items():
        label_type = label_info["label"]
        
        # Check if label exists in CORE_LABELS
        if label_type not in CORE_LABELS:
            invalid_labels.append({
                "word_id": word_id,
                "label": label_type,
                "reason": "Label not in CORE_LABELS taxonomy",
                "value": get_word_text(state, word_id),
            })
            continue
        
        # Get the word text for validation
        word_text = get_word_text(state, word_id)
        
        # Apply regex validation if pattern exists
        if label_type in LABEL_VALIDATION_PATTERNS:
            pattern = LABEL_VALIDATION_PATTERNS[label_type]
            if not pattern.match(word_text):
                invalid_labels.append({
                    "word_id": word_id,
                    "label": label_type,
                    "reason": f"Format mismatch for {label_type}",
                    "value": word_text,
                    "expected_pattern": pattern.pattern,
                })
                continue
        
        # Check for duplicates (except for line items)
        if label_type not in ["PRODUCT_NAME", "QUANTITY", "UNIT_PRICE", "LINE_TOTAL"]:
            if label_type in label_counts:
                # We have a duplicate
                existing_word_id = label_counts[label_type]
                existing_confidence = validated_labels[existing_word_id]["confidence"]
                
                # Keep the one with higher confidence
                if label_info["confidence"] > existing_confidence:
                    # Mark old one as invalid
                    invalid_labels.append({
                        "word_id": existing_word_id,
                        "label": label_type,
                        "reason": "Duplicate label with lower confidence",
                        "value": get_word_text(state, existing_word_id),
                    })
                    # Use new one
                    validated_labels[word_id] = label_info
                    label_counts[label_type] = word_id
                else:
                    # Mark new one as invalid
                    invalid_labels.append({
                        "word_id": word_id,
                        "label": label_type,
                        "reason": "Duplicate label with lower confidence",
                        "value": word_text,
                    })
                continue
            else:
                label_counts[label_type] = word_id
        
        # Label passed validation
        validated_labels[word_id] = {
            **label_info,
            "validation_status": "first_pass_valid",
        }
    
    # Store results
    state["validated_labels"] = validated_labels
    state["invalid_labels"] = invalid_labels
    
    logger.info(
        f"First-pass validation: {len(validated_labels)} valid, "
        f"{len(invalid_labels)} invalid"
    )
    
    state["decisions"].append({
        "node": "first_pass_validator",
        "action": "validated_draft_labels",
        "valid_count": len(validated_labels),
        "invalid_count": len(invalid_labels),
        "timestamp": datetime.now().isoformat(),
    })
    
    return state


def get_word_text(state: ReceiptProcessingState, word_id: int) -> str:
    """Get the text for a word by ID."""
    for word in state.get("receipt_words", []):
        if word.get("word_id") == word_id:
            return word.get("text", "")
    return ""


async def similar_term_retriever_node(state: ReceiptProcessingState) -> ReceiptProcessingState:
    """Find similar valid labels for invalid ones using Pinecone.
    
    This node:
    1. Takes invalid labels from first pass
    2. Queries Pinecone for similar valid labels
    3. Builds correction suggestions
    
    Context Engineering:
    - SELECT: Only query for invalid labels
    - WRITE: similar_terms suggestions
    """
    logger.info("Finding similar terms for invalid labels")
    
    invalid_labels = state.get("invalid_labels", [])
    similar_terms = {}
    
    # TODO: Implement actual Pinecone query
    # For now, use rule-based suggestions
    
    for invalid in invalid_labels:
        word_id = invalid["word_id"]
        label = invalid["label"]
        value = invalid["value"]
        
        suggestions = []
        
        # Suggest corrections based on common mistakes
        if "Format mismatch" in invalid["reason"]:
            if label == "DATE" and "/" not in value and "-" not in value:
                # Might be written out date
                suggestions.append({
                    "suggested_label": "DATE",
                    "confidence": 0.6,
                    "reasoning": "Possible written date format",
                })
            elif label == "PHONE_NUMBER" and len(value) < 7:
                # Too short for phone
                suggestions.append({
                    "suggested_label": None,  # Don't label
                    "confidence": 0.8,
                    "reasoning": "Too short for phone number",
                })
        
        # Check if it might be a different CORE_LABEL
        if label not in CORE_LABELS and value.upper() in ["CASH", "VISA", "MASTERCARD"]:
            suggestions.append({
                "suggested_label": "PAYMENT_METHOD",
                "confidence": 0.9,
                "reasoning": "Common payment method term",
            })
        
        if suggestions:
            similar_terms[word_id] = suggestions
    
    state["similar_terms"] = similar_terms
    
    logger.info(f"Found suggestions for {len(similar_terms)} invalid labels")
    
    state["decisions"].append({
        "node": "similar_term_retriever",
        "action": "found_similar_terms",
        "suggestion_count": len(similar_terms),
        "timestamp": datetime.now().isoformat(),
    })
    
    return state


async def second_pass_validator_node(state: ReceiptProcessingState) -> ReceiptProcessingState:
    """Final validation combining all information.
    
    This node:
    1. Reviews validated labels from first pass
    2. Applies corrections from similar terms
    3. Uses GPT context if available
    4. Produces final validated label set
    
    Context Engineering:
    - WRITE: final labels dict
    """
    logger.info("Running second-pass validation")
    
    # Start with first-pass validated labels
    final_labels = state.get("validated_labels", {}).copy()
    
    # Apply corrections from similar terms
    similar_terms = state.get("similar_terms", {})
    for word_id, suggestions in similar_terms.items():
        if suggestions and suggestions[0]["confidence"] > 0.7:
            best_suggestion = suggestions[0]
            if best_suggestion["suggested_label"]:
                # Apply the correction
                word_text = get_word_text(state, word_id)
                final_labels[word_id] = {
                    "label": best_suggestion["suggested_label"],
                    "confidence": best_suggestion["confidence"],
                    "source": "correction",
                    "validation_status": "validated",
                    "assigned_at": datetime.now().isoformat(),
                    "reasoning": best_suggestion["reasoning"],
                    "proposed_by": "second_pass_validator",
                    "group_id": None,
                    "core_label_desc": CORE_LABELS.get(best_suggestion["suggested_label"], ""),
                }
    
    # If we have GPT responses, incorporate those
    if state.get("gpt_responses"):
        # GPT labels would have been added by gpt_labeling_node
        # Merge them with validated labels
        for word_id, label_info in state.get("labels", {}).items():
            if label_info.get("source") == "gpt" and label_info["label"] in CORE_LABELS:
                final_labels[word_id] = {
                    **label_info,
                    "validation_status": "validated",
                    "core_label_desc": CORE_LABELS.get(label_info["label"], ""),
                }
    
    # Final validation checks
    essential_fields = ["MERCHANT_NAME", "DATE", "TIME", "GRAND_TOTAL"]
    found_essentials = set()
    missing_essentials = []
    
    for label_info in final_labels.values():
        if label_info["label"] in essential_fields:
            found_essentials.add(label_info["label"])
    
    missing_essentials = [f for f in essential_fields if f not in found_essentials]
    
    # Update state with final labels
    state["labels"] = final_labels
    state["found_essentials"] = {field: None for field in found_essentials}  # TODO: Add word IDs
    state["missing_essentials"] = missing_essentials
    
    # Calculate final coverage
    total_words = len(state.get("receipt_words", []))
    state["labeled_words"] = len(final_labels)
    state["coverage_percentage"] = (
        (state["labeled_words"] / total_words * 100) if total_words > 0 else 0
    )
    
    logger.info(
        f"Second-pass validation complete: {len(final_labels)} final labels, "
        f"missing essentials: {missing_essentials}"
    )
    
    state["decisions"].append({
        "node": "second_pass_validator",
        "action": "finalized_labels",
        "label_count": len(final_labels),
        "missing_essentials": missing_essentials,
        "coverage": f"{state['coverage_percentage']:.1f}%",
        "timestamp": datetime.now().isoformat(),
    })
    
    return state


async def enhanced_gpt_labeling_node(state: ReceiptProcessingState) -> ReceiptProcessingState:
    """GPT labeling with CORE_LABELS awareness.
    
    Enhanced version that:
    1. Includes CORE_LABELS descriptions in prompt
    2. Constrains responses to valid labels only
    3. Focuses on missing essentials
    """
    logger.info("Using GPT with CORE_LABELS taxonomy")
    
    spatial_contexts = state.get("gpt_spatial_context", [])
    missing_essentials = state.get("missing_essentials", [])
    
    # Build prompt with CORE_LABELS context
    core_labels_context = "Valid labels and their definitions:\n"
    for label, description in CORE_LABELS.items():
        if label in missing_essentials or label in ["MERCHANT_NAME", "DATE", "TIME", "GRAND_TOTAL"]:
            core_labels_context += f"- {label}: {description}\n"
    
    # TODO: Implement actual GPT call with CORE_LABELS
    # Mock response for now
    for context in spatial_contexts:
        target_field = context["target_field"]
        
        if target_field in CORE_LABELS:
            # Mock finding the label
            logger.info(f"GPT searching for {target_field}: {CORE_LABELS[target_field]}")
    
    # Continue with existing GPT logic from workflow_v2
    # ... (rest of implementation)
    
    return state


def create_enhanced_workflow():
    """Create workflow with CORE_LABELS validation and multi-pass checking.
    
    This workflow implements:
    1. Draft labeling with CORE_LABELS taxonomy
    2. First-pass validation (schema/format checks)
    3. Similar term retrieval for corrections
    4. Second-pass validation with corrections
    5. GPT with CORE_LABELS constraints
    """
    workflow = StateGraph(ReceiptProcessingState)
    
    # Add all nodes
    workflow.add_node("load_merchant", load_merchant_node)
    workflow.add_node("draft_labeling", draft_labeling_node)
    workflow.add_node("first_pass_validator", first_pass_validator_node)
    workflow.add_node("similar_term_retriever", similar_term_retriever_node)
    workflow.add_node("second_pass_validator", second_pass_validator_node)
    workflow.add_node("decision_engine", decision_engine_node)
    workflow.add_node("spatial_context", spatial_context_node)
    workflow.add_node("gpt_labeling", enhanced_gpt_labeling_node)
    workflow.add_node("audit_trail", audit_trail_node)
    
    # Define flow
    workflow.set_entry_point("load_merchant")
    workflow.add_edge("load_merchant", "draft_labeling")
    workflow.add_edge("draft_labeling", "first_pass_validator")
    workflow.add_edge("first_pass_validator", "similar_term_retriever")
    workflow.add_edge("similar_term_retriever", "second_pass_validator")
    workflow.add_edge("second_pass_validator", "decision_engine")
    
    # Conditional branching
    workflow.add_conditional_edges(
        "decision_engine",
        lambda state: "spatial_context" if state.get("needs_gpt", False) else "audit_trail",
        {
            "spatial_context": "spatial_context",
            "audit_trail": "audit_trail"
        }
    )
    
    # GPT path
    workflow.add_edge("spatial_context", "gpt_labeling")
    workflow.add_edge("gpt_labeling", "audit_trail")
    
    # End
    workflow.add_edge("audit_trail", END)
    
    return workflow.compile()


# Export the enhanced workflow
__all__ = ["create_enhanced_workflow", "CORE_LABELS"]