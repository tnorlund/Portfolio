#!/usr/bin/env python3
"""
Test script to verify the full split receipt flow:
1. Split receipt into multiple receipts
2. Wait for compaction to complete
3. Delete original receipt (triggers enhanced compactor)
4. Verify embeddings are deleted from ChromaDB

Usage:
    python scripts/test_split_and_delete.py \
        --image-id 13da1048-3888-429f-b2aa-b3e15341da5e \
        --original-receipt-id 1
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path

# Add repo root to path
repo_root = Path(__file__).parent.parent
sys.path.insert(0, str(repo_root))

from receipt_dynamo import DynamoClient
from receipt_dynamo.entities import Receipt
import os

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
    table_name = os.environ.get("DYNAMODB_TABLE_NAME")
    chromadb_bucket = os.environ.get("CHROMADB_BUCKET")

    # Try loading from Pulumi if not set
    try:
        from receipt_dynamo.data._pulumi import load_env
        project_root = Path(__file__).parent.parent
        infra_dir = project_root / "infra"
        env = load_env("dev", working_dir=str(infra_dir))

        if not table_name:
            table_name = env.get("dynamodb_table_name") or env.get("receipts_table_name")
        if not chromadb_bucket:
            chromadb_bucket = env.get("chromadb_bucket")
    except Exception as e:
        print(f"⚠️  Could not load from Pulumi: {e}")

    if not table_name:
        raise ValueError("DYNAMODB_TABLE_NAME must be set")

    # ChromaDB bucket is optional (only needed for embedding verification)
    if not chromadb_bucket:
        print("⚠️  CHROMADB_BUCKET not set - embedding verification will be skipped")

    return {
        "table_name": table_name,
        "chromadb_bucket": chromadb_bucket,
    }


def run_split_receipt(image_id: str, receipt_id: int, output_dir: Path) -> list[int]:
    """Run split_receipt.py and return list of new receipt IDs."""
    print(f"\n📋 Step 1: Splitting receipt {receipt_id} for image {image_id}")
    print("=" * 60)

    cmd = [
        sys.executable,
        "scripts/split_receipt.py",
        "--image-id", image_id,
        "--original-receipt-id", str(receipt_id),
        "--output-dir", str(output_dir),
    ]

    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        print(f"❌ Split failed!")
        print(result.stderr)
        sys.exit(1)

    print(result.stdout)

    # Query DynamoDB for new receipts (they should have IDs > original_receipt_id)
    print("   🔍 Finding new receipt IDs from DynamoDB...")
    try:
        config = setup_environment()
        client = DynamoClient(config["table_name"])
        all_receipts = client.get_receipts_from_image(image_id)
        # New receipts should have IDs > original_receipt_id
        new_receipt_ids = [r.receipt_id for r in all_receipts if r.receipt_id > receipt_id]
        new_receipt_ids.sort()
        print(f"   Found {len(new_receipt_ids)} new receipts: {new_receipt_ids}")
    except Exception as e:
        print(f"   ⚠️  Error querying DynamoDB: {e}")
        # Fallback: try to read from output directory
        image_dir = output_dir / image_id
        if image_dir.exists():
            receipt_dirs = [d for d in image_dir.iterdir() if d.is_dir() and d.name.startswith("receipt_")]
            new_receipt_ids = []
            for receipt_dir in receipt_dirs:
                receipt_file = receipt_dir / "receipt.json"
                if receipt_file.exists():
                    import json
                    with open(receipt_file, "r") as f:
                        data = json.load(f)
                        if "receipt_id" in data and data["receipt_id"] > receipt_id:
                            new_receipt_ids.append(data["receipt_id"])
            new_receipt_ids.sort()

    if not new_receipt_ids:
        print("   ❌ Could not determine new receipt IDs!")
        sys.exit(1)

    print(f"\n✅ Split complete! New receipt IDs: {new_receipt_ids}")
    return new_receipt_ids


def wait_for_compaction(
    client: DynamoClient,
    image_id: str,
    receipt_ids: list[int],
    timeout: int = 600,
) -> bool:
    """Wait for all compaction runs to complete."""
    print(f"\n⏳ Step 2: Waiting for compaction to complete")
    print("=" * 60)

    start_time = time.time()
    check_interval = 10  # Check every 10 seconds

    while time.time() - start_time < timeout:
        all_complete = True
        for receipt_id in receipt_ids:
            try:
                runs, _ = client.list_compaction_runs_for_receipt(image_id, receipt_id)
                for run in runs:
                    if run.status not in ["COMPLETED", "FAILED"]:
                        all_complete = False
                        print(f"   Receipt {receipt_id}: {run.status} (run {run.compaction_run_id})")
                        break
            except Exception as e:
                print(f"   ⚠️  Error checking receipt {receipt_id}: {e}")
                all_complete = False

        if all_complete:
            print(f"\n✅ All compaction runs completed!")
            return True

        time.sleep(check_interval)
        elapsed = int(time.time() - start_time)
        print(f"   Waiting... ({elapsed}s elapsed)")

    print(f"\n❌ Timeout waiting for compaction (>{timeout}s)")
    return False


def count_receipt_embeddings(
    chroma_client,
    image_id: str,
    receipt_id: int,
) -> dict[str, int]:
    """Count embeddings for a receipt in ChromaDB."""
    if not CHROMADB_AVAILABLE or chroma_client is None:
        return {}

    counts = {}
    for collection_name in [ChromaDBCollection.RECEIPT_LINES, ChromaDBCollection.RECEIPT_WORDS]:
        try:
            collection = chroma_client.get_collection(collection_name)
            # Query for embeddings with this receipt
            results = collection.get(
                where={"image_id": image_id, "receipt_id": receipt_id},
            )
            counts[collection_name] = len(results.get("ids", [])) if results else 0
        except Exception as e:
            print(f"   ⚠️  Error counting {collection_name}: {e}")
            counts[collection_name] = 0
    return counts


def delete_original_receipt(
    client: DynamoClient,
    image_id: str,
    receipt_id: int,
) -> None:
    """Delete the original receipt from DynamoDB and verify compactor deletes embeddings."""
    print(f"\n🗑️  Step 3: Deleting original receipt {receipt_id}")
    print("=" * 60)

    try:
        receipt = client.get_receipt(image_id, receipt_id)
        print(f"   Found receipt: {receipt.receipt_id}")

        # Get receipt lines and words for embedding ID construction
        receipt_lines = client.list_receipt_lines_from_receipt(image_id, receipt_id)
        receipt_words = client.list_receipt_words_from_receipt(image_id, receipt_id)
        print(f"   Receipt has {len(receipt_lines)} lines and {len(receipt_words)} words")

        # Count embeddings before deletion
        print(f"\n   📊 Counting embeddings before deletion...")
        config = setup_environment()
        chromadb_bucket = config.get("chromadb_bucket")
        if CHROMADB_AVAILABLE and chromadb_bucket:
            chroma_client = ChromaClient(chromadb_bucket)
            before_counts = count_receipt_embeddings(chroma_client, image_id, receipt_id)
            print(f"   Before deletion:")
            total_before = 0
            for collection, count in before_counts.items():
                print(f"      {collection}: {count} embeddings")
                total_before += count

            if total_before == 0:
                print(f"   ⚠️  No embeddings found before deletion. They may not exist yet.")
                print(f"   Continuing with deletion test anyway...")
        else:
            print(f"   ⚠️  ChromaDB not available or bucket not set - skipping embedding count")
            chroma_client = None
            before_counts = {}
            total_before = 0

        # Delete the receipt (this should trigger the enhanced compactor via DynamoDB stream)
        print(f"\n   🗑️  Deleting receipt from DynamoDB...")
        print(f"   This will trigger DynamoDB stream -> Enhanced Compactor -> ChromaDB deletion")
        client.delete_receipt(receipt)
        print(f"   ✅ Receipt deleted from DynamoDB")

        # Wait for the stream to process and compactor to run
        print(f"\n   ⏳ Waiting for DynamoDB stream and compactor to process...")
        print(f"   (This may take 1-2 minutes)")

        max_wait = 180  # 3 minutes
        check_interval = 10
        elapsed = 0

        while elapsed < max_wait:
            time.sleep(check_interval)
            elapsed += check_interval

            # Check embeddings after deletion
            if chroma_client:
                after_counts = count_receipt_embeddings(chroma_client, image_id, receipt_id)
                total_after = sum(after_counts.values())

                if total_after == 0:
                    print(f"   ✅ All embeddings deleted! (took {elapsed}s)")
                    break
                else:
                    print(f"   Still waiting... ({elapsed}s) - {total_after} embeddings remaining")
            else:
                # Can't check embeddings, just wait
                print(f"   Waiting for compactor... ({elapsed}s)")
        else:
            # Final check
            if chroma_client:
                after_counts = count_receipt_embeddings(chroma_client, image_id, receipt_id)
                total_after = sum(after_counts.values())
            else:
                after_counts = {}
                total_after = 0

        if chroma_client:
            print(f"\n   📊 Final embedding counts:")
            for collection, count in after_counts.items():
                print(f"      {collection}: {count} embeddings")

            # Verify deletion
            all_deleted = all(count == 0 for count in after_counts.values())
            if all_deleted:
                print(f"\n   ✅ SUCCESS: All embeddings deleted by enhanced compactor!")
            else:
                print(f"\n   ⚠️  WARNING: Some embeddings still exist:")
                for collection, count in after_counts.items():
                    if count > 0:
                        print(f"      {collection}: {count} embeddings remaining")
                print(f"\n   This could mean:")
                print(f"      - Compactor hasn't processed yet (wait longer)")
                print(f"      - Compactor deletion logic has an issue")
                print(f"      - Embeddings were never created")
        else:
            print(f"\n   ⚠️  ChromaDB not available - cannot verify embedding deletion")
            print(f"   Receipt was deleted from DynamoDB - compactor should process it")

    except Exception as e:
        print(f"   ❌ Error deleting receipt: {e}")
        import traceback
        traceback.print_exc()
        raise


def main():
    parser = argparse.ArgumentParser(
        description="Test split receipt and deletion flow"
    )
    parser.add_argument(
        "--image-id",
        required=True,
        help="Image ID to test",
    )
    parser.add_argument(
        "--original-receipt-id",
        type=int,
        required=True,
        help="Original receipt ID to split",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("./test_split_output"),
        help="Output directory for split receipts",
    )
    parser.add_argument(
        "--skip-split",
        action="store_true",
        help="Skip the split step (use if already split)",
    )

    args = parser.parse_args()

    config = setup_environment()
    client = DynamoClient(config["table_name"])

    print("🧪 Testing Split Receipt and Enhanced Compactor Deletion")
    print("=" * 60)

    # Step 1: Split receipt
    if not args.skip_split:
        new_receipt_ids = run_split_receipt(
            args.image_id,
            args.original_receipt_id,
            args.output_dir,
        )
        if not new_receipt_ids:
            print("⚠️  Could not determine new receipt IDs. Please check manually.")
            new_receipt_ids = [args.original_receipt_id + 1]  # Guess
    else:
        print("\n⏭️  Skipping split step")
        # Try to find new receipt IDs from output directory
        new_receipt_ids = []
        output_path = args.output_dir / args.image_id / f"receipt_{args.original_receipt_id:05d}" / "new_receipts.json"
        if output_path.exists():
            import json
            with open(output_path, "r") as f:
                data = json.load(f)
                if isinstance(data, list):
                    new_receipt_ids = [r["receipt_id"] for r in data if "receipt_id" in r]
        if not new_receipt_ids:
            print("⚠️  Could not find new receipt IDs. Please specify manually.")
            sys.exit(1)

    # Step 2: Wait for compaction
    if not wait_for_compaction(client, args.image_id, new_receipt_ids):
        print("❌ Compaction did not complete. Aborting test.")
        sys.exit(1)

    # Step 3: Delete original receipt
    delete_original_receipt(client, args.image_id, args.original_receipt_id)

    print("\n" + "=" * 60)
    print("✅ Test complete!")
    print("=" * 60)


if __name__ == "__main__":
    main()

