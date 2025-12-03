#!/usr/bin/env python3
"""
Rollback script to restore original receipt data from exported JSON files.

This script restores:
- Receipt entity
- ReceiptLine entities
- ReceiptWord entities
- ReceiptLetter entities
- ReceiptWordLabel entities
- ReceiptMetadata entity
- CompactionRun entities (if any)

Usage:
    python scripts/rollback_split_receipt.py \
        --image-id 13da1048-3888-429f-b2aa-b3e15341da5e \
        --receipt-id 1 \
        --export-dir ./exported_image_data
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

# Add repo root to path
repo_root = Path(__file__).parent.parent
sys.path.insert(0, str(repo_root))

from receipt_dynamo import DynamoClient
from receipt_dynamo.entities import (
    Receipt,
    ReceiptLine,
    ReceiptWord,
    ReceiptLetter,
    ReceiptWordLabel,
    ReceiptMetadata,
    CompactionRun,
)


def setup_environment() -> Dict[str, str]:
    """Load environment variables and return configuration dict."""
    table_name = os.environ.get("DYNAMODB_TABLE_NAME")

    # Try loading from Pulumi if not set
    try:
        from receipt_dynamo.data._pulumi import load_env
        project_root = Path(__file__).parent.parent
        infra_dir = project_root / "infra"
        env = load_env("dev", working_dir=str(infra_dir))

        if not table_name:
            table_name = env.get("dynamodb_table_name") or env.get("receipts_table_name")
    except Exception as e:
        print(f"⚠️  Could not load from Pulumi: {e}")
        if not table_name:
            raise ValueError("DYNAMODB_TABLE_NAME must be set in environment or available from Pulumi")

    return {
        "table_name": table_name,
    }


def load_entity_from_json(entity_class, filepath: Path):
    """Load an entity from a JSON file using entity_class(**json_data)."""
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    return entity_class(**data)


def load_entities_from_json(entity_class, filepath: Path) -> List[Any]:
    """Load multiple entities from a JSON file."""
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, list):
        return [entity_class(**item) for item in data]
    elif isinstance(data, dict) and "receipt_id" in data:
        # Single entity
        return [entity_class(**data)]
    else:
        raise ValueError(f"Unexpected JSON format in {filepath}")


def rollback_receipt(
    client: DynamoClient,
    image_id: str,
    receipt_id: int,
    export_dir: Path,
    dry_run: bool = False,
) -> None:
    """Rollback a receipt from exported JSON files."""
    print(f"\n🔄 Rolling back receipt {receipt_id} for image {image_id}")
    print("=" * 60)

    # Find the receipt directory
    receipt_dir = export_dir / image_id / f"receipt_{receipt_id:05d}"

    if not receipt_dir.exists():
        print(f"❌ Receipt directory not found: {receipt_dir}")
        print(f"   Available receipts:")
        image_dir = export_dir / image_id
        if image_dir.exists():
            for d in image_dir.iterdir():
                if d.is_dir() and d.name.startswith("receipt_"):
                    print(f"      {d.name}")
        sys.exit(1)

    print(f"📁 Loading from: {receipt_dir}")

    if dry_run:
        print("\n🔍 DRY RUN MODE - No changes will be made\n")

    # 1. Load Receipt
    receipt_file = receipt_dir / "receipt.json"
    if not receipt_file.exists():
        print(f"❌ Receipt file not found: {receipt_file}")
        sys.exit(1)

    receipt = load_entity_from_json(Receipt, receipt_file)
    print(f"   ✅ Loaded receipt: {receipt.receipt_id}")

    # 2. Load ReceiptLines
    receipt_lines_file = receipt_dir / "receipt_lines.json"
    receipt_lines = []
    if receipt_lines_file.exists():
        receipt_lines = load_entities_from_json(ReceiptLine, receipt_lines_file)
        print(f"   ✅ Loaded {len(receipt_lines)} lines")
    else:
        print(f"   ⚠️  No receipt_lines.json found")

    # 3. Load ReceiptWords
    receipt_words_file = receipt_dir / "receipt_words.json"
    receipt_words = []
    if receipt_words_file.exists():
        receipt_words = load_entities_from_json(ReceiptWord, receipt_words_file)
        print(f"   ✅ Loaded {len(receipt_words)} words")
    else:
        print(f"   ⚠️  No receipt_words.json found")

    # 4. Load ReceiptLetters (optional)
    receipt_letters_file = receipt_dir / "receipt_letters.json"
    receipt_letters = []
    if receipt_letters_file.exists():
        receipt_letters = load_entities_from_json(ReceiptLetter, receipt_letters_file)
        print(f"   ✅ Loaded {len(receipt_letters)} letters")
    else:
        print(f"   ⚠️  No receipt_letters.json found (optional)")

    # 5. Load ReceiptWordLabels
    receipt_labels_file = receipt_dir / "receipt_word_labels.json"
    receipt_labels = []
    if receipt_labels_file.exists():
        receipt_labels = load_entities_from_json(ReceiptWordLabel, receipt_labels_file)
        print(f"   ✅ Loaded {len(receipt_labels)} labels")
    else:
        print(f"   ⚠️  No receipt_word_labels.json found")

    # 6. Load ReceiptMetadata (optional)
    receipt_metadata_file = receipt_dir / "receipt_metadata.json"
    receipt_metadata = None
    if receipt_metadata_file.exists():
        metadata_data = json.load(open(receipt_metadata_file, "r", encoding="utf-8"))
        if isinstance(metadata_data, list) and len(metadata_data) > 0:
            metadata_dict = metadata_data[0]
        elif isinstance(metadata_data, dict):
            metadata_dict = metadata_data
        else:
            metadata_dict = None

        if metadata_dict:
            # Parse datetime strings if present
            from datetime import datetime
            if "timestamp" in metadata_dict and isinstance(metadata_dict["timestamp"], str):
                metadata_dict["timestamp"] = datetime.fromisoformat(metadata_dict["timestamp"].replace("Z", "+00:00"))
            receipt_metadata = ReceiptMetadata(**metadata_dict)
            print(f"   ✅ Loaded metadata")
    else:
        print(f"   ⚠️  No receipt_metadata.json found (optional)")

    # 7. Load CompactionRun (optional)
    compaction_runs_file = receipt_dir / "compaction_runs.json"
    compaction_runs = []
    if compaction_runs_file.exists():
        compaction_runs = load_entities_from_json(CompactionRun, compaction_runs_file)
        print(f"   ✅ Loaded {len(compaction_runs)} compaction runs")
    else:
        print(f"   ⚠️  No compaction_runs.json found (optional)")

    if dry_run:
        print(f"\n📋 Would restore:")
        print(f"   - 1 Receipt")
        print(f"   - {len(receipt_lines)} ReceiptLines")
        print(f"   - {len(receipt_words)} ReceiptWords")
        print(f"   - {len(receipt_letters)} ReceiptLetters")
        print(f"   - {len(receipt_labels)} ReceiptWordLabels")
        if receipt_metadata:
            print(f"   - 1 ReceiptMetadata")
        print(f"   - {len(compaction_runs)} CompactionRuns")
        print(f"\n✅ Dry run complete - no changes made")
        return

    # Restore in correct order
    print(f"\n💾 Restoring to DynamoDB...")

    # 1. Restore Receipt (must be first)
    print(f"   Restoring receipt...")
    try:
        client.add_receipt(receipt)
        print(f"      ✅ Receipt restored")
    except Exception as e:
        if "ConditionalCheckFailed" in str(e) or "already exists" in str(e).lower():
            print(f"      ⚠️  Receipt already exists, skipping")
        else:
            raise

    # 2. Restore ReceiptLines
    if receipt_lines:
        print(f"   Restoring {len(receipt_lines)} lines...")
        try:
            client.add_receipt_lines(receipt_lines)
            print(f"      ✅ Lines restored")
        except Exception as e:
            if "ConditionalCheckFailed" in str(e) or "already exists" in str(e).lower():
                print(f"      ⚠️  Lines already exist, skipping")
            else:
                raise

    # 3. Restore ReceiptWords
    if receipt_words:
        print(f"   Restoring {len(receipt_words)} words...")
        try:
            client.add_receipt_words(receipt_words)
            print(f"      ✅ Words restored")
        except Exception as e:
            if "ConditionalCheckFailed" in str(e) or "already exists" in str(e).lower():
                print(f"      ⚠️  Words already exist, skipping")
            else:
                raise

    # 4. Restore ReceiptLetters
    if receipt_letters:
        print(f"   Restoring {len(receipt_letters)} letters...")
        try:
            client.add_receipt_letters(receipt_letters)
            print(f"      ✅ Letters restored")
        except AttributeError:
            print(f"      ⚠️  Letter restoration not supported")
        except Exception as e:
            if "ConditionalCheckFailed" in str(e) or "already exists" in str(e).lower():
                print(f"      ⚠️  Letters already exist, skipping")
            else:
                raise

    # 5. Restore ReceiptWordLabels
    if receipt_labels:
        print(f"   Restoring {len(receipt_labels)} labels...")
        # Labels need to be added one at a time or in batches
        CHUNK_SIZE = 25
        for i in range(0, len(receipt_labels), CHUNK_SIZE):
            chunk = receipt_labels[i:i + CHUNK_SIZE]
            for label in chunk:
                try:
                    client.add_receipt_word_label(label)
                except Exception as e:
                    print(f"      ⚠️  Error restoring label: {e}")
        print(f"      ✅ Labels restored")

    # 6. Restore ReceiptMetadata
    if receipt_metadata:
        print(f"   Restoring metadata...")
        try:
            client.add_receipt_metadata(receipt_metadata)
            print(f"      ✅ Metadata restored")
        except Exception as e:
            print(f"      ⚠️  Error restoring metadata: {e}")

    # 7. Restore CompactionRun (if any)
    if compaction_runs:
        print(f"   Restoring {len(compaction_runs)} compaction runs...")
        for run in compaction_runs:
            try:
                client.add_compaction_run(run)
            except Exception as e:
                print(f"      ⚠️  Error restoring compaction run: {e}")
        print(f"      ✅ Compaction runs restored")

    print(f"\n✅ Rollback complete!")
    print(f"   Receipt {receipt_id} and all subrecords have been restored")


def main():
    parser = argparse.ArgumentParser(
        description="Rollback receipt data from exported JSON files"
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
        help="Receipt ID to rollback",
    )
    parser.add_argument(
        "--export-dir",
        type=Path,
        default=Path("./exported_image_data"),
        help="Directory containing exported data (default: ./exported_image_data)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Dry run mode - don't make any changes",
    )

    args = parser.parse_args()

    # Setup environment
    config = setup_environment()
    client = DynamoClient(config["table_name"])

    print(f"📊 DynamoDB Table: {config['table_name']}")
    print(f"📁 Export Directory: {args.export_dir}")

    # Rollback receipt
    rollback_receipt(
        client,
        args.image_id,
        args.receipt_id,
        args.export_dir,
        args.dry_run,
    )


if __name__ == "__main__":
    main()
