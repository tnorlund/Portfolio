#!/usr/bin/env python3
"""
Script to download and compare images and receipts between PROD and DEV environments.
"""

import argparse
import json
import os
import sys
from dataclasses import asdict
from datetime import datetime
from typing import Dict, List

from tqdm import tqdm

# Import the receipt_dynamo client
from receipt_dynamo import DynamoClient
from receipt_dynamo.data._pulumi import load_env
from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel


def get_table_name(env: str) -> str:
    """
    Get the DynamoDB table name for the specified environment.

    Args:
        env: Environment name ('prod' or 'dev')

    Returns:
        Table name string
    """
    try:
        env_data = load_env(env)
        table_name = env_data.get("dynamodb_table_name")
        if table_name:
            print(f"Found {env} table: {table_name}")
            return table_name
    except Exception as e:
        print(f"Warning: Could not load {env} table name from Pulumi: {e}")

    # Fallback: try to find table with common naming pattern
    import boto3

    dynamodb = boto3.client("dynamodb", region_name="us-east-1")
    response = dynamodb.list_tables()

    for table in response["TableNames"]:
        if "receipts" in table.lower() or "receipt" in table.lower():
            if env == "prod" and "d7ff76a" in table:
                print(f"Using table: {table}")
                return table
            elif env == "dev" and "dc5be22" in table:
                print(f"Using table: {table}")
                return table

    # If no specific match, try the first receipts table
    for table in response["TableNames"]:
        if "receipts" in table.lower() or "receipt" in table.lower():
            print(f"Using table (fallback): {table}")
            return table

    raise ValueError(f"Could not find table for environment: {env}")


def download_all_images_and_receipts(table_name: str, output_dir: str):
    """
    Download all images and receipts from DynamoDB and save them as JSON files.

    Args:
        table_name: The DynamoDB table name (e.g., 'prod-table' or 'dev-table')
        output_dir: Directory where JSON files will be saved
    """
    # Initialize DynamoDB client
    print(f"Initializing DynamoDB client for table: {table_name}")
    dynamo_client = DynamoClient(table_name)

    # Create output directory
    os.makedirs(output_dir, exist_ok=True)

    # List all images
    all_images = []
    last_evaluated_key = None

    print(f"Downloading images from {table_name}...")
    with tqdm(desc="Fetching images", unit="pages") as pbar:
        while True:
            images, last_evaluated_key = dynamo_client.list_images(
                last_evaluated_key=last_evaluated_key
            )
            all_images.extend(images)
            pbar.update(1)
            pbar.set_postfix({"total_images": len(all_images)})

            if last_evaluated_key is None:
                break

    # Save images to JSON
    images_output_file = os.path.join(output_dir, f"{table_name}_images.json")
    with open(images_output_file, "w") as f:
        json.dump([asdict(image) for image in all_images], f, indent=2, default=str)
    print(f"Saved {len(all_images)} images to {images_output_file}")

    # List all receipts
    all_receipts = []
    last_evaluated_key = None

    print(f"Downloading receipts from {table_name}...")
    with tqdm(desc="Fetching receipts", unit="pages") as pbar:
        while True:
            receipts, last_evaluated_key = dynamo_client.list_receipts(
                last_evaluated_key=last_evaluated_key
            )
            all_receipts.extend(receipts)
            pbar.update(1)
            pbar.set_postfix({"total_receipts": len(all_receipts)})

            if last_evaluated_key is None:
                break

    # Save receipts to JSON
    receipts_output_file = os.path.join(output_dir, f"{table_name}_receipts.json")
    with open(receipts_output_file, "w") as f:
        json.dump(
            [asdict(receipt) for receipt in all_receipts],
            f,
            indent=2,
            default=str,
        )
    print(f"Saved {len(all_receipts)} receipts to {receipts_output_file}")


def download_detailed_data_for_comparison(
    table_name: str, output_dir: str, force_download: bool = False
):
    """
    Download all images and receipts with their full details (lines, words, letters, labels).

    Args:
        table_name: The DynamoDB table name
        output_dir: Directory where JSON files will be saved
        force_download: If True, always download even if file exists
    """
    output_file = os.path.join(output_dir, f"{table_name}_detailed_data.json")

    # Check if file already exists
    if os.path.exists(output_file) and not force_download:
        print(f"Found existing data file: {output_file}")
        print("Skipping download. Use --force to re-download.")
        return

    # Initialize DynamoDB client
    print(f"Initializing DynamoDB client for table: {table_name}")
    dynamo_client = DynamoClient(table_name)

    # Create output directory
    os.makedirs(output_dir, exist_ok=True)

    # Get all images first
    all_images = []
    last_evaluated_key = None
    print("Fetching all images...")
    with tqdm(desc="Fetching images", unit="pages") as pbar:
        while True:
            images, last_evaluated_key = dynamo_client.list_images(
                last_evaluated_key=last_evaluated_key
            )
            all_images.extend(images)
            pbar.update(1)
            pbar.set_postfix({"total_images": len(all_images)})
            if last_evaluated_key is None:
                break

    print(f"Processing {len(all_images)} images from {table_name}...")

    # Process each image and get all its details
    detailed_data = []

    for image in tqdm(all_images, desc="Processing images", unit="image"):
        try:
            # Get all details for this image
            details = dynamo_client.get_image_details(image.image_id)

            # Store everything as a dict
            image_data = {
                "image": asdict(image),
                "lines": [asdict(line) for line in details.lines],
                "words": [asdict(word) for word in details.words],
                "letters": [asdict(letter) for letter in details.letters],
                "receipts": [asdict(receipt) for receipt in details.receipts],
                "receipt_lines": [asdict(rl) for rl in details.receipt_lines],
                "receipt_words": [asdict(rw) for rw in details.receipt_words],
                "receipt_letters": [asdict(rl) for rl in details.receipt_letters],
                "receipt_word_labels": [
                    asdict(rwl) for rwl in details.receipt_word_labels
                ],
            }

            detailed_data.append(image_data)
        except Exception as e:
            print(f"Error processing image {image.image_id}: {e}")
            continue

    # Save to JSON
    with open(output_file, "w") as f:
        json.dump(detailed_data, f, indent=2, default=str)

    print(f"Saved detailed data to {output_file}")
    print(f"Total images processed: {len(detailed_data)}")


