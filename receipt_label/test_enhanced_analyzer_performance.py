#!/usr/bin/env python3
"""
Test the enhanced pattern analyzer performance against sample receipt data.
"""

import json
import logging
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

# Import our analyzer
from receipt_label.pattern_detection.enhanced_pattern_analyzer import (
    EnhancedPatternAnalyzer,
    enhanced_pattern_analysis
)


@dataclass
class TestReceipt:
    """Simple test receipt data structure."""
    name: str
    currency_contexts: List[Dict]
    expected_subtotal: Optional[float] = None
    expected_tax: Optional[float] = None
    expected_total: Optional[float] = None
    expected_line_items: Optional[int] = None


def create_test_receipts() -> List[TestReceipt]:
    """Create test receipt data based on common patterns."""
    return [
        # Test 1: Simple grocery receipt
        TestReceipt(
            name="Simple Grocery Receipt",
            currency_contexts=[
                {
                    'amount': 2.99,
                    'text': '$2.99',
                    'line_id': '1',
                    'x_position': 200,
                    'y_position': 100,
                    'full_line': 'Milk $2.99',
                    'left_text': 'Milk'
                },
                {
                    'amount': 5.98,
                    'text': '$5.98',
                    'line_id': '2',
                    'x_position': 200,
                    'y_position': 120,
                    'full_line': 'Bread 2 @ $2.99 $5.98',
                    'left_text': 'Bread 2 @ $2.99'
                },
                {
                    'amount': 8.97,
                    'text': '$8.97',
                    'line_id': '3',
                    'x_position': 200,
                    'y_position': 200,
                    'full_line': 'SUBTOTAL $8.97',
                    'left_text': 'SUBTOTAL'
                },
                {
                    'amount': 0.72,
                    'text': '$0.72',
                    'line_id': '4',
                    'x_position': 200,
                    'y_position': 220,
                    'full_line': 'TAX $0.72',
                    'left_text': 'TAX'
                },
                {
                    'amount': 9.69,
                    'text': '$9.69',
                    'line_id': '5',
                    'x_position': 200,
                    'y_position': 240,
                    'full_line': 'TOTAL $9.69',
                    'left_text': 'TOTAL'
                }
            ],
            expected_subtotal=8.97,
            expected_tax=0.72,
            expected_total=9.69,
            expected_line_items=2
        ),
        
        # Test 2: Restaurant receipt with tip
        TestReceipt(
            name="Restaurant Receipt",
            currency_contexts=[
                {
                    'amount': 12.95,
                    'text': '$12.95',
                    'line_id': '1',
                    'x_position': 200,
                    'y_position': 100,
                    'full_line': 'Burger $12.95',
                    'left_text': 'Burger'
                },
                {
                    'amount': 8.50,
                    'text': '$8.50',
                    'line_id': '2',
                    'x_position': 200,
                    'y_position': 120,
                    'full_line': 'Fries $8.50',
                    'left_text': 'Fries'
                },
                {
                    'amount': 21.45,
                    'text': '$21.45',
                    'line_id': '3',
                    'x_position': 200,
                    'y_position': 180,
                    'full_line': 'Subtotal $21.45',
                    'left_text': 'Subtotal'
                },
                {
                    'amount': 1.93,
                    'text': '$1.93',
                    'line_id': '4',
                    'x_position': 200,
                    'y_position': 200,
                    'full_line': 'Sales Tax $1.93',
                    'left_text': 'Sales Tax'
                },
                {
                    'amount': 23.38,
                    'text': '$23.38',
                    'line_id': '5',
                    'x_position': 200,
                    'y_position': 220,
                    'full_line': 'Total Due $23.38',
                    'left_text': 'Total Due'
                }
            ],
            expected_subtotal=21.45,
            expected_tax=1.93,
            expected_total=23.38,
            expected_line_items=2
        ),
        
        # Test 3: Receipt with quantity patterns
        TestReceipt(
            name="Receipt with Quantities",
            currency_contexts=[
                {
                    'amount': 11.97,
                    'text': '$11.97',
                    'line_id': '1',
                    'x_position': 200,
                    'y_position': 100,
                    'full_line': 'Apples 3 @ $3.99 $11.97',
                    'left_text': 'Apples 3 @ $3.99'
                },
                {
                    'amount': 15.00,
                    'text': '$15.00',
                    'line_id': '2',
                    'x_position': 200,
                    'y_position': 120,
                    'full_line': 'Ground Beef 2.5 lb $15.00',
                    'left_text': 'Ground Beef 2.5 lb'
                },
                {
                    'amount': 7.98,
                    'text': '$7.98',
                    'line_id': '3',
                    'x_position': 200,
                    'y_position': 140,
                    'full_line': 'Soda 2 x $3.99 $7.98',
                    'left_text': 'Soda 2 x $3.99'
                },
                {
                    'amount': 34.95,
                    'text': '$34.95',
                    'line_id': '4',
                    'x_position': 200,
                    'y_position': 200,
                    'full_line': 'NET TOTAL $34.95',
                    'left_text': 'NET TOTAL'
                },
                {
                    'amount': 2.80,
                    'text': '$2.80',
                    'line_id': '5',
                    'x_position': 200,
                    'y_position': 220,
                    'full_line': 'TAX AMOUNT $2.80',
                    'left_text': 'TAX AMOUNT'
                },
                {
                    'amount': 37.75,
                    'text': '$37.75',
                    'line_id': '6',
                    'x_position': 200,
                    'y_position': 240,
                    'full_line': 'GRAND TOTAL $37.75',
                    'left_text': 'GRAND TOTAL'
                }
            ],
            expected_subtotal=34.95,
            expected_tax=2.80,
            expected_total=37.75,
            expected_line_items=3
        ),
        
        # Test 4: Receipt with discount
        TestReceipt(
            name="Receipt with Discount",
            currency_contexts=[
                {
                    'amount': 19.99,
                    'text': '$19.99',
                    'line_id': '1',
                    'x_position': 200,
                    'y_position': 100,
                    'full_line': 'Shirt $19.99',
                    'left_text': 'Shirt'
                },
                {
                    'amount': 29.99,
                    'text': '$29.99',
                    'line_id': '2',
                    'x_position': 200,
                    'y_position': 120,
                    'full_line': 'Pants $29.99',
                    'left_text': 'Pants'
                },
                {
                    'amount': 5.00,
                    'text': '-$5.00',
                    'line_id': '3',
                    'x_position': 200,
                    'y_position': 140,
                    'full_line': 'Coupon Discount -$5.00',
                    'left_text': 'Coupon Discount'
                },
                {
                    'amount': 44.98,
                    'text': '$44.98',
                    'line_id': '4',
                    'x_position': 200,
                    'y_position': 180,
                    'full_line': 'Subtotal $44.98',
                    'left_text': 'Subtotal'
                },
                {
                    'amount': 3.60,
                    'text': '$3.60',
                    'line_id': '5',
                    'x_position': 200,
                    'y_position': 200,
                    'full_line': 'Tax $3.60',
                    'left_text': 'Tax'
                },
                {
                    'amount': 48.58,
                    'text': '$48.58',
                    'line_id': '6',
                    'x_position': 200,
                    'y_position': 220,
                    'full_line': 'Total $48.58',
                    'left_text': 'Total'
                }
            ],
            expected_subtotal=44.98,
            expected_tax=3.60,
            expected_total=48.58,
            expected_line_items=2  # Discount should not be counted as line item
        ),
        
        # Test 5: Complex receipt without explicit labels
        TestReceipt(
            name="Receipt without Clear Labels",
            currency_contexts=[
                {
                    'amount': 4.99,
                    'text': '4.99',
                    'line_id': '1',
                    'x_position': 200,
                    'y_position': 100,
                    'full_line': 'Item A 4.99',
                    'left_text': 'Item A'
                },
                {
                    'amount': 7.49,
                    'text': '7.49',
                    'line_id': '2',
                    'x_position': 200,
                    'y_position': 120,
                    'full_line': 'Item B 7.49',
                    'left_text': 'Item B'
                },
                {
                    'amount': 12.48,
                    'text': '12.48',
                    'line_id': '3',
                    'x_position': 200,
                    'y_position': 200,
                    'full_line': '12.48',
                    'left_text': ''
                },
                {
                    'amount': 1.00,
                    'text': '1.00',
                    'line_id': '4',
                    'x_position': 200,
                    'y_position': 220,
                    'full_line': '1.00',
                    'left_text': ''
                },
                {
                    'amount': 13.48,
                    'text': '13.48',
                    'line_id': '5',
                    'x_position': 200,
                    'y_position': 240,
                    'full_line': '13.48',
                    'left_text': ''
                }
            ],
            expected_subtotal=12.48,  # Should infer from position
            expected_tax=1.00,        # Should infer from amount (8% of subtotal)
            expected_total=13.48,     # Should infer as largest at bottom
            expected_line_items=2
        )
    ]


