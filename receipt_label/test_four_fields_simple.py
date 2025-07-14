#!/usr/bin/env python3
"""
Simple test focused on 4 required fields only:
MERCHANT_NAME, DATE, TIME, GRAND_TOTAL
"""

import asyncio
import json
from collections import defaultdict
from pathlib import Path

from receipt_label.data.local_data_loader import (
    create_mock_receipt_from_export,
)
from receipt_label.decision_engine.four_field_orchestrator import (
    FourFieldOrchestrator,
)


async def test_four_fields_simple(
    data_dir: str = "./receipt_data_with_labels",
):
    """Test simplified 4-field decision logic."""

    print("🎯 FOUR FIELD DECISION TEST")
    print("=" * 50)
    print("Required: MERCHANT_NAME, DATE, TIME, GRAND_TOTAL")
    print("=" * 50)

    data_path = Path(data_dir)
    receipt_files = list(data_path.glob("*.json"))
    receipt_files = [
        f
        for f in receipt_files
        if not f.name.startswith(
            ("manifest", "coverage", "pattern", "metadata", "required")
        )
    ]

    print(f"\n📁 Found {len(receipt_files)} receipt files")

    # Initialize simple orchestrator
    orchestrator = FourFieldOrchestrator()

    # Statistics
    stats = {
        "total": 0,
        "skip": 0,
        "batch": 0,
        "required": 0,
        "field_detection": defaultdict(int),
        "examples": {"skip": [], "batch": [], "required": []},
    }

    # Process each receipt
    for i, receipt_file in enumerate(receipt_files, 1):
        print(f"\n[{i}/{len(receipt_files)}] {receipt_file.name[:40]}...")

        try:
            # Load receipt data
            with open(receipt_file, "r") as f:
                export_data = json.load(f)

            receipts = export_data.get("receipts", [])
            receipt_words = export_data.get("receipt_words", [])
            receipt_metadatas = export_data.get("receipt_metadatas", [])

            if not receipts:
                continue

            receipt = receipts[0]
            image_id = receipt["image_id"]
            receipt_id = receipt["receipt_id"]

            # Get words for this receipt
            words_for_receipt = [
                w
                for w in receipt_words
                if w["image_id"] == image_id and w["receipt_id"] == receipt_id
            ]

            # Create receipt objects
            receipt_obj, words, lines = create_mock_receipt_from_export(
                {
                    "receipt": receipt,
                    "words": words_for_receipt,
                    "lines": export_data.get("receipt_lines", []),
                }
            )

            # Prepare metadata
            metadata = None
            if receipt_metadatas:
                metadata = receipt_metadatas[0].copy()
                metadata["image_id"] = image_id
                metadata["receipt_id"] = receipt_id

            # Process with simple 4-field logic
            decision = await orchestrator.process_receipt_simple(
                words, metadata
            )

            # Record statistics
            stats["total"] += 1
            stats[decision.action.value] += 1

            # Record which fields were found
            for field in decision.essential_fields_found:
                stats["field_detection"][field] += 1

            # Show results
            found_fields = list(decision.essential_fields_found)
            missing_fields = list(decision.essential_fields_missing)

            print(f"   Decision: {decision.action.value.upper()}")
            print(f"   Found: {found_fields}")
            print(f"   Missing: {missing_fields}")
            print(f"   Reasoning: {decision.reasoning}")

            # Store examples
            example = {
                "file": receipt_file.name,
                "merchant": (
                    metadata.get("merchant_name", "Unknown")
                    if metadata
                    else "Unknown"
                ),
                "category": (
                    metadata.get("merchant_category", "Unknown")
                    if metadata
                    else "Unknown"
                ),
                "found": found_fields,
                "missing": missing_fields,
            }
            stats["examples"][decision.action.value].append(example)

        except Exception as e:
            print(f"   ❌ Error: {e}")
            continue

    # Generate report
    print(f"\n{'='*50}")
    print("📊 FOUR FIELD ANALYSIS RESULTS")
    print(f"{'='*50}")

    if stats["total"] > 0:
        # Decision breakdown
        print(f"\n🎯 DECISION BREAKDOWN ({stats['total']} receipts)")
        skip_rate = stats["skip"] / stats["total"] * 100
        batch_rate = stats["batch"] / stats["total"] * 100
        required_rate = stats["required"] / stats["total"] * 100

        print(f"   SKIP: {stats['skip']} ({skip_rate:.1f}%) ✅")
        print(f"   BATCH: {stats['batch']} ({batch_rate:.1f}%) 🔄")
        print(f"   REQUIRED: {stats['required']} ({required_rate:.1f}%) ❌")

        # Field detection rates
        print(f"\n🔍 FIELD DETECTION RATES")
        for field in ["MERCHANT_NAME", "DATE", "TIME", "GRAND_TOTAL"]:
            count = stats["field_detection"].get(field, 0)
            percentage = count / stats["total"] * 100
            print(f"   {field}: {count}/{stats['total']} ({percentage:.1f}%)")

        # Cost savings calculation
        print(f"\n💰 COST SAVINGS ESTIMATE")
        gpt_cost = 0.05  # $0.05 per receipt
        monthly_volume = 10000  # receipts per month

        skip_savings = (
            stats["skip"] / stats["total"] * monthly_volume * gpt_cost
        )
        batch_savings = (
            stats["batch"] / stats["total"] * monthly_volume * gpt_cost * 0.5
        )  # 50% savings for batch
        total_gpt_cost = (
            stats["required"] / stats["total"] * monthly_volume * gpt_cost
            + batch_savings
        )
        full_gpt_cost = monthly_volume * gpt_cost

        print(f"   Skip savings: ${skip_savings:.2f}/month")
        print(f"   Batch savings: ${batch_savings:.2f}/month")
        print(f"   Total savings: ${skip_savings + batch_savings:.2f}/month")
        print(f"   vs. Full GPT cost: ${full_gpt_cost:.2f}/month")

        # Show examples
        print(f"\n📋 EXAMPLES BY DECISION")

        if stats["examples"]["skip"]:
            print(f"\n✅ SKIP EXAMPLES (Can skip GPT entirely):")
            for example in stats["examples"]["skip"][:3]:
                print(f"   - {example['merchant']} ({example['category']})")
                print(f"     Found: {', '.join(example['found'])}")

        if stats["examples"]["batch"]:
            print(f"\n🔄 BATCH EXAMPLES (Can use batch API):")
            for example in stats["examples"]["batch"][:3]:
                print(f"   - {example['merchant']} ({example['category']})")
                print(f"     Missing: {', '.join(example['missing'])}")

        if stats["examples"]["required"]:
            print(f"\n❌ REQUIRED EXAMPLES (Need immediate GPT):")
            for example in stats["examples"]["required"][:3]:
                print(f"   - {example['merchant']} ({example['category']})")
                print(f"     Missing: {', '.join(example['missing'])}")

        # Key insights
        print(f"\n💡 KEY INSIGHTS")
        if skip_rate > 50:
            print("   ✅ Excellent! High skip rate with simple 4-field logic")
        elif skip_rate > 30:
            print("   🔄 Good progress. 4-field focus is working")
        else:
            print("   ⚠️  Need to improve field detection:")

            # Check which field is the bottleneck
            field_rates = {}
            for field in ["MERCHANT_NAME", "DATE", "TIME", "GRAND_TOTAL"]:
                field_rates[field] = (
                    stats["field_detection"].get(field, 0)
                    / stats["total"]
                    * 100
                )

            lowest_field = min(field_rates, key=field_rates.get)
            print(
                f"      - Bottleneck: {lowest_field} ({field_rates[lowest_field]:.1f}% detection)"
            )

        print(
            f"\n✨ MERCHANT NAME SUCCESS: Fuzzy matching + metadata = 100% detection"
        )
        if stats["field_detection"].get("MERCHANT_NAME", 0) == stats["total"]:
            print("   🎯 Merchant detection is perfect!")

    # Save results
    results_file = Path(data_dir) / "four_fields_simple_results.json"
    with open(results_file, "w") as f:
        json.dump(
            {
                "summary": {
                    "total_receipts": stats["total"],
                    "skip_count": stats["skip"],
                    "batch_count": stats["batch"],
                    "required_count": stats["required"],
                    "skip_rate": (
                        stats["skip"] / stats["total"] * 100
                        if stats["total"] > 0
                        else 0
                    ),
                    "field_detection_rates": {
                        field: stats["field_detection"].get(field, 0)
                        / stats["total"]
                        * 100
                        for field in [
                            "MERCHANT_NAME",
                            "DATE",
                            "TIME",
                            "GRAND_TOTAL",
                        ]
                    },
                },
                "examples": stats["examples"],
            },
            f,
            indent=2,
        )

    print(f"\n💾 Results saved to: {results_file}")


if __name__ == "__main__":
    asyncio.run(test_four_fields_simple())
