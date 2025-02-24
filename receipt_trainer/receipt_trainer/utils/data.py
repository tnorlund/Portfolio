"""Data processing utilities for Receipt Trainer."""

from typing import Dict, Any, List, Tuple, Optional
import logging
import random

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
    image_id: Optional[str] = None,
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
        image_id: Optional ID of the source image/document.
        window_size: Number of tokens per window.
        overlap: Step size—the number of tokens to move before creating the next window.

    Returns:
        A list of windows, where each window is a dictionary containing
        "words", "bboxes", "labels", and optionally "image_id" for that slice.
    """
    # If the input doesn't exceed the window size, just return it as a single window.
    window = {"words": words, "bboxes": bboxes, "labels": labels}
    if image_id is not None:
        window["image_id"] = image_id
        
    if len(words) <= window_size:
        return [window]

    # Calculate the start indices for each window using `overlap` as our step size.
    indices = list(range(0, len(words) - window_size + 1, overlap))

    # If there's space left at the end (so we haven't covered all tokens),
    # add one last window that ends exactly at the final token.
    if indices and (indices[-1] + window_size < len(words)):
        indices.append(len(words) - window_size)

    # Build each window from the calculated start indices.
    windows = []
    for start in indices:
        window = {
            "words": words[start : start + window_size],
            "bboxes": bboxes[start : start + window_size],
            "labels": labels[start : start + window_size],
        }
        if image_id is not None:
            window["image_id"] = image_id
        windows.append(window)

    return windows


def balance_dataset(
    examples: Dict[str, List[Any]], target_entity_ratio: float = 0.7
) -> Dict[str, List[Any]]:
    """Balance dataset with focus on entity distribution.
    
    Args:
        examples: Dictionary containing 'words', 'bbox', 'labels', and optionally 'image_id' lists
        target_entity_ratio: Target ratio of entity tokens to total tokens
    
    Returns:
        Dictionary containing balanced dataset
    """
    # 1. First collect all entity spans with context
    entity_spans = {
        "store_name": [],
        "address": [],
        "date": [],
        "line_item_name": [],
        "line_item_price": [],
        "phone_number": [],
        "time": [],
        "total_amount": [],
        "taxes": [],
    }

    # Get image_ids if they exist
    has_image_ids = "image_id" in examples
    image_ids = examples.get("image_id", [None] * len(examples["words"]))

    for words, bboxes, labels, image_id in zip(
        examples["words"], examples["bbox"], examples["labels"], image_ids
    ):
        i = 0
        while i < len(labels):
            if labels[i].startswith("B-"):
                # Found start of entity
                entity_type = labels[i].split("-")[1].split("_")[0]  # Get base type

                # Find entity end
                j = i + 1
                while j < len(labels) and labels[j].startswith("I-"):
                    j += 1

                # Take context window around entity
                start = max(0, i - 5)  # 5 words before
                end = min(len(words), j + 5)  # 5 words after

                span = {
                    "words": words[start:end],
                    "bbox": bboxes[start:end],
                    "labels": labels[start:end],
                    "entity_start": i - start,  # Track where entity starts in window
                    "entity_end": j - start,
                    "image_id": image_id,
                }

                if entity_type in entity_spans:
                    entity_spans[entity_type].append(span)
                i = j
            else:
                i += 1

    # 2. Balance entity representations
    target_spans_per_type = max(len(spans) for spans in entity_spans.values())
    balanced_data = []

    for entity_type, spans in entity_spans.items():
        if spans:  # If we have any spans of this type
            # Oversample rare entities to match most common
            multiplier = max(1, target_spans_per_type // len(spans))
            balanced_data.extend(spans * multiplier)

    # 3. Convert back to dataset format
    result = {
        "words": [s["words"] for s in balanced_data],
        "bbox": [s["bbox"] for s in balanced_data],
        "labels": [s["labels"] for s in balanced_data],
    }
    
    # Only include image_ids if they existed in input
    if has_image_ids:
        result["image_id"] = [s["image_id"] for s in balanced_data]
    
    return result


def augment_example(examples: Dict[str, List[Any]]) -> Dict[str, List[Any]]:
    """Augment examples with random bbox shifts.
    
    Args:
        examples: Dictionary containing 'words', 'bbox', 'labels', and optionally 'image_id' lists
    
    Returns:
        Dictionary containing augmented examples
    """
    augmented_bboxes = []

    for bbox_list in examples["bbox"]:
        # Randomly shift all bboxes in this example
        if random.random() < 0.5:
            shift = random.randint(-20, 20)
            shifted_bboxes = []
            for bbox in bbox_list:
                # Each bbox is a list of 4 values: [x1, y1, x2, y2]
                shifted_bboxes.append(
                    [
                        bbox[0] + shift,  # x1
                        bbox[1] + shift,  # y1
                        bbox[2] + shift,  # x2
                        bbox[3] + shift,  # y2
                    ]
                )
            augmented_bboxes.append(shifted_bboxes)
        else:
            augmented_bboxes.append(bbox_list)

    # Return augmented examples, preserving all fields including image_id
    return {**examples, "bbox": augmented_bboxes}
