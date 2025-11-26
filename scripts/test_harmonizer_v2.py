#!/usr/bin/env python3
"""
Test script for MerchantHarmonizerV2 (Place ID based grouping).

This script harmonizes merchant metadata by grouping receipts by place_id,
identifying inconsistencies, and optionally applying fixes to DynamoDB.

Usage:
    # Analyze only (default)
    python scripts/test_harmonizer_v2.py

    # Save report to JSON
    python scripts/test_harmonizer_v2.py -o report.json

    # Dry run - show what would be updated
    python scripts/test_harmonizer_v2.py --apply --dry-run

    # Actually apply fixes
    python scripts/test_harmonizer_v2.py --apply

    # Apply with higher confidence threshold
    python scripts/test_harmonizer_v2.py --apply --min-confidence 80 --min-group-size 3

Options:
    --limit N           Limit to N place_id groups (default: all)
    -o, --output        Output JSON file for results
    --no-google         Skip Google Places validation
    --apply             Apply fixes to DynamoDB (updates canonical fields)
    --dry-run           Show what would be updated without writing
    --min-confidence N  Minimum confidence to apply fix (default: 50)
    --min-group-size N  Minimum group size to apply fix (default: 2)
"""

import argparse
import asyncio
import json
import logging
import os
import sys

# Add repo root to path
repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, repo_root)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def setup_environment():
    """Load secrets and outputs from Pulumi and set environment variables."""
    from receipt_dynamo.data._pulumi import load_env, load_secrets

    infra_dir = os.path.join(repo_root, "infra")
    env = load_env("dev", working_dir=infra_dir)
    secrets = load_secrets("dev", working_dir=infra_dir)

    # Set environment variables
    os.environ["RECEIPT_AGENT_DYNAMO_TABLE_NAME"] = env.get(
        "dynamodb_table_name", "receipts"
    )
    os.environ["RECEIPT_AGENT_AWS_REGION"] = secrets.get(
        "portfolio:aws-region", "us-east-1"
    )

    # API Keys
    google_places_key = secrets.get("portfolio:GOOGLE_PLACES_API_KEY")
    if google_places_key:
        os.environ["RECEIPT_AGENT_GOOGLE_PLACES_API_KEY"] = google_places_key
        print("✅ Google Places API key loaded")
    else:
        print("⚠️  Google Places API key not found")

    # LangSmith tracing (for consistency, though harmonizer is deterministic)
    langsmith_key = secrets.get("portfolio:LANGCHAIN_API_KEY", "")
    if langsmith_key:
        os.environ["LANGCHAIN_API_KEY"] = langsmith_key
        os.environ["LANGCHAIN_TRACING_V2"] = "true"
        os.environ["LANGCHAIN_PROJECT"] = "harmonizer-v2"
        print("✅ LangSmith tracing enabled")
    else:
        print("⚠️  LangSmith API key not found - tracing disabled")

    print(f"📊 DynamoDB Table: {os.environ.get('RECEIPT_AGENT_DYNAMO_TABLE_NAME')}")
    return env, secrets


