#!/usr/bin/env python3
"""Test script for Epic #190: Parallel Pattern Detection"""

import asyncio
import time

from receipt_label.pattern_detection import ParallelPatternOrchestrator

from receipt_dynamo.entities import ReceiptWord


async def test_pattern_detection():
    """Test the parallel pattern detection system."""

    # Create sample receipt words
    words = [
        ReceiptWord(
            receipt_id=1,
            image_id="550e8400-e29b-41d4-a716-446655440000",
            line_id=1,
            word_id=1,
            text="McDonald's",
            bounding_box={"x": 0, "y": 20, "width": 100, "height": 20},
            top_left={"x": 0, "y": 20},
            top_right={"x": 100, "y": 20},
            bottom_left={"x": 0, "y": 40},
            bottom_right={"x": 100, "y": 40},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.95,
        ),
        ReceiptWord(
            receipt_id=1,
            image_id="550e8400-e29b-41d4-a716-446655440000",
            line_id=2,
            word_id=2,
            text="01/15/2024",
            bounding_box={"x": 0, "y": 60, "width": 80, "height": 20},
            top_left={"x": 0, "y": 60},
            top_right={"x": 80, "y": 60},
            bottom_left={"x": 0, "y": 80},
            bottom_right={"x": 80, "y": 80},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.95,
        ),
        ReceiptWord(
            receipt_id=1,
            image_id="550e8400-e29b-41d4-a716-446655440000",
            line_id=3,
            word_id=3,
            text="$15.99",
            bounding_box={"x": 0, "y": 100, "width": 60, "height": 20},
            top_left={"x": 0, "y": 100},
            top_right={"x": 60, "y": 100},
            bottom_left={"x": 0, "y": 120},
            bottom_right={"x": 60, "y": 120},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.95,
        ),
        ReceiptWord(
            receipt_id=1,
            image_id="550e8400-e29b-41d4-a716-446655440000",
            line_id=4,
            word_id=4,
            text="(555)",
            bounding_box={"x": 0, "y": 140, "width": 40, "height": 20},
            top_left={"x": 0, "y": 140},
            top_right={"x": 40, "y": 140},
            bottom_left={"x": 0, "y": 160},
            bottom_right={"x": 40, "y": 160},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.95,
        ),
        ReceiptWord(
            receipt_id=1,
            image_id="550e8400-e29b-41d4-a716-446655440000",
            line_id=4,
            word_id=5,
            text="123-4567",
            bounding_box={"x": 50, "y": 140, "width": 70, "height": 20},
            top_left={"x": 50, "y": 140},
            top_right={"x": 120, "y": 140},
            bottom_left={"x": 50, "y": 160},
            bottom_right={"x": 120, "y": 160},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.95,
        ),
        ReceiptWord(
            receipt_id=1,
            image_id="550e8400-e29b-41d4-a716-446655440000",
            line_id=5,
            word_id=6,
            text="2",
            bounding_box={"x": 0, "y": 180, "width": 20, "height": 20},
            top_left={"x": 0, "y": 180},
            top_right={"x": 20, "y": 180},
            bottom_left={"x": 0, "y": 200},
            bottom_right={"x": 20, "y": 200},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.95,
        ),
        ReceiptWord(
            receipt_id=1,
            image_id="550e8400-e29b-41d4-a716-446655440000",
            line_id=5,
            word_id=7,
            text="@",
            bounding_box={"x": 30, "y": 180, "width": 20, "height": 20},
            top_left={"x": 30, "y": 180},
            top_right={"x": 50, "y": 180},
            bottom_left={"x": 30, "y": 200},
            bottom_right={"x": 50, "y": 200},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.95,
        ),
        ReceiptWord(
            receipt_id=1,
            image_id="550e8400-e29b-41d4-a716-446655440000",
            line_id=5,
            word_id=8,
            text="$5.99",
            bounding_box={"x": 60, "y": 180, "width": 50, "height": 20},
            top_left={"x": 60, "y": 180},
            top_right={"x": 110, "y": 180},
            bottom_left={"x": 60, "y": 200},
            bottom_right={"x": 110, "y": 200},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.95,
        ),
    ]

    # Initialize orchestrator
    orchestrator = ParallelPatternOrchestrator(timeout=0.1)

    # Run pattern detection
    print("Running parallel pattern detection...")
    start_time = time.time()

    results = await orchestrator.detect_all_patterns(words)

    elapsed_time = (time.time() - start_time) * 1000
    print(f"\nPattern detection completed in {elapsed_time:.2f}ms")

    # Display results
    print("\nDetected patterns:")
    print("-" * 50)

    for detector_name, matches in results.items():
        if detector_name == "_metadata":
            continue

        if matches:
            print(
                f"\n{detector_name.upper()} detector found {len(matches)} matches:"
            )
            for match in matches:
                print(
                    f"  - '{match.matched_text}' → {match.pattern_type.name} (confidence: {match.confidence:.2f})"
                )

    # Check performance
    metadata = results["_metadata"]
    print(f"\nPerformance metrics:")
    print(f"  - Execution time: {metadata['execution_time_ms']:.2f}ms")
    print(f"  - Word count: {metadata['word_count']}")
    print(f"  - Timeout occurred: {metadata['timeout_occurred']}")

    # Aggregate results
    aggregated = orchestrator.aggregate_patterns(results)
    print(f"\nAggregated pattern summary:")
    for pattern_type, info in aggregated.items():
        print(
            f"  - {pattern_type}: {info['count']} matches ({info['high_confidence_count']} high confidence)"
        )

    # Check essential fields
    essential = orchestrator.get_essential_fields_status(results)
    print(f"\nEssential fields status:")
    for field, status in essential.items():
        print(f"  - {field}: {'✓' if status else '✗'}")

    # Performance target check
    if elapsed_time < 100:
        print(f"\n✅ Performance target MET: {elapsed_time:.2f}ms < 100ms")
    else:
        print(f"\n❌ Performance target MISSED: {elapsed_time:.2f}ms >= 100ms")


if __name__ == "__main__":
    print("Epic #190: Parallel Pattern Detection Test")
    print("=" * 50)
    asyncio.run(test_pattern_detection())
