#!/usr/bin/env python3
"""
Download ChromaDB snapshots from S3 and check for receipt embeddings.

This script uses boto3 directly to avoid import issues.

Usage:
    python scripts/download_and_check_chromadb.py \
        --image-id 13da1048-3888-429f-b2aa-b3e15341da5e \
        --receipt-id 1
"""

import argparse
import json
import os
import sys
import tarfile
import tempfile
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

# Add repo root to path
repo_root = Path(__file__).parent.parent
sys.path.insert(0, str(repo_root))


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


def get_latest_snapshot_version(s3_client, bucket: str, collection: str) -> str:
    """Get the latest snapshot version from the pointer file."""
    pointer_key = f"{collection}/snapshot/latest-pointer.txt"

    try:
        response = s3_client.get_object(Bucket=bucket, Key=pointer_key)
        version_id = response["Body"].read().decode("utf-8").strip()
        return version_id
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchKey":
            print(f"   ⚠️  No pointer file found for {collection}")
            return None
        raise


def list_snapshot_files(s3_client, bucket: str, collection: str, version: str) -> list:
    """List all files in the snapshot directory."""
    prefix = f"{collection}/snapshot/timestamped/{version}/"

    try:
        response = s3_client.list_objects_v2(Bucket=bucket, Prefix=prefix)
        files = [obj["Key"] for obj in response.get("Contents", [])]
        return files
    except ClientError:
        return []


def download_snapshot_tar(s3_client, bucket: str, collection: str, version: str, local_path: Path) -> bool:
    """Download the snapshot tar file from S3."""
    # Try different possible paths
    possible_paths = [
        f"{collection}/snapshot/timestamped/{version}/snapshot.tar.gz",
        f"{collection}/snapshot/timestamped/{version}/chroma.tar.gz",
        f"{collection}/snapshot/timestamped/{version}/{collection}.tar.gz",
    ]

    # First, list what files exist
    print(f"   🔍 Checking what files exist in snapshot...")
    files = list_snapshot_files(s3_client, bucket, collection, version)
    if files:
        print(f"      Found {len(files)} files:")
        for f in files[:5]:
            print(f"         {f}")
        if len(files) > 5:
            print(f"         ... and {len(files) - 5} more")

    # Try to find and download the tar file (only if we don't see individual files)
    if not files or any("tar.gz" in f for f in files):
        for snapshot_key in possible_paths:
            try:
                print(f"   📥 Trying {snapshot_key}...")
                local_tar = local_path / "snapshot.tar.gz"
                s3_client.download_file(bucket, snapshot_key, str(local_tar))
                print(f"      ✅ Downloaded {local_tar.stat().st_size / 1024 / 1024:.2f} MB")
                return True
            except ClientError as e:
                if e.response["Error"]["Code"] == "NoSuchKey":
                    continue
                raise

    # If no tar found, try downloading all files
    if files:
        print(f"   📥 No tar file found, downloading individual files...")
        downloaded = 0
        for file_key in files:
            # Preserve directory structure
            relative_path = Path(file_key).relative_to(f"{collection}/snapshot/timestamped/{version}/")
            local_file = local_path / relative_path
            local_file.parent.mkdir(parents=True, exist_ok=True)
            try:
                s3_client.download_file(bucket, file_key, str(local_file))
                downloaded += 1
            except Exception as e:
                print(f"      ⚠️  Error downloading {file_key}: {e}")
        print(f"      ✅ Downloaded {downloaded}/{len(files)} files")
        return downloaded > 0

    print(f"      ⚠️  No snapshot files found")
    return False


def extract_snapshot(local_path: Path) -> bool:
    """Extract the snapshot tar file (if it exists)."""
    tar_file = local_path / "snapshot.tar.gz"
    if not tar_file.exists():
        # No tar file - files were downloaded directly
        return True

    try:
        print(f"   📦 Extracting snapshot...")
        with tarfile.open(tar_file, "r:gz") as tar:
            tar.extractall(local_path)
        print(f"      ✅ Extracted")
        tar_file.unlink()  # Remove tar after extraction
        return True
    except Exception as e:
        print(f"      ❌ Error extracting: {e}")
        return False


