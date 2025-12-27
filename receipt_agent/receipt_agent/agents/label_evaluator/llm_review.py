"""
LLM Review Module for Label Evaluator

Core logic for LLM-based review of receipt labeling issues.
Moved from infra/label_evaluator_step_functions/lambdas/llm_review.py.

This module provides functions that can be used by both:
- The LangGraph-based label evaluator
- The Lambda-based Step Function workflow

Also includes text assembly and review context building functions
(consolidated from helpers.py).
"""

import logging
import re
from collections import defaultdict
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Optional

from langchain_core.messages import HumanMessage
from receipt_dynamo import ReceiptWordLabel
from receipt_dynamo.entities import ReceiptWord

from receipt_agent.prompts.label_evaluator import (
    build_batched_review_prompt,
    build_receipt_context_prompt,
    build_review_prompt,
    parse_batched_llm_response,
    parse_llm_response,
)
from receipt_agent.utils.chroma_helpers import (
    SimilarWordEvidence,
    compute_label_distribution,
    compute_merchant_breakdown,
    compute_similarity_distribution,
    enrich_evidence_with_dynamo_reasoning,
    query_similar_words,
)

from receipt_agent.constants import CURRENCY_LABELS

from .state import (
    EvaluationIssue,
    ReviewContext,
    VisualLine,
    WordContext,
)
from .word_context import get_same_line_words

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

    from receipt_agent.utils.ollama_rate_limit import RateLimitedLLMInvoker

logger = logging.getLogger(__name__)


# =============================================================================
# Receipt Text Assembly for LLM Review
# =============================================================================


def is_currency_amount(text: str) -> bool:
    """Check if text looks like a currency amount."""
    text = text.strip()
    # Match $X.XX, X.XX, or X.XX patterns (with optional $ and commas)
    return bool(re.match(r"^\$?\d{1,3}(,\d{3})*\.\d{2}$", text))


def parse_currency_value(text: str) -> Optional[float]:
    """Parse currency text to float value."""
    text = text.strip().replace("$", "").replace(",", "")
    try:
        return float(text)
    except ValueError:
        return None


def _group_words_into_ocr_lines(
    words: list[dict],
) -> list[dict]:
    """
    Group words by line_id into OCR lines with computed geometry.

    Returns list of line dicts with:
    - line_id: OCR line ID
    - words: list of words in this line (sorted by x)
    - centroid_y: average Y of word centroids
    - top_y: max top_left Y (top of line)
    - bottom_y: min bottom_left Y (bottom of line)
    - min_x: leftmost X
    """
    lines_by_id: dict[int, list[dict]] = defaultdict(list)
    for w in words:
        lines_by_id[w.get("line_id", 0)].append(w)

    ocr_lines = []
    for line_id, line_words in lines_by_id.items():
        # Sort words by X position
        line_words.sort(key=lambda w: w.get("top_left", {}).get("x", 0))

        # Compute geometry from word corners
        top_ys = []
        bottom_ys = []
        centroid_ys = []

        for w in line_words:
            tl = w.get("top_left", {})
            bl = w.get("bottom_left", {})
            if tl.get("y") is not None:
                top_ys.append(tl["y"])
            if bl.get("y") is not None:
                bottom_ys.append(bl["y"])
            # Centroid Y is average of top and bottom
            if tl.get("y") is not None and bl.get("y") is not None:
                centroid_ys.append((tl["y"] + bl["y"]) / 2)

        ocr_lines.append(
            {
                "line_id": line_id,
                "words": line_words,
                "centroid_y": (
                    sum(centroid_ys) / len(centroid_ys) if centroid_ys else 0
                ),
                "top_y": max(top_ys) if top_ys else 0,
                "bottom_y": min(bottom_ys) if bottom_ys else 0,
                "min_x": min(
                    w.get("top_left", {}).get("x", 0) for w in line_words
                ),
            }
        )

    return ocr_lines


