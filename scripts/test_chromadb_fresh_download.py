#!/usr/bin/env python3
"""
Test script to download fresh ChromaDB snapshot from S3 and test for corruption.

This script:
1. Backs up existing local ChromaDB copy (if exists)
2. Downloads fresh copy from S3
3. Tests ChromaDB operations to identify if the issue is with:
   - Local cached copy (corrupted)
   - S3 snapshot (corrupted)
   - Script logic (bug)
"""

import argparse
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import boto3
from receipt_agent.clients.factory import create_chroma_client
from receipt_agent.config.settings import get_settings


def download_fresh_snapshot(
    bucket: str, collection: str, local_path: str, backup_existing: bool = True
) -> tuple[str, Optional[str]]:
    """
    Download fresh ChromaDB snapshot from S3.

    Args:
        bucket: S3 bucket name
        collection: Collection name (e.g., "words")
        local_path: Local directory to download to
        backup_existing: Whether to backup existing copy if it exists

    Returns:
        Tuple of (timestamp, backup_path)
    """
    s3 = boto3.client("s3")

    # Backup existing if it exists
    backup_path = None
    chroma_db_file = os.path.join(local_path, "chroma.sqlite3")
    if backup_existing and os.path.exists(chroma_db_file):
        backup_path = f"{local_path}.backup.{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        print(f"📦 Backing up existing ChromaDB from {local_path} to {backup_path}")
        try:
            if os.path.exists(backup_path):
                shutil.rmtree(backup_path)
            shutil.copytree(local_path, backup_path)
            print(f"✅ Backup complete: {backup_path}")
        except Exception as e:
            print(f"⚠️  Failed to backup: {e}")
            backup_path = None

        # Remove existing to force fresh download
        print(f"🗑️  Removing existing cache to force fresh download")
        try:
            shutil.rmtree(local_path)
        except Exception as e:
            print(f"⚠️  Failed to remove existing cache: {e}")

    # Get latest pointer
    pointer_key = f"{collection}/snapshot/latest-pointer.txt"
    print(f"📥 Fetching latest snapshot pointer from s3://{bucket}/{pointer_key}")
    try:
        response = s3.get_object(Bucket=bucket, Key=pointer_key)
        timestamp = response["Body"].read().decode().strip()
        print(f"✅ Latest snapshot timestamp: {timestamp}")
    except Exception as e:
        print(f"❌ Failed to get pointer: {e}")
        raise

    # Download snapshot files
    prefix = f"{collection}/snapshot/timestamped/{timestamp}/"
    print(f"📥 Downloading snapshot from s3://{bucket}/{prefix}")
    paginator = s3.get_paginator("list_objects_v2")

    os.makedirs(local_path, exist_ok=True)
    downloaded_files = 0

    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            relative_path = key[len(prefix) :]
            if not relative_path or key.endswith(".snapshot_hash"):
                continue

            local_file_path = os.path.join(local_path, relative_path)
            os.makedirs(os.path.dirname(local_file_path), exist_ok=True)
            s3.download_file(bucket, key, local_file_path)
            downloaded_files += 1

    print(f"✅ Downloaded {downloaded_files} files to {local_path}")
    return timestamp, backup_path


