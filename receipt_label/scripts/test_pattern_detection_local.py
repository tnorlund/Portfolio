#!/usr/bin/env python3
"""
Test pattern detection enhancements using the local development infrastructure.

This script integrates with the LocalDataLoader from PR #215 to test
the Phase 2-3 pattern detection enhancements on local receipt data.
"""

import argparse
import asyncio
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Import local data loader from PR #215
# pylint: disable=import-error,wrong-import-position
from receipt_label.data.local_data_loader import LocalDataLoader

# Import pattern detection modules
# pylint: disable=import-error,wrong-import-position
from receipt_label.pattern_detection.enhanced_orchestrator import (
    EnhancedPatternOrchestrator,
    OptimizationLevel,
    compare_optimization_performance,
    detect_patterns_optimized,
)


async def test_pattern_detection_on_receipt(
    loader: LocalDataLoader,
    image_id: str,
    receipt_id: str,
    optimization_level: OptimizationLevel,
) -> Dict[str, Any]:
    """Test pattern detection on a single receipt using LocalDataLoader."""

    # Load receipt data using the existing LocalDataLoader
    receipt_data = loader.load_receipt_by_id(image_id, receipt_id)

    if not receipt_data:
        logging.warning("No data found for %s/%s", image_id, receipt_id)
        return {
            "image_id": image_id,
            "receipt_id": receipt_id,
            "error": "No data found",
        }

    _, receipt_words, _ = receipt_data

    # Get merchant name from metadata if available
    metadata = loader.load_metadata(image_id, receipt_id)
    merchant_name = metadata.merchant_name if metadata else "Unknown"

    logging.info(
        "Testing %s/%s - %s (%d words)",
        image_id,
        receipt_id,
        merchant_name,
        len(receipt_words),
    )

    try:
        # Test pattern detection
        start_time = time.time()

        if optimization_level == OptimizationLevel.ADVANCED:
            results = await detect_patterns_optimized(
                words=receipt_words, merchant_name=merchant_name
            )
        else:
            orchestrator = EnhancedPatternOrchestrator(optimization_level)
            results = await orchestrator.detect_patterns(
                receipt_words, merchant_name
            )

        end_time = time.time()
        processing_time_ms = (end_time - start_time) * 1000

        return {
            "image_id": image_id,
            "receipt_id": receipt_id,
            "merchant_name": merchant_name,
            "word_count": len(receipt_words),
            "processing_time_ms": processing_time_ms,
            "optimization_level": optimization_level.value,
            "pattern_matches": len(results.get("detected_patterns", [])),
            "results": results,
        }

    except Exception as e:  # pylint: disable=broad-exception-caught
        logging.error("Error testing %s/%s: %s", image_id, receipt_id, e)
        return {
            "image_id": image_id,
            "receipt_id": receipt_id,
            "error": str(e),
        }


async def test_all_receipts(
    data_dir: str, optimization_level: OptimizationLevel, limit: int = None
) -> Dict[str, Any]:
    """Test pattern detection on all receipts in the local data directory."""

    loader = LocalDataLoader(data_dir)

    # Get all available receipts
    all_receipts = loader.list_available_receipts()

    if limit:
        all_receipts = all_receipts[:limit]

    logging.info(
        "Testing %d receipts with %s optimization",
        len(all_receipts),
        optimization_level.value,
    )

    results = []
    start_time = time.time()

    for i, (image_id, receipt_id) in enumerate(all_receipts):
        result = await test_pattern_detection_on_receipt(
            loader, image_id, receipt_id, optimization_level
        )
        results.append(result)

        if (i + 1) % 5 == 0:
            logging.info("Processed %d/%d receipts", i + 1, len(all_receipts))

    total_time = time.time() - start_time

    # Calculate summary statistics
    successful_tests = [r for r in results if "error" not in r]

    if successful_tests:
        avg_processing_time = sum(
            r["processing_time_ms"] for r in successful_tests
        ) / len(successful_tests)
        total_words = sum(r["word_count"] for r in successful_tests)
        total_patterns = sum(r["pattern_matches"] for r in successful_tests)

        summary = {
            "total_receipts": len(all_receipts),
            "successful_tests": len(successful_tests),
            "failed_tests": len(results) - len(successful_tests),
            "total_time_seconds": total_time,
            "avg_processing_time_ms": avg_processing_time,
            "total_words": total_words,
            "total_pattern_matches": total_patterns,
            "optimization_level": optimization_level.value,
        }
    else:
        summary = {
            "total_receipts": len(all_receipts),
            "successful_tests": 0,
            "failed_tests": len(results),
            "total_time_seconds": total_time,
        }

    return {"summary": summary, "results": results}


