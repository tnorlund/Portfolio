#!/usr/bin/env python3
"""
Local test script for ChromaDB delta table creation.

This script simulates the delta creation process that happens in the
poll_line_embedding_batch_handler Lambda without needing to run the
full Step Function pipeline.
"""

import json
import os
import tempfile
import uuid
from datetime import datetime
from typing import List, Dict, Any

# Set up test environment variables if not already set
if "CHROMADB_BUCKET" not in os.environ:
    os.environ["CHROMADB_BUCKET"] = "chromadb-vectors-dev-681647709217"

# Import the necessary functions
from receipt_label.utils.chroma_s3_helpers import produce_embedding_delta


def create_test_line_embedding_results() -> List[dict]:
    """Create sample line embedding results similar to OpenAI batch output."""
    # Simulate results from OpenAI batch API
    results = []
    
    # Create a few test lines
    test_lines = [
        {
            "custom_id": "IMAGE#abc123#RECEIPT#00001#LINE#00001",
            "text": "WALMART SUPERCENTER",
            "embedding": [0.1] * 1536,  # Fake embedding vector
        },
        {
            "custom_id": "IMAGE#abc123#RECEIPT#00001#LINE#00002", 
            "text": "123 MAIN ST",
            "embedding": [0.2] * 1536,
        },
        {
            "custom_id": "IMAGE#abc123#RECEIPT#00001#LINE#00003",
            "text": "ANYTOWN, USA 12345",
            "embedding": [0.3] * 1536,
        },
        {
            "custom_id": "IMAGE#abc123#RECEIPT#00001#LINE#00004",
            "text": "SUBTOTAL         $45.67",
            "embedding": [0.4] * 1536,
        },
        {
            "custom_id": "IMAGE#abc123#RECEIPT#00001#LINE#00005",
            "text": "TAX              $3.21", 
            "embedding": [0.5] * 1536,
        },
        {
            "custom_id": "IMAGE#abc123#RECEIPT#00001#LINE#00006",
            "text": "TOTAL            $48.88",
            "embedding": [0.6] * 1536,
        },
    ]
    
    # Format as OpenAI batch results
    for line in test_lines:
        results.append({
            "custom_id": line["custom_id"],
            "embedding": line["embedding"],
        })
    
    return results


def create_test_descriptions(results: List[dict]) -> dict:
    """Create test receipt descriptions to match the embedding results."""
    # Parse unique image and receipt IDs
    image_id = results[0]["custom_id"].split("#")[1]
    receipt_id = int(results[0]["custom_id"].split("#")[3])
    
    # Create mock receipt details
    from receipt_dynamo.entities import ReceiptLine, ReceiptMetadata
    
    lines = []
    for i, result in enumerate(results):
        line_id = int(result["custom_id"].split("#")[5])
        
        # Determine line text based on line_id
        line_texts = {
            1: "WALMART SUPERCENTER",
            2: "123 MAIN ST", 
            3: "ANYTOWN, USA 12345",
            4: "SUBTOTAL         $45.67",
            5: "TAX              $3.21",
            6: "TOTAL            $48.88",
        }
        
        lines.append(ReceiptLine(
            image_id=image_id,
            receipt_id=receipt_id,
            line_id=line_id,
            text=line_texts.get(line_id, f"Line {line_id}"),
            confidence=0.95,
            bounding_box={"x": 100, "y": 50 + (i * 30), "width": 300, "height": 25},
            embedding_status="PENDING",
        ))
    
    # Create mock metadata
    metadata = ReceiptMetadata(
        image_id=image_id,
        receipt_id=receipt_id,
        merchant_name="Walmart",
        canonical_merchant_name="WALMART INC.",
        purchase_date="2024-01-15",
        total_amount=48.88,
    )
    
    # Return in expected format
    return {
        image_id: {
            receipt_id: {
                "receipt": None,  # Not needed for delta creation
                "lines": lines,
                "words": [],  # Not needed for line embeddings
                "letters": [],  # Not needed  
                "labels": [],  # Not needed
                "metadata": metadata,
                "sections": [],  # Could add if testing section labels
            }
        }
    }


