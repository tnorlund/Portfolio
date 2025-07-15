#!/usr/bin/env python3
"""
Simple validation to check if our spatial/math approach is finding reasonable totals.
Uses heuristics rather than ground truth labels.
"""

import os
import asyncio
from pathlib import Path
from collections import Counter, defaultdict
import json

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


def is_solution_reasonable(solution):
    """Check if a mathematical solution seems reasonable for a receipt."""
    # Basic sanity checks
    if solution.grand_total[0] < 0.01 or solution.grand_total[0] > 10000:
        return False, "Grand total out of reasonable range"
    
    if solution.subtotal < 0.01 or solution.subtotal > 10000:
        return False, "Subtotal out of reasonable range"
    
    # Grand total should be >= subtotal
    if solution.grand_total[0] < solution.subtotal:
        return False, "Grand total less than subtotal"
    
    # If there's tax, it should be reasonable (0-30% of subtotal)
    if solution.tax:
        tax_rate = solution.tax[0] / solution.subtotal if solution.subtotal > 0 else 0
        if tax_rate > 0.30:
            return False, f"Tax rate too high: {tax_rate*100:.1f}%"
    
    # Line items should sum to approximately the subtotal
    if solution.item_prices:
        items_sum = sum(price[0] for price in solution.item_prices)
        difference = abs(items_sum - solution.subtotal)
        if difference > 0.10:  # Allow 10 cents difference for rounding
            return False, f"Items sum ${items_sum:.2f} != subtotal ${solution.subtotal:.2f}"
    
    return True, "Solution appears reasonable"


async def analyze_receipt(file_path: Path):
    """Analyze a single receipt and check if results are reasonable."""
    
    # Load receipt
    words, merchant = load_receipt_words(file_path)
    if not words:
        return {
            'file': file_path.name,
            'merchant': merchant,
            'status': 'load_error'
        }
    
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
            return {
                'file': file_path.name,
                'merchant': merchant,
                'status': 'no_solution',
                'currency_count': len(currency_values),
                'filtered_count': len(column_currencies)
            }
        
        # Get best solution
        best_solution = max(solutions, key=lambda s: s.confidence)
        
        # Check if solution is reasonable
        is_reasonable, reason = is_solution_reasonable(best_solution)
        
        # Calculate confidence
        spatial_analysis = {
            'best_column_confidence': alignment_result['best_column_confidence'],
            'x_alignment_tightness': alignment_result.get('x_alignment_tightness', 0),
            'font_consistency_confidence': alignment_result.get('font_consistency_confidence', 0),
            'has_large_font_patterns': alignment_result.get('has_large_font_patterns', False)
        }
        
        math_score = best_solution.confidence
        spatial_score = spatial_analysis['best_column_confidence']
        
        if spatial_analysis['x_alignment_tightness'] > 0.9:
            spatial_score *= 1.1
        if spatial_analysis['has_large_font_patterns']:
            spatial_score *= 1.1
        if spatial_analysis['font_consistency_confidence'] > 0.6:
            spatial_score *= 1.05
        
        combined_score = (math_score + spatial_score) / 2
        
        if combined_score >= 0.85:
            confidence_level = 'high_confidence'
        elif combined_score >= 0.7:
            confidence_level = 'medium_confidence'
        elif combined_score >= 0.5:
            confidence_level = 'low_confidence'
        else:
            confidence_level = 'no_confidence'
        
        return {
            'file': file_path.name,
            'merchant': merchant,
            'status': 'analyzed',
            'confidence_level': confidence_level,
            'is_reasonable': is_reasonable,
            'reason': reason,
            'grand_total': best_solution.grand_total[0],
            'subtotal': best_solution.subtotal,
            'tax': best_solution.tax[0] if best_solution.tax else None,
            'item_count': len(best_solution.item_prices),
            'math_confidence': best_solution.confidence,
            'spatial_confidence': spatial_analysis['best_column_confidence'],
            'solution_count': len(solutions)
        }
        
    except Exception as e:
        return {
            'file': file_path.name,
            'merchant': merchant,
            'status': 'error',
            'error': str(e)[:100]
        }