async def main():
    parser = argparse.ArgumentParser(
        description="Merchant Harmonizer V2 (Place ID based)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Analyze and report
    python scripts/test_harmonizer_v2.py -o report.json

    # Dry run - see what would change
    python scripts/test_harmonizer_v2.py --apply --dry-run

    # Apply fixes with default thresholds
    python scripts/test_harmonizer_v2.py --apply

    # Apply only high-confidence fixes
    python scripts/test_harmonizer_v2.py --apply --min-confidence 80 --min-group-size 5
"""
    )
    parser.add_argument("--limit", type=int, help="Limit to N place_id groups")
    parser.add_argument("-o", "--output", help="Output JSON file for results")
    parser.add_argument("--no-google", action="store_true", help="Skip Google Places validation")
    parser.add_argument("--apply", action="store_true", help="Apply fixes to DynamoDB")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be updated without writing")
    parser.add_argument("--min-confidence", type=float, default=50.0, help="Minimum confidence to apply fix (default: 50)")
    parser.add_argument("--min-group-size", type=int, default=2, help="Minimum group size to apply fix (default: 2)")
    parser.add_argument("--export", help="Export all metadata to NDJSON before applying (backup)")
    args = parser.parse_args()

    print("=" * 70)
    print("MERCHANT HARMONIZER V2 - Place ID Based Grouping")
    print("=" * 70)
    print()
    print("This tool ensures metadata consistency across receipts sharing")
    print("the same Google Place ID. It identifies inconsistencies and can")
    print("apply fixes by updating the canonical fields in DynamoDB.")
    print()

    # Setup
    env, secrets = setup_environment()

    # Create DynamoDB client
    from receipt_dynamo import DynamoClient
    dynamo = DynamoClient(
        table_name=os.environ.get("RECEIPT_AGENT_DYNAMO_TABLE_NAME"),
        region=os.environ.get("RECEIPT_AGENT_AWS_REGION", "us-east-1"),
    )
    print("✅ DynamoDB client created")

    # Create Places client (optional)
    places = None
    if not args.no_google:
        places_key = os.environ.get("RECEIPT_AGENT_GOOGLE_PLACES_API_KEY")
        if places_key:
            try:
                from receipt_places import PlacesClient, PlacesConfig
                places_config = PlacesConfig(
                    api_key=places_key,
                    cache_enabled=True,
                    table_name=os.environ.get("RECEIPT_AGENT_DYNAMO_TABLE_NAME"),
                    aws_region=os.environ.get("RECEIPT_AGENT_AWS_REGION", "us-east-1"),
                )
                places = PlacesClient(config=places_config)
                print("✅ Google Places client created (cache enabled)")
            except Exception as e:
                print(f"⚠️  Could not create Places client: {e}")
    else:
        print("⚠️  Google Places validation disabled")

    # Create harmonizer
    from receipt_agent.tools.harmonizer_v2 import MerchantHarmonizerV2
    harmonizer = MerchantHarmonizerV2(
        dynamo_client=dynamo,
        places_client=places,
    )
    print("✅ Harmonizer V2 created")
    print()

    # Load and process
    print("📥 Loading receipts from DynamoDB...")
    total = harmonizer.load_all_receipts()
    print(f"   Loaded {total} receipts")
    print(f"   Place ID groups: {len(harmonizer._place_id_groups)}")
    print(f"   Without place_id: {len(harmonizer._no_place_id_receipts)}")
    print()

    # Run harmonization
    print("🔄 Running harmonization...")
    report = await harmonizer.harmonize_all(
        validate_google=not args.no_google,
        limit=args.limit,
    )

    # Print summary
    print()
    harmonizer.print_summary(report)

    # Save to file
    if args.output:
        with open(args.output, "w") as f:
            json.dump(report, f, indent=2)
        print()
        print(f"💾 Saved report to {args.output}")

    # Apply fixes if requested
    if args.apply:
        print()
        print("=" * 70)
        if args.dry_run:
            print("DRY RUN - Showing what would be updated")
        else:
            print("APPLYING FIXES - Updating main fields in DynamoDB")
        print("=" * 70)
        print(f"Thresholds: min_confidence={args.min_confidence}, min_group_size={args.min_group_size}")

        # Generate export path if not provided but not dry run
        export_path = args.export
        if not args.dry_run and not export_path:
            from datetime import datetime
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            export_path = f"receipt_metadata_backup_{timestamp}.ndjson"
            print(f"📦 Backup will be saved to: {export_path}")

        print()

        result = await harmonizer.apply_fixes(
            dry_run=args.dry_run,
            min_confidence=args.min_confidence,
            min_group_size=args.min_group_size,
            export_path=export_path if not args.dry_run else None,
        )

        print()
        print("Update Summary:")
        print(f"  📊 Processed: {result.total_processed}")
        print(f"  ✅ Updated: {result.total_updated}")
        print(f"  ⏭️  Skipped: {result.total_skipped}")
        print(f"  ❌ Failed: {result.total_failed}")

        if result.errors:
            print()
            print("Errors:")
            for err in result.errors[:10]:
                print(f"  - {err}")
            if len(result.errors) > 10:
                print(f"  ... and {len(result.errors) - 10} more")


if __name__ == "__main__":
    asyncio.run(main())
