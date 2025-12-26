"""
Helper functions for the Label Evaluator agent.

Provides spatial analysis utilities for grouping words into visual lines,
computing label patterns across receipts, and applying validation rules.
"""

# pylint: disable=import-outside-toplevel
# Optional imports (langchain_ollama) delayed until actually needed

import logging
import math
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any, Dict, List, Optional, Set, Tuple

from receipt_dynamo.entities import ReceiptWord, ReceiptWordLabel

from receipt_agent.agents.label_evaluator.state import (
    ConstellationGeometry,
    EvaluationIssue,
    GeometricRelationship,
    LabelPairGeometry,
    LabelRelativePosition,
    MerchantPatterns,
    OtherReceiptData,
    ReviewContext,
    VisualLine,
    WordContext,
)
from receipt_agent.utils.chroma_helpers import build_word_chroma_id

logger = logging.getLogger(__name__)


# NOTE: Constants (LABEL_GROUPS, LABEL_TO_GROUP, WITHIN_GROUP_PRIORITY_PAIRS,
# CROSS_GROUP_PRIORITY_PAIRS, CONFLICTING_LABEL_PAIRS) have been moved to constants.py
#
# Pattern computation functions (_calculate_angle_degrees, _calculate_distance,
# _angle_difference, _is_within_group_pair, _is_cross_group_pair, _select_top_label_pairs,
# _select_top_label_ntuples, _generate_label_ntuples, _convert_polar_to_cartesian,
# _print_pattern_statistics, _compute_patterns_for_subset, _compute_constellation_patterns,
# batch_receipts_by_quality, compute_merchant_patterns, detect_label_conflicts,
# classify_conflicts_with_llm, assign_batch_with_llm) have been moved to patterns.py


# NOTE: Issue detection functions (check_position_anomaly, check_unexpected_label_pair,
# check_geometric_anomaly, check_constellation_anomaly, check_text_label_conflict,
# check_missing_label_in_cluster, check_missing_constellation_member,
# evaluate_word_contexts) have been moved to issue_detection.py

# -----------------------------------------------------------------------------
# Review Context Building Functions
# -----------------------------------------------------------------------------


def format_receipt_text(
    visual_lines: List[VisualLine],
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
    visual_lines: List[VisualLine],
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
    visual_lines: List[VisualLine],
) -> List[str]:
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


def format_label_history(word_context: Optional[WordContext]) -> List[Dict]:
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
    visual_lines: List[VisualLine],
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


# -----------------------------------------------------------------------------
# ChromaDB Similar Words Query
# -----------------------------------------------------------------------------


@dataclass
class SimilarWordResult:
    """Result from ChromaDB similarity search for a word."""

    word_text: str
    similarity_score: float
    label: Optional[str]
    validation_status: Optional[str]
    valid_labels: List[str] = field(default_factory=list)
    invalid_labels: List[str] = field(default_factory=list)
    merchant_name: Optional[str] = None


