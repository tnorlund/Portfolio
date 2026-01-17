#!/usr/bin/env python
"""Migration script to add GSI4 keys to existing receipt-related records.

This script updates existing DynamoDB records to include GSI4 attributes
(GSI4PK, GSI4SK) which enable efficient single-query retrieval of receipt
details.

Entities updated:
- Receipt
- ReceiptLine
- ReceiptWord
- ReceiptWordLabel
- ReceiptPlace

Usage:
    python scripts/migrate_gsi4.py --env dev [--dry-run]
    python scripts/migrate_gsi4.py --table-name <table_name> [--dry-run]

Options:
    --env           Pulumi environment (dev/prod) to load table name from
    --table-name    DynamoDB table name (overrides --env)
    --dry-run       Print what would be updated without making changes
    --batch-size    Number of records to update per batch (default: 25)
"""

import argparse
import sys
from typing import Any

from receipt_dynamo.data._pulumi import load_env
from receipt_dynamo.data.dynamo_client import DynamoClient


def migrate_entity(
    client: DynamoClient,
    entity_name: str,
    list_func: callable,
    update_func: callable,
    batch_size: int = 25,
    dry_run: bool = False,
) -> dict[str, int]:
    """Migrate a single entity type by listing and re-updating all records.

    Args:
        client: DynamoDB client
        entity_name: Name of the entity for logging
        list_func: Function to list entities (returns tuple of list, pagination_key)
        update_func: Function to update entities (takes list of entities)
        batch_size: Number of records to update per batch
        dry_run: If True, don't actually update records

    Returns:
        Dict with 'total' and 'updated' counts
    """
    print(f"\n{'='*60}")
    print(f"Migrating {entity_name}...")
    print(f"{'='*60}")

    total_count = 0
    updated_count = 0
    last_key: dict[str, Any] | None = None

    while True:
        # List batch of entities
        entities, last_key = list_func(
            limit=batch_size, last_evaluated_key=last_key
        )

        if not entities:
            break

        total_count += len(entities)

        if dry_run:
            print(f"  [DRY RUN] Would update {len(entities)} {entity_name}(s)")
        else:
            # Update entities - this will write GSI4 keys via to_item()
            update_func(entities)
            updated_count += len(entities)
            print(f"  Updated {updated_count} {entity_name}(s)...")

        if last_key is None:
            break

    print(f"  Total {entity_name}(s) processed: {total_count}")
    if not dry_run:
        print(f"  Total {entity_name}(s) updated: {updated_count}")

    return {"total": total_count, "updated": updated_count}


def main():
    parser = argparse.ArgumentParser(
        description="Migrate DynamoDB records to add GSI4 keys"
    )
    parser.add_argument(
        "--env",
        default="dev",
        choices=["dev", "prod"],
        help="Pulumi environment to load table name from (default: dev)",
    )
    parser.add_argument(
        "--table-name",
        help="DynamoDB table name (overrides --env)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be updated without making changes",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=25,
        help="Number of records to update per batch (default: 25)",
    )
    args = parser.parse_args()

    # Get table name from args or Pulumi environment
    if args.table_name:
        table_name = args.table_name
    else:
        print(f"Loading table name from Pulumi environment: {args.env}")
        env_config = load_env(args.env)
        table_name = env_config.get("dynamodb_table_name")
        if not table_name:
            print(
                f"ERROR: Could not load dynamodb_table_name from "
                f"Pulumi stack '{args.env}'"
            )
            print("Use --table-name to specify the table name directly.")
            return 1

    print(f"GSI4 Migration Script")
    print(f"Environment: {args.env}")
    print(f"Table: {table_name}")
    print(f"Dry run: {args.dry_run}")
    print(f"Batch size: {args.batch_size}")

    # Initialize client
    client = DynamoClient(table_name)

    # Track results
    results: dict[str, dict[str, int]] = {}

    # Migrate each entity type
    entity_configs = [
        ("Receipt", client.list_receipts, client.update_receipts),
        (
            "ReceiptLine",
            client.list_receipt_lines,
            client.update_receipt_lines,
        ),
        (
            "ReceiptWord",
            client.list_receipt_words,
            client.update_receipt_words,
        ),
        (
            "ReceiptWordLabel",
            client.list_receipt_word_labels,
            client.update_receipt_word_labels,
        ),
        (
            "ReceiptPlace",
            client.list_receipt_places,
            client.update_receipt_places,
        ),
    ]

    for entity_name, list_func, update_func in entity_configs:
        try:
            results[entity_name] = migrate_entity(
                client=client,
                entity_name=entity_name,
                list_func=list_func,
                update_func=update_func,
                batch_size=args.batch_size,
                dry_run=args.dry_run,
            )
        except Exception as e:
            print(f"  ERROR migrating {entity_name}: {e}")
            results[entity_name] = {"total": 0, "updated": 0, "error": str(e)}

    # Print summary
    print(f"\n{'='*60}")
    print("Migration Summary")
    print(f"{'='*60}")

    total_all = 0
    updated_all = 0
    for entity_name, counts in results.items():
        total_all += counts.get("total", 0)
        updated_all += counts.get("updated", 0)
        status = (
            f"ERROR: {counts['error']}"
            if "error" in counts
            else f"{counts['total']} total, {counts['updated']} updated"
        )
        print(f"  {entity_name}: {status}")

    print(f"\nTotal records: {total_all}")
    if not args.dry_run:
        print(f"Total updated: {updated_all}")

    return 0 if all("error" not in r for r in results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
