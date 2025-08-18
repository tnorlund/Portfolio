#!/usr/bin/env python3
"""
Script to find vectors similar to a hardcoded SPROUTS vector ID.

This script demonstrates how to:
1. Download the most recent ChromaDB lines snapshot from S3
2. Query for a specific vector by ID
3. Use its embedding to find semantically similar vectors
4. Display results with metadata

Usage:
    python scripts/find_similar_vectors.py
"""

import os
import sys
import tempfile
import shutil
from pathlib import Path
from typing import Dict, Any, List, Optional

# Add the project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import boto3
import chromadb


class VectorSimilarityFinder:
    """Helper class to find similar vectors in ChromaDB."""

    def __init__(self, bucket_name: Optional[str] = None):
        """Initialize with S3 bucket name."""
        self.bucket_name = bucket_name or os.environ.get(
            "CHROMADB_BUCKET",
            "embedding-infra-chromadb-buckets-vectors-ee775fc",
        )
        self.s3_client = boto3.client("s3")
        self.temp_dir = None
        self.client = None
        self.collection = None

    def get_latest_snapshot_path(self) -> str:
        """Find the most recent timestamped snapshot."""
        print("🔍 Finding most recent snapshot...")

        # List timestamped snapshots
        paginator = self.s3_client.get_paginator("list_objects_v2")
        pages = paginator.paginate(
            Bucket=self.bucket_name,
            Prefix="snapshot/timestamped/",
            Delimiter="/",
        )

        snapshots = []
        for page in pages:
            if "CommonPrefixes" in page:
                for prefix in page["CommonPrefixes"]:
                    snapshot_name = prefix["Prefix"].split("/")[-2]
                    if snapshot_name:  # Skip empty strings
                        snapshots.append(snapshot_name)

        if not snapshots:
            raise RuntimeError("No timestamped snapshots found")

        # Sort to get the most recent (assuming YYYYMMDD_HHMMSS format)
        latest_snapshot = sorted(snapshots)[-1]
        snapshot_path = f"snapshot/timestamped/{latest_snapshot}"

        print(f"📅 Using most recent snapshot: {latest_snapshot}")
        return snapshot_path

    def download_snapshot(self, snapshot_path: str) -> str:
        """Download snapshot from S3 to local directory."""
        self.temp_dir = tempfile.mkdtemp(prefix="chromadb_similarity_")
        print(f"📁 Using temporary directory: {self.temp_dir}")

        print(
            f"📥 Downloading snapshot from s3://{self.bucket_name}/{snapshot_path}"
        )

        # Download all files in the snapshot
        paginator = self.s3_client.get_paginator("list_objects_v2")
        pages = paginator.paginate(
            Bucket=self.bucket_name, Prefix=snapshot_path
        )

        file_count = 0
        total_size = 0

        for page in pages:
            if "Contents" not in page:
                continue

            for obj in page["Contents"]:
                s3_key = obj["Key"]

                # Calculate local file path
                relative_path = s3_key[len(snapshot_path) :].lstrip("/")
                if not relative_path:  # Skip the directory itself
                    continue

                local_file = Path(self.temp_dir) / relative_path

                # Create parent directories
                local_file.parent.mkdir(parents=True, exist_ok=True)

                # Download file
                self.s3_client.download_file(
                    self.bucket_name, s3_key, str(local_file)
                )

                file_count += 1
                total_size += obj["Size"]

        print(f"✅ Downloaded {file_count} files ({total_size:,} bytes)")
        return self.temp_dir

    def load_chromadb(self, local_dir: str) -> chromadb.Collection:
        """Load ChromaDB from local directory."""
        print(f"\n🔄 Loading ChromaDB from: {local_dir}")

        # Create ChromaDB client directly (avoid embedding function conflicts)
        settings = chromadb.config.Settings(anonymized_telemetry=False)
        self.client = chromadb.PersistentClient(
            path=local_dir, settings=settings
        )

        # Get the receipt_lines collection
        self.collection = self.client.get_collection("receipt_lines")
        count = self.collection.count()

        print(f"✅ Loaded collection 'receipt_lines' with {count:,} vectors")
        return self.collection

    def find_similar_vectors(
        self, target_vector_id: str, n_results: int = 10
    ) -> Dict[str, Any]:
        """Find vectors similar to the target vector."""
        print(f"\n🎯 Looking for target vector: {target_vector_id}")

        # Get the specific vector
        result = self.collection.get(
            ids=[target_vector_id],
            include=["documents", "metadatas", "embeddings"],
        )

        if not result["ids"]:
            print("❌ Target vector not found")
            return {"error": "Target vector not found"}

        # Display target vector info
        target_text = result["documents"][0]
        target_metadata = result["metadatas"][0]

        print(f"✅ Found target vector!")
        print(f'   Text: "{target_text}"')
        print(f"   Merchant: {target_metadata.get('merchant_name', 'N/A')}")
        print(
            f"   Receipt: #{target_metadata.get('receipt_id', 'N/A')} Line: #{target_metadata.get('line_id', 'N/A')}"
        )
        print(f"   Image ID: {target_metadata.get('image_id', 'N/A')}")
        print(f"   Confidence: {target_metadata.get('confidence', 'N/A')}")

        if target_metadata.get("prev_line"):
            print(f"   Previous: \"{target_metadata['prev_line']}\"")
        if target_metadata.get("next_line"):
            print(f"   Next: \"{target_metadata['next_line']}\"")

        # Use its embedding to find similar vectors
        if result["embeddings"] is None or len(result["embeddings"]) == 0:
            print("❌ No embedding found for target vector")
            return {"error": "No embedding found"}

        embedding = result["embeddings"][0]
        print(
            f'\n🔍 Finding {n_results} vectors similar to "{target_text}"...'
        )

        similar = self.collection.query(
            query_embeddings=[embedding],
            n_results=n_results,
            include=["documents", "metadatas", "distances"],
        )

        # Display results
        print(f"\n📊 Found {len(similar['ids'][0])} similar vectors:")
        print("=" * 80)

        results = []
        for i in range(len(similar["ids"][0])):
            dist = similar["distances"][0][i]
            text = similar["documents"][0][i]
            meta = similar["metadatas"][0][i]
            vector_id = similar["ids"][0][i]

            result_info = {
                "rank": i + 1,
                "vector_id": vector_id,
                "text": text,
                "distance": dist,
                "merchant": meta.get("merchant_name", "N/A"),
                "receipt_id": meta.get("receipt_id", "N/A"),
                "line_id": meta.get("line_id", "N/A"),
                "image_id": meta.get("image_id", "N/A"),
                "confidence": meta.get("confidence", "N/A"),
                "prev_line": meta.get("prev_line", ""),
                "next_line": meta.get("next_line", ""),
            }
            results.append(result_info)

            print(f'\n  {i+1}. "{text}" (distance: {dist:.4f})')
            print(f"     Merchant: {result_info['merchant']}")
            print(
                f"     Receipt: #{result_info['receipt_id']} Line: #{result_info['line_id']}"
            )
            print(f"     Image ID: {result_info['image_id']}")
            print(f"     Confidence: {result_info['confidence']}")

            if result_info["prev_line"]:
                print(f"     Previous: \"{result_info['prev_line']}\"")
            if result_info["next_line"]:
                print(f"     Next: \"{result_info['next_line']}\"")

        return {
            "target": {
                "vector_id": target_vector_id,
                "text": target_text,
                "metadata": target_metadata,
            },
            "similar_vectors": results,
        }

    def cleanup(self):
        """Clean up temporary directory."""
        if self.temp_dir and Path(self.temp_dir).exists():
            shutil.rmtree(self.temp_dir)
            print(f"\n🧹 Cleaned up temporary directory")


