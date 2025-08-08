#!/usr/bin/env python3
"""Run batch canonicalization locally (based on batch_clean_merchants Lambda)."""

import os
import time
from typing import List, Dict, Any

from receipt_label.merchant_validation import (
    choose_canonical_metadata,
    cluster_by_metadata,
    collapse_canonical_aliases,
    merge_place_id_aliases_by_address,
    normalize_address,
    persist_alias_updates,
    update_items_with_canonical,
)
from receipt_dynamo import DynamoClient

client = DynamoClient(os.environ["DYNAMO_TABLE_NAME"])


def process_clusters_by_place_id(
    all_records: List[Any], records_by_place_id: Dict[str, List[Any]]
) -> int:
    """Process all clusters grouped by place_id."""
    
    # This function expects the specific data structure
    updated_count, total_groups = update_items_with_canonical(
        all_records, records_by_place_id
    )
    
    print(f"  ✅ Updated {updated_count} records across {total_groups} place_id groups")
    
    return updated_count


def main():
    """Main function to run batch canonicalization."""

    print("=" * 70)
    print("BATCH MERCHANT CANONICALIZATION")
    print("=" * 70)

    # Check DynamoDB table
    table_name = os.environ.get("DYNAMO_TABLE_NAME")
    if not table_name:
        print("❌ DYNAMO_TABLE_NAME environment variable not set")
        return

    print(f"📊 Using DynamoDB table: {table_name}\n")

    start_time = time.time()

    # Load all metadata
    print("Loading all receipt metadata from DynamoDB...")
    try:
        all_metadata, _ = client.list_receipt_metadatas()
        print(f"✅ Loaded {len(all_metadata)} metadata records\n")
    except Exception as e:
        print(f"❌ Error loading metadata: {e}")
        return

    # Filter to records with place_id
    metadata_with_place_id = [m for m in all_metadata if m.place_id]
    metadata_without_place_id = [m for m in all_metadata if not m.place_id]

    print(f"📊 Records with place_id: {len(metadata_with_place_id)}")
    print(f"📊 Records without place_id: {len(metadata_without_place_id)}\n")

    # Phase 1: Merge place_id aliases by address
    print("Phase 1: Merging place_id aliases by address...")
    alias_count = merge_place_id_aliases_by_address(metadata_with_place_id)
    if alias_count > 0:
        print(f"  Merged {alias_count} place_id aliases")
        # Reload metadata after alias updates
        all_metadata, _ = client.list_receipt_metadatas()
        metadata_with_place_id = [m for m in all_metadata if m.place_id]
    else:
        print("  No place_id aliases found")

    # Phase 2: Group by place_id for canonicalization
    print("\nPhase 2: Grouping by place_id...")
    records_by_place_id = {}
    for record in metadata_with_place_id:
        if record.place_id:
            if record.place_id not in records_by_place_id:
                records_by_place_id[record.place_id] = []
            records_by_place_id[record.place_id].append(record)
    
    print(f"  Found {len(records_by_place_id)} unique place_ids")
    
    # Phase 3: Process clusters by place_id
    print("\nPhase 3: Canonicalizing by place_id...")
    total_updated = process_clusters_by_place_id(all_metadata, records_by_place_id)

    # Phase 4: Collapse canonical aliases
    print("\nPhase 4: Collapsing canonical aliases...")
    canonical_alias_count = collapse_canonical_aliases(all_metadata)
    print(f"  Collapsed {canonical_alias_count} canonical aliases")

    # Summary
    elapsed = time.time() - start_time
    print("\n" + "=" * 70)
    print("CANONICALIZATION COMPLETE")
    print("=" * 70)
    print(f"⏱️  Total time: {elapsed:.2f} seconds")
    print(f"📊 Total records processed: {len(all_metadata)}")
    print(f"✅ Records updated: {total_updated}")
    print(f"🔄 Place IDs processed: {len(records_by_place_id)}")
    print(f"🔗 Canonical aliases collapsed: {canonical_alias_count}")

    # Show sample of canonicalized merchants
    print("\n📝 Sample of canonicalized merchants:")
    # Reload to see updates
    updated_metadata, _ = client.list_receipt_metadatas()
    canonical_merchants = {}
    for m in updated_metadata[:50]:  # Check first 50
        if m.canonical_merchant_name and m.canonical_place_id:
            key = f"{m.canonical_merchant_name}|{m.canonical_place_id}"
            if key not in canonical_merchants:
                canonical_merchants[key] = {
                    "name": m.canonical_merchant_name,
                    "place_id": m.canonical_place_id,
                    "address": m.canonical_address,
                    "count": 0,
                }
            canonical_merchants[key]["count"] += 1

    for merchant in list(canonical_merchants.values())[:10]:
        print(f"  - {merchant['name']}: {merchant['count']} receipts")
        print(f"    Place ID: {merchant['place_id'][:30]}...")


if __name__ == "__main__":
    main()
