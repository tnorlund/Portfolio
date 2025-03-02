#!/usr/bin/env python3
"""
Example script demonstrating how to use the refine_ocr module to process a receipt.
"""

import sys
import os
from pathlib import Path

# Add the parent directory to the Python path to enable imports
parent_dir = Path(__file__).resolve().parent.parent
sys.path.append(str(parent_dir))

# Import the required modules
from receipt_dynamo.data._pulumi import load_env
from receipt_dynamo.data.refine_ocr import (
    refine_receipt_ocr,
    process_all_receipts,
)


def process_single_receipt():
    """Process a single receipt by its image_id and receipt_id."""
    # Load environment variables
    env = load_env("prod")

    # Example values - replace with actual IDs
    image_id = "03fa2d0f-33c6-43be-88b0-dae73ec26c93"
    receipt_id = 1

    print(f"Processing receipt {image_id}_{receipt_id}...")
    print(f"Using table: {env['dynamodb_table_name']}")

    # Process the receipt in dry-run mode (no database changes)
    result = refine_receipt_ocr(
        image_id=image_id,
        receipt_id=receipt_id,
        env=env,
        debug=True,
        commit_changes=False,  # Set to True to commit changes to the database
    )

    # Print summary results
    print("\n=== Summary ===")
    print(f"Success: {result['success']}")

    if result["success"]:
        print("\nOld OCR Entities:")
        for entity_type, count in result["old_data"].items():
            print(f"  {entity_type.capitalize()}: {count}")

        print("\nNew OCR Entities:")
        for entity_type, count in result["new_data"].items():
            print(f"  {entity_type.capitalize()}: {count}")

        print("\nTag Transfer:")
        print(
            f"  Tags transferred: {result['tag_transfer']['transferred_tags']} of {result['tag_transfer']['total_tags']}"
        )
        print(
            f"  Human-validated tags transferred: {result['tag_transfer']['validated_tags_transferred']}"
        )
        print(
            f"  Non-validated tags transferred: {result['tag_transfer']['non_validated_tags_transferred']}"
        )
        print(
            f"  Transfer rate: {result['tag_transfer']['transfer_rate']:.2%}"
        )


def process_multiple_receipts():
    """Process multiple receipts from the database."""
    # Load environment variables
    env = load_env("prod")
    print(f"Using table: {env['dynamodb_table_name']}")

    # Process up to 5 receipts in dry-run mode
    print("Processing multiple receipts...")
    results = process_all_receipts(
        env=env,
        limit=5,
        debug=True,
        commit_changes=False,  # Set to True to commit changes to the database
    )

    # Print summary results
    print("\n=== Summary ===")
    print(f"Receipts processed: {results['processed']}")
    print(f"Successes: {results['succeeded']}")
    print(f"Failures: {results['failed']}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Refine OCR results for receipts"
    )
    parser.add_argument(
        "-m",
        "--multiple",
        action="store_true",
        help="Process multiple receipts",
    )
    parser.add_argument(
        "-i", "--image-id", help="Image ID for single receipt processing"
    )
    parser.add_argument(
        "-r",
        "--receipt-id",
        type=int,
        help="Receipt ID for single receipt processing",
    )
    parser.add_argument(
        "--commit", action="store_true", help="Commit changes to the database"
    )
    parser.add_argument(
        "--env",
        default="prod",
        choices=["prod", "dev", "test"],
        help="Environment to use (prod, dev, test)",
    )

    args = parser.parse_args()

    # Load the specified environment
    env = load_env(args.env)

    if args.multiple:
        # Process multiple receipts
        results = process_all_receipts(
            env=env, limit=5, debug=True, commit_changes=args.commit
        )

        # Print summary results
        print("\n=== Summary ===")
        print(f"Receipts processed: {results['processed']}")
        print(f"Successes: {results['succeeded']}")
        print(f"Failures: {results['failed']}")

    elif args.image_id and args.receipt_id:
        # Process a single receipt
        result = refine_receipt_ocr(
            image_id=args.image_id,
            receipt_id=args.receipt_id,
            env=env,
            debug=True,
            commit_changes=args.commit,
        )

        # Print summary results
        print("\n=== Summary ===")
        print(f"Success: {result['success']}")

        if result["success"]:
            print("\nOld OCR Entities:")
            for entity_type, count in result["old_data"].items():
                print(f"  {entity_type.capitalize()}: {count}")

            print("\nNew OCR Entities:")
            for entity_type, count in result["new_data"].items():
                print(f"  {entity_type.capitalize()}: {count}")

            print("\nTag Transfer:")
            print(
                f"  Tags transferred: {result['tag_transfer']['transferred_tags']} of {result['tag_transfer']['total_tags']}"
            )
            print(
                f"  Human-validated tags transferred: {result['tag_transfer']['validated_tags_transferred']}"
            )
            print(
                f"  Non-validated tags transferred: {result['tag_transfer']['non_validated_tags_transferred']}"
            )
            print(
                f"  Transfer rate: {result['tag_transfer']['transfer_rate']:.2%}"
            )
    else:
        # Use the default example function if no specific arguments provided
        process_single_receipt()
