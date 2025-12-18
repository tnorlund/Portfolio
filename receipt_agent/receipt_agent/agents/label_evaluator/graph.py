"""
Label Evaluator Agent Workflow

A LangGraph agent that validates receipt word labels by analyzing spatial patterns
within receipts and across receipts from the same merchant.

The workflow has two phases:
1. Deterministic evaluation: Uses geometric analysis and pattern matching to flag issues
2. LLM review: Uses a cheap LLM (Haiku) to make semantic decisions on flagged issues

Detects labeling errors such as:
- Position anomalies (MERCHANT_NAME appearing in the middle of the receipt)
- Same-line conflicts (MERCHANT_NAME on same line as PRODUCT_NAME)
- Missing labels in clusters (unlabeled zip code surrounded by ADDRESS_LINE)
- Text-label conflicts (same word text with different labels at different positions)
"""

import json
import logging
from datetime import datetime
from typing import Any, List, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph

# Optional: langchain_anthropic for LLM review
try:
    from langchain_anthropic import ChatAnthropic

    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False
    ChatAnthropic = None  # type: ignore

from receipt_dynamo.constants import ValidationStatus
from receipt_dynamo.entities import ReceiptWordLabel

from receipt_agent.agents.label_evaluator.helpers import (
    assemble_visual_lines,
    build_review_context,
    build_word_contexts,
    compute_merchant_patterns,
    evaluate_word_contexts,
)
from receipt_agent.agents.label_evaluator.state import (
    EvaluationIssue,
    EvaluatorState,
    OtherReceiptData,
    ReviewResult,
)

# Import CORE_LABELS definitions
try:
    from receipt_label.constants import CORE_LABELS
except ImportError:
    # Fallback if receipt_label is not available
    CORE_LABELS = {
        "MERCHANT_NAME": "Trading name or brand of the store issuing the receipt.",
        "STORE_HOURS": "Printed business hours or opening times for the merchant.",
        "PHONE_NUMBER": "Telephone number printed on the receipt (store's main line).",
        "WEBSITE": "Web or email address printed on the receipt (e.g., sprouts.com).",
        "LOYALTY_ID": "Customer loyalty / rewards / membership identifier.",
        "ADDRESS_LINE": "Full address line (street + city etc.) printed on the receipt.",
        "DATE": "Calendar date of the transaction.",
        "TIME": "Time of the transaction.",
        "PAYMENT_METHOD": "Payment instrument summary (e.g., VISA ••••1234, CASH).",
        "COUPON": "Coupon code or description that reduces price.",
        "DISCOUNT": "Any non-coupon discount line item (e.g., '10% OFF').",
        "PRODUCT_NAME": "Name of a product or item being purchased.",
        "QUANTITY": "Number of units purchased (e.g., '2', '1.5 lbs').",
        "UNIT_PRICE": "Price per unit of the product.",
        "LINE_TOTAL": "Total price for a line item (quantity × unit_price).",
        "SUBTOTAL": "Subtotal before tax and discounts.",
        "TAX": "Tax amount (sales tax, VAT, etc.).",
        "GRAND_TOTAL": "Final total amount paid (after all discounts and taxes).",
    }

logger = logging.getLogger(__name__)

# Maximum number of other receipts to fetch for pattern learning
MAX_OTHER_RECEIPTS = 10

# LLM Review Prompt
LLM_REVIEW_PROMPT = """You are reviewing a flagged label issue on a receipt.

An automated evaluator flagged this label as potentially incorrect based on spatial patterns.
Your job is to make the final semantic decision using the full context.

## CORE_LABELS Definitions

{core_labels}

## Issue Details

- **Word**: "{word_text}"
- **Current Label**: {current_label}
- **Issue Type**: {issue_type}
- **Evaluator Reasoning**: {evaluator_reasoning}

## Receipt Context

The word is marked with [brackets] in the receipt below:

```
{receipt_text}
```

## Same Visual Line

Line: "{visual_line_text}"
Labels on this line: {visual_line_labels}

## Label History

{label_history}

## Your Task

Decide if the current label is correct:

- **VALID**: The label IS correct despite the flag (false positive from evaluator)
  - Example: "CHARGE" labeled PAYMENT_METHOD is valid if it's part of "CREDIT CARD CHARGE"

- **INVALID**: The label IS wrong - provide the correct label
  - Example: "Sprouts" in product area should be PRODUCT_NAME, not MERCHANT_NAME

- **NEEDS_REVIEW**: Genuinely ambiguous, needs human review

Respond with ONLY a JSON object (no markdown, no explanation outside JSON):
{{"decision": "VALID|INVALID|NEEDS_REVIEW", "reasoning": "your explanation", "suggested_label": "LABEL_NAME or null"}}"""


