#!/usr/bin/env python3
"""Validation script for Smart Decision Engine.

This script validates that the Decision Engine Phase 1 implementation works correctly
by testing it with realistic synthetic data that simulates various receipt scenarios.
"""

import asyncio
import json

# Add the project root to the path
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).parent.parent))

from receipt_label.decision_engine import (
    ConfidenceLevel,
    DecisionEngine,
    DecisionEngineConfig,
    DecisionOutcome,
    EssentialFieldsStatus,
    PatternDetectionSummary,
    create_aggressive_config,
    create_conservative_config,
)


@dataclass
class ValidationScenario:
    """Test scenario for decision engine validation."""

    name: str
    description: str
    pattern_summary: PatternDetectionSummary
    expected_decision: DecisionOutcome
    expected_confidence: ConfidenceLevel


def create_validation_scenarios() -> List[ValidationScenario]:
    """Create comprehensive validation scenarios."""

    scenarios = []

    # Scenario 1: Perfect receipt - should SKIP
    scenarios.append(
        ValidationScenario(
            name="perfect_walmart_receipt",
            description="Complete Walmart receipt with all essential fields and high coverage",
            pattern_summary=PatternDetectionSummary(
                total_words=25,
                labeled_words=23,
                noise_words=2,
                meaningful_unlabeled_words=0,
                labels_by_type={
                    "MERCHANT": ["WALMART"],
                    "DATE": ["01/15/2024"],
                    "CURRENCY": ["$5.99", "$1.29", "$7.28"],
                    "PRODUCT": ["Bananas", "Milk", "Bread"],
                    "TOTAL": ["$7.28"],
                },
                confidence_scores={
                    "WALMART": 0.95,
                    "01/15/2024": 0.92,
                    "$7.28": 0.98,
                },
                essential_fields=EssentialFieldsStatus(
                    merchant_name_found=True,
                    date_found=True,
                    grand_total_found=True,
                    product_name_found=True,
                ),
                detected_merchant="WALMART",
                merchant_confidence=0.95,
            ),
            expected_decision=DecisionOutcome.SKIP,
            expected_confidence=ConfidenceLevel.HIGH,
        )
    )

    # Scenario 2: Missing critical field - should REQUIRE
    scenarios.append(
        ValidationScenario(
            name="missing_merchant_name",
            description="Receipt missing merchant name (critical field)",
            pattern_summary=PatternDetectionSummary(
                total_words=20,
                labeled_words=18,
                noise_words=1,
                meaningful_unlabeled_words=1,
                labels_by_type={
                    "DATE": ["01/15/2024"],
                    "CURRENCY": ["$12.99"],
                    "PRODUCT": ["Coffee"],
                    "TOTAL": ["$12.99"],
                },
                confidence_scores={"01/15/2024": 0.90, "$12.99": 0.88},
                essential_fields=EssentialFieldsStatus(
                    merchant_name_found=False,  # Missing!
                    date_found=True,
                    grand_total_found=True,
                    product_name_found=True,
                ),
                detected_merchant=None,
                merchant_confidence=None,
            ),
            expected_decision=DecisionOutcome.REQUIRED,
            expected_confidence=ConfidenceLevel.HIGH,
        )
    )

    # Scenario 3: Good coverage but missing product - should BATCH
    scenarios.append(
        ValidationScenario(
            name="missing_product_names",
            description="Good coverage but no clear product names identified",
            pattern_summary=PatternDetectionSummary(
                total_words=22,
                labeled_words=20,
                noise_words=1,
                meaningful_unlabeled_words=1,
                labels_by_type={
                    "MERCHANT": ["Target"],
                    "DATE": ["01/15/2024"],
                    "CURRENCY": ["$25.99"],
                    "ITEM_CODE": [
                        "123456",
                        "789012",
                    ],  # Generic codes, not product names
                    "TOTAL": ["$25.99"],
                },
                confidence_scores={
                    "Target": 0.91,
                    "01/15/2024": 0.94,
                    "$25.99": 0.89,
                },
                essential_fields=EssentialFieldsStatus(
                    merchant_name_found=True,
                    date_found=True,
                    grand_total_found=True,
                    product_name_found=False,  # No clear product names
                ),
                detected_merchant="Target",
                merchant_confidence=0.91,
            ),
            expected_decision=DecisionOutcome.BATCH,
            expected_confidence=ConfidenceLevel.MEDIUM,
        )
    )

    # Scenario 4: Low coverage - should REQUIRE
    scenarios.append(
        ValidationScenario(
            name="low_coverage_receipt",
            description="Poor OCR quality resulting in low pattern coverage",
            pattern_summary=PatternDetectionSummary(
                total_words=30,
                labeled_words=15,  # Only 50% coverage (30-5=25 meaningful, 15/25=60%)
                noise_words=5,
                meaningful_unlabeled_words=10,
                labels_by_type={
                    "MERCHANT": ["McDonald's"],
                    "DATE": ["01/15/2024"],
                    "CURRENCY": ["$8.99"],
                    "PRODUCT": ["Big Mac"],
                    "TOTAL": ["$8.99"],
                },
                confidence_scores={
                    "McDonald's": 0.88,
                    "01/15/2024": 0.85,
                    "$8.99": 0.82,
                },
                essential_fields=EssentialFieldsStatus(
                    merchant_name_found=True,
                    date_found=True,
                    grand_total_found=True,
                    product_name_found=True,
                ),
                detected_merchant="McDonald's",
                merchant_confidence=0.88,
            ),
            expected_decision=DecisionOutcome.REQUIRED,
            expected_confidence=ConfidenceLevel.MEDIUM,
        )
    )

    # Scenario 5: Too many unlabeled words - should REQUIRE
    scenarios.append(
        ValidationScenario(
            name="too_many_unlabeled",
            description="Good coverage but too many meaningful words remain unlabeled",
            pattern_summary=PatternDetectionSummary(
                total_words=20,
                labeled_words=15,
                noise_words=0,
                meaningful_unlabeled_words=8,  # More than 5 threshold
                labels_by_type={
                    "MERCHANT": ["Starbucks"],
                    "DATE": ["01/15/2024"],
                    "CURRENCY": ["$4.95"],
                    "PRODUCT": ["Latte"],
                    "TOTAL": ["$4.95"],
                },
                confidence_scores={
                    "Starbucks": 0.93,
                    "01/15/2024": 0.90,
                    "$4.95": 0.91,
                },
                essential_fields=EssentialFieldsStatus(
                    merchant_name_found=True,
                    date_found=True,
                    grand_total_found=True,
                    product_name_found=True,
                ),
                detected_merchant="Starbucks",
                merchant_confidence=0.93,
            ),
            expected_decision=DecisionOutcome.REQUIRED,
            expected_confidence=ConfidenceLevel.MEDIUM,
        )
    )

    return scenarios


