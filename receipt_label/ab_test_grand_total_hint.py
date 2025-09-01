#!/usr/bin/env python3
"""
A/B test to compare performance with and without GRAND_TOTAL hint.
Tests whether providing the known total in the prompt helps or hurts model performance.
"""

import asyncio
import logging
import os
from typing import Dict, List
from dataclasses import dataclass
from receipt_dynamo import DynamoClient
from receipt_dynamo.data._pulumi import load_env
from receipt_label.costco_analyzer import analyze_costco_receipt, setup_langsmith_tracing

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class ABTestResult:
    """Results from A/B test comparing with/without hint."""
    receipt_id: str
    ground_truth_total: float
    
    # Results with hint
    with_hint_discovered_total: float
    with_hint_confidence: float
    with_hint_labels_count: int
    with_hint_correct: bool
    
    # Results without hint
    without_hint_discovered_total: float
    without_hint_confidence: float
    without_hint_labels_count: int
    without_hint_correct: bool
    
    # Comparison metrics
    hint_accuracy_delta: float  # positive = hint helped accuracy
    hint_confidence_delta: float  # positive = hint increased confidence


async def run_ab_test_single_receipt(
    client: DynamoClient,
    image_id: str, 
    receipt_id: int, 
    ground_truth_total: float
) -> ABTestResult:
    """Run A/B test on a single receipt."""
    
    receipt_identifier = f"{image_id}/{receipt_id}"
    print(f"\n🧪 A/B TESTING: {receipt_identifier}")
    print(f"   Ground truth total: ${ground_truth_total:.2f}")
    print("=" * 60)
    
    # Arm A: WITH hint (current approach)
    print("\n🔵 ARM A: WITH GRAND_TOTAL HINT")
    result_with_hint = await analyze_costco_receipt(
        client, image_id, receipt_id, ground_truth_total
    )
    
    # Extract discovered grand total from labels
    with_hint_grand_totals = [
        label for label in result_with_hint.discovered_labels 
        if label.label_type.value == "GRAND_TOTAL"
    ]
    with_hint_discovered = with_hint_grand_totals[0].value if with_hint_grand_totals else 0.0
    with_hint_correct = abs(with_hint_discovered - ground_truth_total) < 0.01
    
    # Arm B: WITHOUT hint (ablation)
    print("\n🔴 ARM B: WITHOUT GRAND_TOTAL HINT")
    result_without_hint = await analyze_costco_receipt(
        client, image_id, receipt_id, None  # No hint provided
    )
    
    # Extract discovered grand total from labels  
    without_hint_grand_totals = [
        label for label in result_without_hint.discovered_labels
        if label.label_type.value == "GRAND_TOTAL"
    ]
    without_hint_discovered = without_hint_grand_totals[0].value if without_hint_grand_totals else 0.0
    without_hint_correct = abs(without_hint_discovered - ground_truth_total) < 0.01
    
    # Calculate deltas
    accuracy_delta = (1 if with_hint_correct else 0) - (1 if without_hint_correct else 0)
    confidence_delta = result_with_hint.confidence_score - result_without_hint.confidence_score
    
    return ABTestResult(
        receipt_id=receipt_identifier,
        ground_truth_total=ground_truth_total,
        
        with_hint_discovered_total=with_hint_discovered,
        with_hint_confidence=result_with_hint.confidence_score,
        with_hint_labels_count=len(result_with_hint.discovered_labels),
        with_hint_correct=with_hint_correct,
        
        without_hint_discovered_total=without_hint_discovered,
        without_hint_confidence=result_without_hint.confidence_score, 
        without_hint_labels_count=len(result_without_hint.discovered_labels),
        without_hint_correct=without_hint_correct,
        
        hint_accuracy_delta=accuracy_delta,
        hint_confidence_delta=confidence_delta,
    )


def analyze_ab_results(results: List[ABTestResult]) -> Dict:
    """Analyze A/B test results and compute summary statistics."""
    
    if not results:
        return {}
    
    # Accuracy rates
    with_hint_accuracy = sum(1 for r in results if r.with_hint_correct) / len(results)
    without_hint_accuracy = sum(1 for r in results if r.without_hint_correct) / len(results)
    
    # Confidence scores
    avg_with_hint_confidence = sum(r.with_hint_confidence for r in results) / len(results)
    avg_without_hint_confidence = sum(r.without_hint_confidence for r in results) / len(results)
    
    # Label counts
    avg_with_hint_labels = sum(r.with_hint_labels_count for r in results) / len(results)
    avg_without_hint_labels = sum(r.without_hint_labels_count for r in results) / len(results)
    
    # Deltas
    avg_accuracy_delta = sum(r.hint_accuracy_delta for r in results) / len(results)
    avg_confidence_delta = sum(r.hint_confidence_delta for r in results) / len(results)
    
    # Grand total discovery errors
    with_hint_errors = [
        abs(r.with_hint_discovered_total - r.ground_truth_total) 
        for r in results
    ]
    without_hint_errors = [
        abs(r.without_hint_discovered_total - r.ground_truth_total) 
        for r in results
    ]
    
    avg_with_hint_error = sum(with_hint_errors) / len(with_hint_errors)
    avg_without_hint_error = sum(without_hint_errors) / len(without_hint_errors)
    
    return {
        "test_size": len(results),
        "accuracy": {
            "with_hint": with_hint_accuracy,
            "without_hint": without_hint_accuracy,
            "delta": with_hint_accuracy - without_hint_accuracy,
            "hint_helps_accuracy": with_hint_accuracy > without_hint_accuracy
        },
        "confidence": {
            "with_hint": avg_with_hint_confidence,
            "without_hint": avg_without_hint_confidence,
            "delta": avg_confidence_delta,
            "hint_increases_confidence": avg_confidence_delta > 0
        },
        "labels_discovered": {
            "with_hint": avg_with_hint_labels,
            "without_hint": avg_without_hint_labels,
            "delta": avg_with_hint_labels - avg_without_hint_labels
        },
        "grand_total_error": {
            "with_hint": avg_with_hint_error,
            "without_hint": avg_without_hint_error,
            "delta": avg_with_hint_error - avg_without_hint_error,
            "hint_reduces_error": avg_with_hint_error < avg_without_hint_error
        },
        "individual_deltas": {
            "accuracy": avg_accuracy_delta,
            "confidence": avg_confidence_delta
        }
    }