def load_json(file_path: str) -> List[Dict]:
    """Load JSON data from a file."""
    with open(file_path, "r") as f:
        return json.load(f)


def extract_text_from_words(words: List[Dict], receipt_id: int = None) -> str:
    """
    Extract text from receipt words, optionally filtering by receipt_id.

    Args:
        words: List of word dictionaries
        receipt_id: Optional receipt ID to filter words

    Returns:
        Concatenated text from words
    """
    text_parts = []
    for word in words:
        # Only include receipt words if receipt_id matches or is None
        if receipt_id is None or word.get("receipt_id") == receipt_id:
            word_text = word.get("text", "")
            if word_text:
                text_parts.append(word_text)

    return " ".join(text_parts)


def compare_labels(
    prod_data_file: str,
    dev_data_file: str,
    output_file: str = "label_comparison_report.txt",
):
    """
    Compare labels between PROD and DEV environments.

    Args:
        prod_data_file: Path to PROD detailed data JSON
        dev_data_file: Path to DEV detailed data JSON
        output_file: Path to save comparison report
    """
    # Load data
    print(f"Loading PROD data from {prod_data_file}...")
    prod_data = load_json(prod_data_file)
    print(f"Loaded {len(prod_data)} images from PROD")

    print(f"Loading DEV data from {dev_data_file}...")
    dev_data = load_json(dev_data_file)
    print(f"Loaded {len(dev_data)} images from DEV")

    # Build index of DEV images by image_id
    dev_images_by_id = {img["image"]["image_id"]: img for img in dev_data}

    # Track label comparisons
    label_differences = []
    matching_labels = 0
    total_labels = 0

    print(f"Comparing labels between PROD and DEV...")

    for prod_img in tqdm(prod_data, desc="Comparing labels", unit="image"):
        image_id = prod_img["image"]["image_id"]

        if image_id not in dev_images_by_id:
            continue

        dev_img = dev_images_by_id[image_id]

        # Build label index by (image_id, receipt_id, line_id, word_id)
        prod_labels_by_key = {
            (
                img["image_id"],
                img["receipt_id"],
                img["line_id"],
                img["word_id"],
            ): img
            for img in prod_img.get("receipt_word_labels", [])
        }

        dev_labels_by_key = {
            (
                img["image_id"],
                img["receipt_id"],
                img["line_id"],
                img["word_id"],
            ): img
            for img in dev_img.get("receipt_word_labels", [])
        }

        # Compare labels for same words
        for key, prod_label in prod_labels_by_key.items():
            total_labels += 1
            if key in dev_labels_by_key:
                dev_label = dev_labels_by_key[key]

                # Check if label text differs
                if prod_label.get("label") != dev_label.get("label"):
                    label_differences.append(
                        {
                            "image_id": image_id,
                            "receipt_id": prod_label.get("receipt_id"),
                            "line_id": prod_label.get("line_id"),
                            "word_id": prod_label.get("word_id"),
                            "prod_label": prod_label.get("label"),
                            "dev_label": dev_label.get("label"),
                            "prod_validation_status": prod_label.get(
                                "validation_status"
                            ),
                            "dev_validation_status": dev_label.get("validation_status"),
                        }
                    )
                # Check if validation status differs
                elif prod_label.get("validation_status") != dev_label.get(
                    "validation_status"
                ):
                    label_differences.append(
                        {
                            "image_id": image_id,
                            "receipt_id": prod_label.get("receipt_id"),
                            "line_id": prod_label.get("line_id"),
                            "word_id": prod_label.get("word_id"),
                            "prod_label": prod_label.get("label"),
                            "dev_label": dev_label.get("label"),
                            "prod_validation_status": prod_label.get(
                                "validation_status"
                            ),
                            "dev_validation_status": dev_label.get("validation_status"),
                            "type": "validation_status_diff",
                        }
                    )
                else:
                    matching_labels += 1
            else:
                # Label in PROD but not in DEV
                label_differences.append(
                    {
                        "image_id": image_id,
                        "receipt_id": prod_label.get("receipt_id"),
                        "line_id": prod_label.get("line_id"),
                        "word_id": prod_label.get("word_id"),
                        "prod_label": prod_label.get("label"),
                        "dev_label": "MISSING",
                        "prod_validation_status": prod_label.get("validation_status"),
                        "dev_validation_status": None,
                        "type": "missing_in_dev",
                    }
                )

    # Generate report
    report = []
    report.append("=" * 80)
    report.append("LABEL COMPARISON REPORT: PROD vs DEV")
    report.append("=" * 80)
    report.append("")

    report.append(f"Total labels checked: {total_labels}")
    report.append(f"Matching labels: {matching_labels}")
    report.append(f"Label differences: {len(label_differences)}")
    report.append("")

    # Group differences by type
    status_diffs = [
        d for d in label_differences if d.get("type") == "validation_status_diff"
    ]
    text_diffs = [
        d for d in label_differences if d.get("type") != "validation_status_diff"
    ]

    report.append("-" * 80)
    report.append(f"VALIDATION STATUS DIFFERENCES: {len(status_diffs)}")
    report.append("-" * 80)
    for diff in status_diffs[:20]:  # Show first 20
        report.append(f"  Image ID: {diff['image_id']}")
        report.append(
            f"    Receipt: {diff['receipt_id']}, Line: {diff['line_id']}, Word: {diff['word_id']}"
        )
        report.append(f"    Label: {diff['prod_label']}")
        report.append(f"    PROD status: {diff['prod_validation_status']}")
        report.append(f"    DEV status: {diff['dev_validation_status']}")
        report.append("")
    if len(status_diffs) > 20:
        report.append(f"  ... and {len(status_diffs) - 20} more")
    report.append("")

    report.append("-" * 80)
    report.append(f"LABEL TEXT DIFFERENCES: {len(text_diffs)}")
    report.append("-" * 80)
    for diff in text_diffs[:20]:  # Show first 20
        report.append(f"  Image ID: {diff['image_id']}")
        report.append(
            f"    Receipt: {diff['receipt_id']}, Line: {diff['line_id']}, Word: {diff['word_id']}"
        )
        report.append(
            f"    PROD: {diff['prod_label']} (status: {diff['prod_validation_status']})"
        )
        report.append(
            f"    DEV: {diff['dev_label']} (status: {diff['dev_validation_status']})"
        )
        report.append("")
    if len(text_diffs) > 20:
        report.append(f"  ... and {len(text_diffs) - 20} more")
    report.append("")

    # Save report
    report_text = "\n".join(report)
    with open(output_file, "w") as f:
        f.write(report_text)

    print(f"Label comparison report saved to {output_file}")
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"Total labels: {total_labels}")
    print(f"Matching labels: {matching_labels}")
    print(f"Validation status differences: {len(status_diffs)}")
    print(f"Label text differences: {len(text_diffs)}")


