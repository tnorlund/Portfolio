#!/usr/bin/env python3
"""
Clean up compaction locks using the new batch delete method.
"""

import sys
sys.path.append('../receipt_dynamo')

from receipt_dynamo import DynamoClient

def cleanup_all_locks():
    """List and delete all compaction locks using the new batch method."""
    client = DynamoClient("ReceiptsTable-dc5be22")
    
    print("=== Compaction Lock Batch Cleanup ===\n")
    
    # List all current locks
    print("Listing all compaction locks...")
    locks, pagination_token = client.list_compaction_locks()
    
    if not locks:
        print("✓ No locks found. System is already clean.")
        return
    
    print(f"Found {len(locks)} locks")
    
    # Show a few examples
    if len(locks) <= 5:
        for lock in locks:
            print(f"  - {lock}")
    else:
        for i, lock in enumerate(locks[:3]):
            print(f"  - {lock}")
        print(f"  ... and {len(locks) - 3} more")
    
    # Use the new batch delete method
    print(f"\nDeleting all {len(locks)} locks using batch method...")
    try:
        client.delete_compaction_locks(locks)
        print("✓ Successfully deleted all locks in batch!")
    except Exception as e:
        print(f"✗ Batch deletion failed: {e}")
        return
    
    # Final verification
    print("\n--- Final verification ---")
    remaining_locks, _ = client.list_compaction_locks()
    
    if not remaining_locks:
        print("✓ All locks successfully removed!")
    else:
        print(f"⚠ {len(remaining_locks)} locks still remain:")
        for lock in remaining_locks:
            print(f"  - {lock}")

if __name__ == "__main__":
    cleanup_all_locks()