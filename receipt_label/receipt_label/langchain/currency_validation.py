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
import json
import pickle
from pathlib import Path
from typing import List, TypedDict, Annotated, Sequence, Optional
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
    LineItemLabel,
    CurrencyLabelType,
    LineItemLabelType,
    ReceiptAnalysis,
    SimpleReceiptResponse,
    Phase1Response,
    Phase2Response,
)
from receipt_dynamo.entities import ReceiptLine, ReceiptMetadata
from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel
from datetime import datetime
from receipt_label.utils.text_reconstruction import ReceiptTextReconstructor
from receipt_label.constants import CORE_LABELS


def save_state_for_development(
    state: dict,
    stage: str = "combine_results",
    receipt_id: str = None,
    save_format: str = "json",  # "json", "pickle", "both"
    output_dir: str = "./dev_states",
) -> dict:
    """Save workflow state for development and debugging.

    Args:
        state: Current workflow state
        stage: Stage name (e.g., "combine_results", "phase1_complete")
        receipt_id: Receipt identifier for unique filenames
        save_format: "json" (readable), "pickle" (complete), or "both"
        output_dir: Directory to save state files

    Returns:
        dict: Metadata about saved files
    """

    # Create output directory
    output_path = Path(output_dir)
    output_path.mkdir(exist_ok=True)

    # Generate filename
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    receipt_part = f"_{receipt_id.replace('/', '_')}" if receipt_id else ""
    base_filename = f"state_{stage}{receipt_part}_{timestamp}"

    saved_files = {}

    # Create serializable state (remove non-serializable objects)
    serializable_state = prepare_state_for_serialization(state)

    try:
        # Save as JSON (human-readable)
        if save_format in ["json", "both"]:
            json_path = output_path / f"{base_filename}.json"
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(
                    serializable_state,
                    f,
                    indent=2,
                    default=str,
                    ensure_ascii=False,
                )
            saved_files["json"] = str(json_path)
            print(f"   💾 Saved JSON state: {json_path}")

        # Save as Pickle (complete Python objects)
        if save_format in ["pickle", "both"]:
            pickle_path = output_path / f"{base_filename}.pkl"
            with open(pickle_path, "wb") as f:
                pickle.dump(state, f)
            saved_files["pickle"] = str(pickle_path)
            print(f"   💾 Saved Pickle state: {pickle_path}")

        # Save summary metadata
        metadata_path = output_path / f"{base_filename}_metadata.json"
        metadata = {
            "stage": stage,
            "receipt_id": receipt_id,
            "timestamp": timestamp,
            "state_keys": list(state.keys()),
            "currency_labels_count": len(state.get("currency_labels", [])),
            "line_item_labels_count": len(state.get("line_item_labels", [])),
            "discovered_labels_count": len(state.get("discovered_labels", [])),
            "confidence_score": state.get("confidence_score", 0.0),
            "saved_files": saved_files,
        }

        with open(metadata_path, "w") as f:
            json.dump(metadata, f, indent=2)
        saved_files["metadata"] = str(metadata_path)

        print(f"   📊 State Summary:")
        print(f"       Currency Labels: {metadata['currency_labels_count']}")
        print(f"       Line Item Labels: {metadata['line_item_labels_count']}")
        print(
            f"       Total Discovered: {metadata['discovered_labels_count']}"
        )
        print(f"       Confidence: {metadata['confidence_score']:.2f}")

        return saved_files

    except Exception as e:
        print(f"   ⚠️ Failed to save state: {e}")
        return {"error": str(e)}


