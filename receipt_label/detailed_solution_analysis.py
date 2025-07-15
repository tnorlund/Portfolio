#!/usr/bin/env python3
"""
Detailed analysis showing exactly what our spatial/math solver is finding.
Focuses on showing the line items and how they add up.
"""

import os
import asyncio
from pathlib import Path
import json
from collections import defaultdict

# Set up environment  
os.environ["OPENAI_API_KEY"] = "sk-dummy"

from receipt_label.spatial.math_solver_detector import MathSolverDetector
from receipt_label.spatial.vertical_alignment_detector import VerticalAlignmentDetector
from receipt_label.pattern_detection.orchestrator import ParallelPatternOrchestrator
from receipt_dynamo.entities.receipt_word import ReceiptWord


def load_receipt_words(file_path: Path):
    """Load words from receipt."""
    try:
        with open(file_path, 'r') as f:
            data = json.load(f)
        
        words = []
        for word_data in data.get('words', []):
            word = ReceiptWord(
                image_id=word_data['image_id'],
                line_id=word_data['line_id'],
                word_id=word_data['word_id'],
                text=word_data['text'],
                bounding_box=word_data['bounding_box'],
                top_right=word_data['top_right'],
                top_left=word_data['top_left'],
                bottom_right=word_data['bottom_right'],
                bottom_left=word_data['bottom_left'],
                angle_degrees=word_data.get('angle_degrees', 0.0),
                angle_radians=word_data.get('angle_radians', 0.0),
                confidence=word_data['confidence'],
                extracted_data=word_data.get('extracted_data', {}),
                receipt_id=int(word_data.get('receipt_id', 1))
            )
            words.append(word)
        
        # Get merchant from metadata
        merchant = 'Unknown'
        receipt_metadatas = data.get('receipt_metadatas', [])
        if receipt_metadatas:
            merchant = receipt_metadatas[0].get('merchant_name', 'Unknown')
            
        return words, merchant
    except Exception as e:
        return [], f"Load error: {str(e)[:50]}..."


def get_line_text(words, line_id):
    """Get full text for a specific line."""
    line_words = [(w.word_id, w.text) for w in words if w.line_id == line_id]
    line_words.sort(key=lambda x: x[0])
    return ' '.join([text for _, text in line_words])


