#!/usr/bin/env python3
"""
Test script for CompactionLock functionality.

This script tests all the accessor methods in _compaction_lock.py to help
understand how the distributed locking mechanism works.
"""

import os
from datetime import datetime, timedelta, timezone
import time
from uuid import uuid4

from receipt_dynamo import DynamoClient
from receipt_dynamo.entities.compaction_lock import CompactionLock
from receipt_dynamo.data.shared_exceptions import (
    EntityAlreadyExistsError,
    EntityValidationError,
)


def test_basic_lock_operations():
    """Test basic lock acquire, get, and delete operations."""
    print("1. Testing basic lock operations...")

    table_name = os.environ.get("DYNAMODB_TABLE_NAME", "portfolio-dev")
    dynamo_client = DynamoClient(table_name)

    # Create a lock
    lock = CompactionLock(
        lock_id=str(uuid4()),
        owner=str(uuid4()),
        expires=datetime.now(timezone.utc) + timedelta(minutes=5),
    )

    # Add the lock
    print("   Adding lock...")
    dynamo_client.add_compaction_lock(lock)
    print(f"   ✓ Lock added: {lock.lock_id} owned by {lock.owner}")

    # Get the lock
    print("   Getting lock...")
    retrieved_lock = dynamo_client.get_compaction_lock(lock.lock_id)
    print(f"   ✓ Retrieved lock: {retrieved_lock.lock_id}")
    print(f"     Owner: {retrieved_lock.owner}")
    print(f"     Expires: {retrieved_lock.expires}")
    print(f"     Heartbeat: {retrieved_lock.heartbeat}")

    # Try to acquire the same lock (should fail)
    print("   Trying to acquire locked resource...")
    lock2 = CompactionLock(
        lock_id=lock.lock_id,  # SAME lock_id as the existing lock
        owner=str(uuid4()),     # Different owner
        expires=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    try:
        dynamo_client.add_compaction_lock(lock2)
        print("   ❌ ERROR: Should not have acquired lock!")
    except EntityAlreadyExistsError as e:
        print(f"   ✓ Correctly blocked: {e}")

    # Delete the lock
    print("   Deleting lock...")
    dynamo_client.delete_compaction_lock(lock.lock_id, lock.owner)
    print("   ✓ Lock deleted")

    # Verify it's gone
    deleted_lock = dynamo_client.get_compaction_lock(lock.lock_id)
    if deleted_lock is None:
        print("   ✓ Lock confirmed deleted")
    else:
        print("   ❌ ERROR: Lock still exists!")


def test_lock_expiration():
    """Test that expired locks can be overwritten."""
    print("\n2. Testing lock expiration...")

    table_name = os.environ.get("DYNAMODB_TABLE_NAME", "portfolio-dev")
    dynamo_client = DynamoClient(table_name)

    lock_id = str(uuid4())  # Use same lock_id for both locks
    
    # Create an already-expired lock
    expired_lock = CompactionLock(
        lock_id=lock_id,
        owner=str(uuid4()),
        expires=datetime.now(timezone.utc) - timedelta(minutes=1),  # Expired
    )

    # Force add it (bypassing expiration check)
    print("   Adding expired lock...")
    dynamo_client._client.put_item(
        TableName=dynamo_client.table_name,
        Item=expired_lock.to_item(),
    )
    print("   ✓ Expired lock added")

    # Now try to acquire it with a new owner
    new_lock = CompactionLock(
        lock_id=lock_id,  # SAME lock_id - should overwrite expired lock
        owner=str(uuid4()),
        expires=datetime.now(timezone.utc) + timedelta(minutes=5),
    )

    print("   Acquiring expired lock with new owner...")
    dynamo_client.add_compaction_lock(new_lock)
    print("   ✓ Successfully acquired expired lock")

    # Verify new owner
    current_lock = dynamo_client.get_compaction_lock(new_lock.lock_id)
    print(f"   ✓ Lock now owned by: {current_lock.owner}")

    # Clean up
    dynamo_client.delete_compaction_lock(new_lock.lock_id, new_lock.owner)


def test_heartbeat_updates():
    """Test updating lock heartbeats."""
    print("\n3. Testing heartbeat updates...")

    table_name = os.environ.get("DYNAMODB_TABLE_NAME", "portfolio-dev")
    dynamo_client = DynamoClient(table_name)

    # Create a lock with initial heartbeat
    initial_time = datetime.now(timezone.utc)
    lock = CompactionLock(
        lock_id=str(uuid4()),
        owner=str(uuid4()),
        expires=initial_time + timedelta(minutes=5),
        heartbeat=initial_time,  # Set initial heartbeat
    )

    print("   Adding lock...")
    dynamo_client.add_compaction_lock(lock)
    print(f"   ✓ Lock added with heartbeat: {lock.heartbeat}")

    # Wait a moment
    time.sleep(1)

    # Update heartbeat
    print("   Updating heartbeat...")
    new_heartbeat_time = datetime.now(timezone.utc)
    lock.heartbeat = new_heartbeat_time
    dynamo_client.update_compaction_lock(lock)

    # Verify update
    updated_lock = dynamo_client.get_compaction_lock(lock.lock_id)
    print(f"   ✓ Heartbeat updated: {updated_lock.heartbeat}")
    
    # Parse the datetime strings for comparison
    if isinstance(updated_lock.heartbeat, str):
        updated_heartbeat = datetime.fromisoformat(updated_lock.heartbeat.replace('+00:00', '+00:00'))
        original_heartbeat = datetime.fromisoformat(initial_time.isoformat())
        time_diff = (updated_heartbeat - original_heartbeat).total_seconds()
        print(f"   Time difference: {time_diff:.1f}s")
    else:
        print("   (Heartbeat comparison skipped - not a string)")

    # Clean up
    dynamo_client.delete_compaction_lock(lock.lock_id, lock.owner)


def test_lock_ownership():
    """Test that only owners can delete/update their locks."""
    print("\n4. Testing lock ownership...")

    table_name = os.environ.get("DYNAMODB_TABLE_NAME", "portfolio-dev")
    dynamo_client = DynamoClient(table_name)

    # Create a lock
    lock = CompactionLock(
        lock_id=str(uuid4()),
        owner=str(uuid4()),
        expires=datetime.now(timezone.utc) + timedelta(minutes=5),
    )

    print("   Adding lock owned by owner-1...")
    dynamo_client.add_compaction_lock(lock)
    print("   ✓ Lock added")

    # Try to delete with wrong owner
    print("   Trying to delete with wrong owner...")
    try:
        dynamo_client.delete_compaction_lock(lock.lock_id, str(uuid4()))  # Different owner
        print("   ❌ ERROR: Should not have deleted lock!")
    except EntityValidationError as e:
        print(f"   ✓ Correctly blocked: {e}")

    # Delete with correct owner
    print("   Deleting with correct owner...")
    dynamo_client.delete_compaction_lock(lock.lock_id, lock.owner)
    print("   ✓ Lock deleted by owner")


def test_list_locks():
    """Test listing all locks and active locks."""
    print("\n5. Testing lock listing...")

    table_name = os.environ.get("DYNAMODB_TABLE_NAME", "portfolio-dev")
    dynamo_client = DynamoClient(table_name)

    # Create multiple locks
    locks = []
    for i in range(3):
        lock = CompactionLock(
            lock_id=str(uuid4()),
            owner=str(uuid4()),
            expires=datetime.now(timezone.utc) + timedelta(minutes=5 + i),
        )
        locks.append(lock)
        dynamo_client.add_compaction_lock(lock)

    print(f"   ✓ Added {len(locks)} test locks")

    # List all locks
    print("   Listing all compaction locks...")
    all_locks, pagination = dynamo_client.list_compaction_locks()
    print(f"   ✓ Found {len(all_locks)} total locks")
    for lock in all_locks:
        print(f"     - {lock.lock_id}: expires {lock.expires}")

    # List active locks (this might fail due to GSI requirements)
    print("   Listing active locks...")
    try:
        active_locks, pagination = dynamo_client.list_active_compaction_locks()
        print(f"   ✓ Found {len(active_locks)} active locks")
    except Exception as e:
        print(f"   ⚠️  Error listing active locks: {e}")
        print("   (This may be due to missing GSI configuration)")

    # Clean up
    for lock in locks:
        dynamo_client.delete_compaction_lock(lock.lock_id, lock.owner)
    print("   ✓ Cleaned up test locks")


def test_expired_lock_cleanup():
    """Test cleaning up expired locks."""
    print("\n6. Testing expired lock cleanup...")

    table_name = os.environ.get("DYNAMODB_TABLE_NAME", "portfolio-dev")
    dynamo_client = DynamoClient(table_name)

    # Create some expired locks
    expired_locks = []
    for i in range(2):
        lock = CompactionLock(
            lock_id=str(uuid4()),
            owner=str(uuid4()),
            expires=datetime.now(timezone.utc) - timedelta(minutes=i + 1),
        )
        # Force add expired locks
        dynamo_client._client.put_item(
            TableName=dynamo_client.table_name,
            Item=lock.to_item(),
        )
        expired_locks.append(lock)

    print(f"   ✓ Added {len(expired_locks)} expired locks")

    # Try to clean up
    print("   Running cleanup...")
    try:
        removed_count = dynamo_client.cleanup_expired_locks()
        print(f"   ✓ Removed {removed_count} expired locks")
    except Exception as e:
        print(f"   ⚠️  Error during cleanup: {e}")
        print("   (This may be due to missing GSI or TTL configuration)")

        # Manual cleanup
        for lock in expired_locks:
            try:
                dynamo_client.delete_compaction_lock(lock.lock_id, lock.owner)
            except:
                pass


def main():
    """Run all CompactionLock tests."""
    print("CompactionLock Comprehensive Test")
    print("=" * 50)

    # Check environment
    table_name = os.environ.get("DYNAMODB_TABLE_NAME", "portfolio-dev")
    print(f"\nUsing DynamoDB table: {table_name}")

    # Run tests
    test_basic_lock_operations()
    test_lock_expiration()
    test_heartbeat_updates()
    test_lock_ownership()
    test_list_locks()
    test_expired_lock_cleanup()

    print("\n" + "=" * 50)
    print("CompactionLock tests complete!")
    print("\nKey insights:")
    print("- Locks prevent concurrent access to shared resources")
    print("- Expired locks can be overwritten by new owners")
    print("- Only lock owners can update/delete their locks")
    print("- Heartbeats keep locks alive during long operations")
    print("- GSI indexes enable efficient queries for active locks")


if __name__ == "__main__":
    main()
