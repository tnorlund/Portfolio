#!/usr/bin/env python3
"""
Test the Merchant Harmonizer.

This script demonstrates how the harmonizer:
1. Finds similar receipts based on content (not labels)
2. Builds consensus from the group
3. Validates against Google Places
4. Reports inconsistencies

Usage:
    python scripts/test_harmonizer.py
    python scripts/test_harmonizer.py --merchant "Italia Deli & Bakery"
    python scripts/test_harmonizer.py --scan-all --limit 50
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

# Ensure repo is in path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def setup_environment():
    """Load secrets and outputs from Pulumi."""
    print("=" * 60)
    print("🔧 Merchant Harmonizer")
    print("=" * 60)

    infra_dir = Path(__file__).parent.parent / "infra"
    from receipt_dynamo.data._pulumi import load_env, load_secrets

    env = load_env("dev", working_dir=str(infra_dir))
    secrets = load_secrets("dev", working_dir=str(infra_dir))

    # DynamoDB
    table_name = env.get("dynamodb_table_name") or env.get("receipts_table_name")
    os.environ["RECEIPT_AGENT_DYNAMO_TABLE_NAME"] = table_name or ""
    os.environ["DYNAMO_TABLE_NAME"] = table_name or ""
    print(f"📊 DynamoDB Table: {table_name}")

    # ChromaDB
    repo_root = os.path.dirname(os.path.dirname(__file__))
    lines_dir = os.path.join(repo_root, ".chroma_snapshots", "lines")
    words_dir = os.path.join(repo_root, ".chroma_snapshots", "words")

    if os.path.exists(lines_dir) and os.path.exists(words_dir):
        os.environ["RECEIPT_AGENT_CHROMA_LINES_DIRECTORY"] = lines_dir
        os.environ["RECEIPT_AGENT_CHROMA_WORDS_DIRECTORY"] = words_dir
        print(f"✅ ChromaDB: {lines_dir}")
    else:
        print("❌ ChromaDB snapshots not found")
        sys.exit(1)

    # API Keys
    openai_key = secrets.get("portfolio:OPENAI_API_KEY", "")
    if openai_key:
        os.environ["RECEIPT_AGENT_OPENAI_API_KEY"] = openai_key
        os.environ["OPENAI_API_KEY"] = openai_key
        print("✅ OpenAI API key loaded")

    google_key = secrets.get("portfolio:GOOGLE_PLACES_API_KEY", "")
    if google_key:
        os.environ["RECEIPT_AGENT_GOOGLE_PLACES_API_KEY"] = google_key
        print("✅ Google Places API key loaded")
    else:
        print("⚠️  Google Places API key not found (will use consensus only)")

    return env, secrets


async def test_single_receipt(harmonizer, image_id: str, receipt_id: int):
    """Test harmonization on a single receipt."""
    print(f"\n🔍 Analyzing receipt: {image_id[:12]}...#{receipt_id}")
    print("-" * 60)

    result = await harmonizer.harmonize_receipt(image_id, receipt_id)

    print(f"\n📋 Current Metadata:")
    print(f"   Merchant: {result.current_merchant_name}")
    print(f"   Place ID: {result.current_place_id}")
    print(f"   Address:  {result.current_address}")
    print(f"   Phone:    {result.current_phone}")

    print(f"\n🔗 Similar Receipts Found: {result.group_size - 1}")
    for img_id, rcpt_id in result.similar_receipts[:5]:
        print(f"   - {img_id[:12]}...#{rcpt_id}")

    print(f"\n✨ Recommended Metadata:")
    print(f"   Merchant: {result.recommended_merchant_name}")
    print(f"   Place ID: {result.recommended_place_id}")
    print(f"   Address:  {result.recommended_address}")
    print(f"   Phone:    {result.recommended_phone}")

    print(f"\n📊 Status:")
    print(f"   Confidence: {result.confidence:.0%}")
    print(f"   Needs Update: {'Yes ⚠️' if result.needs_update else 'No ✅'}")

    if result.changes_needed:
        print(f"\n🔄 Changes Needed:")
        for change in result.changes_needed:
            print(f"   - {change}")

    print(f"\n💬 Reasoning: {result.reasoning}")

    return result


async def test_merchant(harmonizer, merchant_name: str):
    """Test harmonization for all receipts of a merchant."""
    print(f"\n🏪 Analyzing merchant: {merchant_name}")
    print("=" * 60)

    results = await harmonizer.harmonize_all_for_merchant(merchant_name)

    # Summary
    needs_update = [r for r in results if r.needs_update]
    consistent = [r for r in results if not r.needs_update]

    print(f"\n📊 Summary for '{merchant_name}':")
    print(f"   Total receipts: {len(results)}")
    print(f"   Consistent:     {len(consistent)} ✅")
    print(f"   Need updates:   {len(needs_update)} ⚠️")

    if needs_update:
        print(f"\n⚠️ Receipts needing updates:")
        for r in needs_update:
            print(f"\n   {r.image_id[:12]}...#{r.receipt_id}:")
            for change in r.changes_needed:
                print(f"      - {change}")

    return results


def save_results(results: list, output_file: str):
    """Save harmonization results to JSON file."""
    import json
    from dataclasses import asdict

    # Convert to serializable format
    data = []
    for r in results:
        item = {
            "image_id": r.image_id,
            "receipt_id": r.receipt_id,
            "current": {
                "merchant_name": r.current_merchant_name,
                "place_id": r.current_place_id,
                "address": r.current_address,
                "phone": r.current_phone,
            },
            "recommended": {
                "merchant_name": r.recommended_merchant_name,
                "place_id": r.recommended_place_id,
                "address": r.recommended_address,
                "phone": r.recommended_phone,
            },
            "needs_update": r.needs_update,
            "changes_needed": r.changes_needed,
            "confidence": r.confidence,
            "group_size": r.group_size,
            "reasoning": r.reasoning,
        }
        data.append(item)

    with open(output_file, "w") as f:
        json.dump(data, f, indent=2)

    print(f"\n💾 Saved {len(data)} results to {output_file}")


async def scan_all_merchants(harmonizer, dynamo, output_file: str = None):
    """Scan all merchants and generate a comprehensive report."""
    print("\n🏪 Scanning all merchants...")
    print("=" * 60)

    # Get all receipt metadatas
    metadatas, _ = dynamo.list_receipt_metadatas(limit=500)

    # Group by merchant
    merchants: dict[str, list] = {}
    for m in metadatas:
        name = m.merchant_name or "UNKNOWN"
        if name not in merchants:
            merchants[name] = []
        merchants[name].append((m.image_id, m.receipt_id))

    print(f"Found {len(metadatas)} receipts across {len(merchants)} merchants\n")

    all_results = []
    merchant_summary = []

    for merchant_name in sorted(merchants.keys()):
        receipts = merchants[merchant_name]
        if len(receipts) < 2:
            # Skip merchants with only 1 receipt - no consensus possible
            continue

        print(f"  {merchant_name}: {len(receipts)} receipts...", end=" ", flush=True)

        try:
            results = await harmonizer.harmonize_all_for_merchant(merchant_name)
            needs_update = [r for r in results if r.needs_update]
            consistent = [r for r in results if not r.needs_update]

            summary = {
                "merchant": merchant_name,
                "total_receipts": len(results),
                "consistent": len(consistent),
                "needs_update": len(needs_update),
                "pct_consistent": len(consistent) / len(results) * 100 if results else 0,
            }
            merchant_summary.append(summary)
            all_results.extend(results)

            if needs_update:
                print(f"⚠️  {len(needs_update)} need updates")
            else:
                print("✅ all consistent")

        except Exception as e:
            print(f"❌ Error: {e}")

    # Print summary
    print("\n" + "=" * 60)
    print("SUMMARY BY MERCHANT")
    print("=" * 60)

    # Sort by % consistent
    for s in sorted(merchant_summary, key=lambda x: x["pct_consistent"]):
        status = "✅" if s["pct_consistent"] == 100 else "⚠️"
        print(f"{status} {s['merchant']}: {s['consistent']}/{s['total_receipts']} consistent ({s['pct_consistent']:.0f}%)")

    # Save to file if specified
    if output_file:
        import json
        report = {
            "summary": merchant_summary,
            "receipts": [
                {
                    "image_id": r.image_id,
                    "receipt_id": r.receipt_id,
                    "merchant": r.current_merchant_name,
                    "needs_update": r.needs_update,
                    "changes_needed": r.changes_needed,
                    "confidence": r.confidence,
                    "current": {
                        "merchant_name": r.current_merchant_name,
                        "place_id": r.current_place_id,
                        "address": r.current_address,
                        "phone": r.current_phone,
                    },
                    "recommended": {
                        "merchant_name": r.recommended_merchant_name,
                        "place_id": r.recommended_place_id,
                        "address": r.recommended_address,
                        "phone": r.recommended_phone,
                    },
                }
                for r in all_results
            ],
        }
        with open(output_file, "w") as f:
            json.dump(report, f, indent=2)
        print(f"\n💾 Saved report to {output_file}")

    return all_results


async def scan_all(harmonizer, limit: int):
    """Scan all receipts for inconsistencies."""
    print(f"\n🔍 Scanning up to {limit} receipts for inconsistencies...")
    print("=" * 60)

    inconsistent = await harmonizer.find_all_inconsistencies(limit=limit)

    print(f"\n📊 Found {len(inconsistent)} receipts with inconsistent metadata:")

    # Group by type of inconsistency
    by_issue: dict[str, list] = {}
    for r in inconsistent:
        for change in r.changes_needed:
            issue_type = change.split(":")[0]
            if issue_type not in by_issue:
                by_issue[issue_type] = []
            by_issue[issue_type].append(r)

    for issue_type, receipts in sorted(by_issue.items(), key=lambda x: -len(x[1])):
        print(f"\n   {issue_type}: {len(receipts)} receipts")
        for r in receipts[:3]:
            print(f"      - {r.image_id[:12]}...#{r.receipt_id}")
        if len(receipts) > 3:
            print(f"      ... and {len(receipts) - 3} more")

    return inconsistent


async def main():
    parser = argparse.ArgumentParser(description="Test Merchant Harmonizer")
    parser.add_argument("--merchant", type=str, help="Harmonize specific merchant")
    parser.add_argument("--receipt", type=str, help="Harmonize specific receipt (image_id#receipt_id)")
    parser.add_argument("--scan-all", action="store_true", help="Scan all receipts for inconsistencies")
    parser.add_argument("--scan-merchants", action="store_true", help="Scan all merchants and save report")
    parser.add_argument("--limit", type=int, default=50, help="Limit for scan-all")
    parser.add_argument("--output", "-o", type=str, help="Output file (JSON)")
    args = parser.parse_args()

    setup_environment()

    # Set up logging
    import logging
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("chromadb").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)

    # Create clients
    from receipt_dynamo.data.dynamo_client import DynamoClient
    from receipt_agent.clients.factory import create_chroma_client, create_places_client
    from receipt_agent.tools.harmonizer import MerchantHarmonizer
    from openai import OpenAI

    table_name = os.environ.get("DYNAMO_TABLE_NAME")
    dynamo = DynamoClient(table_name=table_name)
    chroma = create_chroma_client()

    # Create Places client with cache disabled (schema issue in dev)
    from receipt_places import PlacesClient, PlacesConfig
    google_key = os.environ.get("RECEIPT_AGENT_GOOGLE_PLACES_API_KEY", "")
    if google_key:
        places_config = PlacesConfig(
            api_key=google_key,
            table_name=table_name,
            cache_enabled=False,  # Disable cache - DynamoDB schema not set up for it
        )
        places = PlacesClient(config=places_config)
    else:
        places = None
        print("⚠️  No Google Places API key - will use consensus only")

    # Create embedding function
    openai_client = OpenAI()
    def embed_fn(texts):
        response = openai_client.embeddings.create(
            input=texts,
            model="text-embedding-3-small",
        )
        return [d.embedding for d in response.data]

    # Create harmonizer
    harmonizer = MerchantHarmonizer(
        dynamo_client=dynamo,
        chroma_client=chroma,
        places_client=places,
        embed_fn=embed_fn,
    )
    print("✅ Harmonizer initialized")

    if args.receipt:
        # Test single receipt
        parts = args.receipt.split("#")
        if len(parts) != 2:
            print("❌ Invalid receipt format. Use: image_id#receipt_id")
            return
        await test_single_receipt(harmonizer, parts[0], int(parts[1]))

    elif args.merchant:
        # Test specific merchant
        await test_merchant(harmonizer, args.merchant)

    elif args.scan_all:
        # Scan all receipts
        inconsistent = await scan_all(harmonizer, args.limit)
        if args.output:
            save_results(inconsistent, args.output)

    elif args.scan_merchants:
        # Scan all merchants and generate report
        await scan_all_merchants(harmonizer, dynamo, args.output)

    else:
        # Default: test with a sample receipt
        metadatas, _ = dynamo.list_receipt_metadatas(limit=5)
        if metadatas:
            # Pick one with metadata
            for meta in metadatas:
                if meta.merchant_name:
                    await test_single_receipt(harmonizer, meta.image_id, meta.receipt_id)
                    break


if __name__ == "__main__":
    asyncio.run(main())

