#!/usr/bin/env python
"""
Fix receipts where merchant_name is actually an address.

This script finds receipts where the merchant_name looks like an address
(e.g., "10601 Magnolia Blvd", "3980 Thousand Oaks Blvd #2") and runs
the receipt metadata finder agent to find the correct merchant name.

Usage:
    # Dry run - see what would be fixed
    python scripts/fix_address_as_merchant_name.py

    # Actually apply fixes
    python scripts/fix_address_as_merchant_name.py --apply

    # Limit to specific addresses
    python scripts/fix_address_as_merchant_name.py --addresses "10601 Magnolia Blvd" "10601 W Magnolia Blvd"
"""

import argparse
import asyncio
import os
import re
import sys

# Add repo root to path
repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, repo_root)

# Add receipt_chroma to path if not installed
receipt_chroma_path = os.path.join(repo_root, "receipt_chroma")
if os.path.exists(receipt_chroma_path) and receipt_chroma_path not in sys.path:
    sys.path.insert(0, receipt_chroma_path)


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

    ollama_key = secrets.get("portfolio:OLLAMA_API_KEY")
    if ollama_key:
        os.environ["RECEIPT_AGENT_OLLAMA_API_KEY"] = ollama_key

    ollama_base_url = secrets.get("portfolio:OLLAMA_BASE_URL", "https://ollama.com")
    os.environ["RECEIPT_AGENT_OLLAMA_BASE_URL"] = ollama_base_url

    ollama_model = secrets.get("portfolio:OLLAMA_MODEL", "gpt-oss:120b-cloud")
    os.environ["RECEIPT_AGENT_OLLAMA_MODEL"] = ollama_model

    # OpenAI API key for embeddings
    openai_key = secrets.get("portfolio:OPENAI_API_KEY")
    if openai_key:
        os.environ["RECEIPT_AGENT_OPENAI_API_KEY"] = openai_key
        os.environ["OPENAI_API_KEY"] = openai_key

    # ChromaDB
    lines_dir = os.path.join(repo_root, ".chroma_snapshots", "lines")
    words_dir = os.path.join(repo_root, ".chroma_snapshots", "words")

    if os.path.exists(lines_dir) and os.path.exists(words_dir):
        os.environ["RECEIPT_AGENT_CHROMA_LINES_DIRECTORY"] = lines_dir
        os.environ["RECEIPT_AGENT_CHROMA_WORDS_DIRECTORY"] = words_dir
        print(f"✅ ChromaDB: {lines_dir}, {words_dir}")
    else:
        print(f"⚠️  ChromaDB snapshots not found at {lines_dir} or {words_dir}")
        print("   Agent mode may not work without ChromaDB")

    return env, secrets


def looks_like_address(text: str) -> bool:
    """
    Check if a string looks like an address rather than a merchant name.

    This function is STRICT - it only flags strings that are VERY likely to be addresses.
    An address must have BOTH:
    1. A street suffix (Blvd, Rd, St, etc.) at the END of the string
    2. A number (either at the start as a street number, or as a unit number)

    This avoids false positives like "Stonefire Grill" or "Universal Studios Hollywood"
    which might contain street-like words but are clearly merchant names.
    """
    if not text or len(text.strip()) < 5:
        return False

    text_upper = text.upper().strip()
    words = text_upper.split()

    # Street suffixes - must match as whole words at the end
    street_suffixes = [
        "BLVD", "BOULEVARD", "RD", "ROAD", "ST", "STREET", "AVE", "AVENUE",
        "DR", "DRIVE", "LN", "LANE", "CT", "COURT", "CIR", "CIRCLE",
        "PL", "PLACE", "PKWY", "PARKWAY", "WAY", "TER", "TERRACE"
    ]

    # Check if last word (or second-to-last if followed by unit) is a street suffix
    has_street_suffix = False
    if words:
        # Check last word
        if words[-1] in street_suffixes:
            has_street_suffix = True
        # Check second-to-last word if last word is a unit number
        elif len(words) > 1 and re.match(r'^[#]?\d+$', words[-1]) and words[-2] in street_suffixes:
            has_street_suffix = True

    if not has_street_suffix:
        return False

    # Must also have a number - either at start (street number) or as unit
    # Check if starts with number (street number)
    starts_with_number = bool(re.match(r'^\d+', text_upper))

    # Check for unit numbers (#2, Unit 3, Apt 4, Suite 5) - must be at the end
    has_unit = bool(re.search(r'(?:#|UNIT|APT|SUITE)\s*\d+\s*$', text_upper))

    # Check for directional indicators after a number (e.g., "10601 W Magnolia Blvd")
    has_directional_after_number = bool(re.search(r'^\d+\s+[NSEW]\s+', text_upper))

    # STRICT: Must have BOTH street suffix AND (number at start OR unit number OR directional after number)
    # This ensures we only catch actual addresses, not merchant names with street-like words
    return has_street_suffix and (starts_with_number or has_unit or has_directional_after_number)


