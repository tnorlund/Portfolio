#!/usr/bin/env python3
"""
Test script for ChromaDB S3 upload process.

This script demonstrates how to:
1. Create embeddings locally with ChromaDB
2. Upload them as a delta to S3
3. Run compaction to merge into snapshot
"""

import os
import sys
import tempfile
from datetime import datetime
import boto3

from receipt_label.utils.chroma_client import ChromaDBClient
from receipt_label.utils.chroma_s3_helpers import (
    produce_embedding_delta,
    download_snapshot_locally,
)
from receipt_label.utils.chroma_compactor import ChromaCompactor
from receipt_dynamo import DynamoClient


def test_local_chromadb():
    """Test creating ChromaDB locally."""
    print("1. Testing local ChromaDB creation...")

    # Create temporary directory for ChromaDB
    with tempfile.TemporaryDirectory() as temp_dir:
        print(f"   Using temp directory: {temp_dir}")

        # Create ChromaDB client in delta mode
        chroma = ChromaDBClient(persist_directory=temp_dir, mode="delta")

        # Create some test data
        ids = [
            "IMAGE#test-123#RECEIPT#00001#LINE#00001#WORD#00001",
            "IMAGE#test-123#RECEIPT#00001#LINE#00001#WORD#00002",
            "IMAGE#test-123#RECEIPT#00001#LINE#00002#WORD#00001",
        ]

        # Simulate embeddings (normally from OpenAI)
        embeddings = [
            [0.1, 0.2, 0.3] * 512,  # 1536 dimensions
            [0.4, 0.5, 0.6] * 512,
            [0.7, 0.8, 0.9] * 512,
        ]

        documents = ["WALMART", "SUPERCENTER", "$12.99"]

        metadatas = [
            {"merchant_name": "WALMART", "line_id": 1, "word_id": 1},
            {"merchant_name": "WALMART", "line_id": 1, "word_id": 2},
            {
                "merchant_name": "WALMART",
                "line_id": 2,
                "word_id": 1,
                "is_price": True,
            },
        ]

        # Upsert vectors
        print("   Upserting vectors...")
        chroma.upsert_vectors(
            collection_name="words",
            ids=ids,
            embeddings=embeddings,
            documents=documents,
            metadatas=metadatas,
        )

        # Verify locally
        count = chroma.count("words")
        print(f"   ✓ Created {count} vectors locally")

        # Query test
        results = chroma.query(
            collection_name="words", query_texts=["walmart"], n_results=2
        )
        print(f"   ✓ Query returned {len(results['ids'][0])} results")


def test_s3_delta_upload():
    """Test uploading delta to S3."""
    print("\n2. Testing S3 delta upload...")

    # Check environment variables
    bucket_name = os.environ.get("CHROMADB_BUCKET")
    if not bucket_name:
        print("   ❌ Error: CHROMADB_BUCKET environment variable not set")
        print("   Set it to your S3 bucket name from Pulumi:")
        print("   export CHROMADB_BUCKET=chromadb-vectors-dev-{account_id}")
        return

    # Create test embeddings using helper function
    result = produce_embedding_delta(
        ids=[f"TEST#{datetime.now().isoformat()}#WORD#00001"],
        embeddings=[[0.1, 0.2, 0.3] * 512],
        documents=["test word"],
        metadatas=[{"test": True, "timestamp": datetime.now().isoformat()}],
        bucket_name=bucket_name,
        sqs_queue_url=None,  # Skip SQS for local testing
    )

    print(f"   ✓ Uploaded delta to S3: {result['delta_key']}")
    print(f"   Vectors uploaded: {result['vectors_uploaded']}")

    # Verify in S3
    s3 = boto3.client("s3")
    response = s3.list_objects_v2(
        Bucket=bucket_name,
        Prefix=result["delta_key"],
    )

    if "Contents" in response:
        print(f"   ✓ Verified {len(response['Contents'])} files in S3")
        for obj in response["Contents"]:
            print(f"     - {obj['Key']} ({obj['Size']} bytes)")


