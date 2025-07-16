"""Second pass validator node using LLM with similar terms for final validation.

This node receives draft_labels, invalid_labels, and similar_terms to produce
the final validated label set, following the validation pipeline pattern.
"""

import logging
from typing import Dict, List, Optional, Any
from datetime import datetime

from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage

from ...constants import CORE_LABELS
from ...utils.ai_usage_context import ai_usage_context
from ..state import ReceiptProcessingState

logger = logging.getLogger(__name__)


@tool
async def analyze_corrections_with_llm(
    draft_labels: Dict[int, Dict],
    invalid_labels: List[Dict],
    similar_terms: Dict[int, List[Dict]],
    receipt_context: Dict
) -> List[Dict]:
    """Use LLM to analyze similar terms and suggest final corrections.
    
    Args:
        draft_labels: Original draft labels
        invalid_labels: List of invalid label dictionaries
        similar_terms: Dict mapping word_id to similar term suggestions
        receipt_context: Context including merchant name and receipt metadata
        
    Returns:
        List of final correction decisions
    """
    if not invalid_labels:
        return []
    
    # Build comprehensive prompt with all context
    merchant_name = receipt_context.get("merchant_name", "Unknown")
    
    # Format invalid labels with their similar terms
    invalid_analysis = []
    for invalid in invalid_labels:
        word_id = invalid["word_id"]
        text = invalid.get("text", "")
        invalid_label = invalid.get("label", "")
        reason = invalid.get("reason", "")
        
        analysis_block = f"""
Word {word_id}: "{text}"
- Current label: {invalid_label}
- Issue: {reason}"""
        
        # Add similar term suggestions if available
        if word_id in similar_terms and similar_terms[word_id]:
            analysis_block += "\n- Suggestions:"
            for i, suggestion in enumerate(similar_terms[word_id][:3], 1):
                sugg_label = suggestion["suggested_label"]
                confidence = suggestion["confidence"]
                reasoning = suggestion["reasoning"]
                analysis_block += f"\n  {i}. {sugg_label} (conf: {confidence:.2f}) - {reasoning}"
        else:
            analysis_block += "\n- No similar terms found"
        
        invalid_analysis.append(analysis_block)
    
    # Format CORE_LABELS for reference
    essential_labels = [
        "MERCHANT_NAME", "DATE", "TIME", "GRAND_TOTAL",
        "SUBTOTAL", "TAX", "LINE_TOTAL", "PRODUCT_NAME",
        "QUANTITY", "UNIT_PRICE", "PHONE_NUMBER", "ADDRESS_LINE"
    ]
    
    label_descriptions = "\n".join([
        f"- {label}: {CORE_LABELS[label]}"
        for label in essential_labels if label in CORE_LABELS
    ])
    
    invalid_text = "\n".join(invalid_analysis)
    
    prompt = f"""You are a receipt validation expert. Analyze these invalid labels and make final correction decisions.

VALID LABELS (use ONLY these):
{label_descriptions}

MERCHANT: {merchant_name}

INVALID LABELS TO CORRECT:
{invalid_text}

For each invalid label, decide:
1. REMOVE - if it's noise/punctuation and shouldn't be labeled
2. REPLACE - with a valid CORE_LABEL if you can determine the correct one
3. KEEP_INVALID - if uncertain (human review needed)

When choosing REPLACE:
- Consider the similar term suggestions provided
- Merchant context (e.g., "TC#" at Walmart is likely a transaction ID)
- Position context (bottom currency = GRAND_TOTAL, top text = MERCHANT_NAME)
- Text patterns (currency format, date format, etc.)

Format your response as:
word_id|action|new_label|confidence|reasoning

Examples:
23|REPLACE|GRAND_TOTAL|0.90|Currency at bottom matches GRAND_TOTAL pattern
45|REMOVE|None|0.95|Single punctuation mark, noise
67|REPLACE|MERCHANT_NAME|0.85|Top text matches merchant pattern
12|KEEP_INVALID|None|0.60|Unclear what this text represents

Provide corrections for ALL invalid labels listed above."""

    with ai_usage_context("second_pass_validator_llm") as tracker:
        # In production, this would call OpenAI API
        # For now, simulate intelligent analysis based on patterns
        
        corrections = []
        
        for invalid in invalid_labels:
            word_id = invalid["word_id"]
            text = invalid.get("text", "").strip()
            invalid_label = invalid.get("label", "")
            reason = invalid.get("reason", "")
            
            # Get best similar term suggestion
            best_suggestion = None
            if word_id in similar_terms and similar_terms[word_id]:
                best_suggestion = similar_terms[word_id][0]  # Highest confidence
            
            # Apply rule-based correction logic (simulating LLM reasoning)
            action = "KEEP_INVALID"
            new_label = None
            confidence = 0.5
            reasoning = "Needs human review"
            
            # Remove obvious noise
            if len(text) <= 1 or text in [".", ",", "-", "|", "/", "\\"]:
                action = "REMOVE"
                new_label = None
                confidence = 0.95
                reasoning = "Single character or punctuation, likely noise"
            
            # Handle format mismatches with good suggestions
            elif best_suggestion and best_suggestion["confidence"] > 0.8:
                action = "REPLACE"
                new_label = best_suggestion["suggested_label"]
                confidence = best_suggestion["confidence"]
                reasoning = f"High confidence suggestion: {best_suggestion['reasoning']}"
            
            # Handle schema violations
            elif "not in CORE_LABELS" in reason:
                # Try to map common variations
                if "TOTAL" in invalid_label.upper():
                    action = "REPLACE"
                    new_label = "GRAND_TOTAL"
                    confidence = 0.75
                    reasoning = "Invalid TOTAL variant mapped to GRAND_TOTAL"
                elif "MERCHANT" in invalid_label.upper():
                    action = "REPLACE"
                    new_label = "MERCHANT_NAME"
                    confidence = 0.75
                    reasoning = "Invalid MERCHANT variant mapped to MERCHANT_NAME"
                elif best_suggestion and best_suggestion["confidence"] > 0.6:
                    action = "REPLACE"
                    new_label = best_suggestion["suggested_label"]
                    confidence = best_suggestion["confidence"]
                    reasoning = f"Moderate confidence suggestion: {best_suggestion['reasoning']}"
            
            # Handle format mismatches
            elif "Format mismatch" in reason:
                if best_suggestion:
                    action = "REPLACE"
                    new_label = best_suggestion["suggested_label"]
                    confidence = min(best_suggestion["confidence"], 0.8)
                    reasoning = f"Format mismatch corrected: {best_suggestion['reasoning']}"
                else:
                    action = "REMOVE"
                    new_label = None
                    confidence = 0.7
                    reasoning = "Format mismatch with no viable corrections"
            
            # Handle duplicates
            elif "Duplicate" in reason:
                action = "REMOVE"
                new_label = None
                confidence = 0.9
                reasoning = "Duplicate label removed (lower confidence)"
            
            corrections.append({
                "word_id": word_id,
                "action": action,
                "new_label": new_label,
                "confidence": confidence,
                "reasoning": reasoning,
                "original_text": text,
                "original_label": invalid_label,
            })
        
        return corrections