async def find_receipts_with_specific_merchant_names(
    dynamo,
    merchant_names: list[str],
) -> list[tuple[str, int, str]]:
    """
    Find all receipts with specific merchant names.

    Returns:
        List of (image_id, receipt_id, merchant_name) tuples
    """
    print(f"📥 Searching for receipts with these merchant names:")
    for name in merchant_names:
        print(f"  - {name}")
    print()

    receipts_to_fix = []

    try:
        # Query each merchant name directly using get_receipt_metadatas_by_merchant
        for merchant_name in merchant_names:
            print(f"  Searching for: {merchant_name}...")
            last_key = None
            found_count = 0

            while True:
                batch, last_key = dynamo.get_receipt_metadatas_by_merchant(
                    merchant_name=merchant_name,
                    limit=1000,
                    last_evaluated_key=last_key,
                )

                for meta in batch:
                    # Check if merchant_name matches (case-insensitive, allow partial match)
                    meta_name = (meta.merchant_name or "").strip()
                    if meta_name.lower() == merchant_name.lower() or merchant_name.lower() in meta_name.lower():
                        receipts_to_fix.append((
                            meta.image_id,
                            meta.receipt_id,
                            meta_name,
                        ))
                        found_count += 1

                if not last_key:
                    break

            print(f"    Found {found_count} receipts")

        print(f"\n✅ Total found: {len(receipts_to_fix)} receipts")

        # Show all found receipts
        if receipts_to_fix:
            print("\nReceipts to fix:")
            for image_id, receipt_id, merchant_name in receipts_to_fix:
                print(f"  {image_id[:12]}...#{receipt_id}: {merchant_name}")

        return receipts_to_fix

    except Exception as e:
        print(f"❌ Error loading receipts: {e}")
        import traceback
        traceback.print_exc()
        raise


