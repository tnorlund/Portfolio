#!/usr/bin/env python3
"""
Test script for PlaceIdFinder (Find Google Place IDs for receipts).

This script finds Google Place IDs for receipt metadata that don't have place_ids yet.
This complements harmonizer_v2 which works on receipts that already have place_ids.

Usage:
    # Analyze only (default)
    python scripts/test_place_id_finder.py

    # Save report to JSON
    python scripts/test_place_id_finder.py -o report.json

    # Dry run - show what would be updated
    python scripts/test_place_id_finder.py --apply --dry-run

    # Actually apply fixes
    python scripts/test_place_id_finder.py --apply

    # Apply with higher confidence threshold
    python scripts/test_place_id_finder.py --apply --min-confidence 80

    # Also update other fields from Google Places
    python scripts/test_place_id_finder.py --apply --update-other-fields

Options:
    --limit N           Limit to N receipts (default: all)
    -o, --output        Output JSON file for results
    --apply             Apply fixes to DynamoDB (adds place_ids)
    --dry-run           Show what would be updated without writing
    --min-confidence N  Minimum confidence to apply fix (default: 50)
    --update-other-fields  Also update merchant_name, address, phone from Google Places (default: True)
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

    openai_key = secrets.get("portfolio:OPENAI_API_KEY")
    if openai_key:
        os.environ["RECEIPT_AGENT_OPENAI_API_KEY"] = openai_key
        print("✅ OpenAI API key loaded")
    else:
        print("⚠️  OpenAI API key not found")

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
        os.environ["LANGCHAIN_PROJECT"] = "place-id-finder"
        print("✅ LangSmith tracing enabled")
    else:
        print("⚠️  LangSmith API key not found - tracing disabled")

    print(f"📊 DynamoDB Table: {os.environ.get('RECEIPT_AGENT_DYNAMO_TABLE_NAME')}")
    return env, secrets


async def main():
    parser = argparse.ArgumentParser(
        description="Place ID Finder - Find Google Place IDs for receipts",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Analyze and report
    python scripts/test_place_id_finder.py -o report.json

    # Dry run - see what would change
    python scripts/test_place_id_finder.py --apply --dry-run

    # Apply fixes with default thresholds
    python scripts/test_place_id_finder.py --apply

    # Apply only high-confidence fixes
    python scripts/test_place_id_finder.py --apply --min-confidence 80

    # Also update other fields from Google Places
    python scripts/test_place_id_finder.py --apply --update-other-fields
"""
    )
    parser.add_argument("--limit", type=int, help="Limit to N receipts")
    parser.add_argument("-o", "--output", help="Output JSON file for results")
    parser.add_argument("--apply", action="store_true", help="Apply fixes to DynamoDB")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be updated without writing")
    parser.add_argument("--min-confidence", type=float, default=50.0, help="Minimum confidence to apply fix (default: 50)")
    parser.add_argument("--update-other-fields", action="store_true", help="Also update merchant_name, address, phone from Google Places")
    parser.add_argument("--agent", action="store_true", help="Use agent-based reasoning (recommended, requires ChromaDB)")
    args = parser.parse_args()

    print("=" * 70)
    print("PLACE ID FINDER - Find Google Place IDs for Receipts")
    print("=" * 70)
    print()
    print("This tool finds Google Place IDs for receipt metadata that don't")
    print("have place_ids yet. It uses Google Places API to search by phone,")
    print("address, or merchant name.")
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
            cache_enabled=True,  # Enable cache to reduce API costs
        )
        places = PlacesClient(config=places_config)
        print("✅ Google Places client created (cache enabled)")
    except Exception as e:
        print(f"❌ Could not create Places client: {e}")
        sys.exit(1)

    # Setup ChromaDB and embedding function if using agent mode
    chroma_client = None
    embed_fn = None
    settings = None

    if args.agent:
        print("🤖 Agent-based mode enabled")
        # Setup ChromaDB
        try:
            from receipt_agent.clients.factory import create_chroma_client, create_embed_fn
            from receipt_agent.config.settings import get_settings

            settings = get_settings()

            # Try to get ChromaDB directories from environment
            lines_dir = os.environ.get("RECEIPT_AGENT_CHROMA_LINES_DIRECTORY")
            words_dir = os.environ.get("RECEIPT_AGENT_CHROMA_WORDS_DIRECTORY")

            if lines_dir and words_dir:
                # Use factory function which handles DualChromaClient creation
                chroma_client = create_chroma_client(
                    mode="read",
                    settings=settings,
                )
                if chroma_client:
                    print(f"✅ ChromaDB client created (lines: {lines_dir}, words: {words_dir})")
                else:
                    print("⚠️  ChromaDB client creation returned None")
                    args.agent = False
            else:
                print("⚠️  ChromaDB directories not found. Agent mode requires ChromaDB.")
                print("   Set RECEIPT_AGENT_CHROMA_LINES_DIRECTORY and RECEIPT_AGENT_CHROMA_WORDS_DIRECTORY")
                print("   Falling back to simple search mode...")
                args.agent = False

            # Setup embedding function
            if args.agent:
                from receipt_agent.clients.factory import create_embed_fn
                embed_fn = create_embed_fn(settings=settings)
                print("✅ Embedding function created")
        except Exception as e:
            print(f"⚠️  Could not setup agent mode: {e}")
            import traceback
            traceback.print_exc()
            print("   Falling back to simple search mode...")
            args.agent = False

    # Create finder
    from receipt_agent.tools.place_id_finder import PlaceIdFinder
    finder = PlaceIdFinder(
        dynamo_client=dynamo,
        places_client=places,
        chroma_client=chroma_client,
        embed_fn=embed_fn,
        settings=settings,
    )
    print("✅ Place ID Finder created")
    print()

    # Load receipts without place_id
    print("📥 Loading receipts without place_id from DynamoDB...")
    total = finder.load_receipts_without_place_id()
    print(f"   Found {total} receipts without place_id")
    print()

    if total == 0:
        print("✅ All receipts already have place_ids!")
        return

    # Run place ID finding
    if args.agent:
        print("🤖 Searching Google Places using agent-based reasoning...")
        report = await finder.find_all_place_ids_agentic(limit=args.limit)
    else:
        print("🔍 Searching Google Places for place_ids (simple search)...")
        report = await finder.find_all_place_ids(limit=args.limit)

    # Print summary
    print()
    finder.print_summary(report)

    # Save to file
    if args.output:
        # Convert report to dict for JSON serialization
        report_dict = {
            "total_processed": report.total_processed,
            "total_found": report.total_found,
            "total_not_found": report.total_not_found,
            "total_errors": report.total_errors,
            "search_method_counts": dict(report.search_method_counts),
            "matches": [
                {
                    "image_id": m.receipt.image_id,
                    "receipt_id": m.receipt.receipt_id,
                    "merchant_name": m.receipt.merchant_name,
                    "address": m.receipt.address,
                    "phone": m.receipt.phone,
                    "place_id": m.place_id,
                    "place_name": m.place_name,
                    "place_address": m.place_address,
                    "place_phone": m.place_phone,
                    "search_method": m.search_method,
                    "confidence": m.confidence,
                    "found": m.found,
                    "error": m.error,
                    "needs_review": m.needs_review,
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
            print("APPLYING FIXES - Adding place_ids to DynamoDB")
        print("=" * 70)
        print(f"Threshold: min_confidence={args.min_confidence}")
        print("Always updating: place_id, merchant_name, address, phone_number from Google Places")
        print("Will create metadata records if they don't exist")
        print()

        result = await finder.apply_fixes(
            dry_run=args.dry_run,
            min_confidence=args.min_confidence,
            update_other_fields=args.update_other_fields,
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

