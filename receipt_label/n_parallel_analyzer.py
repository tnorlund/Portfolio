#!/usr/bin/env python3
"""
N-Parallel two-phase line-item analysis using LangGraph.

Phase 1: 120B model for currency analysis (GRAND_TOTAL, TAX, LINE_TOTAL, SUBTOTAL)
Phase 2: N parallel nodes, each using 20B model for individual line-item analysis

Architecture:
    Receipt Input 
         ↓
    Phase 1 (120B) - Currency Analysis 
         ↓
    Phase 2 - N Parallel Nodes (each 20B model)
         ├─ LINE_TOTAL_1 → 20B analysis
         ├─ LINE_TOTAL_2 → 20B analysis  
         └─ LINE_TOTAL_N → 20B analysis
         ↓
    Combine Results
"""

import os
import logging
import asyncio
from typing import List, Dict, TypedDict, Optional, Any, Annotated
from dataclasses import dataclass
from pydantic import BaseModel, Field
from operator import add

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


# State management for N-parallel graph with concurrent updates
class NParallelState(TypedDict):
    # Input data
    formatted_receipt_text: str
    currency_contexts: List[Dict]
    lines: List[ReceiptLine]
    receipt_id: str
    
    # Phase 1 results
    currency_labels: Optional[List[CurrencyLabel]]
    line_total_labels: Optional[List[CurrencyLabel]]
    
    # Phase 2 parallel results - use Annotated for concurrent updates
    line_item_results: Annotated[List[CurrencyLabel], add]
    
    # Final results
    all_labels: Optional[List[CurrencyLabel]]
    confidence_score: Optional[float]
    processing_time: Optional[float]


# Pydantic model for individual line-item analysis (20B model)
class WordLabel(BaseModel):
    """Label for a single word."""
    word: str = Field(description="The exact word from the receipt")
    label: str = Field(description="Label type: PRODUCT_NAME, QUANTITY, or UNIT_PRICE")
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence in this label")

class SingleLineItemResponse(BaseModel):
    """Response for analyzing a single line item with per-word labels."""
    
    word_labels: List[WordLabel] = Field(description="Labels for individual words")
    reasoning: str = Field(description="Explanation of the analysis")


@dataclass
class LineItemTarget:
    """A single line item target for 20B analysis."""
    target_id: str  # "line_total_0", "line_total_1", etc.
    line_ids: List[int]
    line_text: str
    line_total_amount: float
    line_total_label: CurrencyLabel


async def phase1_currency_analysis(state: NParallelState) -> NParallelState:
    """Phase 1: Complex currency analysis using 120B model."""
    
    print(f"🔵 PHASE 1: Currency Analysis (120B model)")
    print("Finding GRAND_TOTAL, TAX, LINE_TOTAL, SUBTOTAL amounts")
    
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
    print(f"   Found {len(line_total_labels)} LINE_TOTAL amounts")
    for i, label in enumerate(line_total_labels):
        print(f"   └─ LINE_TOTAL_{i}: ${label.value:.2f} on lines {label.line_ids}")
    
    return {
        **state,
        "currency_labels": currency_labels,
        "line_total_labels": line_total_labels,
        "line_item_results": [],  # Initialize as empty list for concurrent updates
    }