def query_similar_validated_words(
    word: ReceiptWord,
    chroma_client: Any,
    n_results: int = 10,
    min_similarity: float = 0.7,
    merchant_name: Optional[str] = None,
) -> List[SimilarWordResult]:
    """
    Query ChromaDB for similar words that have validated labels.

    Uses the word's existing embedding from ChromaDB instead of generating
    a new one. This ensures we use the same contextual embedding that was
    created during the embedding pipeline.

    Args:
        word: The ReceiptWord to find similar words for
        chroma_client: ChromaDB client (DualChromaClient or similar)
        n_results: Maximum number of results to return
        min_similarity: Minimum similarity score (0.0-1.0) to include
        merchant_name: Optional merchant name to scope results to same merchant

    Returns:
        List of SimilarWordResult objects, sorted by similarity descending
    """
    if not chroma_client:
        logger.warning("ChromaDB client not provided")
        return []

    try:
        # Build the word's ChromaDB ID
        word_chroma_id = build_word_chroma_id(
            word.image_id, word.receipt_id, word.line_id, word.word_id
        )

        # Get the word's existing embedding from ChromaDB
        get_result = chroma_client.get(
            collection_name="words",
            ids=[word_chroma_id],
            include=["embeddings"],
        )

        if not get_result:
            logger.warning("Word not found in ChromaDB: %s", word_chroma_id)
            return []

        embeddings = get_result.get("embeddings")
        if embeddings is None or len(embeddings) == 0:
            logger.warning("No embeddings found for word: %s", word_chroma_id)
            return []

        if embeddings[0] is None:
            logger.warning("No embedding found for word: %s", word_chroma_id)
            return []

        # Convert numpy array to list
        try:
            query_embedding = list(embeddings[0])
        except (TypeError, ValueError):
            logger.warning(
                "Invalid embedding format for word: %s",
                word_chroma_id,
            )
            return []

        if not query_embedding:
            logger.warning(
                "Empty embedding found for word: %s",
                word_chroma_id,
            )
            return []

        # Query ChromaDB words collection using the existing embedding
        results = chroma_client.query(
            collection_name="words",
            query_embeddings=[query_embedding],
            n_results=n_results * 2
            + 1,  # Fetch more, filter later (+1 for self)
            include=["documents", "metadatas", "distances"],
        )

        if not results or not results.get("ids"):
            return []

        ids = list(results.get("ids", [[]])[0])
        documents = list(results.get("documents", [[]])[0])
        metadatas = list(results.get("metadatas", [[]])[0])
        distances = list(results.get("distances", [[]])[0])

        similar_words: List[SimilarWordResult] = []

        for doc_id, doc, meta, dist in zip(
            ids, documents, metadatas, distances
        ):
            # Skip self (the query word)
            if doc_id == word_chroma_id:
                continue

            # Filter by merchant if specified
            if merchant_name:
                result_merchant = meta.get("merchant_name")
                if result_merchant != merchant_name:
                    continue

            # Convert distance to Python float and compute similarity
            try:
                dist_float = float(dist)
            except (TypeError, ValueError):
                logger.debug("Invalid distance value: %s", dist)
                continue

            # Convert L2 distance to similarity (0.0-1.0)
            similarity = max(0.0, 1.0 - (dist_float / 2))

            if similarity < min_similarity:
                continue

            # Parse valid/invalid labels from metadata
            valid_labels_str = meta.get("valid_labels", "")
            invalid_labels_str = meta.get("invalid_labels", "")
            valid_labels = (
                [
                    lbl.strip()
                    for lbl in valid_labels_str.split(",")
                    if lbl.strip()
                ]
                if valid_labels_str
                else []
            )
            invalid_labels = (
                [
                    lbl.strip()
                    for lbl in invalid_labels_str.split(",")
                    if lbl.strip()
                ]
                if invalid_labels_str
                else []
            )

            similar_words.append(
                SimilarWordResult(
                    word_text=doc or meta.get("text", ""),
                    similarity_score=similarity,
                    label=meta.get("label"),
                    validation_status=meta.get("validation_status"),
                    valid_labels=valid_labels,
                    invalid_labels=invalid_labels,
                    merchant_name=meta.get("merchant_name"),
                )
            )

        # Sort by similarity descending and limit results
        similar_words.sort(key=lambda w: -w.similarity_score)
        return similar_words[:n_results]

    except Exception as e:
        logger.error(
            "Error querying ChromaDB for similar words: %s",
            e,
            exc_info=True,
        )
        return []


def format_similar_words_for_prompt(
    similar_words: List[SimilarWordResult],
    max_examples: int = 5,
) -> str:
    """
    Format similar validated words for inclusion in LLM prompt.

    Prioritizes words with VALID validation status and groups by label.

    Args:
        similar_words: Results from query_similar_validated_words()
        max_examples: Maximum number of examples to include

    Returns:
        Formatted string for LLM prompt
    """
    if not similar_words:
        return "No similar validated words found in database."

    # Prioritize validated words
    validated = [w for w in similar_words if w.validation_status == "VALID"]
    other = [w for w in similar_words if w.validation_status != "VALID"]

    # Take validated first, then fill with others
    examples = validated[:max_examples]
    if len(examples) < max_examples:
        examples.extend(other[: max_examples - len(examples)])

    if not examples:
        return "No similar validated words found in database."

    lines = []
    for w in examples:
        status = (
            f"[{w.validation_status}]"
            if w.validation_status
            else "[unvalidated]"
        )
        label = w.label or "no label"
        lines.append(
            f'- "{w.word_text}" → {label} {status} '
            f"(similarity: {w.similarity_score:.2f})"
        )

        # Add valid/invalid labels if available
        if w.valid_labels:
            lines.append(f"    Valid labels: {', '.join(w.valid_labels)}")
        if w.invalid_labels:
            lines.append(f"    Invalid labels: {', '.join(w.invalid_labels)}")

    return "\n".join(lines)


# =============================================================================
# Receipt Text Assembly for LLM Review
# =============================================================================

import re


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


# Currency-related labels that should be shown in receipt context
CURRENCY_LABELS = {
    "LINE_TOTAL",
    "SUBTOTAL",
    "TAX",
    "GRAND_TOTAL",
    "TENDER",
    "CHANGE",
    "DISCOUNT",
    "SAVINGS",
    "CASH_BACK",
    "REFUND",
    "UNIT_PRICE",
}


def _group_words_into_ocr_lines(
    words: List[Dict],
) -> List[Dict]:
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
    lines_by_id: Dict[int, List[Dict]] = defaultdict(list)
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
    words: List[Dict],
    labels: List[Dict],
    highlight_words: Optional[List[Tuple[int, int]]] = None,
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
    label_map: Dict[Tuple[int, int], Dict] = {}
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
    visual_lines: List[Dict] = []

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
    words: List[Dict],
    labels: List[Dict],
) -> List[Dict]:
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
    label_map: Dict[Tuple[int, int], Dict] = {}
    for lbl in labels:
        key = (lbl.get("line_id"), lbl.get("word_id"))
        label_map[key] = lbl

    # Group words by line for context
    lines: Dict[int, List[Dict]] = {}
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
    currency_items: List[Dict] = []
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
