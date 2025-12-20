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
from typing import Any, Dict, List, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph

# Optional: langchain_ollama for Ollama Cloud
try:
    from langchain_ollama import ChatOllama

    HAS_OLLAMA = True
except ImportError:
    HAS_OLLAMA = False
    ChatOllama = None  # type: ignore

from receipt_dynamo.constants import ValidationStatus
from receipt_dynamo.entities import ReceiptWordLabel

from receipt_agent.agents.label_evaluator.helpers import (
    assemble_visual_lines,
    build_review_context,
    build_word_contexts,
    compute_merchant_patterns,
    evaluate_word_contexts,
    format_similar_words_for_prompt,
    query_similar_validated_words,
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
        # Payment-related labels (added 2025-12-18)
        # These were missing from original schema and caused mislabeling in training data.
        # When these values appeared on receipts, they were incorrectly labeled as LINE_TOTAL.
        "CHANGE": "Change amount returned to the customer after transaction.",
        "CASH_BACK": "Cash back amount dispensed from purchase.",
        "REFUND": "Refund amount (full or partial return).",
    }

logger = logging.getLogger(__name__)

# Maximum number of other receipts to fetch for pattern learning
# Pattern computation itself is fast (<0.1s for 150 receipts).
# The limit exists to control DynamoDB I/O time during sequential fetching.
# In Step Functions, use Distributed Map to parallelize fetches and increase this.
# Can be overridden per-run via max_receipts parameter.
MAX_OTHER_RECEIPTS = 10

# Cache for merchant patterns (merchant_name -> MerchantPatterns)
# Speeds up repeated runs on the same merchant
_PATTERN_CACHE: Dict[str, Optional[Any]] = {}

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

## Similar Words from Database

These are semantically similar words from other receipts with their validated labels:

{similar_words}

IMPORTANT: Use these examples to understand what labels are typically assigned to similar text.
If the similar words show that text like "{word_text}" typically has NO label or a DIFFERENT label,
that's strong evidence the current label may be wrong.

## Label History

{label_history}

## Your Task

Decide if the current label is correct:

- **VALID**: The label IS correct despite the flag (false positive from evaluator)
  - Example: "CHARGE" labeled PAYMENT_METHOD is valid if it's part of "CREDIT CARD CHARGE"

- **INVALID**: The label IS wrong - provide the correct label from CORE_LABELS, or null if no label applies
  - Example: "Sprouts" in product area should be PRODUCT_NAME, not MERCHANT_NAME
  - Example: "Tip:" is a descriptor, not a value - it should have no label (suggested_label: null)

- **NEEDS_REVIEW**: Genuinely ambiguous, needs human review

