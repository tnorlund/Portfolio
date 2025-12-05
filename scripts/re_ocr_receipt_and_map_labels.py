#!/usr/bin/env python3
"""
Re-OCR a receipt using Swift OCR engine and map previous ReceiptWordLabels to new OCR data.

This script:
1. Downloads the cropped receipt image (or uses existing)
2. Runs Swift OCR on the cropped image
3. Matches old ReceiptWords to new OCR words (text + position)
4. Maps ReceiptWordLabels from old ReceiptWords to new ReceiptWords
"""

import argparse
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

repo_root = Path(__file__).parent.parent
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "receipt_upload"))

from receipt_dynamo import DynamoClient
from receipt_dynamo.entities import (
    Receipt,
    ReceiptLetter,
    ReceiptLine,
    ReceiptWord,
    ReceiptWordLabel,
)

# Import setup_environment from split_receipt; fallback to Pulumi/env vars
try:
    from scripts.split_receipt import setup_environment
except ImportError:

    def setup_environment() -> Dict[str, str]:
        """Load config from Pulumi (dev stack) with env fallbacks."""
        from receipt_dynamo.data._pulumi import load_env, load_secrets

        repo_root = Path(__file__).parent.parent
        infra_dir = repo_root / "infra"

        env = {}
        secrets = {}
        try:
            env = load_env("dev", working_dir=str(infra_dir)) or {}
        except Exception:
            env = {}
        try:
            secrets = load_secrets("dev", working_dir=str(infra_dir)) or {}
        except Exception:
            secrets = {}

        table_name = (
            os.getenv("DYNAMODB_TABLE_NAME")
            or env.get("dynamodb_table_name")
            or env.get("receipts_table_name")
            or "ReceiptsTable-dc5be22"
        )

        chromadb_bucket = (
            os.getenv("CHROMADB_BUCKET")
            or env.get("embedding_chromadb_bucket_name")
            or env.get("chromadb_bucket_name")
            or ""
        )

        artifacts_bucket = (
            os.getenv("ARTIFACTS_BUCKET")
            or env.get("artifacts_bucket_name")
            or ""
        )

        raw_bucket = (
            os.getenv("RAW_BUCKET")
            or env.get("raw_bucket_name")
            or env.get("raw_bucket")
            or ""
        )

        site_bucket = (
            os.getenv("SITE_BUCKET")
            or env.get("site_bucket_name")
            or env.get("cdn_bucket_name")
            or env.get("cdn_bucket")
            or ""
        )

        # Propagate to environment for downstream libs
        os.environ["DYNAMODB_TABLE_NAME"] = table_name
        if chromadb_bucket:
            os.environ["CHROMADB_BUCKET"] = chromadb_bucket
        if artifacts_bucket:
            os.environ["ARTIFACTS_BUCKET"] = artifacts_bucket
        if raw_bucket:
            os.environ["RAW_BUCKET"] = raw_bucket
        if site_bucket:
            os.environ["SITE_BUCKET"] = site_bucket

        # Optionally propagate OpenAI key if present in Pulumi secrets
        openai_key = secrets.get("OPENAI_API_KEY") or secrets.get(
            "portfolio:OPENAI_API_KEY"
        )
        if openai_key:
            os.environ["OPENAI_API_KEY"] = openai_key

        return {
            "dynamo_table_name": table_name,
            "raw_bucket": raw_bucket,
            "site_bucket": site_bucket,
            "artifacts_bucket": artifacts_bucket,
            "chromadb_bucket": chromadb_bucket,
            "openai_api_key": openai_key or "",
        }


# Import PIL first (should always be available)
try:
    from PIL import Image as PIL_Image
    from PIL import ImageDraw, ImageFont

    PIL_AVAILABLE = True
except ImportError as e:
    PIL_AVAILABLE = False
    PIL_Image = None
    ImageDraw = None
    ImageFont = None
    print(f"⚠️  PIL not available: {e}")

# Import receipt_upload modules
try:
    from receipt_upload.ocr import (
        apple_vision_ocr_job,
        process_ocr_dict_as_image,
    )
    from receipt_upload.utils import (
        image_ocr_to_receipt_ocr,
        upload_all_cdn_formats,
        upload_png_to_s3,
    )

    OCR_AVAILABLE = True
except ImportError as e:
    OCR_AVAILABLE = False
    apple_vision_ocr_job = None
    process_ocr_dict_as_image = None
    image_ocr_to_receipt_ocr = None
    upload_all_cdn_formats = None
    upload_png_to_s3 = None
    print(f"⚠️  receipt_upload not available: {e}")


def calculate_line_centroid(line) -> Tuple[float, float]:
    """Calculate centroid of a line from its corner coordinates.

    Coordinates are expected to be in normalized 0-1 space (warped receipt space).
    """
    try:
        # Try using calculate_centroid method if available
        return line.calculate_centroid()
    except AttributeError:
        # Fallback: calculate from corners
        tl = line.top_left
        tr = line.top_right
        bl = line.bottom_left
        br = line.bottom_right
        x = (tl["x"] + tr["x"] + bl["x"] + br["x"]) / 4.0
        y = (tl["y"] + tr["y"] + bl["y"] + br["y"]) / 4.0
        return (x, y)


def match_lines_by_centroid(
    old_lines: List,
    new_lines: List,
    centroid_tolerance: float = 0.15,  # Increased tolerance for coordinate space differences
) -> Dict[int, int]:
    """
    Match old ReceiptLines to new OCR Lines using centroid position.

    Args:
        old_lines: List of ReceiptLine entities (from DynamoDB)
        new_lines: List of Line entities (from OCR)
        centroid_tolerance: Maximum normalized distance for centroid matching

    Returns:
        Dict mapping old_line_id -> new_line_id
    """
    line_map = {}

    # Calculate centroids for all lines
    old_centroids = {}
    for old_line in old_lines:
        old_centroids[old_line.line_id] = calculate_line_centroid(old_line)

    new_centroids = {}
    for new_line in new_lines:
        new_centroids[new_line.line_id] = calculate_line_centroid(new_line)

    # Match lines by centroid proximity
    for old_line_id, old_centroid in old_centroids.items():
        best_match = None
        best_distance = float("inf")

        for new_line_id, new_centroid in new_centroids.items():
            # Skip if already matched
            if new_line_id in line_map.values():
                continue

            # Calculate normalized distance
            x_diff = abs(old_centroid[0] - new_centroid[0])
            y_diff = abs(old_centroid[1] - new_centroid[1])
            distance = (x_diff**2 + y_diff**2) ** 0.5

            if distance < best_distance and distance < centroid_tolerance:
                best_distance = distance
                best_match = new_line_id

        if best_match is not None:
            line_map[old_line_id] = best_match
            # Only print successful matches to reduce noise
            if best_distance < 0.05:  # Only print close matches
                print(
                    f"  ✓ Matched line {old_line_id} -> {best_match} "
                    f"(distance={best_distance:.4f})"
                )
        # Don't print unmatched lines - many old lines won't be in the new receipt

    return line_map


