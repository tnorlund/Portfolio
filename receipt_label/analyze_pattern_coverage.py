#!/usr/bin/env python3
"""
Analyze pattern coverage across all downloaded receipts.
"""

import asyncio
import json
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Tuple

from receipt_label.data.local_data_loader import (
    create_mock_receipt_from_export,
)
from receipt_label.decision_engine import DecisionEngineConfig
from receipt_label.decision_engine.integration import (
    DecisionEngineOrchestrator,
)
from receipt_label.pattern_detection import ParallelPatternOrchestrator


async def analyze_all_receipts(data_dir: str = "./receipt_data_full"):
    """Analyze pattern coverage for all receipts."""

    print("📊 ANALYZING PATTERN COVERAGE ACROSS ALL RECEIPTS")
    print("=" * 70)

    data_path = Path(data_dir)
    receipt_files = list(data_path.glob("*.json"))
    receipt_files = [f for f in receipt_files if f.name != "manifest.json"]

    print(f"📁 Found {len(receipt_files)} receipt files to analyze\n")

    # Initialize components
    config = DecisionEngineConfig(enabled=True, rollout_percentage=100.0)
    orchestrator = DecisionEngineOrchestrator(
        config=config, optimization_level="advanced"
    )

    # Collect statistics
    coverage_stats = []
    pattern_counts = defaultdict(int)
    decision_counts = defaultdict(int)
    merchant_stats = defaultdict(int)
    missing_fields_stats = defaultdict(int)

    # Process each receipt
    for i, receipt_file in enumerate(receipt_files, 1):
        print(f"[{i}/{len(receipt_files)}] Processing {receipt_file.name}...")

        try:
            # Load receipt data
            with open(receipt_file, "r") as f:
                export_data = json.load(f)

            # Get the first receipt
            receipts = export_data.get("receipts", [])
            receipt_words = export_data.get("receipt_words", [])
            receipt_lines = export_data.get("receipt_lines", [])

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
                    "lines": receipt_lines,
                }
            )

            # Process with decision engine
            result = await orchestrator.process_receipt(
                words, {"image_id": image_id, "receipt_id": receipt_id}
            )

            # Collect statistics
            decision = result.decision
            coverage = decision.coverage_percentage

            coverage_stats.append(
                {
                    "receipt_id": receipt_id,
                    "coverage": coverage,
                    "total_words": decision.total_words,
                    "labeled_words": decision.labeled_words,
                    "unlabeled_words": decision.unlabeled_meaningful_words,
                    "decision": decision.action.value,
                    "merchant": decision.merchant_name,
                }
            )

            # Count patterns by type
            for pattern_type, patterns in result.pattern_results.items():
                if pattern_type != "_metadata" and isinstance(patterns, list):
                    pattern_counts[pattern_type] += len(patterns)

            # Count decisions
            decision_counts[decision.action.value] += 1

            # Track merchants
            if decision.merchant_name:
                merchant_stats[decision.merchant_name] += 1
            else:
                merchant_stats["UNKNOWN"] += 1

            # Track missing fields
            for field in decision.essential_fields_missing:
                missing_fields_stats[field] += 1

            print(
                f"   ✅ Coverage: {coverage:.1f}%, Decision: {decision.action.value}"
            )

        except Exception as e:
            print(f"   ❌ Error: {e}")
            continue

    # Generate report
    print(f"\n{'='*70}")
    print("📊 COVERAGE ANALYSIS REPORT")
    print(f"{'='*70}")

    # Overall statistics
    total_receipts = len(coverage_stats)
    if total_receipts > 0:
        avg_coverage = (
            sum(r["coverage"] for r in coverage_stats) / total_receipts
        )
        avg_labeled = (
            sum(r["labeled_words"] for r in coverage_stats) / total_receipts
        )
        avg_total = (
            sum(r["total_words"] for r in coverage_stats) / total_receipts
        )

        print(f"\n📈 OVERALL STATISTICS ({total_receipts} receipts)")
        print(f"   Average coverage: {avg_coverage:.1f}%")
        print(f"   Average labeled words: {avg_labeled:.1f} / {avg_total:.1f}")

        # Decision distribution
        print(f"\n🎯 DECISION DISTRIBUTION")
        for decision, count in sorted(decision_counts.items()):
            percentage = (count / total_receipts) * 100
            print(f"   {decision}: {count} ({percentage:.1f}%)")

        # Pattern type distribution
        print(f"\n🔍 PATTERN TYPE DISTRIBUTION")
        total_patterns = sum(pattern_counts.values())
        for pattern_type, count in sorted(
            pattern_counts.items(), key=lambda x: -x[1]
        ):
            avg_per_receipt = count / total_receipts
            print(
                f"   {pattern_type}: {count} total ({avg_per_receipt:.1f} per receipt)"
            )

        # Missing fields analysis
        print(f"\n❌ MISSING ESSENTIAL FIELDS")
        for field, count in sorted(
            missing_fields_stats.items(), key=lambda x: -x[1]
        ):
            percentage = (count / total_receipts) * 100
            print(f"   {field}: {count} receipts ({percentage:.1f}%)")

        # Merchant distribution (top 10)
        print(f"\n🏪 TOP MERCHANTS")
        for merchant, count in sorted(
            merchant_stats.items(), key=lambda x: -x[1]
        )[:10]:
            percentage = (count / total_receipts) * 100
            print(f"   {merchant}: {count} ({percentage:.1f}%)")

        # Coverage distribution
        print(f"\n📊 COVERAGE DISTRIBUTION")
        coverage_buckets = defaultdict(int)
        for stat in coverage_stats:
            bucket = int(stat["coverage"] / 10) * 10
            coverage_buckets[bucket] += 1

        for bucket in sorted(coverage_buckets.keys()):
            count = coverage_buckets[bucket]
            percentage = (count / total_receipts) * 100
            print(
                f"   {bucket}-{bucket+9}%: {count} receipts ({percentage:.1f}%)"
            )

        # Find best and worst performing receipts
        sorted_stats = sorted(
            coverage_stats, key=lambda x: x["coverage"], reverse=True
        )

        print(f"\n✅ BEST COVERAGE (Top 5)")
        for stat in sorted_stats[:5]:
            receipt_id_str = str(stat["receipt_id"])
            print(
                f"   {receipt_id_str[:12]}...: {stat['coverage']:.1f}% ({stat['labeled_words']}/{stat['total_words']} words)"
            )

        print(f"\n❌ WORST COVERAGE (Bottom 5)")
        for stat in sorted_stats[-5:]:
            receipt_id_str = str(stat["receipt_id"])
            print(
                f"   {receipt_id_str[:12]}...: {stat['coverage']:.1f}% ({stat['labeled_words']}/{stat['total_words']} words)"
            )

        # Save detailed results
        results_file = Path(data_dir) / "coverage_analysis.json"
        with open(results_file, "w") as f:
            json.dump(
                {
                    "summary": {
                        "total_receipts": total_receipts,
                        "average_coverage": avg_coverage,
                        "average_labeled_words": avg_labeled,
                        "average_total_words": avg_total,
                        "decision_distribution": dict(decision_counts),
                        "pattern_distribution": dict(pattern_counts),
                        "missing_fields": dict(missing_fields_stats),
                    },
                    "receipts": coverage_stats,
                },
                f,
                indent=2,
            )

        print(f"\n💾 Detailed results saved to: {results_file}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Analyze pattern coverage")
    parser.add_argument(
        "--data-dir",
        default="./receipt_data_full",
        help="Directory containing receipt data",
    )

    args = parser.parse_args()

    asyncio.run(analyze_all_receipts(args.data_dir))