async def analyze_single_line_item(
    target: LineItemTarget,
    state: NParallelState
) -> List[CurrencyLabel]:
    """Analyze a single line item using 20B model."""
    
    print(f"   🔸 {target.target_id}: Analyzing '${target.line_total_amount:.2f}' → '{target.line_text}'")
    
    # Create 20B model client for this specific line item
    llm_20b = create_ollama_turbo_client(
        model="gpt-oss:20b",
        base_url="https://ollama.com",
        api_key=os.getenv("OLLAMA_API_KEY"),
        temperature=0.0,
    )
    
    # Create output parser
    output_parser = PydanticOutputParser(pydantic_object=SingleLineItemResponse)
    
    # Per-word labeling prompt
    template = """You are analyzing ONE line item from a receipt and labeling individual words.

LINE TO ANALYZE:
"{line_text}"

TASK: Label each word individually with one of these types:
- PRODUCT_NAME: Words describing the item (e.g., "ORGANIC", "BANANAS", "CHIPS")
- QUANTITY: Words indicating amount (e.g., "2", "LB", "CT", "EACH")  
- UNIT_PRICE: Price amounts (e.g., "1.99", "$5.98")

For each word in the line, determine its label type. If a word doesn't fit any category, don't include it.

Example:
Line: "ORGANIC BANANAS 2 LB 3.99"
Response: [
  {{"word": "ORGANIC", "label": "PRODUCT_NAME", "confidence": 0.9}},
  {{"word": "BANANAS", "label": "PRODUCT_NAME", "confidence": 0.95}},
  {{"word": "2", "label": "QUANTITY", "confidence": 0.9}},
  {{"word": "LB", "label": "QUANTITY", "confidence": 0.9}},
  {{"word": "3.99", "label": "UNIT_PRICE", "confidence": 0.95}}
]

{format_instructions}"""

    prompt_template = PromptTemplate(
        template=template,
        input_variables=["line_text"],
        partial_variables={
            "format_instructions": output_parser.get_format_instructions(),
        }
    )
    
    # Create chain and execute
    chain = prompt_template | llm_20b | output_parser
    
    try:
        response = await chain.ainvoke(
            {
                "line_text": target.line_text
            },
            config={
                "metadata": {
                    "receipt_id": state["receipt_id"],
                    "target_id": target.target_id,
                    "model": "20b",
                    "line_total": target.line_total_amount,
                },
                "tags": ["single-line-item", "20b-model"],
            }
        )
        
        # Convert word labels to CurrencyLabel objects
        line_item_labels = []
        
        for word_label in response.word_labels:
            # Determine label type and value
            if word_label.label == "PRODUCT_NAME":
                label_type = LabelType.PRODUCT_NAME
                value = 0.0
            elif word_label.label == "QUANTITY":
                label_type = LabelType.QUANTITY
                value = 0.0
            elif word_label.label == "UNIT_PRICE":
                label_type = LabelType.UNIT_PRICE
                try:
                    # Parse currency value
                    value = float(word_label.word.replace('$', '').replace(',', ''))
                except:
                    value = 0.0
            else:
                continue  # Skip unknown label types
            
            currency_label = CurrencyLabel(
                word_text=word_label.word,
                label_type=label_type,
                line_number=target.line_ids[0] if target.line_ids else 0,
                line_ids=target.line_ids,
                confidence=word_label.confidence,
                reasoning=f"Word-level {word_label.label} from {target.target_id}: {response.reasoning}",
                value=value,
                position_y=0.5,
                context=target.line_text
            )
            line_item_labels.append(currency_label)
        
        print(f"   ✅ {target.target_id}: Found {len(line_item_labels)} components")
        return line_item_labels
        
    except Exception as e:
        logger.error(f"{target.target_id} analysis failed: {e}")
        return []


def _build_line_item_targets(lines: List[ReceiptLine], line_total_labels: List[CurrencyLabel]) -> List[LineItemTarget]:
    """Build targets for N-parallel line-item analysis."""
    
    from receipt_label.prompt_formatting.lines import format_receipt_lines_visual_order
    
    # Get properly formatted visual lines
    visual_receipt_text = format_receipt_lines_visual_order(lines)
    visual_lines = visual_receipt_text.split('\n')
    
    targets = []
    
    for i, label in enumerate(line_total_labels):
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
                target_id=f"line_total_{i}",
                line_ids=label.line_ids,
                line_text=target_visual_line,
                line_total_amount=label.value,
                line_total_label=label
            )
            targets.append(target)
    
    return targets


