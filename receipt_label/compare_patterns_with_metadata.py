#!/usr/bin/env python3
"""
Compare pattern detection results with validated receipt word labels,
leveraging Google Places metadata for enhanced pattern detection.
"""

import asyncio
import json
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Set, Tuple

from receipt_label.data.local_data_loader import (
    create_mock_receipt_from_export,
)
from receipt_label.decision_engine import DecisionEngineConfig
from receipt_label.decision_engine.integration import (
    DecisionEngineOrchestrator,
)
from receipt_label.pattern_detection import ParallelPatternOrchestrator


async def compare_patterns_with_metadata(
    data_dir: str = "./receipt_data_with_labels",
):
    """Compare pattern detection results with validated labels using metadata."""

    print("🔍 COMPARING PATTERN DETECTION WITH GOOGLE PLACES METADATA")
    print("=" * 70)

    data_path = Path(data_dir)
    receipt_files = list(data_path.glob("*.json"))
    receipt_files = [
        f
        for f in receipt_files
        if f.name != "manifest.json"
        and f.name != "coverage_analysis.json"
        and f.name != "pattern_vs_labels_comparison.json"
    ]

    print(f"📁 Found {len(receipt_files)} receipt files to analyze\n")

    # Initialize components with 100% rollout for testing
    config = DecisionEngineConfig(enabled=True, rollout_percentage=100.0)
    orchestrator = DecisionEngineOrchestrator(
        config=config, optimization_level="advanced"
    )

    # Collect comparison statistics
    total_stats = {
        "total_receipts": 0,
        "receipts_with_metadata": 0,
        "metadata_impact": {
            "merchant_detection_before": 0,
            "merchant_detection_after": 0,
            "phone_detection_before": 0,
            "phone_detection_after": 0,
            "address_detection_before": 0,
            "address_detection_after": 0,
        },
        "merchant_categories": defaultdict(int),
        "decision_impact": {
            "skip_without_metadata": 0,
            "skip_with_metadata": 0,
            "batch_without_metadata": 0,
            "batch_with_metadata": 0,
            "required_without_metadata": 0,
            "required_with_metadata": 0,
        },
    }

    receipt_details = []

    # Process each receipt
    for i, receipt_file in enumerate(receipt_files, 1):
        print(f"[{i}/{len(receipt_files)}] Processing {receipt_file.name}...")

        try:
            # Load receipt data
            with open(receipt_file, "r") as f:
                export_data = json.load(f)

            # Get receipt data
            receipts = export_data.get("receipts", [])
            receipt_words = export_data.get("receipt_words", [])
            receipt_labels = export_data.get("receipt_word_labels", [])
            receipt_lines = export_data.get("receipt_lines", [])
            receipt_metadatas = export_data.get("receipt_metadatas", [])

            if not receipts:
                print(f"   ⚠️  No receipt data found")
                continue

            receipt = receipts[0]
            image_id = receipt["image_id"]
            receipt_id = receipt["receipt_id"]

            # Get metadata if available
            metadata = None
            if receipt_metadatas:
                metadata = receipt_metadatas[0]
                total_stats["receipts_with_metadata"] += 1
                if metadata.get("merchant_category"):
                    total_stats["merchant_categories"][
                        metadata["merchant_category"]
                    ] += 1
                print(
                    f"   🏪 Metadata: merchant='{metadata.get('merchant_name')}', category='{metadata.get('merchant_category')}'"
                )
            else:
                print(f"   ⚠️  No metadata found")

            # Filter data for this receipt
            words_for_receipt = [
                w
                for w in receipt_words
                if w["image_id"] == image_id and w["receipt_id"] == receipt_id
            ]

            labels_for_receipt = [
                l
                for l in receipt_labels
                if l["image_id"] == image_id and l["receipt_id"] == receipt_id
            ]

            # Create receipt objects
            receipt_obj, words, lines = create_mock_receipt_from_export(
                {
                    "receipt": receipt,
                    "words": words_for_receipt,
                    "lines": receipt_lines,
                }
            )

            # Run pattern detection WITHOUT metadata
            result_no_metadata = await orchestrator.process_receipt(
                words, {"image_id": image_id, "receipt_id": receipt_id}
            )

            # Run pattern detection WITH metadata
            receipt_context_with_metadata = {
                "image_id": image_id,
                "receipt_id": receipt_id,
                "merchant_name": (
                    metadata.get("merchant_name") if metadata else None
                ),
                "merchant_category": (
                    metadata.get("merchant_category") if metadata else None
                ),
                "address": metadata.get("address") if metadata else None,
                "phone_number": (
                    metadata.get("phone_number") if metadata else None
                ),
                "place_id": metadata.get("place_id") if metadata else None,
            }

            result_with_metadata = await orchestrator.process_receipt(
                words, receipt_context_with_metadata
            )

            # Compare results
            print(
                f"   📊 Without metadata: {result_no_metadata.decision.action.value}"
            )
            print(
                f"   📊 With metadata: {result_with_metadata.decision.action.value}"
            )

            # Track decision changes
            total_stats["decision_impact"][
                f"{result_no_metadata.decision.action.value.lower()}_without_metadata"
            ] += 1
            total_stats["decision_impact"][
                f"{result_with_metadata.decision.action.value.lower()}_with_metadata"
            ] += 1

            # Track field detection improvements
            if (
                not result_no_metadata.decision.merchant_name
                and result_with_metadata.decision.merchant_name
            ):
                total_stats["metadata_impact"]["merchant_detection_after"] += 1
            if result_no_metadata.decision.merchant_name:
                total_stats["metadata_impact"][
                    "merchant_detection_before"
                ] += 1

            # Check for phone and address detection
            phone_before = any(
                "phone" in str(p).lower()
                for p in result_no_metadata.pattern_results.values()
            )
            phone_after = any(
                "phone" in str(p).lower()
                for p in result_with_metadata.pattern_results.values()
            )
            if phone_before:
                total_stats["metadata_impact"]["phone_detection_before"] += 1
            if phone_after:
                total_stats["metadata_impact"]["phone_detection_after"] += 1

            # Store details
            receipt_detail = {
                "receipt_id": str(receipt_id),
                "has_metadata": metadata is not None,
                "merchant_category": (
                    metadata.get("merchant_category") if metadata else None
                ),
                "decision_without_metadata": result_no_metadata.decision.action.value,
                "decision_with_metadata": result_with_metadata.decision.action.value,
                "merchant_detected_without": bool(
                    result_no_metadata.decision.merchant_name
                ),
                "merchant_detected_with": bool(
                    result_with_metadata.decision.merchant_name
                ),
                "coverage_without": (
                    result_no_metadata.decision.pattern_coverage_percentage
                    if hasattr(
                        result_no_metadata.decision,
                        "pattern_coverage_percentage",
                    )
                    else 0.0
                ),
                "coverage_with": (
                    result_with_metadata.decision.pattern_coverage_percentage
                    if hasattr(
                        result_with_metadata.decision,
                        "pattern_coverage_percentage",
                    )
                    else 0.0
                ),
                "merchant_name": (
                    metadata.get("merchant_name") if metadata else None
                ),
            }

            receipt_details.append(receipt_detail)

            # Update totals
            total_stats["total_receipts"] += 1

        except Exception as e:
            print(f"   ❌ Error: {e}")
            import traceback

            traceback.print_exc()
            continue

    # Generate report
    print(f"\n{'='*70}")
    print("📊 METADATA IMPACT ANALYSIS REPORT")
    print(f"{'='*70}")

    if total_stats["total_receipts"] > 0:
        metadata_percentage = (
            total_stats["receipts_with_metadata"]
            / total_stats["total_receipts"]
        ) * 100
        print(
            f"\n📈 OVERALL STATISTICS ({total_stats['total_receipts']} receipts)"
        )
        print(
            f"   Receipts with metadata: {total_stats['receipts_with_metadata']} ({metadata_percentage:.1f}%)"
        )

        print(f"\n🏪 MERCHANT CATEGORIES")
        for category, count in sorted(
            total_stats["merchant_categories"].items(), key=lambda x: -x[1]
        ):
            print(f"   {category}: {count}")

        print(f"\n🎯 DECISION IMPACT")
        skip_improvement = (
            total_stats["decision_impact"]["skip_with_metadata"]
            - total_stats["decision_impact"]["skip_without_metadata"]
        )
        print(f"   SKIP decisions:")
        print(
            f"      Without metadata: {total_stats['decision_impact']['skip_without_metadata']}"
        )
        print(
            f"      With metadata: {total_stats['decision_impact']['skip_with_metadata']}"
        )
        print(f"      Improvement: +{skip_improvement}")

        required_reduction = (
            total_stats["decision_impact"]["required_without_metadata"]
            - total_stats["decision_impact"]["required_with_metadata"]
        )
        print(f"   REQUIRED decisions:")
        print(
            f"      Without metadata: {total_stats['decision_impact']['required_without_metadata']}"
        )
        print(
            f"      With metadata: {total_stats['decision_impact']['required_with_metadata']}"
        )
        print(f"      Reduction: -{required_reduction}")

        print(f"\n🔍 FIELD DETECTION IMPACT")
        merchant_improvement = (
            total_stats["metadata_impact"]["merchant_detection_after"]
            - total_stats["metadata_impact"]["merchant_detection_before"]
        )
        print(f"   Merchant detection:")
        print(
            f"      Without metadata: {total_stats['metadata_impact']['merchant_detection_before']}"
        )
        print(
            f"      With metadata: {total_stats['metadata_impact']['merchant_detection_after']}"
        )
        print(f"      Improvement: +{merchant_improvement}")

        # Calculate potential cost savings
        skip_rate_without = (
            total_stats["decision_impact"]["skip_without_metadata"]
            / total_stats["total_receipts"]
            * 100
        )
        skip_rate_with = (
            total_stats["decision_impact"]["skip_with_metadata"]
            / total_stats["total_receipts"]
            * 100
        )

        print(f"\n💰 COST REDUCTION POTENTIAL")
        print(f"   Skip rate without metadata: {skip_rate_without:.1f}%")
        print(f"   Skip rate with metadata: {skip_rate_with:.1f}%")
        print(
            f"   Improvement: +{skip_rate_with - skip_rate_without:.1f} percentage points"
        )

        # Estimate GPT cost reduction
        gpt_cost_per_receipt = 0.05  # $0.05 per receipt
        receipts_per_month = 10000  # example volume

        cost_without = (
            receipts_per_month
            * (1 - skip_rate_without / 100)
            * gpt_cost_per_receipt
        )
        cost_with = (
            receipts_per_month
            * (1 - skip_rate_with / 100)
            * gpt_cost_per_receipt
        )
        savings = cost_without - cost_with

        print(f"\n   Estimated monthly savings (10K receipts/month):")
        print(f"      Cost without metadata: ${cost_without:.2f}")
        print(f"      Cost with metadata: ${cost_with:.2f}")
        print(f"      Monthly savings: ${savings:.2f}")
        print(f"      Annual savings: ${savings * 12:.2f}")

        # Save detailed results
        results_file = Path(data_dir) / "metadata_impact_analysis.json"
        with open(results_file, "w") as f:
            json.dump(
                {
                    "summary": total_stats,
                    "receipts": receipt_details,
                    "cost_analysis": {
                        "skip_rate_without_metadata": skip_rate_without,
                        "skip_rate_with_metadata": skip_rate_with,
                        "monthly_savings_estimate": savings,
                        "annual_savings_estimate": savings * 12,
                    },
                },
                f,
                indent=2,
            )

        print(f"\n💾 Detailed results saved to: {results_file}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Compare pattern detection with and without metadata"
    )
    parser.add_argument(
        "--data-dir",
        default="./receipt_data_with_labels",
        help="Directory containing receipt data with metadata",
    )

    args = parser.parse_args()

    asyncio.run(compare_patterns_with_metadata(args.data_dir))
