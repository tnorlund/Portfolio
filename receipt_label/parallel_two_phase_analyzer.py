#!/usr/bin/env python3
"""
Parallel two-phase line-item analysis using LangGraph.

Phase 1: 120B model for complex currency analysis (GRAND_TOTAL, TAX, LINE_TOTAL, SUBTOTAL)
Phase 2: 20B model for parallel line-item component analysis (PRODUCT_NAME, QUANTITY, UNIT_PRICE)
"""

import os
import logging
import asyncio
from typing import List, Dict, TypedDict, Optional
from dataclasses import dataclass
from pydantic import BaseModel, Field

from receipt_label.receipt_models import CurrencyLabel, LabelType
from receipt_label.llm_classifier import analyze_with_ollama
from receipt_label.constants import CORE_LABELS
from receipt_dynamo.entities import ReceiptLine

logger = logging.getLogger(__name__)

try:
    from langgraph.graph import StateGraph, END
    from langchain_core.output_parsers import PydanticOutputParser
    from langchain_core.prompts import PromptTemplate
    from receipt_label.langchain_validation.ollama_turbo_client import create_ollama_turbo_client
    LANGGRAPH_AVAILABLE = True
except ImportError:
    LANGGRAPH_AVAILABLE = False


# State management for the graph
class AnalysisState(TypedDict):
    # Input data
    formatted_receipt_text: str
    currency_contexts: List[Dict]
    lines: List[ReceiptLine]
    receipt_id: str
    
    # Phase 1 results
    currency_labels: Optional[List[CurrencyLabel]]
    line_total_labels: Optional[List[CurrencyLabel]]
    
    # Phase 2 results  
    line_item_labels: Optional[List[CurrencyLabel]]
    
    # Final results
    all_labels: Optional[List[CurrencyLabel]]
    confidence_score: Optional[float]
    processing_time: Optional[float]


# Pydantic models for Phase 2 analysis
class LineItemComponents(BaseModel):
    """Components found for a specific line item."""
    
    product_name: Optional[str] = Field(default=None, description="Product description text")
    quantity: Optional[str] = Field(default=None, description="Quantity with units")
    unit_price: Optional[float] = Field(default=None, description="Price per single unit")


class ParallelLineItemResponse(BaseModel):
    """Response for analyzing multiple line items in parallel."""
    
    line_items: List[LineItemComponents] = Field(
        description="List of line item components, one for each target line"
    )


@dataclass
class LineItemTarget:
    """A line item target for parallel analysis."""
    line_ids: List[int]
    line_text: str
    line_total_amount: float
    line_total_label: CurrencyLabel


async def phase1_currency_analysis(state: AnalysisState) -> AnalysisState:
    """Phase 1: Complex currency analysis using 120B model."""
    
    print(f"🔵 PHASE 1: Currency Analysis (120B model)")
    print("Analyzing GRAND_TOTAL, TAX, LINE_TOTAL, SUBTOTAL amounts")
    
    # Use existing currency analysis with 120B model
    currency_labels = await analyze_with_ollama(
        state["formatted_receipt_text"],
        state["currency_contexts"], 
        receipt_id=state["receipt_id"],
        lines=state["lines"]
    )
    
    # Extract LINE_TOTAL labels for Phase 2
    line_total_labels = [label for label in currency_labels if label.label_type == LabelType.LINE_TOTAL]
    
    print(f"✅ Phase 1 complete: {len(currency_labels)} currency labels")
    print(f"   Found {len(line_total_labels)} LINE_TOTAL amounts for Phase 2")
    
    return {
        **state,
        "currency_labels": currency_labels,
        "line_total_labels": line_total_labels,
    }


