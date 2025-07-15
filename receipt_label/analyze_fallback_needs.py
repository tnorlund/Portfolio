#!/usr/bin/env python3
"""
Analyze how often we need Pinecone fallback vs pure math+spatial approach.
Test the tier system: Math+Spatial first, Pinecone only when needed.
"""

import os
import asyncio
import json
from pathlib import Path
from typing import List, Dict
from collections import Counter, defaultdict

# Set up environment (no Pinecone API key - we want to test without it first)
os.environ["OPENAI_API_KEY"] = "sk-dummy"

from receipt_label.pattern_detection.orchestrator import ParallelPatternOrchestrator
from receipt_label.spatial.vertical_alignment_detector import VerticalAlignmentDetector
from receipt_label.spatial.math_solver_detector import MathSolverDetector
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
        print(f"Error loading {file_path}: {e}")
        return [], "Unknown"


def classify_solution_confidence(solutions: List, price_columns: List) -> str:
    """
    Classify whether we need Pinecone fallback based on math+spatial results.
    
    Returns:
    - 'high_confidence': Use math solution directly, no Pinecone needed
    - 'medium_confidence': Quick Pinecone validation recommended  
    - 'low_confidence': Full Pinecone semantic analysis needed
    - 'no_solution': Math failed completely, need semantic approach
    """
    if not solutions:
        return 'no_solution'
    
    # Check solution quality
    best_solution = max(solutions, key=lambda s: s.confidence)
    has_tax_structure = best_solution.tax is not None
    
    # Check spatial alignment quality
    best_column_confidence = max(c.confidence for c in price_columns) if price_columns else 0
    
    # Decision tree
    if len(solutions) == 1 and best_solution.confidence >= 0.9 and best_column_confidence >= 0.8:
        # Single high-confidence solution with good spatial alignment
        return 'high_confidence'
    elif len(solutions) <= 3 and best_solution.confidence >= 0.8 and has_tax_structure:
        # Few solutions, good math confidence, has tax structure
        return 'medium_confidence'  
    elif len(solutions) <= 10 and best_column_confidence >= 0.7:
        # Moderate number of solutions with decent spatial alignment
        return 'medium_confidence'
    else:
        # Many solutions or poor spatial alignment - need semantic help
        return 'low_confidence'


async def analyze_receipt(file_path: Path, pattern_orchestrator, 
                         alignment_detector, math_solver) -> Dict:
    """Analyze a single receipt using pure math+spatial approach."""
    words, merchant = load_receipt_words(file_path)
    
    if not words:
        return {
            'file': file_path.name,
            'merchant': merchant,
            'status': 'error',
            'error': 'Failed to load'
        }
    
    # Step 1: Pattern detection
    pattern_results = await pattern_orchestrator.detect_all_patterns(words)
    
    # Flatten matches
    all_matches = []
    for detector_name, matches in pattern_results.items():
        if detector_name != '_metadata':
            all_matches.extend(matches)
    
    # Step 2: Extract currency values with reasonable filtering
    currency_patterns = {
        'CURRENCY', 'GRAND_TOTAL', 'SUBTOTAL', 'TAX', 
        'DISCOUNT', 'UNIT_PRICE', 'LINE_TOTAL'
    }
    currency_values = []
    for match in all_matches:
        if match.pattern_type.name in currency_patterns and match.extracted_value:
            try:
                value = float(match.extracted_value)
                # Filter reasonable grocery receipt values
                if 0.001 <= abs(value) <= 999.99:
                    currency_values.append((value, match))
            except (ValueError, TypeError):
                continue
    
    if not currency_values:
        return {
            'file': file_path.name,
            'merchant': merchant,
            'status': 'no_currencies',
            'fallback_needed': 'no_solution',
            'currency_count': 0
        }
    
    # Step 3: Detect price columns (spatial alignment)
    price_columns = alignment_detector.detect_price_columns([v[1] for v in currency_values])
    
    # Step 4: Filter currencies to only those in price columns
    if price_columns:
        column_lines = set()
        for column in price_columns:
            column_lines.update(p.word.line_id for p in column.prices)
        
        column_currencies = [
            (value, match) for value, match in currency_values
            if match.word.line_id in column_lines
        ]
    else:
        column_currencies = currency_values  # No spatial filtering possible
    
    # Step 5: Mathematical validation
    solutions = math_solver.solve_receipt_math(column_currencies)
    
    # Step 6: Classify confidence and fallback needs
    fallback_needed = classify_solution_confidence(solutions, price_columns)
    
    # Extract the best mathematical solution
    best_solution = None
    if solutions:
        best_solution = max(solutions, key=lambda s: s.confidence)
    
    return {
        'file': file_path.name,
        'merchant': merchant,
        'status': 'analyzed',
        'fallback_needed': fallback_needed,
        'currency_count': len(currency_values),
        'filtered_currency_count': len(column_currencies),
        'price_columns': len(price_columns),
        'math_solutions': len(solutions),
        'best_solution_confidence': best_solution.confidence if best_solution else 0,
        'has_tax_structure': best_solution.tax is not None if best_solution else False,
        'grand_total': best_solution.grand_total[0] if best_solution else None,
        'subtotal': best_solution.subtotal if best_solution else None,
        'tax': best_solution.tax[0] if best_solution and best_solution.tax else None,
        'item_count': len(best_solution.item_prices) if best_solution else 0,
        'best_column_confidence': max(c.confidence for c in price_columns) if price_columns else 0
    }