def match_words_directly(
    old_words: List,
    new_words_list: List,
    position_tolerance: float = 0.08,
    text_match_weight: float = 0.6,
    position_match_weight: float = 0.4,
) -> Dict[Tuple[int, int], Tuple[int, int]]:
    """
    Match old ReceiptWords to new OCR words directly by text and position.

    This approach doesn't require line matching first, which allows us to
    match words even when lines don't match perfectly.

    Args:
        old_words: List of ReceiptWord entities (from JSON export)
        new_words_list: List of Word entities (from new OCR)
        position_tolerance: Maximum normalized distance for position matching
        text_match_weight: Weight for text matching in combined score
        position_match_weight: Weight for position matching in combined score

    Returns:
        Dict mapping (old_line_id, old_word_id) -> (new_line_id, new_word_id)
    """
    word_map = {}
    used_new_words = set()

    # Normalize text for comparison
    def normalize_text(text):
        if not text:
            return ""
        normalized = text.strip().upper()
        normalized = normalized.replace('"', "'").replace('"', "'")
        normalized = normalized.replace("–", "-").replace("—", "-")
        return normalized

    # Sort old words by text length (longer words are more distinctive)
    old_words_sorted = sorted(
        old_words,
        key=lambda w: (
            len(w.text or ""),
            w.calculate_centroid()[0],
            w.calculate_centroid()[1],
        ),
        reverse=True,
    )

    print(
        f"  Matching {len(old_words_sorted)} old words to {len(new_words_list)} new words..."
    )

    for old_word in old_words_sorted:
        old_text = normalize_text(old_word.text)
        old_centroid = old_word.calculate_centroid()

        if not old_text:
            continue

        best_match = None
        best_match_score = 0.0
        best_match_key = None

        for new_word in new_words_list:
            # Skip if already matched
            new_key = (new_word.line_id, new_word.word_id)
            if new_key in used_new_words:
                continue

            new_text = normalize_text(new_word.text)
            new_centroid = new_word.calculate_centroid()

            # Text match (exact or partial)
            text_match = 1.0 if old_text == new_text else 0.0
            if not text_match and old_text and new_text:
                # Partial text match (substring)
                if old_text in new_text or new_text in old_text:
                    # Longer text wins
                    text_match = (
                        min(len(old_text), len(new_text))
                        / max(len(old_text), len(new_text))
                        * 0.9
                    )
                # Character overlap (fuzzy match for OCR errors)
                elif len(set(old_text) & set(new_text)) > 0:
                    char_overlap = len(set(old_text) & set(new_text))
                    char_union = len(set(old_text) | set(new_text))
                    text_match = (
                        (char_overlap / char_union) * 0.7
                        if char_union > 0
                        else 0.0
                    )

                    # Bonus for similar length
                    length_ratio = min(len(old_text), len(new_text)) / max(
                        len(old_text), len(new_text), 1
                    )
                    text_match *= 0.5 + 0.5 * length_ratio

            # Position match (normalized distance)
            x_diff = abs(old_centroid[0] - new_centroid[0])
            y_diff = abs(old_centroid[1] - new_centroid[1])
            position_distance = (x_diff**2 + y_diff**2) ** 0.5

            # Hard distance gate to prevent far-away matches of repeated text
            if position_distance > position_tolerance:
                continue

            position_match = max(
                0.0, 1.0 - position_distance / position_tolerance
            )

            # Combined score
            score = (
                text_match * text_match_weight
                + position_match * position_match_weight
            )

            if (
                score > best_match_score and score > 0.3
            ):  # Lower threshold for direct matching
                best_match_score = score
                best_match = new_word
                best_match_key = new_key

        if best_match:
            word_map[(old_word.line_id, old_word.word_id)] = (
                best_match.line_id,
                best_match.word_id,
            )
            used_new_words.add(best_match_key)
            if best_match_score > 0.5:  # Only print high-confidence matches
                print(
                    f"    ✓ '{old_word.text}' (L{old_word.line_id}W{old_word.word_id}) -> "
                    f"'{best_match.text}' (L{best_match.line_id}W{best_match.word_id}) "
                    f"[score={best_match_score:.2f}]"
                )

    return word_map


