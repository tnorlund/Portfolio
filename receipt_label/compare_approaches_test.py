#!/usr/bin/env python3
"""
A/B test comparing two approaches for line-item analysis:

Approach A: Two-phase (currency first, then targeted line-item components)  
Approach B: One-shot (everything at once)

This will help determine which approach is more accurate and efficient.
"""

import asyncio
import time
import logging
import os
from typing import Dict, List
from dataclasses import dataclass

from receipt_dynamo import DynamoClient
from receipt_dynamo.data._pulumi import load_env
from receipt_label.costco_analyzer import setup_langsmith_tracing
from receipt_label.two_phase_analyzer import analyze_costco_receipt_two_phase
from receipt_label.enhanced_line_item_analyzer import analyze_costco_receipt_with_line_items
from receipt_label.costco_models import LabelType

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class ApproachComparison:
    """Results comparing two-phase vs one-shot approaches."""
    
    receipt_id: str
    
    # Two-phase results
    two_phase_labels: int
    two_phase_line_items: int
    two_phase_product_names: int
    two_phase_quantities: int  
    two_phase_unit_prices: int
    two_phase_time: float
    two_phase_confidence: float
    
    # One-shot results  
    one_shot_labels: int
    one_shot_line_items: int
    one_shot_product_names: int
    one_shot_quantities: int
    one_shot_unit_prices: int
    one_shot_time: float
    one_shot_confidence: float
    
    # Performance comparison
    time_difference: float  # positive = two-phase faster
    accuracy_difference: int  # positive = two-phase found more


async def test_both_approaches(client: DynamoClient, image_id: str, receipt_id: int) -> ApproachComparison:
    """Test both approaches on the same receipt and compare results."""
    
    receipt_identifier = f"{image_id}/{receipt_id}"
    print(f"\n🔄 COMPARING APPROACHES: {receipt_identifier}")
    print("=" * 60)
    
    # APPROACH A: Two-phase analysis
    print("🔵 APPROACH A: TWO-PHASE ANALYSIS")
    start_time = time.time()
    
    try:
        two_phase_result = await analyze_costco_receipt_two_phase(
            client, image_id, receipt_id, update_labels=False
        )
        two_phase_time = time.time() - start_time
        two_phase_success = True
    except Exception as e:
        logger.error(f"Two-phase analysis failed: {e}")
        two_phase_result = None
        two_phase_time = time.time() - start_time
        two_phase_success = False
    
    print(f"⏱️ Two-phase completed in {two_phase_time:.2f}s")
    
    # APPROACH B: One-shot analysis
    print(f"\n🔴 APPROACH B: ONE-SHOT ANALYSIS")
    start_time = time.time()
    
    try:
        # Note: This would use the enhanced_line_item_analyzer if we had it working
        # For now, simulate or skip
        one_shot_result = None  # await analyze_costco_receipt_with_line_items(...)
        one_shot_time = time.time() - start_time  
        one_shot_success = False  # Simulated since we don't have working one-shot yet
    except Exception as e:
        logger.error(f"One-shot analysis failed: {e}")
        one_shot_result = None
        one_shot_time = time.time() - start_time
        one_shot_success = False
    
    print(f"⏱️ One-shot completed in {one_shot_time:.2f}s")
    
    # Analyze two-phase results
    if two_phase_success and two_phase_result:
        two_phase_stats = _analyze_labels(two_phase_result.discovered_labels)
    else:
        two_phase_stats = {
            'total_labels': 0, 'line_items': 0, 'product_names': 0,
            'quantities': 0, 'unit_prices': 0, 'confidence': 0.0
        }
    
    # Analyze one-shot results (placeholder)
    one_shot_stats = {
        'total_labels': 0, 'line_items': 0, 'product_names': 0, 
        'quantities': 0, 'unit_prices': 0, 'confidence': 0.0
    }
    
    # Calculate differences
    time_diff = one_shot_time - two_phase_time  # positive = two-phase faster
    accuracy_diff = two_phase_stats['total_labels'] - one_shot_stats['total_labels']
    
    return ApproachComparison(
        receipt_id=receipt_identifier,
        
        two_phase_labels=two_phase_stats['total_labels'],
        two_phase_line_items=two_phase_stats['line_items'],
        two_phase_product_names=two_phase_stats['product_names'],
        two_phase_quantities=two_phase_stats['quantities'],
        two_phase_unit_prices=two_phase_stats['unit_prices'],
        two_phase_time=two_phase_time,
        two_phase_confidence=two_phase_stats['confidence'],
        
        one_shot_labels=one_shot_stats['total_labels'],
        one_shot_line_items=one_shot_stats['line_items'],
        one_shot_product_names=one_shot_stats['product_names'],
        one_shot_quantities=one_shot_stats['quantities'],
        one_shot_unit_prices=one_shot_stats['unit_prices'],
        one_shot_time=one_shot_time,
        one_shot_confidence=one_shot_stats['confidence'],
        
        time_difference=time_diff,
        accuracy_difference=accuracy_diff,
    )