async def analyze_all_receipts():
    """Analyze fallback needs across all receipts."""
    
    print("🧮 ANALYZING MATH+SPATIAL vs PINECONE FALLBACK NEEDS")
    print("=" * 70)
    
    # Find all receipt files
    receipt_dir = Path("./receipt_data_with_labels")
    all_files = list(receipt_dir.glob("*.json"))
    
    print(f"📋 Found {len(all_files)} total receipts")
    
    # Create detectors (no Pinecone - pure math+spatial only)
    pattern_orchestrator = ParallelPatternOrchestrator(timeout=10.0, use_adaptive_selection=False)
    alignment_detector = VerticalAlignmentDetector(alignment_tolerance=0.02)
    math_solver = MathSolverDetector(tolerance=0.02, max_solutions=50, use_numpy_optimization=True)
    
    # Analyze each receipt
    all_results = []
    fallback_stats = Counter()
    merchant_stats = defaultdict(lambda: Counter())
    
    for i, file_path in enumerate(all_files):
        if i % 20 == 0:
            print(f"Processing {i}/{len(all_files)}...")
            
        result = await analyze_receipt(file_path, pattern_orchestrator, 
                                     alignment_detector, math_solver)
        all_results.append(result)
        
        # Track statistics
        fallback_needed = result.get('fallback_needed', 'error')
        fallback_stats[fallback_needed] += 1
        
        # Track by merchant
        merchant = result.get('merchant', 'Unknown')
        if 'Sprouts' in merchant:
            merchant_key = 'Sprouts'
        elif 'Walmart' in merchant or 'WAL-MART' in merchant:
            merchant_key = 'Walmart'
        elif 'Target' in merchant:
            merchant_key = 'Target'
        elif 'McDonald' in merchant:
            merchant_key = 'McDonalds'
        else:
            merchant_key = 'Other'
        
        merchant_stats[merchant_key][fallback_needed] += 1
    
    # Analysis and reporting
    print(f"\n🎯 FALLBACK ANALYSIS RESULTS")
    print("=" * 70)
    
    total_analyzed = sum(fallback_stats.values())
    
    print(f"\n📊 Overall Fallback Distribution:")
    print(f"   Total receipts analyzed: {total_analyzed}")
    for category, count in fallback_stats.most_common():
        percentage = (count / total_analyzed) * 100
        print(f"   {category:>15}: {count:4d} receipts ({percentage:5.1f}%)")
    
    # Calculate Pinecone usage reduction
    no_pinecone_needed = fallback_stats['high_confidence']
    quick_pinecone = fallback_stats['medium_confidence'] 
    full_pinecone = fallback_stats['low_confidence']
    
    current_approach_cost = total_analyzed  # Every receipt uses Pinecone
    new_approach_cost = (quick_pinecone * 0.3) + (full_pinecone * 1.0)  # 30% cost for quick, 100% for full
    cost_reduction = ((current_approach_cost - new_approach_cost) / current_approach_cost) * 100
    
    print(f"\n💰 Pinecone Cost Reduction Analysis:")
    print(f"   Current approach: {current_approach_cost} full Pinecone queries")
    print(f"   New approach: {new_approach_cost:.1f} equivalent Pinecone queries")
    print(f"   Cost reduction: {cost_reduction:.1f}%")
    
    print(f"\n🏪 Merchant Breakdown:")
    for merchant, stats in merchant_stats.items():
        total_merchant = sum(stats.values())
        if total_merchant >= 5:  # Only show merchants with enough data
            print(f"   {merchant} ({total_merchant} receipts):")
            for category, count in stats.most_common():
                percentage = (count / total_merchant) * 100
                print(f"     {category:>15}: {count:3d} ({percentage:4.1f}%)")
    
    print(f"\n✅ High-Confidence Success Examples:")
    high_confidence_examples = [r for r in all_results if r.get('fallback_needed') == 'high_confidence']
    
    # Show best examples
    for result in sorted(high_confidence_examples, 
                        key=lambda x: (x.get('best_solution_confidence', 0), x.get('item_count', 0)), 
                        reverse=True)[:5]:
        print(f"   📄 {result['file'][:30]:<30} | {result['merchant'][:15]:<15}")
        if result.get('grand_total'):
            print(f"      Total: ${result['grand_total']:>7.2f}, Items: {result['item_count']}, " +
                  f"Math confidence: {result['best_solution_confidence']:.2f}, " +
                  f"Spatial confidence: {result['best_column_confidence']:.2f}")
    
    print(f"\n⚠️  Cases Needing Pinecone Fallback:")
    problem_cases = [r for r in all_results if r.get('fallback_needed') in ['low_confidence', 'no_solution']]
    
    problem_reasons = Counter()
    for result in problem_cases:
        if result.get('math_solutions', 0) == 0:
            problem_reasons['No mathematical solutions'] += 1
        elif result.get('math_solutions', 0) > 20:
            problem_reasons['Too many math solutions'] += 1
        elif result.get('price_columns', 0) == 0:
            problem_reasons['No price columns detected'] += 1
        elif result.get('best_column_confidence', 0) < 0.5:
            problem_reasons['Poor spatial alignment'] += 1
        else:
            problem_reasons['Other complexity'] += 1
    
    print(f"   Problem analysis ({len(problem_cases)} receipts):")
    for reason, count in problem_reasons.most_common():
        percentage = (count / len(problem_cases)) * 100 if problem_cases else 0
        print(f"     {reason:>25}: {count:3d} ({percentage:4.1f}%)")


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.WARNING)  # Reduce noise
    asyncio.run(analyze_all_receipts())