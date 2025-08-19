#!/usr/bin/env python3
"""
Script to update metadata for a single ChromaDB record and trigger compaction.

This demonstrates how to:
1. Query an existing record from ChromaDB
2. Update only its metadata
3. Create a delta with the updated metadata
4. Trigger compaction via SQS
"""

import json
import os
import sys
import uuid
from typing import Dict, Any, Optional

import boto3
from receipt_label.utils.chroma_s3_helpers import (
    produce_embedding_delta,
    consume_snapshot_readonly
)
from receipt_label.utils import get_client_manager


def update_single_record_metadata(
    record_id: str,
    metadata_updates: Dict[str, Any],
    collection_name: str = "receipt_words",
    database_name: str = "words",
    bucket_name: Optional[str] = None,
    sqs_queue_url: Optional[str] = None,
    dry_run: bool = False
) -> Dict[str, Any]:
    """
    Update metadata for a single ChromaDB record and trigger compaction.
    
    Args:
        record_id: The ID of the record to update (e.g., "WORD#img123#1#2#3")
        metadata_updates: Dictionary of metadata fields to update
        collection_name: ChromaDB collection name
        database_name: Database name ("words" or "lines")
        bucket_name: S3 bucket name (uses env var if not provided)
        sqs_queue_url: SQS queue URL (uses env var if not provided)
        dry_run: If True, shows what would be updated without making changes
    
    Returns:
        Dictionary with operation results
    """
    
    # Get configuration from environment if not provided
    if not bucket_name:
        bucket_name = os.environ.get("CHROMADB_BUCKET", "chromadb-vectors-dev-681647709217")
    
    if not sqs_queue_url:
        sqs_queue_url = os.environ.get(
            "COMPACTION_QUEUE_URL",
            "https://sqs.us-east-1.amazonaws.com/681647709217/chromadb-delta-queue-dev"
        )
    
    print(f"Configuration:")
    print(f"  Bucket: {bucket_name}")
    print(f"  Queue: {sqs_queue_url}")
    print(f"  Database: {database_name}")
    print(f"  Collection: {collection_name}")
    print(f"  Record ID: {record_id}")
    print()
    
    # Step 1: Read the current snapshot to get the existing record
    print("Step 1: Reading current snapshot from S3...")
    
    try:
        # Use the consume_snapshot_readonly helper to get ChromaDB client
        chroma_client = consume_snapshot_readonly(
            bucket_name=bucket_name,
            database_name=database_name,
            collection_name=collection_name
        )
        
        # Query for the specific record
        result = chroma_client.query_vectors(
            collection_name=collection_name,
            ids=[record_id],
            include=["embeddings", "documents", "metadatas"]
        )
        
        if not result["ids"]:
            print(f"ERROR: Record {record_id} not found in {database_name}/{collection_name}")
            return {"status": "error", "message": "Record not found"}
        
        print(f"Found record: {record_id}")
        print(f"Current metadata: {json.dumps(result['metadatas'][0], indent=2)}")
        
    except Exception as e:
        print(f"ERROR reading snapshot: {e}")
        return {"status": "error", "message": str(e)}
    
    # Step 2: Update the metadata
    print("\nStep 2: Updating metadata...")
    
    # Get existing data
    existing_id = result["ids"][0]
    existing_embedding = result["embeddings"][0]
    existing_document = result["documents"][0]
    existing_metadata = result["metadatas"][0]
    
    # Apply metadata updates
    updated_metadata = existing_metadata.copy()
    updated_metadata.update(metadata_updates)
    
    print(f"Metadata updates to apply: {json.dumps(metadata_updates, indent=2)}")
    print(f"Updated metadata: {json.dumps(updated_metadata, indent=2)}")
    
    if dry_run:
        print("\nDRY RUN: Would create delta with above changes")
        return {"status": "dry_run", "updates": metadata_updates}
    
    # Step 3: Create a delta with the updated metadata
    print("\nStep 3: Creating delta with updated metadata...")
    
    try:
        delta_result = produce_embedding_delta(
            ids=[existing_id],
            embeddings=[existing_embedding],  # Keep same embedding
            documents=[existing_document],    # Keep same document
            metadatas=[updated_metadata],     # Updated metadata
            bucket_name=bucket_name,
            collection_name=collection_name,
            database_name=database_name,
            sqs_queue_url=sqs_queue_url,  # This triggers compaction
            batch_id=f"metadata-update-{uuid.uuid4().hex[:8]}"
        )
        
        print(f"Delta created successfully:")
        print(f"  Delta ID: {delta_result['delta_id']}")
        print(f"  Delta Key: {delta_result['delta_key']}")
        print(f"  Embedding Count: {delta_result['embedding_count']}")
        
    except Exception as e:
        print(f"ERROR creating delta: {e}")
        return {"status": "error", "message": str(e)}
    
    # Step 4: The SQS message was sent automatically by produce_embedding_delta
    print("\nStep 4: Compaction triggered via SQS")
    print("The compaction Lambda will:")
    print("  1. Pick up the SQS message")
    print("  2. Download the delta from S3")
    print("  3. Merge it with existing snapshots")
    print("  4. Create an updated snapshot")
    
    return {
        "status": "success",
        "record_id": record_id,
        "delta_id": delta_result["delta_id"],
        "delta_key": delta_result["delta_key"],
        "updates": metadata_updates
    }


