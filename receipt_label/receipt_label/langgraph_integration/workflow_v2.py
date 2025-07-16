"""Conditional workflow with decision engine and GPT integration.

This extends workflow_v1.py with:
1. Decision engine to determine if GPT is needed
2. Conditional branching based on pattern coverage
3. Spatial context building for efficient prompts
4. GPT integration for gap filling
5. Proper context engineering patterns
"""

import logging
from typing import Dict, Any, Optional, List
from datetime import datetime

from langgraph.graph import StateGraph, END

from .state import (
    ReceiptProcessingState,
    LabelInfo,
    ValidationResult,
    DecisionOutcome,
    ConfidenceLevel,
)
from .workflow_v1 import (
    load_merchant_node,
    pattern_labeling_node,
    validation_node,
)
from .nodes import audit_trail_node

logger = logging.getLogger(__name__)


async def decision_engine_node(state: ReceiptProcessingState) -> ReceiptProcessingState:
    """Analyze pattern coverage and decide if GPT is needed.
    
    Context Engineering:
    - READ: labels, coverage statistics
    - ISOLATE: Only makes routing decision
    - WRITE: decision_outcome, needs_gpt
    
    Implements the 94.4% skip rate logic from PR #221.
    """
    logger.info("Running decision engine")
    
    # Essential fields we must find
    ESSENTIAL_FIELDS = ["MERCHANT_NAME", "DATE", "TIME", "GRAND_TOTAL"]
    
    # Check coverage
    missing_essentials = state.get("missing_essentials", [])
    coverage_percentage = state.get("coverage_percentage", 0)
    unlabeled_meaningful = state.get("unlabeled_meaningful_words", 0)
    
    # Decision logic from PR #221
    if len(missing_essentials) == 0:
        # All essentials found - check if extended labeling worth it
        if coverage_percentage > 80 or unlabeled_meaningful < 5:
            decision = DecisionOutcome.SKIP
            confidence = ConfidenceLevel.HIGH
            reasoning = f"All essentials found, {coverage_percentage:.1f}% coverage"
            needs_gpt = False
        else:
            decision = DecisionOutcome.BATCH  # Can wait for batch API
            confidence = ConfidenceLevel.MEDIUM
            reasoning = f"Essentials found but low coverage ({coverage_percentage:.1f}%)"
            needs_gpt = True
    else:
        # Missing essentials - need GPT
        decision = DecisionOutcome.REQUIRED
        confidence = ConfidenceLevel.HIGH
        reasoning = f"Missing essentials: {', '.join(missing_essentials)}"
        needs_gpt = True
    
    # Update state
    state["decision_outcome"] = decision
    state["decision_confidence"] = confidence
    state["decision_reasoning"] = reasoning
    state["needs_gpt"] = needs_gpt
    
    # Calculate skip rate (for monitoring)
    state["skip_rate"] = 1.0 if decision == DecisionOutcome.SKIP else 0.0
    
    # Update metrics
    state["metrics"]["decision_engine_ms"] = 10  # Mock timing
    state["metrics"]["gpt_skip_rate"] = state["skip_rate"]
    state["metrics"]["batch_api_eligible"] = decision == DecisionOutcome.BATCH
    
    # Track decision
    state["decisions"].append({
        "node": "decision_engine",
        "action": "routing_decision",
        "outcome": decision.value,
        "confidence": confidence.value,
        "reasoning": reasoning,
        "timestamp": datetime.now().isoformat(),
    })
    
    logger.info(f"Decision: {decision.value}, GPT needed: {needs_gpt}")
    
    return state


async def spatial_context_node(state: ReceiptProcessingState) -> ReceiptProcessingState:
    """Build compressed spatial context for GPT.
    
    Context Engineering:
    - SELECT: Only words near missing labels
    - COMPRESS: Summarize spatial relationships
    - WRITE: gpt_spatial_context
    
    This dramatically reduces token usage.
    """
    logger.info("Building spatial context for GPT")
    
    receipt_words = state.get("receipt_words", [])
    missing_essentials = state.get("missing_essentials", [])
    
    # Build focused context for each missing essential
    spatial_contexts = []
    
    for missing_field in missing_essentials:
        if missing_field == "GRAND_TOTAL":
            # Focus on bottom section with prices
            context = build_total_context(receipt_words, state.get("currency_columns", []))
        elif missing_field == "DATE":
            # Focus on top section
            context = build_header_context(receipt_words, "date")
        elif missing_field == "TIME":
            # Focus on top section
            context = build_header_context(receipt_words, "time")
        elif missing_field == "MERCHANT_NAME":
            # Focus on very top
            context = build_merchant_context(receipt_words)
        else:
            context = None
        
        if context:
            spatial_contexts.append({
                "target_field": missing_field,
                "context": context
            })
    
    state["gpt_spatial_context"] = spatial_contexts
    state["gpt_context_type"] = "essential_gaps"
    
    # Track context size
    context_size = sum(len(str(ctx)) for ctx in spatial_contexts)
    logger.info(f"Built spatial context: {len(spatial_contexts)} sections, ~{context_size} chars")
    
    state["decisions"].append({
        "node": "spatial_context",
        "action": "built_context",
        "sections": len(spatial_contexts),
        "size_chars": context_size,
        "timestamp": datetime.now().isoformat(),
    })
    
    return state


