#!/usr/bin/env python3
"""
Fix specific receipts by running the place_id_finder agent on them.

This script is useful when receipts have wrong place_ids and need to be corrected.
"""
import asyncio
import sys
import os
import json
from typing import Optional

# Add repo root to path
repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, repo_root)
sys.path.insert(0, os.path.join(repo_root, "receipt_dynamo"))
sys.path.insert(0, os.path.join(repo_root, "receipt_chroma"))

from receipt_dynamo import DynamoClient
from receipt_places import PlacesClient, PlacesConfig
from receipt_agent.tools.place_id_finder import PlaceIdFinder
from receipt_agent.clients.factory import create_chroma_client, create_embed_fn
from receipt_dynamo.data._pulumi import load_env, load_secrets
from receipt_agent.config.settings import get_settings
from receipt_agent.graph.place_id_finder_workflow import (
    create_place_id_finder_graph,
    run_place_id_finder,
)


def setup_environment():
    """Setup environment variables and secrets (same as test_place_id_finder.py)."""
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    infra_dir = os.path.join(repo_root, "infra")

    env = load_env("dev", working_dir=infra_dir)
    secrets = load_secrets("dev", working_dir=infra_dir)

    # Set environment variables
    os.environ["RECEIPT_AGENT_DYNAMO_TABLE_NAME"] = env.get(
        "dynamodb_table_name", "ReceiptsTable-dc5be22"
    )
    os.environ["RECEIPT_AGENT_AWS_REGION"] = secrets.get(
        "portfolio:aws-region", "us-east-1"
    )

    # API Keys
    google_places_key = secrets.get("portfolio:GOOGLE_PLACES_API_KEY")
    if google_places_key:
        os.environ["RECEIPT_AGENT_GOOGLE_PLACES_API_KEY"] = google_places_key

    openai_key = secrets.get("portfolio:OPENAI_API_KEY")
    if openai_key:
        os.environ["RECEIPT_AGENT_OPENAI_API_KEY"] = openai_key

    ollama_key = secrets.get("portfolio:OLLAMA_API_KEY")
    if ollama_key:
        os.environ["RECEIPT_AGENT_OLLAMA_API_KEY"] = ollama_key

    # Setup ChromaDB directories (same as test_agentic_agent.py)
    snapshot_base_dir = os.path.join(repo_root, ".chroma_snapshots")
    lines_snapshot_dir = os.path.join(snapshot_base_dir, "lines")
    words_snapshot_dir = os.path.join(snapshot_base_dir, "words")

    if (os.path.exists(lines_snapshot_dir) and os.listdir(lines_snapshot_dir) and
            os.path.exists(words_snapshot_dir) and os.listdir(words_snapshot_dir)):
        os.environ["RECEIPT_AGENT_CHROMA_LINES_DIRECTORY"] = lines_snapshot_dir
        os.environ["RECEIPT_AGENT_CHROMA_WORDS_DIRECTORY"] = words_snapshot_dir
        print(f"✅ ChromaDB snapshots found:")
        print(f"   Lines: {lines_snapshot_dir}")
        print(f"   Words: {words_snapshot_dir}")
    else:
        print("❌ ChromaDB snapshots not found. Please download them first.")
        print(f"   Expected: {lines_snapshot_dir} and {words_snapshot_dir}")
        raise ValueError("ChromaDB snapshots required")

    return env, secrets