async def phase2_parallel_line_items(state: AnalysisState) -> AnalysisState:
    """Phase 2: Parallel line-item analysis using 20B model."""
    
    print(f"\n🔴 PHASE 2: Parallel Line-Item Analysis (20B model)")
    
    line_total_labels = state["line_total_labels"]
    if not line_total_labels:
        print("   No LINE_TOTAL amounts found - skipping Phase 2")
        return {**state, "line_item_labels": []}
    
    # Build targets using visual line formatting
    targets = _build_line_item_targets_parallel(state["lines"], line_total_labels)
    
    if not targets:
        print("   No valid targets found - skipping Phase 2")
        return {**state, "line_item_labels": []}
    
    print(f"   Analyzing {len(targets)} line items in parallel")
    
    # Analyze all targets in parallel using 20B model
    line_item_labels = await _analyze_line_items_parallel(targets, state["receipt_id"])
    
    print(f"✅ Phase 2 complete: {len(line_item_labels)} line-item labels")
    
    return {
        **state,
        "line_item_labels": line_item_labels,
    }


async def combine_results(state: AnalysisState) -> AnalysisState:
    """Combine Phase 1 and Phase 2 results."""
    
    print(f"\n🔗 COMBINING RESULTS")
    
    currency_labels = state["currency_labels"] or []
    line_item_labels = state["line_item_labels"] or []
    
    all_labels = currency_labels + line_item_labels
    
    # Calculate average confidence
    if all_labels:
        confidence_score = sum(label.confidence for label in all_labels) / len(all_labels)
    else:
        confidence_score = 0.0
    
    print(f"📋 PARALLEL ANALYSIS COMPLETE")
    print(f"   Phase 1: {len(currency_labels)} currency labels")
    print(f"   Phase 2: {len(line_item_labels)} line-item labels")
    print(f"   Total: {len(all_labels)} labels discovered")
    print(f"   Average confidence: {confidence_score:.3f}")
    
    return {
        **state,
        "all_labels": all_labels,
        "confidence_score": confidence_score,
    }


def _build_line_item_targets_parallel(lines: List[ReceiptLine], line_total_labels: List[CurrencyLabel]) -> List[LineItemTarget]:
    """Build targets for parallel line-item analysis."""
    
    from receipt_label.prompt_formatting.lines import format_receipt_lines_visual_order
    
    # Get properly formatted visual lines
    visual_receipt_text = format_receipt_lines_visual_order(lines)
    visual_lines = visual_receipt_text.split('\n')
    
    targets = []
    
    for label in line_total_labels:
        if not label.line_ids:
            continue
            
        # Find which visual line contains the currency amount
        amount_text = f"{label.value:.2f}"
        target_visual_line = None
        
        for visual_line in visual_lines:
            if amount_text in visual_line:
                target_visual_line = visual_line
                break
        
        if target_visual_line:
            target = LineItemTarget(
                line_ids=label.line_ids,
                line_text=target_visual_line,
                line_total_amount=label.value,
                line_total_label=label
            )
            targets.append(target)
    
    return targets