async def run_simple_validation():
    """Run simple validation on all receipts."""
    
    print("🔍 SIMPLE VALIDATION - CHECKING SOLUTION REASONABLENESS")
    print("=" * 60)
    
    # Find all receipt files
    receipt_dir = Path("./receipt_data_with_labels")
    all_files = list(receipt_dir.glob("*.json"))
    
    # Skip non-receipt files
    skip_files = {"manifest.json", "four_fields_simple_results.json", 
                  "metadata_impact_analysis.json", "pattern_vs_labels_comparison.json",
                  "required_fields_analysis.json", "spatial_line_item_results_labeled.json"}
    
    receipt_files = [f for f in all_files if f.name not in skip_files]
    
    print(f"📄 Found {len(receipt_files)} receipts to analyze")
    
    # Process in batches
    results = []
    batch_size = 10
    
    for i in range(0, len(receipt_files), batch_size):
        batch = receipt_files[i:i+batch_size]
        if i % 50 == 0:
            print(f"Processing receipts {i+1}-{min(i+batch_size, len(receipt_files))}...")
        
        batch_results = await asyncio.gather(*[analyze_receipt(f) for f in batch])
        results.extend(batch_results)
    
    # Analyze results
    print(f"\n📊 ANALYSIS RESULTS")
    print("=" * 60)
    
    # Count by status
    status_counts = Counter(r['status'] for r in results)
    print(f"\nProcessing Status:")
    for status, count in status_counts.most_common():
        print(f"  {status}: {count} ({count/len(results)*100:.1f}%)")
    
    # Analyze successful results
    analyzed = [r for r in results if r['status'] == 'analyzed']
    
    if analyzed:
        print(f"\n✅ Successfully Analyzed: {len(analyzed)} receipts")
        
        # Confidence distribution
        confidence_dist = Counter(r['confidence_level'] for r in analyzed)
        print(f"\nConfidence Distribution:")
        for level, count in [('high_confidence', confidence_dist.get('high_confidence', 0)),
                            ('medium_confidence', confidence_dist.get('medium_confidence', 0)),
                            ('low_confidence', confidence_dist.get('low_confidence', 0)),
                            ('no_confidence', confidence_dist.get('no_confidence', 0))]:
            print(f"  {level}: {count} ({count/len(analyzed)*100:.1f}%)")
        
        # Reasonableness check
        reasonable_count = sum(1 for r in analyzed if r['is_reasonable'])
        print(f"\nSolution Reasonableness:")
        print(f"  Reasonable: {reasonable_count} ({reasonable_count/len(analyzed)*100:.1f}%)")
        print(f"  Unreasonable: {len(analyzed)-reasonable_count} ({(len(analyzed)-reasonable_count)/len(analyzed)*100:.1f}%)")
        
        # High confidence + reasonable
        high_conf_reasonable = sum(1 for r in analyzed 
                                  if r['confidence_level'] == 'high_confidence' 
                                  and r['is_reasonable'])
        high_conf_total = sum(1 for r in analyzed if r['confidence_level'] == 'high_confidence')
        
        print(f"\n🏆 HIGH CONFIDENCE RESULTS:")
        print(f"  Total high confidence: {high_conf_total}")
        if high_conf_total > 0:
            print(f"  Reasonable solutions: {high_conf_reasonable} ({high_conf_reasonable/high_conf_total*100:.1f}%)")
            print(f"  These would be processed WITHOUT Pinecone!")
        
        # Cost reduction calculation
        cost_reduction = (high_conf_reasonable / len(analyzed)) * 100
        print(f"\n💰 EFFECTIVE COST REDUCTION: {cost_reduction:.1f}%")
        print(f"   (High confidence + reasonable solutions)")
        
        # Show examples of unreasonable solutions
        unreasonable = [r for r in analyzed if not r['is_reasonable']]
        if unreasonable:
            print(f"\n❌ Examples of Unreasonable Solutions:")
            for r in unreasonable[:5]:
                print(f"\n  {r['file']} ({r['merchant']}):")
                print(f"    Reason: {r['reason']}")
                print(f"    Total: ${r['grand_total']:.2f}, Subtotal: ${r['subtotal']:.2f}")
                if r['tax'] is not None:
                    print(f"    Tax: ${r['tax']:.2f}")
        
        # Merchant breakdown
        merchant_stats = defaultdict(lambda: {'total': 0, 'reasonable': 0})
        for r in analyzed:
            merchant = r['merchant']
            if 'Sprouts' in merchant:
                merchant_key = 'Sprouts'
            elif 'Walmart' in merchant or 'WAL-MART' in merchant:
                merchant_key = 'Walmart'
            elif 'Target' in merchant:
                merchant_key = 'Target'
            else:
                merchant_key = 'Other'
            
            merchant_stats[merchant_key]['total'] += 1
            if r['is_reasonable']:
                merchant_stats[merchant_key]['reasonable'] += 1
        
        print(f"\n🏪 Results by Merchant:")
        for merchant, stats in sorted(merchant_stats.items()):
            if stats['total'] > 0:
                rate = stats['reasonable'] / stats['total'] * 100
                print(f"  {merchant}: {stats['reasonable']}/{stats['total']} reasonable ({rate:.1f}%)")


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(run_simple_validation())