async def fix_specific_receipts(
    receipt_list: list[dict],
    apply: bool = False,
    output_file: Optional[str] = None,
):
    """
    Run place_id_finder agent on specific receipts.

    Args:
        receipt_list: List of dicts with 'image_id' and 'receipt_id'
        apply: If True, apply fixes to DynamoDB. If False, dry run.
        output_file: Optional file to save results to
    """
    # Setup environment (same as test_place_id_finder.py)
    env, secrets = setup_environment()

    # Create DynamoDB client
    dynamo = DynamoClient(
        table_name=os.environ.get("RECEIPT_AGENT_DYNAMO_TABLE_NAME"),
        region=os.environ.get("RECEIPT_AGENT_AWS_REGION", "us-east-1"),
    )

    # Create Places client
    places_key = os.environ.get("RECEIPT_AGENT_GOOGLE_PLACES_API_KEY")
    if not places_key:
        raise ValueError("Google Places API key required")

    places_config = PlacesConfig(
        api_key=places_key,
        table_name=os.environ.get("RECEIPT_AGENT_DYNAMO_TABLE_NAME"),
        aws_region=os.environ.get("RECEIPT_AGENT_AWS_REGION", "us-east-1"),
        cache_enabled=True,
    )
    places = PlacesClient(config=places_config)

    # Setup ChromaDB and embedding function
    settings = get_settings()

    lines_dir = os.environ.get("RECEIPT_AGENT_CHROMA_LINES_DIRECTORY")
    words_dir = os.environ.get("RECEIPT_AGENT_CHROMA_WORDS_DIRECTORY")

    if lines_dir and words_dir:
        chroma_client = create_chroma_client(mode="read", settings=settings)
        if not chroma_client:
            raise ValueError("Could not create ChromaDB client")
    else:
        raise ValueError(
            "ChromaDB directories required. Set RECEIPT_AGENT_CHROMA_LINES_DIRECTORY "
            "and RECEIPT_AGENT_CHROMA_WORDS_DIRECTORY"
        )

    embed_fn = create_embed_fn(settings=settings)

    # Create agent graph
    print("🤖 Creating place_id_finder agent graph...")
    graph, state_holder = create_place_id_finder_graph(
        dynamo_client=dynamo,
        chroma_client=chroma_client,
        embed_fn=embed_fn,
        places_api=places,
        settings=settings,
    )
    print("✅ Agent graph created")
    print()

    # Process each receipt
    results = []
    for i, receipt_info in enumerate(receipt_list, 1):
        image_id = receipt_info["image_id"]
        receipt_id = receipt_info["receipt_id"]
        expected = receipt_info.get("expected", "Unknown")

        print("=" * 70)
        print(f"Processing {i}/{len(receipt_list)}: {image_id}#{receipt_id}")
        print(f"Expected: {expected}")
        print("=" * 70)

        # Get current metadata
        try:
            current_meta = dynamo.get_receipt_metadata(image_id, receipt_id)
            print(f"Current place_id: {current_meta.place_id[:50] if current_meta.place_id else 'None'}...")
            print(f"Current merchant_name: {current_meta.merchant_name}")
            print(f"Current address: {current_meta.address[:60] if current_meta.address else 'None'}...")
        except Exception as e:
            print(f"Could not get current metadata: {e}")
            current_meta = None

        print()

        # Run agent
        try:
            result = await run_place_id_finder(
                graph=graph,
                state_holder=state_holder,
                image_id=image_id,
                receipt_id=receipt_id,
            )

            results.append({
                "image_id": image_id,
                "receipt_id": receipt_id,
                "expected": expected,
                "found": result.get("found", False),
                "place_id": result.get("place_id"),
                "place_name": result.get("place_name"),
                "place_address": result.get("place_address"),
                "confidence": result.get("confidence", 0.0),
                "reasoning": result.get("reasoning", ""),
                "search_methods_used": result.get("search_methods_used", []),
            })

            if result.get("found"):
                print(f"✅ FOUND place_id: {result.get('place_id', '')[:50]}...")
                print(f"   Name: {result.get('place_name', 'N/A')}")
                print(f"   Confidence: {result.get('confidence', 0.0):.1%}")
            else:
                print(f"❌ NOT FOUND")
                print(f"   Reasoning: {result.get('reasoning', 'N/A')}")

        except Exception as e:
            print(f"❌ ERROR: {e}")
            results.append({
                "image_id": image_id,
                "receipt_id": receipt_id,
                "expected": expected,
                "found": False,
                "error": str(e),
            })

        print()

    # Summary
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    found_count = sum(1 for r in results if r.get("found"))
    print(f"Total processed: {len(results)}")
    print(f"Found: {found_count}")
    print(f"Not found: {len(results) - found_count}")
    print()

    # Save results
    if output_file:
        with open(output_file, "w") as f:
            json.dump(results, f, indent=2)
        print(f"💾 Saved results to {output_file}")
        print()

    # Apply fixes if requested
    if apply and found_count > 0:
        print("=" * 70)
        print("APPLYING FIXES TO DYNAMODB")
        print("=" * 70)
        print()

        from receipt_agent.tools.place_id_finder import PlaceIdMatch, ReceiptRecord
        from receipt_dynamo.entities.receipt_metadata import ReceiptMetadata
        from receipt_dynamo.constants import MerchantValidationStatus
        from datetime import datetime

        updated = 0
        failed = 0

        for result in results:
            if not result.get("found"):
                continue

            image_id = result["image_id"]
            receipt_id = result["receipt_id"]
            place_id = result["place_id"]
            place_name = result["place_name"]
            place_address = result["place_address"]
            place_phone = result.get("place_phone")

            try:
                # Get place details to ensure we have all info
                place_details = places.get_place_details(place_id)
                if place_details:
                    place_name = place_details.get("name") or place_name
                    place_address = place_details.get("formatted_address") or place_address
                    place_phone = (
                        place_details.get("formatted_phone_number") or
                        place_details.get("international_phone_number") or
                        place_phone
                    )

                # Get or create metadata
                try:
                    metadata = dynamo.get_receipt_metadata(image_id, receipt_id)
                except Exception:
                    # Create new metadata
                    from receipt_dynamo.constants import MerchantValidationStatus
                    metadata = ReceiptMetadata(
                        image_id=image_id,
                        receipt_id=receipt_id,
                        place_id=place_id,
                        merchant_name=place_name,
                        address=place_address,
                        phone_number=place_phone,
                        validation_status=MerchantValidationStatus.MATCHED,
                        reasoning=f"Corrected by place_id_finder agent: {result.get('reasoning', '')}",
                        updated_at=datetime.utcnow(),
                    )
                    dynamo.add_receipt_metadata(metadata)
                    print(f"✅ Created metadata for {image_id[:8]}...#{receipt_id}")
                else:
                    # Update existing metadata
                    from receipt_dynamo.constants import MerchantValidationStatus
                    metadata.place_id = place_id
                    metadata.merchant_name = place_name
                    metadata.address = place_address
                    if place_phone:
                        metadata.phone_number = place_phone
                    metadata.validation_status = MerchantValidationStatus.MATCHED
                    metadata.reasoning = f"Corrected by place_id_finder agent: {result.get('reasoning', '')}"
                    metadata.updated_at = datetime.utcnow()
                    dynamo.update_receipt_metadata(metadata)
                    print(f"✅ Updated metadata for {image_id[:8]}...#{receipt_id}")

                updated += 1

            except Exception as e:
                print(f"❌ Failed to update {image_id[:8]}...#{receipt_id}: {e}")
                failed += 1

        print()
        print(f"Updated: {updated}")
        print(f"Failed: {failed}")
    else:
        print("Dry run mode - no changes applied")
        print("Use --apply to actually update DynamoDB")


