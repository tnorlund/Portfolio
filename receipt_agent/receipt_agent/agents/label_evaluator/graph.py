"""
Label Evaluator Agent Workflow

A LangGraph agent that validates receipt word labels by analyzing spatial
patterns within receipts and across receipts from the same merchant.

The workflow has two phases:
1. Deterministic evaluation: Uses geometric analysis and pattern matching to
   flag issues
2. LLM review: Uses a cheap LLM (Haiku) to make semantic decisions on flagged
   issues

Detects labeling errors such as:
- Position anomalies (MERCHANT_NAME appearing in the middle of the receipt)
- Same-line conflicts (MERCHANT_NAME on same line as PRODUCT_NAME)
- Missing labels in clusters (unlabeled zip code surrounded by ADDRESS_LINE)
- Text-label conflicts (same word text with different labels at different
  positions)
"""

# pylint: disable=import-outside-toplevel
# asyncio imported inside sync wrappers to avoid issues in async contexts

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from langchain_core.messages import HumanMessage
from langgraph.graph import END, StateGraph

# Optional: langchain_ollama for Ollama Cloud
try:
    from langchain_ollama import ChatOllama

    HAS_OLLAMA = True
except ImportError:
    HAS_OLLAMA = False
    ChatOllama = None  # type: ignore

from receipt_dynamo.entities import ReceiptWordLabel

from receipt_agent.agents.label_evaluator.word_context import (
    assemble_visual_lines,
    build_word_contexts,
)
from receipt_agent.agents.label_evaluator.helpers import (
    build_review_context,
    evaluate_word_contexts,
    format_similar_words_for_prompt,
    query_similar_validated_words,
)
from receipt_agent.agents.label_evaluator.patterns import (
    compute_merchant_patterns,
)
from receipt_agent.agents.label_evaluator.llm_review import (
    review_all_issues,
)
from receipt_agent.agents.label_evaluator.state import (
    EvaluationIssue,
    EvaluatorState,
    OtherReceiptData,
    ReviewResult,
)
from receipt_agent.constants import CORE_LABELS

logger = logging.getLogger(__name__)

# =============================================================================
# Coordinate Helpers
# =============================================================================


def _coord_value(coord: Any, key: str, default: float = 0.5) -> float:
    if coord is None:
        return default
    if isinstance(coord, dict):
        return float(coord.get(key, default))
    return float(getattr(coord, key, default))


# Maximum number of other receipts to fetch for pattern learning
# Pattern computation itself is fast (<0.1s for 150 receipts).
# The limit exists to control DynamoDB I/O time during sequential fetching.
# In Step Functions, use Distributed Map to parallelize fetches and increase
# this.
# Can be overridden per-run via max_receipts parameter.
MAX_OTHER_RECEIPTS = 10

# LLM Review Prompt
LLM_REVIEW_PROMPT = """You are reviewing a flagged label issue on a receipt.

An automated evaluator flagged this label as potentially incorrect based on
spatial patterns.
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

## Similar Words from Database

These are semantically similar words from other receipts with their validated
labels:

{similar_words}

IMPORTANT: Use these examples to understand what labels are typically assigned
to similar text. If the similar words show that text like "{word_text}"
typically has NO label or a DIFFERENT label,
that's strong evidence the current label may be wrong.

## Label History

{label_history}

## Your Task

Decide if the current label is correct:

- **VALID**: The label IS correct despite the flag (false positive from
  evaluator)
  - Example: "CHARGE" labeled PAYMENT_METHOD is valid if it's part of
    "CREDIT CARD CHARGE"

- **INVALID**: The label IS wrong - provide the correct label from CORE_LABELS,
  or null if no label applies
  - Example: "Sprouts" in product area should be PRODUCT_NAME, not
    MERCHANT_NAME
  - Example: "Tip:" is a descriptor, not a value - it should have no label
    (suggested_label: null)

- **NEEDS_REVIEW**: Genuinely ambiguous, needs human review

Respond with ONLY a JSON object (no markdown, no explanation outside JSON):
{{"decision": "VALID|INVALID|NEEDS_REVIEW", "reasoning": "your explanation",
 "suggested_label": "LABEL_NAME or null"}}"""