async def combine_results(state: NParallelState) -> NParallelState:
    """Combine Phase 1 and all Phase 2 results."""
    
    print(f"\n🔗 COMBINING N-PARALLEL RESULTS")
    
    raw_currency_labels = state["currency_labels"] or []
    
    # DEDUPLICATION: Remove duplicate Phase 1 currency classifications
    # Group by (word_text, label_type, line_ids) and keep highest confidence
    currency_dedup_map = {}
    for label in raw_currency_labels:
        # Create unique key for this label position and type
        line_ids_key = tuple(sorted(label.line_ids)) if label.line_ids else ()
        dedup_key = (label.word_text, label.label_type.value, line_ids_key)
        
        # Keep the highest confidence version
        if dedup_key not in currency_dedup_map or label.confidence > currency_dedup_map[dedup_key].confidence:
            currency_dedup_map[dedup_key] = label
    
    currency_labels = list(currency_dedup_map.values())
    duplicates_removed = len(raw_currency_labels) - len(currency_labels)
    
    if duplicates_removed > 0:
        print(f"   Deduplicated Phase 1: {len(raw_currency_labels)} → {len(currency_labels)} (-{duplicates_removed} duplicates)")
    
    # Collect all line-item labels from parallel results
    # line_item_results is now a list of labels from all parallel nodes
    line_item_results = state["line_item_results"] or []
    raw_line_item_labels = line_item_results  # Already a flat list
    
    print(f"   Received {len(raw_line_item_labels)} total components from parallel nodes")
    
    # Phase 2 line-item labels (conflict resolution happens during label application)
    line_item_labels = raw_line_item_labels
    
    print(f"   Phase 2 labels: {len(line_item_labels)} (conflict filtering during application)")
    
    all_labels = currency_labels + line_item_labels
    
    # Calculate average confidence
    if all_labels:
        confidence_score = sum(label.confidence for label in all_labels) / len(all_labels)
    else:
        confidence_score = 0.0
    
    print(f"📋 N-PARALLEL ANALYSIS COMPLETE")
    print(f"   Phase 1: {len(currency_labels)} currency labels")
    print(f"   Phase 2: {len(line_item_labels)} line-item labels")
    print(f"   Total: {len(all_labels)} labels discovered")
    print(f"   Average confidence: {confidence_score:.3f}")
    
    return {
        **state,
        "all_labels": all_labels,
        "confidence_score": confidence_score,
    }


def create_n_parallel_graph(line_total_count: int):
    """Create LangGraph with N parallel nodes for line-item analysis."""
    
    if not LANGGRAPH_AVAILABLE:
        raise ImportError("LangGraph not available")
    
    # Create the graph
    workflow = StateGraph(NParallelState)
    
    # Add Phase 1 node
    workflow.add_node("phase1_currency", phase1_currency_analysis)
    
    # Add N parallel nodes for Phase 2 (one per LINE_TOTAL)
    for i in range(line_total_count):
        node_name = f"phase2_line_{i}"
        
        # Create a closure to capture the target index
        def create_line_analyzer(target_index: int):
            async def analyze_line_node(state: NParallelState) -> NParallelState:
                line_total_labels = state["line_total_labels"] or []
                
                if target_index >= len(line_total_labels):
                    # No target for this index - return empty list for annotated field
                    return {
                        "line_item_results": []
                    }
                
                # Build target for this specific LINE_TOTAL
                targets = _build_line_item_targets(state["lines"], line_total_labels)
                
                if target_index >= len(targets):
                    # No target built - return empty list for annotated field
                    return {
                        "line_item_results": []
                    }
                
                target = targets[target_index]
                
                # Analyze this single target
                labels = await analyze_single_line_item(target, state)
                
                # Return ONLY the annotated field to avoid concurrent updates to other keys
                # LangGraph will handle the concurrent updates automatically
                return {
                    "line_item_results": labels  # This will be appended to the list
                }
            
            return analyze_line_node
        
        workflow.add_node(node_name, create_line_analyzer(i))
    
    # Add combine results node
    workflow.add_node("combine_results", combine_results)
    
    # Define the flow
    workflow.set_entry_point("phase1_currency")
    
    # Phase 1 connects to all Phase 2 parallel nodes
    for i in range(line_total_count):
        node_name = f"phase2_line_{i}"
        workflow.add_edge("phase1_currency", node_name)
        workflow.add_edge(node_name, "combine_results")
    
    # Combine results goes to END
    workflow.add_edge("combine_results", END)
    
    return workflow.compile()


