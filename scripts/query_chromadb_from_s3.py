#!/usr/bin/env python3
"""
Example script to download ChromaDB vectors from S3 and query them locally.

This script demonstrates how to:
1. Download ChromaDB delta or snapshot files from S3
2. Load them using ChromaDBClient
3. Query the vectors for semantic search
4. Display results with metadata

Usage:
    # Query a specific delta
    python scripts/query_chromadb_from_s3.py --delta lines/delta/007dafa25ce04691ae6972c9ce9a06ce

    # Query the latest snapshot
    python scripts/query_chromadb_from_s3.py --snapshot

    # Query with custom search terms
    python scripts/query_chromadb_from_s3.py --delta <path> --query "grand total"
"""

import argparse
import os
import sys
import tempfile
import shutil
from pathlib import Path
from typing import Optional, List, Dict, Any

# Add the project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import boto3
from receipt_label.utils.chroma_client import ChromaDBClient


class ChromaDBQuerier:
    """Helper class to download and query ChromaDB data from S3."""

    def __init__(self, bucket_name: Optional[str] = None):
        """Initialize with S3 bucket name."""
        self.bucket_name = bucket_name or os.environ.get(
            "CHROMADB_BUCKET",
            "embedding-infra-chromadb-buckets-vectors-ee775fc",
        )
        self.s3_client = boto3.client("s3")
        self.temp_dir = None

    def download_from_s3(self, s3_prefix: str, local_dir: str) -> None:
        """Download ChromaDB files from S3 to local directory."""
        print(f"📥 Downloading from s3://{self.bucket_name}/{s3_prefix}")

        # List all objects with the prefix
        paginator = self.s3_client.get_paginator("list_objects_v2")
        pages = paginator.paginate(Bucket=self.bucket_name, Prefix=s3_prefix)

        file_count = 0
        total_size = 0

        for page in pages:
            if "Contents" not in page:
                continue

            for obj in page["Contents"]:
                s3_key = obj["Key"]

                # Calculate local file path
                relative_path = s3_key[len(s3_prefix) :].lstrip("/")
                if not relative_path:  # Skip the directory itself
                    continue

                local_file = Path(local_dir) / relative_path

                # Create parent directories
                local_file.parent.mkdir(parents=True, exist_ok=True)

                # Download file
                print(f"  ⬇️  {relative_path} ({obj['Size']:,} bytes)")
                self.s3_client.download_file(
                    self.bucket_name, s3_key, str(local_file)
                )

                file_count += 1
                total_size += obj["Size"]

        print(f"✅ Downloaded {file_count} files ({total_size:,} bytes)")

    def load_chromadb(
        self, local_dir: str, collection_name: str = "receipt_lines"
    ) -> ChromaDBClient:
        """Load ChromaDB from local directory."""
        print(f"\n🔄 Loading ChromaDB from: {local_dir}")

        # Check what files exist
        files = list(Path(local_dir).rglob("*"))
        print(f"  Found {len(files)} files/directories")

        # Create ChromaDB client in read mode
        client = ChromaDBClient(
            persist_directory=local_dir,
            collection_prefix="",  # No prefix for database-specific storage
            mode="read",
        )

        # Get collection info
        try:
            collection = client.get_collection(collection_name)
            count = collection.count()
            print(
                f"✅ Loaded collection '{collection_name}' with {count:,} vectors"
            )
        except Exception as e:
            print(
                f"⚠️  Warning: Could not load collection '{collection_name}': {e}"
            )
            # List available collections
            try:
                all_collections = client.client.list_collections()
                if all_collections:
                    print(
                        f"  Available collections: {[c.name for c in all_collections]}"
                    )
                    # Try the first available collection
                    if all_collections:
                        collection_name = all_collections[0].name
                        collection = client.get_collection(collection_name)
                        count = collection.count()
                        print(
                            f"  Using collection '{collection_name}' with {count:,} vectors"
                        )
            except Exception as e2:
                print(f"❌ Could not list collections: {e2}")
                raise

        return client, collection_name

    def query_vectors(
        self,
        client: ChromaDBClient,
        collection_name: str,
        query_texts: List[str],
        n_results: int = 5,
    ) -> Dict[str, Any]:
        """Query vectors and return results."""
        print(f"\n🔍 Querying collection '{collection_name}'...")

        results_dict = {}

        for query_text in query_texts:
            print(f"\n📝 Query: '{query_text}'")

            results = client.query_collection(
                collection_name=collection_name,
                query_texts=[query_text],
                n_results=n_results,
            )

            if not results["ids"] or not results["ids"][0]:
                print(f"  No results found")
                results_dict[query_text] = []
                continue

            query_results = []
            for i in range(len(results["ids"][0])):
                result = {
                    "id": results["ids"][0][i],
                    "text": (
                        results["documents"][0][i]
                        if results.get("documents")
                        else ""
                    ),
                    "distance": (
                        results["distances"][0][i]
                        if results.get("distances")
                        else 0
                    ),
                    "metadata": (
                        results["metadatas"][0][i]
                        if results.get("metadatas")
                        else {}
                    ),
                }
                query_results.append(result)

                # Display result
                print(
                    f"\n  📍 Result {i+1} (distance: {result['distance']:.4f}):"
                )
                print(f"     Text: {result['text']}")
                if result["metadata"]:
                    print(
                        f"     Merchant: {result['metadata'].get('merchant_name', 'Unknown')}"
                    )
                    print(
                        f"     Confidence: {result['metadata'].get('confidence', 'N/A')}"
                    )
                    if (
                        "prev_line" in result["metadata"]
                        and result["metadata"]["prev_line"]
                    ):
                        print(
                            f"     Previous line: {result['metadata']['prev_line']}"
                        )
                    if (
                        "next_line" in result["metadata"]
                        and result["metadata"]["next_line"]
                    ):
                        print(
                            f"     Next line: {result['metadata']['next_line']}"
                        )

            results_dict[query_text] = query_results

        return results_dict

    def display_sample_data(
        self, client: ChromaDBClient, collection_name: str, limit: int = 10
    ):
        """Display sample data from the collection."""
        print(f"\n📊 Sample data from collection '{collection_name}':")

        collection = client.get_collection(collection_name)
        sample = collection.get(limit=limit)

        if not sample["ids"]:
            print("  No data found in collection")
            return

        # Group by merchant
        merchants = {}
        for i in range(len(sample["ids"])):
            metadata = (
                sample["metadatas"][i] if sample.get("metadatas") else {}
            )
            merchant = metadata.get("merchant_name", "Unknown")

            if merchant not in merchants:
                merchants[merchant] = []

            merchants[merchant].append(
                {
                    "id": sample["ids"][i],
                    "text": (
                        sample["documents"][i]
                        if sample.get("documents")
                        else ""
                    ),
                    "metadata": metadata,
                }
            )

        print(f"\n  Found data from {len(merchants)} merchant(s):")
        for merchant, items in merchants.items():
            print(f"\n  🏪 {merchant} ({len(items)} lines):")
            for item in items[:3]:  # Show first 3 lines per merchant
                print(f"     - {item['text']}")

    def cleanup(self):
        """Clean up temporary directory."""
        if self.temp_dir and Path(self.temp_dir).exists():
            shutil.rmtree(self.temp_dir)
            print(f"\n🧹 Cleaned up temporary directory")


