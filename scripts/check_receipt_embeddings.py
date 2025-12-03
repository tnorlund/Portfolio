#!/usr/bin/env python3
"""
Check if receipt embeddings exist in ChromaDB.

Usage:
    python scripts/check_receipt_embeddings.py \
        --image-id 13da1048-3888-429f-b2aa-b3e15341da5e \
        --receipt-id 1
"""

import argparse
import os
import sys
from pathlib import Path

# Add repo root to path
repo_root = Path(__file__).parent.parent
sys.path.insert(0, str(repo_root))

# Try to import ChromaDB client (may not be available)
try:
    from receipt_chroma import ChromaClient
    from receipt_chroma.constants import ChromaDBCollection
    CHROMADB_AVAILABLE = True
except ImportError:
    CHROMADB_AVAILABLE = False
    ChromaClient = None
    ChromaDBCollection = None


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


def check_receipt_embeddings(
    image_id: str,
    receipt_id: int,
) -> None:
    """Check if embeddings exist for a receipt in ChromaDB."""
    if not CHROMADB_AVAILABLE:
        print("❌ ChromaDB not available - cannot check embeddings")
        return

    config = setup_environment()
    chroma_client = ChromaClient(config["chromadb_bucket"])

    print(f"\n🔍 Checking embeddings for receipt {receipt_id} in image {image_id}")
    print("=" * 60)

    total_found = 0
    for collection_name in [ChromaDBCollection.RECEIPT_LINES, ChromaDBCollection.RECEIPT_WORDS]:
        try:
            collection = chroma_client.get_collection(collection_name)

            # Query for embeddings with this receipt
            results = collection.get(
                where={"image_id": image_id, "receipt_id": receipt_id},
            )

            count = len(results.get("ids", [])) if results else 0
            total_found += count

            status = "✅ EXISTS" if count > 0 else "❌ NOT FOUND"
            print(f"   {collection_name}: {count} embeddings {status}")

            if count > 0 and results.get("ids"):
                # Show first few IDs as examples
                sample_ids = results["ids"][:3]
                print(f"      Sample IDs: {sample_ids}")
                if len(results["ids"]) > 3:
                    print(f"      ... and {len(results['ids']) - 3} more")

        except Exception as e:
            print(f"   ⚠️  Error checking {collection_name}: {e}")

    print(f"\n📊 Summary:")
    if total_found > 0:
        print(f"   ✅ Found {total_found} embeddings total")
        print(f"   ⚠️  Receipt embeddings still exist in ChromaDB")
        print(f"   💡 They will need to be deleted or the receipt re-embedded")
    else:
        print(f"   ✅ No embeddings found")
        print(f"   ✅ Receipt embeddings have been deleted from ChromaDB")

    chroma_client.close()


def main():
    parser = argparse.ArgumentParser(
        description="Check if receipt embeddings exist in ChromaDB"
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

    check_receipt_embeddings(args.image_id, args.receipt_id)


if __name__ == "__main__":
    main()