def test_chromadb_operations(chroma_path: str, test_ids: Optional[List[str]] = None) -> dict:
    """
    Test ChromaDB operations to check for corruption.

    Args:
        chroma_path: Path to ChromaDB snapshot
        test_ids: Optional list of IDs to test (if None, uses sample IDs)

    Returns:
        Dict with test results
    """
    print(f"\n🧪 Testing ChromaDB operations at {chroma_path}")

    # Set environment for ChromaDB client
    os.environ["RECEIPT_AGENT_CHROMA_PERSIST_DIRECTORY"] = chroma_path

    settings = get_settings()
    chroma = create_chroma_client(settings=settings)

    results = {
        "collection_exists": False,
        "total_count": 0,
        "test_ids_found": 0,
        "test_ids_missing": 0,
        "errors": [],
    }

    try:
        # Test 1: Check if collection exists and get count
        print("  📊 Checking collection...")
        try:
            collection = chroma.get_collection("words")
            results["collection_exists"] = True
            results["total_count"] = collection.count()
            print(f"  ✅ Collection 'words' exists with {results['total_count']} items")
        except Exception as e:
            results["errors"].append(f"Collection check failed: {e}")
            print(f"  ❌ Collection check failed: {e}")
            return results

        # Test 2: Try to get some IDs
        if test_ids:
            print(f"  🔍 Testing {len(test_ids)} specific IDs...")
            for chroma_id in test_ids:
                try:
                    result = chroma.get(
                        collection_name="words",
                        ids=[chroma_id],
                        include=["embeddings", "metadatas"],
                    )
                    if result and result.get("ids") and chroma_id in result["ids"]:
                        results["test_ids_found"] += 1
                        print(f"    ✅ Found: {chroma_id}")
                    else:
                        results["test_ids_missing"] += 1
                        print(f"    ⚠️  Missing: {chroma_id}")
                except Exception as e:
                    results["test_ids_missing"] += 1
                    error_msg = str(e)
                    if "Internal error" in error_msg or "Error finding id" in error_msg:
                        results["errors"].append(f"InternalError for {chroma_id}: {e}")
                        print(f"    ❌ InternalError for {chroma_id}: {e}")
                    else:
                        results["errors"].append(f"Error for {chroma_id}: {e}")
                        print(f"    ❌ Error for {chroma_id}: {e}")
        else:
            # Test 3: Get a sample of IDs from the collection using peek()
            print("  🔍 Testing sample IDs from collection...")
            try:
                # Use peek() to get first 10 items (proper ChromaDB API)
                sample = collection.peek(limit=10)
                sample_ids = sample.get("ids", [])[:10]
                print(f"  📋 Testing {len(sample_ids)} sample IDs from peek()...")

                for chroma_id in sample_ids:
                    try:
                        result = chroma.get(
                            collection_name="words",
                            ids=[chroma_id],
                            include=["embeddings", "metadatas"],
                        )
                        if result and result.get("ids") and chroma_id in result["ids"]:
                            results["test_ids_found"] += 1
                            print(f"    ✅ Found: {chroma_id[:60]}...")
                        else:
                            results["test_ids_missing"] += 1
                            print(f"    ⚠️  Missing: {chroma_id[:60]}...")
                    except Exception as e:
                        results["test_ids_missing"] += 1
                        error_msg = str(e)
                        if "Internal error" in error_msg or "Error finding id" in error_msg:
                            results["errors"].append(f"InternalError for {chroma_id}: {e}")
                            print(f"    ❌ InternalError for {chroma_id[:60]}...")
                        else:
                            results["errors"].append(f"Error for {chroma_id}: {e}")
                            print(f"    ❌ Error for {chroma_id[:60]}...: {e}")

                print(f"  ✅ Found: {results['test_ids_found']}, Missing: {results['test_ids_missing']}")
            except Exception as e:
                results["errors"].append(f"Sample ID test failed: {e}")
                print(f"  ❌ Sample ID test failed: {e}")

            # Test 4: Try querying by embeddings (if we got any embeddings from peek)
            print("  🔍 Testing query by embeddings...")
            try:
                sample = collection.peek(limit=1)
                sample_embeddings = sample.get("embeddings", [])
                if sample_embeddings and len(sample_embeddings) > 0:
                    query_result = collection.query(
                        query_embeddings=[sample_embeddings[0]],
                        n_results=5,
                        include=["metadatas", "documents"],
                    )
                    if query_result and query_result.get("ids") and len(query_result["ids"][0]) > 0:
                        print(f"  ✅ Query by embedding works: found {len(query_result['ids'][0])} results")
                    else:
                        results["errors"].append("Query by embedding returned no results")
                        print(f"  ⚠️  Query by embedding returned no results")
                else:
                    print(f"  ⚠️  No embeddings in sample to test query")
            except Exception as e:
                results["errors"].append(f"Query by embedding failed: {e}")
                print(f"  ❌ Query by embedding failed: {e}")

    except Exception as e:
        results["errors"].append(f"Test failed: {e}")
        print(f"  ❌ Test failed: {e}")

    return results


def build_chromadb_word_id(image_id: str, receipt_id: int, line_id: int, word_id: int) -> str:
    """Build ChromaDB document ID for a word."""
    return f"IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}#WORD#{word_id:05d}"


def main():
    parser = argparse.ArgumentParser(
        description="Test ChromaDB snapshot download and operations"
    )
    parser.add_argument(
        "--bucket",
        type=str,
        default="chromadb-dev-shared-buckets-vectors-c239843",
        help="S3 bucket name",
    )
    parser.add_argument(
        "--collection",
        type=str,
        default="words",
        help="Collection name (default: words)",
    )
    parser.add_argument(
        "--local-path",
        type=str,
        help="Local path for ChromaDB (default: temp directory)",
    )
    parser.add_argument(
        "--test-ids",
        type=str,
        nargs="+",
        help="Specific ChromaDB IDs to test (format: IMAGE#...RECEIPT#...LINE#...WORD#...)",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Don't backup existing ChromaDB if it exists",
    )

    args = parser.parse_args()

    # Determine local path
    if args.local_path:
        local_path = args.local_path
    else:
        local_path = os.path.join(
            tempfile.gettempdir(), f"chromadb_test_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        )

    print(f"🔧 ChromaDB Fresh Download Test")
    print(f"   Bucket: {args.bucket}")
    print(f"   Collection: {args.collection}")
    print(f"   Local path: {local_path}")
    print()

    # Download fresh snapshot
    try:
        timestamp, backup_path = download_fresh_snapshot(
            bucket=args.bucket,
            collection=args.collection,
            local_path=local_path,
            backup_existing=not args.no_backup,
        )
        if backup_path:
            print(f"\n💾 Original copy backed up to: {backup_path}")
    except Exception as e:
        print(f"\n❌ Failed to download snapshot: {e}")
        sys.exit(1)

    # Test ChromaDB operations
    test_ids = args.test_ids if args.test_ids else None
    results = test_chromadb_operations(local_path, test_ids=test_ids)

    # Print summary
    print(f"\n📊 Test Summary:")
    print(f"   Collection exists: {results['collection_exists']}")
    print(f"   Total items: {results['total_count']}")
    print(f"   IDs found: {results['test_ids_found']}")
    print(f"   IDs missing: {results['test_ids_missing']}")
    print(f"   Errors: {len(results['errors'])}")

    if results["errors"]:
        print(f"\n❌ Errors encountered:")
        for error in results["errors"][:10]:  # Show first 10
            print(f"   - {error}")
        if len(results["errors"]) > 10:
            print(f"   ... and {len(results['errors']) - 10} more")

    if results["test_ids_missing"] > 0 or results["errors"]:
        print(f"\n⚠️  Issues detected - ChromaDB may be corrupted or IDs don't exist")
        sys.exit(1)
    else:
        print(f"\n✅ All tests passed - ChromaDB appears healthy")
        sys.exit(0)


if __name__ == "__main__":
    main()

