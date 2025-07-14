#!/usr/bin/env python3
"""
Test the Smart Decision Engine with production data from DynamoDB export.
"""

import asyncio
import json
from pathlib import Path
from typing import Any, Dict, List

from receipt_label.data.local_data_loader import (
    create_mock_receipt_from_export,
)
from receipt_label.decision_engine import (
    DecisionEngineConfig,
    process_receipt_with_decision_engine,
)


async def test_production_receipt(export_file: Path) -> Dict[str, Any]:
    """Test decision engine on a single production receipt."""

    # Load the export data
    with open(export_file, "r") as f:
        export_data = json.load(f)

    # Get receipt data from export
    receipts = export_data.get("receipts", [])
    receipt_words = export_data.get("receipt_words", [])
    receipt_lines = export_data.get("receipt_lines", [])

    if not receipts:
        return {
            "image_id": export_file.stem,
            "error": "No receipts found in export",
        }

    # Use the first receipt
    receipt = receipts[0]
    image_id = receipt["image_id"]
    receipt_id = receipt["receipt_id"]

    # Filter words and lines for this receipt
    words_for_receipt = [
        w
        for w in receipt_words
        if w["image_id"] == image_id and w["receipt_id"] == receipt_id
    ]
    lines_for_receipt = [
        l
        for l in receipt_lines
        if l["image_id"] == image_id and l["receipt_id"] == receipt_id
    ]

    print(
        f"📋 Receipt {image_id}/{receipt_id}: {len(words_for_receipt)} words, {len(lines_for_receipt)} lines"
    )

    # Create receipt objects using the helper function
    receipt_obj, words, lines = create_mock_receipt_from_export(
        {
            "receipt": receipt,
            "words": words_for_receipt,
            "lines": lines_for_receipt,
        }
    )

    # Test with decision engine
    config = DecisionEngineConfig(enabled=True, rollout_percentage=100.0)

    result = await process_receipt_with_decision_engine(
        words=words,
        config=config,
        client_manager=None,
        receipt_context={"image_id": image_id, "receipt_id": receipt_id},
    )

    decision = result.decision

    return {
        "image_id": image_id,
        "receipt_id": receipt_id,
        "merchant_name": decision.merchant_name,
        "decision": decision.action.value,
        "confidence": decision.confidence.value,
        "coverage_percentage": decision.coverage_percentage,
        "total_words": decision.total_words,
        "labeled_words": decision.labeled_words,
        "unlabeled_words": decision.unlabeled_meaningful_words,
        "essential_fields_found": list(decision.essential_fields_found),
        "essential_fields_missing": list(decision.essential_fields_missing),
        "processing_time_ms": result.processing_time_ms,
        "reasoning": decision.reasoning,
    }


async def main():
    """Test all production receipts."""

    print("🧪 SMART DECISION ENGINE - PRODUCTION DATA TESTING")
    print("=" * 60)

    # Find all export files
    data_dir = Path("./receipt_data_production")
    export_files = list(data_dir.glob("*.json"))

    if not export_files:
        print("❌ No export files found in ./receipt_data_production")
        return

    print(f"📁 Found {len(export_files)} export files")

    results = []

    # Test each export file
    for export_file in export_files:
        print(f"\n🔍 Testing {export_file.name}...")
        try:
            result = await test_production_receipt(export_file)
            results.append(result)

            if "error" not in result:
                print(
                    f"   ✅ {result['decision'].upper()}: {result['merchant_name']} - "
                    f"Coverage: {result['coverage_percentage']:.1f}%, "
                    f"Time: {result['processing_time_ms']:.1f}ms"
                )
                print(
                    f"   📊 {result['total_words']} total words, "
                    f"{result['labeled_words']} labeled, "
                    f"{result['unlabeled_words']} unlabeled"
                )
                print(f"   💭 {result['reasoning']}")
            else:
                print(f"   ❌ {result['error']}")

        except Exception as e:
            print(f"   ❌ Error: {e}")
            results.append({"image_id": export_file.stem, "error": str(e)})

    # Summary
    print(f"\n{'='*60}")
    print("📊 PRODUCTION DATA TEST SUMMARY")
    print(f"{'='*60}")

    successful_results = [r for r in results if "error" not in r]

    if successful_results:
        # Decision distribution
        skip_count = len(
            [r for r in successful_results if r["decision"] == "skip"]
        )
        batch_count = len(
            [r for r in successful_results if r["decision"] == "batch"]
        )
        required_count = len(
            [r for r in successful_results if r["decision"] == "required"]
        )

        print(f"Total receipts tested: {len(results)}")
        print(f"Successful tests: {len(successful_results)}")
        print(f"Failed tests: {len(results) - len(successful_results)}")
        print()
        print("🎯 DECISION DISTRIBUTION:")
        print(
            f"   SKIP (no GPT): {skip_count} ({skip_count/len(successful_results)*100:.1f}%)"
        )
        print(
            f"   BATCH (queue): {batch_count} ({batch_count/len(successful_results)*100:.1f}%)"
        )
        print(
            f"   REQUIRED (immediate): {required_count} ({required_count/len(successful_results)*100:.1f}%)"
        )

        # Performance metrics
        avg_coverage = sum(
            r["coverage_percentage"] for r in successful_results
        ) / len(successful_results)
        avg_processing_time = sum(
            r["processing_time_ms"] for r in successful_results
        ) / len(successful_results)
        avg_words = sum(r["total_words"] for r in successful_results) / len(
            successful_results
        )

        print()
        print("⚡ PERFORMANCE:")
        print(f"   Average coverage: {avg_coverage:.1f}%")
        print(f"   Average processing time: {avg_processing_time:.1f}ms")
        print(f"   Average words per receipt: {avg_words:.0f}")

        # Critical fields analysis
        critical_fields_success = len(
            [
                r
                for r in successful_results
                if len(
                    set(r["essential_fields_missing"]).intersection(
                        {"MERCHANT_NAME", "DATE", "GRAND_TOTAL"}
                    )
                )
                == 0
            ]
        )
        critical_fields_rate = (
            critical_fields_success / len(successful_results) * 100
        )

        print(f"   Critical fields success: {critical_fields_rate:.1f}%")

        # Target assessment
        skip_rate = skip_count / len(successful_results) * 100
        print()
        print("🎯 TARGET ASSESSMENT:")

        if skip_rate >= 84.0:
            print(f"   ✅ GPT Skip Rate: {skip_rate:.1f}% (≥84% target)")
        else:
            print(f"   🔄 GPT Skip Rate: {skip_rate:.1f}% (<84% target)")

        if critical_fields_rate >= 95.0:
            print(
                f"   ✅ Critical Fields: {critical_fields_rate:.1f}% (≥95% target)"
            )
        else:
            print(
                f"   🔄 Critical Fields: {critical_fields_rate:.1f}% (<95% target)"
            )

        if avg_processing_time < 10.0:
            print(
                f"   ✅ Processing Speed: {avg_processing_time:.1f}ms (<10ms target)"
            )
        else:
            print(
                f"   ⚠️ Processing Speed: {avg_processing_time:.1f}ms (≥10ms)"
            )

        print()
        if skip_rate >= 70 and critical_fields_rate >= 90:
            print(
                "🎉 EXCELLENT PERFORMANCE - Decision Engine working well with production data!"
            )
        elif skip_rate >= 50:
            print("✅ GOOD PERFORMANCE - Decision Engine shows promise")
        else:
            print(
                "🔄 BASELINE PERFORMANCE - Pattern detection needs enhancement"
            )

    else:
        print("❌ No successful tests - check data format and export")


if __name__ == "__main__":
    asyncio.run(main())