def assemble_receipt_text(
    words: list[dict],
    labels: list[dict],
    highlight_words: Optional[list[tuple[int, int]]] = None,
    max_lines: int = 60,
) -> str:
    """
    Reassemble receipt text in reading order with labels.

    Uses the same logic as format_receipt_text_receipt_space:
    - Sort OCR lines by centroid Y descending (top first, Y=0 is bottom)
    - Merge lines whose centroid falls within previous line's vertical span
    - Sort words within each visual line by X (left to right)

    Args:
        words: List of word dicts with text, line_id, word_id, corners
        labels: List of label dicts with line_id, word_id, label, validation_status
        highlight_words: Optional list of (line_id, word_id) tuples to mark with []
        max_lines: Maximum visual lines to include (truncate middle if needed)

    Returns:
        Receipt text in reading order, with labels shown inline
    """
    if words is None:
        logger.warning("assemble_receipt_text: words is None")
        return "(empty receipt - words is None)"
    if not isinstance(words, list):
        logger.warning(
            "assemble_receipt_text: words is %s, not list",
            type(words).__name__,
        )
        return f"(invalid receipt - words is {type(words).__name__})"
    if not words:
        return "(empty receipt)"

    # Build label lookup: (line_id, word_id) -> label info
    label_map: dict[tuple[int, int], dict] = {}
    for lbl in labels:
        key = (lbl.get("line_id"), lbl.get("word_id"))
        # Keep only VALID labels, or the most recent if no VALID
        if key not in label_map or lbl.get("validation_status") == "VALID":
            label_map[key] = lbl

    # Build highlight set
    highlight_set = set(highlight_words) if highlight_words else set()

    # Group words into OCR lines with geometry
    ocr_lines = _group_words_into_ocr_lines(words)
    if not ocr_lines:
        return "(empty receipt)"

    # Sort by centroid Y descending (top first, since Y=0 is bottom)
    ocr_lines.sort(key=lambda line: -line["centroid_y"])

    # Merge OCR lines into visual lines using vertical span logic
    visual_lines: list[dict] = []

    for ocr_line in ocr_lines:
        if visual_lines:
            prev = visual_lines[-1]
            centroid_y = ocr_line["centroid_y"]
            # Check if this line's centroid falls within prev visual line's span
            if prev["bottom_y"] < centroid_y < prev["top_y"]:
                # Merge with previous visual line
                prev["words"].extend(ocr_line["words"])
                # Update visual line geometry to encompass both
                prev["top_y"] = max(prev["top_y"], ocr_line["top_y"])
                prev["bottom_y"] = min(prev["bottom_y"], ocr_line["bottom_y"])
                continue

        # Start new visual line with this OCR line's geometry
        visual_lines.append(
            {
                "words": list(ocr_line["words"]),
                "top_y": ocr_line["top_y"],
                "bottom_y": ocr_line["bottom_y"],
            }
        )

    # Sort words within each visual line by X (left to right)
    for vl in visual_lines:
        vl["words"].sort(key=lambda w: w.get("top_left", {}).get("x", 0))

    # Truncate if too many lines (keep top and bottom, skip middle)
    if len(visual_lines) > max_lines:
        keep_top = max_lines // 2
        keep_bottom = max_lines - keep_top - 1
        omitted = len(visual_lines) - max_lines + 1
        placeholder = {
            "words": [
                {
                    "text": f"... ({omitted} lines omitted) ...",
                    "line_id": -1,
                    "word_id": -1,
                }
            ],
            "top_y": 0,
            "bottom_y": 0,
        }
        visual_lines = (
            visual_lines[:keep_top]
            + [placeholder]
            + visual_lines[-keep_bottom:]
        )

    # Format each visual line with labels
    formatted_lines = []
    for vl in visual_lines:
        line_words = vl["words"]
        line_parts = []
        line_labels = []

        for w in line_words:
            text = w.get("text", "")
            key = (w.get("line_id"), w.get("word_id"))

            # Mark highlighted words
            if key in highlight_set:
                text = f"[{text}]"

            line_parts.append(text)

            # Collect label if exists
            if key in label_map:
                lbl = label_map[key]
                label_name = lbl.get("label", "?")
                status = lbl.get("validation_status", "")
                if status == "INVALID":
                    line_labels.append(f"~~{label_name}~~")
                else:
                    line_labels.append(label_name)

        line_text = " ".join(line_parts)
        if line_labels:
            line_text += f"  ({', '.join(line_labels)})"

        formatted_lines.append(line_text)

    return "\n".join(formatted_lines)