async def validate_scenario(
    scenario: ValidationScenario, config: DecisionEngineConfig
) -> Dict[str, Any]:
    """Validate a single scenario."""

    print(f"🧪 Testing: {scenario.name}")
    print(f"   {scenario.description}")

    # Create decision engine
    engine = DecisionEngine(config)

    # Make decision
    start_time = time.time()
    result = engine.decide(scenario.pattern_summary)
    decision_time_ms = (time.time() - start_time) * 1000

    # Check results
    decision_correct = result.action == scenario.expected_decision
    confidence_correct = result.confidence == scenario.expected_confidence
    performance_ok = decision_time_ms < 10.0

    # Display results
    status = "✅ PASS" if decision_correct else "❌ FAIL"
    print(
        f"   Decision: {result.action.value.upper()} (expected: {scenario.expected_decision.value.upper()}) {status}"
    )
    print(
        f"   Confidence: {result.confidence.value} (expected: {scenario.expected_confidence.value})"
    )
    print(f"   Coverage: {result.coverage_percentage:.1f}%")
    print(f"   Time: {decision_time_ms:.2f}ms")
    print(f"   Reasoning: {result.reasoning}")

    if not decision_correct:
        print(f"   ⚠️  Decision mismatch!")

    if not performance_ok:
        print(f"   ⚠️  Performance issue: {decision_time_ms:.2f}ms > 10ms")

    return {
        "name": scenario.name,
        "decision_correct": decision_correct,
        "confidence_correct": confidence_correct,
        "performance_ok": performance_ok,
        "decision_time_ms": decision_time_ms,
        "actual_decision": result.action.value,
        "expected_decision": scenario.expected_decision.value,
        "coverage_percentage": result.coverage_percentage,
        "reasoning": result.reasoning,
    }