def prepare_state_for_serialization(state: dict) -> dict:
    """Prepare state for JSON serialization by handling non-serializable objects."""
    print(f"Preparing state for serialization.")

    serializable_state = {}

    for key, value in state.items():
        if key == "dynamo_client":
            # Don't serialize the DynamoDB client
            serializable_state[key] = "<DynamoClient object - not serialized>"

        elif key in [
            "currency_labels",
            "line_item_labels",
            "discovered_labels",
        ]:
            # Convert CurrencyLabel objects to dicts
            if isinstance(value, list):
                serializable_state[key] = [
                    {
                        "line_text": label.line_text,
                        "amount": label.amount,
                        "label_type": (
                            label.label_type.value
                            if hasattr(label.label_type, "value")
                            else str(label.label_type)
                        ),
                        "line_ids": label.line_ids,
                        "confidence": label.confidence,
                        "reasoning": label.reasoning,
                    }
                    for label in value
                ]
            else:
                serializable_state[key] = value

        elif key == "lines":
            # Convert ReceiptLine objects to basic info
            if isinstance(value, list):
                serializable_state[key] = [
                    {
                        "receipt_id": line.receipt_id,
                        "image_id": line.image_id,
                        "line_id": line.line_id,
                        "text": getattr(line, "text", "<no text>"),
                        "confidence": getattr(line, "confidence", 0.0),
                    }
                    for line in value
                ]
            else:
                serializable_state[key] = value

        else:
            # For other values, try to serialize directly
            try:
                json.dumps(value, default=str)  # Test serialization
                serializable_state[key] = value
            except (TypeError, ValueError):
                serializable_state[key] = str(value)

    return serializable_state


def load_saved_state(file_path: str) -> dict:
    """Load a previously saved state for development.

    Args:
        file_path: Path to saved state file (.json or .pkl)

    Returns:
        dict: Loaded state
    """

    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(f"State file not found: {file_path}")

    if path.suffix == ".json":
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    elif path.suffix == ".pkl":
        with open(path, "rb") as f:
            return pickle.load(f)

    else:
        raise ValueError(f"Unsupported file format: {path.suffix}")


class CurrencyAnalysisState(TypedDict):
    """State for the currency validation workflow with Send API support."""

    # Input
    receipt_id: str
    image_id: str
    lines: List[ReceiptLine]
    formatted_text: str
    dynamo_client: DynamoClient  # Added for word label creation

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


async def phase1_currency_analysis(
    state: CurrencyAnalysisState, ollama_api_key: str
):
    """Node 1: Analyze currency amounts (GRAND_TOTAL, TAX, etc.).

    Args:
        state: Current workflow state (API key NOT included)
        ollama_api_key: API key from closure scope (secure)
    """

    # Create LLM client with secure API key from closure
    llm = ChatOllama(
        model="gpt-oss:120b",
        base_url="https://ollama.com",
        client_kwargs={
            "headers": {"Authorization": f"Bearer {ollama_api_key}"}
        },
    )

    subset = [
        CurrencyLabelType.GRAND_TOTAL.value,
        CurrencyLabelType.TAX.value,
        CurrencyLabelType.SUBTOTAL.value,
        CurrencyLabelType.LINE_TOTAL.value,
    ]
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
    output_parser = PydanticOutputParser(pydantic_object=Phase1Response)
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
                line_text=item.line_text,
                amount=item.amount,
                label_type=getattr(CurrencyLabelType, item.label_type),
                line_ids=item.line_ids,
                confidence=item.confidence,
                reasoning=item.reasoning,
            )
            for item in response.currency_labels
        ]
        return {"currency_labels": currency_labels}
    except Exception as e:
        print(f"Phase 1 failed: {e}")
        return {"currency_labels": []}


