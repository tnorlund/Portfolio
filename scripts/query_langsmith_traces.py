#!/usr/bin/env python3
"""
Query LangSmith traces for label harmonizer runs.

Usage:
    python scripts/query_langsmith_traces.py --image-id <image_id> --receipt-id <receipt_id>
    python scripts/query_langsmith_traces.py --word "SPROUTS" --merchant "Sprouts Farmers Market"
    python scripts/query_langsmith_traces.py --prompt-version "v2-markdown-full-context"
"""

import argparse
import os
import sys
from datetime import datetime, timedelta
from typing import Optional

try:
    from langsmith import Client
except ImportError:
    print("Error: langsmith package not found. Install with: pip install langsmith")
    sys.exit(1)


def query_traces(
    client: Client,
    project_name: str = "label-harmonizer",
    run_id: Optional[str] = None,
    image_id: Optional[str] = None,
    receipt_id: Optional[str] = None,
    line_id: Optional[str] = None,
    word_id: Optional[str] = None,
    word: Optional[str] = None,
    merchant: Optional[str] = None,
    label_type: Optional[str] = None,
    prompt_version: Optional[str] = None,
    limit: int = 10,
    hours: int = 24,
):
    """Query LangSmith traces with various filters."""

    # Build filter query
    filters = []

    if image_id:
        filters.append(f"metadata.image_id == '{image_id}'")
    if receipt_id:
        filters.append(f"metadata.receipt_id == '{receipt_id}'")
    if line_id:
        filters.append(f"metadata.line_id == '{line_id}'")
    if word_id:
        filters.append(f"metadata.word_id == '{word_id}'")
    if word:
        filters.append(f"metadata.word == '{word}'")
    if merchant:
        filters.append(f"metadata.merchant == '{merchant}'")
    if label_type:
        filters.append(f"metadata.label_type == '{label_type}'")
    if prompt_version:
        filters.append(f"metadata.prompt_version == '{prompt_version}'")

    filter_query = " AND ".join(filters) if filters else None

    # Calculate time range
    end_time = datetime.now()
    start_time = end_time - timedelta(hours=hours)

    print(f"Querying LangSmith traces...")
    print(f"  Project: {project_name}")
    if run_id:
        print(f"  Run ID: {run_id}")
    else:
        print(f"  Time range: {start_time.isoformat()} to {end_time.isoformat()}")
        if filter_query:
            print(f"  Filters: {filter_query}")
    print()

    try:
        # If specific run_id provided, fetch that run directly
        if run_id:
            try:
                run = client.read_run(run_id=run_id)
                runs_list = [run]
            except Exception as e:
                print(f"Error fetching run {run_id}: {e}")
                return []
        else:
            runs = client.list_runs(
                project_name=project_name,
                filter=filter_query,
                start_time=start_time,
                end_time=end_time,
                limit=limit,
            )
            runs_list = list(runs)
        print(f"Found {len(runs_list)} trace(s)\n")

        for i, run in enumerate(runs_list, 1):
            print(f"{'='*80}")
            print(f"Trace #{i}")
            print(f"{'='*80}")
            print(f"Run ID: {run.id}")
            print(f"Name: {run.name}")
            print(f"Start Time: {run.start_time}")
            print(f"End Time: {run.end_time}")
            print(f"Status: {run.status}")
            print(f"Run Type: {run.run_type}")

            if run.extra and "metadata" in run.extra:
                metadata = run.extra["metadata"]
                print(f"\nMetadata:")
                for key, value in metadata.items():
                    print(f"  {key}: {value}")

            if run.extra and "tags" in run.extra:
                tags = run.extra["tags"]
                print(f"\nTags: {', '.join(tags)}")

            # Get input/output if available
            if hasattr(run, "inputs") and run.inputs:
                print(f"\nInput:")
                input_str = str(run.inputs)
                if len(input_str) > 2000:
                    print(f"  {input_str[:2000]}...")
                else:
                    print(f"  {input_str}")

            if hasattr(run, "outputs") and run.outputs:
                print(f"\nOutput:")
                output_str = str(run.outputs)
                if len(output_str) > 2000:
                    print(f"  {output_str[:2000]}...")
                else:
                    print(f"  {output_str}")

            # Get URL
            url = f"https://smith.langchain.com/o/default/projects/p/{project_name}/r/{run.id}"
            print(f"\nView in LangSmith: {url}")
            print()

        return runs_list

    except Exception as e:
        print(f"Error querying LangSmith: {e}")
        return []


def main():
    parser = argparse.ArgumentParser(
        description="Query LangSmith traces for label harmonizer runs"
    )
    parser.add_argument(
        "--project",
        default="label-harmonizer",
        help="LangSmith project name (default: label-harmonizer)",
    )
    parser.add_argument("--image-id", help="Filter by image_id")
    parser.add_argument("--receipt-id", help="Filter by receipt_id")
    parser.add_argument("--line-id", help="Filter by line_id")
    parser.add_argument("--word-id", help="Filter by word_id")
    parser.add_argument("--word", help="Filter by word text")
    parser.add_argument("--merchant", help="Filter by merchant name")
    parser.add_argument("--label-type", help="Filter by label type")
    parser.add_argument(
        "--prompt-version", help="Filter by prompt_version (e.g., v2-markdown-full-context)"
    )
    parser.add_argument(
        "--limit", type=int, default=10, help="Maximum number of traces to return"
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=24,
        help="Number of hours to look back (default: 24)",
    )
    parser.add_argument(
        "--api-key",
        help="LangSmith API key (or set LANGCHAIN_API_KEY env var)",
    )
    parser.add_argument(
        "--run-id",
        help="Specific run ID to fetch (e.g., 4aa5a2ad-f4fb-4cea-99ce-584d4f9d4efd)",
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

    # Query traces
    query_traces(
        client=client,
        project_name=args.project,
        run_id=args.run_id,
        image_id=args.image_id,
        receipt_id=args.receipt_id,
        line_id=args.line_id,
        word_id=args.word_id,
        word=args.word,
        merchant=args.merchant,
        label_type=args.label_type,
        prompt_version=args.prompt_version,
        limit=args.limit,
        hours=args.hours,
    )


if __name__ == "__main__":
    main()

