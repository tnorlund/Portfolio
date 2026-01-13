"""Word formatting utilities for embedding context.

This module provides functions for formatting word context for embeddings,
including neighbor detection and position calculation.
"""

from collections.abc import Sequence
from typing import Protocol, runtime_checkable


@runtime_checkable
class WordLike(Protocol):
    """Protocol defining the interface for word objects.

    This allows both ReceiptWord and test mocks to be used interchangeably.
    """

    image_id: str
    receipt_id: int
    line_id: int
    word_id: int
    text: str
    bounding_box: dict[str, float]

    def calculate_centroid(self) -> tuple[float, float]:
        """Calculate the centroid coordinates of the word."""


def _get_word_position(word: WordLike) -> str:
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
    target_word: WordLike,
    all_words: Sequence[WordLike],
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
    left_words, right_words = get_word_neighbors(
        target_word, all_words, context_size
    )

    # Pad left with <EDGE> tags if needed (one per missing position)
    left_padded = (
        ["<EDGE>"] * max(0, context_size - len(left_words)) + left_words
    )[-context_size:]

    # Pad right with <EDGE> tags if needed (one per missing position)
    right_padded = (
        right_words + ["<EDGE>"] * max(0, context_size - len(right_words))
    )[:context_size]

    # Simple format: left_words word right_words
    return " ".join(left_padded + [target_word.text] + right_padded)