def test_delta_creation_direct():
    """Test creating a delta directly using produce_embedding_delta."""
    print("\n=== Test 1: Direct Delta Creation ===")
    
    # Create test data
    ids = ["LINE#1", "LINE#2", "LINE#3"]
    embeddings = [[0.1] * 1536, [0.2] * 1536, [0.3] * 1536]
    documents = ["Test line 1", "Test line 2", "Test line 3"]
    metadatas = [
        {"line_id": 1, "merchant": "Test Store"},
        {"line_id": 2, "merchant": "Test Store"},
        {"line_id": 3, "merchant": "Test Store"},
    ]
    
    # Create delta without SQS notification
    result = produce_embedding_delta(
        ids=ids,
        embeddings=embeddings,
        documents=documents,
        metadatas=metadatas,
        collection_name="receipt_lines",
        bucket_name=None,  # Will use CHROMADB_BUCKET env var
        sqs_queue_url=None,  # Skip SQS notification for local test
        batch_id=f"test-batch-{uuid.uuid4().hex[:8]}",
    )
    
    print(f"✓ Delta created successfully!")
    print(f"  Delta ID: {result['delta_id']}")
    print(f"  Delta Key: {result['delta_key']}")
    print(f"  Embeddings: {result['embedding_count']}")
    
    return result


def test_line_embedding_delta():
    """Test the full line embedding delta creation process."""
    print("\n=== Test 2: Line Embedding Delta Creation (Simulating Lambda) ===")
    
    # Import the actual function used in the Lambda
    from receipt_label.embedding.line.poll import save_line_embeddings_as_delta
    
    # Create test data
    results = create_test_line_embedding_results()
    descriptions = create_test_descriptions(results)
    batch_id = f"line-batch-{uuid.uuid4().hex[:8]}"
    
    print(f"Created {len(results)} test line embeddings")
    print(f"Batch ID: {batch_id}")
    
    # Temporarily remove SQS queue URL to prevent notifications
    original_queue_url = os.environ.get("COMPACTION_QUEUE_URL")
    if "COMPACTION_QUEUE_URL" in os.environ:
        del os.environ["COMPACTION_QUEUE_URL"]
    
    try:
        # Call the actual function used in the Lambda
        delta_result = save_line_embeddings_as_delta(
            results=results,
            descriptions=descriptions,
            batch_id=batch_id,
        )
        
        print(f"\n✓ Delta created successfully!")
        print(f"  Delta ID: {delta_result['delta_id']}")
        print(f"  Delta Key: {delta_result['delta_key']}")
        print(f"  Embeddings: {delta_result['embedding_count']}")
        
        # Show sample metadata structure
        print(f"\n  Sample line metadata:")
        print(f"    - image_id, receipt_id, line_id")
        print(f"    - text, confidence, position (x,y,width,height)")
        print(f"    - prev_line, next_line context")
        print(f"    - merchant_name")
        
    finally:
        # Restore original queue URL
        if original_queue_url:
            os.environ["COMPACTION_QUEUE_URL"] = original_queue_url
    
    return delta_result


def verify_delta_in_s3(delta_key: str):
    """Verify the delta was uploaded to S3."""
    print(f"\n=== Verifying Delta in S3 ===")
    
    try:
        import boto3
        s3 = boto3.client("s3")
        
        bucket = os.environ["CHROMADB_BUCKET"]
        
        # List objects under the delta prefix
        response = s3.list_objects_v2(
            Bucket=bucket,
            Prefix=delta_key,
        )
        
        if "Contents" in response:
            print(f"✓ Delta found in S3 bucket: {bucket}")
            print(f"  Files in delta:")
            for obj in response["Contents"]:
                print(f"    - {obj['Key']} ({obj['Size']} bytes)")
        else:
            print(f"✗ Delta not found in S3")
            
    except Exception as e:
        print(f"✗ Error checking S3: {e}")


def main():
    """Run all tests."""
    print("ChromaDB Delta Creation Test")
    print("=" * 50)
    print(f"Using bucket: {os.environ.get('CHROMADB_BUCKET', 'NOT SET')}")
    print(f"Timestamp: {datetime.utcnow().isoformat()}")
    
    # Test 1: Direct delta creation
    result1 = test_delta_creation_direct()
    
    # Test 2: Line embedding delta (simulating Lambda)
    result2 = test_line_embedding_delta()
    
    # Verify deltas in S3
    verify_delta_in_s3(result1["delta_key"])
    verify_delta_in_s3(result2["delta_key"])
    
    print("\n" + "=" * 50)
    print("✓ All tests completed!")
    print("\nNext steps:")
    print("1. Check S3 bucket for delta files")
    print("2. Run compaction process to merge deltas into snapshot")
    print("3. Query the snapshot to verify data")


if __name__ == "__main__":
    main()