def rollback_prod_labels_from_backup(
    prod_backup_file: str, prod_table_name: str, dry_run: bool = True
):
    """
    Rollback PROD labels to the state saved in the backup JSON file.

    This function restores PROD labels from the locally stored backup JSON file,
    effectively undoing any sync operations that were performed.

    Args:
        prod_backup_file: Path to PROD detailed data JSON (the backup)
        prod_table_name: PROD DynamoDB table name
        dry_run: If True, only show what would be restored without executing
    """
    # Load backup data
    print(f"Loading PROD backup from {prod_backup_file}...")
    if not os.path.exists(prod_backup_file):
        raise FileNotFoundError(
            f"Backup file not found: {prod_backup_file}\n"
            "You need to have run 'download-detailed' for PROD first!"
        )

    backup_data = load_json(prod_backup_file)
    print(f"Loaded {len(backup_data)} images from backup")

    # Collect all labels from backup
    all_backup_labels = []
    for img_data in backup_data:
        for label_dict in img_data.get("receipt_word_labels", []):
            all_backup_labels.append(label_dict)

    print(f"Found {len(all_backup_labels)} labels in backup")

    # Convert to ReceiptWordLabel objects
    backup_label_objects = []
    for label_dict in tqdm(all_backup_labels, desc="Preparing restore", unit="label"):
        label_obj = ReceiptWordLabel(
            image_id=label_dict["image_id"],
            receipt_id=label_dict["receipt_id"],
            line_id=label_dict["line_id"],
            word_id=label_dict["word_id"],
            label=label_dict["label"],
            reasoning=label_dict.get("reasoning"),
            timestamp_added=label_dict.get(
                "timestamp_added", datetime.now().isoformat()
            ),
            validation_status=label_dict.get("validation_status"),
            label_proposed_by=label_dict.get("label_proposed_by"),
            label_consolidated_from=label_dict.get("label_consolidated_from"),
        )
        backup_label_objects.append(label_obj)

    print(f"\n{'='*80}")
    print("ROLLBACK PLAN")
    print(f"{'='*80}")
    print(f"Labels to restore: {len(backup_label_objects)}")
    print(f"Dry run: {dry_run}")

    if dry_run:
        print("\n⚠️  DRY RUN MODE - No changes will be made to PROD")
        print("Run with --execute to restore labels")
        return

    # Execute rollback
    print(f"\n{'='*80}")
    print("EXECUTING ROLLBACK")
    print(f"{'='*80}")

    # Initialize PROD client
    print(f"Connecting to PROD table: {prod_table_name}")
    prod_client = DynamoClient(prod_table_name)

    # Restore all labels - update_receipt_word_labels will update existing and
    # add_receipt_word_labels will add missing ones
    # But we need to handle both: first add all (which will add or update), or use update which requires existence

    # Strategy: Use add_receipt_word_labels for all labels
    # It will add if new, or update if exists (based on DynamoDB Put behavior)
    print(f"Restoring {len(backup_label_objects)} labels...")
    print(f"  (Method handles chunking automatically)")

    try:
        # First, try to add all - this works for new labels
        # For existing labels, we'll use update
        # Actually, we can just use add_receipt_word_labels which will handle both
        # if the condition allows, or we need to split

        # Better approach: Get current labels for these images and compare
        # For now, simpler: just update all from backup

        # Use update_receipt_word_labels - this will update existing labels
        # Note: This won't add labels that don't exist, but will update ones that do
        # So we need to handle both cases

        # Split into labels that need update vs add by checking current state
        # Actually, simplest: use batch write with Put - this will replace/add

        # Check current state first - get all current labels for these images
        print("Checking current label state...")
        all_image_ids = {label.image_id for label in backup_label_objects}

        # Get current labels from PROD
        current_labels_dict = {}
        for image_id in tqdm(all_image_ids, desc="Fetching current labels"):
            try:
                # Get all labels for this image from PROD
                current_labels, _ = prod_client.list_receipt_word_labels_for_image(
                    image_id
                )
                for label in current_labels:
                    key = (
                        label.image_id,
                        label.receipt_id,
                        label.line_id,
                        label.word_id,
                        label.label,
                    )
                    current_labels_dict[key] = label
            except Exception as e:
                print(f"Warning: Could not fetch labels for image {image_id}: {e}")

        # Determine which labels need update vs add
        labels_to_update = []
        labels_to_add = []

        for backup_label in backup_label_objects:
            key = (
                backup_label.image_id,
                backup_label.receipt_id,
                backup_label.line_id,
                backup_label.word_id,
                backup_label.label,
            )
            if key in current_labels_dict:
                labels_to_update.append(backup_label)
            else:
                labels_to_add.append(backup_label)

        print(f"\nLabels to update (existing): {len(labels_to_update)}")
        print(f"Labels to add (missing): {len(labels_to_add)}")

        # Update existing labels - chunk to 25 per batch
        if labels_to_update:
            print(f"\nUpdating {len(labels_to_update)} existing labels...")
            batch_size = 25
            updated_count = 0
            for i in tqdm(
                range(0, len(labels_to_update), batch_size),
                desc="Updating batches",
                unit="batch",
            ):
                batch = labels_to_update[i : i + batch_size]
                prod_client.update_receipt_word_labels(batch)
                updated_count += len(batch)
            print(f"✓ Updated {updated_count} labels")

        # Add missing labels - chunk to 25 per batch
        if labels_to_add:
            print(f"\nAdding {len(labels_to_add)} missing labels...")
            batch_size = 25
            added_count = 0
            for i in tqdm(
                range(0, len(labels_to_add), batch_size),
                desc="Adding batches",
                unit="batch",
            ):
                batch = labels_to_add[i : i + batch_size]
                prod_client.add_receipt_word_labels(batch)
                added_count += len(batch)
            print(f"✓ Added {added_count} labels")

        # Handle labels that exist in PROD but not in backup (should be deleted?)
        # These are labels that were added during sync but shouldn't exist
        backup_keys = {
            (
                label.image_id,
                label.receipt_id,
                label.line_id,
                label.word_id,
                label.label,
            )
            for label in backup_label_objects
        }

        labels_to_delete = [
            label
            for key, label in current_labels_dict.items()
            if key not in backup_keys
        ]

        if labels_to_delete:
            print(
                f"\n⚠️  Found {len(labels_to_delete)} labels in PROD that are not in backup"
            )
            print("These may have been added during sync. Review before deleting.")
            # For safety, we don't auto-delete. User can review manually.

        print(f"\n{'='*80}")
        print("ROLLBACK COMPLETE")
        print(f"{'='*80}")
        print(f"Updated: {len(labels_to_update)}")
        print(f"Added: {len(labels_to_add)}")
        if labels_to_delete:
            print(f"Extra labels found (not deleted): {len(labels_to_delete)}")

    except Exception as e:
        print(f"\n✗ Error during rollback: {e}")
        raise