def main():
    """Example usage of the metadata update function."""
    
    # Example 1: Update a word's label status
    print("=" * 60)
    print("Example: Updating metadata for a word record")
    print("=" * 60)
    
    # This would be a real word ID from your system
    example_word_id = "WORD#image123#1#2#3"
    
    # Metadata fields to update
    metadata_updates = {
        "label_status": "validated",
        "validated_labels": ",MERCHANT_NAME,",
        "label_confidence": 0.95,
        "label_validated_at": "2024-01-19T10:30:00Z",
        "custom_field": "example_value"
    }
    
    # Perform the update (set dry_run=False to actually execute)
    result = update_single_record_metadata(
        record_id=example_word_id,
        metadata_updates=metadata_updates,
        collection_name="receipt_words",
        database_name="words",
        dry_run=True  # Set to False to actually update
    )
    
    print(f"\nResult: {json.dumps(result, indent=2)}")
    
    # Example 2: Update a line's metadata
    print("\n" + "=" * 60)
    print("Example: Updating metadata for a line record")
    print("=" * 60)
    
    example_line_id = "LINE#image456#1#5"
    
    line_metadata_updates = {
        "line_type": "header",
        "confidence": 0.88,
        "processing_status": "completed"
    }
    
    result = update_single_record_metadata(
        record_id=example_line_id,
        metadata_updates=line_metadata_updates,
        collection_name="receipt_lines",
        database_name="lines",
        dry_run=True  # Set to False to actually update
    )
    
    print(f"\nResult: {json.dumps(result, indent=2)}")


if __name__ == "__main__":
    # You can also run this with command-line arguments
    if len(sys.argv) > 1:
        record_id = sys.argv[1]
        
        # Parse metadata updates from remaining arguments
        metadata_updates = {}
        for arg in sys.argv[2:]:
            if "=" in arg:
                key, value = arg.split("=", 1)
                # Try to parse as JSON, otherwise treat as string
                try:
                    value = json.loads(value)
                except:
                    pass
                metadata_updates[key] = value
        
        if metadata_updates:
            print(f"Updating record {record_id} with: {metadata_updates}")
            result = update_single_record_metadata(
                record_id=record_id,
                metadata_updates=metadata_updates,
                dry_run=False
            )
            print(f"Result: {json.dumps(result, indent=2)}")
        else:
            print("Usage: python update_chromadb_metadata.py <record_id> key1=value1 key2=value2 ...")
    else:
        # Run examples
        main()