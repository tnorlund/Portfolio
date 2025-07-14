#!/usr/bin/env python3
"""Integration test for Smart Decision Engine with existing local data.

This script tests the decision engine using the existing receipt_data directory
and validates the integration with pattern detection systems.
"""

import asyncio
import json
import logging

# Add the project root to the path
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from receipt_label.data.local_data_loader import LocalDataLoader
from receipt_label.decision_engine import (
    DecisionEngineConfig,
    DecisionOutcome,
    create_aggressive_config,
    create_conservative_config,
    process_receipt_with_decision_engine,
)


@dataclass
class IntegrationTestResult:
    """Result from integration testing."""

    receipt_id: str
    merchant_name: Optional[str]
    decision: DecisionOutcome
    coverage_percentage: float
    processing_time_ms: float
    essential_fields_found: set
    essential_fields_missing: set
    success: bool
    error: Optional[str] = None


async def test_single_receipt_integration(
    image_id: str,
    receipt_id: str,
    data_loader: LocalDataLoader,
    config: DecisionEngineConfig,
) -> IntegrationTestResult:
    """Test decision engine integration on a single receipt."""

    try:
        # Load receipt data
        receipt_data = data_loader.load_receipt_by_id(image_id, receipt_id)
        if not receipt_data:
            return IntegrationTestResult(
                receipt_id=f"{image_id}/{receipt_id}",
                merchant_name=None,
                decision=DecisionOutcome.REQUIRED,
                coverage_percentage=0.0,
                processing_time_ms=0.0,
                essential_fields_found=set(),
                essential_fields_missing={
                    "MERCHANT_NAME",
                    "DATE",
                    "GRAND_TOTAL",
                },
                success=False,
                error="Failed to load receipt data",
            )

        receipt, words, lines = receipt_data

        # Process with decision engine
        start_time = time.time()
        result = await process_receipt_with_decision_engine(
            words=words,
            config=config,
            client_manager=None,  # No Pinecone in local testing
            receipt_context={"image_id": image_id, "receipt_id": receipt_id},
        )
        processing_time_ms = (time.time() - start_time) * 1000

        decision = result.decision

        return IntegrationTestResult(
            receipt_id=f"{image_id}/{receipt_id}",
            merchant_name=decision.merchant_name,
            decision=decision.action,
            coverage_percentage=decision.coverage_percentage,
            processing_time_ms=processing_time_ms,
            essential_fields_found=decision.essential_fields_found,
            essential_fields_missing=decision.essential_fields_missing,
            success=True,
        )

    except Exception as e:
        logging.error(f"Error processing receipt {image_id}/{receipt_id}: {e}")
        return IntegrationTestResult(
            receipt_id=f"{image_id}/{receipt_id}",
            merchant_name=None,
            decision=DecisionOutcome.REQUIRED,
            coverage_percentage=0.0,
            processing_time_ms=0.0,
            essential_fields_found=set(),
            essential_fields_missing={"MERCHANT_NAME", "DATE", "GRAND_TOTAL"},
            success=False,
            error=str(e),
        )


async def run_integration_tests(
    data_dir: str = "./receipt_data",
    config: Optional[DecisionEngineConfig] = None,
    max_receipts: Optional[int] = None,
) -> List[IntegrationTestResult]:
    """Run integration tests on available receipt data."""

    print("🔗 SMART DECISION ENGINE INTEGRATION TESTS")
    print("=" * 50)

    # Initialize data loader
    try:
        data_loader = LocalDataLoader(data_dir)
    except ValueError as e:
        print(f"❌ Error: {e}")
        print(
            f"💡 Try running: python scripts/test_decision_engine_local.py --download-size 10"
        )
        return []

    # Get available receipts
    available_receipts = data_loader.list_available_receipts()
    if not available_receipts:
        print(f"❌ No receipts found in {data_dir}")
        return []

    if max_receipts:
        available_receipts = available_receipts[:max_receipts]

    print(f"📋 Testing {len(available_receipts)} receipts")
    print(
        f"🔧 Configuration: {config.min_coverage_percentage}% coverage, {config.max_unlabeled_words} max unlabeled"
    )

    # Test each receipt
    results = []
    start_time = time.time()

    for i, (image_id, receipt_id) in enumerate(available_receipts):
        if i % 5 == 0:
            print(f"   Processing receipt {i+1}/{len(available_receipts)}...")

        result = await test_single_receipt_integration(
            image_id, receipt_id, data_loader, config
        )
        results.append(result)

    total_time = time.time() - start_time
    print(f"✅ Completed {len(results)} tests in {total_time:.1f} seconds")

    return results


