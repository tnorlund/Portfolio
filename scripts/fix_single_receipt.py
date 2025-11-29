#!/usr/bin/env python3
"""
Fix a single receipt's metadata by searching for the correct merchant.

Usage:
    python scripts/fix_single_receipt.py <image_id> <receipt_id> <merchant_name> [--apply]

Example:
    python scripts/fix_single_receipt.py 4b326441-103c-4dae-809e-899404c29ac1 1 "Westlake Physical Therapy" --apply
"""

import argparse
import asyncio
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
        print("❌ Google Places API key not found")
        sys.exit(1)

    print(f"📊 DynamoDB Table: {os.environ.get('RECEIPT_AGENT_DYNAMO_TABLE_NAME')}")
    return env, secrets


async def main():
    parser = argparse.ArgumentParser(
        description="Fix a single receipt's metadata",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("image_id", help="Image ID of the receipt")
    parser.add_argument("receipt_id", type=int, help="Receipt ID")
    parser.add_argument("merchant_name", help="Correct merchant name to search for")
    parser.add_argument("--apply", action="store_true", help="Actually apply the fix to DynamoDB")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be updated without writing")
    args = parser.parse_args()

    print("=" * 70)
    print("FIX SINGLE RECEIPT")
    print("=" * 70)
    print()
    print(f"Image ID: {args.image_id}")
    print(f"Receipt ID: {args.receipt_id}")
    print(f"Searching for: {args.merchant_name}")
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
    try:
        from receipt_places import PlacesClient, PlacesConfig
        places_config = PlacesConfig(
            api_key=places_key,
            table_name=os.environ.get("RECEIPT_AGENT_DYNAMO_TABLE_NAME"),
            aws_region=os.environ.get("RECEIPT_AGENT_AWS_REGION", "us-east-1"),
            cache_enabled=True,
        )
        places = PlacesClient(config=places_config)
        print("✅ Google Places client created")
    except Exception as e:
        print(f"❌ Could not create Places client: {e}")
        sys.exit(1)

    # Get current metadata
    print()
    print("📥 Loading current receipt metadata...")
    try:
        current_metadata = dynamo.get_receipt_metadata(
            image_id=args.image_id,
            receipt_id=args.receipt_id,
        )
        if not current_metadata:
            print(f"❌ No metadata found for {args.image_id}#{args.receipt_id}")
            sys.exit(1)

        print(f"   Current merchant_name: {current_metadata.merchant_name}")
        print(f"   Current place_id: {current_metadata.place_id}")
        print(f"   Current address: {current_metadata.address}")
        print(f"   Current phone: {current_metadata.phone_number}")
    except Exception as e:
        print(f"❌ Error loading metadata: {e}")
        sys.exit(1)

    # Search for the correct merchant
    print()
    print(f"🔍 Searching Google Places for '{args.merchant_name}'...")
    search_result = places.search_by_text(args.merchant_name)

    if not search_result or not search_result.get("place_id"):
        print(f"❌ Could not find '{args.merchant_name}' in Google Places")
        sys.exit(1)

    place_id = search_result.get("place_id")
    place_name = search_result.get("name", args.merchant_name)
    place_address = search_result.get("formatted_address", "")
    place_phone = search_result.get("formatted_phone_number") or search_result.get("international_phone_number", "")

    print(f"✅ Found: {place_name}")
    print(f"   Place ID: {place_id}")
    print(f"   Address: {place_address}")
    print(f"   Phone: {place_phone}")

    # Get full place details
    place_details = places.get_place_details(place_id)
    if place_details:
        place_name = place_details.get("name") or place_name
        place_address = place_details.get("formatted_address") or place_address
        place_phone = (
            place_details.get("formatted_phone_number") or
            place_details.get("international_phone_number") or
            place_phone
        )

    # Show what will be updated
    print()
    print("=" * 70)
    print("CHANGES TO BE MADE")
    print("=" * 70)
    print(f"merchant_name: '{current_metadata.merchant_name}' → '{place_name}'")
    print(f"place_id: '{current_metadata.place_id}' → '{place_id}'")
    if current_metadata.address != place_address:
        print(f"address: '{current_metadata.address}' → '{place_address}'")
    if current_metadata.phone_number != place_phone:
        print(f"phone_number: '{current_metadata.phone_number}' → '{place_phone}'")
    print()

    # Apply the update
    if args.apply:
        if args.dry_run:
            print("🔍 DRY RUN - Would update metadata (not actually updating)")
        else:
            print("💾 Updating receipt metadata in DynamoDB...")
            from receipt_dynamo.entities.receipt_metadata import ReceiptMetadata
            from receipt_dynamo.constants import MerchantValidationStatus, ValidationMethod
            from datetime import datetime, timezone

            # Update the metadata
            current_metadata.merchant_name = place_name
            current_metadata.place_id = place_id
            current_metadata.address = place_address
            current_metadata.phone_number = place_phone
            current_metadata.validation_status = MerchantValidationStatus.MATCHED.value
            current_metadata.validated_by = ValidationMethod.TEXT_SEARCH.value
            current_metadata.matched_fields = ["merchant_name", "place_id"]
            current_metadata.reasoning = f"Updated via fix_single_receipt script: searched for '{args.merchant_name}'"
            current_metadata.timestamp = datetime.now(timezone.utc)

            try:
                dynamo.update_receipt_metadata(current_metadata)
                print("✅ Successfully updated receipt metadata!")
            except Exception as e:
                print(f"❌ Error updating metadata: {e}")
                sys.exit(1)
    else:
        print("💡 Use --apply to actually update the metadata")
        print("   Use --dry-run to see what would be updated without writing")


if __name__ == "__main__":
    asyncio.run(main())


