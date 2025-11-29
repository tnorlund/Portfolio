#!/usr/bin/env python3
"""
Test script for Label Validation Agent - Multiple Runs.

Runs the validation agent on multiple NEEDS_REVIEW MERCHANT_NAME labels
and compares the results.
"""

import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.data._pulumi import load_env, load_secrets as load_pulumi_secrets
from receipt_chroma.data.chroma_client import ChromaClient
from receipt_agent.graph.label_validation_workflow import (
    create_label_validation_graph,
    run_label_validation,
)
from receipt_agent.config.settings import get_settings
from receipt_agent.clients.factory import create_embed_fn
from receipt_dynamo.constants import ValidationStatus


async def find_needs_review_merchant_names(limit: int = 10):
    """Find multiple NEEDS_REVIEW MERCHANT_NAME labels."""
    print("Loading environment...")
    env = load_env()

    dynamo = DynamoClient(
        table_name=env["dynamodb_table_name"],
        region=env["region"],
    )

    print(f"Searching for {limit} NEEDS_REVIEW MERCHANT_NAME labels...")

    # Get NEEDS_REVIEW labels - try with larger limit
    labels, _ = dynamo.list_receipt_word_labels_with_status(
        status=ValidationStatus.NEEDS_REVIEW,
        limit=500,  # Get more to filter
    )

    print(f"Found {len(labels)} total NEEDS_REVIEW labels")

    # Filter for MERCHANT_NAME
    merchant_labels = [l for l in labels if l.label == "MERCHANT_NAME"]

    print(f"Found {len(merchant_labels)} NEEDS_REVIEW MERCHANT_NAME labels")

    if not merchant_labels:
        print("No NEEDS_REVIEW MERCHANT_NAME labels found")
        return []

    print(f"Found {len(merchant_labels)} NEEDS_REVIEW MERCHANT_NAME labels")

    # Limit to requested number
    merchant_labels = merchant_labels[:limit]

    # Get word texts and merchant names
    label_data_list = []
    for label in merchant_labels:
        try:
            # Get word text
            word = dynamo.get_receipt_word(
                image_id=label.image_id,
                receipt_id=label.receipt_id,
                line_id=label.line_id,
                word_id=label.word_id,
            )

            if not word:
                continue

            # Get merchant name
            metadata = dynamo.get_receipt_metadata(
                image_id=label.image_id,
                receipt_id=label.receipt_id,
            )
            merchant_name = metadata.merchant_name if metadata else None

            label_data_list.append({
                "word_text": word.text,
                "suggested_label_type": label.label,
                "merchant_name": merchant_name,
                "original_reasoning": label.reasoning or "No reasoning provided",
                "image_id": label.image_id,
                "receipt_id": label.receipt_id,
                "line_id": label.line_id,
                "word_id": label.word_id,
            })
        except Exception as e:
            print(f"Error processing label {label.image_id}: {e}")
            continue

    return label_data_list