def display_ab_results(analysis: Dict, results: List[ABTestResult]):
    """Display comprehensive A/B test results."""
    
    print("\n" + "=" * 80)
    print("📊 A/B TEST RESULTS: WITH vs WITHOUT GRAND_TOTAL HINT")
    print("=" * 80)
    
    print(f"Test size: {analysis['test_size']} receipts")
    print()
    
    # Accuracy comparison
    acc = analysis['accuracy']
    print("🎯 ACCURACY COMPARISON:")
    print(f"   With hint:    {acc['with_hint']:.1%} correct")
    print(f"   Without hint: {acc['without_hint']:.1%} correct")
    print(f"   Delta:        {acc['delta']:+.1%} {'✅' if acc['hint_helps_accuracy'] else '❌'}")
    print()
    
    # Confidence comparison
    conf = analysis['confidence']
    print("🤖 CONFIDENCE COMPARISON:")
    print(f"   With hint:    {conf['with_hint']:.3f} average")
    print(f"   Without hint: {conf['without_hint']:.3f} average")
    print(f"   Delta:        {conf['delta']:+.3f} {'✅' if conf['hint_increases_confidence'] else '❌'}")
    print()
    
    # Error comparison
    err = analysis['grand_total_error']
    print("💰 GRAND TOTAL ERROR COMPARISON:")
    print(f"   With hint:    ${err['with_hint']:.4f} average error")
    print(f"   Without hint: ${err['without_hint']:.4f} average error")
    print(f"   Delta:        ${err['delta']:+.4f} {'✅' if err['hint_reduces_error'] else '❌'}")
    print()
    
    # Labels discovered
    labels = analysis['labels_discovered']
    print("🏷️ LABELS DISCOVERED:")
    print(f"   With hint:    {labels['with_hint']:.1f} average")
    print(f"   Without hint: {labels['without_hint']:.1f} average")
    print(f"   Delta:        {labels['delta']:+.1f}")
    print()
    
    # Individual receipt results
    print("📋 INDIVIDUAL RECEIPT RESULTS:")
    print("Receipt                                        | With Hint | Without | Accuracy | Confidence")
    print("-" * 90)
    
    for result in results:
        with_status = "✅" if result.with_hint_correct else "❌"
        without_status = "✅" if result.without_hint_correct else "❌"
        acc_delta = f"{result.hint_accuracy_delta:+.0f}"
        conf_delta = f"{result.hint_confidence_delta:+.3f}"
        
        print(f"{result.receipt_id:<45} | {with_status:<8} | {without_status:<7} | {acc_delta:<8} | {conf_delta}")
    
    print()
    
    # Recommendations
    print("🔮 RECOMMENDATIONS:")
    if acc['hint_helps_accuracy']:
        print("   ✅ KEEP the GRAND_TOTAL hint - it improves accuracy")
    else:
        print("   ❌ REMOVE the GRAND_TOTAL hint - it doesn't help accuracy")
    
    if conf['hint_increases_confidence']:
        print("   ✅ Hint increases model confidence")
    else:
        print("   ⚠️ Hint decreases model confidence")
    
    if err['hint_reduces_error']:
        print("   ✅ Hint reduces grand total prediction errors")
    else:
        print("   ⚠️ Hint increases grand total prediction errors")


async def main():
    """Run A/B test on COSTCO receipts."""
    
    print("🧪 COSTCO RECEIPT GRAND_TOTAL HINT A/B TEST")
    print("=" * 80)
    print("Testing whether providing known grand total helps or hurts performance")
    print()
    
    # Setup LangSmith tracing with specific project for A/B test
    os.environ["LANGCHAIN_PROJECT"] = "costco-ab-test-grand-total-hint"
    setup_langsmith_tracing()
    print()
    
    # Initialize DynamoDB client
    env_vars = load_env()
    client = DynamoClient(env_vars.get("dynamodb_table_name"))
    
    # COSTCO test receipts with known grand totals
    test_receipts = [
        ("6cd1f7f5-d988-4e11-9209-cb6535fc3b04", 1, 198.93),
        ("314ac65b-2b97-45d6-81c2-e48fb0b8cef4", 1, 87.71),
        ("a861f6a6-8d6d-42bc-907c-3330d8bd2022", 1, 35.68),
        ("8388d1f1-b5d6-4560-b7dc-db273815dda1", 1, 37.66),
    ]
    
    # Run A/B test on all receipts
    ab_results = []
    
    for image_id, receipt_id, ground_truth_total in test_receipts:
        try:
            result = await run_ab_test_single_receipt(
                client, image_id, receipt_id, ground_truth_total
            )
            ab_results.append(result)
        except Exception as e:
            logger.error(f"Error testing {image_id}/{receipt_id}: {e}")
            continue
    
    # Analyze and display results
    if ab_results:
        analysis = analyze_ab_results(ab_results)
        display_ab_results(analysis, ab_results)
        
        print(f"\n🔗 Check detailed traces at: https://smith.langchain.com/")
        print(f"   Project: costco-ab-test-grand-total-hint")
    else:
        print("❌ No successful A/B test results to analyze")


if __name__ == "__main__":
    asyncio.run(main())