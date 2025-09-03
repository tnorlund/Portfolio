#!/usr/bin/env python3
"""
Simple Receipt Analyzer with LangGraph + LangSmith
=================================================

Simplified version of n_parallel_analyzer.py that:
- Uses LangGraph for workflow orchestration
- Maintains LangSmith tracing integration
- Eliminates complex dynamic node creation
- Uses fixed graph: START → Phase1 → Phase2 → END
"""

import os
import time
from typing import List, TypedDict, Annotated, Sequence
from langgraph.graph import StateGraph, START, END
from langgraph.types import Send
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import PromptTemplate
from langchain_ollama import ChatOllama
from langgraph.graph.state import CompiledStateGraph
import operator


# Core dependencies (same as current system)
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_label.langchain.models import (
    CurrencyLabel,
    LabelType,
    ReceiptAnalysis,
    SimpleReceiptResponse,
)
from receipt_dynamo.entities import ReceiptLine, ReceiptMetadata
from receipt_label.utils.text_reconstruction import ReceiptTextReconstructor
from receipt_label.constants import CORE_LABELS


class CurrencyAnalysisState(TypedDict):
    """State for the currency validation workflow with Send API support."""

    # Input
    receipt_id: str
    image_id: str
    lines: List[ReceiptLine]
    formatted_text: str
    ollama_api_key: str

    # Phase 1 Results
    currency_labels: List[CurrencyLabel]

    # Phase 2 Results - Uses reducer to combine parallel results
    line_item_labels: Annotated[List[CurrencyLabel], operator.add]

    # Final Results
    discovered_labels: List[CurrencyLabel]
    confidence_score: float
    processing_time: float


async def load_receipt_data(
    state: CurrencyAnalysisState,
) -> CurrencyAnalysisState:
    """Loads and formats receipt data."""

    print(f"📋 Loading receipt data for {state['receipt_id']}")

    formatted_text, _ = ReceiptTextReconstructor().reconstruct_receipt(
        state["lines"]
    )

    return {**state, "formatted_text": formatted_text}


async def phase1_currency_analysis(state: CurrencyAnalysisState):
    """Node 1: Analyze currency amounts (GRAND_TOTAL, TAX, etc.)."""

    # Create LLM client locally to avoid state conflicts
    llm = ChatOllama(
        model="gpt-oss:120b",
        base_url="https://ollama.com",
        client_kwargs={
            "headers": {"Authorization": f"Bearer {state['ollama_api_key']}"}
        },
    )

    subset = ["GRAND_TOTAL", "TAX", "SUBTOTAL", "LINE_TOTAL"]
    subset_definitions = "\n".join(
        f"- {l}: {CORE_LABELS[l]}" for l in subset if l in CORE_LABELS
    )

    template = """You are analyzing a receipt to classify currency amounts.

RECEIPT TEXT:
{receipt_text}

Find all currency amounts and classify them as:
{subset_definitions}

Focus on the most obvious currency amounts first.

{format_instructions}"""
    # Create parser and chain
    output_parser = PydanticOutputParser(pydantic_object=SimpleReceiptResponse)
    prompt = PromptTemplate(
        template=template,
        input_variables=["receipt_text", "subset_definitions"],
        partial_variables={
            "format_instructions": output_parser.get_format_instructions()
        },
    )

    chain = prompt | llm | output_parser
    try:
        response = await chain.ainvoke(
            {
                "receipt_text": state["formatted_text"],
                "subset_definitions": subset_definitions,
            },
            config={
                "metadata": {
                    "receipt_id": state["receipt_id"],
                    "phase": "currency_analysis",
                    "model": "120b",
                },
                "tags": ["phase1", "currency", "receipt-analysis"],
            },
        )

        # Convert to CurrencyLabel objects directly
        currency_labels = [
            CurrencyLabel(
                word_text=item.word_text,
                label_type=getattr(LabelType, item.label_type),
                line_ids=item.line_ids,
                confidence=item.confidence,
                reasoning=item.reasoning,
            )
            for item in response.currency_amounts
        ]
        return {"currency_labels": currency_labels}
    except Exception as e:
        print(f"Phase 1 failed: {e}")
        return {"currency_labels": []}