def match_words_by_text_and_position(
    old_lines: List,
    old_words: List,
    new_lines: List,
    new_words_list: List,
    line_map: Dict[int, int],
    position_tolerance: float = 0.1,
) -> Dict[Tuple[int, int], Tuple[int, int]]:
    """
    Match old ReceiptWords to new OCR words using line matches and text/position.

    Strategy:
    1. First match lines by centroid (already done via line_map)
    2. Within matched lines, compare word counts and text similarity
    3. Map words within matched lines by text and position

    Args:
        old_lines: List of ReceiptLine entities (from DynamoDB)
        old_words: List of ReceiptWord entities (from DynamoDB)
        new_lines: List of Line entities (from OCR)
        new_words_list: List of Word entities (from OCR)
        line_map: Dict mapping old_line_id -> new_line_id
        position_tolerance: Maximum normalized distance for position matching

    Returns:
        Dict mapping (old_line_id, old_word_id) -> (new_line_id, new_word_id)
    """
    word_map = {}

    # Group words by line
    old_words_by_line: Dict[int, List] = {}
    for word in old_words:
        old_words_by_line.setdefault(word.line_id, []).append(word)

    new_words_by_line: Dict[int, List] = {}
    for word in new_words_list:
        new_words_by_line.setdefault(word.line_id, []).append(word)

    # Process each matched line pair
    for old_line_id, new_line_id in line_map.items():
        old_line_words = old_words_by_line.get(old_line_id, [])
        new_line_words = new_words_by_line.get(new_line_id, [])

        if not old_line_words or not new_line_words:
            print(
                f"  ⚠️  Line {old_line_id}->{new_line_id}: "
                f"old_words={len(old_line_words)}, new_words={len(new_line_words)}"
            )
            continue

        # Compare word counts and text similarity
        # Normalize text for comparison (remove special chars, normalize whitespace)
        def normalize_text(text):
            if not text:
                return ""
            # Remove special characters that OCR might interpret differently
            normalized = text.strip().upper()
            # Replace common OCR variations
            normalized = normalized.replace('"', "'").replace('"', "'")
            normalized = normalized.replace("–", "-").replace("—", "-")
            return normalized

        old_texts = {normalize_text(w.text) for w in old_line_words if w.text}
        new_texts = {normalize_text(w.text) for w in new_line_words if w.text}

        # Remove empty strings
        old_texts = {t for t in old_texts if t}
        new_texts = {t for t in new_texts if t}

        text_overlap = len(old_texts & new_texts)
        text_union = len(old_texts | new_texts)

        if text_union == 0:
            print(
                f"  ⚠️  Line {old_line_id}->{new_line_id}: No text to compare"
            )
            continue

        text_similarity = text_overlap / text_union if text_union > 0 else 0.0
        word_count_ratio = min(len(old_line_words), len(new_line_words)) / max(
            len(old_line_words), len(new_line_words), 1
        )

        # Also check for partial text matches (substring matches)
        partial_matches = 0
        for old_text in old_texts:
            for new_text in new_texts:
                if old_text in new_text or new_text in old_text:
                    partial_matches += 1
                    break

        partial_similarity = (
            partial_matches / max(len(old_texts), len(new_texts), 1)
            if (old_texts or new_texts)
            else 0.0
        )
        combined_similarity = max(text_similarity, partial_similarity * 0.7)

        print(
            f"  Line {old_line_id}->{new_line_id}: "
            f"words={len(old_line_words)}->{len(new_line_words)}, "
            f"text_sim={text_similarity:.2f}, "
            f"partial_sim={partial_similarity:.2f}, "
            f"count_ratio={word_count_ratio:.2f}"
        )

        # Only proceed if we have reasonable similarity
        # Lower threshold since OCR can produce different text
        if combined_similarity < 0.1 and word_count_ratio < 0.3:
            print(
                f"  ⚠️  Line {old_line_id}->{new_line_id}: "
                f"Low similarity, skipping word matching"
            )
            continue

        # Sort words by x-coordinate (left to right)
        old_line_words_sorted = sorted(
            old_line_words, key=lambda w: w.calculate_centroid()[0]
        )
        new_line_words_sorted = sorted(
            new_line_words, key=lambda w: w.calculate_centroid()[0]
        )

        # Match words within the line
        used_new_indices = set()
        for old_word in old_line_words_sorted:
            old_text = (old_word.text or "").strip().upper()
            old_centroid = old_word.calculate_centroid()

            best_match = None
            best_match_score = 0.0
            best_match_idx = None

            for idx, new_word in enumerate(new_line_words_sorted):
                if idx in used_new_indices:
                    continue

                new_text = (new_word.text or "").strip().upper()
                new_centroid = new_word.calculate_centroid()

                # Normalize text for comparison
                old_text_norm = normalize_text(old_text)
                new_text_norm = normalize_text(new_text)

                # Text match (exact or partial)
                text_match = 1.0 if old_text_norm == new_text_norm else 0.0
                if not text_match and old_text_norm and new_text_norm:
                    # Partial text match (substring)
                    if (
                        old_text_norm in new_text_norm
                        or new_text_norm in old_text_norm
                    ):
                        text_match = 0.8
                    # Character overlap (fuzzy match for OCR errors)
                    elif len(set(old_text_norm) & set(new_text_norm)) > 0:
                        char_overlap = len(
                            set(old_text_norm) & set(new_text_norm)
                        )
                        char_union = len(
                            set(old_text_norm) | set(new_text_norm)
                        )
                        text_match = (
                            (char_overlap / char_union) * 0.6
                            if char_union > 0
                            else 0.0
                        )

                # Position match (normalized distance)
                x_diff = abs(old_centroid[0] - new_centroid[0])
                y_diff = abs(old_centroid[1] - new_centroid[1])
                position_distance = (x_diff**2 + y_diff**2) ** 0.5
                position_match = max(
                    0.0, 1.0 - position_distance / position_tolerance
                )

                # Combined score (prioritize text match, but lower threshold for OCR variations)
                score = text_match * 0.7 + position_match * 0.3

                if (
                    score > best_match_score and score > 0.3
                ):  # Lower threshold for OCR variations
                    best_match_score = score
                    best_match = new_word
                    best_match_idx = idx

            if best_match:
                word_map[(old_word.line_id, old_word.word_id)] = (
                    best_match.line_id,
                    best_match.word_id,
                )
                used_new_indices.add(best_match_idx)
                print(
                    f"    ✓ '{old_word.text}' (L{old_word.line_id}W{old_word.word_id}) -> "
                    f"'{best_match.text}' (L{best_match.line_id}W{best_match.word_id}) "
                    f"[score={best_match_score:.2f}]"
                )
            else:
                print(
                    f"    ⚠️  No match: '{old_word.text}' (L{old_word.line_id}W{old_word.word_id})"
                )

    return word_map


def map_labels_to_new_words(
    old_labels: List[ReceiptWordLabel],
    word_map: Dict[Tuple[int, int], Tuple[int, int]],
    new_receipt_id: int,
) -> List[ReceiptWordLabel]:
    """Map ReceiptWordLabels from old words to new words."""
    new_labels = []
    from datetime import datetime, timezone

    for label in old_labels:
        old_key = (label.line_id, label.word_id)
        new_key = word_map.get(old_key)

        if new_key is None:
            print(
                f"  ⚠️  Label '{label.label}' for word (L{label.line_id}W{label.word_id}) has no match"
            )
            continue

        new_line_id, new_word_id = new_key
        new_label = ReceiptWordLabel(
            image_id=label.image_id,
            receipt_id=new_receipt_id,
            line_id=new_line_id,
            word_id=new_word_id,
            label=label.label,
            reasoning=(label.reasoning or "")
            + f" (Mapped from old L{label.line_id}W{label.word_id} via re-OCR)",
            timestamp_added=datetime.now(timezone.utc),
            validation_status=label.validation_status,
            label_proposed_by=label.label_proposed_by or "re_ocr_mapping",
            label_consolidated_from=f"receipt_{label.receipt_id}_word_{label.word_id}",
        )
        new_labels.append(new_label)

    return new_labels


