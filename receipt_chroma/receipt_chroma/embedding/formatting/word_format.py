"""Word formatting utilities for embedding context.

This module provides functions for formatting word context for embeddings,
including neighbor detection and position calculation.
"""

import re
from typing import List, Tuple

from receipt_dynamo.entities import ReceiptWord


def _get_word_position(word: ReceiptWord) -> str:
    """
    Get word position in 3x3 grid format matching batch system.

    Replicates logic from embedding/word/submit.py
    Uses normalized coordinates (0.0-1.0) from calculate_centroid()
    """
    # Calculate centroid coordinates (normalized 0.0–1.0)
    x_center, y_center = word.calculate_centroid()

    # Determine vertical bucket (y=0 at bottom in receipt coordinate system)
    if y_center > 0.66:
        vertical = "top"
    elif y_center > 0.33:
        vertical = "middle"
    else:
        vertical = "bottom"

    # Determine horizontal bucket
    if x_center < 0.33:
        horizontal = "left"
    elif x_center < 0.66:
        horizontal = "center"
    else:
        horizontal = "right"

    return f"{vertical}-{horizontal}"


def format_word_context_embedding_input(
    target_word: ReceiptWord, all_words: List[ReceiptWord], context_size: int = 2
) -> str:
    """
    Format word with spatial context for embedding.

    New format: simple context-only with multiple words and <EDGE> tags.
    Format: "left_words... word right_words..."

    Example with context_size=2:
    - At edge: "<EDGE> <EDGE> Total Tax Discount"
    - 1 from edge: "<EDGE> Subtotal Total Tax Discount"
    - 2+ from edge: "Items Subtotal Total Tax Discount"

    Args:
        target_word: The word to format
        all_words: All words in the receipt for context
        context_size: Number of words to include on each side (default: 2)

    Returns:
        Formatted string with context words and <EDGE> tags
    """
    # Calculate target word's vertical span (using corners)
    target_bottom = target_word.bottom_left["y"]
    target_top = target_word.top_left["y"]

    # Sort all words by x-coordinate (horizontal position)
    # Find words based on horizontal position that are on roughly the same horizontal space
    sorted_all = sorted(all_words, key=lambda w: w.calculate_centroid()[0])
    # Find index of target
    idx = next(
        i
        for i, w in enumerate(sorted_all)
        if (w.image_id, w.receipt_id, w.line_id, w.word_id)
        == (
            target_word.image_id,
            target_word.receipt_id,
            target_word.line_id,
            target_word.word_id,
        )
    )

    # Collect left neighbors (up to context_size)
    # Check if candidate word's centroid y is within target's vertical span
    left_words = []
    for w in reversed(sorted_all[:idx]):
        if w is target_word:
            continue
        # Check if this word is on roughly the same horizontal space
        # by checking if its centroid y is within target's vertical span
        w_centroid_y = w.calculate_centroid()[1]
        if target_bottom <= w_centroid_y <= target_top:
            left_words.append(w.text)
            if len(left_words) >= context_size:
                break

    # Collect right neighbors (up to context_size)
    # Check if candidate word's centroid y is within target's vertical span
    right_words = []
    for w in sorted_all[idx + 1 :]:
        if w is target_word:
            continue
        # Check if this word is on roughly the same horizontal space
        # by checking if its centroid y is within target's vertical span
        w_centroid_y = w.calculate_centroid()[1]
        if target_bottom <= w_centroid_y <= target_top:
            right_words.append(w.text)
            if len(right_words) >= context_size:
                break

    # Pad left with <EDGE> tags if needed (one per missing position)
    left_padded = (["<EDGE>"] * max(0, context_size - len(left_words)) + left_words)[-context_size:]

    # Pad right with <EDGE> tags if needed (one per missing position)
    right_padded = (right_words + ["<EDGE>"] * max(0, context_size - len(right_words)))[:context_size]

    # Simple format: left_words word right_words
    return " ".join(left_padded + [target_word.text] + right_padded)