def extract_receipt_currency_context(
    words: list[dict],
    labels: list[dict],
) -> list[dict]:
    """
    Extract all currency amounts from a receipt with their labels and context.

    Returns a list of dicts with:
    - amount: the numeric value
    - text: original text
    - label: current label (or None)
    - line_id: line number
    - word_id: word number
    - context: surrounding words on the same line
    """
    # Build label lookup
    label_map: dict[tuple[int, int], dict] = {}
    for lbl in labels:
        key = (lbl.get("line_id"), lbl.get("word_id"))
        label_map[key] = lbl

    # Group words by line for context
    lines: dict[int, list[dict]] = {}
    for word in words:
        line_id = word.get("line_id")
        if line_id not in lines:
            lines[line_id] = []
        lines[line_id].append(word)

    # Sort words within each line by x position
    for line_id in lines:
        lines[line_id].sort(
            key=lambda w: w.get("bounding_box", {}).get("x", 0)
        )

    # Extract currency amounts
    currency_items: list[dict] = []
    for word in words:
        text = word.get("text", "")
        if not is_currency_amount(text):
            continue

        amount = parse_currency_value(text)
        if amount is None:
            continue

        line_id = word.get("line_id")
        word_id = word.get("word_id")
        label_info = label_map.get((line_id, word_id))

        # Build context from same line
        line_words = lines.get(line_id, [])
        word_idx = next(
            (
                i
                for i, w in enumerate(line_words)
                if w.get("word_id") == word_id
            ),
            -1,
        )

        # Get words before this one on the same line
        context_before = []
        if word_idx > 0:
            for w in line_words[max(0, word_idx - 3) : word_idx]:
                context_before.append(w.get("text", ""))

        context = (
            " ".join(context_before) if context_before else "(start of line)"
        )

        currency_items.append(
            {
                "amount": amount,
                "text": text,
                "label": label_info.get("label") if label_info else None,
                "validation_status": (
                    label_info.get("validation_status") if label_info else None
                ),
                "line_id": line_id,
                "word_id": word_id,
                "context": context,
                "y_position": word.get("bounding_box", {}).get("y", 0.5),
            }
        )

    # Sort by position on receipt (top to bottom)
    currency_items.sort(key=lambda x: -x["y_position"])

    return currency_items


# =============================================================================
# Review Context Building Functions
# =============================================================================


def format_receipt_text(
    visual_lines: list[VisualLine],
    target_word: Optional[ReceiptWord] = None,
) -> str:
    """
    Format receipt as readable text with optional target word marked.

    Args:
        visual_lines: Visual lines from assemble_visual_lines()
        target_word: Optional word to mark with [brackets]

    Returns:
        Receipt text in reading order, one line per visual line
    """
    lines = []
    for visual_line in visual_lines:
        line_parts = []
        for word_ctx in visual_line.words:
            word_text = word_ctx.word.text
            # Mark target word with brackets
            if target_word and (
                word_ctx.word.line_id == target_word.line_id
                and word_ctx.word.word_id == target_word.word_id
            ):
                word_text = f"[{word_text}]"
            line_parts.append(word_text)
        lines.append(" ".join(line_parts))
    return "\n".join(lines)


def get_visual_line_text(
    issue: EvaluationIssue,
    visual_lines: list[VisualLine],
) -> str:
    """
    Get the text of the visual line containing the issue word.

    Args:
        issue: EvaluationIssue with word_context
        visual_lines: All visual lines for on-demand same-line lookup

    Returns:
        Visual line text as a string
    """
    if not issue.word_context:
        return issue.word.text

    # Get words on same line including this word (computed on-demand)
    same_line_words = get_same_line_words(issue.word_context, visual_lines)
    same_line = [issue.word_context] + same_line_words
    # Sort by x position
    same_line.sort(key=lambda c: c.normalized_x)
    return " ".join(c.word.text for c in same_line)


def get_visual_line_labels(
    issue: EvaluationIssue,
    visual_lines: list[VisualLine],
) -> list[str]:
    """
    Get the labels of other words on the same visual line.

    Args:
        issue: EvaluationIssue with word_context
        visual_lines: All visual lines for on-demand same-line lookup

    Returns:
        List of labels (excluding the target word)
    """
    if not issue.word_context:
        return []

    # Get same-line words on-demand
    same_line_words = get_same_line_words(issue.word_context, visual_lines)
    labels = []
    for ctx in same_line_words:
        if ctx.current_label:
            labels.append(ctx.current_label.label)
        else:
            labels.append("-")
    return labels


def format_label_history(word_context: Optional[WordContext]) -> list[dict]:
    """
    Format label history for a word as a list of dicts.

    Args:
        word_context: WordContext with label_history

    Returns:
        List of dicts with label, status, proposed_by, timestamp
    """
    if not word_context or not word_context.label_history:
        return []

    history = []
    for label in word_context.label_history:
        history.append(
            {
                "label": label.label,
                "status": label.validation_status,
                "proposed_by": label.label_proposed_by,
                "timestamp": (
                    label.timestamp_added.isoformat()
                    if hasattr(label.timestamp_added, "isoformat")
                    else str(label.timestamp_added)
                ),
            }
        )
    return history


