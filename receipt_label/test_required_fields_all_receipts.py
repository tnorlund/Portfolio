#!/usr/bin/env python3
"""
Test pattern detection with 4 required fields against all local receipts.

Required fields:
- MERCHANT_NAME
- DATE
- TIME
- GRAND_TOTAL
"""

import asyncio
import json
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Tuple

from receipt_label.data.local_data_loader import (
    create_mock_receipt_from_export,
)
from receipt_label.decision_engine.enhanced_config import (
    EnhancedDecisionEngineConfig,
)
from receipt_label.decision_engine.enhanced_integration import (
    EnhancedDecisionEngineOrchestrator,
    EnhancedReceiptContext,
)


async def test_all_receipts(data_dir: str = "./receipt_data_with_labels"):
    """Test pattern detection with required fields on all receipts."""

    print("🧾 TESTING REQUIRED FIELDS ON ALL RECEIPTS")
    print("=" * 70)
    print("Required fields: MERCHANT_NAME, DATE, TIME, GRAND_TOTAL")
    print("=" * 70)

    data_path = Path(data_dir)
    receipt_files = list(data_path.glob("*.json"))
    receipt_files = [
        f
        for f in receipt_files
        if not f.name.startswith(
            ("manifest", "coverage", "pattern", "metadata")
        )
    ]

    print(f"\n📁 Found {len(receipt_files)} receipt files to analyze\n")

    # Configure with 4 required fields
    config = EnhancedDecisionEngineConfig(
        enabled=True,
        rollout_percentage=100.0,
        # Required fields
        require_merchant_name=True,
        require_date=True,
        require_total=True,
        # Note: TIME is included with DATE detection usually
        # Practical thresholds
        min_coverage_percentage=25.0,  # Lower threshold since we focus on required fields
        max_unlabeled_words=150,  # Very practical
        enable_adaptive_thresholds=True,
    )

    orchestrator = EnhancedDecisionEngineOrchestrator(config=config)

    # Statistics
    stats = {
        "total_receipts": 0,
        "receipts_with_metadata": 0,
        "decisions": defaultdict(int),
        "fields_found": defaultdict(int),
        "merchant_categories": defaultdict(int),
        "processing_times": [],
        "skip_candidates": [],  # Receipts that could skip GPT
        "batch_candidates": [],  # Receipts suitable for batch
        "required_receipts": [],  # Receipts that need immediate GPT
    }

    # Process each receipt
    for i, receipt_file in enumerate(receipt_files, 1):
        print(
            f"[{i}/{len(receipt_files)}] Processing {receipt_file.name[:40]}..."
        )

        try:
            start_time = time.time()

            # Load receipt data
            with open(receipt_file, "r") as f:
                export_data = json.load(f)

            # Get data components
            receipts = export_data.get("receipts", [])
            receipt_words = export_data.get("receipt_words", [])
            receipt_metadatas = export_data.get("receipt_metadatas", [])

            if not receipts:
                print(f"   ⚠️  No receipt data found")
                continue

            receipt = receipts[0]
            image_id = receipt["image_id"]
            receipt_id = receipt["receipt_id"]

            # Filter words for this receipt
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

            # Process with metadata if available
            if receipt_metadatas:
                metadata = receipt_metadatas[0]
                stats["receipts_with_metadata"] += 1
                if metadata.get("merchant_category"):
                    stats["merchant_categories"][
                        metadata["merchant_category"]
                    ] += 1

                # Use enhanced context with metadata
                enhanced_context = EnhancedReceiptContext(
                    image_id=image_id,
                    receipt_id=receipt_id,
                    merchant_name=metadata.get("merchant_name"),
                    merchant_category=metadata.get("merchant_category"),
                    address=metadata.get("address"),
                    phone_number=metadata.get("phone_number"),
                    place_id=metadata.get("place_id"),
                    validated_by=metadata.get("validated_by"),
                )

                result = await orchestrator.process_receipt_with_metadata(
                    words, enhanced_context
                )
            else:
                # Process without metadata
                basic_context = {
                    "image_id": image_id,
                    "receipt_id": receipt_id,
                }
                result = await orchestrator.process_receipt(
                    words, basic_context
                )

            # Record processing time
            processing_time = (time.time() - start_time) * 1000
            stats["processing_times"].append(processing_time)

            # Record decision
            decision = result.decision.action.value
            stats["decisions"][decision] += 1

            # Check which fields were found
            essential_fields = result.decision.essential_fields_found
            if isinstance(essential_fields, dict):
                for field in ["MERCHANT_NAME", "DATE", "TIME", "GRAND_TOTAL"]:
                    if essential_fields.get(field):
                        stats["fields_found"][field] += 1
            elif isinstance(essential_fields, set):
                for field in ["MERCHANT_NAME", "DATE", "TIME", "GRAND_TOTAL"]:
                    if field in essential_fields:
                        stats["fields_found"][field] += 1

            # Check for TIME separately if combined with DATE
            if (
                "datetime" in result.pattern_results
                and result.pattern_results["datetime"]
            ):
                # Many datetime patterns include time
                stats["fields_found"]["TIME"] += 1

            # Categorize receipt
            receipt_info = {
                "file": receipt_file.name,
                "merchant": result.decision.merchant_name,
                "category": (
                    metadata.get("merchant_category")
                    if receipt_metadatas
                    else None
                ),
                "coverage": result.decision.coverage_percentage,
                "decision": decision,
                "missing_fields": (
                    list(result.decision.essential_fields_missing)
                    if hasattr(result.decision, "essential_fields_missing")
                    else []
                ),
            }

            if decision == "skip":
                stats["skip_candidates"].append(receipt_info)
                print(f"   ✅ SKIP - All required fields found!")
            elif decision == "batch":
                stats["batch_candidates"].append(receipt_info)
                print(
                    f"   🔄 BATCH - Missing some fields: {receipt_info['missing_fields']}"
                )
            else:
                stats["required_receipts"].append(receipt_info)
                print(f"   ❌ REQUIRED - {result.decision.reasoning}")

            stats["total_receipts"] += 1

        except Exception as e:
            print(f"   ❌ Error: {e}")
            import traceback

            traceback.print_exc()
            continue

    # Generate comprehensive report
    print(f"\n{'='*70}")
    print("📊 COMPREHENSIVE ANALYSIS REPORT")
    print(f"{'='*70}")

    if stats["total_receipts"] > 0:
        # Overall statistics
        print(f"\n📈 OVERALL STATISTICS ({stats['total_receipts']} receipts)")
        print(
            f"   Receipts with metadata: {stats['receipts_with_metadata']} ({stats['receipts_with_metadata']/stats['total_receipts']*100:.1f}%)"
        )

        # Decision breakdown
        print(f"\n🎯 DECISION BREAKDOWN")
        total_decisions = sum(stats["decisions"].values())
        for decision, count in sorted(stats["decisions"].items()):
            percentage = (
                (count / total_decisions * 100) if total_decisions > 0 else 0
            )
            print(f"   {decision.upper()}: {count} ({percentage:.1f}%)")

        # Skip rate - the key metric!
        skip_rate = (
            stats["decisions"].get("skip", 0) / stats["total_receipts"] * 100
        )
        print(f"\n💰 SKIP RATE: {skip_rate:.1f}% (Goal: 70%+)")

        # Field detection rates
        print(f"\n🔍 REQUIRED FIELD DETECTION RATES")
        for field in ["MERCHANT_NAME", "DATE", "TIME", "GRAND_TOTAL"]:
            count = stats["fields_found"].get(field, 0)
            percentage = count / stats["total_receipts"] * 100
            print(
                f"   {field}: {count}/{stats['total_receipts']} ({percentage:.1f}%)"
            )

        # All fields found rate
        all_fields_count = len([r for r in stats["skip_candidates"]])
        all_fields_rate = all_fields_count / stats["total_receipts"] * 100
        print(
            f"\n   All 4 fields found: {all_fields_count}/{stats['total_receipts']} ({all_fields_rate:.1f}%)"
        )

        # Merchant categories
        if stats["merchant_categories"]:
            print(f"\n🏪 MERCHANT CATEGORIES")
            for category, count in sorted(
                stats["merchant_categories"].items(), key=lambda x: -x[1]
            ):
                print(f"   {category}: {count}")

        # Performance metrics
        if stats["processing_times"]:
            avg_time = sum(stats["processing_times"]) / len(
                stats["processing_times"]
            )
            max_time = max(stats["processing_times"])
            min_time = min(stats["processing_times"])
            print(f"\n⚡ PERFORMANCE")
            print(f"   Average processing time: {avg_time:.1f}ms")
            print(f"   Min/Max: {min_time:.1f}ms / {max_time:.1f}ms")

        # Cost analysis
        print(f"\n💸 COST ANALYSIS (per 10,000 receipts/month)")
        gpt_cost_per_receipt = 0.05  # $0.05 per receipt for GPT-4

        skip_count = stats["decisions"].get("skip", 0)
        batch_count = stats["decisions"].get("batch", 0)
        required_count = stats["decisions"].get("required", 0)

        # Cost calculation
        skip_savings = skip_count * gpt_cost_per_receipt
        batch_savings = (
            batch_count * gpt_cost_per_receipt * 0.5
        )  # 50% savings for batch
        total_cost = (
            required_count * gpt_cost_per_receipt
            + batch_count * gpt_cost_per_receipt * 0.5
        )

        monthly_multiplier = 10000 / stats["total_receipts"]

        print(
            f"   Skip savings: ${skip_savings * monthly_multiplier:.2f}/month"
        )
        print(
            f"   Batch savings: ${batch_savings * monthly_multiplier:.2f}/month"
        )
        print(
            f"   Total GPT cost: ${total_cost * monthly_multiplier:.2f}/month"
        )
        print(
            f"   vs. Processing all: ${10000 * gpt_cost_per_receipt:.2f}/month"
        )
        print(
            f"   TOTAL SAVINGS: ${(skip_savings + batch_savings) * monthly_multiplier:.2f}/month"
        )

        # Show examples of each category
        print(f"\n📋 EXAMPLE RECEIPTS BY DECISION")

        if stats["skip_candidates"]:
            print(f"\n✅ SKIP CANDIDATES (Can skip GPT entirely):")
            for receipt in stats["skip_candidates"][:3]:
                print(
                    f"   - {receipt['merchant']} ({receipt['category']}) - {receipt['coverage']:.1f}% coverage"
                )

        if stats["batch_candidates"]:
            print(f"\n🔄 BATCH CANDIDATES (Can use cheaper batch API):")
            for receipt in stats["batch_candidates"][:3]:
                print(
                    f"   - {receipt['merchant']} - Missing: {', '.join(receipt['missing_fields'])}"
                )

        if stats["required_receipts"]:
            print(f"\n❌ REQUIRED (Need immediate GPT processing):")
            for receipt in stats["required_receipts"][:3]:
                print(f"   - {receipt['file'][:40]} - {receipt['decision']}")

        # Summary and recommendations
        print(f"\n💡 RECOMMENDATIONS")
        if skip_rate < 30:
            print("   ⚠️  Skip rate is low. Consider:")
            print("      - Improving fuzzy merchant matching")
            print("      - Adding more datetime patterns")
            print("      - Better total amount detection")
        elif skip_rate < 70:
            print("   🔄 Skip rate is moderate. To improve:")
            print("      - Enhance merchant-specific patterns")
            print("      - Improve currency classification for totals")
            print("      - Add time extraction from datetime patterns")
        else:
            print(
                "   ✅ Excellent skip rate! Pattern detection is working well."
            )

        # Save detailed results
        results_file = Path(data_dir) / "required_fields_analysis.json"
        with open(results_file, "w") as f:
            json.dump(
                {
                    "summary": {
                        "total_receipts": stats["total_receipts"],
                        "skip_rate": skip_rate,
                        "decisions": dict(stats["decisions"]),
                        "fields_found": dict(stats["fields_found"]),
                        "merchant_categories": dict(
                            stats["merchant_categories"]
                        ),
                    },
                    "skip_candidates": stats["skip_candidates"],
                    "batch_candidates": stats["batch_candidates"],
                    "required_receipts": stats["required_receipts"],
                },
                f,
                indent=2,
            )

        print(f"\n💾 Detailed results saved to: {results_file}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Test required fields on all receipts"
    )
    parser.add_argument(
        "--data-dir",
        default="./receipt_data_with_labels",
        help="Directory containing receipt data",
    )

    args = parser.parse_args()

    asyncio.run(test_all_receipts(args.data_dir))