@tool
async def apply_corrections_to_labels(
    draft_labels: Dict[int, Dict],
    corrections: List[Dict]
) -> Dict[int, Dict]:
    """Apply LLM corrections to create final validated label set.
    
    Args:
        draft_labels: Original draft labels
        corrections: List of correction decisions from LLM
        
    Returns:
        Dict of final validated labels
    """
    validated_labels = draft_labels.copy()
    
    # Create correction lookup
    correction_map = {c["word_id"]: c for c in corrections}
    
    for word_id, correction in correction_map.items():
        action = correction["action"]
        new_label = correction.get("new_label")
        confidence = correction.get("confidence", 0.5)
        reasoning = correction.get("reasoning", "")
        
        if action == "REMOVE":
            # Remove the invalid label
            if word_id in validated_labels:
                del validated_labels[word_id]
        
        elif action == "REPLACE" and new_label:
            # Replace with corrected label
            validated_labels[word_id] = {
                "label": new_label,
                "confidence": confidence,
                "source": "second_pass_correction",
                "reasoning": reasoning,
                "validation_status": "corrected",
                "corrected_from": draft_labels.get(word_id, {}).get("label", "unknown"),
            }
        
        # KEEP_INVALID: leave as-is for human review
        elif action == "KEEP_INVALID":
            if word_id in validated_labels:
                validated_labels[word_id]["validation_status"] = "needs_review"
                validated_labels[word_id]["review_reason"] = reasoning
    
    return validated_labels