async def run_validation_suite(config_name: str = "default") -> Dict[str, Any]:
    """Run the complete validation suite."""

    print("🔬 SMART DECISION ENGINE VALIDATION SUITE")
    print("=" * 60)

    # Create configuration
    if config_name == "conservative":
        config = create_conservative_config()
        print("⚙️  Using CONSERVATIVE configuration")
    elif config_name == "aggressive":
        config = create_aggressive_config()
        print("⚙️  Using AGGRESSIVE configuration")
    else:
        config = DecisionEngineConfig(enabled=True, rollout_percentage=100.0)
        print("⚙️  Using DEFAULT configuration")

    print(f"   • Coverage threshold: {config.min_coverage_percentage}%")
    print(f"   • Max unlabeled words: {config.max_unlabeled_words}")
    print(f"   • Min confidence: {config.min_pattern_confidence}")

    # Create and run scenarios
    scenarios = create_validation_scenarios()
    print(f"\n🧪 Running {len(scenarios)} validation scenarios...")

    results = []
    for i, scenario in enumerate(scenarios):
        print(f"\n[{i+1}/{len(scenarios)}]", end=" ")
        result = await validate_scenario(scenario, config)
        results.append(result)

    # Calculate summary statistics
    total_tests = len(results)
    decision_correct_count = sum(1 for r in results if r["decision_correct"])
    confidence_correct_count = sum(
        1 for r in results if r["confidence_correct"]
    )
    performance_ok_count = sum(1 for r in results if r["performance_ok"])

    avg_decision_time = (
        sum(r["decision_time_ms"] for r in results) / total_tests
    )
    max_decision_time = max(r["decision_time_ms"] for r in results)

    # Count decision types
    skip_decisions = sum(1 for r in results if r["actual_decision"] == "skip")
    batch_decisions = sum(
        1 for r in results if r["actual_decision"] == "batch"
    )
    required_decisions = sum(
        1 for r in results if r["actual_decision"] == "required"
    )

    summary = {
        "total_tests": total_tests,
        "decision_accuracy": decision_correct_count / total_tests * 100,
        "confidence_accuracy": confidence_correct_count / total_tests * 100,
        "performance_success": performance_ok_count / total_tests * 100,
        "avg_decision_time_ms": avg_decision_time,
        "max_decision_time_ms": max_decision_time,
        "decision_distribution": {
            "skip": skip_decisions,
            "batch": batch_decisions,
            "required": required_decisions,
        },
        "results": results,
    }

    return summary


def display_validation_summary(summary: Dict[str, Any]) -> None:
    """Display validation results summary."""

    print(f"\n{'='*60}")
    print("📊 VALIDATION RESULTS SUMMARY")
    print(f"{'='*60}")

    # Core metrics
    print(f"Total tests: {summary['total_tests']}")
    print(f"Decision accuracy: {summary['decision_accuracy']:.1f}%")
    print(f"Confidence accuracy: {summary['confidence_accuracy']:.1f}%")
    print(f"Performance success: {summary['performance_success']:.1f}%")

    # Performance metrics
    print(f"\n⚡ PERFORMANCE:")
    print(f"   Average decision time: {summary['avg_decision_time_ms']:.2f}ms")
    print(f"   Maximum decision time: {summary['max_decision_time_ms']:.2f}ms")
    print(
        f"   Performance target (<10ms): {'✅ PASS' if summary['max_decision_time_ms'] < 10 else '❌ FAIL'}"
    )

    # Decision distribution
    dist = summary["decision_distribution"]
    print(f"\n🎯 DECISION DISTRIBUTION:")
    print(
        f"   SKIP: {dist['skip']} ({dist['skip']/summary['total_tests']*100:.1f}%)"
    )
    print(
        f"   BATCH: {dist['batch']} ({dist['batch']/summary['total_tests']*100:.1f}%)"
    )
    print(
        f"   REQUIRED: {dist['required']} ({dist['required']/summary['total_tests']*100:.1f}%)"
    )

    # Overall assessment
    print(f"\n🎯 OVERALL ASSESSMENT:")

    if (
        summary["decision_accuracy"] == 100.0
        and summary["performance_success"] == 100.0
    ):
        print("   🎉 PERFECT SCORE - Decision Engine working flawlessly!")
    elif (
        summary["decision_accuracy"] >= 80.0
        and summary["performance_success"] >= 80.0
    ):
        print("   ✅ EXCELLENT - Decision Engine ready for production!")
    elif summary["decision_accuracy"] >= 60.0:
        print("   ⚠️  GOOD - Minor tuning may be needed")
    else:
        print("   ❌ NEEDS WORK - Significant issues detected")

    # Specific failures
    failed_tests = [r for r in summary["results"] if not r["decision_correct"]]
    if failed_tests:
        print(f"\n❌ FAILED SCENARIOS:")
        for test in failed_tests:
            print(
                f"   • {test['name']}: expected {test['expected_decision']}, got {test['actual_decision']}"
            )


async def main():
    """Main validation function."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Validate Smart Decision Engine implementation"
    )
    parser.add_argument(
        "--config",
        choices=["default", "conservative", "aggressive"],
        default="default",
        help="Configuration preset to test",
    )
    parser.add_argument(
        "--output", help="Save validation results to JSON file"
    )

    args = parser.parse_args()

    # Run validation
    summary = await run_validation_suite(args.config)

    # Display results
    display_validation_summary(summary)

    # Save results if requested
    if args.output:
        with open(args.output, "w") as f:
            json.dump(summary, f, indent=2)
        print(f"\n📄 Results saved to: {args.output}")

    # Determine success
    if (
        summary["decision_accuracy"] >= 80.0
        and summary["performance_success"] >= 80.0
    ):
        print(f"\n✅ VALIDATION PASSED - Decision Engine is ready!")
        return 0
    else:
        print(f"\n❌ VALIDATION FAILED - Decision Engine needs improvement")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