def visualize_ocr_comparison(
    old_words: List,
    new_words_list: List,
    word_map: Dict[Tuple[int, int], Tuple[int, int]],
    old_labels: List[ReceiptWordLabel],
    new_labels: List[ReceiptWordLabel],
    receipt_image: PIL_Image.Image,
    output_path: Path,
) -> None:
    """Create a visualization comparing old OCR, new OCR, and label mappings."""
    if not PIL_AVAILABLE:
        print("⚠️  Cannot create visualization - PIL not available")
        return

    img = receipt_image.copy()
    draw = ImageDraw.Draw(img)

    try:
        from PIL import ImageFont

        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 12)
        small_font = ImageFont.truetype(
            "/System/Library/Fonts/Helvetica.ttc", 10
        )
    except:
        font = ImageFont.load_default()
        small_font = ImageFont.load_default()

    receipt_width = img.width
    receipt_height = img.height

    # Draw old words in red
    for word in old_words:
        tl_x = word.top_left["x"] * receipt_width
        tl_y = (1 - word.top_left["y"]) * receipt_height
        tr_x = word.top_right["x"] * receipt_width
        tr_y = (1 - word.top_right["y"]) * receipt_height
        br_x = word.bottom_right["x"] * receipt_width
        br_y = (1 - word.bottom_right["y"]) * receipt_height
        bl_x = word.bottom_left["x"] * receipt_width
        bl_y = (1 - word.bottom_left["y"]) * receipt_height

        corners = [(tl_x, tl_y), (tr_x, tr_y), (br_x, br_y), (bl_x, bl_y)]
        draw.polygon(corners, outline="red", width=2)

    # Draw new words in green
    for word in new_words_list:
        tl_x = word.top_left["x"] * receipt_width
        tl_y = (1 - word.top_left["y"]) * receipt_height
        tr_x = word.top_right["x"] * receipt_width
        tr_y = (1 - word.top_right["y"]) * receipt_height
        br_x = word.bottom_right["x"] * receipt_width
        br_y = (1 - word.bottom_right["y"]) * receipt_height
        bl_x = word.bottom_left["x"] * receipt_width
        bl_y = (1 - word.bottom_left["y"]) * receipt_height

        corners = [(tl_x, tl_y), (tr_x, tr_y), (br_x, br_y), (bl_x, bl_y)]
        draw.polygon(corners, outline="green", width=2)

    # Draw mapping lines for matched words
    for (old_line_id, old_word_id), (
        new_line_id,
        new_word_id,
    ) in word_map.items():
        old_word = next(
            (
                w
                for w in old_words
                if w.line_id == old_line_id and w.word_id == old_word_id
            ),
            None,
        )
        new_word = next(
            (
                w
                for w in new_words_list
                if w.line_id == new_line_id and w.word_id == new_word_id
            ),
            None,
        )

        if old_word and new_word:
            old_centroid = old_word.calculate_centroid()
            new_centroid = new_word.calculate_centroid()

            old_x = old_centroid[0] * receipt_width
            old_y = (1 - old_centroid[1]) * receipt_height
            new_x = new_centroid[0] * receipt_width
            new_y = (1 - new_centroid[1]) * receipt_height

            # Draw line connecting matched words
            draw.line([(old_x, old_y), (new_x, new_y)], fill="blue", width=1)

    # Draw labels on new words
    for label in new_labels:
        new_word = next(
            (
                w
                for w in new_words_list
                if w.line_id == label.line_id and w.word_id == label.word_id
            ),
            None,
        )
        if new_word:
            centroid = new_word.calculate_centroid()
            x = centroid[0] * receipt_width
            y = (1 - centroid[1]) * receipt_height

            # Draw label text
            draw.text(
                (x + 5, y - 15),
                label.label,
                fill="purple",
                font=small_font,
            )

    # Add legend
    legend_y = 10
    draw.rectangle(
        [(10, legend_y), (200, legend_y + 80)], fill="white", outline="black"
    )
    draw.text((20, legend_y + 5), "Old OCR (Red)", fill="red", font=small_font)
    draw.text(
        (20, legend_y + 20), "New OCR (Green)", fill="green", font=small_font
    )
    draw.text(
        (20, legend_y + 35), "Mappings (Blue)", fill="blue", font=small_font
    )
    draw.text(
        (20, legend_y + 50),
        f"Matched: {len(word_map)}",
        fill="black",
        font=small_font,
    )
    draw.text(
        (20, legend_y + 65),
        f"Labels: {len(new_labels)}",
        fill="purple",
        font=small_font,
    )

    img.save(output_path)
    print(f"      💾 Saved comparison visualization: {output_path}")


