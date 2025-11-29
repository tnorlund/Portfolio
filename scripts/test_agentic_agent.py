#!/usr/bin/env python3
"""
Test script for the AGENTIC MetadataValidatorAgent.

This script tests the fully autonomous agent that decides which tools to call.
The agent is guardrailed by the tool design - it can only do what the tools allow.

Usage:
    python scripts/test_agentic_agent.py
"""

import os
import sys
import asyncio

# Ensure repo is in path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def setup_environment():
    """Load secrets and outputs from Pulumi and set environment variables."""
    print("=" * 60)
    print("🧪 Agentic Metadata Validation Test")
    print("=" * 60)

    # Load Pulumi configuration
    from pathlib import Path
    infra_dir = Path(__file__).parent.parent / "infra"
    from receipt_dynamo.data._pulumi import load_env, load_secrets

    env = load_env("dev", working_dir=str(infra_dir))
    secrets = load_secrets("dev", working_dir=str(infra_dir))

    # DynamoDB - try multiple key names
    table_name = env.get("dynamodb_table_name") or env.get("receipts_table_name")
    os.environ["RECEIPT_AGENT_DYNAMO_TABLE_NAME"] = table_name or ""
    os.environ["DYNAMO_TABLE_NAME"] = table_name or ""
    print(f"📊 DynamoDB Table: {table_name}")

    # ChromaDB - use local snapshots
    repo_root = os.path.dirname(os.path.dirname(__file__))
    snapshot_base_dir = os.path.join(repo_root, ".chroma_snapshots")
    lines_snapshot_dir = os.path.join(snapshot_base_dir, "lines")
    words_snapshot_dir = os.path.join(snapshot_base_dir, "words")

    if (os.path.exists(lines_snapshot_dir) and os.listdir(lines_snapshot_dir) and
            os.path.exists(words_snapshot_dir) and os.listdir(words_snapshot_dir)):
        os.environ["RECEIPT_AGENT_CHROMA_LINES_DIRECTORY"] = lines_snapshot_dir
        os.environ["RECEIPT_AGENT_CHROMA_WORDS_DIRECTORY"] = words_snapshot_dir
        print(f"✅ ChromaDB snapshots found:")
        print(f"   Lines: {lines_snapshot_dir}")
        print(f"   Words: {words_snapshot_dir}")
    else:
        print("❌ ChromaDB snapshots not found. Run test_metadata_agent.py first to download them.")
        sys.exit(1)

    # API Keys (use portfolio: prefix for Pulumi secrets)
    openai_key = secrets.get("portfolio:OPENAI_API_KEY", "")
    if openai_key:
        os.environ["RECEIPT_AGENT_OPENAI_API_KEY"] = openai_key
        os.environ["OPENAI_API_KEY"] = openai_key
        print("✅ OpenAI API key loaded")

    google_places_key = secrets.get("portfolio:GOOGLE_PLACES_API_KEY", "")
    if google_places_key:
        os.environ["RECEIPT_AGENT_GOOGLE_PLACES_API_KEY"] = google_places_key
        print("✅ Google Places API key loaded")

    langsmith_key = secrets.get("portfolio:LANGCHAIN_API_KEY", "")
    if langsmith_key:
        os.environ["RECEIPT_AGENT_LANGSMITH_API_KEY"] = langsmith_key
        os.environ["LANGCHAIN_API_KEY"] = langsmith_key
        print("✅ LangSmith API key loaded")

    ollama_key = secrets.get("portfolio:OLLAMA_API_KEY", "")
    if ollama_key:
        os.environ["RECEIPT_AGENT_OLLAMA_API_KEY"] = ollama_key
        print("✅ Ollama API key loaded")

    return env, secrets


async def test_agentic_agent():
    """Test the agentic agent on a sample receipt."""
    from receipt_dynamo.data.dynamo_client import DynamoClient
    from receipt_agent import MetadataValidatorAgent
    from receipt_agent.clients.factory import create_chroma_client, create_places_client

    # Set up logging
    import logging
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("chromadb").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)

    # Create DynamoDB client
    table_name = os.environ.get("DYNAMO_TABLE_NAME")
    if not table_name:
        print("❌ DynamoDB table name not set")
        return

    dynamo_client = DynamoClient(table_name=table_name)

    # Find a receipt to validate
    print("🔍 Looking for receipts with metadata...")

    metadatas, _ = dynamo_client.list_receipt_metadatas(limit=10)

    if not metadatas:
        print("❌ No receipt metadata found")
        return

    # Pick a receipt with metadata
    target = None
    for meta in metadatas:
        if meta.merchant_name and meta.place_id:
            target = meta
            break

    if not target:
        target = metadatas[0]

    print(f"✅ Found receipt: image_id={target.image_id[:12]}..., receipt_id={target.receipt_id}")
    print(f"   Merchant: {target.merchant_name}")
    print(f"   Place ID: {target.place_id[:20] if target.place_id else 'None'}...")
    print(f"   Status: {target.validation_status}")

    # Create ChromaDB client
    chroma_client = create_chroma_client()
    if chroma_client:
        print("✅ ChromaDB client created")
    else:
        print("❌ ChromaDB client not created")
        return

    # Create Places client
    places_client = create_places_client()
    if places_client:
        print("✅ Places client created")

    # Create AGENTIC agent
    print("🚀 Initializing AGENTIC MetadataValidatorAgent...")
    agent = MetadataValidatorAgent(
        dynamo_client=dynamo_client,
        chroma_client=chroma_client,
        places_api=places_client,
        enable_tracing=True,
        mode="agentic",  # <-- This is the key change!
    )
    print(f"✅ MetadataValidatorAgent created (mode={agent.mode})")

    # Run validation
    print(f"🔄 Validating receipt: image_id={target.image_id[:12]}..., receipt_id={target.receipt_id}")
    print("-" * 60)

    result = await agent.validate(
        image_id=target.image_id,
        receipt_id=target.receipt_id,
    )

    # Print result
    print("-" * 60)
    print("📋 Validation Result:")
    print(f"   Status: {result.status.value}")
    print(f"   Confidence: {result.confidence:.2%}")
    print(f"   Reasoning: {result.reasoning[:500]}..." if len(result.reasoning) > 500 else f"   Reasoning: {result.reasoning}")

    if result.recommendations:
        print("   Evidence:")
        for rec in result.recommendations[:5]:
            print(f"   - {rec}")

    print("✅ Agentic test completed!")


if __name__ == "__main__":
    env, secrets = setup_environment()
    asyncio.run(test_agentic_agent())

