"""
Word context building utilities for the Label Evaluator agent.

Functions for building WordContext objects from receipt words/labels
and grouping them into visual lines.
"""

from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from receipt_dynamo.entities import ReceiptWord, ReceiptWordLabel

from receipt_agent.agents.label_evaluator.state import (
    VisualLine,
    WordContext,
)


def build_word_contexts(
    words: List[ReceiptWord],
    labels: List[ReceiptWordLabel],
) -> List[WordContext]:
    """
    Build WordContext objects for each word, linking to their label history.

    Args:
        words: All ReceiptWord entities for the receipt
        labels: All ReceiptWordLabel entities for the receipt

    Returns:
        List of WordContext objects with label history populated
    """
    # Group labels by word (line_id, word_id)
    labels_by_word: Dict[Tuple[int, int], List[ReceiptWordLabel]] = (
        defaultdict(list)
    )
    for label in labels:
        key = (label.line_id, label.word_id)
        labels_by_word[key].append(label)

    # Sort labels by timestamp to find "current" (most recent)
    for key in labels_by_word:
        labels_by_word[key].sort(
            key=lambda lbl: (
                lbl.timestamp_added.isoformat()
                if hasattr(lbl.timestamp_added, "isoformat")
                else str(lbl.timestamp_added)
            ),
            reverse=True,
        )

    # Build WordContext for each word
    word_contexts = []
    for word in words:
        key = (word.line_id, word.word_id)
        history = labels_by_word.get(key, [])

        # Prefer VALID labels for evaluation - only consider labels that have
        # been validated. This prevents re-flagging already-reviewed issues
        # and ensures we evaluate against ground truth labels.
        valid_labels = [
            lbl for lbl in history if lbl.validation_status == "VALID"
        ]
        if valid_labels:
            current = valid_labels[0]  # Most recent VALID label
        else:
            # No VALID labels - treat as unlabeled for evaluation purposes
            # This includes words with INVALID/NEEDS_REVIEW labels (already
            # reviewed) and words with no labels at all
            current = None

        centroid = word.calculate_centroid()
        ctx = WordContext(
            word=word,
            current_label=current,
            label_history=history,
            normalized_y=centroid[1],
            normalized_x=centroid[0],
        )
        word_contexts.append(ctx)

    return word_contexts


def assemble_visual_lines(
    word_contexts: List[WordContext],
    y_tolerance: Optional[float] = None,
) -> List[VisualLine]:
    """
    Group words into visual lines by y-coordinate proximity.

    Since OCR line_id can split visual lines (e.g., product name and price
    on the same row become separate line_ids), this function groups words
    by their actual y-position using the same tolerance logic as
    extract_pricing_table_from_words in helpers.py.

    Args:
        word_contexts: List of WordContext objects to group
        y_tolerance: Optional explicit tolerance. If None, computed from
            median word height (median_height * 0.75)

    Returns:
        List of VisualLine objects, sorted top-to-bottom (y descending)
    """
    if not word_contexts:
        return []

    # Sort by y descending (top first, since y=1 is top, y=0 is bottom)
    sorted_contexts = sorted(
        word_contexts,
        key=lambda c: -c.normalized_y,
    )

    # Calculate row tolerance from median word height if not provided
    if y_tolerance is None:
        heights = [
            w.word.bounding_box.get("height", 0.02)
            for w in sorted_contexts
            if w.word.bounding_box.get("height")
        ]
        if heights:
            median_height = sorted(heights)[len(heights) // 2]
            y_tolerance = max(0.01, median_height * 0.75)
        else:
            y_tolerance = 0.015  # Fallback

    # Group by y-proximity
    visual_lines: List[VisualLine] = []
    current_line_words: List[WordContext] = [sorted_contexts[0]]
    current_y = sorted_contexts[0].normalized_y

    for ctx in sorted_contexts[1:]:
        if abs(ctx.normalized_y - current_y) <= y_tolerance:
            current_line_words.append(ctx)
            # Update running average y
            current_y = sum(c.normalized_y for c in current_line_words) / len(
                current_line_words
            )
        else:
            # Sort current line left-to-right and save
            current_line_words.sort(key=lambda c: c.normalized_x)
            visual_lines.append(
                VisualLine(
                    line_index=len(visual_lines),
                    words=current_line_words,
                    y_center=current_y,
                )
            )
            current_line_words = [ctx]
            current_y = ctx.normalized_y

    # Don't forget last line
    current_line_words.sort(key=lambda c: c.normalized_x)
    visual_lines.append(
        VisualLine(
            line_index=len(visual_lines),
            words=current_line_words,
            y_center=current_y,
        )
    )

    # Populate position_in_line for each context
    for line in visual_lines:
        for i, ctx in enumerate(line.words):
            ctx.visual_line_index = line.line_index
            ctx.position_in_line = i

    return visual_lines


def get_same_line_words(
    ctx: WordContext,
    visual_lines: List[VisualLine],
) -> List[WordContext]:
    """
    Get other words on the same visual line as ctx.

    Computed on-demand to avoid circular references in state.

    Args:
        ctx: The WordContext to find neighbors for
        visual_lines: All visual lines from the receipt

    Returns:
        List of WordContext objects on the same line (excluding ctx)
    """
    if ctx.visual_line_index >= len(visual_lines):
        return []
    line = visual_lines[ctx.visual_line_index]
    return [c for c in line.words if c is not ctx]