def _get_merchant_name_for_receipt(client, image_id: str, receipt_id: int) -> str:
    """Get merchant name from ReceiptMetadata or return 'unknown'."""
    try:
        metadata = client.get_receipt_metadata(image_id, receipt_id)
        # Use canonical name if available, otherwise use merchant_name
        merchant_name = metadata.canonical_merchant_name or metadata.merchant_name
        return merchant_name if merchant_name else "unknown"
    except Exception:
        # If no metadata found or error occurs, use generic name
        return "unknown"


async def analyze_receipt_n_parallel(
    client,
    image_id: str,
    receipt_id: int,
    update_labels: bool = False,
    dry_run: bool = False
):
    """N-Parallel receipt analysis with dynamic node creation."""
    
    import time
    from receipt_label.text_reconstruction import ReceiptTextReconstructor
    from receipt_label.validator import validate_arithmetic_relationships
    from receipt_label.receipt_models import ReceiptAnalysis
    
    receipt_identifier = f"{image_id}/{receipt_id}"
    print(f"\n🚀 N-PARALLEL TWO-PHASE ANALYSIS: {receipt_identifier}")
    print("=" * 80)
    print("Phase 1: 120B model | Phase 2: N parallel 20B models (one per LINE_TOTAL)")
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
    
    # Estimate parallel node count from currency contexts (avoid extra LLM call)
    # Most receipts have 2-6 line items, so use conservative estimate
    estimated_line_total_count = min(max(len(currency_contexts) // 8, 2), 8)  # 2-8 nodes
    print(f"🔍 Estimated {estimated_line_total_count} LINE_TOTAL amounts from {len(currency_contexts)} currency contexts")
    print(f"📈 Creating graph with {estimated_line_total_count} parallel Phase 2 nodes")
    
    # Create dynamic graph based on estimated LINE_TOTAL count
    graph = create_n_parallel_graph(estimated_line_total_count)
    
    # Initial state
    initial_state = NParallelState(
        formatted_receipt_text=formatted_receipt_text,
        currency_contexts=currency_contexts,
        lines=lines,
        receipt_id=receipt_identifier,
        currency_labels=None,
        line_total_labels=None,
        line_item_results=[],  # Initialize as empty list for Annotated updates
        all_labels=None,
        confidence_score=None,
        processing_time=None
    )
    
    # Execute the N-parallel workflow
    final_state = await graph.ainvoke(initial_state)
    
    processing_time = time.time() - start_time
    final_state["processing_time"] = processing_time
    
    print(f"\n⚡ N-PARALLEL EXECUTION TIME: {processing_time:.2f}s")
    
    # Validate results
    discovered_labels = final_state["all_labels"]
    validation_total = validate_arithmetic_relationships(discovered_labels, known_total=0.0)
    
    # Update labels if requested (same logic as two_phase_analyzer.py)
    label_update_results = []
    if update_labels and discovered_labels:
        from receipt_label.label_updater import ReceiptLabelUpdater, display_label_update_results
        
        # Get merchant name for proper label attribution
        merchant_name = _get_merchant_name_for_receipt(client, image_id, receipt_id)
        
        label_updater = ReceiptLabelUpdater(client, merchant_name=merchant_name)
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
    
    # Return results
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
    print("🚀 N-Parallel Two-Phase Analyzer with Dynamic LangGraph")
    print("Phase 1: 120B model (currency analysis)")
    print("Phase 2: N parallel 20B models (one per LINE_TOTAL)")