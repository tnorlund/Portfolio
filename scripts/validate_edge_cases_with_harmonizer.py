#!/usr/bin/env python3
"""
Validate edge cases by testing them with the harmonizer.

This script loads edge cases and tests them against the harmonizer to ensure
they work correctly and don't create false positives.

Usage:
    # Validate edge cases from JSON
    python scripts/validate_edge_cases_with_harmonizer.py --input edge_cases_llm.json

    # Test specific label type
    python scripts/validate_edge_cases_with_harmonizer.py --input edge_cases_llm.json --label-type MERCHANT_NAME
"""

import argparse
import asyncio
import json
import os
import sys
from typing import Dict, List, Optional

# Add parent directory to path for imports
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

from receipt_dynamo import DynamoClient
from receipt_agent.tools.label_harmonizer_v2 import LabelHarmonizerV2, LabelRecord
from receipt_agent.config.settings import get_settings
from receipt_agent.clients.factory import (
    create_dynamo_client,
    create_chroma_client,
    create_embed_fn,
)
from langchain_ollama import ChatOllama

try:
    from receipt_dynamo.data._pulumi import load_env
except ImportError:
    load_env = None


async def validate_edge_cases(
    edge_cases: List[Dict],
    label_type: str,
    harmonizer: LabelHarmonizerV2,
    dynamo: DynamoClient,
) -> Dict:
    """Validate edge cases by testing with harmonizer."""
    results = {
        "total_tested": len(edge_cases),
        "validated": [],
        "false_positives": [],
        "errors": [],
    }

    print(f"\nValidating {len(edge_cases)} edge cases...")

    for i, edge_case in enumerate(edge_cases, 1):
        word_text = edge_case.get("word_text") or edge_case.get("pattern", "")
        merchant_name = edge_case.get("merchant_name")
        match_type = edge_case.get("match_type", "exact")

        print(f"\n{i}. Testing: '{word_text}' ({match_type})")

        # Create a test label record
        # We'll use a dummy record - the harmonizer will check edge cases before
        # doing expensive operations
        test_word = LabelRecord(
            image_id="test",
            receipt_id=0,
            line_id=0,
            word_id=0,
            label=label_type,
            validation_status="PENDING",
            merchant_name=merchant_name,
            word_text=word_text,
        )

        try:
            # Check if edge case matches
            edge_case_match = await harmonizer._check_edge_cases(
                word_text=word_text,
                suggested_label_type=label_type,
                merchant_name=merchant_name,
            )

            if edge_case_match:
                print(f"   ✅ Edge case correctly matches")
                results["validated"].append(
                    {
                        "edge_case": edge_case,
                        "matched": True,
                        "matched_edge_case": {
                            "word_text": edge_case_match.word_text,
                            "reason": edge_case_match.reason,
                        },
                    }
                )
            else:
                print(f"   ⚠️  Edge case does NOT match (might not be loaded in DynamoDB)")
                results["errors"].append(
                    {
                        "edge_case": edge_case,
                        "error": "Edge case not found in DynamoDB - may need to load first",
                    }
                )

        except Exception as e:
            print(f"   ❌ Error: {e}")
            results["errors"].append({"edge_case": edge_case, "error": str(e)})

    return results


async def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Validate edge cases with harmonizer"
    )
    parser.add_argument(
        "--input",
        type=str,
        required=True,
        help="JSON file containing edge cases",
    )
    parser.add_argument(
        "--label-type",
        type=str,
        help="Filter to specific label type",
    )

    args = parser.parse_args()

    # Load edge cases
    try:
        with open(args.input, "r") as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"Error: File '{args.input}' not found.")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in '{args.input}': {e}")
        sys.exit(1)

    # Get edge cases
    if "edge_cases" in data:
        # New format from LLM generator
        edge_cases = data["edge_cases"]
        label_type = args.label_type or data.get("label_type", "MERCHANT_NAME")
    elif "exact_matches" in data:
        # Old format from pattern generator
        if args.label_type:
            edge_cases = data["exact_matches"].get(args.label_type, [])
            label_type = args.label_type
        else:
            print("Error: --label-type required for this format")
            sys.exit(1)
    else:
        print("Error: Unknown edge case format")
        sys.exit(1)

    if not edge_cases:
        print("No edge cases found in file.")
        sys.exit(0)

    print(f"Loaded {len(edge_cases)} edge cases for {label_type}")

    # Initialize clients
    try:
        settings = get_settings()

        # Get table name from Pulumi or settings
        if load_env:
            pulumi_outputs = load_env("dev") or load_env("prod")
            if pulumi_outputs and "dynamodb_table_name" in pulumi_outputs:
                table_name = pulumi_outputs["dynamodb_table_name"]
                if "dynamodb_table_arn" in pulumi_outputs:
                    arn = pulumi_outputs["dynamodb_table_arn"]
                    region = arn.split(":")[3] if ":" in arn else pulumi_outputs.get("region", "us-east-1")
                else:
                    region = pulumi_outputs.get("region", "us-east-1")
            else:
                table_name = settings.dynamo_table_name
                region = settings.aws_region
        else:
            table_name = settings.dynamo_table_name
            region = settings.aws_region

        dynamo = create_dynamo_client(settings=settings)
        chroma = create_chroma_client(settings=settings)
        embed_fn = create_embed_fn(settings=settings)

        llm = ChatOllama(
            model=settings.ollama_model,
            base_url=settings.ollama_base_url,
            client_kwargs={
                "headers": {
                    "Authorization": f"Bearer {settings.ollama_api_key.get_secret_value()}"
                }
                if settings.ollama_api_key
                else {},
                "timeout": 120,
            },
            temperature=0.0,
        )

        harmonizer = LabelHarmonizerV2(
            dynamo_client=dynamo,
            chroma_client=chroma,
            embed_fn=embed_fn,
            llm=llm,
            settings=settings,
        )

    except Exception as e:
        print(f"Error initializing clients: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)

    # Validate edge cases
    results = await validate_edge_cases(edge_cases, label_type, harmonizer, dynamo)

    # Print summary
    print("\n" + "=" * 60)
    print("Validation Summary")
    print("=" * 60)
    print(f"Total tested: {results['total_tested']}")
    print(f"Validated: {len(results['validated'])}")
    print(f"Errors: {len(results['errors'])}")
    print("=" * 60)

    if results["errors"]:
        print("\nErrors:")
        for error in results["errors"][:5]:
            print(f"  - {error['edge_case'].get('word_text', 'N/A')}: {error['error']}")


if __name__ == "__main__":
    asyncio.run(main())

