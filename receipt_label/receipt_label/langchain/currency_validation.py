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
from receipt_dynamo.entities.receipt_word import ReceiptWord
from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel
from datetime import datetime
from receipt_label.utils.text_reconstruction import ReceiptTextReconstructor
from receipt_label.constants import CORE_LABELS


def save_state_for_development(
    state: dict,
    stage: str = "combine_results",
    receipt_id: str = None,
    save_format: str = "json",  # "json", "pickle", "both"
    output_dir: str = "./dev.states",
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
            # Convert label objects (CurrencyLabel or LineItemLabel) to dicts
            if isinstance(value, list):
                serialized_list = []
                for label in value:
                    item = {
                        "line_text": getattr(label, "line_text", None),
                        "label_type": (
                            label.label_type.value
                            if hasattr(label.label_type, "value")
                            else str(getattr(label, "label_type", ""))
                        ),
                        "line_ids": getattr(label, "line_ids", []),
                        "confidence": getattr(label, "confidence", 0.0),
                        "reasoning": getattr(label, "reasoning", None),
                    }
                    # CurrencyLabel has amount
                    if hasattr(label, "amount"):
                        item["amount"] = getattr(label, "amount")
                    # LineItemLabel has word_text
                    if hasattr(label, "word_text"):
                        item["word_text"] = getattr(label, "word_text")
                    serialized_list.append(item)
                serializable_state[key] = serialized_list
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

        elif key == "words":
            # Convert ReceiptWord objects to basic info
            if isinstance(value, list):
                serializable_state[key] = [
                    {
                        "receipt_id": word.receipt_id,
                        "image_id": word.image_id,
                        "line_id": word.line_id,
                        "word_id": word.word_id,
                        "text": getattr(word, "text", "<no text>"),
                        "confidence": getattr(word, "confidence", 0.0),
                    }
                    for word in value
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
    words: List[ReceiptWord]
    formatted_text: str
    dynamo_client: DynamoClient  # Added for word label creation
    existing_word_labels: List[ReceiptWordLabel]

    # Phase 1 Results
    currency_labels: List[CurrencyLabel]

    # Phase 2 Results - Uses reducer to combine parallel results
    line_item_labels: Annotated[List[LineItemLabel], operator.add]

    # Final Results
    discovered_labels: List[CurrencyLabel]
    confidence_score: float
    processing_time: float


async def load_receipt_data(
    state: CurrencyAnalysisState,
) -> CurrencyAnalysisState:
    """Loads and formats receipt data."""

    print(f"📋 Loading receipt data for {state['receipt_id']}")

    # Load full receipt details (lines + words) for reliable downstream mapping
    image_id, receipt_id_str = state["receipt_id"].split("/")
    receipt_id_int = int(receipt_id_str)
    client: DynamoClient = state["dynamo_client"]
    details = client.get_receipt_details(image_id, receipt_id_int)

    # Reconstruct formatted text from loaded lines
    formatted_text, _ = ReceiptTextReconstructor().reconstruct_receipt(
        details.lines
    )

    return {
        **state,
        "lines": details.lines,
        "words": details.words,
        "existing_word_labels": details.labels,
        "formatted_text": formatted_text,
    }


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

    # Build quick lookup for words by line_id to compile target line text
    words: List[ReceiptWord] = state.get("words", []) or []
    words_by_line: dict[int, List[str]] = {}
    try:
        for w in words:
            words_by_line.setdefault(w.line_id, []).append(
                getattr(w, "text", "")
            )
        # Sort tokens per line by their order if possible (word_id), otherwise keep insertion order
        # For simplicity, we joined in insertion order from the preloaded list
    except Exception:
        pass

    # Create Send command for each LINE_TOTAL
    sends = []
    for i, line_total in enumerate(line_totals):
        # Each Send creates a separate instance with its own context
        # API key injected here from closure, not stored in state
        # Compile target line text from the authoritative Phase 1 line_ids
        target_lines_text_parts: List[str] = []
        try:
            for lid in getattr(line_total, "line_ids", []) or []:
                tokens = words_by_line.get(lid, [])
                if tokens:
                    target_lines_text_parts.append(" ".join(tokens))
        except Exception:
            pass
        compiled_text = " ".join([t for t in target_lines_text_parts if t])

        send_data = {
            "line_total_index": i,
            "target_line_total": line_total,
            "target_line_text_compiled": compiled_text,
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

    template = """You are analyzing a receipt snippet to identify line item components.

TARGET SNIPPET (COMPILED FROM OCR WORDS):
{target_line_text}
TARGET AMOUNT: {target_amount}

CURRENCY CONTEXT FROM PHASE 1:
{currency_context}

FULL RECEIPT CONTEXT:
{receipt_text}

Identify only the components on the target snippet:
{subset_definitions}

IMPORTANT:
1. Output only labels for words that could plausibly co-occur with the amount.
2. Do not include line positions or IDs; only the word text and label type.
3. Focus on PRODUCT_NAME, QUANTITY, UNIT_PRICE.

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
                "target_line_text": send_data.get(
                    "target_line_text_compiled", ""
                ),
                "target_amount": str(getattr(line_total, "amount", "")),
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

        # Convert LLM output to internal LineItemLabel objects (no line_ids at this stage)
        line_item_labels = []
        for item in response.line_item_labels:
            # ✅ Validate label type is in our allowed subset
            lt_value = (
                item.label_type.value
                if hasattr(item.label_type, "value")
                else item.label_type
            )
            if lt_value not in allowed_label_types:
                print(
                    f"⚠️ Skipping invalid label type '{lt_value}' for '{getattr(item, 'word_text', '')}' - not in Phase 2 subset"
                )
                continue

            try:
                label_type = getattr(LineItemLabelType, lt_value)
                label = LineItemLabel(
                    word_text=item.word_text,
                    label_type=label_type,
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
            f"   ✅ Phase 2.{index}: Found {len(line_item_labels)} labels for amount {getattr(line_total, 'amount', '')}"
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
    words: List[ReceiptWord] | None,
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
    # Global mapping for line-item labels without line constraints: word_text -> List[(line_id, word_id)]
    global_word_index: dict[str, List[tuple[int, int]]] = {}
    # For debug: track per-line word texts and word id→text
    line_to_word_texts = {}
    word_text_map: dict[tuple, str] = {}

    # Build mapping from preloaded words if available, else fallback to client per-line
    if words is None:
        # Get all words for each line to build the mapping
        for line in lines:
            try:
                _words = client.list_receipt_words_from_line(
                    image_id=line.image_id,
                    receipt_id=line.receipt_id,
                    line_id=line.line_id,
                )
                print(
                    f"   🔎 Loaded {len(_words)} words for line {line.line_id}"
                )
                for word in _words:
                    key = (line.line_id, word.text.strip().upper())
                    if key not in word_mapping:
                        word_mapping[key] = []
                    word_mapping[key].append(word.word_id)
                    line_to_word_texts.setdefault(line.line_id, set()).add(
                        word.text.strip().upper()
                    )
                    word_text_map[(line.line_id, word.word_id)] = word.text
                    for token in word.text.strip().split():
                        token_key = (line.line_id, token.upper())
                        if token_key not in word_mapping:
                            word_mapping[token_key] = []
                        if word.word_id not in word_mapping[token_key]:
                            word_mapping[token_key].append(word.word_id)
                    # Populate global index
                    t = word.text.strip().upper()
                    if t:
                        global_word_index.setdefault(t, []).append(
                            (line.line_id, word.word_id)
                        )
                        for tok in t.split():
                            global_word_index.setdefault(tok, []).append(
                                (line.line_id, word.word_id)
                            )
            except Exception as e:
                print(
                    f"   ⚠️ Warning: Could not load words for line {line.line_id}: {e}"
                )
                continue
    else:
        # Preloaded words: one pass
        print(f"   🔎 Using preloaded words: {len(words)} total")
        for word in words:
            key = (word.line_id, word.text.strip().upper())
            if key not in word_mapping:
                word_mapping[key] = []
            word_mapping[key].append(word.word_id)
            line_to_word_texts.setdefault(word.line_id, set()).add(
                word.text.strip().upper()
            )
            word_text_map[(word.line_id, word.word_id)] = word.text
            for token in word.text.strip().split():
                token_key = (word.line_id, token.upper())
                if token_key not in word_mapping:
                    word_mapping[token_key] = []
                if word.word_id not in word_mapping[token_key]:
                    word_mapping[token_key].append(word.word_id)
            # Populate global index
            t = word.text.strip().upper()
            if t:
                global_word_index.setdefault(t, []).append(
                    (word.line_id, word.word_id)
                )
                for tok in t.split():
                    global_word_index.setdefault(tok, []).append(
                        (word.line_id, word.word_id)
                    )

    print(
        f"   📝 Built word mapping for {len(word_mapping)} unique (line_id, word_text) combinations"
    )

    receipt_word_labels = []
    current_time = datetime.now()

    for label in discovered_labels:
        # Decide mapping strategy by label type
        label_type_str = (
            label.label_type.value
            if hasattr(label.label_type, "value")
            else str(label.label_type)
        )
        is_currency_label = label_type_str in {
            "GRAND_TOTAL",
            "TAX",
            "SUBTOTAL",
            "LINE_TOTAL",
        }

        if is_currency_label:
            if not getattr(label, "line_ids", None):
                print(
                    f"   ⚠️ Skipping currency label '{getattr(label, 'line_text', '')}' - no line_ids"
                )
                continue

            for line_id in getattr(label, "line_ids", []) or []:
                word_ids: List[int] = []
                # Map currency strictly by amount token(s)
                amount_tokens: List[str] = []
                try:
                    amt = float(getattr(label, "amount"))
                    amt_str = f"{amt:.2f}".upper()
                    amount_tokens = [amt_str, f"${amt_str}"]
                except Exception:
                    amount_tokens = []

                for cand in amount_tokens:
                    ids = word_mapping.get((line_id, cand), [])
                    if ids:
                        word_ids.extend(ids)
                if word_ids:
                    texts = [
                        word_text_map.get((line_id, wid), "")
                        for wid in word_ids
                    ]
                    print(
                        f"   ✅ Amount match for {label_type_str} {getattr(label, 'amount', None)} on line {line_id}: tokens={amount_tokens} word_ids={sorted(set(word_ids))} texts={texts}"
                    )

                if not word_ids:
                    available = sorted(
                        list(line_to_word_texts.get(line_id, []))
                    )
                    sample = ", ".join(available[:10])
                    print(
                        f"   🔬 Debug: No amount-token match for {label_type_str} {getattr(label, 'amount', None)} on line {line_id}. Tried {amount_tokens} Available: [{sample}]"
                    )

                if not word_ids:
                    print(
                        f"   ⚠️ No words found for currency label '{label_type_str}' on line {line_id}"
                    )
                    continue

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
        else:
            # Line item: search globally for the word_text across all words
            candidate_texts = set()
            wt = str(getattr(label, "word_text", "")).strip().upper()
            if wt:
                candidate_texts.add(wt)
                for tok in wt.split():
                    candidate_texts.add(tok)

            matches: List[tuple[int, int]] = []  # (line_id, word_id)
            for cand in candidate_texts:
                for loc in global_word_index.get(cand, []) or []:
                    matches.append(loc)

            # Partial containment fallback across entire receipt
            if not matches and candidate_texts:
                for text_key, locations in global_word_index.items():
                    if any(text_key in cand for cand in candidate_texts):
                        matches.extend(locations)

            if not matches:
                print(
                    f"   ⚠️ No words found for line-item label '{label_type_str}' text='{wt}' across receipt"
                )
                continue

            for line_id, word_id in matches:
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
    receipt_word_labels_to_add: List[ReceiptWordLabel],
    receipt_word_labels_to_update: Optional[List[ReceiptWordLabel]] = None,
    dry_run: bool = False,
) -> dict:
    """Save ReceiptWordLabels to DynamoDB (adds and updates)."""

    to_add = receipt_word_labels_to_add or []
    to_update = receipt_word_labels_to_update or []

    if dry_run:
        print(
            f"   🔍 DRY RUN: Would add {len(to_add)} and update {len(to_update)} ReceiptWordLabel entities"
        )
        return {
            "saved": 0,
            "updated": 0,
            "skipped": len(to_add) + len(to_update),
        }

    saved_count = 0
    updated_count = 0
    failed_count = 0

    # Adds (bulk then fallback)
    if to_add:
        print(f"   💾 Adding {len(to_add)} ReceiptWordLabel entities...")
        try:
            client.add_receipt_word_labels(to_add)
            saved_count += len(to_add)
            print(f"   ✅ Added {len(to_add)} labels")
        except Exception as e:
            print(f"   ⚠️ Bulk add failed: {e}")
            print("   🔄 Attempting individual adds...")
            for label in to_add:
                try:
                    client.add_receipt_word_label(label)
                    saved_count += 1
                except Exception as individual_error:
                    print(
                        f"   ⚠️ Failed to add label {label.label} for word {label.word_id}: {individual_error}"
                    )
                    failed_count += 1

    # Updates (bulk-friendly API provided by client)
    if to_update:
        print(f"   ♻️ Updating {len(to_update)} ReceiptWordLabel entities...")
        try:
            client.update_receipt_word_labels(to_update)
            updated_count += len(to_update)
            print(f"   ✅ Updated {len(to_update)} labels")
        except Exception as e:
            print(f"   ⚠️ Bulk update failed: {e}")
            print("   🔄 Attempting individual updates...")
            for label in to_update:
                try:
                    client.update_receipt_word_label(label)
                    updated_count += 1
                except Exception as individual_error:
                    print(
                        f"   ⚠️ Failed to update label {label.label} for word {label.word_id}: {individual_error}"
                    )
                    failed_count += 1

    print(
        f"   📊 Save Results → added: {saved_count}, updated: {updated_count}, failed: {failed_count}"
    )
    return {
        "saved": saved_count,
        "updated": updated_count,
        "failed": failed_count,
    }


async def combine_results(
    state: CurrencyAnalysisState, save_dev_state: bool = False
) -> CurrencyAnalysisState:
    """Final node: Combine all results and calculate final metrics.

    Args:
        state: Current workflow state
        save_dev_state: Whether to save state for development (default: False)
    """

    print(f"🔄 Combining results")

    # 💾 Save state for development if requested (align with ./dev.states)
    if save_dev_state:
        print(f"   💾 Saving state for development...")
        save_state_for_development(
            state=dict(state),
            stage="combine_results_start",
            receipt_id=state.get("receipt_id"),
            save_format="both",
            output_dir="./dev.states",
        )

    # Combine all discovered labels
    discovered_labels = []

    # Add currency labels from Phase 1
    currency_labels = state.get("currency_labels", [])
    discovered_labels.extend(currency_labels)

    # Add line item labels from Phase 2 (already reduced by graph reducer)
    line_item_labels = state.get("line_item_labels", [])
    print(f"   🧮 Phase 2 raw labels: {len(line_item_labels)}")
    if line_item_labels:
        try:
            preview = ", ".join(
                [
                    f"{getattr(lbl.label_type, 'value', str(getattr(lbl, 'label_type', '')))}:'{getattr(lbl, 'word_text', getattr(lbl, 'line_text', ''))}'@{getattr(lbl, 'line_ids', [])}"
                    for lbl in line_item_labels[:5]
                ]
            )
            print(f"      ↳ sample: [{preview}]")
        except Exception:
            pass

    # Filter out currency duplicates - prefer Phase 1 currency labels over Phase 2
    currency_types = {"GRAND_TOTAL", "TAX", "SUBTOTAL", "LINE_TOTAL"}
    phase1_currency_texts = {
        label.line_text
        for label in currency_labels
        if label.label_type.value in currency_types
    }

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
    if line_item_labels and not filtered_line_labels:
        print(
            "   ℹ️ All Phase 2 labels were dropped by duplicate-currency filter"
        )
    if not line_item_labels:
        print("   ℹ️ Phase 2 reducer produced 0 labels")

    # Create initial proposed ReceiptWordLabel entities from discovered labels
    proposed_labels_raw = create_receipt_word_labels_from_currency_labels(
        discovered_labels=discovered_labels,
        lines=state.get("lines", []),
        words=state.get("words"),
        image_id=state.get("image_id", ""),
        receipt_id=state.get("receipt_id", ""),
        client=state.get("dynamo_client"),
    )

    # Aggregate duplicates from proposals and set audit trail
    def combine_text(a: Optional[str], b: Optional[str]) -> Optional[str]:
        if a and b:
            if b in a:
                return a
            return f"{a} | {b}" if a != b else a
        return a or b

    def combine_sources(a: Optional[str], b: Optional[str]) -> Optional[str]:
        if not a and not b:
            return None
        if not a:
            return b
        if not b:
            return a
        parts = {p.strip() for p in (a.split(";") + b.split(";")) if p.strip()}
        return ";".join(sorted(parts))

    aggregated: dict[tuple, ReceiptWordLabel] = {}
    for lbl in proposed_labels_raw:
        key = (
            lbl.image_id,
            lbl.receipt_id,
            lbl.line_id,
            lbl.word_id,
            lbl.label,
        )
        if key not in aggregated:
            aggregated[key] = lbl
        else:
            # Merge reasoning and mark consolidation
            existing = aggregated[key]
            merged_reason = combine_text(existing.reasoning, lbl.reasoning)
            merged_consolidated = combine_sources(
                existing.label_consolidated_from, "MULTIPLE_SOURCES"
            )
            aggregated[key] = ReceiptWordLabel(
                image_id=existing.image_id,
                receipt_id=existing.receipt_id,
                line_id=existing.line_id,
                word_id=existing.word_id,
                label=existing.label,
                reasoning=merged_reason,
                timestamp_added=existing.timestamp_added,
                validation_status=existing.validation_status,
                label_proposed_by=existing.label_proposed_by,
                label_consolidated_from=merged_consolidated,
            )

    proposed_labels = list(aggregated.values())

    # Enforce precedence: If a word has a LINE_TOTAL (from Phase 1),
    # drop competing Phase 2 labels (e.g., UNIT_PRICE/PRODUCT_NAME) for that word
    try:
        line_total_words: set[tuple] = set(
            (
                lbl.line_id,
                lbl.word_id,
            )
            for lbl in proposed_labels
            if lbl.label == "LINE_TOTAL"
        )

        if line_total_words:
            before_count = len(proposed_labels)
            proposed_labels = [
                lbl
                for lbl in proposed_labels
                if (lbl.line_id, lbl.word_id) not in line_total_words
                or lbl.label == "LINE_TOTAL"
            ]
            after_count = len(proposed_labels)
            dropped = before_count - after_count
            if dropped:
                print(
                    f"   🧹 Precedence: dropped {dropped} non-LINE_TOTAL labels on words with LINE_TOTAL"
                )
    except Exception:
        pass

    # Load existing labels from DynamoDB for this receipt
    client: DynamoClient = state.get("dynamo_client")
    image_id = state.get("image_id", "")
    receipt_id_str = state.get("receipt_id", "")  # format: image/receipt
    try:
        actual_receipt_id = int(str(receipt_id_str).split("/")[-1])
    except Exception:
        actual_receipt_id = 0

    # Prefer labels preloaded from get_receipt_details; fallback to paged call
    if state.get("existing_word_labels") is not None:
        existing = state.get("existing_word_labels")
        print(f"   📦 Loaded {len(existing)} existing labels (preloaded)")
    else:
        print("   📥 Loading existing ReceiptWordLabels for this receipt...")
        existing = []
        lek = None
        while True:
            batch, lek = client.list_receipt_word_labels_for_receipt(
                image_id, actual_receipt_id, last_evaluated_key=lek
            )
            existing.extend(batch)
            if not lek:
                break
        print(f"   📦 Loaded {len(existing)} existing labels")

    existing_by_key = {
        (e.image_id, e.receipt_id, e.line_id, e.word_id, e.label): e
        for e in existing
    }

    # Compute adds and updates
    to_add: List[ReceiptWordLabel] = []
    to_update: List[ReceiptWordLabel] = []

    for lbl in proposed_labels:
        key = (
            lbl.image_id,
            lbl.receipt_id,
            lbl.line_id,
            lbl.word_id,
            lbl.label,
        )
        current = existing_by_key.get(key)
        if current is None:
            to_add.append(lbl)
            continue
        # Determine if an update is needed (reasoning or audit trail improvement)
        needs_update = False

        new_reasoning = combine_text(current.reasoning, lbl.reasoning)
        new_consolidated = combine_sources(
            current.label_consolidated_from,
            lbl.label_consolidated_from or "simple_receipt_analyzer",
        )

        # We preserve original timestamp_added and validation_status
        updated = ReceiptWordLabel(
            image_id=current.image_id,
            receipt_id=current.receipt_id,
            line_id=current.line_id,
            word_id=current.word_id,
            label=current.label,
            reasoning=new_reasoning,
            timestamp_added=current.timestamp_added,
            validation_status=current.validation_status,
            label_proposed_by=current.label_proposed_by
            or lbl.label_proposed_by,
            label_consolidated_from=new_consolidated,
        )

        # Consider validation_status to avoid churn on validated items
        is_pending = str(current.validation_status or "").upper() in {
            "PENDING",
            "NONE",
            "",
        }

        if not is_pending:
            # Fully skip any updates for validated items (freeze state)
            print(
                f"   ℹ️ Skipping update for validated label {current.label} on word {current.word_id}"
            )
            continue

        # Decide if any meaningful change remains
        if (
            (updated.label_consolidated_from or "")
            != (current.label_consolidated_from or "")
        ) or (
            is_pending
            and (
                (updated.reasoning != current.reasoning)
                or (
                    (updated.label_proposed_by or "")
                    != (current.label_proposed_by or "")
                )
            )
        ):
            needs_update = True
        else:
            # No-op; validated labels are already handled above
            pass

        if needs_update:
            to_update.append(updated)

    # Detailed per-word debug of proposals vs existing vs adds/updates
    try:
        # Build (line_id, word_id) -> text map from preloaded words if available
        word_text_map: dict[tuple, str] = {}
        try:
            for w in state.get("words", []) or []:
                word_text_map[(w.line_id, w.word_id)] = getattr(
                    w, "text", ""
                ).strip()
        except Exception:
            pass

        def group_labels_by_word(
            labels: List[ReceiptWordLabel],
        ) -> dict[tuple, List[str]]:
            grouped: dict[tuple, List[str]] = {}
            for l in labels:
                wkey = (l.line_id, l.word_id)
                grouped.setdefault(wkey, []).append(l.label)
            return grouped

        proposed_by_word = group_labels_by_word(proposed_labels)
        existing_by_word_only = group_labels_by_word(existing)
        add_by_word = group_labels_by_word(to_add)
        update_by_word = group_labels_by_word(to_update)

        all_word_keys = (
            set(proposed_by_word.keys())
            | set(existing_by_word_only.keys())
            | set(add_by_word.keys())
            | set(update_by_word.keys())
        )
        for line_id_dbg, word_id_dbg in sorted(all_word_keys):
            proposed_labels_list = sorted(
                set(proposed_by_word.get((line_id_dbg, word_id_dbg), []))
            )
            existing_labels_list = sorted(
                set(existing_by_word_only.get((line_id_dbg, word_id_dbg), []))
            )
            add_labels_list = sorted(
                set(add_by_word.get((line_id_dbg, word_id_dbg), []))
            )
            update_labels_list = sorted(
                set(update_by_word.get((line_id_dbg, word_id_dbg), []))
            )
            word_text_dbg = word_text_map.get((line_id_dbg, word_id_dbg), "")
            print(
                f"   🧾 Word line={line_id_dbg} word={word_id_dbg} text='{word_text_dbg}' → proposed={proposed_labels_list} existing={existing_labels_list} add={add_labels_list} update={update_labels_list}"
            )
    except Exception as _dbg_e:
        print(f"   ⚠️ Debug aggregation failed: {_dbg_e}")

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
    print(
        f"   📌 Proposed adds: {len(to_add)}, updates: {len(to_update)} (existing: {len(existing)})"
    )
    print(f"   ✅ Overall confidence: {confidence_score:.2f}")

    return {
        "discovered_labels": discovered_labels,
        # Backward compatibility: keep prior key for adds
        "receipt_word_labels": to_add,
        # New detailed outputs
        "receipt_word_labels_to_add": to_add,
        "receipt_word_labels_to_update": to_update,
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
    if save_labels:
        adds = result.get(
            "receipt_word_labels_to_add", result.get("receipt_word_labels", [])
        )
        updates = result.get("receipt_word_labels_to_update", [])
        if adds or updates:
            print(f"\n💾 SAVING RECEIPT WORD LABELS (adds + updates)")
            save_results = await save_receipt_word_labels(
                client=client,
                receipt_word_labels_to_add=adds,
                receipt_word_labels_to_update=updates,
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
