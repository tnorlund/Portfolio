#!/usr/bin/env python3
"""
Simplified example showing how to update ChromaDB metadata and trigger compaction.

This shows the complete flow:
1. Create a metadata-only delta
2. Upload to S3
3. Send SQS message to trigger compaction
4. Compaction Lambda processes the delta automatically
"""

import json
import boto3
import uuid
from datetime import datetime

# Configuration (adjust for your environment)
BUCKET_NAME = "chromadb-vectors-dev-681647709217"
SQS_QUEUE_URL = "https://sqs.us-east-1.amazonaws.com/681647709217/chromadb-delta-queue-dev"


def trigger_metadata_update_compaction():
    """
    Demonstrates triggering compaction after a metadata update.
    
    In practice, this happens automatically when you use produce_embedding_delta(),
    but this shows the manual SQS message format.
    """
    
    # Initialize SQS client
    sqs = boto3.client('sqs', region_name='us-east-1')
    
    # Create a batch ID for tracking
    batch_id = f"metadata-update-{uuid.uuid4().hex[:8]}"
    
    # Simulate that we've already created a delta in S3
    # In reality, produce_embedding_delta() does this
    delta_id = uuid.uuid4().hex
    delta_key = f"words/delta/{delta_id}/"
    
    print(f"Triggering compaction for delta: {delta_key}")
    
    # Message format for the compaction Lambda
    message = {
        "operation": "process_chunk",
        "batch_id": batch_id,
        "chunk_index": 0,
        "database": "words",  # or "lines" for line embeddings
        "delta_results": [
            {
                "delta_key": delta_key,
                "delta_id": delta_id,
                "embedding_count": 1,  # Just one record updated
                "collection": "receipt_words"
            }
        ]
    }
    
    # Send to SQS
    response = sqs.send_message(
        QueueUrl=SQS_QUEUE_URL,
        MessageBody=json.dumps(message),
        MessageAttributes={
            'operation': {
                'StringValue': 'process_chunk',
                'DataType': 'String'
            },
            'database': {
                'StringValue': 'words',
                'DataType': 'String'
            }
        }
    )
    
    print(f"SQS Message sent: {response['MessageId']}")
    print(f"Message body: {json.dumps(message, indent=2)}")
    
    print("\nWhat happens next:")
    print("1. Compaction Lambda receives the SQS message")
    print("2. Downloads the delta from S3")
    print("3. Processes it in chunks (for large batches)")
    print("4. Performs final merge into snapshot")
    print("5. Updates both timestamped and 'latest' snapshots")
    
    return response


def show_compaction_flow():
    """
    Shows the complete flow of how compaction works.
    """
    
    print("=" * 70)
    print("ChromaDB Compaction Flow for Metadata Updates")
    print("=" * 70)
    
    print("\n📝 Step 1: Update Record Metadata")
    print("-" * 40)
    print("You have a record that needs metadata update:")
    print("  Record ID: WORD#img123#1#2#3")
    print("  Update: Add validated label")
    
    print("\n📦 Step 2: Create Delta")
    print("-" * 40)
    print("Use produce_embedding_delta() to create a delta:")
    print("""
    from receipt_label.utils.chroma_s3_helpers import produce_embedding_delta
    
    result = produce_embedding_delta(
        ids=["WORD#img123#1#2#3"],
        embeddings=[[0.1, 0.2, ...]],  # Keep existing embedding
        documents=["original_text"],
        metadatas=[{
            "label_status": "validated",
            "validated_labels": ",MERCHANT_NAME,"
        }],
        bucket_name="chromadb-vectors-dev-681647709217",
        collection_name="receipt_words",
        database_name="words",
        sqs_queue_url="https://sqs.../chromadb-delta-queue-dev"
    )
    """)
    
    print("\n📤 Step 3: Automatic SQS Notification")
    print("-" * 40)
    print("produce_embedding_delta() automatically sends SQS message")
    print("Message format:")
    print("""
    {
        "delta_key": "words/delta/abc123/",
        "collection": "receipt_words",
        "database": "words",
        "vector_count": 1,
        "timestamp": "2024-01-19T10:30:00Z"
    }
    """)
    
    print("\n⚙️ Step 4: Compaction Lambda Processing")
    print("-" * 40)
    print("The Lambda handler (unified_embedding/handlers/compaction.py):")
    print("  1. Receives SQS message")
    print("  2. Groups deltas into chunks of 10")
    print("  3. Processes chunks in parallel (no lock needed)")
    print("  4. Performs final merge (acquires lock)")
    print("  5. Updates snapshot in S3")
    
    print("\n✅ Step 5: Updated Snapshot Available")
    print("-" * 40)
    print("New snapshot locations:")
    print(f"  Latest: s3://{BUCKET_NAME}/words/snapshot/latest/")
    print(f"  Timestamped: s3://{BUCKET_NAME}/words/snapshot/timestamped/20240119_103000/")
    
    print("\n🔍 Step 6: Query Updated Data")
    print("-" * 40)
    print("Other services can now query the updated metadata:")
    print("""
    from receipt_label.utils.chroma_s3_helpers import consume_snapshot_readonly
    
    chroma = consume_snapshot_readonly(
        bucket_name="chromadb-vectors-dev-681647709217",
        database_name="words"
    )
    
    results = chroma.query_vectors(
        collection_name="receipt_words",
        ids=["WORD#img123#1#2#3"]
    )
    """)


if __name__ == "__main__":
    # Show the complete flow
    show_compaction_flow()
    
    print("\n" + "=" * 70)
    print("Triggering Example Compaction")
    print("=" * 70)
    
    # Uncomment to actually trigger compaction
    # trigger_metadata_update_compaction()
    
    print("\nTo actually trigger compaction, uncomment the function call above.")
    print("Or use the update_chromadb_metadata.py script for real updates.")