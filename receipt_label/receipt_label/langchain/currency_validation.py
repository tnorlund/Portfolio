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

import time
from typing import List, TypedDict
from langgraph.graph import StateGraph, END
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import PromptTemplate
from langchain_ollama import ChatOllama
from langgraph.graph.state import CompiledStateGraph


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
    """State for the currency validation workflow."""

    llm_120b: ChatOllama
    llm_20b: ChatOllama

    # Input
    receipt_id: str
    image_id: str
    # receipt_metadata: ReceiptMetadata
    lines: List[ReceiptLine]
    formatted_text: str

    # Phase 1 Results
    currency_labels: List[CurrencyLabel]

    # Phase 2 Results
    line_item_labels: List[CurrencyLabel]

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
    llm = state["llm_120b"]

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
        return {**state, "currency_labels": currency_labels}
    except Exception as e:
        print(f"Phase 1 failed: {e}")
        return {**state, "currency_labels": []}


def create_phase1_only_graph() -> CompiledStateGraph:
    """Create graph for Phase 1 only: load → currency analysis → end"""

    workflow = StateGraph(CurrencyAnalysisState)

    # Phase 1 workflow
    workflow.add_node("load_data", load_receipt_data)
    workflow.add_node("phase1_currency", phase1_currency_analysis)

    # Linear flow: load → phase1 → end
    workflow.set_entry_point("load_data")
    workflow.add_edge("load_data", "phase1_currency")
    workflow.add_edge("phase1_currency", END)

    return workflow.compile()


def create_dynamic_analysis_graph(
    currency_labels: List[CurrencyLabel],
) -> CompiledStateGraph:
    """Create graph with dynamic Phase 2 nodes based on LINE_TOTALs found."""

    workflow = StateGraph(CurrencyAnalysisState)

    # Phase 1 is already done - currency_labels contains the results
    # We're building Phase 2 graph dynamically based on Phase 1 results

    # Find LINE_TOTALs from Phase 1
    line_totals = [
        label
        for label in currency_labels
        if label.label_type == LabelType.LINE_TOTAL
    ]

    if not line_totals:
        # No line items to analyze - create simple passthrough
        workflow.add_node(
            "no_line_items", lambda state: {**state, "line_item_labels": []}
        )
        workflow.set_entry_point("no_line_items")
        workflow.add_edge("no_line_items", END)
        return workflow.compile()

    # Create dispatcher node for parallel execution
    workflow.add_node("dispatch", lambda state: state)  # Simple passthrough
    workflow.set_entry_point("dispatch")

    # Create dynamic Phase 2 nodes - one per LINE_TOTAL
    phase2_nodes = []
    for i, line_total in enumerate(line_totals):
        node_name = f"phase2_line_{i}"

        # Create specialized analyzer for this specific line total
        analyzer_func = create_line_item_analyzer(line_total, i)
        workflow.add_node(node_name, analyzer_func)
        phase2_nodes.append(node_name)

        # Connect dispatcher to this phase2 node for parallel execution
        workflow.add_edge("dispatch", node_name)

    # Combine results node
    workflow.add_node("combine_results", combine_results)

    # All phase2 nodes connect to combine_results
    for node_name in phase2_nodes:
        workflow.add_edge(node_name, "combine_results")

    # End after combining
    workflow.add_edge("combine_results", END)

    return workflow.compile()


def create_line_item_analyzer(line_total: CurrencyLabel, index: int):
    """Create a specialized analyzer function for a specific LINE_TOTAL."""

    async def phase2_lineitem_analysis(
        state: CurrencyAnalysisState,
    ) -> CurrencyAnalysisState:
        """Node 2: Analyze line items (PRODUCT_NAME, QUANTITY, etc.)."""
        llm = state["llm_20b"]

        subset = ["PRODUCT_NAME", "QUANTITY", "UNIT_PRICE"]
        subset_definitions = "\n".join(
            f"- {l}: {CORE_LABELS[l]}" for l in subset if l in CORE_LABELS
        )

        template = """You are analyzing receipt line items to identify products and quantities.

    RECEIPT TEXT:
    {receipt_text}

    CURRENCY AMOUNTS ALREADY FOUND:
    {currency_context}

    Find product information:
    {subset_definitions}

    Focus on lines that look like individual product purchases.

    {format_instructions}"""

        currency_context = "\n".join(
            [
                f"- {label.label_type.value}: {label.word_text}"
                for label in state["currency_labels"]
            ]
        )
        output_parser = PydanticOutputParser(
            pydantic_object=SimpleReceiptResponse
        )
        prompt = PromptTemplate(
            template=template,
            input_variables=[
                "receipt_text",
                "currency_context",
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
                    "receipt_text": state["formatted_text"],
                    "currency_context": currency_context,
                    "subset_definitions": subset_definitions,
                },
                config={
                    "metadata": {
                        "receipt_id": state["receipt_id"],
                        "phase": "lineitem_analysis",
                        "model": "20b",
                    },
                    "tags": ["phase2", "line-items", "receipt-analysis"],
                },
            )
            # Convert to CurrencyLabel objects using currency_amounts (not line_items)
            line_item_labels = []
            for (
                item
            ) in (
                response.currency_amounts
            ):  # ✅ Changed from line_items to currency_amounts
                label = CurrencyLabel(
                    word_text=item.word_text,
                    label_type=getattr(LabelType, item.label_type),
                    line_ids=item.line_ids,
                    confidence=item.confidence,
                    reasoning=item.reasoning,
                )
                line_item_labels.append(label)
            return {**state, "line_item_labels": line_item_labels}

        except Exception as e:
            print(f"Phase 2 failed: {e}")
            return {**state, "line_item_labels": []}

    return phase2_lineitem_analysis


