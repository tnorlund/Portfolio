#!/usr/bin/env python3
"""Debug script to check label count cache entries in DynamoDB."""

import os
import sys
import time
from datetime import datetime

# Add parent directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from receipt_dynamo import DynamoClient


def main():
    """Check label count cache entries."""
    # Get table name from Pulumi stack
    table_name = "ReceiptsTable-d7ff76a"  # From pulumi stack output
    
    print(f"Checking cache entries in table: {table_name}")
    
    # Initialize client
    client = DynamoClient(table_name)
    
    # List all cache entries
    print("\nFetching all label count cache entries...")
    cache_entries, _ = client.list_label_count_caches()
    
    if not cache_entries:
        print("❌ No cache entries found!")
        print("\nPossible reasons:")
        print("1. The cache updater Lambda hasn't run yet")
        print("2. The Lambda is failing to execute")
        print("3. The Lambda doesn't have proper permissions")
        print("\nCheck CloudWatch logs for 'label_count_cache_updater_lambda'")
        return
    
    print(f"\n✅ Found {len(cache_entries)} cache entries")
    
    current_time = int(time.time())
    
    # Check each entry
    for entry in cache_entries:
        print(f"\nLabel: {entry.label}")
        print(f"  Valid: {entry.valid_count}")
        print(f"  Invalid: {entry.invalid_count}")
        print(f"  Pending: {entry.pending_count}")
        print(f"  Needs Review: {entry.needs_review_count}")
        print(f"  None: {entry.none_count}")
        print(f"  Last Updated: {entry.last_updated}")
        
        # Check TTL
        if entry.time_to_live:
            if entry.time_to_live > current_time:
                minutes_left = (entry.time_to_live - current_time) / 60
                print(f"  TTL: Valid (expires in {minutes_left:.1f} minutes)")
            else:
                minutes_ago = (current_time - entry.time_to_live) / 60
                print(f"  TTL: EXPIRED ({minutes_ago:.1f} minutes ago)")
        else:
            print("  TTL: None")
            
        # Check age
        last_updated = datetime.fromisoformat(entry.last_updated)
        age_minutes = (datetime.now() - last_updated).total_seconds() / 60
        print(f"  Age: {age_minutes:.1f} minutes")


if __name__ == "__main__":
    main()