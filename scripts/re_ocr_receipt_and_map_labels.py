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
import json
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

repo_root = Path(__file__).parent.parent
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "receipt_upload"))

from receipt_dynamo import DynamoClient
from receipt_dynamo.entities import ReceiptWordLabel
from scripts.split_receipt import setup_environment

try:
    from receipt_upload.ocr import apple_vision_ocr_job, process_ocr_dict_as_image
    from receipt_upload.utils.s3_utils import get_image_from_s3
    from PIL import Image as PIL_Image

    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    print("⚠️  PIL or receipt_upload not available")


def match_words_by_text_and_position(
    old_words: List,
    new_words: List,
    position_tolerance: float = 0.1,
) -> Dict[Tuple[int, int], Tuple[int, int]]:
    """
    Match old ReceiptWords to new OCR words using text and position.

    Returns:
        Dict mapping (old_line_id, old_word_id) -> (new_line_id, new_word_id)
    """
    word_map = {}

    # Group words by line
    old_words_by_line: Dict[int, List] = {}
    for word in old_words:
        old_words_by_line.setdefault(word.line_id, []).append(word)

    new_words_by_line: Dict[int, List] = {}
    for word in new_words:
        new_words_by_line.setdefault(word.line_id, []).append(word)

    # Match within each line
    for old_line_id, old_line_words in old_words_by_line.items():
        # Try to find matching line in new OCR
        best_line_match = None
        best_match_score = 0

        for new_line_id, new_line_words in new_words_by_line.items():
            # Count text matches between lines
            old_texts = {w.text.strip().upper() for w in old_line_words}
            new_texts = {w.text.strip().upper() for w in new_line_words}
            match_score = len(old_texts & new_texts) / max(len(old_texts), len(new_texts), 1)
            if match_score > best_match_score:
                best_match_score = match_score
                best_line_match = new_line_id

        if best_line_match is None or best_match_score < 0.3:
            print(f"  ⚠️  No good line match for old line_id={old_line_id} (best_score={best_match_score:.2f})")
            continue

        # Match words within the matched lines
        new_line_words = new_words_by_line[best_line_match]
        old_line_words_sorted = sorted(old_line_words, key=lambda w: w.calculate_centroid()[0])
        new_line_words_sorted = sorted(new_line_words, key=lambda w: w.calculate_centroid()[0])

        # Try to match words by text and position
        for old_word in old_line_words_sorted:
            old_text = (old_word.text or "").strip().upper()
            old_centroid = old_word.calculate_centroid()

            best_match = None
            best_match_score = 0

            for new_word in new_line_words_sorted:
                new_text = (new_word.text or "").strip().upper()
                new_centroid = new_word.calculate_centroid()

                # Text match
                text_match = 1.0 if old_text == new_text else 0.0
                if not text_match and old_text and new_text:
                    # Partial text match
                    if old_text in new_text or new_text in old_text:
                        text_match = 0.5

                # Position match (normalized distance)
                x_diff = abs(old_centroid[0] - new_centroid[0])
                y_diff = abs(old_centroid[1] - new_centroid[1])
                position_match = 1.0 - min(x_diff + y_diff, position_tolerance * 2) / (position_tolerance * 2)

                # Combined score
                score = text_match * 0.7 + position_match * 0.3

                if score > best_match_score and score > 0.5:
                    best_match_score = score
                    best_match = new_word

            if best_match:
                word_map[(old_word.line_id, old_word.word_id)] = (
                    best_match.line_id,
                    best_match.word_id,
                )
                print(
                    f"    Matched: '{old_word.text}' (L{old_word.line_id}W{old_word.word_id}) -> "
                    f"'{best_match.text}' (L{best_match.line_id}W{best_match.word_id}) [score={best_match_score:.2f}]"
                )
            else:
                print(
                    f"    ⚠️  No match for: '{old_word.text}' (L{old_word.line_id}W{old_word.word_id})"
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
            reasoning=(
                label.reasoning or ""
            ) + f" (Mapped from old L{label.line_id}W{label.word_id} via re-OCR)",
            timestamp_added=datetime.now(timezone.utc),
            validation_status=label.validation_status,
            label_proposed_by=label.label_proposed_by or "re_ocr_mapping",
            label_consolidated_from=f"receipt_{label.receipt_id}_word_{label.word_id}",
        )
        new_labels.append(new_label)

    return new_labels