async def analyze_sprouts_receipt():
    """Analyze a specific Sprouts receipt to show detailed breakdown."""
    
    # Let's analyze the Sprouts receipt we saw earlier
    file_path = Path("./receipt_data_with_labels/7c932424-cb54-4fdb-ac2d-465e9b57b7c8.json")
    
    # Load receipt
    words, merchant = load_receipt_words(file_path)
    
    print(f"🛒 DETAILED ANALYSIS: {merchant}")
    print("=" * 80)
    
    # Create detectors
    pattern_orchestrator = ParallelPatternOrchestrator(timeout=10.0, use_adaptive_selection=False)
    alignment_detector = VerticalAlignmentDetector(alignment_tolerance=0.02, use_enhanced_clustering=True)
    math_solver = MathSolverDetector(tolerance=0.02, max_solutions=50, use_numpy_optimization=True)
    
    # Get pattern matches
    pattern_results = await pattern_orchestrator.detect_all_patterns(words)
    all_matches = []
    for detector_name, matches in pattern_results.items():
        if detector_name != '_metadata':
            all_matches.extend(matches)
    
    # Extract currency values
    currency_patterns = {'CURRENCY', 'GRAND_TOTAL', 'SUBTOTAL', 'TAX', 'DISCOUNT', 'UNIT_PRICE', 'LINE_TOTAL'}
    currency_values = []
    currency_details = []  # Track details for display
    
    for match in all_matches:
        if match.pattern_type.name in currency_patterns and match.extracted_value:
            try:
                value = float(match.extracted_value)
                if 0.001 <= abs(value) <= 999.99:
                    currency_values.append((value, match))
                    line_text = get_line_text(words, match.word.line_id)
                    currency_details.append({
                        'value': value,
                        'line_id': match.word.line_id,
                        'line_text': line_text,
                        'pattern': match.pattern_type.name
                    })
            except (ValueError, TypeError):
                continue
    
    print(f"\n📊 FOUND {len(currency_values)} CURRENCY VALUES:")
    for detail in sorted(currency_details, key=lambda x: x['line_id'])[:20]:
        print(f"  Line {detail['line_id']:3d}: ${detail['value']:7.2f} - \"{detail['line_text']}\"")
    
    # Spatial analysis
    alignment_result = alignment_detector.detect_line_items_with_alignment(words, all_matches)
    price_columns = alignment_detector.detect_price_columns([v[1] for v in currency_values])
    
    print(f"\n📍 SPATIAL ANALYSIS:")
    print(f"  Price columns detected: {len(price_columns)}")
    if price_columns:
        best_column = max(price_columns, key=lambda c: c.confidence)
        print(f"  Best column confidence: {best_column.confidence:.2f}")
        print(f"  Column X position: {best_column.x_center:.3f}")
        print(f"  Prices in column: {len(best_column.prices)}")
    
    # Filter to price columns
    if price_columns:
        column_lines = set()
        for column in price_columns:
            column_lines.update(p.word.line_id for p in column.prices)
        
        column_currencies = [
            (value, match) for value, match in currency_values
            if match.word.line_id in column_lines
        ]
        
        print(f"\n📐 FILTERED TO PRICE COLUMN:")
        print(f"  Values in column: {len(column_currencies)}")
        for value, match in sorted(column_currencies, key=lambda x: x[1].word.line_id)[:10]:
            line_text = get_line_text(words, match.word.line_id)
            print(f"    Line {match.word.line_id:3d}: ${value:7.2f} - \"{line_text}\"")
    else:
        column_currencies = currency_values
    
    # Mathematical validation
    solutions = math_solver.solve_receipt_math(column_currencies)
    
    if solutions:
        best_solution = max(solutions, key=lambda s: s.confidence)
        
        print(f"\n🧮 MATHEMATICAL SOLUTION:")
        print(f"  Total solutions found: {len(solutions)}")
        print(f"  Best solution confidence: {best_solution.confidence:.2f}")
        
        print(f"\n  BREAKDOWN:")
        print(f"  Line Items ({len(best_solution.item_prices)}):")
        items_total = 0
        for i, (price, match) in enumerate(best_solution.item_prices):
            line_text = get_line_text(words, match.word.line_id)
            print(f"    {i+1}. ${price:7.2f} (Line {match.word.line_id:3d}) - \"{line_text}\"")
            items_total += price
        
        print(f"  ─────────────")
        print(f"  Items Total:     ${items_total:7.2f}")
        print(f"  Detected Subtotal: ${best_solution.subtotal:7.2f}")
        
        if best_solution.tax:
            print(f"  Tax:             ${best_solution.tax[0]:7.2f}")
            tax_line_text = get_line_text(words, best_solution.tax[1].word.line_id)
            print(f"                   (Line {best_solution.tax[1].word.line_id}: \"{tax_line_text}\")")
        
        print(f"  ─────────────")
        print(f"  Grand Total:     ${best_solution.grand_total[0]:7.2f}")
        total_line_text = get_line_text(words, best_solution.grand_total[1].word.line_id)
        print(f"                   (Line {best_solution.grand_total[1].word.line_id}: \"{total_line_text}\")")
        
        # Verify the math
        print(f"\n✅ VERIFICATION:")
        calculated_total = best_solution.subtotal + (best_solution.tax[0] if best_solution.tax else 0)
        print(f"  Subtotal + Tax = ${best_solution.subtotal:.2f} + ${best_solution.tax[0] if best_solution.tax else 0:.2f} = ${calculated_total:.2f}")
        print(f"  Grand Total = ${best_solution.grand_total[0]:.2f}")
        print(f"  Difference: ${abs(calculated_total - best_solution.grand_total[0]):.2f}")