async def _analyze_line_items_parallel(targets: List[LineItemTarget], receipt_id: str = None) -> List[CurrencyLabel]:
    """Analyze all line items in parallel using 20B model."""
    
    if not targets:
        return []
    
    # Create 20B model client for faster line-item analysis
    llm_20b = create_ollama_turbo_client(
        model="gpt-oss:20b",  # Smaller, faster model for simpler task
        base_url="https://ollama.com",
        api_key=os.getenv("OLLAMA_API_KEY"),
        temperature=0.0,
    )
    
    # Create output parser
    output_parser = PydanticOutputParser(pydantic_object=ParallelLineItemResponse)
    
    # Build prompt for parallel analysis
    template = """You are analyzing multiple line items from a receipt in parallel.

LINE ITEMS TO ANALYZE:
{line_items_text}

COMPONENT DEFINITIONS:
- PRODUCT_NAME: The item description (e.g., "CHIPS", "GARLIC", "SUGAR")
- QUANTITY: The amount purchased (e.g., "2", "1 lb", "1.5")
- UNIT_PRICE: The price per unit (if present)

TASK:
For each line item above, extract the PRODUCT_NAME, QUANTITY, and UNIT_PRICE components.
Return results in the same order as the input line items.

FORMAT UNDERSTANDING:
Lines are formatted as "PRODUCT_NAME AMOUNT" (e.g., "CHIPS 6.29", "GARLIC 3.99")
- Look for descriptive text as PRODUCT_NAME
- Look for quantities with units (lb, oz, ea) 
- Look for unit prices (often with @ symbol)

{format_instructions}"""

    # Build line items text for the prompt
    line_items_text = ""
    for i, target in enumerate(targets, 1):
        line_items_text += f"{i}. Line {target.line_ids}: '{target.line_text}' (LINE_TOTAL: ${target.line_total_amount:.2f})\n"
    
    prompt_template = PromptTemplate(
        template=template,
        input_variables=["line_items_text"],
        partial_variables={
            "format_instructions": output_parser.get_format_instructions(),
        }
    )
    
    # Create chain and execute
    chain = prompt_template | llm_20b | output_parser
    
    try:
        print(f"   🤖 Calling Ollama 20B model for parallel line-item analysis...")
        
        response = await chain.ainvoke(
            {"line_items_text": line_items_text},
            config={
                "metadata": {
                    "receipt_id": receipt_id or "unknown",
                    "analysis_type": "parallel_line_items",
                    "model": "20b",
                    "target_count": len(targets),
                },
                "tags": ["parallel-line-items", "20b-model"],
            }
        )
        
        # Convert response to CurrencyLabel objects
        line_item_labels = []
        
        for i, (target, components) in enumerate(zip(targets, response.line_items)):
            # Convert each component to a label with all required fields
            if components.product_name:
                product_label = CurrencyLabel(
                    word_text=components.product_name,
                    label_type=LabelType.PRODUCT_NAME,
                    line_number=target.line_ids[0] if target.line_ids else 0,
                    line_ids=target.line_ids,
                    confidence=0.95,
                    reasoning=f"Product name extracted from line: '{target.line_text}'",
                    value=0.0,  # Not applicable for product names
                    position_y=0.5,  # Default middle position
                    context=target.line_text
                )
                line_item_labels.append(product_label)
            
            if components.quantity:
                quantity_label = CurrencyLabel(
                    word_text=components.quantity,
                    label_type=LabelType.QUANTITY,
                    line_number=target.line_ids[0] if target.line_ids else 0,
                    line_ids=target.line_ids,
                    confidence=0.95,
                    reasoning=f"Quantity extracted from line: '{target.line_text}'",
                    value=0.0,
                    position_y=0.5,
                    context=target.line_text
                )
                line_item_labels.append(quantity_label)
            
            if components.unit_price:
                unit_price_label = CurrencyLabel(
                    word_text=str(components.unit_price),
                    label_type=LabelType.UNIT_PRICE,
                    line_number=target.line_ids[0] if target.line_ids else 0,
                    line_ids=target.line_ids,
                    confidence=0.95,
                    reasoning=f"Unit price extracted from line: '{target.line_text}'",
                    value=components.unit_price,
                    position_y=0.5,
                    context=target.line_text
                )
                line_item_labels.append(unit_price_label)
        
        print(f"   ✅ Parallel analysis complete: {len(line_item_labels)} components found")
        return line_item_labels
        
    except Exception as e:
        logger.error(f"Parallel line-item analysis failed: {e}")
        return []


def create_parallel_analysis_graph():
    """Create the LangGraph workflow for parallel two-phase analysis."""
    
    if not LANGGRAPH_AVAILABLE:
        raise ImportError("LangGraph not available - install with: pip install langgraph")
    
    # Create the graph
    workflow = StateGraph(AnalysisState)
    
    # Add nodes
    workflow.add_node("phase1_currency", phase1_currency_analysis)
    workflow.add_node("phase2_line_items", phase2_parallel_line_items)  
    workflow.add_node("combine_results", combine_results)
    
    # Define the flow
    workflow.set_entry_point("phase1_currency")
    workflow.add_edge("phase1_currency", "phase2_line_items")
    workflow.add_edge("phase2_line_items", "combine_results")
    workflow.add_edge("combine_results", END)
    
    return workflow.compile()


