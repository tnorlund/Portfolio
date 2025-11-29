#!/usr/bin/env python3
"""
Review edge cases in a human-readable format.

This script reads edge cases from JSON and displays them in a formatted,
easy-to-review format, grouped by label type.

Usage:
    # Review all edge cases
    python scripts/review_edge_cases.py edge_case_candidates.json

    # Review specific label type
    python scripts/review_edge_cases.py edge_case_candidates.json --label-type MERCHANT_NAME

    # Show only top N cases per label
    python scripts/review_edge_cases.py edge_case_candidates.json --top 10
"""

import argparse
import json
import sys
from typing import Dict, List


def format_edge_case(edge_case: Dict, index: int) -> str:
    """Format a single edge case for display."""
    word_text = edge_case.get("word_text") or edge_case.get("pattern", "N/A")
    match_type = edge_case.get("match_type", "exact")
    count = edge_case.get("count", 0)
    merchant = edge_case.get("merchant_name")
    reason = edge_case.get("reason", "No reason provided")

    merchant_str = f" (merchant: {merchant})" if merchant else " (global)"
    match_type_str = f" [{match_type}]" if match_type != "exact" else ""

    lines = [
        f"  {index}. '{word_text}'{match_type_str}{merchant_str}",
        f"     Count: {count} occurrences",
        f"     Reason: {reason}",
    ]

    # Show examples if available
    examples = edge_case.get("examples", [])
    if examples:
        example_words = [ex.get("word_text", "N/A") for ex in examples[:3]]
        lines.append(f"     Examples: {', '.join(example_words)}")

    return "\n".join(lines)


def review_edge_cases(
    file_path: str, label_type: str = None, top: int = None
) -> None:
    """Review edge cases from JSON file."""
    try:
        with open(file_path, "r") as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"Error: File '{file_path}' not found.")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in '{file_path}': {e}")
        sys.exit(1)

    exact_matches = data.get("exact_matches", {})
    patterns = data.get("patterns", {})

    # Filter by label type if specified
    if label_type:
        label_type = label_type.upper()
        exact_matches = {label_type: exact_matches.get(label_type, {})}
        patterns = {label_type: patterns.get(label_type, {})}

    # Display exact matches
    print("=" * 80)
    print("EDGE CASES - EXACT MATCHES")
    print("=" * 80)
    print()

    if not exact_matches:
        print("No exact match edge cases found.")
    else:
        for label, cases in sorted(exact_matches.items()):
            if not cases:
                continue

            print(f"\n{label}")
            print("-" * 80)

            # Sort by count (descending)
            sorted_cases = sorted(cases, key=lambda x: x.get("count", 0), reverse=True)

            # Limit to top N if specified
            if top:
                sorted_cases = sorted_cases[:top]

            for i, case in enumerate(sorted_cases, 1):
                print(format_edge_case(case, i))
                print()

    # Display pattern-based edge cases
    if patterns:
        print("\n" + "=" * 80)
        print("EDGE CASES - PATTERN-BASED")
        print("=" * 80)
        print()

        for label, pattern_cases in sorted(patterns.items()):
            if not pattern_cases:
                continue

            print(f"\n{label}")
            print("-" * 80)

            for i, case in enumerate(pattern_cases, 1):
                print(format_edge_case(case, i))
                print()

    # Summary statistics
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)

    total_exact = sum(len(cases) for cases in exact_matches.values())
    total_patterns = sum(len(cases) for cases in patterns.values())

    print(f"Total exact match edge cases: {total_exact}")
    print(f"Total pattern-based edge cases: {total_patterns}")

    for label, cases in sorted(exact_matches.items()):
        if cases:
            total_count = sum(c.get("count", 0) for c in cases)
            print(f"  {label}: {len(cases)} cases, {total_count} total occurrences")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Review edge cases in a human-readable format"
    )
    parser.add_argument(
        "file",
        type=str,
        help="JSON file containing edge cases",
    )
    parser.add_argument(
        "--label-type",
        type=str,
        help="Filter to specific CORE_LABEL (e.g., MERCHANT_NAME)",
    )
    parser.add_argument(
        "--top",
        type=int,
        help="Show only top N cases per label type",
    )

    args = parser.parse_args()

    review_edge_cases(args.file, args.label_type, args.top)


if __name__ == "__main__":
    main()

