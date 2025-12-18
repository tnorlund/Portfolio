#!/usr/bin/env python3
"""
Query LangSmith traces by place_id metadata.

This script uses place_id and other metadata to efficiently find traces
related to specific problematic receipts.

Usage:
    python scripts/query_langsmith_by_place_id.py <place_id>
    python scripts/query_langsmith_by_place_id.py --all-issues
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional

try:
    from langsmith import Client
except ImportError:
    print("Error: langsmith package not installed. Install with: pip install langsmith")
    sys.exit(1)


def query_traces_by_place_id(
    place_id: str,
    project_id: str = "0b5481ab-0216-4d7d-84bc-252de5ee48dd",
    days_back: int = 2,
    limit: int = 10,
) -> list:
    """
    Query LangSmith for traces with a specific place_id.

    The harmonizer sets:
    - metadata.place_id = place_id
    - metadata.workflow = "harmonizer_v3"
    - thread_id = place_id

    Args:
        place_id: The Google Places place_id to search for
        project_id: LangSmith project ID
        days_back: How many days back to search
        limit: Maximum number of traces to return

    Returns:
        List of Run objects
    """
    api_key = os.environ.get("LANGCHAIN_API_KEY")
    if not api_key:
        print("Error: LANGCHAIN_API_KEY environment variable not set")
        sys.exit(1)

    client = Client(api_key=api_key)

    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=days_back)

    traces = []

    # Try querying by metadata.place_id first
    try:
        runs = list(
            client.list_runs(
                project_name=project_id,
                start_time=start_time,
                end_time=end_time,
                filter=f'and(eq(metadata.place_id, "{place_id}"), eq(metadata.workflow, "harmonizer_v3"))',
                limit=limit,
            )
        )
        traces.extend(runs)
    except Exception as e:
        print(f"Warning: Could not query by metadata.place_id: {e}")

    # Fallback: query by thread_id (also set to place_id)
    if not traces:
        try:
            runs = list(
                client.list_runs(
                    project_name=project_id,
                    start_time=start_time,
                    end_time=end_time,
                    filter=f'eq(thread_id, "{place_id}")',
                    limit=limit,
                )
            )
            traces.extend(runs)
        except Exception as e:
            print(f"Warning: Could not query by thread_id: {e}")

    return traces


def main():
    """Main entry point."""
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    if sys.argv[1] == "--all-issues":
        # Query for all known problematic place_ids
        problematic_place_ids = {
            "lind0_reasoning": "ChIJGSYWwmkk6IARbe-OmbfvA7o",
            "zip_typo": "ChIJ8enef6Um6IARnahyB5i37H0",
            "wrong_merchant": "ChIJ847UEF2mrIkRCMr-uq3pKr8",
        }

        print("=== Querying LangSmith for all problematic place_ids ===\n")

        for issue_type, place_id in problematic_place_ids.items():
            print(f"\n{issue_type.upper()} - place_id: {place_id}")
            print("-" * 60)

            traces = query_traces_by_place_id(place_id)

            if traces:
                print(f"Found {len(traces)} trace(s):")
                for i, trace in enumerate(traces[:5], 1):
                    print(f"  {i}. Trace ID: {trace.id}")
                    print(f"     Start: {trace.start_time}")
                    print(f"     Status: {trace.status}")
                    if hasattr(trace, "metadata") and trace.metadata:
                        receipt_count = trace.metadata.get("receipt_count", "N/A")
                        print(f"     Receipt count: {receipt_count}")
                    print()
            else:
                print("  No traces found")
    else:
        # Query for a specific place_id
        place_id = sys.argv[1]
        print(f"=== Querying LangSmith for place_id: {place_id} ===\n")

        traces = query_traces_by_place_id(place_id)

        if traces:
            print(f"Found {len(traces)} trace(s):\n")
            for i, trace in enumerate(traces, 1):
                print(f"{i}. Trace ID: {trace.id}")
                print(f"   Start: {trace.start_time}")
                print(f"   Status: {trace.status}")
                if hasattr(trace, "metadata") and trace.metadata:
                    receipt_count = trace.metadata.get("receipt_count", "N/A")
                    print(f"   Receipt count: {receipt_count}")
                print(
                    f"   View at: https://smith.langchain.com/o/{os.environ.get('LANGCHAIN_ORG_ID', 'org')}/projects/p/{os.environ.get('LANGCHAIN_PROJECT', project_id)}/r/{trace.id}"
                )
                print()
        else:
            print("No traces found for this place_id")


if __name__ == "__main__":
    main()
