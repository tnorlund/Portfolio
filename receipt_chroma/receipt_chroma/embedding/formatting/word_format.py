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
    target_word: ReceiptWord, all_words: List[ReceiptWord]
) -> str:
    """
    Format word with spatial context matching batch embedding structure.

    Replicates the format from embedding/word/submit.py:
    <TARGET>word</TARGET> <POS>position</POS> <CONTEXT>left right</CONTEXT>

    Args:
        target_word: The word to format
        all_words: All words in the receipt for context

    Returns:
        Formatted string with target, position, and context
    """
    # Calculate position using same logic as batch system
    position = _get_word_position(target_word)

    # Batch-equivalent neighbor selection (scan-based, not nearest-distance)
    target_bottom = target_word.bounding_box["y"]
    target_top = (
        target_word.bounding_box["y"] + target_word.bounding_box["height"]
    )

    # Sort all words by x centroid
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

    # Candidates with vertical span overlap (same line)
    candidates = []
    for w in sorted_all:
        if w is target_word:
            continue
        w_top = w.top_left["y"]
        w_bottom = w.bottom_left["y"]
        if w_bottom >= target_bottom and w_top <= target_top:
            candidates.append(w)

    left_text = "<EDGE>"
    for w in reversed(sorted_all[:idx]):
        if w in candidates:
            left_text = w.text
            break

    right_text = "<EDGE>"
    for w in sorted_all[idx + 1 :]:
        if w in candidates:
            right_text = w.text
            break

    return (
        f"<TARGET>{target_word.text}</TARGET> <POS>{position}</POS> "
        f"<CONTEXT>{left_text} {right_text}</CONTEXT>"
    )


def parse_left_right_from_formatted(fmt: str) -> Tuple[str, str]:
    """
    Parse left and right words from formatted embedding input.

    Given a string like:
      "<TARGET>WORD</TARGET> <POS>…</POS> <CONTEXT>LEFT RIGHT</CONTEXT>"
    return ("LEFT", "RIGHT").

    Args:
        fmt: Formatted string with CONTEXT tags

    Returns:
        Tuple of (left_word, right_word)
    """
    m = re.search(r"<CONTEXT>(.*?)</CONTEXT>", fmt)
    if not m:
        raise ValueError(f"No <CONTEXT>…</CONTEXT> in {fmt!r}")
    cont = m.group(1).strip()
    # Assuming exactly two tokens separated by whitespace
    parts = cont.split(maxsplit=1)
    left = parts[0] if parts else "<EDGE>"
    right = parts[1] if len(parts) > 1 else "<EDGE>"
    return left, right


def get_word_neighbors(
    target_word: ReceiptWord, all_words: List[ReceiptWord]
) -> Tuple[str, str]:
    """
    Get the left and right neighbor words for the target word.

    This is the same logic as format_word_context_embedding_input but
    returns the neighbors directly instead of formatting them.

    Args:
        target_word: The word to find neighbors for
        all_words: All words in the receipt

    Returns:
        Tuple of (left_word, right_word)
    """
    # Batch-equivalent neighbor selection
    target_bottom = target_word.bounding_box["y"]
    target_top = (
        target_word.bounding_box["y"] + target_word.bounding_box["height"]
    )

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

    candidates = []
    for w in sorted_all:
        if w is target_word:
            continue
        w_top = w.top_left["y"]
        w_bottom = w.bottom_left["y"]
        if w_bottom >= target_bottom and w_top <= target_top:
            candidates.append(w)

    left_text = "<EDGE>"
    for w in reversed(sorted_all[:idx]):
        if w in candidates:
            left_text = w.text
            break

    right_text = "<EDGE>"
    for w in sorted_all[idx + 1 :]:
        if w in candidates:
            right_text = w.text
            break

    return left_text, right_text
