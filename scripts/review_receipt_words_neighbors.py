#!/usr/bin/env python3
"""
Review left/right neighbors for words in receipt export files.

This script loads exported OCR data (including ReceiptWords and ReceiptLetters)
and shows the left/right neighbors for each word using the line-aware algorithm.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

repo_root = Path(__file__).parent.parent
sys.path.insert(0, str(repo_root))

# Import directly from file to avoid chromadb dependency
import importlib.util
spec = importlib.util.spec_from_file_location(
    "word_format",
    repo_root / "receipt_chroma" / "receipt_chroma" / "embedding" / "formatting" / "word_format.py"
)
word_format = importlib.util.module_from_spec(spec)
spec.loader.exec_module(word_format)
get_word_neighbors = word_format.get_word_neighbors


class MockReceiptWord:
    """Mock ReceiptWord for use with exported data."""

    def __init__(self, word_data: Dict[str, Any], receipt_id: int, image_id: str):
        self.image_id = image_id
        self.receipt_id = receipt_id
        self.line_id = word_data["line_id"]
        self.word_id = word_data["word_id"]
        self.text = word_data.get("text", "")
        self.bounding_box = word_data.get("bounding_box", {})
        self.top_left = word_data.get("corners", {}).get("top_left", {})
        self.top_right = word_data.get("corners", {}).get("top_right", {})
        self.bottom_left = word_data.get("corners", {}).get("bottom_left", {})
        self.bottom_right = word_data.get("corners", {}).get("bottom_right", {})

    def calculate_centroid(self):
        """Calculate centroid from bounding box."""
        x = self.bounding_box.get("x", 0) + self.bounding_box.get("width", 0) / 2
        y = self.bounding_box.get("y", 0) + self.bounding_box.get("height", 0) / 2
        return (x, y)


def load_receipt_words_from_export(export_path: Path) -> List[MockReceiptWord]:
    """Load ReceiptWords from export JSON file."""
    with open(export_path, "r") as f:
        export_data = json.load(f)

    image_id = export_data["image_id"]
    receipt_id = export_data["cluster_id"]  # Use cluster_id as receipt_id

    words = []
    for word_data in export_data.get("words", []):
        word = MockReceiptWord(word_data, receipt_id, image_id)
        words.append(word)

    return words


def review_word_neighbors(
    export_path: Path, target_words: List[str] = None
) -> None:
    """Review left/right neighbors for words in the export file."""
    print("=" * 70)
    print(f"REVIEWING WORDS FROM: {export_path.name}")
    print("=" * 70)
    print()

    # Load words
    words = load_receipt_words_from_export(export_path)
    print(f"Loaded {len(words)} words from export")
    print()

    # Filter to target words if specified
    if target_words:
        words_to_review = [
            w for w in words if any(keyword.lower() in (w.text or "").lower() for keyword in target_words)
        ]
        if not words_to_review:
            print(f"⚠️  No words found matching: {target_words}")
            return
    else:
        # Review all words (or show a sample)
        words_to_review = words[:20]  # Limit to first 20 if not specified
        if len(words) > 20:
            print(f"Showing first 20 words (total: {len(words)})")
            print("Use --target-words to review specific words")
            print()

    # Review each word
    for word in words_to_review:
        target_centroid = word.calculate_centroid()
        print(f'Word: "{word.text}"')
        print(f"  Position: line_id={word.line_id}, word_id={word.word_id}")
        print(f"  Centroid: x={target_centroid[0]:.4f}, y={target_centroid[1]:.4f}")
        print()

        # Get neighbors
        left_words, right_words = get_word_neighbors(word, words, context_size=2)

        print("  Left neighbors:")
        if left_words:
            for i, word_text in enumerate(left_words):
                # Find the word to show details
                candidates = [
                    w
                    for w in words
                    if w.text == word_text
                    and w.receipt_id == word.receipt_id
                ]
                if candidates:
                    closest = min(
                        candidates,
                        key=lambda w: abs(
                            w.calculate_centroid()[1] - target_centroid[1]
                        ),
                    )
                    c = closest.calculate_centroid()
                    y_diff = abs(c[1] - target_centroid[1])
                    x_diff = target_centroid[0] - c[0]
                    same_line = (
                        " (SAME LINE)"
                        if closest.line_id == word.line_id
                        else ""
                    )
                    print(
                        f"    [{i+1}] \"{word_text}\" (line_id={closest.line_id}, "
                        f"x_diff={x_diff:.4f}, y_diff={y_diff:.4f}){same_line}"
                    )
                else:
                    print(f"    [{i+1}] \"{word_text}\" (not found in receipt)")
        else:
            print("    (none - will use <EDGE>)")

        print("  Right neighbors:")
        if right_words:
            for i, word_text in enumerate(right_words):
                candidates = [
                    w
                    for w in words
                    if w.text == word_text
                    and w.receipt_id == word.receipt_id
                ]
                if candidates:
                    closest = min(
                        candidates,
                        key=lambda w: abs(
                            w.calculate_centroid()[1] - target_centroid[1]
                        ),
                    )
                    c = closest.calculate_centroid()
                    y_diff = abs(c[1] - target_centroid[1])
                    x_diff = c[0] - target_centroid[0]
                    same_line = (
                        " (SAME LINE)"
                        if closest.line_id == word.line_id
                        else ""
                    )
                    print(
                        f"    [{i+1}] \"{word_text}\" (line_id={closest.line_id}, "
                        f"x_diff={x_diff:.4f}, y_diff={y_diff:.4f}){same_line}"
                    )
                else:
                    print(f"    [{i+1}] \"{word_text}\" (not found in receipt)")
        else:
            print("    (none - will use <EDGE>)")

        print()
        print("-" * 70)
        print()


def main():
    parser = argparse.ArgumentParser(
        description="Review left/right neighbors for words in receipt export files"
    )
    parser.add_argument(
        "export_path",
        type=Path,
        help="Path to receipt OCR export JSON file",
    )
    parser.add_argument(
        "--target-words",
        nargs="+",
        help="Specific words to review (e.g., '2.28N' '10.98' 'CANDY')",
    )

    args = parser.parse_args()

    if not args.export_path.exists():
        print(f"❌ Export file not found: {args.export_path}")
        sys.exit(1)

    review_word_neighbors(args.export_path, args.target_words)


if __name__ == "__main__":
    main()