def load_receipt_ocr_from_json(
    json_path: Path,
    image_id: str,
    original_receipt_id: int,
) -> Tuple[List, List, List, float, float]:
    """
    Load ReceiptLines, ReceiptWords, and ReceiptLetters from JSON export.

    The JSON export contains receipt-level OCR data with coordinates in
    warped receipt space (absolute pixels). We normalize them to 0-1 space
    to match the new OCR coordinate system.

    Returns:
        Tuple of (lines, words, letters, warped_width, warped_height)
    """
    from receipt_dynamo.entities import ReceiptLetter, ReceiptLine, ReceiptWord

    with open(json_path, "r") as f:
        data = json.load(f)

    # Get warped receipt dimensions for normalization
    warped_width = data.get("warped_width", 1.0)
    warped_height = data.get("warped_height", 1.0)

    # Handle invalid height (sometimes it's negative or near zero)
    # Calculate actual dimensions from coordinates (warped_receipt_coords are in PIL space, absolute pixels)
    all_x_coords = []
    all_y_coords = []
    for line_data in data.get("lines", []):
        for coord in line_data.get("warped_receipt_coords", []):
            all_x_coords.append(coord.get("x", 0))
            all_y_coords.append(coord.get("y", 0))
    for word_data in data.get("words", []):
        for corner in word_data.get("warped_receipt_coords", []):
            all_x_coords.append(corner.get("x", 0))
            all_y_coords.append(corner.get("y", 0))

    if all_x_coords and all_y_coords:
        actual_width = (
            max(all_x_coords) - min(all_x_coords)
            if all_x_coords
            else warped_width
        )
        actual_height = (
            max(all_y_coords) - min(all_y_coords)
            if all_y_coords
            else warped_height
        )

        # Use actual dimensions if warped dimensions are invalid
        if warped_width <= 0 or abs(warped_width) < 1:
            warped_width = actual_width
        if warped_height <= 0 or abs(warped_height) < 1:
            warped_height = actual_height

        # Ensure we have valid dimensions
        if warped_width <= 0:
            warped_width = 1.0
        if warped_height <= 0:
            warped_height = 1.0
    else:
        # Fallback
        if warped_width <= 0:
            warped_width = 1.0
        if warped_height <= 0:
            warped_height = 1.0

    lines = []
    words = []
    letters = []

    # Reconstruct ReceiptLines
    for line_data in data.get("lines", []):
        # Use warped receipt coordinates (absolute pixels) and normalize to 0-1
        warped_coords = line_data.get("warped_receipt_coords", [])
        if len(warped_coords) >= 4:
            # Normalize coordinates to 0-1 space (OCR space: y=0 at bottom)
            # The warped_receipt_coords are in absolute pixels, PIL space (y=0 at top)
            # We need to convert to normalized OCR space (y=0 at bottom)
            normalized_coords = []
            for coord in warped_coords:
                x_norm = (
                    coord["x"] / warped_width
                    if warped_width > 0
                    else coord["x"]
                )
                # Convert from PIL space (y=0 at top) to OCR space (y=0 at bottom)
                # In PIL: y=0 is top, y=height is bottom
                # In OCR: y=0 is bottom, y=1 is top
                # So: y_ocr = 1 - (y_pil / height)
                y_pil = coord["y"]
                y_norm = (
                    1.0 - (y_pil / warped_height)
                    if warped_height > 0
                    else (1.0 - y_pil)
                )
                normalized_coords.append({"x": x_norm, "y": y_norm})

            # Calculate bounding box from normalized coordinates
            x_coords = [c["x"] for c in normalized_coords]
            y_coords = [c["y"] for c in normalized_coords]

            line = ReceiptLine(
                receipt_id=original_receipt_id,
                image_id=image_id,
                line_id=line_data["line_id"],
                text=line_data.get("text", ""),
                bounding_box={
                    "x": min(x_coords),
                    "y": min(y_coords),
                    "width": max(x_coords) - min(x_coords),
                    "height": max(y_coords) - min(y_coords),
                },
                top_left={
                    "x": normalized_coords[0]["x"],
                    "y": normalized_coords[0]["y"],
                },
                top_right={
                    "x": normalized_coords[1]["x"],
                    "y": normalized_coords[1]["y"],
                },
                bottom_right={
                    "x": normalized_coords[2]["x"],
                    "y": normalized_coords[2]["y"],
                },
                bottom_left={
                    "x": normalized_coords[3]["x"],
                    "y": normalized_coords[3]["y"],
                },
                angle_degrees=0.0,
                angle_radians=0.0,
                confidence=1.0,
            )
            lines.append(line)

    # Reconstruct ReceiptWords
    for word_data in data.get("words", []):
        # Use warped receipt coordinates (absolute pixels) and normalize to 0-1
        corners = word_data.get("warped_receipt_coords", [])
        if len(corners) >= 4:
            # Normalize coordinates to 0-1 space (OCR space: y=0 at bottom)
            # The warped_receipt_coords are in absolute pixels, PIL space (y=0 at top)
            # We need to convert to normalized OCR space (y=0 at bottom)
            normalized_corners = []
            for corner in corners:
                x_norm = (
                    corner["x"] / warped_width
                    if warped_width > 0
                    else corner["x"]
                )
                # Convert from PIL space (y=0 at top) to OCR space (y=0 at bottom)
                # In PIL: y=0 is top, y=height is bottom
                # In OCR: y=0 is bottom, y=1 is top
                # So: y_ocr = 1 - (y_pil / height)
                y_pil = corner["y"]
                y_norm = (
                    1.0 - (y_pil / warped_height)
                    if warped_height > 0
                    else (1.0 - y_pil)
                )
                normalized_corners.append({"x": x_norm, "y": y_norm})

            from receipt_dynamo.entities import (
                ReceiptWord as ReceiptWordEntity,
            )

            word = ReceiptWordEntity(
                receipt_id=original_receipt_id,
                image_id=image_id,
                line_id=word_data["line_id"],
                word_id=word_data["word_id"],
                text=word_data.get("text", ""),
                bounding_box=word_data.get("bounding_box", {}),
                top_left={
                    "x": normalized_corners[0]["x"],
                    "y": normalized_corners[0]["y"],
                },
                top_right={
                    "x": normalized_corners[1]["x"],
                    "y": normalized_corners[1]["y"],
                },
                bottom_right={
                    "x": normalized_corners[2]["x"],
                    "y": normalized_corners[2]["y"],
                },
                bottom_left={
                    "x": normalized_corners[3]["x"],
                    "y": normalized_corners[3]["y"],
                },
                angle_degrees=0.0,
                angle_radians=0.0,
                confidence=1.0,
            )
            words.append(word)

    # Reconstruct ReceiptLetters (if present)
    for letter_data in data.get("letters", []):
        # Similar structure to words
        corners = letter_data.get("warped_receipt_coords", [])
        if len(corners) >= 4:
            from receipt_dynamo.entities import (
                ReceiptLetter as ReceiptLetterEntity,
            )

            letter = ReceiptLetterEntity(
                receipt_id=original_receipt_id,
                image_id=image_id,
                line_id=letter_data["line_id"],
                word_id=letter_data["word_id"],
                letter_id=letter_data["letter_id"],
                text=letter_data.get("text", ""),
                bounding_box=letter_data.get("bounding_box", {}),
                top_left={"x": corners[0]["x"], "y": corners[0]["y"]},
                top_right={"x": corners[1]["x"], "y": corners[1]["y"]},
                bottom_right={"x": corners[2]["x"], "y": corners[2]["y"]},
                bottom_left={"x": corners[3]["x"], "y": corners[3]["y"]},
                angle_degrees=0.0,
                angle_radians=0.0,
                confidence=1.0,
            )
            letters.append(letter)

    return lines, words, letters, warped_width, warped_height


