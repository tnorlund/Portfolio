#!/usr/bin/env python3
"""
Test script for MerchantHarmonizerV3 (Agent-Based Harmonizer).

This script tests the new agent-based harmonizer that uses an LLM
to reason about receipt metadata consistency.

Usage:
    # Basic run (processes 5 groups by default)
    python scripts/test_harmonizer_v3.py

    # Process more groups
    python scripts/test_harmonizer_v3.py --limit 20

    # Apply fixes (dry run)
    python scripts/test_harmonizer_v3.py --apply-fixes

    # Actually apply fixes
    python scripts/test_harmonizer_v3.py --apply-fixes --no-dry-run

    # Save report to file
    python scripts/test_harmonizer_v3.py --output harmonizer_v3_report.json

    # Skip Google Places validation
    python scripts/test_harmonizer_v3.py --no-google
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


async def main():
    parser = argparse.ArgumentParser(
        description="Test the Agent-Based Harmonizer V3",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Maximum number of inconsistent groups to process (default: 5)",
    )
    parser.add_argument(
        "--apply-fixes",
        action="store_true",
        help="Apply harmonization fixes to DynamoDB",
    )
    parser.add_argument(
        "--no-dry-run",
        action="store_true",
        help="Actually apply fixes (not just dry run)",
    )
    parser.add_argument(
        "--min-confidence",
        type=float,
        default=0.5,
        help="Minimum confidence to apply fixes (default: 0.5)",
    )
    parser.add_argument(
        "--output",
        type=str,
        help="Save report to JSON file",
    )
    parser.add_argument(
        "--no-google",
        action="store_true",
        help="Skip Google Places validation",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
        logging.getLogger("receipt_agent").setLevel(logging.DEBUG)

    print("=" * 70)
    print("HARMONIZER V3 TEST (Agent-Based)")
    print("=" * 70)
    print(f"Started at: {datetime.now().isoformat()}")
    print()

    # Load environment variables from Pulumi (same as other test scripts)
    def setup_environment():
        """Load secrets and outputs from Pulumi and set environment variables."""
        from receipt_dynamo.data._pulumi import load_env, load_secrets

        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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

        ollama_base_url = secrets.get("portfolio:OLLAMA_BASE_URL", "https://ollama.com")
        os.environ["RECEIPT_AGENT_OLLAMA_BASE_URL"] = ollama_base_url

        ollama_model = secrets.get("portfolio:OLLAMA_MODEL", "gpt-oss:120b-cloud")
        os.environ["RECEIPT_AGENT_OLLAMA_MODEL"] = ollama_model

        # LangSmith tracing
        langsmith_key = secrets.get("portfolio:LANGCHAIN_API_KEY", "")
        if langsmith_key:
            os.environ["LANGCHAIN_API_KEY"] = langsmith_key
            os.environ["LANGCHAIN_TRACING_V2"] = "true"
            os.environ["LANGCHAIN_PROJECT"] = "receipt-agent-harmonizer-v3"

    setup_environment()
    print("✅ Environment variables loaded")

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
            print("⚠️  No Google Places API key found")
    else:
        print("⚠️  Google Places validation disabled")

    # Get settings for the agent
    from receipt_agent.config.settings import get_settings
    settings = get_settings()
    print("✅ Settings loaded")

    # Create harmonizer
    from receipt_agent.tools.harmonizer_v3 import MerchantHarmonizerV3
    harmonizer = MerchantHarmonizerV3(
        dynamo_client=dynamo,
        places_client=places,
        settings=settings,
    )
    print("✅ Harmonizer V3 created")
    print()

    # Load receipts
    print("📥 Loading receipts from DynamoDB...")
    total = harmonizer.load_all_receipts()
    print(f"   Loaded {total} receipts")
    print(f"   Place ID groups: {len(harmonizer._place_id_groups)}")
    print(f"   Without place_id: {len(harmonizer._no_place_id_receipts)}")

    inconsistent = harmonizer.get_inconsistent_groups()
    print(f"   Inconsistent groups: {len(inconsistent)}")
    print()

    # Show sample inconsistent groups
    if inconsistent:
        print("Sample inconsistent groups:")
        for group in inconsistent[:3]:
            names = set(r.merchant_name for r in group.receipts if r.merchant_name)
            print(f"  {group.place_id[:20]}... ({len(group.receipts)} receipts)")
            print(f"    Merchant names: {names}")
        print()

    # Run harmonization
    print(f"🤖 Running agent-based harmonization (limit: {args.limit})...")
    print("   This may take a while as the agent reasons about each group...")
    print()

    report = await harmonizer.harmonize_all(
        limit=args.limit,
        skip_consistent=True,
        min_group_size=1,
    )

    # Print summary
    print()
    harmonizer.print_summary(report)

    # Show detailed results
    results = report.get("results", [])
    successful = [r for r in results if "error" not in r]

    if successful and args.verbose:
        print("\n" + "=" * 70)
        print("DETAILED RESULTS")
        print("=" * 70)
        for r in successful:
            print(f"\nPlace ID: {r.get('place_id')}")
            print(f"  Canonical name: {r.get('canonical_merchant_name')}")
            print(f"  Canonical address: {r.get('canonical_address')}")
            print(f"  Canonical phone: {r.get('canonical_phone')}")
            print(f"  Confidence: {r.get('confidence', 0):.0%}")
            print(f"  Source: {r.get('source')}")
            print(f"  Reasoning: {r.get('reasoning', '')[:100]}...")
            print(f"  Updates needed: {r.get('receipts_needing_update')}/{r.get('total_receipts')}")

    # Apply fixes if requested
    if args.apply_fixes:
        print("\n" + "=" * 70)
        print("APPLYING FIXES")
        print("=" * 70)

        dry_run = not args.no_dry_run
        print(f"Dry run: {dry_run}")
        print(f"Min confidence: {args.min_confidence}")

        result = await harmonizer.apply_fixes(
            dry_run=dry_run,
            min_confidence=args.min_confidence,
        )

        print(f"\nResults:")
        print(f"  Processed: {result.total_processed}")
        print(f"  Updated: {result.total_updated}")
        print(f"  Skipped: {result.total_skipped}")
        print(f"  Failed: {result.total_failed}")

        if result.errors:
            print(f"\nErrors:")
            for e in result.errors[:5]:
                print(f"  ❌ {e}")

    # Save report if requested
    if args.output:
        # Convert to JSON-serializable format
        report_json = {
            "summary": report.get("summary"),
            "agent_results": report.get("agent_results"),
            "updates": report.get("updates"),
            "results": [
                {
                    "place_id": r.get("place_id"),
                    "canonical_merchant_name": r.get("canonical_merchant_name"),
                    "canonical_address": r.get("canonical_address"),
                    "canonical_phone": r.get("canonical_phone"),
                    "confidence": r.get("confidence"),
                    "source": r.get("source"),
                    "reasoning": r.get("reasoning"),
                    "total_receipts": r.get("total_receipts"),
                    "receipts_needing_update": r.get("receipts_needing_update"),
                    "updates": r.get("updates"),
                    "error": r.get("error"),
                }
                for r in report.get("results", [])
            ],
            "generated_at": datetime.now().isoformat(),
        }

        with open(args.output, "w") as f:
            json.dump(report_json, f, indent=2, default=str)

        print(f"\n✅ Report saved to {args.output}")

    print(f"\n✅ Completed at: {datetime.now().isoformat()}")


if __name__ == "__main__":
    asyncio.run(main())