Respond with ONLY a JSON object (no markdown, no explanation outside JSON):
{{"decision": "VALID|INVALID|NEEDS_REVIEW", "reasoning": "your explanation", "suggested_label": "LABEL_NAME or null"}}"""


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
        llm: Optional pre-configured LLM instance. If provided, ignores other LLM settings.
        ollama_base_url: Base URL for Ollama Cloud API (default: https://ollama.com)
        ollama_api_key: API key for Ollama Cloud (required for cloud usage)
        chroma_client: Optional ChromaDB client for similar word lookup. Words' existing
                       embeddings are retrieved by ID, so no embed_fn is needed.
        max_pair_patterns: Maximum label pairs/tuples to compute geometry for (default: 4)
                           Higher = more comprehensive analysis but slower
        max_relationship_dimension: Analyze n-label relationships (default: 2)
                                   2=pairs, 3=triples, 4+=higher-order

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
        client_kwargs = {"timeout": 120}
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
        logger.info(f"Using Ollama LLM: {llm_model} at {ollama_base_url}")
    else:
        _llm = None
        logger.warning(
            "langchain_ollama not installed. LLM review will be skipped. "
            "Install with: pip install langchain-ollama"
        )

    def fetch_receipt_data(state: EvaluatorState) -> dict:
        """Fetch words, labels, and place data for the receipt being evaluated."""
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
                    f"Could not fetch place data for receipt "
                    f"{state.image_id}#{state.receipt_id}"
                )

            logger.info(
                f"Fetched {len(words)} words and {len(labels)} labels for "
                f"receipt {state.image_id}#{state.receipt_id}"
            )

            return {
                "words": words,
                "labels": labels,
                "place": place,
            }

        except Exception as e:
            logger.error(f"Error fetching receipt data: {e}")
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
            other_places, _ = (
                _dynamo_client.get_receipt_places_by_merchant(
                    merchant_name,
                    limit=limit,  # None means fetch all available
                )
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
                    f"No other receipts found for merchant '{merchant_name}'"
                )
                return {"other_receipt_data": []}

            logger.info(
                f"Found {len(other_places)} other receipts for merchant "
                f"'{merchant_name}'"
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
                        f"Could not fetch data for receipt "
                        f"{other_place.image_id}#{other_place.receipt_id}: {e}"
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
            state.place.merchant_name
            if state.place
            else "Unknown"
        )

        patterns = compute_merchant_patterns(
            state.other_receipt_data,
            merchant_name,
            max_pair_patterns=_max_pair_patterns,
            max_relationship_dimension=_max_relationship_dimension,
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

        # Skip LLM review if LLM is not available
        if _llm is None:
            logger.warning(
                "LLM not available, using evaluator results directly"
            )
            new_labels = []
            for issue in state.issues_found:
                label = _create_evaluation_label(issue, None)
                new_labels.append(label)
            return {"review_results": [], "new_labels": new_labels}

        merchant_name = "Unknown"
        if state.place:
            merchant_name = state.place.merchant_name or "Unknown"

        review_results: List[ReviewResult] = []
        new_labels: List[ReceiptWordLabel] = []

        for issue in state.issues_found:
            try:
                # Build context for this issue
                review_ctx = build_review_context(
                    issue, state.visual_lines, merchant_name
                )

                # Query ChromaDB for similar validated words (scoped to same merchant)
                similar_words_text = (
                    "ChromaDB not configured - no similar words available."
                )
                if _chroma_client:
                    try:
                        similar_words = query_similar_validated_words(
                            word=issue.word,
                            chroma_client=_chroma_client,
                            n_results=10,
                            min_similarity=0.6,
                            merchant_name=merchant_name,
                        )
                        similar_words_text = format_similar_words_for_prompt(
                            similar_words, max_examples=5
                        )
                        logger.debug(
                            f"Found {len(similar_words)} similar words for '{issue.word.text}'"
                        )
                    except Exception as e:
                        logger.warning(f"Error querying similar words: {e}")
                        similar_words_text = (
                            f"Error querying similar words: {e}"
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
                    similar_words=similar_words_text,
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
                    reasoning = result_json.get(
                        "reasoning", "No reasoning provided"
                    )
                    suggested_label = result_json.get("suggested_label")

                    # Validate decision
                    if decision not in ("VALID", "INVALID", "NEEDS_REVIEW"):
                        decision = "NEEDS_REVIEW"

                    # Validate suggested_label is from CORE_LABELS (or None)
                    if suggested_label and suggested_label not in CORE_LABELS:
                        logger.warning(
                            f"LLM suggested label '{suggested_label}' not in CORE_LABELS, "
                            f"ignoring suggestion"
                        )
                        suggested_label = None

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
                logger.error(
                    f"Error reviewing issue for '{issue.word.text}': {e}"
                )
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
            logger.info(
                f"Wrote {len(state.new_labels)} evaluation labels to DynamoDB"
            )
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
            f"Evaluation complete: {result['issues_found']} issues, "
            f"{len(review_results)} reviewed, "
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


# =============================================================================
# Compute-Only Graph for Step Functions / Lambda
# =============================================================================
#
# This graph skips I/O operations (DynamoDB fetch) and expects pre-loaded state.
# Use this when:
# - Running in AWS Step Functions with Distributed Map
# - Data is fetched in parallel by separate Lambdas and passed via S3/state
# - You want to benchmark pure computation without I/O overhead
#
# Required pre-loaded state fields:
# - words: List[ReceiptWord] - target receipt words
# - labels: List[ReceiptWordLabel] - target receipt labels
# - other_receipt_data: List[OtherReceiptData] - training receipts from same merchant
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
        max_pair_patterns: Maximum label pairs/tuples to compute geometry for (default: 4)
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
                f"No labels for receipt {state.image_id}#{state.receipt_id}"
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
            f"Validated state: {len(state.words)} words, "
            f"{len(state.labels)} labels, "
            f"{len(state.other_receipt_data)} training receipts"
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
) -> dict:
    """
    Run the compute-only label evaluator with pre-loaded state.

    Args:
        graph: Compiled compute-only workflow graph
        state: Pre-populated EvaluatorState with words, labels, other_receipt_data

    Returns:
        Evaluation result dict with issues found
    """
    logger.info(
        f"Starting compute-only evaluation for "
        f"{state.image_id}#{state.receipt_id}"
    )

    try:
        config = {
            "recursion_limit": 10,
            "configurable": {
                "thread_id": f"{state.image_id}#{state.receipt_id}"
            },
        }

        final_state = await graph.ainvoke(state, config=config)

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
            f"Compute-only evaluation complete: {result['issues_found']} issues"
        )

        return result

    except Exception as e:
        logger.error(f"Error in compute-only evaluation: {e}")
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
) -> dict:
    """
    Synchronous wrapper for run_compute_only.

    Args:
        graph: Compiled compute-only workflow graph
        state: Pre-populated EvaluatorState

    Returns:
        Evaluation result dict
    """
    import asyncio

    return asyncio.run(run_compute_only(graph, state))
