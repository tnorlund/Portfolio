#!/usr/bin/env python3
"""
Test script for Label Validation Agent.

Finds a NEEDS_REVIEW MERCHANT_NAME label and runs the validation agent on it.
"""

import asyncio
import os
import sys
from pathlib import Path

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

# Embedding function is imported above


async def find_needs_review_merchant_name():
    """Find a NEEDS_REVIEW MERCHANT_NAME label."""
    print("Loading environment...")
    env = load_env()

    dynamo = DynamoClient(
        table_name=env["dynamodb_table_name"],
        region=env["region"],
    )

    print("Searching for NEEDS_REVIEW MERCHANT_NAME labels...")

    # Get NEEDS_REVIEW labels
    labels, _ = dynamo.list_receipt_word_labels_with_status(
        status=ValidationStatus.NEEDS_REVIEW,
        limit=100,
    )

    # Filter for MERCHANT_NAME
    merchant_labels = [l for l in labels if l.label == "MERCHANT_NAME"]

    if not merchant_labels:
        print("No NEEDS_REVIEW MERCHANT_NAME labels found")
        return None

    print(f"Found {len(merchant_labels)} NEEDS_REVIEW MERCHANT_NAME labels")

    # Get the first one
    label = merchant_labels[0]

    # Get word text
    word = dynamo.get_receipt_word(
        image_id=label.image_id,
        receipt_id=label.receipt_id,
        line_id=label.line_id,
        word_id=label.word_id,
    )

    if not word:
        print(f"Could not find word for label {label.image_id}#{label.receipt_id}")
        return None

    # Get merchant name
    metadata = dynamo.get_receipt_metadata(
        image_id=label.image_id,
        receipt_id=label.receipt_id,
    )
    merchant_name = metadata.merchant_name if metadata else None

    print(f"\nFound label to validate:")
    print(f"  Word: '{word.text}'")
    print(f"  Label: {label.label}")
    print(f"  Status: {label.validation_status}")
    print(f"  Merchant: {merchant_name}")
    print(f"  Image ID: {label.image_id}")
    print(f"  Receipt ID: {label.receipt_id}")
    print(f"  Line ID: {label.line_id}")
    print(f"  Word ID: {label.word_id}")
    print(f"  Reasoning: {label.reasoning or 'None'}")

    return {
        "word_text": word.text,
        "suggested_label_type": label.label,
        "merchant_name": merchant_name,
        "original_reasoning": label.reasoning or "No reasoning provided",
        "image_id": label.image_id,
        "receipt_id": label.receipt_id,
        "line_id": label.line_id,
        "word_id": label.word_id,
    }


async def test_validation_agent():
    """Test the label validation agent."""
    print("=" * 80)
    print("Label Validation Agent Test")
    print("=" * 80)

    # Find a NEEDS_REVIEW MERCHANT_NAME label
    label_data = await find_needs_review_merchant_name()

    if not label_data:
        print("Could not find a label to validate")
        return

    print("\n" + "=" * 80)
    print("Running Validation Agent...")
    print("=" * 80)

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

                print(f"   Bucket: {bucket_name}")
                print(f"   Target: {words_dir}")

                words_result = download_snapshot_atomic(
                    bucket=bucket_name,
                    collection="words",
                    local_path=words_dir,
                    verify_integrity=True,
                )

                if words_result.get("status") == "downloaded":
                    print(f"✅ Words collection downloaded: {words_result.get('version_id', 'unknown')}")
                    chroma_persist_dir = words_dir
                else:
                    print(f"⚠️  Words download failed: {words_result.get('error', 'unknown error')}")
                    print("   Continuing without ChromaDB (similarity search will be disabled)")
                    chroma_persist_dir = "/tmp/chromadb"  # Fallback
            except Exception as e:
                print(f"⚠️  Could not download ChromaDB from S3: {e}")
                print("   Continuing without ChromaDB (similarity search will be disabled)")
                chroma_persist_dir = "/tmp/chromadb"  # Fallback
        else:
            print("⚠️  No ChromaDB bucket configured, continuing without ChromaDB")
            chroma_persist_dir = "/tmp/chromadb"  # Fallback

    print(f"\nInitializing ChromaDB client at: {chroma_persist_dir}")
    chroma = ChromaClient(persist_directory=chroma_persist_dir)

    # Verify words collection exists
    try:
        words_col = chroma.get_collection("words", create_if_missing=False)
        print(f"✅ Words collection verified: {words_col.count()} documents")
    except Exception as e:
        print(f"⚠️  Warning: Could not access words collection: {e}")

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
    # (The workflow will handle this automatically, but we can pre-load it here)
    pulumi_secrets = load_pulumi_secrets("dev") or load_pulumi_secrets("prod")
    if pulumi_secrets:
        ollama_api_key = (
            pulumi_secrets.get("portfolio:OLLAMA_API_KEY") or
            pulumi_secrets.get("OLLAMA_API_KEY") or
            pulumi_secrets.get("RECEIPT_AGENT_OLLAMA_API_KEY")
        )
        if ollama_api_key and not settings.ollama_api_key.get_secret_value():
            # Set in environment so settings picks it up
            os.environ["RECEIPT_AGENT_OLLAMA_API_KEY"] = ollama_api_key
            # Reload settings
            settings = get_settings()
            print(f"Loaded Ollama API key from Pulumi secrets")

    # Create the graph
    graph, state_holder = create_label_validation_graph(
        dynamo_client=dynamo,
        chroma_client=chroma,
        embed_fn=embed_fn,
    )

    # Run validation
    result = await run_label_validation(
        graph=graph,
        state_holder=state_holder,
        **label_data,
    )

    # Print results
    print("\n" + "=" * 80)
    print("Validation Result")
    print("=" * 80)
    print(f"Decision: {result['decision']}")
    print(f"Confidence: {result['confidence']:.2%}")

    # Show which tools were used (if available in result)
    if 'tools_used' in result:
        print(f"\nTools Used: {', '.join(result['tools_used'])}")

    print(f"\nReasoning:")
    print(result['reasoning'])
    print(f"\nEvidence:")
    for i, evidence in enumerate(result.get('evidence', []), 1):
        print(f"  {i}. {evidence}")

    print("\n" + "=" * 80)
    print("Test Complete")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(test_validation_agent())

