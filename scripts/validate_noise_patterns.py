#!/usr/bin/env python3
"""
Quick validation script for testing noise detection patterns.

This script allows you to:
1. Test specific words/patterns
2. Validate against known receipt samples
3. Check edge cases

Usage:
    python scripts/validate_noise_patterns.py
    python scripts/validate_noise_patterns.py --word "..."
    python scripts/validate_noise_patterns.py --file receipt_sample.txt
"""

import argparse
import sys
from pathlib import Path
from typing import List, Tuple

# Add parent directories to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from receipt_label.utils.noise_detection import (
    NoiseDetectionConfig,
    is_noise_text,
)

# Common receipt samples for testing
SAMPLE_RECEIPTS = {
    "grocery": [
        "WALMART",
        "SUPERCENTER",
        "#5260",
        "MANAGER",
        "TOM",
        "SMITH",
        "979-123-4567",
        "ST#",
        "5260",
        "OP#",
        "00001234",
        "TE#",
        "25",
        "TR#",
        "0123",
        "GROCERY",
        "BANANAS",
        "0.68",
        "LB",
        "@",
        "$0.29/LB",
        "$0.20",
        "F",
        "MILK",
        "2%",
        "GAL",
        "$3.49",
        "F",
        "BREAD",
        "WHEAT",
        "$2.99",
        "F",
        "-",
        "-",
        "-",
        "-",
        "-",
        "-",
        "-",
        "-",
        "-",
        "-",
        "SUBTOTAL",
        "$6.68",
        "TAX",
        "1",
        "@",
        "8.250%",
        "$0.55",
        "TOTAL",
        "$7.23",
        "CASH",
        "TEND",
        "$10.00",
        "CHANGE",
        "DUE",
        "$2.77",
        "****",
        "****",
        "****",
        "1234",
        "|",
        "|",
        "|",
        "|",
        "|",
        ".",
        ".",
        ".",
        ".",
        ".",
        "===",
        "===",
        "===",
        "===",
        "---",
        "---",
        "---",
        "---",
    ],
    "restaurant": [
        "OLIVE",
        "GARDEN",
        "TABLE",
        "12",
        "SERVER:",
        "JANE",
        "GUESTS:",
        "2",
        "================================",
        "APPETIZER",
        "BREADSTICKS",
        "$0.00",
        "CALAMARI",
        "$12.99",
        "ENTREES",
        "CHICKEN",
        "PARM",
        "$18.99",
        "FETTUCCINE",
        "ALFREDO",
        "$16.99",
        "DRINKS",
        "COKE",
        "$3.99",
        "WATER",
        "$0.00",
        "------",
        "------",
        "------",
        "SUBTOTAL:",
        "$52.96",
        "TAX:",
        "$4.37",
        "======",
        "======",
        "======",
        "TOTAL:",
        "$57.33",
        "SUGGESTED",
        "TIP:",
        "15%",
        "=",
        "$8.60",
        "18%",
        "=",
        "$10.32",
        "20%",
        "=",
        "$11.47",
        "....",
        "....",
        "....",
    ],
    "gas": [
        "SHELL",
        "STATION",
        "#1234",
        "123",
        "MAIN",
        "ST",
        "PUMP",
        "#",
        "3",
        "REGULAR",
        "UNLEADED",
        "GALLONS:",
        "12.345",
        "@",
        "$3.459",
        "/GAL",
        "FUEL",
        "TOTAL:",
        "$42.72",
        "----------",
        "----------",
        "INSIDE",
        "STORE",
        "COFFEE",
        "$2.99",
        "SNACKS",
        "$5.49",
        "----------",
        "SUBTOTAL:",
        "$51.20",
        "TAX:",
        "$0.42",
        "TOTAL:",
        "$51.62",
        "VISA",
        "****",
        "****",
        "****",
        "1234",
        "|||||||",
        "|||||||",
    ],
}


def test_word(word: str, config: NoiseDetectionConfig = None) -> Tuple[str, bool, str]:
    """Test a single word and return result with explanation."""
    is_noise = is_noise_text(word, config)

    # Determine why it's classified as noise or not
    reason = ""
    if is_noise:
        if word.strip() == "":
            reason = "whitespace"
        elif len(word) == 1 and word in ".,;:!?'\"":
            reason = "punctuation"
        elif word in ["$", "€", "£", "¥", "¢"]:
            reason = "currency (but preserved)"
        elif all(c in "-=_|" for c in word):
            reason = "separator"
        elif all(c == word[0] for c in word) and word[0] in "*#.":
            reason = "repeated artifact"
        else:
            reason = "pattern match"
    else:
        if "$" in word and any(c.isdigit() for c in word):
            reason = "price/amount"
        elif word.upper() == word and len(word) > 1:
            reason = "likely label/merchant"
        elif any(c.isdigit() for c in word):
            reason = "contains numbers"
        else:
            reason = "meaningful text"

    return word, is_noise, reason