def _format_core_labels() -> str:
    """Format CORE_LABELS as a readable string."""
    return "\n".join(f"- **{label}**: {definition}" for label, definition in CORE_LABELS.items())


def create_label_evaluator_graph(
    dynamo_client: Any,
    llm_model: str = "claude-3-5-haiku-latest",
    llm: Any = None,
) -> Any:
    """
    Create the label evaluator workflow graph.

    Args:
        dynamo_client: DynamoDB client for fetching receipt data
        llm_model: Model to use for LLM review (default: claude-3-5-haiku-latest)
        llm: Optional pre-configured LLM instance. If None, creates ChatAnthropic.

    Returns:
        Compiled LangGraph workflow
    """
    # Store client in closure for node access
    _dynamo_client = dynamo_client

    # Initialize LLM for review (cheap model for cost efficiency)
    if llm is not None:
        _llm = llm
    elif HAS_ANTHROPIC and ChatAnthropic is not None:
        _llm = ChatAnthropic(model=llm_model, temperature=0)
    else:
        _llm = None
        logger.warning(
            "langchain_anthropic not installed. LLM review will be skipped. "
            "Install with: pip install langchain-anthropic"
        )

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
            return {"issues_found": []}

        # Evaluate all word contexts against validation rules
        issues = evaluate_word_contexts(
            state.word_contexts,
            state.merchant_patterns,
        )

        logger.info(f"Found {len(issues)} issues")

        return {"issues_found": issues}

    def review_issues_with_llm(state: EvaluatorState) -> dict:
        """Use LLM to review flagged issues and make semantic decisions."""
        if state.error:
            return {}

        if not state.issues_found:
            return {"review_results": [], "new_labels": []}

        # Skip LLM review if configured
        if state.skip_llm_review:
            logger.info("Skipping LLM review (skip_llm_review=True)")
            # Use evaluator results directly
            new_labels = []
            for issue in state.issues_found:
                label = _create_evaluation_label(issue, None)
                new_labels.append(label)
            return {"review_results": [], "new_labels": new_labels}

        merchant_name = "Unknown"
        if state.metadata:
            merchant_name = (
                state.metadata.canonical_merchant_name
                or state.metadata.merchant_name
                or "Unknown"
            )

        review_results: List[ReviewResult] = []
        new_labels: List[ReceiptWordLabel] = []

        for issue in state.issues_found:
            try:
                # Build context for this issue
                review_ctx = build_review_context(
                    issue, state.visual_lines, merchant_name
                )

                # Format label history
                if review_ctx.label_history:
                    history_text = "\n".join(
                        f"- {h['label']} ({h['status']}) by {h['proposed_by']}"
                        for h in review_ctx.label_history
                    )
                else:
                    history_text = "No previous labels"

                # Format prompt
                prompt = LLM_REVIEW_PROMPT.format(
                    core_labels=_format_core_labels(),
                    word_text=review_ctx.word_text,
                    current_label=review_ctx.current_label or "None",
                    issue_type=review_ctx.issue_type,
                    evaluator_reasoning=review_ctx.evaluator_reasoning,
                    receipt_text=review_ctx.receipt_text,
                    visual_line_text=review_ctx.visual_line_text,
                    visual_line_labels=review_ctx.visual_line_labels,
                    label_history=history_text,
                )

                # Call LLM
                response = _llm.invoke([HumanMessage(content=prompt)])
                response_text = response.content.strip()

                # Parse JSON response
                try:
                    # Handle potential markdown code blocks
                    if response_text.startswith("```"):
                        response_text = response_text.split("```")[1]
                        if response_text.startswith("json"):
                            response_text = response_text[4:]
                        response_text = response_text.strip()

                    result_json = json.loads(response_text)
                    decision = result_json.get("decision", "NEEDS_REVIEW")
                    reasoning = result_json.get("reasoning", "No reasoning provided")
                    suggested_label = result_json.get("suggested_label")

                    # Validate decision
                    if decision not in ("VALID", "INVALID", "NEEDS_REVIEW"):
                        decision = "NEEDS_REVIEW"

                    review_result = ReviewResult(
                        issue=issue,
                        decision=decision,
                        reasoning=reasoning,
                        suggested_label=suggested_label,
                    )

                except json.JSONDecodeError as e:
                    logger.warning(f"Failed to parse LLM response: {e}")
                    review_result = ReviewResult(
                        issue=issue,
                        decision="NEEDS_REVIEW",
                        reasoning=f"LLM response parsing failed: {response_text[:100]}",
                        review_completed=False,
                        review_error=str(e),
                    )

                review_results.append(review_result)

                # Create label based on review result
                label = _create_evaluation_label(issue, review_result)
                new_labels.append(label)

                logger.info(
                    f"Reviewed '{issue.word.text}': {review_result.decision} - {reasoning[:50]}..."
                )

            except Exception as e:
                logger.error(f"Error reviewing issue for '{issue.word.text}': {e}")
                # Fall back to evaluator result
                review_result = ReviewResult(
                    issue=issue,
                    decision=issue.suggested_status,
                    reasoning=f"LLM review failed: {e}. Using evaluator result.",
                    review_completed=False,
                    review_error=str(e),
                )
                review_results.append(review_result)
                label = _create_evaluation_label(issue, review_result)
                new_labels.append(label)

        logger.info(f"LLM reviewed {len(review_results)} issues")

        return {
            "review_results": review_results,
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
    workflow.add_node("review_issues_with_llm", review_issues_with_llm)
    workflow.add_node("write_evaluation_results", write_evaluation_results)

    # Linear flow
    workflow.add_edge("fetch_receipt_data", "fetch_merchant_receipts")
    workflow.add_edge("fetch_merchant_receipts", "build_spatial_context")
    workflow.add_edge("build_spatial_context", "compute_patterns")
    workflow.add_edge("compute_patterns", "evaluate_labels")
    workflow.add_edge("evaluate_labels", "review_issues_with_llm")
    workflow.add_edge("review_issues_with_llm", "write_evaluation_results")
    workflow.add_edge("write_evaluation_results", END)

    workflow.set_entry_point("fetch_receipt_data")

    return workflow.compile()


def _create_evaluation_label(
    issue: EvaluationIssue,
    review_result: Optional[ReviewResult] = None,
) -> ReceiptWordLabel:
    """
    Create a new ReceiptWordLabel documenting the evaluation result.

    Args:
        issue: The evaluation issue
        review_result: Optional LLM review result (if None, uses evaluator result)

    Returns:
        New ReceiptWordLabel with validation status and reasoning
    """
    if review_result:
        # Use LLM review result
        if review_result.decision == "INVALID" and review_result.suggested_label:
            label = review_result.suggested_label
        elif issue.suggested_label:
            label = issue.suggested_label
        elif issue.current_label:
            label = issue.current_label
        else:
            label = "UNKNOWN"

        validation_status = review_result.decision
        reasoning = f"[LLM Review] {review_result.reasoning}"
        proposed_by = "label_evaluator_agent:llm_review"
    else:
        # Use evaluator result directly
        if issue.suggested_label:
            label = issue.suggested_label
        elif issue.current_label:
            label = issue.current_label
        else:
            label = "UNKNOWN"

        validation_status = issue.suggested_status
        reasoning = f"[Evaluator] {issue.reasoning}"
        proposed_by = "label_evaluator_agent:pattern_match"

    return ReceiptWordLabel(
        image_id=issue.word.image_id,
        receipt_id=issue.word.receipt_id,
        line_id=issue.word.line_id,
        word_id=issue.word.word_id,
        label=label,
        reasoning=reasoning,
        timestamp_added=datetime.now(),
        validation_status=validation_status,
        label_proposed_by=proposed_by,
        label_consolidated_from=None,
    )


async def run_label_evaluator(
    graph: Any,
    image_id: str,
    receipt_id: int,
    skip_llm_review: bool = False,
) -> dict:
    """
    Run the label evaluator workflow for a receipt.

    Args:
        graph: Compiled workflow graph
        image_id: Image ID of the receipt
        receipt_id: Receipt ID
        skip_llm_review: If True, skip LLM review and use evaluator results directly

    Returns:
        Evaluation result dict with issues found, reviews, and labels written
    """
    initial_state = EvaluatorState(
        image_id=image_id,
        receipt_id=receipt_id,
        skip_llm_review=skip_llm_review,
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
            "reviews": [
                {
                    "word_text": review.issue.word.text,
                    "decision": review.decision,
                    "reasoning": review.reasoning,
                    "suggested_label": review.suggested_label,
                    "review_completed": review.review_completed,
                }
                for review in final_state.review_results
            ],
            "error": final_state.error,
        }

        if final_state.merchant_patterns:
            result["merchant_receipts_analyzed"] = (
                final_state.merchant_patterns.receipt_count
            )

        logger.info(
            f"Evaluation complete: {result['issues_found']} issues, "
            f"{len(final_state.review_results)} reviewed, "
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
            "reviews": [],
            "error": str(e),
        }


def run_label_evaluator_sync(
    graph: Any,
    image_id: str,
    receipt_id: int,
    skip_llm_review: bool = False,
) -> dict:
    """
    Synchronous wrapper for run_label_evaluator.

    Args:
        graph: Compiled workflow graph
        image_id: Image ID of the receipt
        receipt_id: Receipt ID
        skip_llm_review: If True, skip LLM review and use evaluator results directly

    Returns:
        Evaluation result dict
    """
    import asyncio

    return asyncio.run(
        run_label_evaluator(graph, image_id, receipt_id, skip_llm_review)
    )