def build_review_context(
    issue: EvaluationIssue,
    visual_lines: list[VisualLine],
    merchant_name: str,
) -> ReviewContext:
    """
    Build complete context for LLM review of an issue.

    Args:
        issue: The evaluation issue to review
        visual_lines: All visual lines from the receipt
        merchant_name: Name of the merchant

    Returns:
        ReviewContext with all information needed for LLM review
    """
    return ReviewContext(
        word_text=issue.word.text,
        current_label=issue.current_label,
        issue_type=issue.issue_type,
        evaluator_reasoning=issue.reasoning,
        receipt_text=format_receipt_text(visual_lines, target_word=issue.word),
        visual_line_text=get_visual_line_text(issue, visual_lines),
        visual_line_labels=get_visual_line_labels(issue, visual_lines),
        label_history=format_label_history(issue.word_context),
        merchant_name=merchant_name,
    )


# =============================================================================
# Constants and Configuration
# =============================================================================

# Default batch size for LLM calls
DEFAULT_ISSUES_PER_LLM_CALL = 10


# =============================================================================
# Core Review Functions
# =============================================================================


def gather_evidence_for_issue(
    issue: dict[str, Any],
    merchant_name: str,
    chroma_client: Optional[Any] = None,
    dynamo_client: Optional[Any] = None,
    n_similar_results: int = 100,
) -> dict[str, Any]:
    """
    Gather all evidence needed for LLM review of a single issue.

    Args:
        issue: The issue dict with word_text, line_id, word_id, image_id, etc.
        merchant_name: Merchant name for same-merchant filtering
        chroma_client: Optional ChromaDB client for similar word queries
        dynamo_client: Optional DynamoDB client for reasoning enrichment
        n_similar_results: Number of similar words to query

    Returns:
        Dict with issue and all gathered evidence
    """
    evidence: dict[str, Any] = {
        "issue": issue,
        "similar_evidence": [],
        "similarity_dist": {"very_high": 0, "high": 0, "medium": 0, "low": 0},
        "label_dist": {},
        "merchant_breakdown": [],
        "currency_context": [],
    }

    if not chroma_client:
        return evidence

    # Query for similar words
    try:
        similar_evidence = query_similar_words(
            chroma_client=chroma_client,
            word_text=issue.get("word_text", ""),
            image_id=issue.get("image_id", ""),
            receipt_id=issue.get("receipt_id", 0),
            line_id=issue.get("line_id", 0),
            word_id=issue.get("word_id", 0),
            target_merchant=merchant_name,
            n_results=n_similar_results,
        )

        # Enrich with DynamoDB reasoning if available
        if dynamo_client and similar_evidence:
            similar_evidence = enrich_evidence_with_dynamo_reasoning(
                similar_evidence, dynamo_client, limit=20
            )

        evidence["similar_evidence"] = similar_evidence
        evidence["similarity_dist"] = compute_similarity_distribution(
            similar_evidence
        )
        evidence["label_dist"] = compute_label_distribution(similar_evidence)
        evidence["merchant_breakdown"] = compute_merchant_breakdown(
            similar_evidence
        )

    except Exception as e:
        logger.warning("Error gathering evidence for issue: %s", e)

    return evidence


def review_single_issue(
    issue: dict[str, Any],
    similar_evidence: list[SimilarWordEvidence],
    merchant_name: str,
    merchant_receipt_count: int,
    llm: "BaseChatModel",
    currency_context: Optional[list[dict]] = None,
    line_item_patterns: Optional[dict] = None,
    rate_limiter: Optional["RateLimitedLLMInvoker"] = None,
) -> dict[str, Any]:
    """
    Review a single issue with LLM.

    Args:
        issue: The issue dict
        similar_evidence: List of similar word evidence
        merchant_name: Merchant name
        merchant_receipt_count: Number of receipts for this merchant
        llm: LangChain chat model
        currency_context: Optional currency context from receipt
        line_item_patterns: Optional line item patterns for merchant
        rate_limiter: Optional rate limiter wrapper

    Returns:
        Dict with decision, reasoning, suggested_label, confidence
    """
    # Compute distributions from evidence
    similarity_dist = compute_similarity_distribution(similar_evidence)
    label_dist = compute_label_distribution(similar_evidence)
    merchant_breakdown = compute_merchant_breakdown(similar_evidence)

    # Build prompt
    prompt = build_review_prompt(
        issue=issue,
        similar_evidence=similar_evidence,
        similarity_dist=similarity_dist,
        label_dist=label_dist,
        merchant_breakdown=merchant_breakdown,
        merchant_name=merchant_name,
        merchant_receipt_count=merchant_receipt_count,
        currency_context=currency_context,
        line_item_patterns=line_item_patterns,
    )

    # Call LLM (with optional rate limiting)
    try:
        if rate_limiter:
            response = rate_limiter.invoke([HumanMessage(content=prompt)])
        else:
            response = llm.invoke([HumanMessage(content=prompt)])

        response_text = response.content.strip()
        return parse_llm_response(response_text)

    except Exception as e:
        # Check if this is a rate limit error that should trigger Step Function retry
        from receipt_agent.utils import OllamaRateLimitError, BothProvidersFailedError
        if isinstance(e, (OllamaRateLimitError, BothProvidersFailedError)):
            logger.error("LLM review rate limited, propagating for retry: %s", e)
            raise  # Let Step Function retry handle this

        logger.error("LLM review failed: %s", e)
        return {
            "decision": "NEEDS_REVIEW",
            "reasoning": f"LLM review failed: {e}",
            "suggested_label": None,
            "confidence": "low",
        }


