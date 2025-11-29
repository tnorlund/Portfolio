#!/usr/bin/env python3
"""
Test script for ReceiptMetadataFinder (Find complete metadata for receipts).

This script finds ALL missing metadata for receipts:
- place_id (Google Place ID)
- merchant_name (business name)
- address (formatted address)
- phone_number (phone number)

Usage:
    # Analyze only (default)
    python scripts/test_receipt_metadata_finder.py

    # Save report to JSON
    python scripts/test_receipt_metadata_finder.py -o report.json

    # Dry run - show what would be updated
    python scripts/test_receipt_metadata_finder.py --apply --dry-run

    # Actually apply fixes
    python scripts/test_receipt_metadata_finder.py --apply

    # Apply with higher confidence threshold
    python scripts/test_receipt_metadata_finder.py --apply --min-confidence 80

Options:
    --limit N           Limit to N receipts (default: all)
    -o, --output        Output JSON file for results
    --apply             Apply fixes to DynamoDB (adds all found metadata)
    --dry-run           Show what would be updated without writing
    --min-confidence N  Minimum confidence to apply fix (default: 50)
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

# Add receipt_chroma to path if not installed
receipt_chroma_path = os.path.join(repo_root, "receipt_chroma")
if os.path.exists(receipt_chroma_path) and receipt_chroma_path not in sys.path:
    sys.path.insert(0, receipt_chroma_path)

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

    ollama_key = secrets.get("portfolio:OLLAMA_API_KEY")
    if ollama_key:
        os.environ["RECEIPT_AGENT_OLLAMA_API_KEY"] = ollama_key
        print("✅ Ollama API key loaded")
    else:
        print("⚠️  Ollama API key not found")

    # LangSmith tracing
    langsmith_key = secrets.get("portfolio:LANGCHAIN_API_KEY", "")
    if langsmith_key:
        os.environ["LANGCHAIN_API_KEY"] = langsmith_key
        os.environ["LANGCHAIN_TRACING_V2"] = "true"
        os.environ["LANGCHAIN_PROJECT"] = "receipt-metadata-finder"
        print("✅ LangSmith tracing enabled")
    else:
        print("⚠️  LangSmith API key not found - tracing disabled")

    print(f"📊 DynamoDB Table: {os.environ.get('RECEIPT_AGENT_DYNAMO_TABLE_NAME')}")
    return env, secrets


async def main():
    parser = argparse.ArgumentParser(
        description="Receipt Metadata Finder - Find complete metadata for receipts",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Analyze and report
    python scripts/test_receipt_metadata_finder.py -o report.json

    # Dry run - see what would change
    python scripts/test_receipt_metadata_finder.py --apply --dry-run

    # Apply fixes with default thresholds
    python scripts/test_receipt_metadata_finder.py --apply

    # Apply only high-confidence fixes
    python scripts/test_receipt_metadata_finder.py --apply --min-confidence 80
"""
    )
    parser.add_argument("--limit", type=int, help="Limit to N receipts")
    parser.add_argument("-o", "--output", help="Output JSON file for results")
    parser.add_argument("--apply", action="store_true", help="Apply fixes to DynamoDB")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be updated without writing")
    parser.add_argument("--min-confidence", type=float, default=50.0, help="Minimum confidence to apply fix (default: 50)")
    args = parser.parse_args()

    print("=" * 70)
    print("RECEIPT METADATA FINDER - Find Complete Metadata for Receipts")
    print("=" * 70)
    print()
    print("This tool finds ALL missing metadata for receipts:")
    print("- place_id (Google Place ID)")
    print("- merchant_name (business name)")
    print("- address (formatted address)")
    print("- phone_number (phone number)")
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

    # Create Places client (required)
    places_key = os.environ.get("RECEIPT_AGENT_GOOGLE_PLACES_API_KEY")
    if not places_key:
        print("❌ Google Places API key required")
        sys.exit(1)

    try:
        from receipt_places import PlacesClient, PlacesConfig
        places_config = PlacesConfig(
            api_key=places_key,
            table_name=os.environ.get("RECEIPT_AGENT_DYNAMO_TABLE_NAME"),
            aws_region=os.environ.get("RECEIPT_AGENT_AWS_REGION", "us-east-1"),
            cache_enabled=True,
        )
        places = PlacesClient(config=places_config)
        print("✅ Google Places client created (cache enabled)")
    except Exception as e:
        print(f"❌ Could not create Places client: {e}")
        sys.exit(1)

    # Setup ChromaDB and embedding function (required for agent mode)
    print("🤖 Agent-based mode enabled (requires ChromaDB)")
    try:
        from receipt_agent.clients.factory import create_chroma_client, create_embed_fn
        from receipt_agent.config.settings import get_settings

        settings = get_settings()

        # Try to get ChromaDB directories from environment
        lines_dir = os.environ.get("RECEIPT_AGENT_CHROMA_LINES_DIRECTORY")
        words_dir = os.environ.get("RECEIPT_AGENT_CHROMA_WORDS_DIRECTORY")

        if lines_dir and words_dir:
            chroma_client = create_chroma_client(
                mode="read",
                settings=settings,
            )
            if chroma_client:
                print(f"✅ ChromaDB client created (lines: {lines_dir}, words: {words_dir})")
            else:
                print("❌ ChromaDB client creation returned None")
                sys.exit(1)
        else:
            print("❌ ChromaDB directories not found. Agent mode requires ChromaDB.")
            print("   Set RECEIPT_AGENT_CHROMA_LINES_DIRECTORY and RECEIPT_AGENT_CHROMA_WORDS_DIRECTORY")
            sys.exit(1)

        # Setup embedding function
        embed_fn = create_embed_fn(settings=settings)
        print("✅ Embedding function created")
    except Exception as e:
        print(f"❌ Could not setup agent mode: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    # Create finder
    from receipt_agent.tools.receipt_metadata_finder import ReceiptMetadataFinder
    finder = ReceiptMetadataFinder(
        dynamo_client=dynamo,
        places_client=places,
        chroma_client=chroma_client,
        embed_fn=embed_fn,
        settings=settings,
    )
    print("✅ Receipt Metadata Finder created")
    print()

    # Load receipts with missing metadata
    print("📥 Loading receipts with missing metadata from DynamoDB...")
    total = finder.load_receipts_with_missing_metadata()
    print(f"   Found {total} receipts with missing metadata")
    print()

    if total == 0:
        print("✅ All receipts already have complete metadata!")
        return

    # Run metadata finding
    print("🤖 Finding metadata using agent-based reasoning...")
    print("   This may take a while as the agent reasons about each receipt...")
    print()
    report = await finder.find_all_metadata_agentic(limit=args.limit)

    # Print summary
    print()
    finder.print_summary(report)

    # Save to file
    if args.output:
        # Convert report to dict for JSON serialization
        report_dict = {
            "total_processed": report.total_processed,
            "total_found_all": report.total_found_all,
            "total_found_partial": report.total_found_partial,
            "total_not_found": report.total_not_found,
            "total_errors": report.total_errors,
            "field_counts": dict(report.field_counts),
            "matches": [
                {
                    "image_id": m.receipt.image_id,
                    "receipt_id": m.receipt.receipt_id,
                    "place_id": m.place_id,
                    "merchant_name": m.merchant_name,
                    "address": m.address,
                    "phone_number": m.phone_number,
                    "confidence": m.confidence,
                    "field_confidence": m.field_confidence,
                    "sources": m.sources,
                    "fields_found": m.fields_found,
                    "found": m.found,
                    "error": m.error,
                    "reasoning": m.reasoning,
                }
                for m in report.matches
            ],
        }
        with open(args.output, "w") as f:
            json.dump(report_dict, f, indent=2)
        print()
        print(f"💾 Saved report to {args.output}")

    # Apply fixes if requested
    if args.apply:
        print()
        print("=" * 70)
        if args.dry_run:
            print("DRY RUN - Showing what would be updated")
        else:
            print("APPLYING FIXES - Adding metadata to DynamoDB")
        print("=" * 70)
        print(f"Threshold: min_confidence={args.min_confidence}")
        print("Will update any missing fields with found values")
        print("Will create metadata records if they don't exist")
        print()

        result = await finder.apply_fixes(
            dry_run=args.dry_run,
            min_confidence=args.min_confidence,
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