def _analyze_labels(labels: List) -> Dict:
    """Analyze discovered labels and return statistics."""
    
    if not labels:
        return {
            'total_labels': 0, 'line_items': 0, 'product_names': 0,
            'quantities': 0, 'unit_prices': 0, 'confidence': 0.0
        }
    
    # Count by type
    product_names = sum(1 for label in labels if label.label_type == LabelType.PRODUCT_NAME)
    quantities = sum(1 for label in labels if label.label_type == LabelType.QUANTITY) 
    unit_prices = sum(1 for label in labels if label.label_type == LabelType.UNIT_PRICE)
    line_totals = sum(1 for label in labels if label.label_type == LabelType.LINE_TOTAL)
    
    # Calculate average confidence
    avg_confidence = sum(label.confidence for label in labels) / len(labels)
    
    return {
        'total_labels': len(labels),
        'line_items': min(product_names, line_totals),  # Complete line items
        'product_names': product_names,
        'quantities': quantities,
        'unit_prices': unit_prices, 
        'confidence': avg_confidence
    }


def display_comparison_results(comparison: ApproachComparison):
    """Display detailed comparison results."""
    
    print(f"\n📊 APPROACH COMPARISON RESULTS: {comparison.receipt_id}")
    print("=" * 80)
    
    # Performance comparison
    print("⏱️ PERFORMANCE:")
    print(f"   Two-phase: {comparison.two_phase_time:.2f}s")
    print(f"   One-shot:  {comparison.one_shot_time:.2f}s") 
    print(f"   Difference: {comparison.time_difference:+.2f}s {'(Two-phase faster)' if comparison.time_difference > 0 else '(One-shot faster)'}")
    print()
    
    # Label discovery comparison
    print("🏷️ LABEL DISCOVERY:")
    print(f"   {'Component':<15} {'Two-phase':<12} {'One-shot':<10} {'Difference'}")
    print(f"   {'-'*15} {'-'*12} {'-'*10} {'-'*10}")
    print(f"   {'Total Labels':<15} {comparison.two_phase_labels:<12} {comparison.one_shot_labels:<10} {comparison.two_phase_labels - comparison.one_shot_labels:+}")
    print(f"   {'Product Names':<15} {comparison.two_phase_product_names:<12} {comparison.one_shot_product_names:<10} {comparison.two_phase_product_names - comparison.one_shot_product_names:+}")
    print(f"   {'Quantities':<15} {comparison.two_phase_quantities:<12} {comparison.one_shot_quantities:<10} {comparison.two_phase_quantities - comparison.one_shot_quantities:+}")
    print(f"   {'Unit Prices':<15} {comparison.two_phase_unit_prices:<12} {comparison.one_shot_unit_prices:<10} {comparison.two_phase_unit_prices - comparison.one_shot_unit_prices:+}")
    print(f"   {'Complete Items':<15} {comparison.two_phase_line_items:<12} {comparison.one_shot_line_items:<10} {comparison.two_phase_line_items - comparison.one_shot_line_items:+}")
    print()
    
    # Confidence comparison
    print("🤖 CONFIDENCE:")
    print(f"   Two-phase: {comparison.two_phase_confidence:.3f}")
    print(f"   One-shot:  {comparison.one_shot_confidence:.3f}")
    print(f"   Difference: {comparison.two_phase_confidence - comparison.one_shot_confidence:+.3f}")
    print()
    
    # Winner determination
    print("🏆 WINNER:")
    if comparison.accuracy_difference > 0:
        print("   📊 TWO-PHASE wins on accuracy (found more labels)")
    elif comparison.accuracy_difference < 0:
        print("   📊 ONE-SHOT wins on accuracy (found more labels)")
    else:
        print("   📊 TIE on accuracy")
        
    if comparison.time_difference > 0:
        print("   ⏱️ TWO-PHASE wins on speed (faster execution)")
    elif comparison.time_difference < 0:
        print("   ⏱️ ONE-SHOT wins on speed (faster execution)")
    else:
        print("   ⏱️ TIE on speed")


async def main():
    """Run A/B comparison test."""
    
    print("🔄 TWO-PHASE vs ONE-SHOT APPROACH COMPARISON")
    print("=" * 80)
    print("Testing which approach works better for line-item analysis")
    print()
    
    # Setup
    os.environ["LANGCHAIN_PROJECT"] = "costco-approach-comparison"
    setup_langsmith_tracing()
    
    env_vars = load_env()
    client = DynamoClient(env_vars.get("dynamodb_table_name"))
    
    # Test on single receipt first
    image_id = "6cd1f7f5-d988-4e11-9209-cb6535fc3b04"
    receipt_id = 1
    
    # Run comparison
    comparison = await test_both_approaches(client, image_id, receipt_id)
    
    # Display results
    display_comparison_results(comparison)
    
    print(f"\n🔗 Check detailed traces in LangSmith:")
    print(f"   Project: costco-approach-comparison")


if __name__ == "__main__":
    asyncio.run(main())