def review_issues_batch(
    issues_with_context: list[dict[str, Any]],
    merchant_name: str,
    merchant_receipt_count: int,
    llm: "BaseChatModel",
    line_item_patterns: Optional[dict] = None,
    rate_limiter: Optional["RateLimitedLLMInvoker"] = None,
) -> list[dict[str, Any]]:
    """
    Review multiple issues in a single LLM call.

    Args:
        issues_with_context: List of dicts, each containing:
            - issue: The issue dict
            - similar_evidence: List of SimilarWordEvidence
            - similarity_dist: SimilarityDistribution (optional, will compute)
            - label_dist: Label distribution (optional, will compute)
            - merchant_breakdown: Merchant breakdown (optional, will compute)
            - currency_context: Currency amounts from receipt
        merchant_name: Merchant name
        merchant_receipt_count: Number of receipts for this merchant
        llm: LangChain chat model
        line_item_patterns: Optional line item patterns for merchant
        rate_limiter: Optional rate limiter wrapper

    Returns:
        List of dicts, one per issue, each with:
            decision, reasoning, suggested_label, confidence
    """
    if not issues_with_context:
        return []

    # Ensure each item has computed distributions without mutating inputs
    normalized_issues: list[dict[str, Any]] = []
    for item in issues_with_context:
        similar_evidence = item.get("similar_evidence", [])
        normalized = dict(item)
        if "similarity_dist" not in normalized:
            normalized["similarity_dist"] = compute_similarity_distribution(
                similar_evidence
            )
        if "label_dist" not in normalized:
            normalized["label_dist"] = compute_label_distribution(
                similar_evidence
            )
        if "merchant_breakdown" not in normalized:
            normalized["merchant_breakdown"] = compute_merchant_breakdown(
                similar_evidence
            )
        normalized_issues.append(normalized)

    # Build batched prompt
    prompt = build_batched_review_prompt(
        issues_with_context=normalized_issues,
        merchant_name=merchant_name,
        merchant_receipt_count=merchant_receipt_count,
        line_item_patterns=line_item_patterns,
    )

    # Call LLM
    try:
        if rate_limiter:
            response = rate_limiter.invoke([HumanMessage(content=prompt)])
        else:
            response = llm.invoke([HumanMessage(content=prompt)])

        response_text = response.content.strip()
        return parse_batched_llm_response(
            response_text, len(normalized_issues)
        )

    except Exception as e:
        # Check if this is a rate limit error that should trigger Step Function retry
        from receipt_agent.utils import OllamaRateLimitError, BothProvidersFailedError
        if isinstance(e, (OllamaRateLimitError, BothProvidersFailedError)):
            logger.error("Batched LLM review rate limited, propagating for retry: %s", e)
            raise  # Let Step Function retry handle this

        logger.error("Batched LLM review failed: %s", e)
        # Return fallback for all issues (non-rate-limit errors only)
        return [
            {
                "decision": "NEEDS_REVIEW",
                "reasoning": f"Batched LLM review failed: {e}",
                "suggested_label": None,
                "confidence": "low",
            }
            for _ in issues_with_context
        ]