async def combine_results(
    state: CurrencyAnalysisState,
) -> CurrencyAnalysisState:
    """Node 4: Combine all results and calculate final metrics."""

    print(f"🔄 Combining results")

    # Combine all discovered labels
    discovered_labels = []
    discovered_labels.extend(state.get("currency_labels", []))
    discovered_labels.extend(state.get("line_item_labels", []))

    # Calculate overall confidence
    if discovered_labels:
        confidence_score = sum(
            label.confidence for label in discovered_labels
        ) / len(discovered_labels)
    else:
        confidence_score = 0.0

    print(f"   ✅ Combined {len(discovered_labels)} total labels")
    print(f"   ✅ Overall confidence: {confidence_score:.2f}")

    return {
        **state,
        "discovered_labels": discovered_labels,
        "confidence_score": confidence_score,
    }


async def analyze_receipt_simple(
    client: DynamoClient,
    image_id: str,
    receipt_id: int,
    ollama_api_key: str,
) -> ReceiptAnalysis:
    """Analyze a receipt using the currency analysis graph."""

    print(f"🚀 Analyzing receipt {image_id}/{receipt_id}")
    print("=" * 60)
    print("Fixed workflow: Load → Phase1(120B) → Phase2(20B) → Combine")
    print()

    start_time = time.time()
    lines = client.list_receipt_lines_from_receipt(image_id, receipt_id)

    llm_120b = ChatOllama(
        model="gpt-oss:120b",
        base_url="https://ollama.com",
        client_kwargs={
            "headers": {"Authorization": f"Bearer {ollama_api_key}"}
        },
    )
    llm_20b = ChatOllama(
        model="gpt-oss:20b",
        base_url="https://ollama.com",
        client_kwargs={
            "headers": {"Authorization": f"Bearer {ollama_api_key}"}
        },
    )

    # Simple test message
    # from langchain_core.messages import HumanMessage

    # response = llm_20b.invoke([HumanMessage(content="Hello")])
    # print(f"✅ Authentication working: {response.content[:50]}...")

    initial_state: CurrencyAnalysisState = {
        "receipt_id": f"{image_id}/{receipt_id}",
        "image_id": image_id,
        "lines": lines,
        "formatted_text": "",
        "currency_labels": [],
        "line_item_labels": [],
        "discovered_labels": [],
        "confidence_score": 0.0,
        "processing_time": 0.0,
        "llm_120b": llm_120b,
        "llm_20b": llm_20b,
    }
    print("\n🔵 PHASE 1: Currency Analysis")
    print("-" * 30)

    # STEP 1: Run Phase 1 only graph (load + currency analysis)
    phase1_graph = create_phase1_only_graph()
    phase1_result = await phase1_graph.ainvoke(
        initial_state,
        config={
            "metadata": {
                "receipt_id": f"{image_id}/{receipt_id}",
                "phase": "phase1",
            },
            "tags": ["phase1", "currency-analysis"],
        },
    )

    print(
        f"Phase 1 found {len(phase1_result['currency_labels'])} currency labels"
    )

    # STEP 2: Build dynamic Phase 2 graph based on Phase 1 results
    print("\n🟢 PHASE 2: Dynamic Line Item Analysis")
    print("-" * 40)

    if phase1_result["currency_labels"]:
        line_totals = [
            label
            for label in phase1_result["currency_labels"]
            if label.label_type == LabelType.LINE_TOTAL
        ]
        print(f"Building dynamic graph for {len(line_totals)} LINE_TOTALs")

        dynamic_phase2_graph = create_dynamic_analysis_graph(
            phase1_result["currency_labels"]
        )

        # STEP 3: Run dynamic Phase 2 graph with Phase 1 results as input
        result = await dynamic_phase2_graph.ainvoke(
            phase1_result,
            config={
                "metadata": {
                    "receipt_id": f"{image_id}/{receipt_id}",
                    "phase": "phase2",
                },
                "tags": ["phase2", "dynamic-analysis"],
            },
        )
    else:
        print("No currency labels found - skipping Phase 2")
        # No currency labels, just add empty line_item_labels and combine
        result = await combine_results(
            {**phase1_result, "line_item_labels": []}
        )

    processing_time = time.time() - start_time

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