def dispatch_to_parallel_phase2(
    state: CurrencyAnalysisState,
) -> Sequence[Send]:
    """Dispatcher that creates Send commands for parallel Phase 2 nodes."""

    print(f"🔄 Dispatching parallel Phase 2 analysis")

    # Find LINE_TOTALs from Phase 1
    line_totals = [
        label
        for label in state["currency_labels"]
        if label.label_type == LabelType.LINE_TOTAL
    ]

    if not line_totals:
        print("   No LINE_TOTAL amounts found - skipping Phase 2")
        # Return empty list - will go directly to combine_results
        return []

    print(f"   Creating {len(line_totals)} parallel Phase 2 tasks")

    # Create Send command for each LINE_TOTAL
    sends = []
    for i, line_total in enumerate(line_totals):
        # Each Send creates a separate instance with its own context
        send_data = {
            "line_total_index": i,
            "target_line_total": line_total,
            "receipt_text": state["formatted_text"],
            "receipt_id": state["receipt_id"],
            "ollama_api_key": state["ollama_api_key"],
        }
        sends.append(Send("phase2_line_analysis", send_data))

    return sends


async def phase2_line_analysis(send_data: dict) -> dict:
    """Phase 2 node: Analyze individual line items (PRODUCT_NAME, QUANTITY, etc.)."""

    index = send_data["line_total_index"]
    line_total = send_data["target_line_total"]
    receipt_text = send_data["receipt_text"]
    receipt_id = send_data["receipt_id"]
    ollama_api_key = send_data["ollama_api_key"]

    print(f"   🤖 Phase 2.{index}: Analyzing line with {line_total.word_text}")

    # Create LLM client for line analysis
    llm = ChatOllama(
        model="gpt-oss:20b",
        base_url="https://ollama.com",
        client_kwargs={
            "headers": {"Authorization": f"Bearer {ollama_api_key}"}
        },
    )

    subset = ["PRODUCT_NAME", "QUANTITY", "UNIT_PRICE"]
    subset_definitions = "\n".join(
        f"- {l}: {CORE_LABELS[l]}" for l in subset if l in CORE_LABELS
    )

    template = """You are analyzing a specific line item from a receipt to identify its components.

TARGET LINE ITEM: {target_line_text}
TARGET AMOUNT: {target_amount}

FULL RECEIPT CONTEXT:
{receipt_text}

Focus ONLY on the target line item "{target_line_text}" that contains the amount {target_amount}.

For this specific line item, identify:
{subset_definitions}

IMPORTANT: Only label words that appear in the target line "{target_line_text}". 
Do not label words from other lines in the receipt.

{format_instructions}"""

    output_parser = PydanticOutputParser(pydantic_object=SimpleReceiptResponse)
    prompt = PromptTemplate(
        template=template,
        input_variables=[
            "target_line_text",
            "target_amount",
            "receipt_text",
            "subset_definitions",
        ],
        partial_variables={
            "format_instructions": output_parser.get_format_instructions()
        },
    )

    chain = prompt | llm | output_parser
    try:
        response = await chain.ainvoke(
            {
                "target_line_text": line_total.word_text,
                "target_amount": line_total.word_text,
                "receipt_text": receipt_text,
                "subset_definitions": subset_definitions,
            },
            config={
                "metadata": {
                    "receipt_id": receipt_id,
                    "phase": "lineitem_analysis",
                    "model": "20b",
                    "line_index": index,
                },
                "tags": ["phase2", "line-items", "receipt-analysis"],
            },
        )

        # Convert to CurrencyLabel objects
        line_item_labels = []
        for item in response.currency_amounts:
            try:
                label_type = getattr(LabelType, item.label_type)
                label = CurrencyLabel(
                    word_text=item.word_text,
                    label_type=label_type,
                    line_ids=item.line_ids,
                    confidence=item.confidence,
                    reasoning=item.reasoning,
                )
                line_item_labels.append(label)
            except AttributeError:
                print(
                    f"⚠️ Warning: Unknown label type '{item.label_type}' for '{item.word_text}', skipping"
                )
                continue

        print(
            f"   ✅ Phase 2.{index}: Found {len(line_item_labels)} labels for {line_total.word_text}"
        )

        # Return line_item_labels which will be added to the state via the reducer
        return {"line_item_labels": line_item_labels}

    except Exception as e:
        print(f"Phase 2.{index} failed: {e}")
        return {"line_item_labels": []}


