#!/usr/bin/env python3
"""
View reasoning and prompts from label validation results.

Usage:
    python scripts/view_validation_results.py [results_file.json]

If no file is provided, uses the most recent results file.
"""

import json
import glob
import os
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


def find_latest_results_file():
    """Find the most recent results file."""
    files = glob.glob("label_validation_results_*.json")
    if not files:
        return None
    return max(files, key=os.path.getmtime)


def view_result(result_file: str, word_text: str = None, decision: str = None, limit: int = 10):
    """View results from a JSON file."""
    with open(result_file) as f:
        results = json.load(f)

    print(f"Results file: {result_file}")
    print(f"Total results: {len(results)}")
    print("=" * 80)

    # Filter if needed
    filtered = results
    if word_text:
        filtered = [r for r in filtered if word_text.lower() in r.get("word_text", "").lower()]
        print(f"Filtered by word '{word_text}': {len(filtered)} results")
    if decision:
        filtered = [r for r in filtered if r.get("decision") == decision]
        print(f"Filtered by decision '{decision}': {len(filtered)} results")

    # Show results
    for i, result in enumerate(filtered[:limit], 1):
        if "error" in result:
            print(f"\n[{i}] ERROR: {result.get('error')}")
            continue

        print(f"\n[{i}] Word: '{result.get('word_text')}'")
        print(f"    Merchant: {result.get('merchant_name')}")
        print(f"    Decision: {result.get('decision')} ({result.get('confidence', 0):.0%} confidence)")
        print(f"    Tools used: {', '.join(result.get('tools_used', []))}")
        print(f"\n    Reasoning:")
        reasoning = result.get('reasoning', '')
        if reasoning:
            # Print reasoning with proper wrapping
            for line in reasoning.split('\n'):
                print(f"      {line}")
        else:
            print("      (No reasoning provided)")

        evidence = result.get('evidence', [])
        if evidence:
            print(f"\n    Evidence ({len(evidence)} items):")
            for j, ev in enumerate(evidence, 1):
                print(f"      {j}. {ev}")

        original_reasoning = result.get('original_reasoning', '')
        if original_reasoning:
            print(f"\n    Original reasoning: {original_reasoning}")

        print("-" * 80)

    if len(filtered) > limit:
        print(f"\n... and {len(filtered) - limit} more results")
        print(f"Use --limit {len(filtered)} to see all")


def analyze_patterns(result_file: str):
    """Analyze patterns in results."""
    with open(result_file) as f:
        results = json.load(f)

    print("=" * 80)
    print("Pattern Analysis")
    print("=" * 80)

    # Group by decision
    by_decision = {}
    for r in results:
        if "error" in r:
            continue
        decision = r.get("decision", "UNKNOWN")
        if decision not in by_decision:
            by_decision[decision] = []
        by_decision[decision].append(r)

    print(f"\nBy Decision:")
    for decision, items in by_decision.items():
        avg_confidence = sum(r.get("confidence", 0) for r in items) / len(items) if items else 0
        print(f"  {decision}: {len(items)} labels (avg confidence: {avg_confidence:.0%})")

    # Common reasoning patterns
    print(f"\nCommon Reasoning Patterns (VALID):")
    valid_reasonings = [r.get("reasoning", "") for r in by_decision.get("VALID", []) if r.get("reasoning")]
    if valid_reasonings:
        # Show first few unique reasonings
        seen = set()
        for reasoning in valid_reasonings[:10]:
            key = reasoning[:100]  # First 100 chars as key
            if key not in seen:
                seen.add(key)
                print(f"  - {reasoning[:200]}...")

    print(f"\nCommon Reasoning Patterns (INVALID):")
    invalid_reasonings = [r.get("reasoning", "") for r in by_decision.get("INVALID", []) if r.get("reasoning")]
    if invalid_reasonings:
        seen = set()
        for reasoning in invalid_reasonings[:10]:
            key = reasoning[:100]
            if key not in seen:
                seen.add(key)
                print(f"  - {reasoning[:200]}...")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="View label validation results")
    parser.add_argument("file", nargs="?", help="Results JSON file (default: latest)")
    parser.add_argument("--word", help="Filter by word text")
    parser.add_argument("--decision", choices=["VALID", "INVALID", "NEEDS_REVIEW"], help="Filter by decision")
    parser.add_argument("--limit", type=int, default=10, help="Number of results to show")
    parser.add_argument("--analyze", action="store_true", help="Show pattern analysis")

    args = parser.parse_args()

    # Find file
    if args.file:
        result_file = args.file
    else:
        result_file = find_latest_results_file()
        if not result_file:
            print("No results file found. Run the test script first.")
            sys.exit(1)

    if not os.path.exists(result_file):
        print(f"File not found: {result_file}")
        sys.exit(1)

    if args.analyze:
        analyze_patterns(result_file)
    else:
        view_result(result_file, word_text=args.word, decision=args.decision, limit=args.limit)

