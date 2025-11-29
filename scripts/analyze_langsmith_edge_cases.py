#!/usr/bin/env python3
"""
Analyze LangSmith traces to identify edge cases by label type.

This script helps discover patterns and edge cases across all CORE_LABEL types
by analyzing LLM decisions in LangSmith traces.

Usage:
    # Analyze all label types for the last 24 hours
    python scripts/analyze_langsmith_edge_cases.py

    # Analyze specific label type
    python scripts/analyze_langsmith_edge_cases.py --label-type MERCHANT_NAME

    # Analyze with custom time range
    python scripts/analyze_langsmith_edge_cases.py --hours 48

    # Export to CSV for further analysis
    python scripts/analyze_langsmith_edge_cases.py --export-csv edge_cases.csv
"""

import argparse
import csv
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional

try:
    from langsmith import Client
except ImportError:
    print("Error: langsmith package not found. Install with: pip install langsmith")
    sys.exit(1)

# All CORE_LABELS
CORE_LABELS = [
    "MERCHANT_NAME",
    "STORE_HOURS",
    "PHONE_NUMBER",
    "WEBSITE",
    "LOYALTY_ID",
    "ADDRESS_LINE",
    "DATE",
    "TIME",
    "PAYMENT_METHOD",
    "COUPON",
    "DISCOUNT",
    "PRODUCT_NAME",
    "QUANTITY",
    "UNIT_PRICE",
    "LINE_TOTAL",
    "SUBTOTAL",
    "TAX",
    "GRAND_TOTAL",
]


def analyze_traces(
    client: Client,
    project_name: str = "label-harmonizer",
    label_type: Optional[str] = None,
    hours: int = 24,
    limit: int = 1000,
) -> Dict[str, List[Dict]]:
    """Analyze LangSmith traces to identify edge cases."""

    end_time = datetime.now()
    start_time = end_time - timedelta(hours=hours)

    print(f"Analyzing LangSmith traces...")
    print(f"  Project: {project_name}")
    print(f"  Time range: {start_time.isoformat()} to {end_time.isoformat()}")
    if label_type:
        print(f"  Label type: {label_type}")
    print()

    # Build filter
    filter_query = None
    if label_type:
        filter_query = f"metadata.label_type == '{label_type}'"

    # Collect all traces
    all_traces = []
    try:
        runs = client.list_runs(
            project_name=project_name,
            filter=filter_query,
            start_time=start_time,
            end_time=end_time,
            limit=limit,
        )
        all_traces = list(runs)
    except Exception as e:
        print(f"Error querying LangSmith: {e}")
        return {}

    print(f"Found {len(all_traces)} traces\n")

    # Group by label type and analyze
    by_label_type: Dict[str, List[Dict]] = defaultdict(list)
    edge_cases: Dict[str, List[Dict]] = defaultdict(list)

    for run in all_traces:
        # LangSmith stores metadata in run.extra.metadata or run.extra
        metadata = {}
        if hasattr(run, "extra") and run.extra:
            if isinstance(run.extra, dict):
                metadata = run.extra.get("metadata", run.extra)
            elif hasattr(run.extra, "metadata"):
                metadata = run.extra.metadata or {}

        lt = metadata.get("label_type", "UNKNOWN")

        # Extract key information
        trace_info = {
            "run_id": run.id,
            "label_type": lt,
            "merchant": metadata.get("merchant", "N/A"),
            "word": metadata.get("word", "N/A"),
            "validation_status": metadata.get("validation_status", "N/A"),
            "is_outlier": None,  # Will extract from output
            "reasoning": None,
            "url": f"https://smith.langchain.com/o/default/projects/p/{project_name}/r/{run.id}",
            "start_time": run.start_time.isoformat() if run.start_time else None,
        }

        # Try to extract outlier decision from output
        # LangChain structured output returns the Pydantic model as output
        if hasattr(run, "outputs") and run.outputs:
            output = run.outputs
            if isinstance(output, dict):
                trace_info["is_outlier"] = output.get("is_outlier")
                trace_info["reasoning"] = output.get("reasoning")
            elif hasattr(output, "is_outlier"):
                # Pydantic model
                trace_info["is_outlier"] = output.is_outlier
                trace_info["reasoning"] = getattr(output, "reasoning", None)

        by_label_type[lt].append(trace_info)

        # Identify potential edge cases
        # 1. Outliers that might be false positives
        if trace_info["is_outlier"] is True:
            edge_cases[lt].append({
                **trace_info,
                "edge_case_type": "outlier_detected",
            })

        # 2. Words with specific validation statuses
        if trace_info["validation_status"] in ["INVALID", "NEEDS_REVIEW"]:
            edge_cases[lt].append({
                **trace_info,
                "edge_case_type": f"status_{trace_info['validation_status']}",
            })

    return {
        "by_label_type": dict(by_label_type),
        "edge_cases": dict(edge_cases),
    }