def parse_left_right_from_formatted(fmt: str, context_size: int = 2) -> Tuple[List[str], List[str]]:
    """
    Parse left and right context words from formatted embedding input.

    New format: "left_words... word right_words..."
    Example: "<EDGE> Subtotal Total Tax Discount"

    Returns the first word from each side for backward compatibility,
    but can be extended to return full context.

    Args:
        fmt: Formatted string with context words
        context_size: Expected number of context words on each side (default: 2)

    Returns:
        Tuple of (left_words, right_words) as lists
    """
    # Split by spaces to get all tokens
    tokens = fmt.split()

    # Find the target word (it's the one that's not <EDGE> and not in a known position)
    # For now, we'll use a simpler approach: assume format is "left... word right..."
    # We need to identify where the word boundaries are

    # Try to find the word by looking for non-<EDGE> tokens
    # This is a heuristic - in practice, you should know the word text
    words_only = [t for t in tokens if t != "<EDGE>"]

    if len(words_only) < 1:
        # All edges - return all edges
        return ["<EDGE>"] * context_size, ["<EDGE>"] * context_size

    # The middle word is likely the target (assuming context_size words on each side)
    # But we don't know which one, so we'll extract context_size from each side
    # For backward compatibility, return first word from each side
    left_tokens = []
    right_tokens = []
    found_word = False

    for token in tokens:
        if token == "<EDGE>":
            if not found_word:
                left_tokens.append(token)
            else:
                right_tokens.append(token)
        else:
            if not found_word:
                # Still collecting left context
                left_tokens.append(token)
                # Heuristic: if we have context_size non-EDGE words, next is target
                if len([t for t in left_tokens if t != "<EDGE>"]) >= context_size:
                    found_word = True
            else:
                # Collecting right context
                right_tokens.append(token)

    # Extract first word from each side for backward compatibility
    left_first = left_tokens[-1] if left_tokens and left_tokens[-1] != "<EDGE>" else "<EDGE>"
    right_first = right_tokens[0] if right_tokens and right_tokens[0] != "<EDGE>" else "<EDGE>"

    # For full context, extract context_size words from each side
    left_words = [t for t in left_tokens if t != "<EDGE>"][-context_size:] if left_tokens else []
    right_words = [t for t in right_tokens if t != "<EDGE>"][:context_size] if right_tokens else []

    # Pad with <EDGE> if needed
    left_padded = (["<EDGE>"] * max(0, context_size - len(left_words)) + left_words)[-context_size:]
    right_padded = (right_words + ["<EDGE>"] * max(0, context_size - len(right_words)))[:context_size]

    return left_padded, right_padded


def get_word_neighbors(
    target_word: ReceiptWord, all_words: List[ReceiptWord], context_size: int = 2
) -> Tuple[List[str], List[str]]:
    """
    Get the left and right neighbor words for the target word with configurable context size.

    Returns multiple neighbors on each side, using <EDGE> tags for missing positions.
    This preserves relative position information and distinguishes words at different
    distances from edges.

    Args:
        target_word: The word to find neighbors for
        all_words: All words in the receipt
        context_size: Number of words to include on each side (default: 2)

    Returns:
        Tuple of (left_words, right_words) where each is a list of context_size words
    """
    # Calculate target word's vertical span (using corners)
    target_bottom = target_word.bottom_left["y"]
    target_top = target_word.top_left["y"]

    # Sort all words by x-coordinate (horizontal position)
    # Find words based on horizontal position that are on roughly the same horizontal space
    sorted_all = sorted(all_words, key=lambda w: w.calculate_centroid()[0])
    idx = next(
        i
        for i, w in enumerate(sorted_all)
        if (w.image_id, w.receipt_id, w.line_id, w.word_id)
        == (
            target_word.image_id,
            target_word.receipt_id,
            target_word.line_id,
            target_word.word_id,
        )
    )

    # Collect left neighbors (up to context_size)
    # Check if candidate word's centroid y is within target's vertical span
    left_words = []
    for w in reversed(sorted_all[:idx]):
        if w is target_word:
            continue
        # Check if this word is on roughly the same horizontal space
        # by checking if its centroid y is within target's vertical span
        w_centroid_y = w.calculate_centroid()[1]
        if target_bottom <= w_centroid_y <= target_top:
            left_words.append(w.text)
            if len(left_words) >= context_size:
                break

    # Collect right neighbors (up to context_size)
    # Check if candidate word's centroid y is within target's vertical span
    right_words = []
    for w in sorted_all[idx + 1 :]:
        if w is target_word:
            continue
        # Check if this word is on roughly the same horizontal space
        # by checking if its centroid y is within target's vertical span
        w_centroid_y = w.calculate_centroid()[1]
        if target_bottom <= w_centroid_y <= target_top:
            right_words.append(w.text)
            if len(right_words) >= context_size:
                break

    return left_words, right_words