def re_ocr_receipt_and_map_labels(
    image_id: str,
    receipt_id: int,
    new_receipt_id: Optional[int] = None,
    cropped_image_path: Optional[Path] = None,
    ocr_export_json_path: Optional[Path] = None,
    dry_run: bool = True,
    output_dir: Optional[Path] = None,
) -> None:
    """Re-OCR a receipt and map previous labels to new OCR data.

    Args:
        image_id: Image ID
        receipt_id: Original receipt ID (to load labels from)
        new_receipt_id: New receipt ID (for the split receipt)
        cropped_image_path: Path to cropped receipt image
        ocr_export_json_path: Path to JSON export from visualize_final_clusters_cropped.py
            containing accurate receipt-level OCR data for this cluster
        dry_run: If True, don't save to DynamoDB
        output_dir: Directory for output visualizations
    """
    env = setup_environment()
    table_name = env.get("dynamo_table_name") or "ReceiptsTable-dc5be22"
    client = DynamoClient(table_name)

    print("=" * 70)
    print(f"RE-OCR RECEIPT AND MAP LABELS")
    print("=" * 70)
    print(f"Image ID: {image_id}")
    print(f"Receipt ID: {receipt_id}")
    if new_receipt_id:
        print(f"New Receipt ID: {new_receipt_id}")
    print(f"Dry Run: {dry_run}")
    print()

    # Load accurate receipt-level OCR data from JSON export
    if ocr_export_json_path and ocr_export_json_path.exists():
        print("📥 Loading receipt-level OCR data from JSON export...")
        old_lines, old_words, old_letters, warped_width, warped_height = (
            load_receipt_ocr_from_json(
                ocr_export_json_path, image_id, receipt_id
            )
        )
        print(
            f"   Warped receipt dimensions: {warped_width:.0f} x {warped_height:.0f}"
        )
        print(f"   Lines: {len(old_lines)}")
        print(f"   Words: {len(old_words)}")
        print(f"   Letters: {len(old_letters)}")

        # Load labels for these specific words from DynamoDB
        print("📥 Loading labels for these words from DynamoDB...")
        old_labels, _ = client.list_receipt_word_labels_for_receipt(
            image_id, receipt_id
        )
        # Filter to only labels for words that are in our export
        word_keys = {(w.line_id, w.word_id) for w in old_words}
        old_labels = [
            label
            for label in old_labels
            if (label.line_id, label.word_id) in word_keys
        ]
        print(f"   Labels: {len(old_labels)}")
    else:
        # Fallback: load from DynamoDB (less accurate)
        print("📥 Loading receipt data from DynamoDB (fallback)...")
        print("   ⚠️  Using JSON export is recommended for accurate matching")
        old_lines = client.list_receipt_lines_from_receipt(
            image_id, receipt_id
        )
        old_words = client.list_receipt_words_from_receipt(
            image_id, receipt_id
        )
        old_labels, _ = client.list_receipt_word_labels_for_receipt(
            image_id, receipt_id
        )
        print(f"   Lines: {len(old_lines)}")
        print(f"   Words: {len(old_words)}")
        print(f"   Labels: {len(old_labels)}")

    print()

    # Get cropped receipt image
    if not cropped_image_path or not cropped_image_path.exists():
        raise ValueError(
            "Cropped image path required. Please provide --cropped-image-path"
        )

    print(f"📷 Using cropped image: {cropped_image_path}")
    image_path = cropped_image_path

    # Run Swift OCR
    if not OCR_AVAILABLE:
        raise ValueError("receipt_upload not available - cannot run OCR")

    print("🔍 Running Swift OCR...")
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        image_path_obj = (
            Path(image_path) if isinstance(image_path, str) else image_path
        )
        json_files = apple_vision_ocr_job([image_path_obj], temp_path)

        if not json_files:
            raise ValueError("No OCR results generated")

        # Process OCR results
        with open(json_files[0], "r", encoding="utf-8") as f:
            ocr_data = json.load(f)

        new_lines, new_words_list, new_letters = process_ocr_dict_as_image(
            ocr_data, image_id
        )
        # new_words_list is a flat list of Word entities
        # new_lines is a list of Line entities
        print(
            f"   New OCR: {len(new_lines)} lines, {len(new_words_list)} words"
        )
        print()

    # Match words directly (word-first approach for better coverage)
    print("🔗 Matching words directly by text and position...")
    word_map = match_words_directly(old_words, new_words_list)
    print(f"   Matched: {len(word_map)}/{len(old_words)} words")
    print()

    # Determine new receipt ID (get max existing + 1)
    if new_receipt_id is None:
        print("🔍 Finding next available receipt ID...")
        try:
            # Try get_receipts_from_image (even though type says int, it works with string UUIDs)
            existing_receipts = client.get_receipts_from_image(image_id)  # type: ignore
            if existing_receipts:
                max_receipt_id = max(r.receipt_id for r in existing_receipts)
                new_receipt_id = max_receipt_id + 1
                print(
                    f"   Found {len(existing_receipts)} existing receipts, max ID: {max_receipt_id}"
                )
                print(f"   Using new receipt ID: {new_receipt_id}")
            else:
                new_receipt_id = 1
                print(f"   No existing receipts found, using receipt ID: 1")
        except Exception as e:
            print(f"   ⚠️  Could not query existing receipts: {e}")
            print(f"   Using receipt ID: 1000 (safe fallback)")
            new_receipt_id = 1000

    print("🏷️  Mapping labels to new words...")
    new_labels = map_labels_to_new_words(old_labels, word_map, new_receipt_id)
    print(f"   Mapped: {len(new_labels)}/{len(old_labels)} labels")
    print()

    # Load warped receipt image and get dimensions
    warped_image = None
    if PIL_AVAILABLE:
        if cropped_image_path and Path(cropped_image_path).exists():
            warped_image = PIL_Image.open(cropped_image_path)
        else:
            # Try to find clean image from output_dir
            if output_dir:
                clean_path = output_dir / f"receipt_{receipt_id}_clean.png"
                if clean_path.exists():
                    warped_image = PIL_Image.open(clean_path)

    if not warped_image:
        raise ValueError(
            "Warped receipt image required. Please provide --cropped-image-path or ensure clean image exists in output_dir"
        )

    warped_width = warped_image.width
    warped_height = warped_image.height
    print(f"📐 Warped receipt dimensions: {warped_width} x {warped_height}")

    # Get corners from JSON export or calculate from warped image
    # For a warped receipt, corners are: TL=(0,0), TR=(w,0), BR=(w,h), BL=(0,h) in normalized image space
    # But we need to transform back to original image space for the Receipt entity
    # Load JSON export to get box_4_ordered (cluster bounds in original image space)
    receipt_corners = None
    image_entity = client.get_image(image_id)
    image_width = image_entity.width
    image_height = image_entity.height

    if ocr_export_json_path and ocr_export_json_path.exists():
        with open(ocr_export_json_path, "r", encoding="utf-8") as f:
            export_data = json.load(f)
        box_4_ordered = export_data.get("box_4_ordered")
        if box_4_ordered and len(box_4_ordered) == 4:
            # box_4_ordered is in PIL space (absolute pixels, y=0 at top)
            # Convert to normalized OCR space (0-1, y=0 at bottom) for Receipt entity
            receipt_corners = {
                "top_left": {
                    "x": (
                        box_4_ordered[0][0] / image_width
                        if image_width > 0
                        else 0.0
                    ),
                    "y": (
                        1.0 - (box_4_ordered[0][1] / image_height)
                        if image_height > 0
                        else 1.0
                    ),
                },
                "top_right": {
                    "x": (
                        box_4_ordered[1][0] / image_width
                        if image_width > 0
                        else 1.0
                    ),
                    "y": (
                        1.0 - (box_4_ordered[1][1] / image_height)
                        if image_height > 0
                        else 1.0
                    ),
                },
                "bottom_right": {
                    "x": (
                        box_4_ordered[2][0] / image_width
                        if image_width > 0
                        else 1.0
                    ),
                    "y": (
                        1.0 - (box_4_ordered[2][1] / image_height)
                        if image_height > 0
                        else 0.0
                    ),
                },
                "bottom_left": {
                    "x": (
                        box_4_ordered[3][0] / image_width
                        if image_width > 0
                        else 0.0
                    ),
                    "y": (
                        1.0 - (box_4_ordered[3][1] / image_height)
                        if image_height > 0
                        else 0.0
                    ),
                },
            }
            print(f"   ✅ Loaded corners from JSON export")
        else:
            print(
                f"   ⚠️  No valid box_4_ordered in JSON export, using default corners"
            )

    if not receipt_corners:
        # Default: warped receipt fills the entire image (shouldn't happen, but fallback)
        receipt_corners = {
            "top_left": {"x": 0.0, "y": 1.0},
            "top_right": {"x": 1.0, "y": 1.0},
            "bottom_right": {"x": 1.0, "y": 0.0},
            "bottom_left": {"x": 0.0, "y": 0.0},
        }
        print(f"   ⚠️  Using default corners (full image)")

    # Get buckets from environment or image entity
    raw_bucket = env.get("raw_bucket") or image_entity.raw_s3_bucket or ""
    site_bucket = env.get("site_bucket") or image_entity.cdn_s3_bucket or ""
    artifacts_bucket = env.get("artifacts_bucket") or ""
    chromadb_bucket = env.get("chromadb_bucket") or ""

    if not raw_bucket or not site_bucket:
        raise ValueError(
            f"Missing buckets: raw_bucket={raw_bucket}, site_bucket={site_bucket}"
        )

    print(f"📦 Buckets: raw={raw_bucket}, site={site_bucket}")
    if artifacts_bucket:
        print(f"   artifacts={artifacts_bucket}")
    if chromadb_bucket:
        print(f"   chromadb={chromadb_bucket}")

    # Convert OCR entities to Receipt entities
    print("🔄 Converting OCR entities to Receipt entities...")
    if not image_ocr_to_receipt_ocr:
        raise ValueError("image_ocr_to_receipt_ocr not available")

    # Ensure new_receipt_id is not None
    assert new_receipt_id is not None, "new_receipt_id must be set"

    receipt_lines, receipt_words, receipt_letters = image_ocr_to_receipt_ocr(
        new_lines, new_words_list, new_letters, new_receipt_id
    )
    print(
        f"   Converted: {len(receipt_lines)} lines, {len(receipt_words)} words, {len(receipt_letters)} letters"
    )

    # Upload images to S3 (skip in dry-run)
    if not dry_run:
        print("📤 Uploading images to S3...")
        raw_s3_key = f"raw/{image_id}_RECEIPT_{new_receipt_id:05d}.png"
        upload_png_to_s3(warped_image, raw_bucket, raw_s3_key)
        print(f"   ✅ Uploaded raw image: s3://{raw_bucket}/{raw_s3_key}")

        # Upload CDN formats
        cdn_base_key = f"assets/{image_id}_RECEIPT_{new_receipt_id:05d}"
        cdn_keys = upload_all_cdn_formats(
            warped_image, site_bucket, cdn_base_key, generate_thumbnails=True
        )
        print(
            f"   ✅ Uploaded CDN formats: {len([k for k in cdn_keys.values() if k])} variants"
        )

        # Calculate SHA256
        sha256 = hashlib.sha256(warped_image.tobytes()).hexdigest()
    else:
        print("🔍 Dry run: skipping S3 uploads")
        raw_s3_key = ""
        cdn_keys = {}
        sha256 = ""

    # Create Receipt entity
    print("📝 Creating Receipt entity...")
    # Ensure new_receipt_id is not None
    assert new_receipt_id is not None, "new_receipt_id must be set"

    receipt = Receipt(
        image_id=image_id,
        receipt_id=new_receipt_id,
        width=warped_width,
        height=warped_height,
        timestamp_added=datetime.now(timezone.utc),
        raw_s3_bucket=raw_bucket,
        raw_s3_key=raw_s3_key,
        top_left=receipt_corners["top_left"],
        top_right=receipt_corners["top_right"],
        bottom_left=receipt_corners["bottom_left"],
        bottom_right=receipt_corners["bottom_right"],
        sha256=sha256,
        cdn_s3_bucket=site_bucket,
        cdn_s3_key=cdn_keys.get("jpeg"),
        cdn_webp_s3_key=cdn_keys.get("webp"),
        cdn_avif_s3_key=cdn_keys.get("avif"),
        cdn_thumbnail_s3_key=cdn_keys.get("jpeg_thumbnail"),
        cdn_thumbnail_webp_s3_key=cdn_keys.get("webp_thumbnail"),
        cdn_thumbnail_avif_s3_key=cdn_keys.get("avif_thumbnail"),
        cdn_small_s3_key=cdn_keys.get("jpeg_small"),
        cdn_small_webp_s3_key=cdn_keys.get("webp_small"),
        cdn_small_avif_s3_key=cdn_keys.get("avif_small"),
        cdn_medium_s3_key=cdn_keys.get("jpeg_medium"),
        cdn_medium_webp_s3_key=cdn_keys.get("webp_medium"),
        cdn_medium_avif_s3_key=cdn_keys.get("avif_medium"),
    )
    print(f"   ✅ Created Receipt entity (ID: {new_receipt_id})")

    # Create visualization comparing old and new OCR
    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)
        comparison_path = (
            output_dir / f"receipt_{new_receipt_id}_ocr_comparison.png"
        )

        if PIL_AVAILABLE and warped_image:
            visualize_ocr_comparison(
                old_words,
                new_words_list,
                word_map,
                old_labels,
                new_labels,
                warped_image,
                comparison_path,
            )

    # Export all entities to JSON for review
    export_data = {
        "image_id": image_id,
        "original_receipt_id": receipt_id,
        "new_receipt_id": new_receipt_id,
        "warped_width": warped_width,
        "warped_height": warped_height,
        "receipt": {
            "image_id": receipt.image_id,
            "receipt_id": receipt.receipt_id,
            "width": receipt.width,
            "height": receipt.height,
            "raw_s3_bucket": receipt.raw_s3_bucket,
            "raw_s3_key": receipt.raw_s3_key,
            "top_left": receipt.top_left,
            "top_right": receipt.top_right,
            "bottom_left": receipt.bottom_left,
            "bottom_right": receipt.bottom_right,
            "sha256": receipt.sha256,
            "cdn_s3_bucket": receipt.cdn_s3_bucket,
            "cdn_s3_key": receipt.cdn_s3_key,
            "cdn_webp_s3_key": receipt.cdn_webp_s3_key,
            "cdn_avif_s3_key": receipt.cdn_avif_s3_key,
        },
        "receipt_lines": [
            {
                "image_id": line.image_id,
                "receipt_id": line.receipt_id,
                "line_id": line.line_id,
                "text": line.text,
                "bounding_box": line.bounding_box,
                "top_left": line.top_left,
                "top_right": line.top_right,
                "bottom_left": line.bottom_left,
                "bottom_right": line.bottom_right,
            }
            for line in receipt_lines
        ],
        "receipt_words": [
            {
                "image_id": word.image_id,
                "receipt_id": word.receipt_id,
                "line_id": word.line_id,
                "word_id": word.word_id,
                "text": word.text,
                "bounding_box": word.bounding_box,
                "top_left": word.top_left,
                "top_right": word.top_right,
                "bottom_left": word.bottom_left,
                "bottom_right": word.bottom_right,
            }
            for word in receipt_words
        ],
        "receipt_letters": [
            {
                "image_id": letter.image_id,
                "receipt_id": letter.receipt_id,
                "line_id": letter.line_id,
                "word_id": letter.word_id,
                "letter_id": letter.letter_id,
                "text": letter.text,
                "bounding_box": letter.bounding_box,
                "top_left": letter.top_left,
                "top_right": letter.top_right,
                "bottom_left": letter.bottom_left,
                "bottom_right": letter.bottom_right,
            }
            for letter in receipt_letters
        ],
        "receipt_word_labels": [
            {
                "image_id": label.image_id,
                "receipt_id": label.receipt_id,
                "line_id": label.line_id,
                "word_id": label.word_id,
                "label": label.label,
                "reasoning": label.reasoning,
                "validation_status": label.validation_status,
            }
            for label in new_labels
        ],
    }

    # Save JSON export
    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)
        json_path = (
            output_dir / f"receipt_{new_receipt_id}_entities_export.json"
        )
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(export_data, f, indent=2, default=str)
        print(f"💾 Exported entities to: {json_path}")

    # Save to DynamoDB (if not dry_run)
    if not dry_run:
        print("💾 Saving entities to DynamoDB...")
        client.add_receipt(receipt)
        client.add_receipt_lines(receipt_lines)
        client.add_receipt_words(receipt_words)
        if receipt_letters:
            client.add_receipt_letters(receipt_letters)
        print(
            f"   ✅ Saved Receipt, {len(receipt_lines)} lines, {len(receipt_words)} words, {len(receipt_letters)} letters"
        )

        # Export NDJSON to S3 (if artifacts_bucket is available)
        if artifacts_bucket:
            print("📤 Exporting NDJSON to S3...")
            try:
                from receipt_agent.receipt_agent.lifecycle.ndjson_manager import (
                    export_receipt_ndjson,
                )

                export_receipt_ndjson(
                    client=client,
                    artifacts_bucket=artifacts_bucket,
                    image_id=image_id,
                    receipt_id=new_receipt_id,
                    receipt_lines=receipt_lines,
                    receipt_words=receipt_words,
                )
                print("   ✅ NDJSON exported")
            except ImportError as e:
                print(f"   ⚠️  Could not import export_receipt_ndjson: {e}")
            except Exception as e:
                print(f"   ⚠️  Error exporting NDJSON: {e}")
                import traceback

                traceback.print_exc()

        # Create embeddings and CompactionRun (if chromadb_bucket is available)
        if chromadb_bucket:
            print("🧠 Creating embeddings and CompactionRun...")
            try:
                from receipt_agent.receipt_agent.lifecycle.embedding_manager import (
                    create_embeddings,
                )

                compaction_run_id = create_embeddings(
                    client=client,
                    chromadb_bucket=chromadb_bucket,
                    image_id=image_id,
                    receipt_id=new_receipt_id,
                    receipt_lines=receipt_lines,
                    receipt_words=receipt_words,
                    merchant_name=None,  # Could be extracted from receipt metadata if available
                )
                if compaction_run_id:
                    print(
                        f"   ✅ Created embeddings and CompactionRun: {compaction_run_id}"
                    )
                else:
                    print(
                        "   ⚠️  Embedding creation skipped (not available or failed)"
                    )
            except ImportError as e:
                print(f"   ⚠️  Could not import create_embeddings: {e}")
            except Exception as e:
                print(f"   ⚠️  Error creating embeddings: {e}")
                import traceback

                traceback.print_exc()

        if new_labels:
            saved_count = 0
            failed_count = 0

            # Chunk into batches of 25 (DynamoDB limit)
            chunk_size = 25
            for i in range(0, len(new_labels), chunk_size):
                chunk = new_labels[i : i + chunk_size]
                try:
                    client.add_receipt_word_labels(chunk)
                    saved_count += len(chunk)
                    print(f"   ✅ Saved batch of {len(chunk)} labels")
                except Exception as e:
                    print(
                        f"   ⚠️ Bulk add failed for chunk of {len(chunk)}: {e}"
                    )
                    print("   🔄 Attempting individual adds for this chunk...")
                    for label in chunk:
                        try:
                            client.add_receipt_word_label(label)
                            saved_count += 1
                        except Exception as individual_error:
                            print(
                                f"   ⚠️ Failed to add label {label.label} for word (L{label.line_id}W{label.word_id}): {individual_error}"
                            )
                            failed_count += 1

            print(f"   ✅ Saved {saved_count}/{len(new_labels)} labels")
            if failed_count > 0:
                print(f"   ⚠️ Failed to save {failed_count} labels")
        else:
            print("   ℹ️  No labels to save")
    else:
        print("🔍 DRY RUN - Would save:")
        print(f"   - Receipt entity (ID: {new_receipt_id})")
        print(f"   - {len(receipt_lines)} ReceiptLine entities")
        print(f"   - {len(receipt_words)} ReceiptWord entities")
        print(f"   - {len(receipt_letters)} ReceiptLetter entities")
        print(f"   - {len(new_labels)} ReceiptWordLabel entities")
        print()
        print("   First 10 labels:")
        for label in new_labels[:10]:
            print(
                f"     - {label.label} -> (L{label.line_id}W{label.word_id}) '{label.reasoning[:50] if label.reasoning else ''}...'"
            )
        if len(new_labels) > 10:
            print(f"     ... and {len(new_labels) - 10} more")

    print()
    print("✅ Complete!")


def main():
    parser = argparse.ArgumentParser(
        description="Re-OCR a receipt and map previous labels to new OCR data"
    )
    parser.add_argument("--image-id", required=True, help="Image ID")
    parser.add_argument(
        "--receipt-id", type=int, required=True, help="Receipt ID"
    )
    parser.add_argument(
        "--new-receipt-id",
        type=int,
        help="New receipt ID (if different from receipt-id)",
    )
    parser.add_argument(
        "--cropped-image-path",
        type=Path,
        help="Path to cropped receipt image (if not provided, will download and crop)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Dry run mode (default: True)",
    )
    parser.add_argument(
        "--no-dry-run",
        action="store_false",
        dest="dry_run",
        help="Actually save to DynamoDB",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Output directory for visualizations",
    )
    parser.add_argument(
        "--ocr-export-json",
        type=Path,
        help="Path to JSON export from visualize_final_clusters_cropped.py",
    )

    args = parser.parse_args()

    re_ocr_receipt_and_map_labels(
        args.image_id,
        args.receipt_id,
        args.new_receipt_id,
        args.cropped_image_path,
        args.ocr_export_json,
        args.dry_run,
        args.output_dir,
    )


if __name__ == "__main__":
    main()
