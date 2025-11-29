#!/usr/bin/env python3
"""Pick a random receipt and test label suggestion agent on it."""

import asyncio
import os
import random
import subprocess
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from receipt_dynamo.data.dynamo_client import DynamoClient


def list_receipts(table_name: str, limit: int = 100):
    """List receipts from DynamoDB."""
    print(f"📋 Listing receipts from {table_name}...")
    client = DynamoClient(table_name=table_name)

    # Get receipts using list_receipt_metadatas (more efficient)
    receipts = []
    last_evaluated_key = None

    while len(receipts) < limit:
        metadatas, last_evaluated_key = client.list_receipt_metadatas(
            limit=min(100, limit - len(receipts)),
            last_evaluated_key=last_evaluated_key,
        )

        for metadata in metadatas:
            receipts.append((metadata.image_id, metadata.receipt_id))

        if not last_evaluated_key:
            break

    print(f"✅ Found {len(receipts)} receipts")
    return receipts


def setup_environment():
    """Load secrets and outputs from Pulumi."""
    from receipt_dynamo.data._pulumi import load_env, load_secrets

    infra_dir = project_root / "infra"
    env = load_env("dev", working_dir=str(infra_dir))
    secrets = load_secrets("dev", working_dir=str(infra_dir))

    # DynamoDB
    table_name = env.get("dynamodb_table_name") or env.get("receipts_table_name")
    if table_name:
        os.environ["DYNAMODB_TABLE_NAME"] = table_name
        os.environ["RECEIPT_AGENT_DYNAMO_TABLE_NAME"] = table_name
        print(f"📊 DynamoDB Table: {table_name}")
    else:
        print("⚠️  DynamoDB table name not found")
        return None, None

    # API Keys
    openai_key = secrets.get("portfolio:OPENAI_API_KEY")
    if openai_key:
        os.environ["OPENAI_API_KEY"] = openai_key
        print("✅ OpenAI API key loaded")

    ollama_key = secrets.get("portfolio:OLLAMA_API_KEY")
    if ollama_key:
        os.environ["OLLAMA_API_KEY"] = ollama_key
        print("✅ Ollama API key loaded")

    return env, secrets


def run_test():
    """Pick a random receipt and test label suggestion agent."""
    # Setup environment first
    print("🔧 Loading environment from Pulumi...")
    env, secrets = setup_environment()

    if not env:
        print("❌ Failed to load environment")
        return 1

    table_name = os.environ.get("DYNAMODB_TABLE_NAME")
    if not table_name:
        print("❌ DynamoDB table name not found")
        return 1

    # List receipts
    receipts = list_receipts(table_name, limit=50)

    if not receipts:
        print("❌ No receipts found")
        return 1

    # Pick a random receipt
    image_id, receipt_id = random.choice(receipts)
    print(f"\n🎲 Selected random receipt:")
    print(f"   Image ID: {image_id}")
    print(f"   Receipt ID: {receipt_id}")

    # Run the test script as a subprocess
    print(f"\n🚀 Running label suggestion agent (dry-run)...")
    print("=" * 80)

    # Run as subprocess to avoid import issues
    script_path = project_root / "scripts" / "test_label_suggestion_agent.py"
    cmd = [
        sys.executable,
        str(script_path),
        image_id,
        str(receipt_id),
        "--table-name", table_name,
        "--dry-run",
    ]

    # Set PYTHONPATH to include receipt_chroma
    env = os.environ.copy()
    receipt_chroma_path = str(project_root / "receipt_chroma")
    if "PYTHONPATH" in env:
        env["PYTHONPATH"] = f"{receipt_chroma_path}:{env['PYTHONPATH']}"
    else:
        env["PYTHONPATH"] = receipt_chroma_path

    result = subprocess.run(cmd, env=env, cwd=str(project_root))
    return result.returncode


if __name__ == "__main__":
    sys.exit(run_test())

