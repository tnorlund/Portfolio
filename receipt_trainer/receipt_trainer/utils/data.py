"""Data processing utilities for Receipt Trainer."""

from typing import Dict, Any, List, Tuple, Optional
import logging

logger = logging.getLogger(__name__)


def process_receipt_details(
    receipt_details: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """Process a single receipt's details into the LayoutLM format.

    Args:
        receipt_details: Dictionary containing receipt information

    Returns:
        Processed receipt data in LayoutLM format, or None if no validated words
    """
    receipt = receipt_details["receipt"]
    words = receipt_details["words"]
    word_tags = receipt_details["word_tags"]

    has_validated_word = False
    word_data = []

    # Calculate scale factor maintaining aspect ratio
    max_dim = 1000
    scale = max_dim / max(receipt.width, receipt.height)
    scaled_width = int(receipt.width * scale)
    scaled_height = int(receipt.height * scale)

    # First pass: collect words and their base labels
    for word in words:
        tag_label = "O"  # Default to Outside
        for tag in word_tags:
            if tag.word_id == word.word_id and tag.human_validated:
                tag_label = tag.tag
                has_validated_word = True
                break

        # Get the coordinates (already normalized 0-1)
        x1 = min(word.top_left["x"], word.bottom_left["x"])
        y1 = min(word.top_left["y"], word.top_right["y"])
        x2 = max(word.top_right["x"], word.bottom_right["x"])
        y2 = max(word.bottom_left["y"], word.bottom_right["y"])

        # Store center_y for grouping
        center_y = (y1 + y2) / 2

        # Scale to 0-1000 maintaining aspect ratio
        normalized_bbox = [
            int(x1 * scaled_width),
            int(y1 * scaled_height),
            int(x2 * scaled_width),
            int(y2 * scaled_height),
        ]

        word_data.append(
            {
                "text": word.text,
                "bbox": normalized_bbox,
                "base_label": tag_label,
                "word_id": word.word_id,
                "line_id": word.line_id,
                "center_y": center_y,
                "raw_coords": [x1, y1, x2, y2],
            }
        )

    if not has_validated_word:
        return None

    # Sort words by line_id and word_id to maintain reading order
    word_data.sort(key=lambda x: (x["line_id"], x["word_id"]))

    # Convert to IOB format
    processed_words = []
    for i, word in enumerate(word_data):
        base_label = word["base_label"]
        iob_label = "O"

        if base_label != "O":
            # Check if this word is a continuation
            is_continuation = False
            if i > 0:
                prev_word = word_data[i - 1]
                y_diff = abs(word["center_y"] - prev_word["center_y"])
                x_diff = word["raw_coords"][0] - prev_word["raw_coords"][2]
                if (
                    prev_word["base_label"] == base_label
                    and y_diff < 20
                    and x_diff < 50
                ):
                    is_continuation = True

            iob_label = f"I-{base_label}" if is_continuation else f"B-{base_label}"

        processed_words.append(
            {"text": word["text"], "bbox": word["bbox"], "label": iob_label}
        )

    return {
        "words": [w["text"] for w in processed_words],
        "bboxes": [w["bbox"] for w in processed_words],
        "labels": [w["label"] for w in processed_words],
        "image_id": receipt.image_id,
        "receipt_id": receipt.receipt_id,
        "width": receipt.width,
        "height": receipt.height,
    }


def create_sliding_windows(
    words: List[str],
    bboxes: List[List[int]],
    labels: List[str],
    window_size: int = 50,
    overlap: int = 10,
) -> List[Dict[str, Any]]:
    """
    Create sliding windows from a list of words, bounding boxes, and labels.

    If the total number of tokens is less than or equal to `window_size`, a
    single window containing the entire input is returned. Otherwise, each
    subsequent window starts `overlap` tokens after the start of the previous
    window, until the end of the list is reached.

    Args:
        words: List of words.
        bboxes: Corresponding bounding boxes for each word.
        labels: Corresponding labels for each word.
        window_size: Number of tokens per window.
        overlap: Step size—the number of tokens to move before creating the next window.

    Returns:
        A list of windows, where each window is a dictionary containing
        "words", "bboxes", and "labels" for that slice of the input.
    """
    # If the input doesn't exceed the window size, just return it as a single window.
    if len(words) <= window_size:
        return [{"words": words, "bboxes": bboxes, "labels": labels}]

    # Calculate the start indices for each window using `overlap` as our step size.
    indices = list(range(0, len(words) - window_size + 1, overlap))

    # If there's space left at the end (so we haven't covered all tokens),
    # add one last window that ends exactly at the final token.
    if indices and (indices[-1] + window_size < len(words)):
        indices.append(len(words) - window_size)

    # Build each window from the calculated start indices.
    windows = []
    for start in indices:
        windows.append(
            {
                "words": words[start : start + window_size],
                "bboxes": bboxes[start : start + window_size],
                "labels": labels[start : start + window_size],
            }
        )

    return windows
