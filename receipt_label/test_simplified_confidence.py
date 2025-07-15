#!/usr/bin/env python3
"""
Test simplified confidence thresholds with all Phase 2 features.
Remove the overcomplicated single-solution requirement.
"""

import os
import time
import asyncio
from pathlib import Path
from collections import Counter

# Set up environment
os.environ["OPENAI_API_KEY"] = "sk-dummy"

from receipt_label.spatial.math_solver_detector import MathSolverDetector
from receipt_label.spatial.vertical_alignment_detector import (
    VerticalAlignmentDetector,
)
from receipt_label.pattern_detection.orchestrator import (
    ParallelPatternOrchestrator,
)
import json
from receipt_dynamo.entities.receipt_word import ReceiptWord


def load_receipt_words(file_path: Path):
    """Load words from receipt."""
    try:
        with open(file_path, "r") as f:
            data = json.load(f)

        words = []
        for word_data in data.get("words", []):
            word = ReceiptWord(
                image_id=word_data["image_id"],
                line_id=word_data["line_id"],
                word_id=word_data["word_id"],
                text=word_data["text"],
                bounding_box=word_data["bounding_box"],
                top_right=word_data["top_right"],
                top_left=word_data["top_left"],
                bottom_right=word_data["bottom_right"],
                bottom_left=word_data["bottom_left"],
                angle_degrees=word_data.get("angle_degrees", 0.0),
                angle_radians=word_data.get("angle_radians", 0.0),
                confidence=word_data["confidence"],
                extracted_data=word_data.get("extracted_data", {}),
                receipt_id=int(word_data.get("receipt_id", 1)),
            )
            words.append(word)

        # Get merchant from metadata
        merchant = "Unknown"
        receipt_metadatas = data.get("receipt_metadatas", [])
        if receipt_metadatas:
            merchant = receipt_metadatas[0].get("merchant_name", "Unknown")

        return words, merchant
    except Exception as e:
        return [], f"Load error: {str(e)[:50]}..."


def classify_confidence_simplified(solutions, spatial_analysis):
    """
    SIMPLIFIED confidence classification removing overcomplicated requirements.

    Key changes:
    - Remove single solution requirement (unrealistic)
    - Use spatial analysis quality more heavily
    - Consider font analysis and alignment tightness
    """
    if not solutions:
        return "no_solution"

    best_solution = max(solutions, key=lambda s: s.confidence)

    # Extract spatial metrics
    column_confidence = spatial_analysis.get("best_column_confidence", 0)
    x_tightness = spatial_analysis.get("x_alignment_tightness", 0)
    has_large_fonts = spatial_analysis.get("has_large_font_patterns", False)
    font_consistency = spatial_analysis.get("font_consistency_confidence", 0)

    # SIMPLIFIED THRESHOLDS
    # High confidence if:
    # - Good mathematical solution (>= 0.8)
    # - Good spatial alignment (>= 0.7)
    # - Bonus for Phase 2 features

    # Calculate combined score
    math_score = best_solution.confidence
    spatial_score = column_confidence

    # Phase 2 bonuses
    if x_tightness > 0.9:
        spatial_score *= 1.1  # Tight alignment bonus
    if has_large_fonts:
        spatial_score *= 1.1  # Large font detection bonus
    if font_consistency > 0.6:
        spatial_score *= 1.05  # Font consistency bonus

    # Combine scores
    combined_score = (math_score + spatial_score) / 2

    # Simplified classification
    if combined_score >= 0.85:
        return "high_confidence"
    elif combined_score >= 0.7:
        return "medium_confidence"
    elif combined_score >= 0.5:
        return "low_confidence"
    else:
        return "no_solution"


