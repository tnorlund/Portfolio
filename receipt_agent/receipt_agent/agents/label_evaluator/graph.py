"""
Label Evaluator Agent Workflow

A LangGraph agent that validates receipt word labels by analyzing spatial patterns
within receipts and across receipts from the same merchant.

This is a deterministic agent (no LLM) that uses geometric analysis and pattern
matching to detect labeling errors such as:
- Position anomalies (MERCHANT_NAME appearing in the middle of the receipt)
- Same-line conflicts (MERCHANT_NAME on same line as PRODUCT_NAME)
- Missing labels in clusters (unlabeled zip code surrounded by ADDRESS_LINE)
- Text-label conflicts (same word text with different labels at different positions)
"""

import logging
from datetime import datetime
from typing import Any, Optional

from langgraph.graph import END, StateGraph

from receipt_dynamo.constants import ValidationStatus
from receipt_dynamo.entities import ReceiptWordLabel

from receipt_agent.agents.label_evaluator.helpers import (
    assemble_visual_lines,
    build_word_contexts,
    compute_merchant_patterns,
    evaluate_word_contexts,
)
from receipt_agent.agents.label_evaluator.state import (
    EvaluationIssue,
    EvaluatorState,
    OtherReceiptData,
)

logger = logging.getLogger(__name__)

# Maximum number of other receipts to fetch for pattern learning
MAX_OTHER_RECEIPTS = 10