def main():
    """Main function to find similar vectors."""
    # Hardcoded SPROUTS vector ID (found from previous analysis)
    SPROUTS_VECTOR_ID = (
        "IMAGE#63aa7edd-cf85-42fd-8c62-363981a115fe#RECEIPT#00001#LINE#00028"
    )

    print("🚀 ChromaDB Vector Similarity Finder")
    print("=" * 50)

    finder = VectorSimilarityFinder()

    try:
        # Get the most recent snapshot
        snapshot_path = finder.get_latest_snapshot_path()

        # Download and load ChromaDB
        local_dir = finder.download_snapshot(snapshot_path)
        collection = finder.load_chromadb(local_dir)

        # Find similar vectors
        results = finder.find_similar_vectors(
            target_vector_id=SPROUTS_VECTOR_ID, n_results=10
        )

        if "error" in results:
            print(f"❌ Error: {results['error']}")
            return 1

        print(f"\n" + "=" * 80)
        print(
            f"🎉 Successfully found {len(results['similar_vectors'])} similar vectors!"
        )
        print(f"Target vector: \"{results['target']['text']}\"")

        # Summary of merchant diversity
        merchants = set()
        for vec in results["similar_vectors"]:
            if vec["merchant"] != "N/A":
                merchants.add(vec["merchant"])

        if merchants:
            print(f"Merchants represented: {', '.join(sorted(merchants))}")

        return 0

    except Exception as e:
        print(f"\n❌ Error: {e}")
        return 1

    finally:
        finder.cleanup()


if __name__ == "__main__":
    exit(main())
