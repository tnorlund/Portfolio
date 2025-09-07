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
from receipt_label.langchain.state.currency_validation import (
    save_json,
    CurrencyAnalysisState,
)
from receipt_label.langchain.nodes.load_data import load_receipt_data
from receipt_label.langchain.nodes.phase1 import phase1_currency_analysis
from receipt_label.langchain.nodes.phase2 import (
    dispatch_to_parallel_phase2,
    phase2_line_analysis,
)
from receipt_label.langchain.services.label_mapping import (
    create_receipt_word_labels_from_currency_labels,
)


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
        save_json(
            state=dict(state),
            stage="combine_results_start",
            receipt_id=state.receipt_id,
            output_dir="./dev.states",
        )

    # Combine all discovered labels
    discovered_labels = []

    # Add currency labels from Phase 1
    currency_labels = state.currency_labels
    discovered_labels.extend(currency_labels)

    # Add line item labels from Phase 2 (already reduced by graph reducer)
    line_item_labels = state.line_item_labels
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
        lines=state.lines,
        words=state.words,
        image_id=state.image_id,
        receipt_id=state.receipt_id,
        client=state.dynamo_client,
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
    client: DynamoClient = state.dynamo_client
    image_id = state.image_id
    receipt_id_str = state.receipt_id  # format: image/receipt
    try:
        actual_receipt_id = int(str(receipt_id_str).split("/")[-1])
    except Exception:
        actual_receipt_id = 0

    # Prefer labels preloaded from get_receipt_details; fallback to paged call
    if state.existing_word_labels is not None:
        existing = state.existing_word_labels
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
            for w in state.words:
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