def build_total_context(words: List[Dict], currency_columns: List[Dict]) -> Dict:
    """Build context for finding GRAND_TOTAL.
    
    COMPRESS: Focus on bottom 30% with currency values.
    """
    if not words:
        return {}
    
    max_y = max(w["y"] for w in words)
    bottom_words = [w for w in words if w["y"] > 0.7 * max_y]
    
    # Find all prices in bottom section
    price_words = []
    for col in currency_columns:
        for price in col.get("prices", []):
            if price["y_position"] > 0.7 * max_y:
                price_words.append({
                    "word_id": price["word_id"],
                    "value": price["value"],
                    "y": price["y_position"]
                })
    
    return {
        "section": "bottom",
        "nearby_text": [w["text"] for w in bottom_words[-20:]],  # Last 20 words
        "price_candidates": price_words,
        "hint": "Look for largest price or price after TOTAL/AMOUNT DUE"
    }


def build_header_context(words: List[Dict], target: str) -> Dict:
    """Build context for finding DATE or TIME.
    
    COMPRESS: Focus on top 20% of receipt.
    """
    if not words:
        return {}
    
    max_y = max(w["y"] for w in words)
    top_words = [w for w in words if w["y"] < 0.2 * max_y]
    
    return {
        "section": "header",
        "nearby_text": [w["text"] for w in top_words[:30]],  # First 30 words
        "hint": f"Look for {target} patterns like MM/DD/YYYY or HH:MM"
    }


def build_merchant_context(words: List[Dict]) -> Dict:
    """Build context for finding MERCHANT_NAME.
    
    COMPRESS: Focus on very first lines.
    """
    if not words:
        return {}
    
    # Group by line_id
    lines = {}
    for w in words:
        line_id = w.get("line_id", 0)
        if line_id not in lines:
            lines[line_id] = []
        lines[line_id].append(w)
    
    # Get first 3 lines
    first_lines = []
    for i in range(min(3, len(lines))):
        if i in lines:
            line_text = " ".join(w["text"] for w in sorted(lines[i], key=lambda x: x["x"]))
            first_lines.append(line_text)
    
    return {
        "section": "header",
        "first_lines": first_lines,
        "hint": "Merchant name is typically in large text at the very top"
    }


async def gpt_labeling_node(state: ReceiptProcessingState) -> ReceiptProcessingState:
    """Use GPT to fill label gaps.
    
    Context Engineering:
    - READ: gpt_spatial_context (compressed)
    - ISOLATE: Only fills missing labels
    - WRITE: label updates
    
    This is only called when needed (5.6% of receipts).
    """
    logger.info("Using GPT to fill label gaps")
    
    # TODO: Implement actual GPT call
    # For now, mock the response
    
    spatial_contexts = state.get("gpt_spatial_context", [])
    
    # Mock GPT responses for missing fields
    for context in spatial_contexts:
        target_field = context["target_field"]
        
        if target_field == "GRAND_TOTAL" and context["context"].get("price_candidates"):
            # Pick the largest price
            largest = max(context["context"]["price_candidates"], key=lambda p: p["value"])
            state["labels"][largest["word_id"]] = {
                "label": "GRAND_TOTAL",
                "confidence": 0.85,
                "source": "gpt",
                "validation_status": "pending",
                "assigned_at": datetime.now().isoformat(),
                "reasoning": "GPT identified as largest value in bottom section",
                "proposed_by": "gpt_labeling",
                "group_id": None,
            }
    
    # Track GPT usage
    state["gpt_responses"].append({
        "prompt": "Mock prompt for missing essentials",
        "response": "Mock response",
        "tokens": 150,
        "cost": 0.003,
        "timestamp": datetime.now().isoformat(),
    })
    
    state["metrics"]["gpt_prompt_tokens"] += 150
    state["metrics"]["gpt_completion_tokens"] += 50
    state["metrics"]["gpt_cost_usd"] += 0.003
    state["metrics"]["total_cost_usd"] += 0.003
    
    state["decisions"].append({
        "node": "gpt_labeling",
        "action": "filled_gaps",
        "fields_filled": len(spatial_contexts),
        "cost_usd": 0.003,
        "timestamp": datetime.now().isoformat(),
    })
    
    return state


def routing_function(state: ReceiptProcessingState) -> str:
    """Decide next node based on decision engine outcome."""
    if state.get("needs_gpt", False):
        return "spatial_context"
    else:
        return "validation"


def create_conditional_workflow():
    """Create workflow with conditional branching based on pattern coverage.
    
    This implements the full Phase 3 workflow with:
    1. Pattern-first approach (94.4% skip rate)
    2. Conditional GPT usage only for gaps
    3. Context engineering for efficiency
    4. Proper state management
    """
    workflow = StateGraph(ReceiptProcessingState)
    
    # Add all nodes
    workflow.add_node("load_merchant", load_merchant_node)
    workflow.add_node("pattern_labeling", pattern_labeling_node)
    workflow.add_node("decision_engine", decision_engine_node)
    workflow.add_node("spatial_context", spatial_context_node)
    workflow.add_node("gpt_labeling", gpt_labeling_node)
    workflow.add_node("validation", validation_node)
    workflow.add_node("audit_trail", audit_trail_node)
    
    # Define flow with conditional branching
    workflow.set_entry_point("load_merchant")
    workflow.add_edge("load_merchant", "pattern_labeling")
    workflow.add_edge("pattern_labeling", "decision_engine")
    
    # Conditional branching based on decision
    workflow.add_conditional_edges(
        "decision_engine",
        routing_function,
        {
            "spatial_context": "spatial_context",
            "validation": "validation"
        }
    )
    
    # GPT path
    workflow.add_edge("spatial_context", "gpt_labeling")
    workflow.add_edge("gpt_labeling", "validation")
    
    # All paths lead to validation, then audit, then end
    workflow.add_edge("validation", "audit_trail")
    workflow.add_edge("audit_trail", END)
    
    return workflow.compile()


# Export the workflow
__all__ = ["create_conditional_workflow"]