def create_label_evaluator_graph(
    dynamo_client: Any,
) -> Any:
    """
    Create the label evaluator workflow graph.

    Args:
        dynamo_client: DynamoDB client for fetching receipt data

    Returns:
        Compiled LangGraph workflow
    """
    # Store client in closure for node access
    _dynamo_client = dynamo_client

    def fetch_receipt_data(state: EvaluatorState) -> dict:
        """Fetch words, labels, and metadata for the receipt being evaluated."""
        try:
            # Get all words for this receipt
            words = _dynamo_client.list_receipt_words_from_receipt(
                state.image_id, state.receipt_id
            )
            if isinstance(words, tuple):
                words = words[0]  # Handle pagination tuple

            # Get all labels for this receipt (includes full history per word)
            labels, _ = _dynamo_client.list_receipt_word_labels_for_receipt(
                state.image_id, state.receipt_id
            )

            # Get receipt metadata (for merchant info)
            try:
                metadata = _dynamo_client.get_receipt_metadata(
                    state.image_id, state.receipt_id
                )
            except Exception:
                metadata = None
                logger.warning(
                    f"Could not fetch metadata for receipt "
                    f"{state.image_id}#{state.receipt_id}"
                )

            logger.info(
                f"Fetched {len(words)} words and {len(labels)} labels for "
                f"receipt {state.image_id}#{state.receipt_id}"
            )

            return {
                "words": words,
                "labels": labels,
                "metadata": metadata,
            }

        except Exception as e:
            logger.error(f"Error fetching receipt data: {e}")
            return {"error": f"Failed to fetch receipt data: {e}"}

    def fetch_merchant_receipts(state: EvaluatorState) -> dict:
        """Fetch other receipts from the same merchant for pattern learning."""
        if state.error:
            return {}  # Skip if previous step failed

        if not state.metadata:
            logger.info("No metadata available, skipping merchant pattern learning")
            return {"other_receipt_data": []}

        merchant_name = (
            state.metadata.canonical_merchant_name or state.metadata.merchant_name
        )
        if not merchant_name:
            logger.info("No merchant name available, skipping pattern learning")
            return {"other_receipt_data": []}

        try:
            # Query for other receipts with same merchant
            other_metadatas, _ = _dynamo_client.get_receipt_metadatas_by_merchant(
                merchant_name,
                limit=MAX_OTHER_RECEIPTS + 1,  # +1 to account for current receipt
            )

            # Filter out current receipt
            other_metadatas = [
                m
                for m in other_metadatas
                if not (m.image_id == state.image_id and m.receipt_id == state.receipt_id)
            ][:MAX_OTHER_RECEIPTS]

            if not other_metadatas:
                logger.info(f"No other receipts found for merchant '{merchant_name}'")
                return {"other_receipt_data": []}

            logger.info(
                f"Found {len(other_metadatas)} other receipts for merchant "
                f"'{merchant_name}'"
            )

            # Fetch words and labels for each other receipt
            other_receipt_data = []
            for other_meta in other_metadatas:
                try:
                    words = _dynamo_client.list_receipt_words_from_receipt(
                        other_meta.image_id, other_meta.receipt_id
                    )
                    if isinstance(words, tuple):
                        words = words[0]

                    labels, _ = _dynamo_client.list_receipt_word_labels_for_receipt(
                        other_meta.image_id, other_meta.receipt_id
                    )

                    other_receipt_data.append(
                        OtherReceiptData(
                            metadata=other_meta,
                            words=words,
                            labels=labels,
                        )
                    )
                except Exception as e:
                    logger.warning(
                        f"Could not fetch data for receipt "
                        f"{other_meta.image_id}#{other_meta.receipt_id}: {e}"
                    )
                    continue

            return {"other_receipt_data": other_receipt_data}

        except Exception as e:
            logger.error(f"Error fetching merchant receipts: {e}")
            return {"other_receipt_data": []}

    def build_spatial_context(state: EvaluatorState) -> dict:
        """Build visual lines and word contexts using geometric analysis."""
        if state.error:
            return {}

        if not state.words:
            logger.warning("No words to analyze")
            return {"word_contexts": [], "visual_lines": []}

        # Build WordContext for each word with label history
        word_contexts = build_word_contexts(state.words, state.labels)

        # Group into visual lines by y-coordinate proximity
        visual_lines = assemble_visual_lines(word_contexts)

        logger.info(
            f"Built {len(word_contexts)} word contexts in "
            f"{len(visual_lines)} visual lines"
        )

        return {
            "word_contexts": word_contexts,
            "visual_lines": visual_lines,
        }

    def compute_patterns(state: EvaluatorState) -> dict:
        """Compute merchant patterns from other receipts."""
        if state.error:
            return {}

        if not state.other_receipt_data:
            return {"merchant_patterns": None}

        merchant_name = (
            state.metadata.canonical_merchant_name
            if state.metadata
            else "Unknown"
        ) or (state.metadata.merchant_name if state.metadata else "Unknown")

        patterns = compute_merchant_patterns(
            state.other_receipt_data,
            merchant_name,
        )

        if patterns:
            logger.info(
                f"Computed patterns from {patterns.receipt_count} receipts: "
                f"{len(patterns.label_positions)} label types"
            )

        return {"merchant_patterns": patterns}

    def evaluate_labels(state: EvaluatorState) -> dict:
        """Apply validation rules and detect issues."""
        if state.error:
            return {}

        if not state.word_contexts:
            return {"issues_found": [], "new_labels": []}

        # Evaluate all word contexts against validation rules
        issues = evaluate_word_contexts(
            state.word_contexts,
            state.merchant_patterns,
        )

        logger.info(f"Found {len(issues)} issues")

        # Create new ReceiptWordLabel entries for each issue
        new_labels = []
        for issue in issues:
            label = _create_evaluation_label(issue)
            new_labels.append(label)

        return {
            "issues_found": issues,
            "new_labels": new_labels,
        }

    def write_evaluation_results(state: EvaluatorState) -> dict:
        """Write new evaluation labels to DynamoDB."""
        if state.error:
            return {}

        if not state.new_labels:
            logger.info("No issues found, nothing to write")
            return {}

        try:
            _dynamo_client.add_receipt_word_labels(state.new_labels)
            logger.info(f"Wrote {len(state.new_labels)} evaluation labels to DynamoDB")
        except Exception as e:
            logger.error(f"Error writing evaluation results: {e}")
            return {"error": f"Failed to write evaluation results: {e}"}

        return {}

    # Build the graph
    workflow = StateGraph(EvaluatorState)

    # Add nodes
    workflow.add_node("fetch_receipt_data", fetch_receipt_data)
    workflow.add_node("fetch_merchant_receipts", fetch_merchant_receipts)
    workflow.add_node("build_spatial_context", build_spatial_context)
    workflow.add_node("compute_patterns", compute_patterns)
    workflow.add_node("evaluate_labels", evaluate_labels)
    workflow.add_node("write_evaluation_results", write_evaluation_results)

    # Linear flow
    workflow.add_edge("fetch_receipt_data", "fetch_merchant_receipts")
    workflow.add_edge("fetch_merchant_receipts", "build_spatial_context")
    workflow.add_edge("build_spatial_context", "compute_patterns")
    workflow.add_edge("compute_patterns", "evaluate_labels")
    workflow.add_edge("evaluate_labels", "write_evaluation_results")
    workflow.add_edge("write_evaluation_results", END)

    workflow.set_entry_point("fetch_receipt_data")

    return workflow.compile()