async def compare_optimization_levels(
    data_dir: str, limit: int = None
) -> Dict[str, Any]:
    """Compare all optimization levels on the same dataset."""

    loader = LocalDataLoader(data_dir)
    all_receipts = loader.list_available_receipts()

    if limit:
        all_receipts = all_receipts[:limit]

    logging.info(
        "Comparing optimization levels on %d receipts", len(all_receipts)
    )

    comparisons = []

    for i, (image_id, receipt_id) in enumerate(all_receipts):
        receipt_data = loader.load_receipt_by_id(image_id, receipt_id)
        if not receipt_data:
            continue

        _, receipt_words, _ = receipt_data
        metadata = loader.load_metadata(image_id, receipt_id)
        merchant_name = metadata.merchant_name if metadata else "Unknown"

        try:
            comparison = await compare_optimization_performance(
                words=receipt_words, merchant_name=merchant_name
            )

            comparisons.append(
                {
                    "image_id": image_id,
                    "receipt_id": receipt_id,
                    "merchant_name": merchant_name,
                    "word_count": len(receipt_words),
                    "comparison": comparison,
                }
            )

        except Exception as e:  # pylint: disable=broad-exception-caught
            logging.error("Error comparing %s/%s: %s", image_id, receipt_id, e)

        if (i + 1) % 5 == 0:
            logging.info("Compared %d/%d receipts", i + 1, len(all_receipts))

    return {"total_receipts": len(comparisons), "comparisons": comparisons}


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Test pattern detection with local development data"
    )

    parser.add_argument(
        "--data-dir",
        default=os.environ.get("RECEIPT_LOCAL_DATA_DIR", "./receipt_data"),
        help="Directory containing local receipt data",
    )
    parser.add_argument(
        "--optimization-level",
        choices=["legacy", "basic", "optimized", "advanced"],
        default="advanced",
        help="Optimization level to test",
    )
    parser.add_argument(
        "--limit", type=int, help="Limit number of receipts to test"
    )
    parser.add_argument(
        "--compare-all",
        action="store_true",
        help="Compare all optimization levels",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Verbose logging"
    )

    args = parser.parse_args()

    # Configure logging
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )

    # Check if data directory exists
    if not Path(args.data_dir).exists():
        print(f"Error: Data directory '{args.data_dir}' not found")
        print("Run 'make export-sample-data' first to export receipt data")
        sys.exit(1)

    # Set environment variable for stubbed APIs
    os.environ["USE_STUB_APIS"] = "true"

    try:
        if args.compare_all:
            results = asyncio.run(
                compare_optimization_levels(args.data_dir, args.limit)
            )

            print("\n📊 Optimization Level Comparison:")
            print("=" * 50)
            print(f"Total receipts compared: {results['total_receipts']}")

        else:
            optimization_level = OptimizationLevel(args.optimization_level)

            results = asyncio.run(
                test_all_receipts(
                    args.data_dir, optimization_level, args.limit
                )
            )

            summary = results["summary"]

            print("\n📊 Pattern Detection Test Results:")
            print("=" * 50)
            print(f"Total receipts: {summary['total_receipts']}")
            print(f"Successful tests: {summary['successful_tests']}")
            print(f"Failed tests: {summary['failed_tests']}")
            print(f"Total test time: {summary['total_time_seconds']:.2f}s")

            if summary["successful_tests"] > 0:
                print(
                    f"Average processing time: "
                    f"{summary['avg_processing_time_ms']:.2f}ms"
                )
                print(f"Total words processed: {summary['total_words']:,}")
                print(
                    f"Total pattern matches: "
                    f"{summary['total_pattern_matches']}"
                )
                print(f"Optimization level: {summary['optimization_level']}")

        print("\n✅ Testing completed successfully!")

    except KeyboardInterrupt:
        print("\nTesting cancelled by user")
        sys.exit(1)
    except Exception as e:  # pylint: disable=broad-exception-caught
        logging.error("Testing failed: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