def re_ocr_receipt_and_map_labels(
    image_id: str,
    receipt_id: int,
    new_receipt_id: Optional[int] = None,
    cropped_image_path: Optional[Path] = None,
    dry_run: bool = True,
) -> None:
    """Re-OCR a receipt and map previous labels to new OCR data."""
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

    # Load old receipt data
    print("📥 Loading old receipt data...")
    receipt = client.get_receipt(image_id, receipt_id)
    old_words = client.list_receipt_words_from_receipt(image_id, receipt_id)
    old_labels, _ = client.list_receipt_word_labels_for_receipt(image_id, receipt_id)

    print(f"   Old words: {len(old_words)}")
    print(f"   Old labels: {len(old_labels)}")
    print()

    # Get cropped receipt image
    if cropped_image_path and cropped_image_path.exists():
        print(f"📷 Using existing cropped image: {cropped_image_path}")
        image_path = cropped_image_path
    else:
        print("📷 Downloading and cropping receipt image...")
        # Download original image
        image_entity = client.get_image(image_id)
        raw_bucket = image_entity.raw_s3_bucket or env.get("raw_bucket")
        s3_key = image_entity.raw_s3_key or f"raw/{image_id}.png"

        original_image = get_image_from_s3(raw_bucket, s3_key)
        print(f"   Downloaded image: {original_image.size}")

        # Crop receipt region (simplified - would need proper transform)
        # For now, assume we have the cropped image
        raise ValueError(
            "Cropped image path required. Please provide --cropped-image-path"
        )

    # Run Swift OCR
    print("🔍 Running Swift OCR...")
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        json_files = apple_vision_ocr_job([str(image_path)], temp_path)

        if not json_files:
            raise ValueError("No OCR results generated")

        # Process OCR results
        with open(json_files[0], "r") as f:
            ocr_data = json.load(f)

        new_lines, new_words, new_letters = process_ocr_dict_as_image(
            ocr_data, image_id
        )
        print(f"   New OCR: {len(new_lines)} lines, {len(new_words)} words")
        print()

    # Match old words to new words
    print("🔗 Matching old words to new OCR words...")
    word_map = match_words_by_text_and_position(old_words, new_words)
    print(f"   Matched: {len(word_map)}/{len(old_words)} words")
    print()

    # Map labels
    if new_receipt_id is None:
        new_receipt_id = receipt_id

    print("🏷️  Mapping labels to new words...")
    new_labels = map_labels_to_new_words(old_labels, word_map, new_receipt_id)
    print(f"   Mapped: {len(new_labels)}/{len(old_labels)} labels")
    print()

    # Save results
    if not dry_run:
        print("💾 Saving new labels to DynamoDB...")
        for label in new_labels:
            client.put_receipt_word_label(label)
        print(f"   ✅ Saved {len(new_labels)} labels")
    else:
        print("🔍 DRY RUN - Would save:")
        for label in new_labels[:10]:  # Show first 10
            print(
                f"   - {label.label} -> (L{label.line_id}W{label.word_id}) '{label.reasoning[:50]}...'"
            )
        if len(new_labels) > 10:
            print(f"   ... and {len(new_labels) - 10} more")

    print()
    print("✅ Complete!")


def main():
    parser = argparse.ArgumentParser(
        description="Re-OCR a receipt and map previous labels to new OCR data"
    )
    parser.add_argument("--image-id", required=True, help="Image ID")
    parser.add_argument("--receipt-id", type=int, required=True, help="Receipt ID")
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

    args = parser.parse_args()

    re_ocr_receipt_and_map_labels(
        args.image_id,
        args.receipt_id,
        args.new_receipt_id,
        args.cropped_image_path,
        args.dry_run,
    )


if __name__ == "__main__":
    main()