def review_issues_with_receipt_context(
    receipt_words: list[dict],
    receipt_labels: list[dict],
    issues_with_context: list[dict[str, Any]],
    merchant_name: str,
    merchant_receipt_count: int,
    llm: "BaseChatModel",
    line_item_patterns: Optional[dict] = None,
    rate_limiter: Optional["RateLimitedLLMInvoker"] = None,
    max_lines: int = 50,
) -> list[dict[str, Any]]:
    """
    Review issues with full receipt context visible in prompt.

    This provides more context by showing the full receipt text with
    the issue words highlighted, then details each issue.

    Args:
        receipt_words: All words from the receipt (as dicts)
        receipt_labels: All labels for the receipt (as dicts)
        issues_with_context: List of issue dicts with similar_evidence
        merchant_name: Merchant name
        merchant_receipt_count: Number of receipts for this merchant
        llm: LangChain chat model
        line_item_patterns: Optional line item patterns
        rate_limiter: Optional rate limiter
        max_lines: Maximum receipt lines to show

    Returns:
        List of review decision dicts
    """
    if not issues_with_context:
        return []

    # Build list of highlight positions
    highlight_words = []
    for item in issues_with_context:
        issue = item.get("issue", {})
        line_id = issue.get("line_id")
        word_id = issue.get("word_id")
        if line_id is not None and word_id is not None:
            highlight_words.append((line_id, word_id))

    # Assemble receipt text with highlighted words
    receipt_text = assemble_receipt_text(
        words=receipt_words,
        labels=receipt_labels,
        highlight_words=highlight_words,
        max_lines=max_lines,
    )

    # Build prompt
    prompt = build_receipt_context_prompt(
        receipt_text=receipt_text,
        issues_with_context=issues_with_context,
        merchant_name=merchant_name,
        merchant_receipt_count=merchant_receipt_count,
        line_item_patterns=line_item_patterns,
    )

    # Call LLM
    try:
        if rate_limiter:
            response = rate_limiter.invoke([HumanMessage(content=prompt)])
        else:
            response = llm.invoke([HumanMessage(content=prompt)])

        response_text = response.content.strip()
        return parse_batched_llm_response(
            response_text, len(issues_with_context)
        )

    except Exception as e:
        # Check if this is a rate limit error that should trigger Step Function retry
        from receipt_agent.utils import OllamaRateLimitError, BothProvidersFailedError
        if isinstance(e, (OllamaRateLimitError, BothProvidersFailedError)):
            logger.error("Receipt context LLM review rate limited, propagating for retry: %s", e)
            raise  # Let Step Function retry handle this

        logger.error("Receipt context LLM review failed: %s", e)
        return [
            {
                "decision": "NEEDS_REVIEW",
                "reasoning": f"LLM review failed: {e}",
                "suggested_label": None,
                "confidence": "low",
            }
            for _ in issues_with_context
        ]


# =============================================================================
# High-Level Review Orchestration
# =============================================================================


def review_all_issues(
    issues: list[dict[str, Any]],
    receipt_words: list[dict],
    receipt_labels: list[dict],
    merchant_name: str,
    merchant_receipt_count: int,
    llm: "BaseChatModel",
    chroma_client: Optional[Any] = None,
    dynamo_client: Optional[Any] = None,
    rate_limiter: Optional["RateLimitedLLMInvoker"] = None,
    line_item_patterns: Optional[dict] = None,
    max_issues_per_call: int = DEFAULT_ISSUES_PER_LLM_CALL,
    use_receipt_context: bool = True,
) -> list[dict[str, Any]]:
    """
    Review all issues with batching and optional receipt context.

    This is the main entry point for LLM review. It:
    1. Gathers evidence for each issue
    2. Extracts currency context from the receipt
    3. Batches issues for efficient LLM calls
    4. Returns review decisions

    Args:
        issues: List of issue dicts to review
        receipt_words: All words from the receipt (as dicts)
        receipt_labels: All labels for the receipt (as dicts)
        merchant_name: Merchant name
        merchant_receipt_count: Number of receipts for this merchant
        llm: LangChain chat model
        chroma_client: Optional ChromaDB client
        dynamo_client: Optional DynamoDB client
        rate_limiter: Optional rate limiter
        line_item_patterns: Optional line item patterns
        max_issues_per_call: Maximum issues per LLM call
        use_receipt_context: Whether to include full receipt context

    Returns:
        List of dicts, one per issue, each containing:
            - issue: Original issue dict
            - decision: VALID, INVALID, or NEEDS_REVIEW
            - reasoning: LLM's reasoning
            - suggested_label: Suggested label if INVALID
            - confidence: low, medium, or high
    """
    if not issues:
        return []

    # Gather evidence for each issue
    logger.info("Gathering evidence for %d issues", len(issues))
    issues_with_context = []

    for issue in issues:
        evidence = gather_evidence_for_issue(
            issue=issue,
            merchant_name=merchant_name,
            chroma_client=chroma_client,
            dynamo_client=dynamo_client,
        )
        issues_with_context.append(evidence)

    # Extract currency context from receipt
    currency_context = extract_receipt_currency_context(
        receipt_words, receipt_labels
    )

    # Add currency context to each issue
    for item in issues_with_context:
        item["currency_context"] = currency_context

    # Review in batches
    all_results = []
    for i in range(0, len(issues_with_context), max_issues_per_call):
        batch = issues_with_context[i : i + max_issues_per_call]

        logger.info(
            "Reviewing batch %d-%d of %d issues",
            i,
            i + len(batch),
            len(issues_with_context),
        )

        if use_receipt_context:
            batch_results = review_issues_with_receipt_context(
                receipt_words=receipt_words,
                receipt_labels=receipt_labels,
                issues_with_context=batch,
                merchant_name=merchant_name,
                merchant_receipt_count=merchant_receipt_count,
                llm=llm,
                line_item_patterns=line_item_patterns,
                rate_limiter=rate_limiter,
            )
        else:
            batch_results = review_issues_batch(
                issues_with_context=batch,
                merchant_name=merchant_name,
                merchant_receipt_count=merchant_receipt_count,
                llm=llm,
                line_item_patterns=line_item_patterns,
                rate_limiter=rate_limiter,
            )

        # Combine issue with its review result
        for item, result in zip(batch, batch_results, strict=True):
            all_results.append(
                {
                    "issue": item["issue"],
                    "decision": result["decision"],
                    "reasoning": result["reasoning"],
                    "suggested_label": result.get("suggested_label"),
                    "confidence": result.get("confidence", "medium"),
                }
            )

    logger.info("Completed LLM review of %d issues", len(all_results))
    return all_results