def check_receipt_in_snapshot(
    local_path: Path,
    collection: str,
    image_id: str,
    receipt_id: int,
) -> dict:
    """Check if receipt embeddings exist in the extracted snapshot."""
    print(f"   🔍 Checking for receipt {receipt_id} embeddings...")

    # Try to use ChromaDB if available
    try:
        from receipt_chroma import ChromaClient
        chroma_client = ChromaClient(
            persist_directory=str(local_path),
            mode="read",
            metadata_only=True,
        )

        try:
            coll = chroma_client.get_collection(collection)

            # Try string match first
            where_str = {
                "$and": [
                    {"image_id": {"$eq": image_id}},
                    {"receipt_id": {"$eq": str(receipt_id)}},
                ]
            }
            results_str = coll.get(where=where_str, limit=100000)
            count = len(results_str.get("ids", [])) if results_str else 0

            # Fallback: try numeric match
            if count == 0:
                where_int = {
                    "$and": [
                        {"image_id": {"$eq": image_id}},
                        {"receipt_id": {"$eq": receipt_id}},
                    ]
                }
                results_int = coll.get(where=where_int, limit=100000)
                count = len(results_int.get("ids", [])) if results_int else 0

            # Get total count
            total_count = coll.count() if hasattr(coll, "count") else None

            # Get sample IDs
            sample_ids = results_str.get("ids", [])[:5] if results_str and results_str.get("ids") else []

            chroma_client.close()

            return {
                "count": count,
                "total_count": total_count,
                "sample_ids": sample_ids,
                "status": "ok",
            }
        except Exception as e:
            chroma_client.close()
            return {
                "count": 0,
                "error": str(e),
                "status": "error",
            }
    except ImportError:
        # ChromaDB not available - try to read SQLite database directly
        print(f"      ⚠️  ChromaDB not available, trying to read SQLite database...")

        sqlite_file = local_path / "chroma.sqlite3"
        if sqlite_file.exists():
            try:
                import sqlite3
                conn = sqlite3.connect(str(sqlite_file))
                cursor = conn.cursor()

                # ChromaDB stores metadata in the 'embeddings' table
                # Try to find the table name
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
                tables = [row[0] for row in cursor.fetchall()]

                # Look for embeddings or collections table
                count = 0
                for table in tables:
                    try:
                        # Try to query for our receipt
                        cursor.execute(f"SELECT COUNT(*) FROM {table} WHERE id LIKE ?",
                                     (f"%IMAGE#{image_id}#RECEIPT#{receipt_id:05d}%",))
                        result = cursor.fetchone()
                        if result:
                            count += result[0]
                    except Exception:
                        # Table might not have the expected structure
                        continue

                conn.close()

                if count > 0:
                    return {
                        "count": count,
                        "status": "ok",
                        "method": "sqlite_direct",
                    }
                else:
                    return {
                        "count": 0,
                        "status": "ok",
                        "method": "sqlite_direct",
                        "note": "No embeddings found in SQLite",
                    }
            except Exception as e:
                return {
                    "count": 0,
                    "status": "error",
                    "error": f"SQLite read error: {e}",
                }
        else:
            return {
                "count": 0,
                "status": "error",
                "error": "ChromaDB files not found and library not available",
            }


def main():
    parser = argparse.ArgumentParser(
        description="Download ChromaDB snapshots and check for receipt embeddings"
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
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Output directory (default: temp directory)",
    )

    args = parser.parse_args()

    config = setup_environment()
    chromadb_bucket = config["chromadb_bucket"]

    print(f"🔍 Checking ChromaDB snapshots for receipt {args.receipt_id}")
    print(f"   Image ID: {args.image_id}")
    print(f"   Bucket: {chromadb_bucket}")
    print("=" * 60)

    s3_client = boto3.client("s3")

    # Use temp directory if not specified
    if args.out_dir:
        base_dir = args.out_dir
        base_dir.mkdir(parents=True, exist_ok=True)
    else:
        base_dir = Path(tempfile.mkdtemp(prefix="chroma_check_"))

    results = {}

    for collection in ["lines", "words"]:
        print(f"\n📊 Checking {collection} collection:")
        print("-" * 60)

        # Get latest version
        version = get_latest_snapshot_version(s3_client, chromadb_bucket, collection)
        if not version:
            results[collection] = {"status": "error", "error": "No snapshot version found"}
            continue

        print(f"   📌 Latest version: {version}")

        # Download snapshot
        collection_dir = base_dir / collection
        collection_dir.mkdir(parents=True, exist_ok=True)

        if not download_snapshot_tar(s3_client, chromadb_bucket, collection, version, collection_dir):
            results[collection] = {"status": "error", "error": "Download failed"}
            continue

        # Extract snapshot (if tar file exists)
        extract_snapshot(collection_dir)  # This is a no-op if files were downloaded directly

        # Check for receipt
        check_result = check_receipt_in_snapshot(
            collection_dir,
            collection,
            args.image_id,
            args.receipt_id,
        )

        results[collection] = {
            "version": version,
            **check_result,
        }

    # Print summary
    print(f"\n" + "=" * 60)
    print(f"📊 Summary:")
    print("=" * 60)

    total_found = 0
    for collection, result in results.items():
        print(f"\n{collection.upper()}:")
        if result.get("status") == "ok":
            count = result.get("count", 0)
            total_count = result.get("total_count", "unknown")
            version = result.get("version", "unknown")

            status = "✅ EXISTS" if count > 0 else "❌ NOT FOUND"
            print(f"   Status: {status}")
            print(f"   Receipt embeddings: {count}")
            print(f"   Total embeddings: {total_count}")
            print(f"   Snapshot version: {version}")

            if result.get("sample_ids"):
                print(f"   Sample IDs: {result['sample_ids'][:3]}")

            total_found += count
        else:
            print(f"   Status: ❌ ERROR")
            print(f"   Error: {result.get('error', 'Unknown error')}")
            if result.get("version"):
                print(f"   Snapshot version: {result['version']}")

    print(f"\n📈 Total receipt embeddings found: {total_found}")

    if total_found > 0:
        print(f"\n⚠️  Receipt embeddings still exist in ChromaDB!")
        print(f"   The enhanced compactor may not have processed the deletion yet,")
        print(f"   or the deletion failed.")
    else:
        print(f"\n✅ No receipt embeddings found in ChromaDB!")
        print(f"   The enhanced compactor successfully deleted the embeddings.")

    print(f"\n💾 Snapshots downloaded to: {base_dir}")
    print(f"   (You can inspect them manually if needed)")


if __name__ == "__main__":
    main()

