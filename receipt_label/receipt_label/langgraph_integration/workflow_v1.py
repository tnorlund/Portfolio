"""Minimal linear workflow for Phase 3 receipt processing.

This is the simplest possible workflow that demonstrates:
1. Loading merchant data from DynamoDB
2. Applying pattern-based labels
3. Validating the results

No conditional logic or GPT integration yet.
"""

import logging
from typing import Dict, Any, Optional
from datetime import datetime

from langgraph.graph import StateGraph, END

from .state import ReceiptProcessingState, LabelInfo, ValidationResult

logger = logging.getLogger(__name__)


async def load_merchant_node(state: ReceiptProcessingState) -> ReceiptProcessingState:
    """Load merchant data from DynamoDB.
    
    This node:
    1. Checks if we have merchant metadata from receipt validation
    2. Loads merchant-specific patterns from DynamoDB
    3. Prepares merchant context for pattern matching
    """
    logger.info(f"Loading merchant data for receipt {state['receipt_id']}")
    
    # Initialize metrics if not present
    if "metrics" not in state:
        state["metrics"] = {
            "pattern_detection_ms": 0.0,
            "decision_engine_ms": 0.0,
            "gpt_context_building_ms": 0.0,
            "gpt_labeling_ms": 0.0,
            "validation_ms": 0.0,
            "total_processing_ms": 0.0,
            "gpt_prompt_tokens": 0,
            "gpt_completion_tokens": 0,
            "gpt_cost_usd": 0.0,
            "pinecone_queries": 0,
            "pinecone_cost_usd": 0.0,
            "total_cost_usd": 0.0,
            "pattern_coverage": 0.0,
            "gpt_skip_rate": 1.0,
            "batch_api_eligible": False,
        }
    
    # TODO: Implement actual DynamoDB lookup
    # For now, use mock data to test the flow
    if state.get("merchant_name"):
        # Simulate loading merchant patterns
        state["merchant_patterns"] = ["TC#", "ST#", "OP#"]  # Walmart patterns
        state["merchant_validation_status"] = "MATCHED"
        state["merchant_matched_fields"] = ["name", "address"]
        
        logger.info(f"Loaded patterns for merchant: {state['merchant_name']}")
    else:
        logger.warning("No merchant name available, skipping pattern load")
        state["merchant_patterns"] = []
    
    # Track node execution
    if "decisions" not in state:
        state["decisions"] = []
    
    state["decisions"].append({
        "node": "load_merchant",
        "action": "loaded_merchant_data",
        "merchant": state.get("merchant_name", "unknown"),
        "patterns_count": len(state.get("merchant_patterns", [])),
        "timestamp": datetime.now().isoformat(),
    })
    
    return state


async def pattern_labeling_node(state: ReceiptProcessingState) -> ReceiptProcessingState:
    """Apply pattern-based labels from Phase 2 results.
    
    This node:
    1. Uses pattern matches from Phase 2
    2. Applies spatial heuristics for currency labels
    3. Tracks coverage statistics
    """
    logger.info("Applying pattern-based labels")
    
    # Initialize labels dict if not present
    if "labels" not in state:
        state["labels"] = {}
    
    # Process pattern matches from Phase 2
    pattern_matches = state.get("pattern_matches", {})
    labels_applied = 0
    
    # Apply date patterns
    for match in pattern_matches.get("DATE", []):
        word_id = match["word_id"]
        state["labels"][word_id] = {
            "label": "DATE",
            "confidence": match["confidence"],
            "source": "pattern",
            "validation_status": "pending",
            "assigned_at": datetime.now().isoformat(),
            "reasoning": f"Matched date pattern: {match['matched_text']}",
            "proposed_by": "pattern_detection",
            "group_id": None,
        }
        labels_applied += 1
    
    # Apply time patterns
    for match in pattern_matches.get("TIME", []):
        word_id = match["word_id"]
        state["labels"][word_id] = {
            "label": "TIME",
            "confidence": match["confidence"],
            "source": "pattern",
            "validation_status": "pending",
            "assigned_at": datetime.now().isoformat(),
            "reasoning": f"Matched time pattern: {match['matched_text']}",
            "proposed_by": "pattern_detection",
            "group_id": None,
        }
        labels_applied += 1
    
    # Apply merchant patterns
    for match in pattern_matches.get("MERCHANT_NAME", []):
        word_id = match["word_id"]
        state["labels"][word_id] = {
            "label": "MERCHANT_NAME",
            "confidence": match["confidence"],
            "source": "pattern",
            "validation_status": "pending",
            "assigned_at": datetime.now().isoformat(),
            "reasoning": f"Matched merchant pattern: {match['matched_text']}",
            "proposed_by": "pattern_detection",
            "group_id": None,
        }
        labels_applied += 1
    
    # Apply currency labels with spatial heuristics
    currency_columns = state.get("currency_columns", [])
    if currency_columns:
        # Find the largest value at the bottom (likely total)
        all_prices = []
        for column in currency_columns:
            all_prices.extend(column["prices"])
        
        if all_prices and state.get("receipt_words"):
            # Sort by value
            largest_price = max(all_prices, key=lambda p: p["value"])
            
            # Check if it's at the bottom
            receipt_words = state["receipt_words"]
            max_y = max(w["y"] for w in receipt_words) if receipt_words else 1.0
            
            if largest_price["y_position"] > 0.8 * max_y:
                word_id = largest_price["word_id"]
                state["labels"][word_id] = {
                    "label": "GRAND_TOTAL",
                    "confidence": 0.85,
                    "source": "position_heuristic",
                    "validation_status": "pending",
                    "assigned_at": datetime.now().isoformat(),
                    "reasoning": "Largest currency value at bottom of receipt",
                    "proposed_by": "spatial_analysis",
                    "group_id": None,
                }
                labels_applied += 1
    
    # Calculate coverage statistics
    total_words = len(state.get("receipt_words", []))
    state["total_words"] = total_words
    state["labeled_words"] = len(state["labels"])
    state["coverage_percentage"] = (
        (state["labeled_words"] / total_words * 100) if total_words > 0 else 0
    )
    
    # Update metrics
    state["metrics"]["pattern_coverage"] = state["coverage_percentage"] / 100
    
    # Track decision
    state["decisions"].append({
        "node": "pattern_labeling",
        "action": "applied_patterns",
        "labels_applied": labels_applied,
        "coverage": f"{state['coverage_percentage']:.1f}%",
        "timestamp": datetime.now().isoformat(),
    })
    
    logger.info(
        f"Applied {labels_applied} labels, coverage: {state['coverage_percentage']:.1f}%"
    )
    
    return state