# =============================================================================
# Apply Decisions to DynamoDB
# =============================================================================


def apply_llm_decisions(
    reviewed_issues: list[dict[str, Any]],
    dynamo_client: Any,
    execution_id: str,
) -> dict[str, int]:
    """
    Apply LLM review decisions to DynamoDB.

    For each reviewed issue:
    - VALID: Confirm the current label, invalidate conflicting labels
    - INVALID: Invalidate the current label, confirm/add suggested label
    - NEEDS_REVIEW: No changes

    Args:
        reviewed_issues: List of reviewed issue dicts with llm_review results.
            Each dict should have:
            - image_id: str
            - receipt_id: int
            - issue: dict with line_id, word_id, current_label
            - llm_review: dict with decision, reasoning, suggested_label, confidence
        dynamo_client: DynamoClient instance
        execution_id: Execution ID for audit trail

    Returns:
        Dict with counts of actions taken:
        - labels_confirmed: Labels marked as VALID
        - labels_invalidated: Labels marked as INVALID
        - labels_created: New labels created
        - conflicts_resolved: Conflicting labels invalidated
        - skipped_needs_review: Issues left for human review
        - errors: Number of errors encountered
    """
    stats = {
        "labels_confirmed": 0,
        "labels_invalidated": 0,
        "labels_created": 0,
        "conflicts_resolved": 0,
        "skipped_needs_review": 0,
        "errors": 0,
    }

    for item in reviewed_issues:
        try:
            image_id = item.get("image_id")
            receipt_id = item.get("receipt_id")
            issue = item.get("issue", {})
            llm_review = item.get("llm_review", {})

            line_id = issue.get("line_id")
            word_id = issue.get("word_id")
            current_label = issue.get("current_label")
            decision = llm_review.get("decision")
            reasoning = llm_review.get("reasoning", "")
            suggested_label = llm_review.get("suggested_label")
            confidence = llm_review.get("confidence", "medium")

            # Validate required fields
            # current_label can be None for unlabeled words if we have suggested_label
            has_required_fields = all([
                image_id,
                receipt_id is not None,
                line_id is not None,
                word_id is not None,
            ])
            has_label_info = current_label or (
                decision == "INVALID" and suggested_label
            )

            if not has_required_fields or not has_label_info:
                logger.warning("Missing required fields for issue: %s", item)
                stats["errors"] += 1
                continue
            assert isinstance(image_id, str)
            assert isinstance(receipt_id, int)
            assert isinstance(line_id, int)
            assert isinstance(word_id, int)

            # Build audit reasoning
            audit_reasoning = (
                f"[label-evaluator {execution_id}] "
                f"Decision: {decision} ({confidence}). {reasoning[:200]}"
            )
            timestamp = datetime.now(UTC).isoformat()

            if decision == "VALID":
                # 1. Confirm the current label (update reasoning if desired)
                # 2. Invalidate any conflicting labels on the same word

                # Get all labels for this word (returns tuple: list, last_key)
                all_labels, _ = (
                    dynamo_client.list_receipt_word_labels_for_word(
                        image_id, receipt_id, line_id, word_id
                    )
                )

                for label in all_labels:
                    if label.label == current_label:
                        # This is the confirmed label - already VALID, count it
                        stats["labels_confirmed"] += 1
                    elif label.validation_status == "VALID":
                        # Conflicting VALID label - invalidate it
                        updated_label = ReceiptWordLabel(
                            image_id=image_id,
                            receipt_id=receipt_id,
                            line_id=line_id,
                            word_id=word_id,
                            label=label.label,
                            reasoning=(
                                f"Invalidated: {current_label} confirmed. "
                                f"{audit_reasoning}"
                            ),
                            timestamp_added=label.timestamp_added,
                            validation_status="INVALID",
                            label_proposed_by=label.label_proposed_by,
                            label_consolidated_from=label.label_consolidated_from,
                        )
                        dynamo_client.update_receipt_word_label(updated_label)
                        stats["labels_invalidated"] += 1
                        stats["conflicts_resolved"] += 1
                        logger.info(
                            "Invalidated conflicting %s on %s:%s:%s:%s",
                            label.label,
                            image_id,
                            receipt_id,
                            line_id,
                            word_id,
                        )

            elif decision == "INVALID":
                # 1. Invalidate the current label (if exists)
                # 2. If suggested_label provided, confirm or create it

                # Get all labels for this word (returns tuple: list, last_key)
                all_labels, _ = (
                    dynamo_client.list_receipt_word_labels_for_word(
                        image_id, receipt_id, line_id, word_id
                    )
                )

                # Find and invalidate the current label (if it exists)
                if current_label:
                    for label in all_labels:
                        if label.label == current_label:
                            updated_label = ReceiptWordLabel(
                                image_id=image_id,
                                receipt_id=receipt_id,
                                line_id=line_id,
                                word_id=word_id,
                                label=label.label,
                                reasoning=audit_reasoning,
                                timestamp_added=label.timestamp_added,
                                validation_status="INVALID",
                                label_proposed_by=label.label_proposed_by,
                                label_consolidated_from=label.label_consolidated_from,
                            )
                            dynamo_client.update_receipt_word_label(updated_label)
                            stats["labels_invalidated"] += 1
                            logger.info(
                                "Invalidated %s on %s:%s:%s:%s",
                                current_label,
                                image_id,
                                receipt_id,
                                line_id,
                                word_id,
                            )
                            break

                # Handle suggested label
                if suggested_label:
                    # Check if suggested label already exists
                    existing_suggested = None
                    for label in all_labels:
                        if label.label == suggested_label:
                            existing_suggested = label
                            break

                    if existing_suggested:
                        # Confirm the existing suggested label
                        if existing_suggested.validation_status != "VALID":
                            updated_label = ReceiptWordLabel(
                                image_id=image_id,
                                receipt_id=receipt_id,
                                line_id=line_id,
                                word_id=word_id,
                                label=suggested_label,
                                reasoning=(
                                    f"Confirmed as replacement. {audit_reasoning}"
                                ),
                                timestamp_added=existing_suggested.timestamp_added,
                                validation_status="VALID",
                                label_proposed_by=existing_suggested.label_proposed_by,
                                label_consolidated_from=(
                                    existing_suggested.label_consolidated_from
                                ),
                            )
                            dynamo_client.update_receipt_word_label(
                                updated_label
                            )
                            stats["labels_confirmed"] += 1
                    else:
                        # Create new label
                        new_label = ReceiptWordLabel(
                            image_id=image_id,
                            receipt_id=receipt_id,
                            line_id=line_id,
                            word_id=word_id,
                            label=suggested_label,
                            reasoning=f"Added by LLM review. {audit_reasoning}",
                            timestamp_added=timestamp,
                            validation_status="VALID",
                            label_proposed_by="label-evaluator-llm",
                            label_consolidated_from=None,
                        )
                        dynamo_client.add_receipt_word_label(new_label)
                        stats["labels_created"] += 1
                        logger.info(
                            "Created new %s on %s:%s:%s:%s",
                            suggested_label,
                            image_id,
                            receipt_id,
                            line_id,
                            word_id,
                        )

            elif decision == "NEEDS_REVIEW":
                # No changes - leave for human review
                stats["skipped_needs_review"] += 1

        except Exception as e:
            logger.error("Error applying decision: %s", e)
            stats["errors"] += 1

    logger.info("Applied decisions: %s", stats)
    return stats