async def main():
    # Receipts to fix
    receipts_to_fix = [
        {
            "image_id": "ab732680-ad1d-410a-98e8-356a550d543b",
            "receipt_id": 2,
            "expected": "USA Gasoline",
        },
        {
            "image_id": "8945c5e8-5c2c-4abf-a2de-078d7ab8f0ff",
            "receipt_id": 1,
            "expected": "Eastwood",
        },
        {
            "image_id": "e1223ce4-b7f3-48ee-a306-a574dd86a3ce",
            "receipt_id": 1,
            "expected": "Unknown (check receipt)",
        },
        {
            "image_id": "490a4076-6d7c-45bf-8fcc-5570a295d429",
            "receipt_id": 1,
            "expected": "The HandleBar Barbershop",
        },
    ]

    # Find Marina del Rey case
    load_env()
    os.environ["RECEIPT_AGENT_DYNAMO_TABLE_NAME"] = os.environ.get(
        "RECEIPT_AGENT_DYNAMO_TABLE_NAME", "ReceiptsTable-dc5be22"
    )
    os.environ["RECEIPT_AGENT_AWS_REGION"] = os.environ.get(
        "RECEIPT_AGENT_AWS_REGION", "us-east-1"
    )

    dynamo = DynamoClient(
        table_name=os.environ.get("RECEIPT_AGENT_DYNAMO_TABLE_NAME"),
        region=os.environ.get("RECEIPT_AGENT_AWS_REGION"),
    )

    last_key = None
    while True:
        batch, last_key = dynamo.list_receipt_metadatas(limit=1000, last_evaluated_key=last_key)
        for receipt in batch:
            if receipt.merchant_name and "marina del rey" in receipt.merchant_name.lower():
                if receipt.address and "13837 Fiji Way" in receipt.address:
                    receipts_to_fix.append({
                        "image_id": receipt.image_id,
                        "receipt_id": receipt.receipt_id,
                        "expected": "Marina del Rey (needs real business name)",
                    })
                    break
        if len(receipts_to_fix) >= 5 or not last_key:
            break

    # Check for Thousand Oaks city name case (we already fixed this one, but check)
    last_key = None
    while True:
        batch, last_key = dynamo.list_receipt_metadatas(limit=1000, last_evaluated_key=last_key)
        for receipt in batch:
            if receipt.merchant_name and receipt.merchant_name.lower() == "thousand oaks":
                if receipt.address and "2745 Teller Rd" in receipt.address:
                    # Check if it's still wrong
                    if receipt.place_id and "Thousand Oaks" not in receipt.place_id:
                        receipts_to_fix.append({
                            "image_id": receipt.image_id,
                            "receipt_id": receipt.receipt_id,
                            "expected": "The Home Depot",
                        })
                    break
        if not last_key:
            break

    print(f"Found {len(receipts_to_fix)} receipts to fix")
    print()

    # Run with dry run first
    await fix_specific_receipts(
        receipt_list=receipts_to_fix,
        apply=False,  # Dry run first
        output_file="/tmp/fix_specific_receipts_results.json",
    )

    print()
    print("=" * 70)
    print("Review the results above.")
    print("To apply fixes, run with --apply flag")
    print("=" * 70)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Fix specific receipts with place_id_finder agent")
    parser.add_argument("--apply", action="store_true", help="Apply fixes to DynamoDB")
    args = parser.parse_args()

    asyncio.run(fix_specific_receipts(
        receipt_list=[
            {
                "image_id": "ab732680-ad1d-410a-98e8-356a550d543b",
                "receipt_id": 2,
                "expected": "USA Gasoline",
            },
            {
                "image_id": "8945c5e8-5c2c-4abf-a2de-078d7ab8f0ff",
                "receipt_id": 1,
                "expected": "Eastwood",
            },
            {
                "image_id": "e1223ce4-b7f3-48ee-a306-a574dd86a3ce",
                "receipt_id": 1,
                "expected": "Unknown (check receipt)",
            },
            {
                "image_id": "490a4076-6d7c-45bf-8fcc-5570a295d429",
                "receipt_id": 1,
                "expected": "The HandleBar Barbershop",
            },
        ],
        apply=args.apply,
        output_file="/tmp/fix_specific_receipts_results.json",
    ))

