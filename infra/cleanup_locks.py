#!/usr/bin/env python3
"""
Clean up compaction locks using the proper DynamoDB client methods.
"""

import sys

import botocore.exceptions

from receipt_dynamo import DynamoClient
from receipt_dynamo.data._pulumi import load_env
from receipt_dynamo.data.shared_exceptions import (
    DynamoDBError,
    DynamoDBServerError,
    DynamoDBThroughputError,
    EntityNotFoundError,
    EntityValidationError,
    OperationError,
)


def list_all_locks():
    """List all compaction locks in the table."""
    client = DynamoClient(load_env().get("dynamodb_table_name"))

    print("Listing all compaction locks...")
    locks, _ = client.list_compaction_locks()

    print(f"Found {len(locks)} locks:")
    for lock in locks:
        print(f"  - {lock}")

    return locks


def cleanup_expired_locks():
    """Clean up expired locks using the built-in method."""
    client = DynamoClient(load_env().get("dynamodb_table_name"))

    print("Cleaning up expired locks...")
    count = client.cleanup_expired_locks()
    print(f"Removed {count} expired locks")

    return count


def delete_all_locks_individually():
    """Delete all locks one by one to avoid batch write limits."""
    client = DynamoClient(load_env().get("dynamodb_table_name"))

    # First list all locks
    locks, _ = client.list_compaction_locks()

    print(f"Deleting {len(locks)} locks individually...")
    success_count = 0
    error_count = 0

    for i, lock in enumerate(locks, 1):
        try:
            client.delete_compaction_lock(lock.lock_id, lock.owner, lock.collection)
            success_count += 1
            if i % 50 == 0:  # Progress update every 50 deletions
                print(
                    f"  Progress: {i}/{len(locks)} "
                    f"({success_count} successful, {error_count} errors)"
                )
        except (
            EntityNotFoundError,
            EntityValidationError,
            DynamoDBThroughputError,
            DynamoDBServerError,
            DynamoDBError,
            OperationError,
            botocore.exceptions.ClientError,
        ) as e:
            error_count += 1
            if "ConditionalCheckFailedException" not in str(
                e
            ):  # Don't log ownership errors
                print(f"  ✗ Failed to delete lock {lock.lock_id}: {e}")

    print(f"  Final result: {success_count} successful, {error_count} errors")


if __name__ == "__main__":
    print("=== Compaction Lock Cleanup ===\n")

    # List current locks
    all_locks = list_all_locks()

    if not all_locks:
        print("No locks found. System is clean.")
        sys.exit(0)
    print(f"\nFound {len(all_locks)} total locks")

    # Skip the expired cleanup since it's causing batch errors
    # Go straight to individual deletion
    print("\nWARNING: Found a large number of locks. This likely indicates")
    print("malformed locks from the old system that need to be cleaned up.")

    response = input(
        f"Delete all {len(all_locks)} locks individually? "
        f"This will take some time. (y/N): "
    )
    if response.lower() == "y":
        print("\n--- Deleting all locks individually ---")
        delete_all_locks_individually()

        # Final verification
        print("\n--- Final verification ---")
        final_locks = list_all_locks()
        if not final_locks:
            print("✓ All locks successfully removed!")
        else:
            print(f"⚠ {len(final_locks)} locks still remain")
    else:
        print("Lock cleanup cancelled.")
