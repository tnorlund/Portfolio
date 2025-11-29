#!/usr/bin/env python3
"""
Test script for Receipt Grouper (Determine correct receipt groupings).

This script analyzes an image to determine if receipts are correctly grouped,
trying different combinations of merging receipts.

Usage:
    # Analyze specific image
    python scripts/test_receipt_grouper.py --image-id cb22100f-44c2-4b7d-b29f-46627a64355a

    # Save report to JSON
    python scripts/test_receipt_grouper.py --image-id cb22100f-44c2-4b7d-b29f-46627a64355a -o report.json
"""

import argparse
import asyncio
import json
import logging
import os
import sys

# Add repo root to path
repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, repo_root)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def setup_environment():
    """Load secrets and outputs from Pulumi and set environment variables."""
    from receipt_dynamo.data._pulumi import load_env, load_secrets

    infra_dir = os.path.join(repo_root, "infra")
    env = load_env("dev", working_dir=infra_dir)
    secrets = load_secrets("dev", working_dir=infra_dir)

    # Set environment variables
    os.environ["RECEIPT_AGENT_DYNAMO_TABLE_NAME"] = env.get(
        "dynamodb_table_name", "receipts"
    )
    os.environ["RECEIPT_AGENT_AWS_REGION"] = secrets.get(
        "portfolio:aws-region", "us-east-1"
    )

    # API Keys
    openai_key = secrets.get("portfolio:OPENAI_API_KEY")
    if openai_key:
        os.environ["RECEIPT_AGENT_OPENAI_API_KEY"] = openai_key
        print("✅ OpenAI API key loaded")
    else:
        print("⚠️  OpenAI API key not found")

    ollama_key = secrets.get("portfolio:OLLAMA_API_KEY")
    if ollama_key:
        os.environ["RECEIPT_AGENT_OLLAMA_API_KEY"] = ollama_key
        print("✅ Ollama API key loaded")
    else:
        print("⚠️  Ollama API key not found")

    # LangSmith tracing
    langsmith_key = secrets.get("portfolio:LANGCHAIN_API_KEY", "")
    if langsmith_key:
        os.environ["LANGCHAIN_API_KEY"] = langsmith_key
        os.environ["LANGCHAIN_TRACING_V2"] = "true"
        os.environ["LANGCHAIN_PROJECT"] = "receipt-grouper"
        print("✅ LangSmith tracing enabled")
    else:
        print("⚠️  LangSmith API key not found - tracing disabled")

    print(f"📊 DynamoDB Table: {os.environ.get('RECEIPT_AGENT_DYNAMO_TABLE_NAME')}")
    return env, secrets


async def main():
    parser = argparse.ArgumentParser(
        description="Receipt Grouper - Determine correct receipt groupings",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Analyze specific image
    python scripts/test_receipt_grouper.py --image-id cb22100f-44c2-4b7d-b29f-46627a64355a

    # Save report to JSON
    python scripts/test_receipt_grouper.py --image-id cb22100f-44c2-4b7d-b29f-46627a64355a -o report.json
"""
    )
    parser.add_argument("--image-id", required=True, help="Image ID to analyze")
    parser.add_argument("-o", "--output", help="Output JSON file for results")
    args = parser.parse_args()

    print("=" * 70)
    print("RECEIPT GROUPER - Determine Correct Receipt Groupings")
    print("=" * 70)
    print()
    print("This tool analyzes an image to determine if receipts are correctly")
    print("grouped by trying different combinations of merging receipts.")
    print()

    # Setup
    env, secrets = setup_environment()

    # Create DynamoDB client
    from receipt_dynamo import DynamoClient
    dynamo = DynamoClient(
        table_name=os.environ.get("RECEIPT_AGENT_DYNAMO_TABLE_NAME"),
        region=os.environ.get("RECEIPT_AGENT_AWS_REGION", "us-east-1"),
    )
    print("✅ DynamoDB client created")
    print()

    # Create grouping workflow
    from receipt_agent.graph.receipt_grouping_workflow import (
        create_receipt_grouping_graph,
        run_receipt_grouping,
    )

    graph, state_holder = create_receipt_grouping_graph(dynamo_client=dynamo)
    print("✅ Receipt grouping workflow created")
    print()

    # Run grouping analysis
    print(f"🔍 Analyzing receipt groupings for image {args.image_id}...")
    print()

    result = await run_receipt_grouping(
        graph=graph,
        state_holder=state_holder,
        image_id=args.image_id,
    )

    # Print results
    print()
    print("=" * 70)
    print("RESULTS")
    print("=" * 70)
    print()
    print(f"Image ID: {result.get('image_id', args.image_id)}")
    print(f"Status: {result.get('status', 'COMPLETE')}")
    print(f"Confidence: {result.get('confidence', 0.0):.2%}")
    print()
    print("Reasoning:")
    print(result.get('reasoning', 'No reasoning provided'))
    print()

    if result.get('grouping'):
        print("Recommended Grouping:")
        grouping = result['grouping']
        for receipt_id, line_ids in grouping.items():
            print(f"  Receipt {receipt_id}: {len(line_ids)} lines - {line_ids[:10]}{'...' if len(line_ids) > 10 else ''}")
    else:
        print("No grouping recommendation provided")

    # Save to file
    if args.output:
        with open(args.output, "w") as f:
            json.dump(result, f, indent=2)
        print()
        print(f"💾 Saved report to {args.output}")


if __name__ == "__main__":
    asyncio.run(main())