def dispatch_to_parallel_phase2(
    state: CurrencyAnalysisState, ollama_api_key: str
) -> Sequence[Send]:
    """Dispatcher that creates Send commands for parallel Phase 2 nodes.

    Args:
        state: Current workflow state (API key NOT included)
        ollama_api_key: API key from closure scope (secure)
    """

    print(f"🔄 Dispatching parallel Phase 2 analysis")

    # Find LINE_TOTALs from Phase 1
    line_totals = [
        label
        for label in state["currency_labels"]
        if label.label_type == CurrencyLabelType.LINE_TOTAL
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
        # API key injected here from closure, not stored in state
        send_data = {
            "line_total_index": i,
            "target_line_total": line_total,
            "receipt_text": state["formatted_text"],
            "receipt_id": state["receipt_id"],
            "ollama_api_key": ollama_api_key,  # ✅ Secure injection from closure
            "currency_labels": state["currency_labels"],  # Phase 1 results
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
    currency_labels = send_data[
        "currency_labels"
    ]  # ✅ Now have Phase 1 results

    print(f"   🤖 Phase 2.{index}: Analyzing line with {line_total.line_text}")

    # Create LLM client for line analysis
    llm = ChatOllama(
        model="gpt-oss:20b",
        base_url="https://ollama.com",
        client_kwargs={
            "headers": {"Authorization": f"Bearer {ollama_api_key}"}
        },
    )

    subset = [
        LineItemLabelType.PRODUCT_NAME.value,
        LineItemLabelType.QUANTITY.value,
        LineItemLabelType.UNIT_PRICE.value,
    ]
    subset_definitions = "\n".join(
        f"- {l}: {CORE_LABELS[l]}" for l in subset if l in CORE_LABELS
    )

    template = """You are analyzing a specific line item from a receipt to identify its components.

TARGET LINE ITEM: {target_line_text}
TARGET AMOUNT: {target_amount}

CURRENCY CONTEXT FROM PHASE 1:
{currency_context}

FULL RECEIPT CONTEXT:
{receipt_text}

Focus ONLY on the target line item "{target_line_text}" that contains the amount {target_amount}.

For this specific line item, identify:
{subset_definitions}

IMPORTANT: 
1. Only label words that appear in the target line "{target_line_text}". 
2. Do not label words from other lines in the receipt.
3. Focus on identifying: PRODUCT_NAME, QUANTITY, UNIT_PRICE for line items
4. You may also identify currency amounts (LINE_TOTAL, etc.) if they appear in this line
5. Be precise - label each component separately

{format_instructions}"""

    output_parser = PydanticOutputParser(pydantic_object=Phase2Response)
    # Add currency context for the prompt
    currency_context = "\n".join(
        [
            f"- {label.label_type.value}: {label.amount}"
            for label in currency_labels
        ]
    )

    prompt = PromptTemplate(
        template=template,
        input_variables=[
            "target_line_text",
            "target_amount",
            "currency_context",
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
                "target_line_text": line_total.line_text,
                "target_amount": line_total.word_text,
                "currency_context": currency_context,
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

        # Define allowed label types for Phase 2
        # Note: Include currency types since line text contains amounts the LLM will identify
        allowed_label_types = {
            LineItemLabelType.PRODUCT_NAME.value,
            LineItemLabelType.QUANTITY.value,
            LineItemLabelType.UNIT_PRICE.value,
        }

        # Convert to CurrencyLabel objects with validation
        line_item_labels = []
        for item in response.line_item_labels:
            # ✅ Validate label type is in our allowed subset
            if item.label_type not in allowed_label_types:
                print(
                    f"⚠️ Skipping invalid label type '{item.label_type}' for '{item.line_text}' - not in Phase 2 subset"
                )
                continue

            try:
                label_type = getattr(LineItemLabelType, item.label_type)
                label = LineItemLabel(
                    line_text=item.line_text,
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


def create_unified_analysis_graph(
    ollama_api_key: str, save_dev_state: bool = False
) -> CompiledStateGraph:
    """Create single unified graph with secure API key injection via closures.

    Args:
        ollama_api_key: API key injected via closure - never stored in state
        save_dev_state: Whether to save state at combine_results for development
    """

    workflow = StateGraph(CurrencyAnalysisState)

    # Create nodes with API key injected via closures (secure)
    async def phase1_with_key(state):
        """Phase 1 node with API key from closure scope"""
        return await phase1_currency_analysis(state, ollama_api_key)

    def dispatch_with_key(state):
        """Dispatcher with API key injected into Send data"""
        return dispatch_to_parallel_phase2(state, ollama_api_key)

    async def phase2_with_key(send_data):
        """Phase 2 node that receives API key from dispatcher"""
        return await phase2_line_analysis(send_data)

    async def combine_with_dev_save(state):
        """Combine results with optional state saving for development"""
        return await combine_results(state, save_dev_state)

    # Add nodes with secure key injection
    workflow.add_node("load_data", load_receipt_data)
    workflow.add_node("phase1_currency", phase1_with_key)
    workflow.add_node("phase2_line_analysis", phase2_with_key)
    workflow.add_node("combine_results", combine_with_dev_save)

    # Define the flow using Send API for dynamic dispatch
    workflow.add_edge(START, "load_data")
    workflow.add_edge("load_data", "phase1_currency")

    # Use conditional edge to dispatch parallel Phase 2 nodes
    workflow.add_conditional_edges(
        "phase1_currency",
        dispatch_with_key,  # Dispatcher injects API key
        ["phase2_line_analysis"],
    )

    # All Phase 2 instances automatically go to combine_results
    workflow.add_edge("phase2_line_analysis", "combine_results")
    workflow.add_edge("combine_results", END)

    return workflow.compile()


def create_receipt_word_labels_from_currency_labels(
    discovered_labels: List[CurrencyLabel],
    lines: List[ReceiptLine],
    image_id: str,
    receipt_id: str,
    client: DynamoClient,
) -> List[ReceiptWordLabel]:
    """Convert CurrencyLabel objects to ReceiptWordLabel entities."""

    print(f"   🏷️ Creating ReceiptWordLabel entities...")

    # Parse receipt_id from the combined string
    actual_receipt_id = int(receipt_id.split("/")[-1])

    # Create word mapping: (line_id, word_text) -> List[word_id]
    word_mapping = {}

    # Get all words for each line to build the mapping
    for line in lines:
        try:
            # Get all ReceiptWords for this line
            words = client.list_receipt_words_from_line(
                actual_receipt_id, image_id, line.line_id
            )
            for word in words:
                # Handle multiple words with same text on same line
                key = (line.line_id, word.text.strip().upper())
                if key not in word_mapping:
                    word_mapping[key] = []
                word_mapping[key].append(word.word_id)

                # Also create partial matches for compound labels
                # e.g., "YOGURT 8.99" can match both "YOGURT" and "8.99"
                word_tokens = word.text.strip().split()
                for token in word_tokens:
                    token_key = (line.line_id, token.upper())
                    if token_key not in word_mapping:
                        word_mapping[token_key] = []
                    if word.word_id not in word_mapping[token_key]:
                        word_mapping[token_key].append(word.word_id)

        except Exception as e:
            print(
                f"   ⚠️ Warning: Could not load words for line {line.line_id}: {e}"
            )
            continue

    print(
        f"   📝 Built word mapping for {len(word_mapping)} unique (line_id, word_text) combinations"
    )

    receipt_word_labels = []
    current_time = datetime.now()

    for label in discovered_labels:
        if not label.line_ids:
            print(f"   ⚠️ Skipping label '{label.line_text}' - no line_ids")
            continue

        # For each line_id this label applies to
        for line_id in label.line_ids:
            # Try to find word_ids that match this label's text
            label_text = label.line_text.strip().upper()
            lookup_key = (line_id, label_text)

            word_ids = word_mapping.get(lookup_key, [])

            if not word_ids:
                # Try partial matching for compound words
                for (
                    mapped_line_id,
                    mapped_text,
                ), mapped_word_ids in word_mapping.items():
                    if mapped_line_id == line_id and label_text in mapped_text:
                        word_ids.extend(mapped_word_ids)
                        break

            if not word_ids:
                print(
                    f"   ⚠️ No words found for label '{label.line_text}' on line {line_id}"
                )
                continue

            # Create ReceiptWordLabel for each matching word
            for word_id in word_ids:
                receipt_word_label = ReceiptWordLabel(
                    image_id=image_id,
                    receipt_id=actual_receipt_id,
                    line_id=line_id,
                    word_id=word_id,
                    label=label.label_type.value,
                    reasoning=label.reasoning
                    or "Identified by simple_receipt_analyzer",
                    timestamp_added=current_time,
                    label_proposed_by="simple_receipt_analyzer",
                    validation_status="PENDING",
                )
                receipt_word_labels.append(receipt_word_label)

    print(
        f"   ✅ Created {len(receipt_word_labels)} ReceiptWordLabel entities"
    )
    return receipt_word_labels


async def save_receipt_word_labels(
    client: DynamoClient,
    receipt_word_labels: List[ReceiptWordLabel],
    dry_run: bool = False,
) -> dict:
    """Save ReceiptWordLabels to DynamoDB using proper DynamoClient methods."""

    if dry_run:
        print(
            f"   🔍 DRY RUN: Would save {len(receipt_word_labels)} ReceiptWordLabel entities"
        )
        return {"saved": 0, "skipped": len(receipt_word_labels)}

    if not receipt_word_labels:
        print(f"   📝 No ReceiptWordLabel entities to save")
        return {"saved": 0, "failed": 0}

    print(
        f"   💾 Saving {len(receipt_word_labels)} ReceiptWordLabel entities to DynamoDB..."
    )

    try:
        # Use the bulk method for better performance
        client.add_receipt_word_labels(receipt_word_labels)
        print(f"   ✅ Successfully saved {len(receipt_word_labels)} labels")
        return {"saved": len(receipt_word_labels), "failed": 0}

    except Exception as e:
        print(f"   ⚠️ Failed to save labels in bulk: {e}")
        print(f"   🔄 Attempting individual saves...")

        # Fallback to individual saves
        saved_count = 0
        failed_count = 0

        for label in receipt_word_labels:
            try:
                client.add_receipt_word_label(label)
                saved_count += 1

            except Exception as individual_error:
                print(
                    f"   ⚠️ Failed to save label {label.label} for word {label.word_id}: {individual_error}"
                )
                failed_count += 1

        print(
            f"   📊 Individual saves: {saved_count} success, {failed_count} failed"
        )
        return {"saved": saved_count, "failed": failed_count}


async def combine_results(
    state: CurrencyAnalysisState, save_dev_state: bool = False
) -> CurrencyAnalysisState:
    """Final node: Combine all results and calculate final metrics.

    Args:
        state: Current workflow state
        save_dev_state: Whether to save state for development (default: False)
    """

    print(f"🔄 Combining results")

    # 💾 Save state for development if requested
    if save_dev_state:
        print(f"   💾 Saving state for development...")
        save_state_for_development(
            state=dict(state),
            stage="combine_results_start",
            receipt_id=state.get("receipt_id"),
            save_format="both",  # Save both JSON and pickle
            output_dir="./dev_states",
        )

    # Combine all discovered labels
    discovered_labels = []

    # Add currency labels from Phase 1
    currency_labels = state.get("currency_labels", [])
    discovered_labels.extend(currency_labels)

    # Add line item labels from Phase 2 (automatically combined by reducer)
    line_item_labels = state.get("line_item_labels", [])

    # Filter out currency duplicates - prefer Phase 1 currency labels over Phase 2
    currency_types = {"GRAND_TOTAL", "TAX", "SUBTOTAL", "LINE_TOTAL"}
    phase1_currency_texts = {
        label.line_text
        for label in currency_labels
        if label.label_type.value in currency_types
    }

    # Only add Phase 2 labels that aren't currency duplicates
    filtered_line_labels = [
        label
        for label in line_item_labels
        if not (
            label.label_type.value in currency_types
            and label.line_text in phase1_currency_texts
        )
    ]

    discovered_labels.extend(filtered_line_labels)

    print(
        f"   🔍 Filtered out {len(line_item_labels) - len(filtered_line_labels)} duplicate currency labels"
    )

    # Create ReceiptWordLabel entities from discovered labels
    receipt_word_labels = create_receipt_word_labels_from_currency_labels(
        discovered_labels=discovered_labels,
        lines=state.get("lines", []),
        image_id=state.get("image_id", ""),
        receipt_id=state.get("receipt_id", ""),
        client=state.get("dynamo_client"),
    )

    # Calculate overall confidence
    if discovered_labels:
        confidence_score = sum(
            label.confidence for label in discovered_labels
        ) / len(discovered_labels)
    else:
        confidence_score = 0.0

    print(f"   ✅ Combined {len(discovered_labels)} total labels")
    print(f"   ✅ Phase 1: {len(currency_labels)} currency labels")
    print(
        f"   ✅ Phase 2: {len(filtered_line_labels)} line item labels (after dedup)"
    )
    print(f"   ✅ Overall confidence: {confidence_score:.2f}")

    return {
        "discovered_labels": discovered_labels,
        "receipt_word_labels": receipt_word_labels,
        "confidence_score": confidence_score,
    }


async def analyze_receipt_simple(
    client: DynamoClient,
    image_id: str,
    receipt_id: int,
    ollama_api_key: str,
    langsmith_api_key: Optional[str] = None,
    save_labels: bool = False,
    dry_run: bool = False,
    save_dev_state: bool = False,
) -> ReceiptAnalysis:
    """Analyze a receipt using the unified single-trace graph with secure API key handling.

    Args:
        client: DynamoDB client for data access
        image_id: Receipt image identifier
        receipt_id: Receipt identifier
        ollama_api_key: Ollama API key (injected via closures, never stored in state)
        langsmith_api_key: LangSmith API key for tracing (optional, secure handling)
        save_labels: Whether to save ReceiptWordLabels to DynamoDB
        dry_run: Preview mode - don't actually save to DynamoDB
        save_dev_state: Save workflow state at combine_results for development
    """

    # Setup LangSmith tracing with secure API key handling
    if langsmith_api_key and langsmith_api_key.strip():
        # Temporarily set environment variable for this execution
        os.environ["LANGCHAIN_API_KEY"] = langsmith_api_key
        os.environ["LANGCHAIN_TRACING_V2"] = "true"
        os.environ["LANGCHAIN_PROJECT"] = (
            f"receipt-analysis-unified-{image_id[:8]}"
        )
        print(f"✅ LangSmith tracing enabled with provided API key")
        print(f"   Project: receipt-analysis-unified-{image_id[:8]}")
        print(f"   View at: https://smith.langchain.com/")
    else:
        print("⚠️ LangSmith API key not provided - tracing disabled")
        # Ensure tracing is disabled
        os.environ.pop("LANGCHAIN_API_KEY", None)
        os.environ.pop("LANGCHAIN_TRACING_V2", None)

    print(f"🚀 Analyzing receipt {image_id}/{receipt_id}")
    print("=" * 60)
    print(
        "UNIFIED WORKFLOW: load → phase1 → dispatch → parallel phase2 → combine → end"
    )
    print("✨ Single trace with dynamic parallel execution")
    print()

    start_time = time.time()
    lines = client.list_receipt_lines_from_receipt(image_id, receipt_id)

    # Initial state for unified graph (API key NOT included - secure!)
    initial_state: CurrencyAnalysisState = {
        "receipt_id": f"{image_id}/{receipt_id}",
        "image_id": image_id,
        "lines": lines,
        "formatted_text": "",
        "dynamo_client": client,  # Pass client for word label creation
        "currency_labels": [],
        "line_item_labels": [],
        "discovered_labels": [],
        "confidence_score": 0.0,
        "processing_time": 0.0,
    }

    # Create and run the unified graph with secure API key injection
    unified_graph = create_unified_analysis_graph(
        ollama_api_key, save_dev_state
    )  # ✅ Secure closure injection
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

    # Optionally save ReceiptWordLabels to DynamoDB
    if save_labels and "receipt_word_labels" in result:
        print(f"\n💾 SAVING RECEIPT WORD LABELS")
        save_results = await save_receipt_word_labels(
            client=client,
            receipt_word_labels=result["receipt_word_labels"],
            dry_run=dry_run,
        )
        print(f"   📊 Save Results: {save_results}")

    # Cleanup: Remove API keys from environment for security
    try:
        if langsmith_api_key:
            os.environ.pop("LANGCHAIN_API_KEY", None)
            os.environ.pop("LANGCHAIN_TRACING_V2", None)
            print(f"🧹 Cleaned up LangSmith API key from environment")
    except Exception:
        pass  # Don't fail if cleanup fails

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