def _format_core_labels() -> str:
    """Format CORE_LABELS as a readable string."""
    return "\n".join(
        f"- **{label}**: {definition}"
        for label, definition in CORE_LABELS.items()
    )


def create_label_evaluator_graph(
    dynamo_client: Any,
    llm_model: Optional[str] = None,
    llm: Any = None,
    ollama_base_url: str = "https://ollama.com",
    ollama_api_key: Optional[str] = None,
    chroma_client: Any = None,
    max_pair_patterns: int = 4,
    max_relationship_dimension: int = 2,
) -> Any:
    """
    Create the label evaluator workflow graph.

    Args:
        dynamo_client: DynamoDB client for fetching receipt data
        llm_model: Model to use for LLM review. Default: "gpt-oss:20b-cloud"
        llm: Optional pre-configured LLM instance. If provided, ignores other
            LLM settings.
        ollama_base_url: Base URL for Ollama Cloud API (default:
            https://ollama.com)
        ollama_api_key: API key for Ollama Cloud (required for cloud usage)
        chroma_client: Optional ChromaDB client for similar word lookup.
            Words' existing embeddings are retrieved by ID, so no embed_fn is
            needed.
        max_pair_patterns: Maximum label pairs/tuples to compute geometry for
            (default: 4). Higher = more comprehensive analysis but slower.
        max_relationship_dimension: Analyze n-label relationships (default: 2).
            2=pairs, 3=triples, 4+=higher-order.

    Returns:
        Compiled LangGraph workflow
    """
    # Set default model
    if llm_model is None:
        llm_model = "gpt-oss:20b-cloud"
    # Store clients and configuration in closure for node access
    _dynamo_client = dynamo_client
    _chroma_client = chroma_client
    _max_pair_patterns = max_pair_patterns
    _max_relationship_dimension = max_relationship_dimension

    # Log ChromaDB availability
    if _chroma_client:
        logger.info("ChromaDB client provided for similar word lookup")
    else:
        logger.info(
            "ChromaDB not configured - similar word lookup will be skipped"
        )

    # Initialize LLM for review (uses Ollama)
    if llm is not None:
        _llm = llm
    elif HAS_OLLAMA and ChatOllama is not None:
        client_kwargs: dict[str, Any] = {"timeout": 120}
        if ollama_api_key:
            client_kwargs["headers"] = {
                "Authorization": f"Bearer {ollama_api_key}"
            }
        _llm = ChatOllama(
            base_url=ollama_base_url,
            model=llm_model,
            client_kwargs=client_kwargs,
            temperature=0,
        )
        logger.info("Using Ollama LLM: %s at %s", llm_model, ollama_base_url)
    else:
        _llm = None
        logger.warning(
            "langchain_ollama not installed. LLM review will be skipped. "
            "Install with: pip install langchain-ollama"
        )

    def fetch_receipt_data(state: EvaluatorState) -> dict:
        """Fetch words, labels, and place data for the receipt being
        evaluated."""
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

            # Get receipt place (for merchant info)
            try:
                place = _dynamo_client.get_receipt_place(
                    state.image_id, state.receipt_id
                )
            except Exception:
                place = None
                logger.warning(
                    "Could not fetch place data for receipt %s#%s",
                    state.image_id,
                    state.receipt_id,
                )

            logger.info(
                "Fetched %s words and %s labels for receipt %s#%s",
                len(words),
                len(labels),
                state.image_id,
                state.receipt_id,
            )

            return {
                "words": words,
                "labels": labels,
                "place": place,
            }

        except Exception as e:
            logger.error("Error fetching receipt data: %s", e)
            return {"error": f"Failed to fetch receipt data: {e}"}

    def fetch_merchant_receipts(state: EvaluatorState) -> dict:
        """Fetch other receipts from the same merchant for pattern learning."""
        if state.error:
            return {}  # Skip if previous step failed

        if not state.place:
            logger.info(
                "No place data available, skipping merchant pattern learning"
            )
            return {"other_receipt_data": []}

        merchant_name = state.place.merchant_name
        if not merchant_name:
            logger.info(
                "No merchant name available, skipping pattern learning"
            )
            return {"other_receipt_data": []}

        try:
            # Query for other receipts with same merchant
            # If MAX_OTHER_RECEIPTS is None, fetch all receipts
            limit = (
                MAX_OTHER_RECEIPTS + 1
                if MAX_OTHER_RECEIPTS is not None
                else None
            )
            other_places, _ = _dynamo_client.get_receipt_places_by_merchant(
                merchant_name,
                limit=limit,  # None means fetch all available
            )

            # Filter out current receipt
            other_places = [
                p
                for p in other_places
                if not (
                    p.image_id == state.image_id
                    and p.receipt_id == state.receipt_id
                )
            ]

            # Apply limit if MAX_OTHER_RECEIPTS is set
            if MAX_OTHER_RECEIPTS is not None:
                other_places = other_places[:MAX_OTHER_RECEIPTS]

            if not other_places:
                logger.info(
                    "No other receipts found for merchant '%s'",
                    merchant_name,
                )
                return {"other_receipt_data": []}

            logger.info(
                "Found %s other receipts for merchant '%s'",
                len(other_places),
                merchant_name,
            )

            # Fetch words and labels for each other receipt
            other_receipt_data = []
            for other_place in other_places:
                try:
                    words = _dynamo_client.list_receipt_words_from_receipt(
                        other_place.image_id, other_place.receipt_id
                    )
                    if isinstance(words, tuple):
                        words = words[0]

                    labels, _ = (
                        _dynamo_client.list_receipt_word_labels_for_receipt(
                            other_place.image_id, other_place.receipt_id
                        )
                    )

                    other_receipt_data.append(
                        OtherReceiptData(
                            place=other_place,
                            words=words,
                            labels=labels,
                        )
                    )
                except Exception as e:
                    logger.warning(
                        "Could not fetch data for receipt %s#%s: %s",
                        other_place.image_id,
                        other_place.receipt_id,
                        e,
                    )
                    continue

            return {"other_receipt_data": other_receipt_data}

        except Exception as e:
            logger.error("Error fetching merchant receipts: %s", e)
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
            "Built %s word contexts in %s visual lines",
            len(word_contexts),
            len(visual_lines),
        )

        return {
            "word_contexts": word_contexts,
            "visual_lines": visual_lines,
        }

    def compute_patterns(state: EvaluatorState) -> dict:
        """Compute merchant patterns from other receipts."""
        if state.error:
            return {}

        # Use pre-computed patterns if already provided
        if state.merchant_patterns:
            logger.info(
                "Using pre-computed patterns (%s receipts)",
                state.merchant_patterns.receipt_count,
            )
            return {}  # Don't overwrite existing patterns

        if not state.other_receipt_data:
            return {"merchant_patterns": None}

        merchant_name = state.place.merchant_name if state.place else "Unknown"

        patterns = compute_merchant_patterns(
            state.other_receipt_data,
            merchant_name,
            max_pair_patterns=_max_pair_patterns,
            max_relationship_dimension=_max_relationship_dimension,
        )

        if patterns:
            logger.info(
                "Computed patterns from %s receipts: %s label types",
                patterns.receipt_count,
                len(patterns.label_positions),
            )

        return {"merchant_patterns": patterns}

    def evaluate_labels(state: EvaluatorState) -> dict:
        """Apply validation rules and detect issues."""
        if state.error:
            return {}

        if not state.word_contexts:
            return {"issues_found": []}

        # Evaluate all word contexts against validation rules
        # Pass visual_lines for on-demand same-line lookup.
        issues = evaluate_word_contexts(
            state.word_contexts,
            state.merchant_patterns,
            state.visual_lines,
        )

        logger.info("Found %s issues", len(issues))

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
            skip_labels: list[ReceiptWordLabel] = []
            for issue in state.issues_found:
                eval_label = _create_evaluation_label(issue, None)
                skip_labels.append(eval_label)
            return {"review_results": [], "new_labels": skip_labels}

        # Skip LLM review if LLM is not available
        if _llm is None:
            logger.warning(
                "LLM not available, using evaluator results directly"
            )
            fallback_labels: list[ReceiptWordLabel] = []
            for issue in state.issues_found:
                eval_label = _create_evaluation_label(issue, None)
                fallback_labels.append(eval_label)
            return {"review_results": [], "new_labels": fallback_labels}

        merchant_name = "Unknown"
        merchant_receipt_count = 0
        if state.place:
            merchant_name = state.place.merchant_name or "Unknown"
        if state.other_receipt_data:
            merchant_receipt_count = len(state.other_receipt_data)

        # Convert EvaluationIssue objects to dicts for the new module
        issues_as_dicts = []
        for issue in state.issues_found:
            issues_as_dicts.append(
                {
                    "word_text": issue.word.text,
                    "line_id": issue.word.line_id,
                    "word_id": issue.word.word_id,
                    "image_id": issue.word.image_id,
                    "receipt_id": issue.word.receipt_id,
                    "current_label": issue.current_label,
                    "type": issue.issue_type,
                    "reasoning": issue.reasoning,
                    "suggested_label": issue.suggested_label,
                    "suggested_status": issue.suggested_status,
                }
            )

        # Convert words and labels to dicts for the new module
        words_as_dicts = []
        for word in state.words:
            top_left = word.top_left
            bottom_left = word.bottom_left
            words_as_dicts.append(
                {
                    "text": word.text,
                    "line_id": word.line_id,
                    "word_id": word.word_id,
                    "top_left": (
                        {
                            "x": _coord_value(top_left, "x"),
                            "y": _coord_value(top_left, "y"),
                        }
                        if top_left
                        else {}
                    ),
                    "bottom_left": (
                        {
                            "x": _coord_value(bottom_left, "x"),
                            "y": _coord_value(bottom_left, "y"),
                        }
                        if bottom_left
                        else {}
                    ),
                    "bounding_box": {
                        "x": _coord_value(top_left, "x"),
                        "y": _coord_value(top_left, "y"),
                    },
                }
            )

        labels_as_dicts = []
        for label in state.labels:
            labels_as_dicts.append(
                {
                    "line_id": label.line_id,
                    "word_id": label.word_id,
                    "label": label.label,
                    "validation_status": label.validation_status,
                }
            )

        # Get line item patterns from merchant_patterns if available
        line_item_patterns = None
        if state.merchant_patterns:
            line_item_patterns = {
                "merchant": state.merchant_patterns.merchant_name,
                "receipt_count": state.merchant_patterns.receipt_count,
            }

        # Call the new review_all_issues function
        try:
            llm_results = review_all_issues(
                issues=issues_as_dicts,
                receipt_words=words_as_dicts,
                receipt_labels=labels_as_dicts,
                merchant_name=merchant_name,
                merchant_receipt_count=merchant_receipt_count,
                llm=_llm,
                chroma_client=_chroma_client,
                dynamo_client=_dynamo_client,
                line_item_patterns=line_item_patterns,
                max_issues_per_call=10,
                use_receipt_context=True,
            )
        except Exception as e:
            logger.error("LLM review failed: %s", e, exc_info=True)
            # Fall back to evaluator results
            fallback_labels_on_error: list[ReceiptWordLabel] = []
            for issue in state.issues_found:
                eval_label = _create_evaluation_label(issue, None)
                fallback_labels_on_error.append(eval_label)
            return {
                "review_results": [],
                "new_labels": fallback_labels_on_error,
            }

        # Convert results back to ReviewResult objects
        review_results: list[ReviewResult] = []
        new_labels: list[ReceiptWordLabel] = []

        for issue, result in zip(state.issues_found, llm_results, strict=True):
            # Validate suggested_label is from CORE_LABELS
            suggested_label = result.get("suggested_label")
            if suggested_label and suggested_label not in CORE_LABELS:
                logger.warning(
                    "LLM suggested label '%s' not in CORE_LABELS, ignoring",
                    suggested_label,
                )
                suggested_label = None

            review_result = ReviewResult(
                issue=issue,
                decision=result.get("decision", "NEEDS_REVIEW"),
                reasoning=result.get("reasoning", "No reasoning provided"),
                suggested_label=suggested_label,
            )
            review_results.append(review_result)

            # Create label based on review result
            label = _create_evaluation_label(issue, review_result)
            new_labels.append(label)

            logger.info(
                "Reviewed '%s': %s - %s...",
                issue.word.text,
                review_result.decision,
                result.get("reasoning", "")[:50],
            )

        logger.info("LLM reviewed %s issues", len(review_results))

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
            logger.info(
                "Wrote %s evaluation labels to DynamoDB",
                len(state.new_labels),
            )
        except Exception as e:
            logger.error("Error writing evaluation results: %s", e)
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
        review_result: Optional LLM review result (if None, uses evaluator
            result)

    Returns:
        New ReceiptWordLabel with validation status and reasoning
    """
    if review_result:
        # Use LLM review result
        if (
            review_result.decision == "INVALID"
            and review_result.suggested_label
        ):
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
        timestamp_added=datetime.now(timezone.utc),
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
        skip_llm_review: If True, skip LLM review and use evaluator results
            directly

    Returns:
        Evaluation result dict with issues found, reviews, and labels written
    """
    initial_state = EvaluatorState(
        image_id=image_id,
        receipt_id=receipt_id,
        skip_llm_review=skip_llm_review,
    )

    logger.info("Starting label evaluation for %s#%s", image_id, receipt_id)

    try:
        config = {
            "recursion_limit": 10,
            "configurable": {"thread_id": f"{image_id}#{receipt_id}"},
        }

        final_state = await graph.ainvoke(initial_state, config=config)

        # LangGraph returns a dict, access fields as dict keys
        issues_found = final_state.get("issues_found", [])
        new_labels = final_state.get("new_labels", [])
        review_results = final_state.get("review_results", [])
        merchant_patterns = final_state.get("merchant_patterns")
        error = final_state.get("error")

        result = {
            "image_id": image_id,
            "receipt_id": receipt_id,
            "issues_found": len(issues_found),
            "labels_written": len(new_labels),
            "issues": [
                {
                    "type": issue.issue_type,
                    "word_text": issue.word.text,
                    "current_label": issue.current_label,
                    "suggested_status": issue.suggested_status,
                    "suggested_label": issue.suggested_label,
                    "reasoning": issue.reasoning,
                }
                for issue in issues_found
            ],
            "reviews": [
                {
                    "word_text": review.issue.word.text,
                    "decision": review.decision,
                    "reasoning": review.reasoning,
                    "suggested_label": review.suggested_label,
                    "review_completed": review.review_completed,
                }
                for review in review_results
            ],
            "error": error,
        }

        if merchant_patterns:
            result["merchant_receipts_analyzed"] = (
                merchant_patterns.receipt_count
            )

        logger.info(
            "Evaluation complete: %s issues, %s reviewed, %s labels written",
            result["issues_found"],
            len(review_results),
            result["labels_written"],
        )

        return result

    except Exception as e:
        logger.error("Error in label evaluation: %s", e)
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
        skip_llm_review: If True, skip LLM review and use evaluator results
            directly

    Returns:
        Evaluation result dict
    """
    import asyncio

    return asyncio.run(
        run_label_evaluator(graph, image_id, receipt_id, skip_llm_review)
    )


# =============================================================================
# Compute-Only Graph for Step Functions / Lambda
# =============================================================================
#
# This graph skips I/O operations (DynamoDB fetch) and expects pre-loaded
# state.
# Use this when:
# - Running in AWS Step Functions with Distributed Map
# - Data is fetched in parallel by separate Lambdas and passed via S3/state
# - You want to benchmark pure computation without I/O overhead
#
# Required pre-loaded state fields:
# - words: List[ReceiptWord] - target receipt words
# - labels: List[ReceiptWordLabel] - target receipt labels
# - other_receipt_data: List[OtherReceiptData] - training receipts from same
#   merchant
# - place: Optional[ReceiptPlace] - for merchant name (optional)
#


def create_compute_only_graph(
    max_pair_patterns: int = 4,
    max_relationship_dimension: int = 2,
) -> Any:
    """
    Create a compute-only label evaluator graph for Lambda/Step Functions.

    This graph expects pre-loaded state and performs only pure computation:
    1. validate_state: Check that required data is present
    2. build_spatial_context: Build word contexts and visual lines
    3. compute_patterns: Compute merchant patterns from training data
    4. evaluate_labels: Apply validation rules and detect issues

    No DynamoDB fetches, no LLM calls, no writes. Perfect for:
    - AWS Lambda with data passed via S3
    - Step Functions Distributed Map pattern
    - Performance benchmarking of pure Python computation

    Args:
        max_pair_patterns: Maximum label pairs/tuples to compute geometry for
            (default: 4)
        max_relationship_dimension: Analyze n-label relationships (default: 2)
                                   2=pairs, 3=triples, 4+=higher-order

    Returns:
        Compiled LangGraph workflow

    Example usage in Lambda:
        ```python
        # In Lambda handler
        import json
        from receipt_agent.agents.label_evaluator import (
            create_compute_only_graph,
            run_compute_only,
        )
        from receipt_agent.agents.label_evaluator.state import (
            EvaluatorState,
            OtherReceiptData,
        )

        def handler(event, context):
            # Load pre-fetched data from S3 or event
            s3_data = load_from_s3(event["data_key"])

            # Create pre-loaded state
            initial_state = EvaluatorState(
                image_id=event["image_id"],
                receipt_id=event["receipt_id"],
                words=deserialize_words(s3_data["target_words"]),
                labels=deserialize_labels(s3_data["target_labels"]),
                place=deserialize_place(s3_data.get("place")),
                other_receipt_data=[
                    OtherReceiptData(
                        place=deserialize_place(r["place"]),
                        words=deserialize_words(r["words"]),
                        labels=deserialize_labels(r["labels"]),
                    )
                    for r in s3_data["training_receipts"]
                ],
                skip_llm_review=True,
            )

            graph = create_compute_only_graph()
            result = run_compute_only(graph, initial_state)
            return result
        ```
    """
    _max_pair_patterns = max_pair_patterns
    _max_relationship_dimension = max_relationship_dimension

    def validate_state(state: EvaluatorState) -> dict:
        """Validate that required pre-loaded data is present."""
        errors = []

        if not state.words:
            errors.append("words: required but empty or missing")
        if not state.labels:
            # Labels can be empty for unlabeled receipts, but log warning
            logger.warning(
                "No labels for receipt %s#%s",
                state.image_id,
                state.receipt_id,
            )

        # other_receipt_data can be empty (no merchant match)
        if not state.other_receipt_data:
            logger.info(
                "No other_receipt_data provided - pattern learning disabled"
            )

        if errors:
            error_msg = f"Validation failed: {'; '.join(errors)}"
            logger.error(error_msg)
            return {"error": error_msg}

        logger.info(
            "Validated state: %s words, %s labels, %s training receipts",
            len(state.words),
            len(state.labels),
            len(state.other_receipt_data),
        )
        return {}

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
            "Built %s word contexts in %s visual lines",
            len(word_contexts),
            len(visual_lines),
        )

        return {
            "word_contexts": word_contexts,
            "visual_lines": visual_lines,
        }

    def compute_patterns(state: EvaluatorState) -> dict:
        """Compute merchant patterns from other receipts."""
        logger.info(
            "compute_patterns: entered, error=%s, merchant_patterns=%s, "
            "other_receipt_data=%s",
            state.error,
            state.merchant_patterns is not None,
            len(state.other_receipt_data) if state.other_receipt_data else 0,
        )
        if state.error:
            return {}

        # Use pre-computed patterns if already provided
        if state.merchant_patterns:
            logger.info(
                "Using pre-computed patterns (%s receipts)",
                state.merchant_patterns.receipt_count,
            )
            return {}  # Don't overwrite existing patterns

        if not state.other_receipt_data:
            logger.info(
                "compute_patterns: no other_receipt_data, returning None"
            )
            return {"merchant_patterns": None}

        merchant_name = "Unknown"
        if state.place:
            merchant_name = state.place.merchant_name or "Unknown"

        patterns = compute_merchant_patterns(
            state.other_receipt_data,
            merchant_name,
            max_pair_patterns=_max_pair_patterns,
            max_relationship_dimension=_max_relationship_dimension,
        )

        if patterns:
            logger.info(
                "Computed patterns from %s receipts: %s label types",
                patterns.receipt_count,
                len(patterns.label_positions),
            )

        return {"merchant_patterns": patterns}

    def evaluate_labels(state: EvaluatorState) -> dict:
        """Apply validation rules and detect issues."""
        logger.info(
            "evaluate_labels: entered, error=%s, word_contexts=%s, "
            "merchant_patterns=%s",
            state.error,
            len(state.word_contexts) if state.word_contexts else 0,
            state.merchant_patterns is not None,
        )
        if state.error:
            return {}

        if not state.word_contexts:
            return {"issues_found": []}

        logger.info("evaluate_labels: starting evaluation...")
        # Evaluate all word contexts against validation rules
        issues = evaluate_word_contexts(
            state.word_contexts,
            state.merchant_patterns,
            state.visual_lines,
            # Pass visual_lines for on-demand same-line lookup
        )

        logger.info("Found %s issues", len(issues))

        return {"issues_found": issues}

    # Build the compute-only graph
    workflow = StateGraph(EvaluatorState)

    # Add nodes (no fetch, no LLM, no write)
    workflow.add_node("validate_state", validate_state)
    workflow.add_node("build_spatial_context", build_spatial_context)
    workflow.add_node("compute_patterns", compute_patterns)
    workflow.add_node("evaluate_labels", evaluate_labels)

    # Linear flow
    workflow.add_edge("validate_state", "build_spatial_context")
    workflow.add_edge("build_spatial_context", "compute_patterns")
    workflow.add_edge("compute_patterns", "evaluate_labels")
    workflow.add_edge("evaluate_labels", END)

    workflow.set_entry_point("validate_state")

    return workflow.compile()


async def run_compute_only(
    graph: Any,
    state: EvaluatorState,
    config: dict | None = None,
) -> dict:
    """
    Run the compute-only label evaluator with pre-loaded state.

    Args:
        graph: Compiled compute-only workflow graph
        state: Pre-populated EvaluatorState with words, labels,
            other_receipt_data
        config: Optional LangChain config dict (for callbacks/tracing)

    Returns:
        Evaluation result dict with issues found
    """
    logger.info(
        "Starting compute-only evaluation for %s#%s",
        state.image_id,
        state.receipt_id,
    )

    try:
        invoke_config = {
            "recursion_limit": 10,
            "configurable": {
                "thread_id": f"{state.image_id}#{state.receipt_id}"
            },
        }
        # Merge in provided config (e.g., callbacks for tracing)
        if config:
            invoke_config.update(config)

        final_state = await graph.ainvoke(state, config=invoke_config)

        # LangGraph returns a dict
        issues_found = final_state.get("issues_found", [])
        merchant_patterns = final_state.get("merchant_patterns")
        error = final_state.get("error")

        result = {
            "image_id": state.image_id,
            "receipt_id": state.receipt_id,
            "issues_found": len(issues_found),
            "issues": [
                {
                    "type": issue.issue_type,
                    "word_text": issue.word.text,
                    "word_id": issue.word.word_id,
                    "line_id": issue.word.line_id,
                    "current_label": issue.current_label,
                    "suggested_status": issue.suggested_status,
                    "suggested_label": issue.suggested_label,
                    "reasoning": issue.reasoning,
                }
                for issue in issues_found
            ],
            "error": error,
        }

        if merchant_patterns:
            result["merchant_receipts_analyzed"] = (
                merchant_patterns.receipt_count
            )
            result["label_types_found"] = len(
                merchant_patterns.label_positions
            )

        logger.info(
            "Compute-only evaluation complete: %s issues",
            result["issues_found"],
        )

        return result

    except Exception as e:
        logger.error("Error in compute-only evaluation: %s", e)
        return {
            "image_id": state.image_id,
            "receipt_id": state.receipt_id,
            "issues_found": 0,
            "issues": [],
            "error": str(e),
        }


def run_compute_only_sync(
    graph: Any,
    state: EvaluatorState,
    config: dict | None = None,
) -> dict:
    """
    Synchronous wrapper for run_compute_only.

    Args:
        graph: Compiled compute-only workflow graph
        state: Pre-populated EvaluatorState
        config: Optional LangChain config dict (for callbacks/tracing)

    Returns:
        Evaluation result dict
    """
    import asyncio

    return asyncio.run(run_compute_only(graph, state, config=config))