def sync_prod_labels_from_dev(
    prod_data_file: str,
    dev_data_file: str,
    prod_table_name: str,
    dry_run: bool = True,
    delete_prod_only_labels: bool = False,
    output_file: str = "sync_plan.json",
):
    """
    Create a plan to sync PROD labels from DEV labels.

    This function:
    1. Updates labels that differ between PROD and DEV
    2. Adds labels that exist in DEV but not PROD
    3. Flags labels that exist in PROD but not DEV (for manual review)
    4. Optionally deletes PROD-only labels

    Args:
        prod_data_file: Path to PROD detailed data JSON
        dev_data_file: Path to DEV detailed data JSON
        prod_table_name: PROD DynamoDB table name
        dry_run: If True, only create plan without executing
        delete_prod_only_labels: If True, delete labels that exist in PROD but not DEV
        output_file: Path to save sync plan
    """
    # Load data
    print(f"Loading PROD data from {prod_data_file}...")
    prod_data = load_json(prod_data_file)
    print(f"Loaded {len(prod_data)} images from PROD")

    print(f"Loading DEV data from {dev_data_file}...")
    dev_data = load_json(dev_data_file)
    print(f"Loaded {len(dev_data)} images from DEV")

    # Build index of DEV images by image_id
    dev_images_by_id = {img["image"]["image_id"]: img for img in dev_data}

    # Plan tracking
    labels_to_update = []  # Labels that exist in both but differ
    labels_to_add = []  # Labels in DEV but not PROD
    labels_to_delete = []  # Labels in PROD but not DEV
    labels_unchanged = 0

    print(f"Analyzing label differences...")

    for prod_img in tqdm(prod_data, desc="Analyzing", unit="image"):
        image_id = prod_img["image"]["image_id"]

        if image_id not in dev_images_by_id:
            # Image not in DEV - skip for now (could be handled separately)
            continue

        dev_img = dev_images_by_id[image_id]

        # Build label index by (image_id, receipt_id, line_id, word_id, label)
        # Note: Multiple labels can exist for same word (different label values)
        prod_labels_by_key = {}
        for label in prod_img.get("receipt_word_labels", []):
            key = (
                label["image_id"],
                label["receipt_id"],
                label["line_id"],
                label["word_id"],
            )
            if key not in prod_labels_by_key:
                prod_labels_by_key[key] = []
            prod_labels_by_key[key].append(label)

        dev_labels_by_key = {}
        for label in dev_img.get("receipt_word_labels", []):
            key = (
                label["image_id"],
                label["receipt_id"],
                label["line_id"],
                label["word_id"],
            )
            if key not in dev_labels_by_key:
                dev_labels_by_key[key] = []
            dev_labels_by_key[key].append(label)

        # Compare labels for each word position
        all_positions = set(
            list(prod_labels_by_key.keys()) + list(dev_labels_by_key.keys())
        )

        for position_key in all_positions:
            prod_labels_at_pos = prod_labels_by_key.get(position_key, [])
            dev_labels_at_pos = dev_labels_by_key.get(position_key, [])

            # Build label lookup by label value
            prod_by_label_value = {l["label"]: l for l in prod_labels_at_pos}
            dev_by_label_value = {l["label"]: l for l in dev_labels_at_pos}

            # Find labels in DEV that need to be added/updated in PROD
            for label_value, dev_label in dev_by_label_value.items():
                if label_value in prod_by_label_value:
                    # Label exists in both - check if it needs update
                    prod_label = prod_by_label_value[label_value]
                    needs_update = False

                    if (
                        prod_label.get("label") != dev_label.get("label")
                        or prod_label.get("validation_status")
                        != dev_label.get("validation_status")
                        or prod_label.get("reasoning") != dev_label.get("reasoning")
                    ):
                        needs_update = True

                    if needs_update:
                        labels_to_update.append(
                            {
                                "position": position_key,
                                "prod": prod_label,
                                "dev": dev_label,
                            }
                        )
                    else:
                        labels_unchanged += 1
                else:
                    # Label in DEV but not PROD - add it
                    labels_to_add.append({"position": position_key, "dev": dev_label})

            # Find labels in PROD but not in DEV
            for label_value, prod_label in prod_by_label_value.items():
                if label_value not in dev_by_label_value:
                    labels_to_delete.append(
                        {"position": position_key, "prod": prod_label}
                    )

    # Create sync plan
    sync_plan = {
        "summary": {
            "total_labels_unchanged": labels_unchanged,
            "labels_to_update": len(labels_to_update),
            "labels_to_add": len(labels_to_add),
            "labels_to_delete": len(labels_to_delete),
            "dry_run": dry_run,
            "delete_prod_only_labels": delete_prod_only_labels,
        },
        "labels_to_update": labels_to_update,
        "labels_to_add": labels_to_add,
        "labels_to_delete": labels_to_delete,
    }

    # Save plan
    with open(output_file, "w") as f:
        json.dump(sync_plan, f, indent=2, default=str)

    print(f"\n{'='*80}")
    print("SYNC PLAN SUMMARY")
    print(f"{'='*80}")
    print(f"Labels unchanged: {labels_unchanged}")
    print(f"Labels to update: {len(labels_to_update)}")
    print(f"Labels to add: {len(labels_to_add)}")
    print(f"Labels in PROD but not DEV: {len(labels_to_delete)}")
    print(f"\nSync plan saved to: {output_file}")

    if dry_run:
        print("\n⚠️  DRY RUN MODE - No changes will be made to PROD")
        print("Run with --execute to apply changes")
        return sync_plan

    # Execute the plan
    if len(labels_to_update) == 0 and len(labels_to_add) == 0:
        print("\n✓ No changes needed!")
        return sync_plan

    print(f"\n{'='*80}")
    print("EXECUTING SYNC PLAN")
    print(f"{'='*80}")

    # Initialize PROD client
    print(f"Connecting to PROD table: {prod_table_name}")
    prod_client = DynamoClient(prod_table_name)

    # Convert to ReceiptWordLabel objects and batch update
    labels_to_update_objects = []
    for item in tqdm(labels_to_update, desc="Preparing updates", unit="label"):
        dev_label = item["dev"]
        label_obj = ReceiptWordLabel(
            image_id=dev_label["image_id"],
            receipt_id=dev_label["receipt_id"],
            line_id=dev_label["line_id"],
            word_id=dev_label["word_id"],
            label=dev_label["label"],
            reasoning=dev_label.get("reasoning"),
            timestamp_added=dev_label.get(
                "timestamp_added", datetime.now().isoformat()
            ),
            validation_status=dev_label.get("validation_status"),
            label_proposed_by=dev_label.get("label_proposed_by"),
            label_consolidated_from=dev_label.get("label_consolidated_from"),
        )
        labels_to_update_objects.append(label_obj)

    # Batch update - chunk to 25 per batch (DynamoDB transaction limit)
    if labels_to_update_objects:
        print(f"Updating {len(labels_to_update_objects)} labels...")
        print(f"  (Chunking to 25 per transaction)")
        batch_size = 25
        updated_count = 0
        try:
            for i in tqdm(
                range(0, len(labels_to_update_objects), batch_size),
                desc="Updating batches",
                unit="batch",
            ):
                batch = labels_to_update_objects[i : i + batch_size]
                prod_client.update_receipt_word_labels(batch)
                updated_count += len(batch)
            print(f"✓ Updated {updated_count} labels")
        except Exception as e:
            print(f"✗ Error updating labels: {e}")
            raise

    # Convert labels to add
    labels_to_add_objects = []
    for item in tqdm(labels_to_add, desc="Preparing adds", unit="label"):
        dev_label = item["dev"]
        label_obj = ReceiptWordLabel(
            image_id=dev_label["image_id"],
            receipt_id=dev_label["receipt_id"],
            line_id=dev_label["line_id"],
            word_id=dev_label["word_id"],
            label=dev_label["label"],
            reasoning=dev_label.get("reasoning"),
            timestamp_added=dev_label.get(
                "timestamp_added", datetime.now().isoformat()
            ),
            validation_status=dev_label.get("validation_status"),
            label_proposed_by=dev_label.get("label_proposed_by"),
            label_consolidated_from=dev_label.get("label_consolidated_from"),
        )
        labels_to_add_objects.append(label_obj)

    # Batch add - chunk to 25 per batch (DynamoDB batch write limit)
    if labels_to_add_objects:
        print(f"Adding {len(labels_to_add_objects)} new labels...")
        print(f"  (Chunking to 25 per batch)")
        batch_size = 25
        added_count = 0
        try:
            for i in tqdm(
                range(0, len(labels_to_add_objects), batch_size),
                desc="Adding batches",
                unit="batch",
            ):
                batch = labels_to_add_objects[i : i + batch_size]
                prod_client.add_receipt_word_labels(batch)
                added_count += len(batch)
            print(f"✓ Added {added_count} labels")
        except Exception as e:
            print(f"✗ Error adding labels: {e}")
            raise

    # Handle PROD-only labels
    if delete_prod_only_labels and labels_to_delete:
        print(f"\n⚠️  Found {len(labels_to_delete)} labels in PROD but not DEV")
        print("These labels would be deleted. Review the sync plan first!")
        # For safety, we don't auto-delete. User should review manually.

    print(f"\n{'='*80}")
    print("SYNC COMPLETE")
    print(f"{'='*80}")
    print(f"Updated: {len(labels_to_update)}")
    print(f"Added: {len(labels_to_add)}")
    print(f"PROD-only labels (not deleted): {len(labels_to_delete)}")

    return sync_plan


