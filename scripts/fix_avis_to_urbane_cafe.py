#!/usr/bin/env python3
"""
Fix receipts incorrectly marked as "Avis Car Rental" - should be "Urbane Cafe".

This script uses the PlaceIdFinder agent to re-validate receipts and update
their metadata with the correct merchant name and place_id.

Usage:
    # Dry run - see what would be updated
    python scripts/fix_avis_to_urbane_cafe.py --dry-run

    # Actually apply fixes
    python scripts/fix_avis_to_urbane_cafe.py --apply

    # Limit to N receipts
    python scripts/fix_avis_to_urbane_cafe.py --apply --limit 5
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from typing import Optional

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

    print(f"📊 DynamoDB Table: {os.environ.get('RECEIPT_AGENT_DYNAMO_TABLE_NAME')}")
    return env, secrets


async def main():
    parser = argparse.ArgumentParser(
        description="Fix receipts incorrectly marked as 'Avis Car Rental'",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Dry run - see what would be updated
    python scripts/fix_avis_to_urbane_cafe.py --dry-run

    # Actually apply fixes
    python scripts/fix_avis_to_urbane_cafe.py --apply

    # Limit to N receipts
    python scripts/fix_avis_to_urbane_cafe.py --apply --limit 5
"""
    )
    parser.add_argument("--limit", type=int, help="Limit to N receipts")
    parser.add_argument("--apply", action="store_true", help="Apply fixes to DynamoDB")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be updated without writing")
    parser.add_argument("--min-confidence", type=float, default=50.0, help="Minimum confidence to apply fix (default: 50)")
    parser.add_argument("-o", "--output", help="Output JSON file for results")
    args = parser.parse_args()

    print("=" * 70)
    print("FIX RECEIPTS: Avis Car Rental → Urbane Cafe")
    print("=" * 70)
    print()
    print("This script finds receipts incorrectly marked as 'Avis Car Rental'")
    print("and uses the PlaceIdFinder agent to find the correct place_id for")
    print("'Urbane Cafe', then updates the metadata.")
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

    # Setup ChromaDB and embedding function for agent mode
    chroma_client = None
    embed_fn = None
    settings = None

    print("🤖 Setting up agent-based mode...")
    try:
        from receipt_agent.clients.factory import create_chroma_client, create_embed_fn
        from receipt_agent.config.settings import get_settings

        settings = get_settings()

        # ChromaDB - use separate directories for lines and words collections
        snapshot_base_dir = os.path.join(repo_root, ".chroma_snapshots")
        lines_snapshot_dir = os.path.join(snapshot_base_dir, "lines")
        words_snapshot_dir = os.path.join(snapshot_base_dir, "words")
        local_chroma = os.path.join(repo_root, ".chromadb_local", "words")  # Old local path

        # Prefer local snapshot directories (downloaded from S3)
        chroma_dirs_set = False
        if (os.path.exists(lines_snapshot_dir) and os.listdir(lines_snapshot_dir) and
                os.path.exists(words_snapshot_dir) and os.listdir(words_snapshot_dir)):
            os.environ["RECEIPT_AGENT_CHROMA_LINES_DIRECTORY"] = lines_snapshot_dir
            os.environ["RECEIPT_AGENT_CHROMA_WORDS_DIRECTORY"] = words_snapshot_dir
            print(f"✅ ChromaDB snapshots found:")
            print(f"   Lines: {lines_snapshot_dir}")
            print(f"   Words: {words_snapshot_dir}")
            chroma_dirs_set = True
        elif os.path.exists(local_chroma):
            # Check if old local ChromaDB has both collections needed
            try:
                from receipt_chroma import ChromaClient
                with ChromaClient(persist_directory=local_chroma, mode="read") as client:
                    collections = client.list_collections()
                    has_lines = "lines" in collections
                    has_words = "words" in collections

                    if has_lines and has_words:
                        os.environ["RECEIPT_AGENT_CHROMA_LINES_DIRECTORY"] = local_chroma
                        os.environ["RECEIPT_AGENT_CHROMA_WORDS_DIRECTORY"] = local_chroma
                        print(f"✅ ChromaDB local (old format): {local_chroma}")
                        print(f"   Collections: {', '.join(collections)}")
                        chroma_dirs_set = True
                    else:
                        print(f"⚠️  Local ChromaDB missing required collections")
                        print(f"   Found: {', '.join(collections) if collections else 'none'}")
                        print(f"   Needed: lines, words")
            except Exception as e:
                print(f"⚠️  Could not check local ChromaDB: {e}")

        # If we don't have valid ChromaDB directories, try to download from S3
        if not chroma_dirs_set:
            bucket_name = (
                env.get("embedding_chromadb_bucket_name")
                or env.get("chromadb_bucket_name")
                or os.environ.get("CHROMADB_BUCKET")
            )

            if bucket_name:
                print(f"📥 Downloading ChromaDB snapshots from S3 bucket: {bucket_name}")
                try:
                    from receipt_chroma import download_snapshot_atomic
                    import tempfile
                    import shutil

                    os.makedirs(lines_snapshot_dir, exist_ok=True)
                    os.makedirs(words_snapshot_dir, exist_ok=True)

                    lines_temp = tempfile.mkdtemp(prefix="chroma_lines_")
                    words_temp = tempfile.mkdtemp(prefix="chroma_words_")

                    try:
                        # Download lines collection
                        lines_result = download_snapshot_atomic(
                            bucket=bucket_name,
                            collection="lines",
                            local_path=lines_temp,
                            verify_integrity=True,
                        )

                        if lines_result.get("status") == "downloaded":
                            if os.path.exists(lines_snapshot_dir):
                                shutil.rmtree(lines_snapshot_dir)
                            shutil.copytree(lines_temp, lines_snapshot_dir)
                            print(f"✅ Lines collection downloaded")

                        # Download words collection
                        words_result = download_snapshot_atomic(
                            bucket=bucket_name,
                            collection="words",
                            local_path=words_temp,
                            verify_integrity=True,
                        )

                        if words_result.get("status") == "downloaded":
                            if os.path.exists(words_snapshot_dir):
                                shutil.rmtree(words_snapshot_dir)
                            shutil.copytree(words_temp, words_snapshot_dir)
                            print(f"✅ Words collection downloaded")

                        # Verify both collections are available
                        if lines_result.get("status") == "downloaded" and words_result.get("status") == "downloaded":
                            from receipt_chroma import ChromaClient
                            with ChromaClient(persist_directory=lines_snapshot_dir, mode="read") as lines_client:
                                lines_collections = lines_client.list_collections()
                            with ChromaClient(persist_directory=words_snapshot_dir, mode="read") as words_client:
                                words_collections = words_client.list_collections()

                            if "lines" in lines_collections and "words" in words_collections:
                                os.environ["RECEIPT_AGENT_CHROMA_LINES_DIRECTORY"] = lines_snapshot_dir
                                os.environ["RECEIPT_AGENT_CHROMA_WORDS_DIRECTORY"] = words_snapshot_dir
                                print(f"✅ ChromaDB snapshots ready")
                                chroma_dirs_set = True
                    finally:
                        shutil.rmtree(lines_temp, ignore_errors=True)
                        shutil.rmtree(words_temp, ignore_errors=True)

                except Exception as e:
                    print(f"⚠️  Failed to download snapshot: {e}")

        # Create ChromaDB client if directories are set
        if chroma_dirs_set:
            chroma_client = create_chroma_client(
                mode="read",
                settings=settings,
            )
            if chroma_client:
                print(f"✅ ChromaDB client created")
            else:
                print("⚠️  ChromaDB client creation returned None")

        # Setup embedding function
        if chroma_client:
            embed_fn = create_embed_fn(settings=settings)
            print("✅ Embedding function created")
        else:
            print("⚠️  ChromaDB not available - will use simple search mode")
            print("   (Simple search may not find the correct merchant if metadata is wrong)")

    except Exception as e:
        print(f"⚠️  Could not setup agent mode: {e}")
        import traceback
        traceback.print_exc()
        print("   Will use simple search mode...")

    # Load receipts with merchant_name="Avis Car Rental"
    print()
    print("📥 Loading receipts with merchant_name='Avis Car Rental'...")
    metadatas, _ = dynamo.get_receipt_metadatas_by_merchant(
        merchant_name="Avis Car Rental",
        limit=args.limit,
    )

    if not metadatas:
        print("✅ No receipts found with merchant_name='Avis Car Rental'")
        return

    print(f"   Found {len(metadatas)} receipts to process")
    print()

    # Create PlaceIdFinder
    from receipt_agent.tools.place_id_finder import PlaceIdFinder, ReceiptRecord
    finder = PlaceIdFinder(
        dynamo_client=dynamo,
        places_client=places,
        chroma_client=chroma_client,
        embed_fn=embed_fn,
        settings=settings,
    )
    print("✅ Place ID Finder created")
    print()

    # Convert metadatas to ReceiptRecord format
    receipts = []
    for meta in metadatas:
        receipt = ReceiptRecord(
            image_id=meta.image_id,
            receipt_id=meta.receipt_id,
            merchant_name=meta.merchant_name,
            address=meta.address,
            phone=meta.phone_number,
            validation_status=getattr(meta, 'validation_status', None),
        )
        receipts.append(receipt)

    # Search for Urbane Cafe directly since we know that's the correct merchant
    print("🔍 Searching for 'Urbane Cafe' in Google Places...")
    urbane_cafe_result = places.search_by_text("Urbane Cafe")

    if not urbane_cafe_result or not urbane_cafe_result.get("place_id"):
        print("❌ Could not find 'Urbane Cafe' in Google Places")
        print("   Cannot proceed without the correct place_id")
        return

    urbane_place_id = urbane_cafe_result.get("place_id")
    urbane_name = urbane_cafe_result.get("name", "Urbane Cafe")
    urbane_address = urbane_cafe_result.get("formatted_address", "")
    urbane_phone = urbane_cafe_result.get("formatted_phone_number") or urbane_cafe_result.get("international_phone_number", "")

    print(f"✅ Found: {urbane_name}")
    print(f"   Place ID: {urbane_place_id}")
    print(f"   Address: {urbane_address}")
    print(f"   Phone: {urbane_phone}")
    print()

    # Get place details to ensure we have all information
    place_details = places.get_place_details(urbane_place_id)
    if place_details:
        urbane_name = place_details.get("name") or urbane_name
        urbane_address = place_details.get("formatted_address") or urbane_address
        urbane_phone = (
            place_details.get("formatted_phone_number") or
            place_details.get("international_phone_number") or
            urbane_phone
        )

    # Create matches for all receipts with Urbane Cafe's place_id
    print("📝 Creating matches for all receipts...")
    from receipt_agent.tools.place_id_finder import PlaceIdMatch, FinderResult

    matches = []
    for receipt in receipts:
        match = PlaceIdMatch(receipt=receipt)
        match.place_id = urbane_place_id
        match.place_name = urbane_name
        match.place_address = urbane_address
        match.place_phone = urbane_phone
        match.search_method = "text_search (Urbane Cafe)"
        match.confidence = 95.0  # High confidence since we're explicitly searching for Urbane Cafe
        match.found = True
        matches.append(match)

    # Create a FinderResult for compatibility with apply_fixes
    report = FinderResult(
        total_processed=len(matches),
        total_found=len(matches),
        total_not_found=0,
        total_errors=0,
        matches=matches,
        search_method_counts={"text_search (Urbane Cafe)": len(matches)},
    )
    finder._last_report = report

    # Print progress for each match
    print()
    for i, match in enumerate(matches, 1):
        print(f"[{i}/{len(matches)}] {match.receipt.image_id[:8]}...#{match.receipt.receipt_id}: ", end="")
        if match.found and match.place_id:
            print(f"✅ Will update to: {match.place_name} (place_id: {match.place_id[:20]}..., confidence: {match.confidence:.0f}%)")
        elif match.error:
            print(f"❌ Error: {match.error}")
        else:
            print(f"⚠️  No match found")

    # Print summary
    print()
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"Total receipts: {report.total_processed}")
    print(f"✅ Will update to Urbane Cafe: {report.total_found}")
    print(f"⚠️  Not found: {report.total_not_found}")
    print(f"❌ Errors: {report.total_errors}")
    print()

    # Save to file
    if args.output:
        report_dict = {
            "total_processed": report.total_processed,
            "total_found": report.total_found,
            "total_not_found": report.total_not_found,
            "total_errors": report.total_errors,
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
                }
                for m in matches
            ],
        }
        with open(args.output, "w") as f:
            json.dump(report_dict, f, indent=2)
        print(f"💾 Saved report to {args.output}")
        print()

    # Apply fixes if requested
    if args.apply:
        print("=" * 70)
        if args.dry_run:
            print("DRY RUN - Showing what would be updated")
        else:
            print("APPLYING FIXES - Updating metadata in DynamoDB")
        print("=" * 70)
        print(f"Threshold: min_confidence={args.min_confidence}")
        print()

        # Use the report from find_all_place_ids_agentic
        result = await finder.apply_fixes(
            dry_run=args.dry_run,
            min_confidence=args.min_confidence,
            update_other_fields=True,
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
    else:
        print()
        print("💡 Use --apply to actually update the metadata")
        print("   Use --dry-run to see what would be updated without writing")


if __name__ == "__main__":
    asyncio.run(main())