async def test_simplified_confidence():
    """Test simplified confidence thresholds with Phase 2 features."""

    print("🚀 TESTING SIMPLIFIED CONFIDENCE WITH PHASE 2 FEATURES")
    print("=" * 60)

    # Find ALL receipt files
    receipt_dir = Path("./receipt_data_with_labels")
    all_files = list(receipt_dir.glob("*.json"))

    print(f"📄 Testing ALL {len(all_files)} receipts")
    print(f"🎯 Using simplified confidence thresholds")
    print(f"✨ All Phase 2 features enabled")

    # Create detectors - all Phase 2 features enabled
    pattern_orchestrator = ParallelPatternOrchestrator(
        timeout=10.0, use_adaptive_selection=False
    )
    alignment_detector = VerticalAlignmentDetector(
        alignment_tolerance=0.02, use_enhanced_clustering=True
    )
    math_solver = MathSolverDetector(
        tolerance=0.02, max_solutions=50, use_numpy_optimization=True
    )

    results = []
    total_start_time = time.time()

    print("\n🔄 Processing receipts...")

    for i, file_path in enumerate(all_files):
        if i % 20 == 0:
            elapsed = time.time() - total_start_time
            if i > 0:
                rate = i / elapsed
                eta = (len(all_files) - i) / rate
                print(
                    f"   Progress: {i}/{len(all_files)} ({i/len(all_files)*100:.1f}%) - ETA: {eta:.1f}s"
                )

        receipt_start_time = time.time()

        # Load receipt
        words, merchant = load_receipt_words(file_path)
        if not words:
            results.append(
                {
                    "file": file_path.name,
                    "merchant": merchant,
                    "success": False,
                    "error": merchant,
                    "processing_time": time.time() - receipt_start_time,
                }
            )
            continue

        try:
            # Get pattern matches
            pattern_results = await pattern_orchestrator.detect_all_patterns(
                words
            )
            all_matches = []
            for detector_name, matches in pattern_results.items():
                if detector_name != "_metadata":
                    all_matches.extend(matches)

            # Extract currency values
            currency_patterns = {
                "CURRENCY",
                "GRAND_TOTAL",
                "SUBTOTAL",
                "TAX",
                "DISCOUNT",
                "UNIT_PRICE",
                "LINE_TOTAL",
            }
            currency_values = []
            for match in all_matches:
                if (
                    match.pattern_type.name in currency_patterns
                    and match.extracted_value
                ):
                    try:
                        value = float(match.extracted_value)
                        if 0.001 <= abs(value) <= 999.99:
                            currency_values.append((value, match))
                    except (ValueError, TypeError):
                        continue

            # Spatial analysis with Phase 2 features
            alignment_result = (
                alignment_detector.detect_line_items_with_alignment(
                    words, all_matches
                )
            )
            price_columns = alignment_detector.detect_price_columns(
                [v[1] for v in currency_values]
            )

            # Filter to price columns if found
            if price_columns:
                column_lines = set()
                for column in price_columns:
                    column_lines.update(p.word.line_id for p in column.prices)

                column_currencies = [
                    (value, match)
                    for value, match in currency_values
                    if match.word.line_id in column_lines
                ]
            else:
                column_currencies = currency_values

            # Mathematical validation
            solutions = math_solver.solve_receipt_math(column_currencies)

            processing_time = time.time() - receipt_start_time

            # Build spatial analysis results
            spatial_analysis = {
                "best_column_confidence": alignment_result[
                    "best_column_confidence"
                ],
                "x_alignment_tightness": alignment_result.get(
                    "x_alignment_tightness", 0
                ),
                "font_consistency_confidence": alignment_result.get(
                    "font_consistency_confidence", 0
                ),
                "has_large_font_patterns": alignment_result.get(
                    "has_large_font_patterns", False
                ),
            }

            # Use SIMPLIFIED classification
            confidence = classify_confidence_simplified(
                solutions, spatial_analysis
            )

            # Check for Phase 2 features
            has_indented_items = any(
                item.has_indented_description
                for item in alignment_result.get("line_items", [])
            )

            results.append(
                {
                    "file": file_path.name,
                    "merchant": merchant,
                    "success": True,
                    "currency_count": len(currency_values),
                    "filtered_count": len(column_currencies),
                    "price_columns": len(price_columns),
                    "solutions": len(solutions),
                    "confidence": confidence,
                    "best_solution_confidence": (
                        solutions[0].confidence if solutions else 0
                    ),
                    "best_column_confidence": spatial_analysis[
                        "best_column_confidence"
                    ],
                    "x_alignment_tightness": spatial_analysis[
                        "x_alignment_tightness"
                    ],
                    "font_consistency": spatial_analysis[
                        "font_consistency_confidence"
                    ],
                    "has_large_fonts": spatial_analysis[
                        "has_large_font_patterns"
                    ],
                    "has_indented_items": has_indented_items,
                    "processing_time": processing_time,
                }
            )

        except Exception as e:
            processing_time = time.time() - receipt_start_time
            results.append(
                {
                    "file": file_path.name,
                    "merchant": merchant,
                    "success": False,
                    "error": str(e)[:100],
                    "processing_time": processing_time,
                }
            )

    total_time = time.time() - total_start_time

    # COMPREHENSIVE ANALYSIS
    print(
        f"\n🎯 SIMPLIFIED CONFIDENCE RESULTS - ALL {len(all_files)} RECEIPTS"
    )
    print("=" * 60)
    print(f"⏰ Total processing time: {total_time:.2f} seconds")
    print(f"⚡ Average per receipt: {total_time/len(all_files)*1000:.1f}ms")

    successful = [r for r in results if r["success"]]
    failed = [r for r in results if not r["success"]]

    print(f"\n📊 Success Rate:")
    print(
        f"   Successful: {len(successful)}/{len(all_files)} ({len(successful)/len(all_files)*100:.1f}%)"
    )
    print(
        f"   Failed: {len(failed)}/{len(all_files)} ({len(failed)/len(all_files)*100:.1f}%)"
    )

    if successful:
        # Confidence distribution
        confidence_dist = Counter(r["confidence"] for r in successful)

        print(f"\n🔥 CONFIDENCE DISTRIBUTION (SIMPLIFIED):")
        for confidence_level in [
            "high_confidence",
            "medium_confidence",
            "low_confidence",
            "no_solution",
        ]:
            count = confidence_dist.get(confidence_level, 0)
            print(
                f"   {confidence_level}: {count} ({count/len(successful)*100:.1f}%)"
            )

        # Cost reduction calculation
        high_conf = confidence_dist.get("high_confidence", 0)
        medium_conf = confidence_dist.get("medium_confidence", 0)
        low_conf = confidence_dist.get("low_confidence", 0)
        no_solution = confidence_dist.get("no_solution", 0)

        current_cost = len(successful) * 1.0  # 100% use Pinecone
        new_cost = (
            (high_conf * 0.0)
            + (medium_conf * 0.3)
            + (low_conf * 1.0)
            + (no_solution * 1.0)
        )
        cost_reduction = ((current_cost - new_cost) / current_cost) * 100

        print(f"\n💰 COST REDUCTION ANALYSIS:")
        print(
            f"   High confidence (no Pinecone): {high_conf} ({high_conf/len(successful)*100:.1f}%)"
        )
        print(
            f"   Medium confidence (light Pinecone): {medium_conf} ({medium_conf/len(successful)*100:.1f}%)"
        )
        print(f"   🎯 COST REDUCTION: {cost_reduction:.1f}%")

        # Phase 2 feature usage
        phase2_features = {
            "tight_alignment": sum(
                1
                for r in successful
                if r.get("x_alignment_tightness", 0) > 0.9
            ),
            "font_analysis": sum(
                1 for r in successful if r.get("font_consistency", 0) > 0
            ),
            "large_fonts": sum(
                1 for r in successful if r.get("has_large_fonts", False)
            ),
            "indented_items": sum(
                1 for r in successful if r.get("has_indented_items", False)
            ),
        }

        print(f"\n✨ PHASE 2 FEATURE USAGE:")
        print(
            f"   Tight X-alignment (>0.9): {phase2_features['tight_alignment']} ({phase2_features['tight_alignment']/len(successful)*100:.1f}%)"
        )
        print(
            f"   Font analysis available: {phase2_features['font_analysis']} ({phase2_features['font_analysis']/len(successful)*100:.1f}%)"
        )
        print(
            f"   Large font detection: {phase2_features['large_fonts']} ({phase2_features['large_fonts']/len(successful)*100:.1f}%)"
        )
        print(
            f"   Indented descriptions: {phase2_features['indented_items']} ({phase2_features['indented_items']/len(successful)*100:.1f}%)"
        )

        # Complexity analysis
        currency_counts = [r["currency_count"] for r in successful]
        print(f"\n📊 Receipt Complexity:")
        print(
            f"   Average currencies: {sum(currency_counts)/len(currency_counts):.1f}"
        )
        print(f"   Max currencies: {max(currency_counts)}")

        # Performance by merchant
        merchant_stats = Counter()
        merchant_confidence = {}

        for r in successful:
            merchant = r["merchant"]
            if "Sprouts" in merchant:
                merchant_key = "Sprouts"
            elif "Walmart" in merchant or "WAL-MART" in merchant:
                merchant_key = "Walmart"
            elif "Target" in merchant:
                merchant_key = "Target"
            elif "McDonald" in merchant:
                merchant_key = "McDonalds"
            else:
                merchant_key = "Other"

            merchant_stats[merchant_key] += 1

            if merchant_key not in merchant_confidence:
                merchant_confidence[merchant_key] = []
            merchant_confidence[merchant_key].append(r["confidence"])

        print(f"\n🏪 Performance by Merchant:")
        for merchant, count in merchant_stats.most_common():
            confidences = merchant_confidence[merchant]
            high_conf_count = sum(
                1 for c in confidences if c == "high_confidence"
            )
            print(
                f"   {merchant}: {count} receipts, {high_conf_count} high confidence ({high_conf_count/count*100:.1f}%)"
            )

    print(f"\n🏁 SIMPLIFIED CONFIDENCE VERDICT:")
    print(f"   Removed overcomplicated single-solution requirement")
    print(f"   Using combined math + spatial scoring")
    print(f"   Phase 2 features boost confidence naturally")
    print(f"   Result: {cost_reduction:.1f}% cost reduction achieved!")


if __name__ == "__main__":
    asyncio.run(test_simplified_confidence())
