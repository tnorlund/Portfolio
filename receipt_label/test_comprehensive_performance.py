#!/usr/bin/env python3
"""
Comprehensive performance test on a larger sample of real receipts.
"""

import os
import time
import asyncio
from pathlib import Path
import random

# Set up environment  
os.environ["OPENAI_API_KEY"] = "sk-dummy"

from receipt_label.spatial.math_solver_detector import MathSolverDetector
from receipt_label.spatial.vertical_alignment_detector import VerticalAlignmentDetector
from receipt_label.pattern_detection.orchestrator import ParallelPatternOrchestrator
import json
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
        return [], "Unknown"


async def test_comprehensive_performance():
    """Test performance on a larger sample of real receipts."""
    
    print("📊 COMPREHENSIVE PERFORMANCE TEST ON REAL RECEIPTS")
    print("=" * 60)
    
    # Find all receipt files
    receipt_dir = Path("./receipt_data_with_labels")
    all_files = list(receipt_dir.glob("*.json"))
    
    # Test on a random sample of 30 receipts
    sample_size = min(30, len(all_files))
    sample_files = random.sample(all_files, sample_size)
    
    print(f"📄 Testing {sample_size} random receipts from {len(all_files)} total")
    
    # Create detectors
    pattern_orchestrator = ParallelPatternOrchestrator(timeout=10.0, use_adaptive_selection=False)
    alignment_detector = VerticalAlignmentDetector(alignment_tolerance=0.02)
    
    # Test with NumPy optimization
    math_solver_optimized = MathSolverDetector(tolerance=0.02, max_solutions=50, use_numpy_optimization=True)
    
    results = []
    total_time = 0
    
    print("\n🔄 Processing receipts...")
    
    for i, file_path in enumerate(sample_files):
        if i % 10 == 0:
            print(f"   Progress: {i}/{sample_size}")
        
        # Load receipt
        words, merchant = load_receipt_words(file_path)
        if not words:
            continue
        
        start_time = time.time()
        
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
            
            # Run spatial analysis
            price_columns = alignment_detector.detect_price_columns([v[1] for v in currency_values])
            
            # Filter to price columns if found
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
            
            # Run math solver
            solutions = math_solver_optimized.solve_receipt_math(column_currencies)
            
            processing_time = time.time() - start_time
            total_time += processing_time
            
            results.append({
                'file': file_path.name,
                'merchant': merchant,
                'currency_count': len(currency_values),
                'filtered_count': len(column_currencies),
                'price_columns': len(price_columns),
                'solutions': len(solutions),
                'processing_time': processing_time,
                'success': True
            })
            
        except Exception as e:
            processing_time = time.time() - start_time
            total_time += processing_time
            
            results.append({
                'file': file_path.name,
                'merchant': merchant,
                'currency_count': 0,
                'filtered_count': 0,
                'price_columns': 0,
                'solutions': 0,
                'processing_time': processing_time,
                'success': False,
                'error': str(e)
            })
    
    # Analysis
    print(f"\n📈 COMPREHENSIVE PERFORMANCE RESULTS")
    print("=" * 60)
    
    successful = [r for r in results if r['success']]
    failed = [r for r in results if not r['success']]
    
    print(f"📊 Receipt Analysis:")
    print(f"   Total receipts tested: {len(results)}")
    print(f"   Successful: {len(successful)} ({len(successful)/len(results)*100:.1f}%)")
    print(f"   Failed: {len(failed)} ({len(failed)/len(results)*100:.1f}%)")
    
    if successful:
        # Performance metrics
        times = [r['processing_time'] for r in successful]
        avg_time = sum(times) / len(times)
        max_time = max(times)
        min_time = min(times)
        
        print(f"\n⏱️  Processing Time Analysis:")
        print(f"   Average time per receipt: {avg_time*1000:.1f}ms")
        print(f"   Fastest receipt: {min_time*1000:.1f}ms")
        print(f"   Slowest receipt: {max_time*1000:.1f}ms")
        print(f"   Total processing time: {total_time:.2f}s")
        
        # Currency complexity analysis
        currency_counts = [r['currency_count'] for r in successful]
        avg_currencies = sum(currency_counts) / len(currency_counts)
        max_currencies = max(currency_counts)
        
        print(f"\n💰 Currency Complexity:")
        print(f"   Average currencies per receipt: {avg_currencies:.1f}")
        print(f"   Maximum currencies: {max_currencies}")
        print(f"   Receipts with >15 currencies: {sum(1 for c in currency_counts if c > 15)}")
        print(f"   Receipts with >20 currencies: {sum(1 for c in currency_counts if c > 20)}")
        
        # Spatial analysis
        spatial_successes = [r for r in successful if r['price_columns'] > 0]
        print(f"\n🎯 Spatial Analysis:")
        print(f"   Receipts with price columns: {len(spatial_successes)} ({len(spatial_successes)/len(successful)*100:.1f}%)")
        
        if spatial_successes:
            avg_columns = sum(r['price_columns'] for r in spatial_successes) / len(spatial_successes)
            print(f"   Average price columns: {avg_columns:.1f}")
        
        # Math solver performance
        solution_counts = [r['solutions'] for r in successful]
        avg_solutions = sum(solution_counts) / len(solution_counts)
        receipts_with_solutions = sum(1 for c in solution_counts if c > 0)
        
        print(f"\n🧮 Math Solver Performance:")
        print(f"   Receipts with solutions: {receipts_with_solutions} ({receipts_with_solutions/len(successful)*100:.1f}%)")
        print(f"   Average solutions per receipt: {avg_solutions:.1f}")
        
        # Performance by complexity
        print(f"\n📊 Performance by Complexity:")
        simple = [r for r in successful if r['currency_count'] <= 10]
        medium = [r for r in successful if 10 < r['currency_count'] <= 20]
        complex_receipts = [r for r in successful if r['currency_count'] > 20]
        
        if simple:
            avg_simple = sum(r['processing_time'] for r in simple) / len(simple)
            print(f"   Simple (≤10 currencies): {len(simple)} receipts, {avg_simple*1000:.1f}ms avg")
        
        if medium:
            avg_medium = sum(r['processing_time'] for r in medium) / len(medium)
            print(f"   Medium (11-20 currencies): {len(medium)} receipts, {avg_medium*1000:.1f}ms avg")
        
        if complex_receipts:
            avg_complex = sum(r['processing_time'] for r in complex_receipts) / len(complex_receipts)
            print(f"   Complex (>20 currencies): {len(complex_receipts)} receipts, {avg_complex*1000:.1f}ms avg")
        
        # Extrapolation to full dataset
        print(f"\n🔮 Full Dataset Extrapolation:")
        estimated_total_time = avg_time * len(all_files)
        estimated_minutes = estimated_total_time / 60
        
        print(f"   Estimated time for {len(all_files)} receipts: {estimated_total_time:.1f}s ({estimated_minutes:.1f} minutes)")
        
        # Compare to original problem
        print(f"   Original problem: >5 minutes, hung on complex receipts")
        print(f"   With optimization: ~{estimated_minutes:.1f} minutes, handles all complexities")
        
        if estimated_minutes < 5:
            print(f"   ✅ SUCCESS: Under 5 minute target!")
        else:
            print(f"   ⚠️  Still over 5 minutes, may need further optimization")
    
    # Show some examples
    if failed:
        print(f"\n❌ Failed Examples:")
        for r in failed[:3]:
            print(f"   {r['file']}: {r.get('error', 'Unknown error')}")
    
    if successful:
        print(f"\n✅ Success Examples:")
        # Show fastest and slowest
        fastest = min(successful, key=lambda x: x['processing_time'])
        slowest = max(successful, key=lambda x: x['processing_time'])
        
        print(f"   Fastest: {fastest['file'][:30]} - {fastest['processing_time']*1000:.1f}ms ({fastest['currency_count']} currencies)")
        print(f"   Slowest: {slowest['file'][:30]} - {slowest['processing_time']*1000:.1f}ms ({slowest['currency_count']} currencies)")


if __name__ == "__main__":
    asyncio.run(test_comprehensive_performance())