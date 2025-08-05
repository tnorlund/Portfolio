#!/usr/bin/env python3
"""
Reset embedding status for all receipt lines to trigger ChromaDB step function.
"""

import os
import sys

from receipt_dynamo import DynamoClient
from receipt_dynamo.constants import EmbeddingStatus


def reset_line_embeddings(dry_run: bool = True) -> None:
    """
    Reset all receipt lines with SUCCESS embedding status back to NONE.

    Args:
        dry_run: If True, only show what would be updated without making
            changes
    """
    # Initialize DynamoDB client
    client = DynamoClient(os.environ.get("DYNAMODB_TABLE_NAME"))

    # Query all receipt lines with successful embeddings
    print("Querying receipt lines with successful embeddings...")

    lines_to_reset = client.list_receipt_lines_by_embedding_status(
        EmbeddingStatus.SUCCESS
    )

    print(f"Found {len(lines_to_reset)} lines with SUCCESS embedding status")

    if not lines_to_reset:
        print("No lines need resetting.")
        return

    # Show sample of lines to be reset
    print("\nSample of lines to reset:")
    for line in lines_to_reset[:5]:
        print(
            f"  - Receipt {line.receipt_id}, Line {line.line_number}: '{line.text[:50]}...'"
        )

    if len(lines_to_reset) > 5:
        print(f"  ... and {len(lines_to_reset) - 5} more")

    if dry_run:
        print("\nDRY RUN: No changes made. Run with --execute to apply changes.")
        return

    # Reset embedding status for each line
    print(f"\nResetting embedding status for {len(lines_to_reset)} lines...")

    success_count = 0
    error_count = 0

    for i, line in enumerate(lines_to_reset):
        try:
            # Update the line's embedding status
            line.embedding_status = EmbeddingStatus.NONE

            # Save to DynamoDB
            client.put_item(Item=line.to_item())

            success_count += 1

            # Progress indicator
            if (i + 1) % 100 == 0:
                print(f"  Processed {i + 1}/{len(lines_to_reset)} lines...")

        except Exception as e:
            error_count += 1
            print(f"  Error updating line {line.receipt_id}/{line.line_number}: {e}")

    print("\nReset complete!")
    print(f"  Successfully reset: {success_count} lines")
    print(f"  Errors: {error_count} lines")

    if success_count > 0:
        print("\nNext steps:")
        print("1. Run the step function to create new embedding batches")
        print("2. The ChromaDB polling Lambda will process the batches")
        print("3. Delta files will be written to S3")
        print("4. The compaction Lambda will create the final ChromaDB snapshot")


def update_all_lines(
    dry_run: bool = True, list_limit: int = 1000, update_limit: int = 25
) -> None:
    """
    Update all receipt lines to have NONE embedding status.
    """
    client = DynamoClient(os.environ.get("DYNAMODB_TABLE_NAME"))

    print("Reading all lines", end="", flush=True)
    lines, last_evaluated_key = client.list_receipt_lines(
        limit=list_limit,
        last_evaluated_key=None,
    )
    print(".", end="", flush=True)

    while last_evaluated_key:
        next_lines, last_evaluated_key = client.list_receipt_lines(
            limit=list_limit,
            last_evaluated_key=last_evaluated_key,
        )
        lines.extend(next_lines)
        print(".", end="", flush=True)

    print()  # New line after reading
    print(f"Found {len(lines)} lines to update")

    if dry_run:
        print("DRY RUN: No changes made")
        return

    print("Updating all lines", end="", flush=True)
    # Batch by update_limit
    for i in range(0, len(lines), update_limit):
        batch = lines[i : i + update_limit]
        client.update_receipt_lines(batch)
        print(".", end="", flush=True)

    print()  # New line after updating
    print("Update complete!")


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Reset embedding status for receipt lines to trigger ChromaDB migration"
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually perform the reset (default is dry run)",
    )

    args = parser.parse_args()

    print("ChromaDB Migration: Reset Line Embeddings")
    print("=" * 50)

    # Check for required environment variable
    if not os.environ.get("DYNAMODB_TABLE_NAME"):
        print("ERROR: DYNAMODB_TABLE_NAME environment variable not set")
        print("Run: export DYNAMODB_TABLE_NAME=ReceiptsTable-dc5be22")
        sys.exit(1)

    reset_line_embeddings(dry_run=not args.execute)


if __name__ == "__main__":
    main()