def main():
    """Main function to run the ChromaDB query example."""
    parser = argparse.ArgumentParser(
        description="Download and query ChromaDB vectors from S3",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # S3 source options
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument(
        "--delta",
        help="S3 path to delta directory (e.g., lines/delta/007dafa25ce04691ae6972c9ce9a06ce)",
    )
    source_group.add_argument(
        "--snapshot", action="store_true", help="Download the latest snapshot"
    )

    # Query options
    parser.add_argument(
        "--query",
        nargs="+",
        default=["total", "tax", "walmart", "subtotal"],
        help="Query terms to search for (default: total tax walmart subtotal)",
    )
    parser.add_argument(
        "--collection",
        default="receipt_lines",
        help="Collection name to query (default: receipt_lines)",
    )
    parser.add_argument(
        "--n-results",
        type=int,
        default=5,
        help="Number of results per query (default: 5)",
    )
    parser.add_argument(
        "--bucket",
        help="S3 bucket name (uses CHROMADB_BUCKET env var if not specified)",
    )
    parser.add_argument(
        "--show-sample",
        action="store_true",
        help="Show sample data from the collection",
    )
    parser.add_argument(
        "--keep-files",
        action="store_true",
        help="Keep downloaded files after script completes",
    )

    args = parser.parse_args()

    # Create querier
    querier = ChromaDBQuerier(bucket_name=args.bucket)

    try:
        # Create temporary directory
        if args.keep_files:
            download_dir = Path.cwd() / "chromadb_download"
            download_dir.mkdir(exist_ok=True)
            print(f"📁 Using persistent directory: {download_dir}")
        else:
            querier.temp_dir = tempfile.mkdtemp(prefix="chromadb_")
            download_dir = querier.temp_dir
            print(f"📁 Using temporary directory: {download_dir}")

        # Determine S3 path
        if args.delta:
            s3_prefix = args.delta.rstrip("/")
        else:  # snapshot
            s3_prefix = "snapshot/latest"

        # Download from S3
        querier.download_from_s3(s3_prefix, str(download_dir))

        # Load ChromaDB
        client, collection_name = querier.load_chromadb(
            str(download_dir), args.collection
        )

        # Show sample data if requested
        if args.show_sample:
            querier.display_sample_data(client, collection_name)

        # Query vectors
        results = querier.query_vectors(
            client, collection_name, args.query, args.n_results
        )

        # Summary
        print(f"\n" + "=" * 60)
        print(f"📈 Query Summary:")
        for query, query_results in results.items():
            print(f"  '{query}': {len(query_results)} results found")

        if args.keep_files:
            print(f"\n💾 Files kept in: {download_dir}")

    except Exception as e:
        print(f"\n❌ Error: {e}")
        return 1

    finally:
        if not args.keep_files:
            querier.cleanup()

    return 0


if __name__ == "__main__":
    exit(main())