def test_receipt_sample(name: str, words: List[str]) -> None:
    """Test a complete receipt sample."""
    print(f"\n{'='*60}")
    print(f"Testing {name.upper()} Receipt Sample")
    print(f"{'='*60}")

    results = [test_word(word) for word in words]

    # Statistics
    total = len(results)
    noise_count = sum(1 for _, is_noise, _ in results if is_noise)
    noise_percentage = (noise_count / total) * 100 if total > 0 else 0

    print(f"\nStatistics:")
    print(f"- Total words: {total}")
    print(f"- Noise words: {noise_count}")
    print(f"- Meaningful words: {total - noise_count}")
    print(f"- Noise percentage: {noise_percentage:.1f}%")

    # Show noise words by category
    noise_by_reason = {}
    for word, is_noise, reason in results:
        if is_noise:
            if reason not in noise_by_reason:
                noise_by_reason[reason] = []
            noise_by_reason[reason].append(word)

    if noise_by_reason:
        print("\nNoise words by category:")
        for reason, words in sorted(noise_by_reason.items()):
            print(f"- {reason}: {', '.join(repr(w) for w in words[:10])}")
            if len(words) > 10:
                print(f"  ... and {len(words) - 10} more")


def interactive_mode():
    """Run interactive testing mode."""
    print("Noise Detection Pattern Validator")
    print("=" * 60)
    print("Enter words to test (or 'quit' to exit)")
    print("Commands:")
    print("  test <word>     - Test a single word")
    print("  receipt <type>  - Test a receipt sample (grocery/restaurant/gas)")
    print("  edge            - Test edge cases")
    print("  quit            - Exit")
    print()

    while True:
        try:
            user_input = input("> ").strip()

            if user_input.lower() == "quit":
                break

            if user_input.startswith("test "):
                word = user_input[5:]
                word, is_noise, reason = test_word(word)
                status = "NOISE" if is_noise else "MEANINGFUL"
                print(f"'{word}' -> {status} ({reason})")

            elif user_input.startswith("receipt "):
                receipt_type = user_input[8:].lower()
                if receipt_type in SAMPLE_RECEIPTS:
                    test_receipt_sample(receipt_type, SAMPLE_RECEIPTS[receipt_type])
                else:
                    print(
                        f"Unknown receipt type. Choose from: {', '.join(SAMPLE_RECEIPTS.keys())}"
                    )

            elif user_input == "edge":
                print("\nTesting edge cases...")
                edge_cases = [
                    # Currency
                    ("$", "Currency symbols"),
                    ("$5.99", "Price"),
                    ("€10", "Euro price"),
                    # Mixed alphanumeric
                    ("ABC123", "Product code"),
                    ("3M", "Brand name"),
                    ("V2.0", "Version"),
                    # Special patterns
                    ("15%", "Percentage"),
                    ("@", "At symbol"),
                    ("****1234", "Masked number"),
                    # Whitespace
                    ("", "Empty string"),
                    ("   ", "Spaces"),
                    ("\t", "Tab"),
                    # OCR artifacts
                    ("|||", "Vertical bars"),
                    ("...", "Dots"),
                    ("===", "Equals"),
                    ("---", "Dashes"),
                ]

                for test, description in edge_cases:
                    word, is_noise, reason = test_word(test)
                    status = "NOISE" if is_noise else "MEANINGFUL"
                    print(
                        f"{description:20} '{repr(word):10}' -> {status:11} ({reason})"
                    )

            else:
                print(
                    "Unknown command. Try 'test <word>', 'receipt <type>', 'edge', or 'quit'"
                )

        except (EOFError, KeyboardInterrupt):
            print("\nExiting...")
            break


def main():
    parser = argparse.ArgumentParser(description="Validate noise detection patterns")
    parser.add_argument("--word", help="Test a specific word")
    parser.add_argument("--file", help="Test words from a file (one per line)")
    parser.add_argument(
        "--receipt",
        choices=["grocery", "restaurant", "gas"],
        help="Test a sample receipt type",
    )

    args = parser.parse_args()

    if args.word:
        word, is_noise, reason = test_word(args.word)
        status = "NOISE" if is_noise else "MEANINGFUL"
        print(f"'{word}' -> {status} ({reason})")

    elif args.file:
        with open(args.file, "r") as f:
            words = [line.strip() for line in f if line.strip()]
        test_receipt_sample(Path(args.file).stem, words)

    elif args.receipt:
        test_receipt_sample(args.receipt, SAMPLE_RECEIPTS[args.receipt])

    else:
        # Interactive mode
        interactive_mode()


if __name__ == "__main__":
    main()