def test_manual_compaction():
    """Test running compaction manually."""
    print("\n3. Testing manual compaction...")

    # Check environment
    bucket_name = os.environ.get("CHROMADB_BUCKET")
    table_name = os.environ.get("DYNAMODB_TABLE_NAME")

    if not bucket_name or not table_name:
        print("   ❌ Error: Missing environment variables")
        print("   Required: CHROMADB_BUCKET, DYNAMODB_TABLE_NAME")
        return

    # Initialize compactor
    dynamo_client = DynamoClient(table_name)
    compactor = ChromaCompactor(
        dynamo_client=dynamo_client,
        bucket_name=bucket_name,
    )

    # List available deltas
    s3 = boto3.client("s3")
    response = s3.list_objects_v2(
        Bucket=bucket_name,
        Prefix="delta/",
        Delimiter="/",
    )

    delta_keys = []
    if "CommonPrefixes" in response:
        delta_keys = [prefix["Prefix"] for prefix in response["CommonPrefixes"]]
        print(f"   Found {len(delta_keys)} deltas to compact")
    else:
        print("   No deltas found to compact")
        return

    # Run compaction
    print("   Running compaction...")
    result = compactor.compact_deltas(delta_keys[:5])  # Limit to 5 for testing

    print(f"   Status: {result.status}")
    if result.status == "success":
        print(f"   ✓ Compacted {result.deltas_processed} deltas")
        print(f"   New snapshot: {result.snapshot_key}")
        print(f"   Duration: {result.duration_seconds:.2f} seconds")
    elif result.status == "busy":
        print("   ⚠️  Another compaction is in progress")
    else:
        print(f"   ❌ Error: {result.error_message}")


def test_query_snapshot():
    """Test querying from snapshot."""
    print("\n4. Testing snapshot query...")

    bucket_name = os.environ.get("CHROMADB_BUCKET")
    if not bucket_name:
        print("   ❌ Error: CHROMADB_BUCKET not set")
        return

    # Download snapshot locally

    try:
        local_path = download_snapshot_locally(bucket_name=bucket_name)
        print(f"   ✓ Downloaded snapshot to {local_path}")

        # Query from snapshot
        chroma = ChromaDBClient(persist_directory=local_path, mode="read")

        # Try to query
        try:
            results = chroma.query(
                collection_name="words", query_texts=["test"], n_results=5
            )

            if results["ids"] and results["ids"][0]:
                print(f"   ✓ Found {len(results['ids'][0])} results")
                for i, doc in enumerate(results["documents"][0][:3]):
                    print(f"     {i+1}. {doc}")
            else:
                print("   No results found (snapshot might be empty)")

        except ValueError as e:
            print(f"   Collection might not exist yet: {e}")

    except Exception as e:
        print(f"   Error downloading snapshot: {e}")
        print("   This is normal if no snapshot exists yet")


def main():
    """Run all tests."""
    print("ChromaDB S3 Pipeline Test")
    print("=" * 50)

    # Set up test environment
    print("\nEnvironment Setup:")
    print(f"CHROMADB_BUCKET: {os.environ.get('CHROMADB_BUCKET', 'NOT SET')}")
    print(
        f"DYNAMODB_TABLE_NAME: {os.environ.get('DYNAMODB_TABLE_NAME', 'NOT SET')}",
    )
    print(f"AWS_REGION: {boto3.Session().region_name}")

    # Run tests
    test_local_chromadb()
    test_s3_delta_upload()
    test_manual_compaction()
    test_query_snapshot()

    print("\n" + "=" * 50)
    print("Test complete!")

    print("\nNext steps:")
    print("1. Set up EventBridge to trigger compaction periodically")
    print("2. Deploy producer Lambda to create deltas from receipts")
    print("3. Deploy query Lambda for searching vectors")


if __name__ == "__main__":
    # Check for required environment variables
    required_vars = ["OPENAI_API_KEY"]
    missing = [var for var in required_vars if not os.environ.get(var)]

    if missing:
        print(
            f"Error: Missing required environment variables: {', '.join(missing)}",
        )
        sys.exit(1)

    # Optional: Set these for full testing
    if not os.environ.get("CHROMADB_BUCKET"):
        print("\nTo test S3 upload, set CHROMADB_BUCKET:")
        print("export CHROMADB_BUCKET=chromadb-vectors-dev-{your-account-id}")
        print("(Get this from: pulumi stack output chromadb_bucket_name)")

    if not os.environ.get("DYNAMODB_TABLE_NAME"):
        print("\nTo test compaction, also set DYNAMODB_TABLE_NAME:")
        print("export DYNAMODB_TABLE_NAME=portfolio-dev")

    print()
    main()