def compare_images_and_receipts(
    prod_data_file: str,
    dev_data_file: str,
    output_file: str = "comparison_report.txt",
):
    """
    Compare images and receipts between PROD and DEV environments.

    Args:
        prod_data_file: Path to PROD detailed data JSON
        dev_data_file: Path to DEV detailed data JSON
        output_file: Path to save comparison report
    """
    # Load data
    print(f"Loading PROD data from {prod_data_file}...")
    prod_data = load_json(prod_data_file)
    print(f"Loaded {len(prod_data)} images from PROD")

    print(f"Loading DEV data from {dev_data_file}...")
    dev_data = load_json(dev_data_file)
    print(f"Loaded {len(dev_data)} images from DEV")

    # Build index of DEV images by image_id
    dev_images_by_id = {img["image"]["image_id"]: img for img in dev_data}

    # Track comparisons
    matching_images = []
    only_in_prod = []
    only_in_dev = []
    text_differences = []

    print(
        f"Comparing PROD ({len(prod_data)} images) with DEV ({len(dev_data)} images)..."
    )

    for prod_img in tqdm(prod_data, desc="Comparing images", unit="image"):
        image_id = prod_img["image"]["image_id"]

        if image_id in dev_images_by_id:
            # Both environments have this image - compare in detail
            dev_img = dev_images_by_id[image_id]

            # Extract text from PROD and DEV receipt words
            prod_text = extract_text_from_words(prod_img["receipt_words"])
            dev_text = extract_text_from_words(dev_img["receipt_words"])

            # Compare by text
            if prod_text == dev_text:
                matching_images.append(
                    {
                        "image_id": image_id,
                        "status": "identical_text",
                        "prod_receipt_count": len(prod_img["receipts"]),
                        "dev_receipt_count": len(dev_img["receipts"]),
                    }
                )
            else:
                text_differences.append(
                    {
                        "image_id": image_id,
                        "prod_text": (
                            prod_text[:200] + "..."
                            if len(prod_text) > 200
                            else prod_text
                        ),
                        "dev_text": (
                            dev_text[:200] + "..." if len(dev_text) > 200 else dev_text
                        ),
                        "prod_word_count": len(prod_img["receipt_words"]),
                        "dev_word_count": len(dev_img["receipt_words"]),
                        "prod_line_count": len(prod_img["receipt_lines"]),
                        "dev_line_count": len(dev_img["receipt_lines"]),
                    }
                )
        else:
            # Only in PROD
            only_in_prod.append(
                {
                    "image_id": image_id,
                    "receipt_count": len(prod_img["receipts"]),
                    "word_count": len(prod_img["receipt_words"]),
                }
            )

    # Find images only in DEV
    prod_images_by_id = {img["image"]["image_id"]: img for img in prod_data}
    for dev_img in dev_data:
        image_id = dev_img["image"]["image_id"]
        if image_id not in prod_images_by_id:
            only_in_dev.append(
                {
                    "image_id": image_id,
                    "receipt_count": len(dev_img["receipts"]),
                    "word_count": len(dev_img["receipt_words"]),
                }
            )

    # Generate report
    report = []
    report.append("=" * 80)
    report.append("COMPARISON REPORT: PROD vs DEV")
    report.append("=" * 80)
    report.append("")

    report.append(f"Total PROD images: {len(prod_data)}")
    report.append(f"Total DEV images: {len(dev_data)}")
    report.append("")

    report.append("-" * 80)
    report.append(f"MATCHING IMAGES (identical text): {len(matching_images)}")
    report.append("-" * 80)
    for img in matching_images[:10]:  # Show first 10
        report.append(f"  Image ID: {img['image_id']}")
        report.append(
            f"    PROD receipts: {img['prod_receipt_count']}, DEV receipts: {img['dev_receipt_count']}"
        )
    if len(matching_images) > 10:
        report.append(f"  ... and {len(matching_images) - 10} more")
    report.append("")

    report.append("-" * 80)
    report.append(f"TEXT DIFFERENCES: {len(text_differences)}")
    report.append("-" * 80)
    for diff in text_differences[:10]:  # Show first 10
        report.append(f"  Image ID: {diff['image_id']}")
        report.append(
            f"    PROD words: {diff['prod_word_count']}, lines: {diff['prod_line_count']}"
        )
        report.append(
            f"    DEV words: {diff['dev_word_count']}, lines: {diff['dev_line_count']}"
        )
        report.append(f"    PROD text: {diff['prod_text']}")
        report.append(f"    DEV text: {diff['dev_text']}")
        report.append("")
    if len(text_differences) > 10:
        report.append(f"  ... and {len(text_differences) - 10} more")
    report.append("")

    report.append("-" * 80)
    report.append(f"ONLY IN PROD: {len(only_in_prod)}")
    report.append("-" * 80)
    for img in only_in_prod[:10]:  # Show first 10
        report.append(f"  Image ID: {img['image_id']}")
        report.append(
            f"    Receipts: {img['receipt_count']}, Words: {img['word_count']}"
        )
    if len(only_in_prod) > 10:
        report.append(f"  ... and {len(only_in_prod) - 10} more")
    report.append("")

    report.append("-" * 80)
    report.append(f"ONLY IN DEV: {len(only_in_dev)}")
    report.append("-" * 80)
    for img in only_in_dev[:10]:  # Show first 10
        report.append(f"  Image ID: {img['image_id']}")
        report.append(
            f"    Receipts: {img['receipt_count']}, Words: {img['word_count']}"
        )
    if len(only_in_dev) > 10:
        report.append(f"  ... and {len(only_in_dev) - 10} more")
    report.append("")

    # Save report
    report_text = "\n".join(report)
    with open(output_file, "w") as f:
        f.write(report_text)

    print(f"Comparison report saved to {output_file}")

    # Print summary
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"Identical images: {len(matching_images)}")
    print(f"Images with text differences: {len(text_differences)}")
    print(f"Images only in PROD: {len(only_in_prod)}")
    print(f"Images only in DEV: {len(only_in_dev)}")