def create_unified_analysis_graph() -> CompiledStateGraph:
    """Create single unified graph: load → phase1 → dispatch → parallel phase2 → combine → end"""

    workflow = StateGraph(CurrencyAnalysisState)

    # Add all nodes
    workflow.add_node("load_data", load_receipt_data)
    workflow.add_node("phase1_currency", phase1_currency_analysis)
    workflow.add_node("phase2_line_analysis", phase2_line_analysis)
    workflow.add_node("combine_results", combine_results)

    # Define the flow using Send API for dynamic dispatch
    workflow.add_edge(START, "load_data")
    workflow.add_edge("load_data", "phase1_currency")

    # Use conditional edge to dispatch parallel Phase 2 nodes
    workflow.add_conditional_edges(
        "phase1_currency",
        dispatch_to_parallel_phase2,
        ["phase2_line_analysis"],
    )

    # All Phase 2 instances automatically go to combine_results
    workflow.add_edge("phase2_line_analysis", "combine_results")
    workflow.add_edge("combine_results", END)

    return workflow.compile()


async def combine_results(
    state: CurrencyAnalysisState,
) -> CurrencyAnalysisState:
    """Final node: Combine all results and calculate final metrics."""

    print(f"🔄 Combining results")

    # Combine all discovered labels
    discovered_labels = []

    # Add currency labels from Phase 1
    currency_labels = state.get("currency_labels", [])
    discovered_labels.extend(currency_labels)

    # Add line item labels from Phase 2 (automatically combined by reducer)
    line_item_labels = state.get("line_item_labels", [])
    discovered_labels.extend(line_item_labels)

    # Calculate overall confidence
    if discovered_labels:
        confidence_score = sum(
            label.confidence for label in discovered_labels
        ) / len(discovered_labels)
    else:
        confidence_score = 0.0

    print(f"   ✅ Combined {len(discovered_labels)} total labels")
    print(f"   ✅ Phase 1: {len(currency_labels)} currency labels")
    print(f"   ✅ Phase 2: {len(line_item_labels)} line item labels")
    print(f"   ✅ Overall confidence: {confidence_score:.2f}")

    return {
        "discovered_labels": discovered_labels,
        "confidence_score": confidence_score,
    }


async def analyze_receipt_simple(
    client: DynamoClient,
    image_id: str,
    receipt_id: int,
    ollama_api_key: str,
) -> ReceiptAnalysis:
    """Analyze a receipt using the unified single-trace graph."""

    # Setup LangSmith tracing
    langchain_api_key = os.getenv("LANGCHAIN_API_KEY")
    if langchain_api_key and langchain_api_key.strip():
        os.environ["LANGCHAIN_TRACING_V2"] = "true"
        os.environ["LANGCHAIN_PROJECT"] = (
            f"receipt-analysis-unified-{image_id[:8]}"
        )
        print("✅ LangSmith tracing enabled")
        print(f"   Project: receipt-analysis-unified-{image_id[:8]}")
        print("   View at: https://smith.langchain.com/")
    else:
        print("⚠️ LANGCHAIN_API_KEY not set - tracing disabled")

    print(f"🚀 Analyzing receipt {image_id}/{receipt_id}")
    print("=" * 60)
    print(
        "UNIFIED WORKFLOW: load → phase1 → dispatch → parallel phase2 → combine → end"
    )
    print("✨ Single trace with dynamic parallel execution")
    print()

    start_time = time.time()
    lines = client.list_receipt_lines_from_receipt(image_id, receipt_id)

    # Initial state for unified graph
    initial_state: CurrencyAnalysisState = {
        "receipt_id": f"{image_id}/{receipt_id}",
        "image_id": image_id,
        "lines": lines,
        "formatted_text": "",
        "ollama_api_key": ollama_api_key,
        "currency_labels": [],
        "line_item_labels": [],
        "discovered_labels": [],
        "confidence_score": 0.0,
        "processing_time": 0.0,
    }

    # Create and run the unified graph - SINGLE TRACE!
    unified_graph = create_unified_analysis_graph()
    result = await unified_graph.ainvoke(
        initial_state,
        config={
            "metadata": {
                "receipt_id": f"{image_id}/{receipt_id}",
                "workflow": "unified_parallel",
            },
            "tags": ["unified", "parallel", "receipt-analysis"],
        },
    )

    processing_time = time.time() - start_time
    print(f"\n⚡ UNIFIED EXECUTION TIME: {processing_time:.2f}s")

    return ReceiptAnalysis(
        discovered_labels=result["discovered_labels"],
        confidence_score=result["confidence_score"],
        validation_total=0.0,  # You can add arithmetic validation
        processing_time=processing_time,
        receipt_id=f"{image_id}/{receipt_id}",
        known_total=0.0,
        validation_results={},
        total_lines=len(lines),
        formatted_text=(
            result["formatted_text"][:500] + "..."
            if len(result["formatted_text"]) > 500
            else result["formatted_text"]
        ),
    )
