#!/usr/bin/env python3
"""
Inspect a specific receipt to understand why pattern coverage is low.
"""

import asyncio
import json
from pathlib import Path

from receipt_label.data.local_data_loader import (
    create_mock_receipt_from_export,
)
from receipt_label.pattern_detection import ParallelPatternOrchestrator


async def inspect_receipt(receipt_file: str):
    """Inspect patterns in a specific receipt."""

    print(f"🔍 INSPECTING RECEIPT: {receipt_file}")
    print("=" * 70)

    # Load receipt data
    with open(receipt_file, "r") as f:
        export_data = json.load(f)

    # Get the first receipt
    receipts = export_data.get("receipts", [])
    receipt_words = export_data.get("receipt_words", [])
    receipt_lines = export_data.get("receipt_lines", [])

    receipt = receipts[0]
    image_id = receipt["image_id"]
    receipt_id = receipt["receipt_id"]

    # Filter words for this receipt
    words_for_receipt = [
        w
        for w in receipt_words
        if w["image_id"] == image_id and w["receipt_id"] == receipt_id
    ]

    # Create receipt objects
    receipt_obj, words, lines = create_mock_receipt_from_export(
        {
            "receipt": receipt,
            "words": words_for_receipt,
            "lines": receipt_lines,
        }
    )

    print(f"📋 Receipt ID: {receipt_id}")
    print(f"📊 Total words: {len(words)}")

    # Show first 20 words to understand content
    print(f"\n📝 FIRST 20 WORDS:")
    for i, word in enumerate(words[:20]):
        print(f"   {i+1:2d}. '{word.text}' (noise={word.is_noise})")

    # Run pattern detection
    print(f"\n🔬 RUNNING PATTERN DETECTION...")
    orchestrator = ParallelPatternOrchestrator()
    pattern_results = await orchestrator.detect_all_patterns(words)

    # Show detailed results
    print(f"\n📊 PATTERN DETECTION RESULTS:")
    total_patterns = 0

    for detector_name, results in pattern_results.items():
        if detector_name == "_metadata":
            continue
        if isinstance(results, list) and len(results) > 0:
            print(f"\n{detector_name.upper()} ({len(results)} patterns):")
            for i, pattern in enumerate(results[:5]):  # Show first 5
                if hasattr(pattern, "word") and hasattr(
                    pattern, "extracted_value"
                ):
                    print(
                        f"   '{pattern.word.text}' → '{pattern.extracted_value}'"
                    )
            if len(results) > 5:
                print(f"   ... and {len(results) - 5} more")
            total_patterns += len(results)

    print(f"\n📈 Total patterns found: {total_patterns}")

    # Analyze why coverage is low
    print(f"\n🔍 COVERAGE ANALYSIS:")

    # Count words that match patterns
    pattern_word_texts = set()
    for detector_name, results in pattern_results.items():
        if detector_name == "_metadata":
            continue
        if isinstance(results, list):
            for pattern in results:
                if hasattr(pattern, "word"):
                    pattern_word_texts.add(pattern.word.text)

    # Show words that should match patterns but don't
    print(f"\n❓ POTENTIAL PATTERNS MISSED:")

    # Look for currency-like patterns
    currency_like = []
    date_like = []
    merchant_like = []

    for word in words:
        text = word.text

        # Currency patterns
        if any(c in text for c in ["$", ".", ","]) and any(
            c.isdigit() for c in text
        ):
            if text not in pattern_word_texts:
                currency_like.append(text)

        # Date patterns
        if "/" in text or "-" in text:
            parts = text.replace("/", "-").split("-")
            if len(parts) >= 2 and all(p.isdigit() for p in parts if p):
                if text not in pattern_word_texts:
                    date_like.append(text)

        # Merchant patterns (usually in first few lines)
        if len(text) > 3 and text.isupper():
            merchant_like.append(text)

    if currency_like:
        print(f"\nCurrency-like words not detected:")
        for text in currency_like[:5]:
            print(f"   '{text}'")

    if date_like:
        print(f"\nDate-like words not detected:")
        for text in date_like[:5]:
            print(f"   '{text}'")

    if merchant_like:
        print(f"\nPotential merchant names (header text):")
        for text in merchant_like[:5]:
            print(f"   '{text}'")

    # Analyze word distribution
    print(f"\n📊 WORD TYPE DISTRIBUTION:")

    numeric_words = sum(
        1 for w in words if w.text.replace(".", "").replace(",", "").isdigit()
    )
    alpha_words = sum(1 for w in words if w.text.isalpha())
    mixed_words = sum(
        1
        for w in words
        if any(c.isdigit() for c in w.text)
        and any(c.isalpha() for c in w.text)
    )
    special_words = len(words) - numeric_words - alpha_words - mixed_words

    print(f"   Numeric: {numeric_words} ({numeric_words/len(words)*100:.1f}%)")
    print(f"   Alphabetic: {alpha_words} ({alpha_words/len(words)*100:.1f}%)")
    print(f"   Mixed: {mixed_words} ({mixed_words/len(words)*100:.1f}%)")
    print(f"   Special: {special_words} ({special_words/len(words)*100:.1f}%)")

    # Show line structure
    print(f"\n📄 RECEIPT STRUCTURE (first 10 lines):")

    # Show first 10 words as a simple sequence
    line_text = " ".join(w.text for w in words[:20])
    print(f"   First 20 words: {line_text}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Inspect receipt patterns")
    parser.add_argument("receipt_file", help="Path to receipt JSON file")

    args = parser.parse_args()

    asyncio.run(inspect_receipt(args.receipt_file))