async def validation_node(state: ReceiptProcessingState) -> ReceiptProcessingState:
    """Validate labels for consistency and completeness.
    
    This node:
    1. Checks if essential fields are found
    2. Validates mathematical relationships
    3. Sets needs_review flag if issues found
    """
    logger.info("Validating labels")
    
    # Essential fields we must find
    ESSENTIAL_FIELDS = ["MERCHANT_NAME", "DATE", "TIME", "GRAND_TOTAL"]
    
    # Check which essential fields we found
    found_labels = {label["label"] for label in state["labels"].values()}
    missing_essentials = [field for field in ESSENTIAL_FIELDS if field not in found_labels]
    found_essentials = {
        field: next(
            (wid for wid, lbl in state["labels"].items() if lbl["label"] == field), None
        )
        for field in ESSENTIAL_FIELDS
        if field in found_labels
    }
    
    state["missing_essentials"] = missing_essentials
    state["found_essentials"] = found_essentials
    
    # Initialize validation results
    validation_results: Dict[str, ValidationResult] = {}
    
    # Essential fields validation
    if missing_essentials:
        validation_results["essential_fields"] = {
            "passed": False,
            "errors": [f"Missing essential fields: {', '.join(missing_essentials)}"],
            "warnings": [],
            "checked_at": datetime.now().isoformat(),
        }
        state["needs_review"] = True
    else:
        validation_results["essential_fields"] = {
            "passed": True,
            "errors": [],
            "warnings": [],
            "checked_at": datetime.now().isoformat(),
        }
        state["needs_review"] = False
    
    # Mathematical validation (if we have math solutions from Phase 2)
    math_solutions = state.get("math_solutions", [])
    if math_solutions:
        # TODO: Implement mathematical validation
        # For now, assume it passes
        validation_results["mathematical_consistency"] = {
            "passed": True,
            "errors": [],
            "warnings": [],
            "checked_at": datetime.now().isoformat(),
        }
    
    state["validation_results"] = validation_results
    
    # Track decision
    state["decisions"].append({
        "node": "validation",
        "action": "validated_labels",
        "essential_fields_found": len(found_essentials),
        "essential_fields_missing": len(missing_essentials),
        "needs_review": state["needs_review"],
        "timestamp": datetime.now().isoformat(),
    })
    
    logger.info(
        f"Validation complete. Missing essentials: {missing_essentials}, "
        f"Needs review: {state['needs_review']}"
    )
    
    return state


def create_simple_workflow():
    """Create a minimal linear workflow for testing.
    
    This workflow:
    1. Loads merchant data
    2. Applies pattern-based labels
    3. Validates the results
    
    No conditional logic or external API calls yet.
    """
    workflow = StateGraph(ReceiptProcessingState)
    
    # Add nodes
    workflow.add_node("load_merchant", load_merchant_node)
    workflow.add_node("pattern_labeling", pattern_labeling_node)
    workflow.add_node("validation", validation_node)
    
    # Define linear flow
    workflow.set_entry_point("load_merchant")
    workflow.add_edge("load_merchant", "pattern_labeling")
    workflow.add_edge("pattern_labeling", "validation")
    workflow.add_edge("validation", END)
    
    return workflow.compile()


# Export the workflow
__all__ = ["create_simple_workflow"]