def analyze_integration_results(
    results: List[IntegrationTestResult],
) -> Dict[str, Any]:
    """Analyze integration test results."""

    if not results:
        return {"error": "No results to analyze"}

    successful_tests = [r for r in results if r.success]
    failed_tests = [r for r in results if not r.success]

    # Count decision outcomes
    skip_count = len(
        [r for r in successful_tests if r.decision == DecisionOutcome.SKIP]
    )
    batch_count = len(
        [r for r in successful_tests if r.decision == DecisionOutcome.BATCH]
    )
    required_count = len(
        [r for r in successful_tests if r.decision == DecisionOutcome.REQUIRED]
    )

    # Calculate statistics
    total_tests = len(results)
    success_rate = len(successful_tests) / total_tests * 100
    skip_rate = skip_count / total_tests * 100

    # Coverage statistics
    if successful_tests:
        avg_coverage = sum(
            r.coverage_percentage for r in successful_tests
        ) / len(successful_tests)
        avg_processing_time = sum(
            r.processing_time_ms for r in successful_tests
        ) / len(successful_tests)
    else:
        avg_coverage = 0.0
        avg_processing_time = 0.0

    # Essential fields analysis
    critical_fields_found = len(
        [
            r
            for r in successful_tests
            if len(
                r.essential_fields_missing.intersection(
                    {"MERCHANT_NAME", "DATE", "GRAND_TOTAL"}
                )
            )
            == 0
        ]
    )
    critical_fields_success_rate = (
        (critical_fields_found / len(successful_tests) * 100)
        if successful_tests
        else 0.0
    )

    return {
        "total_tests": total_tests,
        "successful_tests": len(successful_tests),
        "failed_tests": len(failed_tests),
        "success_rate": success_rate,
        "skip_count": skip_count,
        "batch_count": batch_count,
        "required_count": required_count,
        "skip_rate": skip_rate,
        "avg_coverage": avg_coverage,
        "avg_processing_time_ms": avg_processing_time,
        "critical_fields_success_rate": critical_fields_success_rate,
        "merchants_detected": len(
            set(r.merchant_name for r in successful_tests if r.merchant_name)
        ),
        "errors": [r.error for r in failed_tests if r.error],
    }