def parse_left_right_from_formatted(
    fmt: str, context_size: int = 2
) -> tuple[list[str], list[str]]:
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
        tokens = (
            ["<EDGE>"] * context_size + tokens + ["<EDGE>"] * context_size
        )[:expected_length]
    elif len(tokens) > expected_length:
        # Too many tokens - take first context_size, middle word,
        # last context_size
        tokens = (
            tokens[:context_size]
            + [tokens[len(tokens) // 2]]
            + tokens[-context_size:]
        )

    # Extract left context (first context_size tokens)
    left_tokens = tokens[:context_size]
    # Target word is at index context_size
    # Extract right context (last context_size tokens)
    right_tokens = tokens[context_size + 1 :]

    # Return as-is (already includes <EDGE> tokens if present)
    return left_tokens, right_tokens


def get_word_neighbors(
    target_word: WordLike,
    all_words: Sequence[WordLike],
    context_size: int = 2,
    y_proximity_threshold: float = 0.05,
    x_proximity_threshold: float = 0.25,
) -> tuple[list[str], list[str]]:
    """
    Get the left and right neighbor words for the target word with
    configurable context size.

    Returns up to context_size actual neighbor texts on each side. No <EDGE>
    padding is added; if fewer neighbors are available, the lists will be
    shorter than context_size. Callers requiring <EDGE>-padded outputs should
    use format_word_context_embedding_input instead.

    Uses a line-aware approach:
    1. First, find words on the same visual line using vertical span overlap
    2. If not enough, find words on nearby lines (within y_proximity_threshold)
    3. Sort by x-coordinate to find left/right neighbors

    Args:
        target_word: The word to find neighbors for
        all_words: All words in the receipt
        context_size: Maximum number of words to include on each side
            (default: 2). Actual number may be less if fewer neighbors exist.
        y_proximity_threshold: Maximum y-coordinate difference to consider
            words as being on "nearby" lines (default: 0.05 normalized units)
        x_proximity_threshold: Maximum x-coordinate difference to consider
            nearby-line words as candidates (default: 0.25 normalized
            units). Filters out words that are horizontally too far from
            the target when falling back to nearby lines.

    Returns:
        Tuple of (left_words, right_words) where each is a list of up to
        context_size word texts. Lists may be shorter if fewer neighbors are
        available.
    """

    # Cache centroids for all words to avoid repeated calculations
    def word_key(w: WordLike) -> tuple[str, int, int, int]:
        return (w.image_id, w.receipt_id, w.line_id, w.word_id)

    centroid_cache: dict[tuple[str, int, int, int], tuple[float, float]] = {
        word_key(w): w.calculate_centroid() for w in all_words
    }

    target_centroid = centroid_cache[word_key(target_word)]
    target_x = target_centroid[0]
    target_y = target_centroid[1]

    # Use vertical span overlap for same-line detection
    target_bottom = target_word.bounding_box["y"]
    target_top = target_bottom + target_word.bounding_box["height"]

    # Sort all words by x-coordinate
    sorted_all = sorted(
        enumerate(all_words),
        key=lambda item: (centroid_cache[word_key(item[1])][0], item[0]),
    )

    # Find target word's index
    target_idx = next(
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

    # Find candidates: same line (vertical overlap) or nearby lines
    same_line_candidates = []
    nearby_line_candidates = []

    for orig_idx, w in sorted_all:
        if (w.image_id, w.receipt_id, w.line_id, w.word_id) == (
            target_word.image_id,
            target_word.receipt_id,
            target_word.line_id,
            target_word.word_id,
        ):
            continue

        w_centroid = centroid_cache[word_key(w)]
        w_bottom = w.bounding_box["y"]
        w_top = w_bottom + w.bounding_box["height"]

        # Check vertical span overlap (same visual line)
        # Overlap: w_bottom <= target_top AND w_top >= target_bottom
        if w_bottom <= target_top and w_top >= target_bottom:
            same_line_candidates.append((orig_idx, w))
        # Check y-proximity (nearby lines)
        elif abs(w_centroid[1] - target_y) < y_proximity_threshold:
            nearby_line_candidates.append((orig_idx, w))

    # Filter nearby-line candidates by x-proximity, then sort by y-proximity
    nearby_line_left_filtered = [
        (orig_idx, w)
        for orig_idx, w in nearby_line_candidates
        if centroid_cache[word_key(w)][0] < target_x
        and (target_x - centroid_cache[word_key(w)][0]) < x_proximity_threshold
    ]
    nearby_line_right_filtered = [
        (orig_idx, w)
        for orig_idx, w in nearby_line_candidates
        if centroid_cache[word_key(w)][0] > target_x
        and (centroid_cache[word_key(w)][0] - target_x) < x_proximity_threshold
    ]

    nearby_line_left_sorted = sorted(
        nearby_line_left_filtered,
        key=lambda item: (
            abs(centroid_cache[word_key(item[1])][1] - target_y),
            target_x - centroid_cache[word_key(item[1])][0],
        ),
    )
    nearby_line_right_sorted = sorted(
        nearby_line_right_filtered,
        key=lambda item: (
            abs(centroid_cache[word_key(item[1])][1] - target_y),
            centroid_cache[word_key(item[1])][0] - target_x,
        ),
    )

    # Create set of same-line word identifiers for O(1) lookup
    same_line_ids = {
        (w.image_id, w.receipt_id, w.line_id, w.word_id)
        for _, w in same_line_candidates
    }

    # Collect left neighbors: prioritize same line, then nearby lines
    left_words = []

    # First, try same-line words to the left
    for _, w in reversed(sorted_all[:target_idx]):
        word_id = (w.image_id, w.receipt_id, w.line_id, w.word_id)
        if word_id in same_line_ids:
            left_words.append(w.text)
            if len(left_words) >= context_size:
                break

    # If not enough, add nearby-line words to the left
    if len(left_words) < context_size:
        for _, w in nearby_line_left_sorted:
            left_words.append(w.text)
            if len(left_words) >= context_size:
                break

    # Collect right neighbors: prioritize same line, then nearby lines
    right_words = []

    # First, try same-line words to the right
    for _, w in sorted_all[target_idx + 1 :]:
        word_id = (w.image_id, w.receipt_id, w.line_id, w.word_id)
        if word_id in same_line_ids:
            right_words.append(w.text)
            if len(right_words) >= context_size:
                break

    # If not enough, add nearby-line words to the right
    if len(right_words) < context_size:
        for _, w in nearby_line_right_sorted:
            right_words.append(w.text)
            if len(right_words) >= context_size:
                break

    # Reverse left_words to reading order (farthest-first for left-to-right)
    return list(reversed(left_words)), right_words