async def test_multiple_validations(num_labels: int = 5):
    """Test the label validation agent on multiple labels."""
    print("=" * 80)
    print("Label Validation Agent - Multiple Runs Comparison")
    print("=" * 80)
    print()

    # Find labels to validate
    label_data_list = await find_needs_review_merchant_names(limit=num_labels)

    if not label_data_list:
        print("Could not find labels to validate")
        return

    print(f"\nFound {len(label_data_list)} labels to validate")
    print()

    # Load environment
    env = load_env()

    # Initialize clients
    dynamo = DynamoClient(
        table_name=env["dynamodb_table_name"],
        region=env["region"],
    )

    # Initialize ChromaDB - download words collection from S3 if needed
    words_dir = "/tmp/chromadb_words"
    words_dir_path = Path(words_dir)

    if words_dir_path.exists() and any(words_dir_path.iterdir()):
        print(f"✅ Using downloaded words collection from S3: {words_dir}")
        chroma_persist_dir = words_dir
    else:
        print(f"📥 Words collection not found at {words_dir}, downloading from S3...")
        bucket_name = (
            env.get("embedding_chromadb_bucket_name") or
            env.get("chromadb_bucket_name") or
            os.environ.get("CHROMADB_BUCKET")
        )

        if bucket_name:
            try:
                from receipt_chroma import download_snapshot_atomic
                os.makedirs(words_dir, exist_ok=True)

                words_result = download_snapshot_atomic(
                    bucket=bucket_name,
                    collection="words",
                    local_path=words_dir,
                    verify_integrity=True,
                )

                if words_result.get("status") == "downloaded":
                    print(f"✅ Words collection downloaded")
                    chroma_persist_dir = words_dir
                else:
                    print(f"⚠️  Words download failed, using fallback")
                    chroma_persist_dir = "/tmp/chromadb"
            except Exception as e:
                print(f"⚠️  Could not download ChromaDB from S3: {e}")
                chroma_persist_dir = "/tmp/chromadb"
        else:
            chroma_persist_dir = "/tmp/chromadb"

    chroma = ChromaClient(persist_directory=chroma_persist_dir)

    # Initialize embedding function
    settings = get_settings()
    try:
        embed_fn = create_embed_fn(settings=settings)
    except ValueError as e:
        print(f"\nWarning: {e}")
        print("Using dummy embedding function (similarity search may not work)")
        def dummy_embed_fn(texts):
            return [[0.0] * 1536 for _ in texts]
        embed_fn = dummy_embed_fn

    # Load Ollama API key from Pulumi secrets if not in settings
    pulumi_secrets = load_pulumi_secrets("dev") or load_pulumi_secrets("prod")
    if pulumi_secrets:
        ollama_api_key = (
            pulumi_secrets.get("portfolio:OLLAMA_API_KEY") or
            pulumi_secrets.get("OLLAMA_API_KEY") or
            pulumi_secrets.get("RECEIPT_AGENT_OLLAMA_API_KEY")
        )
        if ollama_api_key and not settings.ollama_api_key.get_secret_value():
            os.environ["RECEIPT_AGENT_OLLAMA_API_KEY"] = ollama_api_key
            settings = get_settings()
            print(f"Loaded Ollama API key from Pulumi secrets")

    # Create the graph
    graph, state_holder = create_label_validation_graph(
        dynamo_client=dynamo,
        chroma_client=chroma,
        embed_fn=embed_fn,
    )

    # Run validations
    results = []
    print("\n" + "=" * 80)
    print("Running Validations...")
    print("=" * 80)
    print()

    for i, label_data in enumerate(label_data_list, 1):
        print(f"[{i}/{len(label_data_list)}] Validating: '{label_data['word_text']}' -> {label_data['suggested_label_type']}")

        try:
            result = await run_label_validation(
                graph=graph,
                state_holder=state_holder,
                **label_data,
            )

            results.append({
                "label_data": label_data,
                "result": result,
            })

            print(f"  Decision: {result['decision']} ({result['confidence']:.0%} confidence)")
            print(f"  Tools used: {', '.join(result.get('tools_used', []))}")
            print()

        except Exception as e:
            print(f"  ❌ Error: {e}")
            print()
            results.append({
                "label_data": label_data,
                "error": str(e),
            })

    # Analyze results
    print("=" * 80)
    print("ANALYSIS")
    print("=" * 80)
    print()

    successful = [r for r in results if "result" in r]
    failed = [r for r in results if "error" in r]

    print(f"Total: {len(results)}")
    print(f"Successful: {len(successful)}")
    print(f"Failed: {len(failed)}")
    print()

    if successful:
        # Decision distribution
        decisions = {}
        for r in successful:
            decision = r["result"]["decision"]
            decisions[decision] = decisions.get(decision, 0) + 1

        print("Decision Distribution:")
        for decision, count in sorted(decisions.items()):
            pct = (count / len(successful)) * 100
            print(f"  {decision}: {count} ({pct:.1f}%)")
        print()

        # Tool usage
        tool_usage = {}
        for r in successful:
            tools = r["result"].get("tools_used", [])
            for tool in tools:
                tool_usage[tool] = tool_usage.get(tool, 0) + 1

        print("Tool Usage:")
        for tool, count in sorted(tool_usage.items(), key=lambda x: -x[1]):
            pct = (count / len(successful)) * 100
            print(f"  {tool}: {count}/{len(successful)} ({pct:.1f}%)")
        print()

        # Check if get_word_context/get_merchant_metadata are being called
        context_tool_calls = sum(1 for r in successful if "get_word_context" in r["result"].get("tools_used", []))
        metadata_tool_calls = sum(1 for r in successful if "get_merchant_metadata" in r["result"].get("tools_used", []))

        print("Context Tool Calls (should be 0):")
        print(f"  get_word_context: {context_tool_calls}/{len(successful)} ({context_tool_calls/len(successful)*100:.1f}%)")
        print(f"  get_merchant_metadata: {metadata_tool_calls}/{len(successful)} ({metadata_tool_calls/len(successful)*100:.1f}%)")
        print()

        # Confidence statistics
        confidences = [r["result"]["confidence"] for r in successful]
        avg_confidence = sum(confidences) / len(confidences)
        min_confidence = min(confidences)
        max_confidence = max(confidences)

        print("Confidence Statistics:")
        print(f"  Average: {avg_confidence:.1%}")
        print(f"  Min: {min_confidence:.1%}")
        print(f"  Max: {max_confidence:.1%}")
        print()

    # Save results to JSON
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = f"label_validation_comparison_{timestamp}.json"

    output_data = {
        "timestamp": timestamp,
        "total_labels": len(results),
        "successful": len(successful),
        "failed": len(failed),
        "results": results,
    }

    with open(output_file, "w") as f:
        json.dump(output_data, f, indent=2, default=str)

    print(f"Results saved to: {output_file}")
    print()

    # Print detailed results
    print("=" * 80)
    print("DETAILED RESULTS")
    print("=" * 80)
    print()

    for i, r in enumerate(results, 1):
        label_data = r["label_data"]
        print(f"{i}. '{label_data['word_text']}' -> {label_data['suggested_label_type']}")

        if "result" in r:
            result = r["result"]
            print(f"   Decision: {result['decision']} ({result['confidence']:.0%})")
            print(f"   Tools: {', '.join(result.get('tools_used', []))}")
            print(f"   Reasoning: {result['reasoning'][:100]}...")
        else:
            print(f"   ❌ Error: {r.get('error', 'Unknown error')}")
        print()

    print("=" * 80)
    print("Test Complete")
    print("=" * 80)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--num-labels", type=int, default=5, help="Number of labels to test")
    args = parser.parse_args()

    asyncio.run(test_multiple_validations(num_labels=args.num_labels))

