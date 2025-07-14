#!/usr/bin/env python3
"""
Compare pattern detection results with validated receipt word labels.
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


async def compare_patterns_with_labels(data_dir: str = "./receipt_data_full"):
    """Compare pattern detection results with validated labels."""

    print("🔍 COMPARING PATTERN DETECTION VS VALIDATED LABELS")
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

    # Initialize components
    config = DecisionEngineConfig(enabled=True)
    orchestrator = DecisionEngineOrchestrator(
        config=config, optimization_level="advanced"
    )

    # Collect comparison statistics
    total_stats = {
        "total_receipts": 0,
        "total_labeled_words": 0,
        "total_pattern_detected": 0,
        "true_positives": 0,
        "false_positives": 0,
        "false_negatives": 0,
        "label_type_stats": defaultdict(lambda: {"total": 0, "detected": 0}),
        "pattern_type_stats": defaultdict(int),
        "essential_field_stats": defaultdict(
            lambda: {"labeled": 0, "detected": 0}
        ),
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

            if not receipts:
                print(f"   ⚠️  No receipt data found")
                continue

            receipt = receipts[0]
            image_id = receipt["image_id"]
            receipt_id = receipt["receipt_id"]

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

            # Create label lookup by word_id
            labels_by_word_id = {}
            for label in labels_for_receipt:
                word_id = label["word_id"]
                label_type = label["label_type"]
                labels_by_word_id[word_id] = label_type

            # Run pattern detection
            result = await orchestrator.process_receipt(
                words, {"image_id": image_id, "receipt_id": receipt_id}
            )

            # Extract pattern matches
            pattern_word_ids = set()
            pattern_by_word_id = {}

            for pattern_type, patterns in result.pattern_results.items():
                if pattern_type == "_metadata":
                    continue
                if isinstance(patterns, list):
                    for pattern in patterns:
                        if hasattr(pattern, "word"):
                            word_id = pattern.word.word_id
                            pattern_word_ids.add(word_id)
                            pattern_by_word_id[word_id] = pattern_type
                            total_stats["pattern_type_stats"][
                                pattern_type
                            ] += 1

            # Compare with labels
            receipt_stats = {
                "receipt_id": str(receipt_id),
                "total_words": len(words),
                "labeled_words": len(labels_by_word_id),
                "pattern_detected": len(pattern_word_ids),
                "true_positives": 0,
                "false_positives": 0,
                "false_negatives": 0,
                "precision": 0.0,
                "recall": 0.0,
                "f1_score": 0.0,
            }

            # Calculate true positives, false positives, false negatives
            # Check if patterns detected the labeled words
            for word_id, label_type in labels_by_word_id.items():
                total_stats["label_type_stats"][label_type]["total"] += 1
                if word_id in pattern_word_ids:
                    receipt_stats["true_positives"] += 1
                    total_stats["true_positives"] += 1
                    total_stats["label_type_stats"][label_type][
                        "detected"
                    ] += 1
                else:
                    receipt_stats["false_negatives"] += 1
                    total_stats["false_negatives"] += 1

            # Check for false positives (patterns detected on unlabeled words)
            for word_id in pattern_word_ids:
                if word_id not in labels_by_word_id:
                    receipt_stats["false_positives"] += 1
                    total_stats["false_positives"] += 1

            # Calculate metrics
            if receipt_stats["pattern_detected"] > 0:
                receipt_stats["precision"] = (
                    receipt_stats["true_positives"]
                    / receipt_stats["pattern_detected"]
                )

            if receipt_stats["labeled_words"] > 0:
                receipt_stats["recall"] = (
                    receipt_stats["true_positives"]
                    / receipt_stats["labeled_words"]
                )

            if receipt_stats["precision"] + receipt_stats["recall"] > 0:
                receipt_stats["f1_score"] = (
                    2
                    * (receipt_stats["precision"] * receipt_stats["recall"])
                    / (receipt_stats["precision"] + receipt_stats["recall"])
                )

            # Check essential fields
            essential_labels = {
                "MERCHANT_NAME": False,
                "DATE": False,
                "GRAND_TOTAL": False,
                "PRODUCT_NAME": False,
            }

            for label_type in labels_by_word_id.values():
                if label_type == "MERCHANT_NAME":
                    essential_labels["MERCHANT_NAME"] = True
                elif label_type in ["DATE", "TIME", "DATETIME"]:
                    essential_labels["DATE"] = True
                elif label_type == "GRAND_TOTAL":
                    essential_labels["GRAND_TOTAL"] = True
                elif label_type == "PRODUCT_NAME":
                    essential_labels["PRODUCT_NAME"] = True

            # Update essential field stats
            for field, is_labeled in essential_labels.items():
                if is_labeled:
                    total_stats["essential_field_stats"][field]["labeled"] += 1
                    # Check if detected by patterns
                    # Check if field was detected
                    if (
                        field == "MERCHANT_NAME"
                        and result.decision.merchant_name
                    ):
                        total_stats["essential_field_stats"][field][
                            "detected"
                        ] += 1
                    elif (
                        field == "DATE"
                        and "datetime" in result.pattern_results
                        and len(result.pattern_results["datetime"]) > 0
                    ):
                        total_stats["essential_field_stats"][field][
                            "detected"
                        ] += 1
                    elif (
                        field == "GRAND_TOTAL"
                        and "GRAND_TOTAL"
                        in result.decision.essential_fields_found
                    ):
                        total_stats["essential_field_stats"][field][
                            "detected"
                        ] += 1
                    elif (
                        field == "PRODUCT_NAME"
                        and "PRODUCT_NAME"
                        in result.decision.essential_fields_found
                    ):
                        total_stats["essential_field_stats"][field][
                            "detected"
                        ] += 1

            # Update totals
            total_stats["total_receipts"] += 1
            total_stats["total_labeled_words"] += receipt_stats[
                "labeled_words"
            ]
            total_stats["total_pattern_detected"] += receipt_stats[
                "pattern_detected"
            ]

            receipt_details.append(receipt_stats)

            print(
                f"   ✅ Precision: {receipt_stats['precision']:.1%}, Recall: {receipt_stats['recall']:.1%}, F1: {receipt_stats['f1_score']:.1%}"
            )

        except Exception as e:
            print(f"   ❌ Error: {e}")
            import traceback

            traceback.print_exc()
            continue

    # Generate report
    print(f"\n{'='*70}")
    print("📊 PATTERN DETECTION VS VALIDATED LABELS REPORT")
    print(f"{'='*70}")

    if total_stats["total_receipts"] > 0:
        # Overall metrics
        overall_precision = total_stats["true_positives"] / max(
            1, total_stats["total_pattern_detected"]
        )
        overall_recall = total_stats["true_positives"] / max(
            1, total_stats["total_labeled_words"]
        )
        overall_f1 = (
            2
            * (overall_precision * overall_recall)
            / max(0.001, overall_precision + overall_recall)
        )

        print(
            f"\n📈 OVERALL PERFORMANCE ({total_stats['total_receipts']} receipts)"
        )
        print(f"   Total labeled words: {total_stats['total_labeled_words']}")
        print(
            f"   Total patterns detected: {total_stats['total_pattern_detected']}"
        )
        print(f"   True positives: {total_stats['true_positives']}")
        print(f"   False positives: {total_stats['false_positives']}")
        print(f"   False negatives: {total_stats['false_negatives']}")
        print(f"   \n   Precision: {overall_precision:.1%}")
        print(f"   Recall: {overall_recall:.1%}")
        print(f"   F1 Score: {overall_f1:.1%}")

        # Label type performance
        print(f"\n🏷️ PERFORMANCE BY LABEL TYPE")
        for label_type, stats in sorted(
            total_stats["label_type_stats"].items()
        ):
            if stats["total"] > 0:
                detection_rate = stats["detected"] / stats["total"]
                print(
                    f"   {label_type}: {stats['detected']}/{stats['total']} detected ({detection_rate:.1%})"
                )

        # Pattern type distribution
        print(f"\n🔍 PATTERNS DETECTED BY TYPE")
        for pattern_type, count in sorted(
            total_stats["pattern_type_stats"].items(), key=lambda x: -x[1]
        ):
            print(f"   {pattern_type}: {count}")

        # Essential fields detection
        print(f"\n🔑 ESSENTIAL FIELDS DETECTION")
        for field, stats in total_stats["essential_field_stats"].items():
            if stats["labeled"] > 0:
                detection_rate = stats["detected"] / stats["labeled"]
                print(
                    f"   {field}: {stats['detected']}/{stats['labeled']} receipts ({detection_rate:.1%})"
                )

        # Best and worst performers
        sorted_details = sorted(
            receipt_details, key=lambda x: x["f1_score"], reverse=True
        )

        print(f"\n✅ BEST PERFORMING RECEIPTS (Top 5)")
        for detail in sorted_details[:5]:
            print(
                f"   {detail['receipt_id'][:12]}...: F1={detail['f1_score']:.1%} (P={detail['precision']:.1%}, R={detail['recall']:.1%})"
            )

        print(f"\n❌ WORST PERFORMING RECEIPTS (Bottom 5)")
        for detail in sorted_details[-5:]:
            print(
                f"   {detail['receipt_id'][:12]}...: F1={detail['f1_score']:.1%} (P={detail['precision']:.1%}, R={detail['recall']:.1%})"
            )

        # Save detailed results
        results_file = Path(data_dir) / "pattern_vs_labels_comparison.json"
        with open(results_file, "w") as f:
            json.dump(
                {
                    "summary": {
                        "total_receipts": total_stats["total_receipts"],
                        "overall_precision": overall_precision,
                        "overall_recall": overall_recall,
                        "overall_f1_score": overall_f1,
                        "true_positives": total_stats["true_positives"],
                        "false_positives": total_stats["false_positives"],
                        "false_negatives": total_stats["false_negatives"],
                        "label_type_performance": dict(
                            total_stats["label_type_stats"]
                        ),
                        "essential_field_detection": dict(
                            total_stats["essential_field_stats"]
                        ),
                    },
                    "receipts": receipt_details,
                },
                f,
                indent=2,
            )

        print(f"\n💾 Detailed results saved to: {results_file}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Compare patterns with labels"
    )
    parser.add_argument(
        "--data-dir",
        default="./receipt_data_full",
        help="Directory containing receipt data",
    )

    args = parser.parse_args()

    asyncio.run(compare_patterns_with_labels(args.data_dir))
