#!/usr/bin/env python3
"""
Analyze actual solutions vs receipt text to verify accuracy.
Shows the receipt text and what our spatial/math solver found.
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


def get_receipt_text_by_line(words):
    """Organize receipt text by line."""
    lines = defaultdict(list)
    for word in words:
        lines[word.line_id].append((word.word_id, word.text))
    
    # Sort words within each line
    text_by_line = {}
    for line_id, word_list in lines.items():
        word_list.sort(key=lambda x: x[0])  # Sort by word_id
        text_by_line[line_id] = ' '.join([text for _, text in word_list])
    
    return text_by_line


def find_word_in_receipt(words, value):
    """Find which word contains a specific value."""
    value_str = f"{value:.2f}"
    for word in words:
        # Check exact match
        clean_text = word.text.replace('$', '').replace(',', '')
        try:
            if float(clean_text) == value:
                return word
        except:
            pass
    return None


async def analyze_receipt_solution(file_path: Path):
    """Analyze a single receipt and show solution vs actual text."""
    
    # Load receipt
    words, merchant = load_receipt_words(file_path)
    if not words:
        return None
    
    # Get text by line
    text_by_line = get_receipt_text_by_line(words)
    
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
        
        if not solutions:
            return None
        
        # Get best solution
        best_solution = max(solutions, key=lambda s: s.confidence)
        
        # Find the actual words/lines for our solution
        result = {
            'file': file_path.name,
            'merchant': merchant,
            'grand_total': best_solution.grand_total[0],
            'subtotal': best_solution.subtotal,
            'tax': best_solution.tax[0] if best_solution.tax else None,
            'line_items': [price[0] for price in best_solution.item_prices],
            'confidence': best_solution.confidence
        }
        
        # Find which lines contain our detected values
        grand_total_word = find_word_in_receipt(words, best_solution.grand_total[0])
        subtotal_word = find_word_in_receipt(words, best_solution.subtotal)
        tax_word = find_word_in_receipt(words, best_solution.tax[0]) if best_solution.tax else None
        
        if grand_total_word:
            result['grand_total_line'] = text_by_line.get(grand_total_word.line_id, "")
            result['grand_total_line_num'] = grand_total_word.line_id
        
        if subtotal_word:
            result['subtotal_line'] = text_by_line.get(subtotal_word.line_id, "")
            result['subtotal_line_num'] = subtotal_word.line_id
            
        if tax_word:
            result['tax_line'] = text_by_line.get(tax_word.line_id, "")
            result['tax_line_num'] = tax_word.line_id
        
        # Get bottom lines of receipt for context
        max_line = max(text_by_line.keys())
        result['bottom_lines'] = []
        for i in range(max(1, max_line - 10), max_line + 1):
            if i in text_by_line:
                result['bottom_lines'].append(f"Line {i}: {text_by_line[i]}")
        
        return result
        
    except Exception as e:
        return None


async def analyze_sample_receipts():
    """Analyze a sample of receipts to verify accuracy."""
    
    print("🔍 ANALYZING ACTUAL SOLUTIONS VS RECEIPT TEXT")
    print("=" * 80)
    
    # Find sample receipts
    receipt_dir = Path("./receipt_data_with_labels")
    skip_files = {"manifest.json", "four_fields_simple_results.json", 
                  "metadata_impact_analysis.json", "pattern_vs_labels_comparison.json",
                  "required_fields_analysis.json", "spatial_line_item_results_labeled.json"}
    
    receipt_files = [f for f in receipt_dir.glob("*.json") if f.name not in skip_files]
    
    # Analyze first 10 receipts
    sample_files = receipt_files[:10]
    
    results = []
    for file_path in sample_files:
        result = await analyze_receipt_solution(file_path)
        if result:
            results.append(result)
    
    # Display results
    for i, result in enumerate(results):
        print(f"\n{'='*80}")
        print(f"RECEIPT {i+1}: {result['file']}")
        print(f"Merchant: {result['merchant']}")
        print(f"Confidence: {result['confidence']:.2f}")
        print(f"\nDETECTED VALUES:")
        print(f"  Grand Total: ${result['grand_total']:.2f}")
        print(f"  Subtotal: ${result['subtotal']:.2f}")
        if result['tax']:
            print(f"  Tax: ${result['tax']:.2f}")
        print(f"  Line Items ({len(result['line_items'])}): {[f'${x:.2f}' for x in result['line_items'][:5]]}")
        
        print(f"\nACTUAL RECEIPT TEXT:")
        if 'grand_total_line' in result:
            print(f"  Grand Total (Line {result['grand_total_line_num']}): \"{result['grand_total_line']}\"")
        if 'subtotal_line' in result:
            print(f"  Subtotal (Line {result['subtotal_line_num']}): \"{result['subtotal_line']}\"")
        if 'tax_line' in result:
            print(f"  Tax (Line {result['tax_line_num']}): \"{result['tax_line']}\"")
        
        print(f"\nBOTTOM OF RECEIPT:")
        for line in result['bottom_lines'][-5:]:
            print(f"  {line}")
    
    print(f"\n{'='*80}")
    print(f"✅ Analyzed {len(results)} receipts with solutions")


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(analyze_sample_receipts())