def _create_evaluation_label(issue: EvaluationIssue) -> ReceiptWordLabel:
    """
    Create a new ReceiptWordLabel documenting the evaluation result.

    Args:
        issue: The evaluation issue to record

    Returns:
        New ReceiptWordLabel with evaluation status and reasoning
    """
    # Determine the label to use
    if issue.suggested_label:
        label = issue.suggested_label
    elif issue.current_label:
        label = issue.current_label
    else:
        label = "UNKNOWN"

    return ReceiptWordLabel(
        image_id=issue.word.image_id,
        receipt_id=issue.word.receipt_id,
        line_id=issue.word.line_id,
        word_id=issue.word.word_id,
        label=label,
        reasoning=issue.reasoning,
        timestamp_added=datetime.now(),
        validation_status=issue.suggested_status,
        label_proposed_by="label_evaluator_agent",
        label_consolidated_from=None,
    )


async def run_label_evaluator(
    graph: Any,
    image_id: str,
    receipt_id: int,
) -> dict:
    """
    Run the label evaluator workflow for a receipt.

    Args:
        graph: Compiled workflow graph
        image_id: Image ID of the receipt
        receipt_id: Receipt ID

    Returns:
        Evaluation result dict with issues found and labels written
    """
    initial_state = EvaluatorState(
        image_id=image_id,
        receipt_id=receipt_id,
    )

    logger.info(f"Starting label evaluation for {image_id}#{receipt_id}")

    try:
        config = {
            "recursion_limit": 10,
            "configurable": {"thread_id": f"{image_id}#{receipt_id}"},
        }

        final_state = await graph.ainvoke(initial_state, config=config)

        result = {
            "image_id": image_id,
            "receipt_id": receipt_id,
            "issues_found": len(final_state.issues_found),
            "labels_written": len(final_state.new_labels),
            "issues": [
                {
                    "type": issue.issue_type,
                    "word_text": issue.word.text,
                    "current_label": issue.current_label,
                    "suggested_status": issue.suggested_status,
                    "suggested_label": issue.suggested_label,
                    "reasoning": issue.reasoning,
                }
                for issue in final_state.issues_found
            ],
            "error": final_state.error,
        }

        if final_state.merchant_patterns:
            result["merchant_receipts_analyzed"] = (
                final_state.merchant_patterns.receipt_count
            )

        logger.info(
            f"Evaluation complete: {result['issues_found']} issues, "
            f"{result['labels_written']} labels written"
        )

        return result

    except Exception as e:
        logger.error(f"Error in label evaluation: {e}")
        return {
            "image_id": image_id,
            "receipt_id": receipt_id,
            "issues_found": 0,
            "labels_written": 0,
            "issues": [],
            "error": str(e),
        }


def run_label_evaluator_sync(
    graph: Any,
    image_id: str,
    receipt_id: int,
) -> dict:
    """
    Synchronous wrapper for run_label_evaluator.

    Args:
        graph: Compiled workflow graph
        image_id: Image ID of the receipt
        receipt_id: Receipt ID

    Returns:
        Evaluation result dict
    """
    import asyncio

    return asyncio.run(run_label_evaluator(graph, image_id, receipt_id))