@tool
async def calculate_validation_metrics(
    draft_labels: Dict[int, Dict],
    validated_labels: Dict[int, Dict],
    corrections: List[Dict],
    essential_fields: List[str] = None
) -> Dict[str, Any]:
    """Calculate comprehensive validation metrics.
    
    Args:
        draft_labels: Original draft labels
        validated_labels: Final validated labels
        corrections: List of corrections applied
        essential_fields: List of essential label types to check
        
    Returns:
        Dict of validation metrics
    """
    if essential_fields is None:
        essential_fields = ["MERCHANT_NAME", "DATE", "TIME", "GRAND_TOTAL"]
    
    # Basic counts
    draft_count = len(draft_labels)
    validated_count = len(validated_labels)
    corrections_count = len(corrections)
    
    # Correction actions
    actions = [c["action"] for c in corrections]
    removed_count = actions.count("REMOVE")
    replaced_count = actions.count("REPLACE")
    needs_review_count = actions.count("KEEP_INVALID")
    
    # Essential field coverage
    validated_label_types = set(
        label_info["label"] for label_info in validated_labels.values()
    )
    found_essentials = essential_fields.intersection(validated_label_types)
    missing_essentials = set(essential_fields) - found_essentials
    
    # Quality metrics
    high_confidence_count = sum(
        1 for label_info in validated_labels.values()
        if label_info.get("confidence", 0) >= 0.8
    )
    
    validation_rate = validated_count / draft_count if draft_count > 0 else 0
    essential_coverage = len(found_essentials) / len(essential_fields)
    
    return {
        "draft_labels_count": draft_count,
        "validated_labels_count": validated_count,
        "corrections_applied": corrections_count,
        "labels_removed": removed_count,
        "labels_replaced": replaced_count,
        "labels_needing_review": needs_review_count,
        "validation_rate": validation_rate,
        "essential_fields_found": list(found_essentials),
        "essential_fields_missing": list(missing_essentials),
        "essential_coverage": essential_coverage,
        "high_confidence_labels": high_confidence_count,
        "quality_score": (validation_rate + essential_coverage) / 2,
    }


async def second_pass_validator_node(state: ReceiptProcessingState) -> ReceiptProcessingState:
    """Final validation pass using LLM analysis of similar terms and corrections.
    
    This node:
    1. Analyzes invalid labels with their similar term suggestions
    2. Uses LLM to make final correction decisions
    3. Applies corrections to create validated label set
    4. Calculates comprehensive validation metrics
    
    Based on the validation pipeline pattern from infra/validation_pipeline/lambda.py
    
    Context Engineering:
    - WRITE: validated_labels as final output
    - SELECT: Only invalid labels sent to LLM for correction
    - COMPRESS: Multiple data sources (draft, invalid, similar) into single decision
    """
    logger.info("Starting second-pass validation with LLM corrections")
    
    # Get inputs from state
    draft_labels = state.get("draft_labels", {})
    invalid_labels = state.get("invalid_labels", [])
    similar_terms = state.get("similar_terms", {})
    
    if not draft_labels:
        logger.warning("No draft labels to validate")
        state["validated_labels"] = {}
        state["validation_metrics"] = {}
        return state
    
    # Start with valid labels from first pass
    valid_from_first_pass = {
        word_id: label_info
        for word_id, label_info in draft_labels.items()
        if word_id not in [inv["word_id"] for inv in invalid_labels]
    }
    
    corrections = []
    
    # Only run LLM analysis if there are invalid labels
    if invalid_labels:
        logger.info(f"Analyzing {len(invalid_labels)} invalid labels with LLM")
        
        corrections = await analyze_corrections_with_llm.ainvoke({
            "draft_labels": draft_labels,
            "invalid_labels": invalid_labels,
            "similar_terms": similar_terms,
            "receipt_context": {
                "merchant_name": state.get("merchant_name"),
                "receipt_id": state.get("receipt_id"),
            }
        })
    
    # Apply corrections to create final validated set
    validated_labels = await apply_corrections_to_labels.ainvoke({
        "draft_labels": valid_from_first_pass,  # Start with valid labels
        "corrections": corrections
    })
    
    # Calculate comprehensive metrics
    validation_metrics = await calculate_validation_metrics.ainvoke({
        "draft_labels": draft_labels,
        "validated_labels": validated_labels,
        "corrections": corrections,
        "essential_fields": ["MERCHANT_NAME", "DATE", "TIME", "GRAND_TOTAL"]
    })
    
    # Update state with final results
    state["validated_labels"] = validated_labels
    state["label_corrections"] = corrections
    state["validation_metrics"] = validation_metrics
    
    # Update coverage and quality metrics
    total_words = len(state.get("receipt_words", []))
    state["final_coverage"] = len(validated_labels) / total_words * 100 if total_words > 0 else 0
    state["validation_quality"] = validation_metrics["quality_score"]
    
    # Add decision tracking
    state["decisions"].append({
        "node": "second_pass_validator",
        "action": "finalized_validation",
        "draft_count": len(draft_labels),
        "validated_count": len(validated_labels),
        "corrections_applied": len(corrections),
        "validation_rate": f"{validation_metrics['validation_rate']:.1%}",
        "essential_coverage": f"{validation_metrics['essential_coverage']:.1%}",
        "quality_score": f"{validation_metrics['quality_score']:.1%}",
        "final_coverage": f"{state['final_coverage']:.1f}%",
        "timestamp": datetime.now().isoformat(),
    })
    
    logger.info(
        f"Second-pass validation complete: {len(validated_labels)} final labels "
        f"({validation_metrics['validation_rate']:.1%} validation rate, "
        f"{validation_metrics['quality_score']:.1%} quality score)"
    )
    
    return state