"""Word formatting utilities for embedding context.

This module provides functions for formatting word context for embeddings,
including neighbor detection and position calculation.
"""

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
    target_word: ReceiptWord,
    all_words: List[ReceiptWord],
    context_size: int = 2,
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
    # Sort all words by x-coordinate (horizontal position)
    # Use a stable sort key: (x, original_index) to preserve order when
    # x is identical
    sorted_all = sorted(
        enumerate(all_words),
        key=lambda item: (item[1].calculate_centroid()[0], item[0]),
    )

    # Find index of target in sorted list
    idx = next(
        i
        for i, (orig_idx, w) in enumerate(sorted_all)
        if (w.image_id, w.receipt_id, w.line_id, w.word_id)
        == (
            target_word.image_id,
            target_word.receipt_id,
            target_word.line_id,
            target_word.word_id,
        )
    )

    # Collect left neighbors (up to context_size)
    # Find words based on horizontal position, regardless of line
    left_words = []
    for orig_idx, w in reversed(sorted_all[:idx]):
        if (w.image_id, w.receipt_id, w.line_id, w.word_id) == (
            target_word.image_id,
            target_word.receipt_id,
            target_word.line_id,
            target_word.word_id,
        ):
            continue
        left_words.append(w.text)
        if len(left_words) >= context_size:
            break

    # Collect right neighbors (up to context_size)
    # Find words based on horizontal position, regardless of line
    right_words = []
    for orig_idx, w in sorted_all[idx + 1 :]:
        if (w.image_id, w.receipt_id, w.line_id, w.word_id) == (
            target_word.image_id,
            target_word.receipt_id,
            target_word.line_id,
            target_word.word_id,
        ):
            continue
        right_words.append(w.text)
        if len(right_words) >= context_size:
            break

    # Pad left with <EDGE> tags if needed (one per missing position)
    left_padded = (["<EDGE>"] * max(0, context_size - len(left_words)) + left_words)[
        -context_size:
    ]

    # Pad right with <EDGE> tags if needed (one per missing position)
    right_padded = (right_words + ["<EDGE>"] * max(0, context_size - len(right_words)))[
        :context_size
    ]

    # Simple format: left_words word right_words
    return " ".join(left_padded + [target_word.text] + right_padded)


def parse_left_right_from_formatted(
    fmt: str, context_size: int = 2
) -> Tuple[List[str], List[str]]:
    """
    Parse left and right context words from formatted embedding input.

    New format: "left_words... word right_words..."
    Example: "<EDGE> Subtotal Total Tax Discount"

    The format is: [left_context (context_size tokens)] [target_word]
    [right_context (context_size tokens)]
    Total tokens = context_size + 1 + context_size = 2*context_size + 1

    Args:
        fmt: Formatted string with context words
        context_size: Expected number of context words on each side
            (default: 2)

    Returns:
        Tuple of (left_words, right_words) as lists, each of length
        context_size
    """
    # Split by spaces to get all tokens
    tokens = fmt.split()

    # Expected format: [left_context] [target] [right_context]
    # Total length should be: context_size + 1 + context_size =
    # 2*context_size + 1
    expected_length = 2 * context_size + 1

    if len(tokens) < expected_length:
        # Not enough tokens - pad with <EDGE>
        tokens = (["<EDGE>"] * context_size + tokens + ["<EDGE>"] * context_size)[
            :expected_length
        ]
    elif len(tokens) > expected_length:
        # Too many tokens - take first context_size, middle word,
        # last context_size
        tokens = (
            tokens[:context_size] + [tokens[len(tokens) // 2]] + tokens[-context_size:]
        )

    # Extract left context (first context_size tokens)
    left_tokens = tokens[:context_size]
    # Target word is at index context_size
    # Extract right context (last context_size tokens)
    right_tokens = tokens[context_size + 1 :]

    # Return as-is (already includes <EDGE> tokens if present)
    return left_tokens, right_tokens


def get_word_neighbors(
    target_word: ReceiptWord,
    all_words: List[ReceiptWord],
    context_size: int = 2,
) -> Tuple[List[str], List[str]]:
    """
    Get the left and right neighbor words for the target word with
    configurable context size.

    Returns up to context_size actual neighbor texts on each side. No <EDGE>
    padding is added; if fewer neighbors are available, the lists will be
    shorter than context_size. Callers requiring <EDGE>-padded outputs should
    use format_word_context_embedding_input instead.

    Finds neighbors based on horizontal position (x-coordinate), regardless
    of line (y-coordinate). When x-coordinates are identical, preserves
    original list order (stable sort).

    Args:
        target_word: The word to find neighbors for
        all_words: All words in the receipt
        context_size: Maximum number of words to include on each side
            (default: 2). Actual number may be less if fewer neighbors exist.

    Returns:
        Tuple of (left_words, right_words) where each is a list of up to
        context_size word texts. Lists may be shorter if fewer neighbors are
        available.
    """
    # Sort all words by x-coordinate (horizontal position)
    # Use a stable sort key: (x, original_index) to preserve order when
    # x is identical
    sorted_all = sorted(
        enumerate(all_words),
        key=lambda item: (item[1].calculate_centroid()[0], item[0]),
    )

    # Find index of target in sorted list
    idx = next(
        i
        for i, (orig_idx, w) in enumerate(sorted_all)
        if (w.image_id, w.receipt_id, w.line_id, w.word_id)
        == (
            target_word.image_id,
            target_word.receipt_id,
            target_word.line_id,
            target_word.word_id,
        )
    )

    # Collect left neighbors (up to context_size)
    # Find words based on horizontal position, regardless of line
    left_words = []
    for orig_idx, w in reversed(sorted_all[:idx]):
        if (w.image_id, w.receipt_id, w.line_id, w.word_id) == (
            target_word.image_id,
            target_word.receipt_id,
            target_word.line_id,
            target_word.word_id,
        ):
            continue
        left_words.append(w.text)
        if len(left_words) >= context_size:
            break

    # Collect right neighbors (up to context_size)
    # Find words based on horizontal position, regardless of line
    right_words = []
    for orig_idx, w in sorted_all[idx + 1 :]:
        if (w.image_id, w.receipt_id, w.line_id, w.word_id) == (
            target_word.image_id,
            target_word.receipt_id,
            target_word.line_id,
            target_word.word_id,
        ):
            continue
        right_words.append(w.text)
        if len(right_words) >= context_size:
            break

    return left_words, right_words