def evaluate_results(test_receipt: TestReceipt, result: Dict) -> Dict:
    """Evaluate the analysis results against expected values."""
    evaluation = {
        'name': test_receipt.name,
        'passed': True,
        'details': [],
        'accuracy_scores': {}
    }
    
    # Check financial summary
    if test_receipt.expected_subtotal is not None:
        actual_subtotal = result['financial_summary'].get('subtotal')
        if actual_subtotal is None:
            evaluation['passed'] = False
            evaluation['details'].append(f"Missing subtotal (expected {test_receipt.expected_subtotal})")
            evaluation['accuracy_scores']['subtotal'] = 0
        elif abs(actual_subtotal - test_receipt.expected_subtotal) > 0.01:
            evaluation['passed'] = False
            evaluation['details'].append(
                f"Incorrect subtotal: {actual_subtotal} (expected {test_receipt.expected_subtotal})"
            )
            evaluation['accuracy_scores']['subtotal'] = 0
        else:
            evaluation['accuracy_scores']['subtotal'] = 1
    
    if test_receipt.expected_tax is not None:
        actual_tax = result['financial_summary'].get('tax')
        if actual_tax is None:
            evaluation['passed'] = False
            evaluation['details'].append(f"Missing tax (expected {test_receipt.expected_tax})")
            evaluation['accuracy_scores']['tax'] = 0
        elif abs(actual_tax - test_receipt.expected_tax) > 0.01:
            evaluation['passed'] = False
            evaluation['details'].append(
                f"Incorrect tax: {actual_tax} (expected {test_receipt.expected_tax})"
            )
            evaluation['accuracy_scores']['tax'] = 0
        else:
            evaluation['accuracy_scores']['tax'] = 1
    
    if test_receipt.expected_total is not None:
        actual_total = result['financial_summary'].get('total')
        if actual_total is None:
            evaluation['passed'] = False
            evaluation['details'].append(f"Missing total (expected {test_receipt.expected_total})")
            evaluation['accuracy_scores']['total'] = 0
        elif abs(actual_total - test_receipt.expected_total) > 0.01:
            evaluation['passed'] = False
            evaluation['details'].append(
                f"Incorrect total: {actual_total} (expected {test_receipt.expected_total})"
            )
            evaluation['accuracy_scores']['total'] = 0
        else:
            evaluation['accuracy_scores']['total'] = 1
    
    # Check line items
    if test_receipt.expected_line_items is not None:
        actual_items = len(result.get('line_items', []))
        if actual_items != test_receipt.expected_line_items:
            evaluation['passed'] = False
            evaluation['details'].append(
                f"Incorrect line item count: {actual_items} (expected {test_receipt.expected_line_items})"
            )
            evaluation['accuracy_scores']['line_items'] = actual_items / test_receipt.expected_line_items
        else:
            evaluation['accuracy_scores']['line_items'] = 1
    
    # Check for quantity extraction in line items
    quantity_count = 0
    for item in result.get('line_items', []):
        if 'quantity' in item:
            quantity_count += 1
    
    if result.get('line_items'):
        evaluation['accuracy_scores']['quantity_extraction'] = quantity_count / len(result['line_items'])
    
    # Overall confidence
    evaluation['confidence'] = result.get('confidence', 0)
    
    return evaluation