async def main():
    parser = argparse.ArgumentParser(
        description="Fix receipts where merchant_name is actually an address",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually apply fixes to DynamoDB (default: dry run)",
    )
    parser.add_argument(
        "--addresses",
        nargs="+",
        help="Specific addresses to fix (e.g., '10601 Magnolia Blvd' '10601 W Magnolia Blvd')",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Limit to N receipts (for testing)",
    )
    parser.add_argument(
        "--min-confidence",
        type=float,
        default=50.0,
        help="Minimum confidence to apply fix (default: 50)",
    )
    args = parser.parse_args()

    print("=" * 70)
    print("FIX RECEIPTS WITH ADDRESS AS MERCHANT NAME")
    print("=" * 70)
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

    # Create Places client
    places_key = os.environ.get("RECEIPT_AGENT_GOOGLE_PLACES_API_KEY")
    if not places_key:
        print("❌ Google Places API key required")
        sys.exit(1)

    from receipt_places import PlacesClient, PlacesConfig
    places_config = PlacesConfig(
        api_key=places_key,
        table_name=os.environ.get("RECEIPT_AGENT_DYNAMO_TABLE_NAME"),
        aws_region=os.environ.get("RECEIPT_AGENT_AWS_REGION", "us-east-1"),
        cache_enabled=True,
    )
    places = PlacesClient(config=places_config)
    print("✅ Google Places client created")

    # Setup ChromaDB and embedding function
    from receipt_agent.clients.factory import create_chroma_client, create_embed_fn
    from receipt_agent.config.settings import get_settings

    settings = get_settings()
    chroma_client = create_chroma_client(mode="read", settings=settings)
    if not chroma_client:
        print("❌ ChromaDB client required for agent mode")
        sys.exit(1)
    print("✅ ChromaDB client created")

    embed_fn = create_embed_fn(settings=settings)
    print("✅ Embedding function created")
    print()

    # Find receipts with specific merchant names
    if not args.addresses:
        print("❌ Error: --addresses argument required")
        print("   Example: --addresses '10601 Magnolia Blvd' '10601 W Magnolia Blvd'")
        sys.exit(1)

    receipts_to_fix = await find_receipts_with_specific_merchant_names(
        dynamo,
        merchant_names=args.addresses,
    )

    if not receipts_to_fix:
        print("✅ No receipts found with addresses as merchant_name")
        return

    if args.limit:
        receipts_to_fix = receipts_to_fix[:args.limit]
        print(f"⚠️  Limited to {len(receipts_to_fix)} receipts for testing")

    print()
    print("=" * 70)
    print("RUNNING RECEIPT METADATA FINDER AGENT")
    print("=" * 70)
    print()

    # Create ReceiptMetadataFinder
    from receipt_agent.tools.receipt_metadata_finder import ReceiptMetadataFinder
    from receipt_agent.graph.receipt_metadata_finder_workflow import (
        create_receipt_metadata_finder_graph,
        run_receipt_metadata_finder,
    )

    finder = ReceiptMetadataFinder(
        dynamo_client=dynamo,
        places_client=places,
        chroma_client=chroma_client,
        embed_fn=embed_fn,
        settings=settings,
    )

    # Create agent graph
    graph, state_holder = create_receipt_metadata_finder_graph(
        dynamo_client=dynamo,
        chroma_client=chroma_client,
        embed_fn=embed_fn,
        places_api=places,
        settings=settings,
    )

    # Process each receipt
    from receipt_agent.tools.receipt_metadata_finder import FinderResult, MetadataMatch, ReceiptRecord

    result = FinderResult()
    result.total_processed = len(receipts_to_fix)

    print(f"Processing {len(receipts_to_fix)} receipts...")
    print()

    for i, (image_id, receipt_id, merchant_name) in enumerate(receipts_to_fix):
        print(f"  [{i+1}/{len(receipts_to_fix)}] Processing {image_id[:12]}...#{receipt_id} ({merchant_name})...")
        sys.stdout.flush()  # Force output to be written

        receipt = ReceiptRecord(
            image_id=image_id,
            receipt_id=receipt_id,
            merchant_name=merchant_name,
            place_id=None,
            address=None,
            phone=None,
        )

        try:
            # Run agent
            print(f"    Running agent...")
            sys.stdout.flush()
            agent_result = await run_receipt_metadata_finder(
                graph=graph,
                state_holder=state_holder,
                image_id=image_id,
                receipt_id=receipt_id,
            )
            print(f"    Agent completed")
            sys.stdout.flush()

            # Convert to MetadataMatch
            match = MetadataMatch(receipt=receipt)

            if agent_result and agent_result.get("found"):
                match.place_id = agent_result.get("place_id")
                match.merchant_name = agent_result.get("merchant_name")
                match.address = agent_result.get("address")
                match.phone_number = agent_result.get("phone_number")
                match.confidence = agent_result.get("confidence", 0.0) * 100.0
                match.field_confidence = agent_result.get("field_confidence", {})
                match.sources = agent_result.get("sources", {})
                match.fields_found = agent_result.get("fields_found", [])
                match.reasoning = agent_result.get("reasoning", "")
                match.found = True

                # Count fields found
                for field in match.fields_found:
                    result.field_counts[field] += 1

                # Determine if all fields found or partial
                required_fields = ["place_id", "merchant_name", "address", "phone_number"]
                found_required = [f for f in required_fields if f in match.fields_found]
                if len(found_required) == len(required_fields):
                    result.total_found_all += 1
                elif len(found_required) > 0:
                    result.total_found_partial += 1
                else:
                    result.total_not_found += 1
            else:
                match.found = False
                match.error = agent_result.get("reasoning", "No metadata found") if agent_result else "No metadata found"
                match.confidence = 0.0
                result.total_not_found += 1

            result.matches.append(match)

            if match.error:
                result.total_errors += 1

        except Exception as e:
            print(f"  ❌ Error processing {image_id[:12]}...#{receipt_id}: {e}")
            match = MetadataMatch(receipt=receipt)
            match.found = False
            match.error = str(e)
            match.confidence = 0.0
            result.matches.append(match)
            result.total_not_found += 1
            result.total_errors += 1

    # Store result for apply_fixes
    finder._last_report = result

    # Print summary
    print()
    finder.print_summary(result)

    # Apply fixes if requested
    if args.apply:
        print()
        print("=" * 70)
        print("APPLYING FIXES TO DYNAMODB")
        print("=" * 70)
        print(f"Threshold: min_confidence={args.min_confidence}")
        print()

        update_result = await finder.apply_fixes(
            dry_run=False,
            min_confidence=args.min_confidence,
        )

        print()
        print("Update Summary:")
        print(f"  📊 Processed: {update_result.total_processed}")
        print(f"  ✅ Updated: {update_result.total_updated}")
        print(f"  ⏭️  Skipped: {update_result.total_skipped}")
        print(f"  ❌ Failed: {update_result.total_failed}")

        if update_result.errors:
            print()
            print("Errors:")
            for err in update_result.errors[:10]:
                print(f"  - {err}")
            if len(update_result.errors) > 10:
                print(f"  ... and {len(update_result.errors) - 10} more")
    else:
        print()
        print("=" * 70)
        print("DRY RUN - No changes made")
        print("=" * 70)
        print("Use --apply to actually update DynamoDB")


if __name__ == "__main__":
    asyncio.run(main())

