#!/usr/bin/env python3
"""
Download ChromaDB snapshot and check if receipt embeddings exist.

Usage:
    python scripts/check_chromadb_snapshot_for_receipt.py \
        --image-id 13da1048-3888-429f-b2aa-b3e15341da5e \
        --receipt-id 1
"""

import argparse
import os
import sys
import tempfile
from pathlib import Path

# Add repo root to path
repo_root = Path(__file__).parent.parent
sys.path.insert(0, str(repo_root))

try:
    from receipt_chroma import ChromaClient
    from receipt_chroma.s3 import download_snapshot_atomic
    from receipt_chroma.constants import ChromaDBCollection
    CHROMADB_AVAILABLE = True
except ImportError:
    # Try alternative import path
    try:
        from receipt_chroma.data.chroma_client import ChromaClient
        from receipt_chroma.s3 import download_snapshot_atomic
        from receipt_chroma.constants import ChromaDBCollection
        CHROMADB_AVAILABLE = True
    except ImportError:
        print("❌ ChromaDB not available - cannot check embeddings")
        sys.exit(1)


def setup_environment() -> dict:
    """Load environment variables."""
    chromadb_bucket = os.environ.get("CHROMADB_BUCKET")

    # Try loading from Pulumi if not set
    try:
        from receipt_dynamo.data._pulumi import load_env
        project_root = Path(__file__).parent.parent
        infra_dir = project_root / "infra"
        env = load_env("dev", working_dir=str(infra_dir))

        if not chromadb_bucket:
            chromadb_bucket = (
                env.get("embedding_chromadb_bucket_name") or
                env.get("chromadb_bucket_name")
            )
    except Exception as e:
        print(f"⚠️  Could not load from Pulumi: {e}")

    if not chromadb_bucket:
        raise ValueError("CHROMADB_BUCKET must be set")

    return {
        "chromadb_bucket": chromadb_bucket,
    }


def check_receipt_in_snapshot(
    image_id: str,
    receipt_id: int,
    collection_name: str,
    chromadb_bucket: str,
    temp_dir: Path,
) -> dict:
    """Download snapshot and check for receipt embeddings."""
    print(f"\n   📥 Downloading {collection_name} snapshot...")

    download_result = download_snapshot_atomic(
        bucket=chromadb_bucket,
        collection=collection_name,
        local_path=str(temp_dir),
        verify_integrity=True,
    )

    if download_result.get("status") != "downloaded":
        return {
            "collection": collection_name,
            "status": "error",
            "error": download_result.get("error", "Download failed"),
            "count": 0,
        }

    version_id = download_result.get("version_id", "unknown")
    print(f"      ✅ Downloaded version: {version_id}")

    # Open the snapshot
    print(f"      🔍 Checking for receipt embeddings...")
    chroma_client = ChromaClient(
        persist_directory=str(temp_dir),
        mode="read",
        metadata_only=True,
    )

    try:
        collection = chroma_client.get_collection(collection_name)

        # Try string match first
        where_str = {
            "$and": [
                {"image_id": {"$eq": image_id}},
                {"receipt_id": {"$eq": str(receipt_id)}},
            ]
        }
        results_str = collection.get(where=where_str, limit=100000)
        count = len(results_str.get("ids", [])) if results_str else 0

        # Fallback: try numeric match
        if count == 0:
            where_int = {
                "$and": [
                    {"image_id": {"$eq": image_id}},
                    {"receipt_id": {"$eq": receipt_id}},
                ]
            }
            results_int = collection.get(where=where_int, limit=100000)
            count = len(results_int.get("ids", [])) if results_int else 0

        # Get total count for context
        total_count = collection.count() if hasattr(collection, "count") else None

        chroma_client.close()

        return {
            "collection": collection_name,
            "status": "ok",
            "version_id": version_id,
            "count": count,
            "total_count": total_count,
        }
    except Exception as e:
        chroma_client.close()
        return {
            "collection": collection_name,
            "status": "error",
            "error": str(e),
            "count": 0,
        }


def main():
    parser = argparse.ArgumentParser(
        description="Check if receipt embeddings exist in ChromaDB snapshot"
    )
    parser.add_argument(
        "--image-id",
        required=True,
        help="Image ID",
    )
    parser.add_argument(
        "--receipt-id",
        type=int,
        required=True,
        help="Receipt ID to check",
    )

    args = parser.parse_args()

    config = setup_environment()
    chromadb_bucket = config["chromadb_bucket"]

    print(f"🔍 Checking ChromaDB snapshot for receipt {args.receipt_id}")
    print(f"   Image ID: {args.image_id}")
    print(f"   Bucket: {chromadb_bucket}")
    print("=" * 60)

    # Create temp directory for downloads
    temp_base = tempfile.mkdtemp(prefix="chroma_check_")

    results = {}
    for collection_name in ["lines", "words"]:
        temp_dir = Path(temp_base) / collection_name
        temp_dir.mkdir(parents=True, exist_ok=True)

        result = check_receipt_in_snapshot(
            args.image_id,
            args.receipt_id,
            collection_name,
            chromadb_bucket,
            temp_dir,
        )
        results[collection_name] = result

    # Print summary
    print(f"\n📊 Summary:")
    print("=" * 60)

    total_found = 0
    for collection_name, result in results.items():
        if result["status"] == "ok":
            count = result["count"]
            total_count = result.get("total_count", "unknown")
            version_id = result.get("version_id", "unknown")

            status = "✅ EXISTS" if count > 0 else "❌ NOT FOUND"
            print(f"   {collection_name}:")
            print(f"      {status} - {count} embeddings found")
            print(f"      Total in snapshot: {total_count}")
            print(f"      Snapshot version: {version_id}")
            total_found += count
        else:
            print(f"   {collection_name}:")
            print(f"      ❌ ERROR: {result.get('error', 'Unknown error')}")

    print(f"\n📈 Total embeddings found: {total_found}")

    if total_found > 0:
        print(f"\n⚠️  Receipt embeddings still exist in ChromaDB!")
        print(f"   The enhanced compactor may not have processed the deletion yet,")
        print(f"   or the deletion failed.")
    else:
        print(f"\n✅ No receipt embeddings found in ChromaDB!")
        print(f"   The enhanced compactor successfully deleted the embeddings.")


if __name__ == "__main__":
    main()