async def analyze_receipt_parallel_two_phase(
    client,
    image_id: str,
    receipt_id: int,
    update_labels: bool = False,
    dry_run: bool = False
):
    """Drop-in replacement using parallel LangGraph workflow."""
    
    import time
    from receipt_label.text_reconstruction import ReceiptTextReconstructor
    from receipt_label.validator import validate_arithmetic_relationships
    from receipt_label.receipt_models import ReceiptAnalysis
    
    receipt_identifier = f"{image_id}/{receipt_id}"
    print(f"\n🚀 PARALLEL TWO-PHASE ANALYSIS: {receipt_identifier}")
    print("=" * 80)
    print("Phase 1: 120B model (currency) | Phase 2: 20B model (line items) - PARALLEL")
    print()
    
    start_time = time.time()
    
    # Get receipt data  
    lines = client.list_receipt_lines_from_receipt(image_id, receipt_id)
    print(f"📊 Found {len(lines)} lines")
    
    # Reconstruct text
    reconstructor = ReceiptTextReconstructor()
    formatted_receipt_text, text_groups = reconstructor.reconstruct_receipt(lines)
    currency_contexts = reconstructor.extract_currency_context(text_groups)
    print(f"💰 Found {len(currency_contexts)} currency amounts")
    
    # Create and run the parallel workflow
    graph = create_parallel_analysis_graph()
    
    # Initial state
    initial_state = AnalysisState(
        formatted_receipt_text=formatted_receipt_text,
        currency_contexts=currency_contexts,
        lines=lines,
        receipt_id=receipt_identifier,
        currency_labels=None,
        line_total_labels=None,
        line_item_labels=None,
        all_labels=None,
        confidence_score=None,
        processing_time=None
    )
    
    # Execute the parallel workflow
    final_state = await graph.ainvoke(initial_state)
    
    processing_time = time.time() - start_time
    final_state["processing_time"] = processing_time
    
    print(f"\n⚡ PARALLEL EXECUTION TIME: {processing_time:.2f}s")
    
    # Validate results
    discovered_labels = final_state["all_labels"]
    validation_total = validate_arithmetic_relationships(discovered_labels, known_total=0.0)
    
    # Update labels if requested (same logic as two_phase_analyzer.py)
    label_update_results = []
    if update_labels and discovered_labels:
        from receipt_label.label_updater import ReceiptLabelUpdater, display_label_update_results
        
        label_updater = ReceiptLabelUpdater(client)
        label_update_results = await label_updater.apply_currency_labels(
            image_id=image_id,
            receipt_id=receipt_id,
            currency_labels=discovered_labels,
            dry_run=dry_run
        )
        
        if dry_run:
            print("\n🔍 DRY RUN - Label Updates That Would Be Applied:")
        else:
            print("\n📝 Applied Label Updates:")
        display_label_update_results(label_update_results)
    
    # Return results in same format as original analyzer
    return ReceiptAnalysis(
        discovered_labels=discovered_labels,
        confidence_score=final_state["confidence_score"],
        validation_total=validation_total,
        processing_time=processing_time,
        receipt_id=receipt_identifier,
        known_total=0.0,
        validation_results={},
        total_lines=len(lines),
        formatted_text=formatted_receipt_text[:500] + "..." if len(formatted_receipt_text) > 500 else formatted_receipt_text
    )


if __name__ == "__main__":
    # Example usage
    print("🚀 Parallel Two-Phase Analyzer with LangGraph")
    print("Phase 1: 120B model (currency analysis)")
    print("Phase 2: 20B model (parallel line-item analysis)")