def main():
    parser = argparse.ArgumentParser(
        description="Download and compare receipts between environments"
    )
    parser.add_argument(
        "command",
        choices=[
            "download",
            "download-detailed",
            "compare",
            "run",
            "compare-labels",
            "sync-labels",
            "rollback",
        ],
        help="Command to execute",
    )
    parser.add_argument("--table", type=str, help="DynamoDB table name")
    parser.add_argument("--output-dir", type=str, help="Output directory")
    parser.add_argument("--prod-file", type=str, help="PROD data file for comparison")
    parser.add_argument("--dev-file", type=str, help="DEV data file for comparison")
    parser.add_argument(
        "--report",
        type=str,
        default="comparison_report.txt",
        help="Output report file",
    )
    parser.add_argument(
        "--env",
        type=str,
        choices=["prod", "dev"],
        help="Environment (prod or dev) for single-env operations",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-download even if files exist",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Execute sync (default is dry-run)",
    )
    parser.add_argument(
        "--delete-prod-only",
        action="store_true",
        help="Delete labels that exist in PROD but not DEV (use with caution!)",
    )

    args = parser.parse_args()

    if args.command == "download":
        if not args.output_dir:
            print("Error: --output-dir is required")
            sys.exit(1)

        if args.table:
            table_name = args.table
        elif args.env:
            table_name = get_table_name(args.env)
        else:
            print("Error: Either --table or --env must be specified")
            sys.exit(1)

        download_all_images_and_receipts(table_name, args.output_dir)

    elif args.command == "download-detailed":
        if not args.output_dir:
            print("Error: --output-dir is required")
            sys.exit(1)

        if args.table:
            table_name = args.table
        elif args.env:
            table_name = get_table_name(args.env)
        else:
            print("Error: Either --table or --env must be specified")
            sys.exit(1)

        download_detailed_data_for_comparison(
            table_name, args.output_dir, force_download=args.force
        )

    elif args.command == "compare":
        if not args.prod_file or not args.dev_file:
            print("Error: --prod-file and --dev-file are required for compare command")
            sys.exit(1)
        compare_images_and_receipts(args.prod_file, args.dev_file, args.report)

    elif args.command == "compare-labels":
        if not args.prod_file or not args.dev_file:
            print(
                "Error: --prod-file and --dev-file are required for compare-labels command"
            )
            sys.exit(1)
        compare_labels(args.prod_file, args.dev_file, args.report)

    elif args.command == "sync-labels":
        if not args.prod_file or not args.dev_file:
            print(
                "Error: --prod-file and --dev-file are required for sync-labels command"
            )
            sys.exit(1)

        # Get PROD table name
        prod_table = get_table_name("prod")

        sync_prod_labels_from_dev(
            prod_data_file=args.prod_file,
            dev_data_file=args.dev_file,
            prod_table_name=prod_table,
            dry_run=not args.execute,
            delete_prod_only_labels=args.delete_prod_only,
            output_file=args.report,
        )

    elif args.command == "rollback":
        if not args.prod_file:
            print("Error: --prod-file is required for rollback command")
            print(
                "This should be the PROD backup JSON file created by download-detailed"
            )
            sys.exit(1)

        # Get PROD table name
        prod_table = get_table_name("prod")

        rollback_prod_labels_from_backup(
            prod_backup_file=args.prod_file,
            prod_table_name=prod_table,
            dry_run=not args.execute,
        )

    elif args.command == "run":
        # Run the complete workflow: download from PROD and DEV, then compare
        print("Starting complete comparison workflow...")

        # Get table names
        prod_table = get_table_name("prod")
        dev_table = get_table_name("dev")

        print(f"\n{'='*80}")
        print("Step 1: Downloading PROD data")
        print(f"{'='*80}\n")
        prod_dir = "./prod_detailed"
        download_detailed_data_for_comparison(
            prod_table, prod_dir, force_download=args.force
        )

        print(f"\n{'='*80}")
        print("Step 2: Downloading DEV data")
        print(f"{'='*80}\n")
        dev_dir = "./dev_detailed"
        download_detailed_data_for_comparison(
            dev_table, dev_dir, force_download=args.force
        )

        # Find the output files
        import glob

        prod_files = glob.glob(f"{prod_dir}/*_detailed_data.json")
        dev_files = glob.glob(f"{dev_dir}/*_detailed_data.json")

        if not prod_files or not dev_files:
            print("Error: Could not find downloaded data files")
            sys.exit(1)

        prod_file = prod_files[0]
        dev_file = dev_files[0]

        print(f"\n{'='*80}")
        print("Step 3: Comparing PROD and DEV")
        print(f"{'='*80}\n")
        compare_images_and_receipts(prod_file, dev_file, args.report)

        print(f"\n{'='*80}")
        print("Complete workflow finished!")
        print(f"{'='*80}")


if __name__ == "__main__":
    main()