async def analyze_multiple_receipts():
    """Analyze several receipts to show variety."""
    
    receipt_files = [
        "03fa2d0f-33c6-43be-88b0-dae73ec26c93.json",  # Another receipt
        "a0301717-d765-4f34-a15d-48c362ebf9fd.json",  # Sprouts with multiple items
    ]
    
    for file_name in receipt_files:
        file_path = Path("./receipt_data_with_labels") / file_name
        if not file_path.exists():
            continue
            
        print(f"\n\n{'='*80}")
        print(f"ANALYZING: {file_name}")
        print('='*80)
        
        await analyze_detailed_receipt(file_path)


async def analyze_detailed_receipt(file_path):
    """Analyze a single receipt with detailed breakdown."""
    
    # Load receipt
    words, merchant = load_receipt_words(file_path)
    
    print(f"Merchant: {merchant}")
    
    # Create detectors
    pattern_orchestrator = ParallelPatternOrchestrator(timeout=10.0, use_adaptive_selection=False)
    alignment_detector = VerticalAlignmentDetector(alignment_tolerance=0.02, use_enhanced_clustering=True)
    math_solver = MathSolverDetector(tolerance=0.02, max_solutions=50, use_numpy_optimization=True)
    
    try:
        # Get pattern matches
        pattern_results = await pattern_orchestrator.detect_all_patterns(words)
        all_matches = []
        for detector_name, matches in pattern_results.items():
            if detector_name != '_metadata':
                all_matches.extend(matches)
        
        # Extract currency values
        currency_patterns = {'CURRENCY', 'GRAND_TOTAL', 'SUBTOTAL', 'TAX', 'DISCOUNT', 'UNIT_PRICE', 'LINE_TOTAL'}
        currency_values = []
        
        for match in all_matches:
            if match.pattern_type.name in currency_patterns and match.extracted_value:
                try:
                    value = float(match.extracted_value)
                    if 0.001 <= abs(value) <= 999.99:
                        currency_values.append((value, match))
                except (ValueError, TypeError):
                    continue
        
        # Spatial analysis
        alignment_result = alignment_detector.detect_line_items_with_alignment(words, all_matches)
        price_columns = alignment_detector.detect_price_columns([v[1] for v in currency_values])
        
        # Filter to price columns
        if price_columns:
            column_lines = set()
            for column in price_columns:
                column_lines.update(p.word.line_id for p in column.prices)
            
            column_currencies = [
                (value, match) for value, match in currency_values
                if match.word.line_id in column_lines
            ]
        else:
            column_currencies = currency_values
        
        # Mathematical validation
        solutions = math_solver.solve_receipt_math(column_currencies)
        
        if solutions:
            best_solution = max(solutions, key=lambda s: s.confidence)
            
            print(f"\n📋 SOLUTION SUMMARY:")
            print(f"  Line Items: {len(best_solution.item_prices)}")
            print(f"  Subtotal: ${best_solution.subtotal:.2f}")
            if best_solution.tax:
                print(f"  Tax: ${best_solution.tax[0]:.2f}")
            print(f"  Grand Total: ${best_solution.grand_total[0]:.2f}")
            print(f"  Confidence: {best_solution.confidence:.2f}")
            
            # Show first few line items
            print(f"\n  Sample Line Items:")
            for i, (price, match) in enumerate(best_solution.item_prices[:5]):
                line_text = get_line_text(words, match.word.line_id)
                print(f"    ${price:7.2f} - \"{line_text[:60]}...\"" if len(line_text) > 60 else f"    ${price:7.2f} - \"{line_text}\"")
            
            if len(best_solution.item_prices) > 5:
                print(f"    ... and {len(best_solution.item_prices) - 5} more items")
        else:
            print("\n  No mathematical solution found")
            
    except Exception as e:
        print(f"\n  Error analyzing receipt: {str(e)}")


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.WARNING)
    
    print("🔍 DETAILED RECEIPT SOLUTION ANALYSIS")
    print("Showing exactly what our spatial/math solver finds\n")
    
    asyncio.run(analyze_sprouts_receipt())
    asyncio.run(analyze_multiple_receipts())