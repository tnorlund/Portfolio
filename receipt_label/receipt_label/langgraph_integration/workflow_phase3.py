"""Phase 3 LangGraph workflow implementing the complete validation pipeline.

This workflow follows the exact pattern from your diagram:
1. DraftLabellerNode - LLM or rule-based labeling  
2. FirstPassValidatorNode - Schema/regex validation
3. SimilarTermRetrieverNode - Pinecone similarity queries
4. SecondPassValidatorNode - LLM corrections with similar terms
5. PersistToDynamoNode - Write to DynamoDB
"""

import logging
from typing import Dict, Any

from langgraph.graph import StateGraph, END

from .state import ReceiptProcessingState
from .nodes import (
    draft_labeling_node,
    first_pass_validator_node,
    similar_term_retriever_node,
    second_pass_validator_node,
    persist_to_dynamo_node,
    audit_trail_node,
)

logger = logging.getLogger(__name__)


def create_phase3_workflow():
    """Create the Phase 3 validation pipeline workflow.
    
    This workflow implements the complete validation pipeline:
    START → DraftLabeling → FirstPassValidator → SimilarTermRetriever 
    → SecondPassValidator → PersistToDynamo → AuditTrail → END
    
    Returns:
        Compiled LangGraph workflow
    """
    workflow = StateGraph(ReceiptProcessingState)
    
    # Add all nodes
    workflow.add_node("draft_labeling", draft_labeling_node)
    workflow.add_node("first_pass_validator", first_pass_validator_node)
    workflow.add_node("similar_term_retriever", similar_term_retriever_node)
    workflow.add_node("second_pass_validator", second_pass_validator_node)
    workflow.add_node("persist_to_dynamo", persist_to_dynamo_node)
    workflow.add_node("audit_trail", audit_trail_node)
    
    # Define linear flow (no conditional branching in Phase 3)
    workflow.set_entry_point("draft_labeling")
    workflow.add_edge("draft_labeling", "first_pass_validator")
    workflow.add_edge("first_pass_validator", "similar_term_retriever")
    workflow.add_edge("similar_term_retriever", "second_pass_validator")
    workflow.add_edge("second_pass_validator", "persist_to_dynamo")
    workflow.add_edge("persist_to_dynamo", "audit_trail")
    workflow.add_edge("audit_trail", END)
    
    return workflow.compile()


async def run_phase3_validation(
    receipt_id: int,
    image_id: str,
    receipt_words: list,
    pattern_matches: dict = None,
    currency_columns: list = None,
    merchant_name: str = None
) -> ReceiptProcessingState:
    """Run the complete Phase 3 validation pipeline on a receipt.
    
    Args:
        receipt_id: Receipt identifier
        image_id: Image UUID
        receipt_words: List of OCR word dictionaries
        pattern_matches: Optional pattern detection results
        currency_columns: Optional spatial currency analysis
        merchant_name: Optional merchant name
        
    Returns:
        Final state with validated labels and metrics
    """
    logger.info(f"Starting Phase 3 validation for receipt {receipt_id}")
    
    # Create workflow
    workflow = create_phase3_workflow()
    
    # Prepare initial state
    initial_state = {
        "receipt_id": receipt_id,
        "image_id": image_id,
        "receipt_words": receipt_words,
        "pattern_matches": pattern_matches or {},
        "currency_columns": currency_columns or [],
        "merchant_name": merchant_name,
        "decisions": [],  # Track all node decisions
    }
    
    # Execute workflow
    final_state = await workflow.ainvoke(initial_state)
    
    logger.info(
        f"Phase 3 validation complete for receipt {receipt_id}: "
        f"{len(final_state.get('validated_labels', {}))} final labels, "
        f"status: {final_state.get('dynamo_status', 'unknown')}"
    )
    
    return final_state


# Example usage and testing function
async def test_phase3_workflow():
    """Test the Phase 3 workflow with sample data."""
    
    # Sample receipt data
    sample_receipt_words = [
        {"word_id": 1, "line_id": 1, "text": "WALMART", "x": 0.5, "y": 0.9},
        {"word_id": 2, "line_id": 1, "text": "SUPERCENTER", "x": 0.7, "y": 0.9},
        {"word_id": 3, "line_id": 2, "text": "01/15/2024", "x": 0.2, "y": 0.8},
        {"word_id": 4, "line_id": 2, "text": "10:30", "x": 0.6, "y": 0.8},
        {"word_id": 5, "line_id": 10, "text": "TOTAL", "x": 0.3, "y": 0.1},
        {"word_id": 6, "line_id": 10, "text": "$45.99", "x": 0.7, "y": 0.1},
    ]
    
    sample_pattern_matches = {
        "MERCHANT_NAME": [{"word_id": 1, "confidence": 0.9}],
        "DATE": [{"word_id": 3, "confidence": 0.95}],
        "TIME": [{"word_id": 4, "confidence": 0.9}],
        "CURRENCY": [{"word_id": 6, "confidence": 0.95, "value": 45.99}],
    }
    
    sample_currency_columns = [
        {
            "prices": [
                {"word_id": 6, "value": 45.99, "y_position": 0.1}
            ]
        }
    ]
    
    # Run workflow
    result = await run_phase3_validation(
        receipt_id=12345,
        image_id="550e8400-e29b-41d4-a716-446655440000",
        receipt_words=sample_receipt_words,
        pattern_matches=sample_pattern_matches,
        currency_columns=sample_currency_columns,
        merchant_name="Walmart"
    )
    
    # Print results
    print("=== Phase 3 Validation Results ===")
    print(f"Validated Labels: {len(result.get('validated_labels', {}))}")
    print(f"Validation Quality: {result.get('validation_quality', 0):.1%}")
    print(f"Final Coverage: {result.get('final_coverage', 0):.1f}%")
    print(f"Dynamo Status: {result.get('dynamo_status', 'unknown')}")
    
    validation_metrics = result.get('validation_metrics', {})
    if validation_metrics:
        print(f"Essential Coverage: {validation_metrics.get('essential_coverage', 0):.1%}")
        print(f"Found Essentials: {validation_metrics.get('essential_fields_found', [])}")
        print(f"Missing Essentials: {validation_metrics.get('essential_fields_missing', [])}")
    
    # Print decision trail
    print("\n=== Decision Trail ===")
    for decision in result.get('decisions', []):
        print(f"{decision['node']}: {decision['action']} at {decision['timestamp']}")
    
    return result


if __name__ == "__main__":
    import asyncio
    asyncio.run(test_phase3_workflow())