def display_results(analysis: Dict[str, Any]) -> None:
    """Display integration test results."""

    print(f"\n{'='*50}")
    print("📊 INTEGRATION TEST RESULTS")
    print(f"{'='*50}")

    if "error" in analysis:
        print(f"❌ {analysis['error']}")
        return

    # Overall results
    print(f"Total receipts tested: {analysis['total_tests']}")
    print(
        f"Successful tests: {analysis['successful_tests']} ({analysis['success_rate']:.1f}%)"
    )
    print(f"Failed tests: {analysis['failed_tests']}")

    if analysis["failed_tests"] > 0:
        print(
            f"   Common errors: {set(analysis['errors'][:3])}"
        )  # Show first 3 unique errors

    # Decision distribution
    print(f"\n🎯 DECISION DISTRIBUTION:")
    print(
        f"   SKIP (no GPT): {analysis['skip_count']} ({analysis['skip_rate']:.1f}%)"
    )
    print(f"   BATCH (queue): {analysis['batch_count']}")
    print(f"   REQUIRED (immediate): {analysis['required_count']}")

    # Performance metrics
    print(f"\n⚡ PERFORMANCE:")
    print(f"   Average coverage: {analysis['avg_coverage']:.1f}%")
    print(
        f"   Average processing time: {analysis['avg_processing_time_ms']:.1f}ms"
    )
    print(
        f"   Critical fields success: {analysis['critical_fields_success_rate']:.1f}%"
    )
    print(f"   Merchants detected: {analysis['merchants_detected']}")

    # Assessment
    print(f"\n🎯 ASSESSMENT:")

    # Target evaluations
    targets_met = 0
    if analysis["skip_rate"] >= 84.0:
        print(
            f"   ✅ GPT Skip Rate: {analysis['skip_rate']:.1f}% (≥84% target)"
        )
        targets_met += 1
    else:
        print(
            f"   ❌ GPT Skip Rate: {analysis['skip_rate']:.1f}% (<84% target)"
        )

    if analysis["critical_fields_success_rate"] >= 95.0:
        print(
            f"   ✅ Critical Fields: {analysis['critical_fields_success_rate']:.1f}% (≥95% target)"
        )
        targets_met += 1
    else:
        print(
            f"   ❌ Critical Fields: {analysis['critical_fields_success_rate']:.1f}% (<95% target)"
        )

    if analysis["avg_processing_time_ms"] < 500:  # Including pattern detection
        print(
            f"   ✅ Processing Speed: {analysis['avg_processing_time_ms']:.1f}ms (<500ms target)"
        )
        targets_met += 1
    else:
        print(
            f"   ⚠️  Processing Speed: {analysis['avg_processing_time_ms']:.1f}ms (≥500ms)"
        )

    # Overall assessment
    if targets_met == 3:
        print(f"\n🎉 ALL TARGETS MET! Decision Engine is working well.")
    elif targets_met == 2:
        print(f"\n⚠️  MOSTLY SUCCESSFUL - Minor tuning may be needed.")
    else:
        print(
            f"\n❌ NEEDS IMPROVEMENT - Consider adjusting thresholds or pattern detection."
        )

    # Recommendations
    print(f"\n💡 RECOMMENDATIONS:")
    if analysis["skip_rate"] < 84.0:
        print(f"   • Lower coverage threshold or increase max unlabeled words")
        print(f"   • Improve pattern detection for common receipt types")
    if analysis["critical_fields_success_rate"] < 95.0:
        print(f"   • Investigate why critical fields are being missed")
        print(f"   • May need better merchant name detection")
    if analysis["avg_processing_time_ms"] > 500:
        print(f"   • Consider optimizing pattern detection performance")


async def main():
    """Main integration test function."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Integration test Smart Decision Engine"
    )
    parser.add_argument(
        "--data-dir",
        default="./receipt_data",
        help="Path to receipt data directory",
    )
    parser.add_argument(
        "--config",
        choices=["default", "conservative", "aggressive"],
        default="default",
        help="Configuration preset",
    )
    parser.add_argument(
        "--max-receipts", type=int, help="Maximum receipts to test"
    )
    parser.add_argument("--output", help="Save results to JSON file")
    parser.add_argument(
        "--verbose", action="store_true", help="Verbose logging"
    )

    args = parser.parse_args()

    # Setup logging
    level = logging.DEBUG if args.verbose else logging.WARNING
    logging.basicConfig(level=level)

    # Create configuration
    if args.config == "conservative":
        config = create_conservative_config()
    elif args.config == "aggressive":
        config = create_aggressive_config()
    else:
        config = DecisionEngineConfig(enabled=True, rollout_percentage=100.0)

    # Run tests
    results = await run_integration_tests(
        data_dir=args.data_dir, config=config, max_receipts=args.max_receipts
    )

    if not results:
        return 1

    # Analyze and display results
    analysis = analyze_integration_results(results)
    display_results(analysis)

    # Save results if requested
    if args.output:
        output_data = {
            "analysis": analysis,
            "detailed_results": [
                {
                    "receipt_id": r.receipt_id,
                    "merchant_name": r.merchant_name,
                    "decision": r.decision.value,
                    "coverage_percentage": r.coverage_percentage,
                    "processing_time_ms": r.processing_time_ms,
                    "essential_fields_found": list(r.essential_fields_found),
                    "essential_fields_missing": list(
                        r.essential_fields_missing
                    ),
                    "success": r.success,
                    "error": r.error,
                }
                for r in results
            ],
        }

        with open(args.output, "w") as f:
            json.dump(output_data, f, indent=2)

        print(f"\n📄 Results saved to: {args.output}")

    # Return success if targets mostly met
    skip_rate = analysis.get("skip_rate", 0)
    critical_rate = analysis.get("critical_fields_success_rate", 0)

    if (
        skip_rate >= 70 and critical_rate >= 90
    ):  # Relaxed thresholds for integration test
        return 0
    else:
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