def print_summary(analysis: Dict[str, List[Dict]]):
    """Print summary of analysis."""
    by_label_type = analysis["by_label_type"]
    edge_cases = analysis["edge_cases"]

    print("=" * 80)
    print("SUMMARY BY LABEL TYPE")
    print("=" * 80)

    for label_type in sorted(by_label_type.keys()):
        traces = by_label_type[label_type]
        outliers = sum(1 for t in traces if t.get("is_outlier") is True)
        non_outliers = sum(1 for t in traces if t.get("is_outlier") is False)

        print(f"\n{label_type}:")
        print(f"  Total traces: {len(traces)}")
        print(f"  Outliers detected: {outliers}")
        print(f"  Non-outliers: {non_outliers}")
        print(f"  Edge cases: {len(edge_cases.get(label_type, []))}")

        # Show unique merchants
        merchants = set(t["merchant"] for t in traces if t["merchant"] != "N/A")
        if merchants:
            print(f"  Merchants: {len(merchants)} ({', '.join(sorted(merchants)[:5])}{'...' if len(merchants) > 5 else ''})")

    print("\n" + "=" * 80)
    print("EDGE CASES BY LABEL TYPE")
    print("=" * 80)

    for label_type in sorted(edge_cases.keys()):
        cases = edge_cases[label_type]
        print(f"\n{label_type}: {len(cases)} edge cases")

        # Group by edge case type
        by_type = defaultdict(list)
        for case in cases:
            by_type[case["edge_case_type"]].append(case)

        for edge_type, type_cases in sorted(by_type.items()):
            print(f"  {edge_type}: {len(type_cases)}")

            # Show examples
            for case in type_cases[:3]:  # Show first 3
                print(f"    - Word: '{case['word']}' | Merchant: {case['merchant']} | Status: {case['validation_status']}")
                if case.get("reasoning"):
                    reasoning = case["reasoning"][:100] + "..." if len(case["reasoning"]) > 100 else case["reasoning"]
                    print(f"      Reasoning: {reasoning}")
                print(f"      URL: {case['url']}")
            if len(type_cases) > 3:
                print(f"    ... and {len(type_cases) - 3} more")


def export_csv(analysis: Dict[str, List[Dict]], filename: str):
    """Export edge cases to CSV."""
    edge_cases = analysis["edge_cases"]

    with open(filename, "w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "run_id",
                "label_type",
                "merchant",
                "word",
                "validation_status",
                "is_outlier",
                "reasoning",
                "edge_case_type",
                "url",
                "start_time",
            ],
        )
        writer.writeheader()

        for label_type, cases in edge_cases.items():
            for case in cases:
                writer.writerow(case)

    print(f"\nExported {sum(len(cases) for cases in edge_cases.values())} edge cases to {filename}")


def main():
    parser = argparse.ArgumentParser(
        description="Analyze LangSmith traces to identify edge cases by label type"
    )
    parser.add_argument(
        "--project",
        default="label-harmonizer",
        help="LangSmith project name (default: label-harmonizer). Use a clean project name like 'label-harmonizer-v2' for new runs.",
    )
    parser.add_argument(
        "--label-type",
        choices=CORE_LABELS,
        help="Filter by specific label type",
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=24,
        help="Number of hours to look back (default: 24)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=1000,
        help="Maximum number of traces to analyze (default: 1000)",
    )
    parser.add_argument(
        "--export-csv",
        help="Export edge cases to CSV file",
    )
    parser.add_argument(
        "--api-key",
        help="LangSmith API key (or set LANGCHAIN_API_KEY env var)",
    )

    args = parser.parse_args()

    # Get API key
    api_key = args.api_key or os.environ.get("LANGCHAIN_API_KEY")
    if not api_key:
        print("Error: LangSmith API key required.")
        print("Set LANGCHAIN_API_KEY environment variable or use --api-key")
        sys.exit(1)

    # Create client
    client = Client(api_key=api_key)

    # Analyze
    analysis = analyze_traces(
        client=client,
        project_name=args.project,
        label_type=args.label_type,
        hours=args.hours,
        limit=args.limit,
    )

    # Print summary
    print_summary(analysis)

    # Export if requested
    if args.export_csv:
        export_csv(analysis, args.export_csv)


if __name__ == "__main__":
    main()