def main():
    """Run the performance test."""
    logger.info("=" * 70)
    logger.info("Enhanced Pattern Analyzer Performance Test")
    logger.info("=" * 70)
    
    # Create test receipts
    test_receipts = create_test_receipts()
    logger.info(f"\nTesting against {len(test_receipts)} sample receipts...\n")
    
    # Initialize analyzer
    analyzer = EnhancedPatternAnalyzer()
    
    # Track results
    all_evaluations = []
    total_accuracy = {
        'subtotal': [],
        'tax': [],
        'total': [],
        'line_items': [],
        'quantity_extraction': []
    }
    
    # Process each test receipt
    for test_receipt in test_receipts:
        logger.info(f"\nProcessing: {test_receipt.name}")
        logger.info("-" * 40)
        
        # Analyze
        result = analyzer.analyze_currency_contexts(
            test_receipt.currency_contexts,
            receipt_height=300  # Approximate height
        )
        
        # Evaluate
        evaluation = evaluate_results(test_receipt, result)
        all_evaluations.append(evaluation)
        
        # Log results
        status = "✅ PASSED" if evaluation['passed'] else "❌ FAILED"
        logger.info(f"Status: {status}")
        logger.info(f"Confidence: {evaluation['confidence']:.2f}")
        
        if evaluation['details']:
            logger.info("Issues:")
            for detail in evaluation['details']:
                logger.info(f"  - {detail}")
        
        # Log what was found
        logger.info(f"\nFound:")
        logger.info(f"  Subtotal: ${result['financial_summary'].get('subtotal') or 'None'}")
        logger.info(f"  Tax: ${result['financial_summary'].get('tax') or 'None'}")
        logger.info(f"  Total: ${result['financial_summary'].get('total') or 'None'}")
        logger.info(f"  Line Items: {len(result.get('line_items', []))}")
        
        # Show line items with quantities
        for item in result.get('line_items', []):
            desc = item.get('description', 'Unknown')
            amt = item.get('amount', 0)
            if 'quantity' in item:
                logger.info(f"    - {desc}: ${amt} (Qty: {item['quantity']})")
            else:
                logger.info(f"    - {desc}: ${amt}")
        
        # Track accuracy
        for metric, score in evaluation['accuracy_scores'].items():
            if metric in total_accuracy:
                total_accuracy[metric].append(score)
    
    # Calculate overall performance
    logger.info("\n" + "=" * 70)
    logger.info("OVERALL PERFORMANCE SUMMARY")
    logger.info("=" * 70)
    
    passed = sum(1 for e in all_evaluations if e['passed'])
    total = len(all_evaluations)
    logger.info(f"\nTests Passed: {passed}/{total} ({passed/total*100:.1f}%)")
    
    logger.info("\nAccuracy by Component:")
    for metric, scores in total_accuracy.items():
        if scores:
            avg_score = sum(scores) / len(scores)
            logger.info(f"  {metric.replace('_', ' ').title()}: {avg_score*100:.1f}%")
    
    # Overall accuracy
    all_scores = []
    for scores in total_accuracy.values():
        all_scores.extend(scores)
    
    if all_scores:
        overall_accuracy = sum(all_scores) / len(all_scores)
        logger.info(f"\nOverall Accuracy: {overall_accuracy*100:.1f}%")
        
        if overall_accuracy >= 0.80:
            logger.info("\n✅ Target accuracy of 80-85% achieved!")
        else:
            logger.info(f"\n⚠️  Below target accuracy of 80-85%")
    
    # Average confidence
    avg_confidence = sum(e['confidence'] for e in all_evaluations) / len(all_evaluations)
    logger.info(f"Average Confidence: {avg_confidence:.2f}")
    
    logger.info("\n" + "=" * 70)


if __name__ == "__main__":
    main()