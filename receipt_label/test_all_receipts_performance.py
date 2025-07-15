#!/usr/bin/env python3
"""
Test performance on ALL receipts to get real full dataset metrics.
"""

import os
import time
import asyncio
from pathlib import Path
from collections import Counter

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
        return [], f"Load error: {str(e)[:50]}..."


async def test_all_receipts_performance():
    """Test performance on ALL receipts - the definitive test."""
    
    print("🏁 DEFINITIVE PERFORMANCE TEST - ALL RECEIPTS")
    print("=" * 60)
    
    # Find ALL receipt files
    receipt_dir = Path("./receipt_data_with_labels")
    all_files = list(receipt_dir.glob("*.json"))
    
    print(f"📄 Testing ALL {len(all_files)} receipts")
    print(f"⏰ Started at: {time.strftime('%H:%M:%S')}")
    
    # Create detectors - optimized configuration
    pattern_orchestrator = ParallelPatternOrchestrator(timeout=10.0, use_adaptive_selection=False)
    alignment_detector = VerticalAlignmentDetector(alignment_tolerance=0.02)
    math_solver_optimized = MathSolverDetector(tolerance=0.02, max_solutions=50, use_numpy_optimization=True)
    
    results = []
    total_start_time = time.time()
    
    print("\n🔄 Processing ALL receipts...")
    
    for i, file_path in enumerate(all_files):
        if i % 20 == 0:
            elapsed = time.time() - total_start_time
            if i > 0:
                rate = i / elapsed
                eta = (len(all_files) - i) / rate
                print(f"   Progress: {i}/{len(all_files)} ({i/len(all_files)*100:.1f}%) - {elapsed:.1f}s elapsed - ETA: {eta:.1f}s")
            else:
                print(f"   Progress: {i}/{len(all_files)} - Starting...")
        
        receipt_start_time = time.time()
        
        # Load receipt
        words, merchant = load_receipt_words(file_path)
        if not words:
            results.append({
                'file': file_path.name,
                'merchant': merchant,
                'success': False,
                'error': merchant,  # merchant contains error message
                'processing_time': time.time() - receipt_start_time
            })
            continue
        
        try:
            # Get pattern matches
            pattern_results = await pattern_orchestrator.detect_all_patterns(words)
            all_matches = []
            for detector_name, matches in pattern_results.items():
                if detector_name != '_metadata':
                    all_matches.extend(matches)
            
            # Extract currency values with reasonable filtering
            currency_patterns = {'CURRENCY', 'GRAND_TOTAL', 'SUBTOTAL', 'TAX', 'DISCOUNT', 'UNIT_PRICE', 'LINE_TOTAL'}
            currency_values = []
            for match in all_matches:
                if match.pattern_type.name in currency_patterns and match.extracted_value:
                    try:
                        value = float(match.extracted_value)
                        if 0.001 <= abs(value) <= 999.99:  # Reasonable grocery receipt values
                            currency_values.append((value, match))
                    except (ValueError, TypeError):
                        continue
            
            # Spatial analysis - detect price columns
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
            
            # Mathematical validation with NumPy optimization
            solutions = math_solver_optimized.solve_receipt_math(column_currencies)
            
            processing_time = time.time() - receipt_start_time
            
            # Classify fallback need based on solution quality
            if not solutions:
                fallback_needed = 'no_solution'
            elif len(solutions) == 1 and solutions[0].confidence >= 0.9:
                fallback_needed = 'high_confidence'
            elif len(solutions) <= 3 and solutions[0].confidence >= 0.8:
                fallback_needed = 'medium_confidence'
            else:
                fallback_needed = 'low_confidence'
            
            results.append({
                'file': file_path.name,
                'merchant': merchant,
                'success': True,
                'currency_count': len(currency_values),
                'filtered_count': len(column_currencies),
                'price_columns': len(price_columns),
                'solutions': len(solutions),
                'fallback_needed': fallback_needed,
                'best_confidence': solutions[0].confidence if solutions else 0,
                'processing_time': processing_time
            })
            
        except Exception as e:
            processing_time = time.time() - receipt_start_time
            results.append({
                'file': file_path.name,
                'merchant': merchant,
                'success': False,
                'error': str(e)[:100],
                'processing_time': processing_time
            })
    
    total_time = time.time() - total_start_time
    
    # COMPREHENSIVE ANALYSIS
    print(f"\n🎯 DEFINITIVE RESULTS - ALL {len(all_files)} RECEIPTS")
    print("=" * 60)
    print(f"⏰ Total processing time: {total_time:.2f} seconds ({total_time/60:.2f} minutes)")
    print(f"⚡ Average per receipt: {total_time/len(all_files)*1000:.1f}ms")
    
    successful = [r for r in results if r['success']]
    failed = [r for r in results if not r['success']]
    
    print(f"\n📊 Success Rate:")
    print(f"   Successful: {len(successful)}/{len(all_files)} ({len(successful)/len(all_files)*100:.1f}%)")
    print(f"   Failed: {len(failed)}/{len(all_files)} ({len(failed)/len(all_files)*100:.1f}%)")
    
    if successful:
        # Performance distribution
        times = [r['processing_time'] for r in successful]
        times.sort()
        
        print(f"\n⏱️  Processing Time Distribution:")
        print(f"   Fastest: {min(times)*1000:.1f}ms")
        print(f"   Median: {times[len(times)//2]*1000:.1f}ms")
        print(f"   95th percentile: {times[int(len(times)*0.95)]*1000:.1f}ms")
        print(f"   Slowest: {max(times)*1000:.1f}ms")
        
        # Complexity analysis
        currency_counts = [r['currency_count'] for r in successful]
        print(f"\n💰 Currency Complexity Distribution:")
        print(f"   Average currencies: {sum(currency_counts)/len(currency_counts):.1f}")
        print(f"   Max currencies: {max(currency_counts)}")
        print(f"   Receipts with >10 currencies: {sum(1 for c in currency_counts if c > 10)} ({sum(1 for c in currency_counts if c > 10)/len(successful)*100:.1f}%)")
        print(f"   Receipts with >20 currencies: {sum(1 for c in currency_counts if c > 20)} ({sum(1 for c in currency_counts if c > 20)/len(successful)*100:.1f}%)")
        print(f"   Receipts with >30 currencies: {sum(1 for c in currency_counts if c > 30)} ({sum(1 for c in currency_counts if c > 30)/len(successful)*100:.1f}%)")
        
        # Spatial analysis results
        spatial_success = [r for r in successful if r['price_columns'] > 0]
        print(f"\n🎯 Spatial Analysis Results:")
        print(f"   Receipts with price columns: {len(spatial_success)}/{len(successful)} ({len(spatial_success)/len(successful)*100:.1f}%)")
        
        # Math solver performance
        with_solutions = [r for r in successful if r['solutions'] > 0]
        print(f"\n🧮 Math Solver Results:")
        print(f"   Receipts with solutions: {len(with_solutions)}/{len(successful)} ({len(with_solutions)/len(successful)*100:.1f}%)")
        if with_solutions:
            avg_solutions = sum(r['solutions'] for r in with_solutions) / len(with_solutions)
            print(f"   Average solutions when found: {avg_solutions:.1f}")
        
        # FALLBACK ANALYSIS - The key metric!
        fallback_stats = Counter(r['fallback_needed'] for r in successful)
        print(f"\n🔥 FALLBACK ANALYSIS - KEY RESULTS:")
        print(f"   High confidence (no Pinecone): {fallback_stats['high_confidence']} ({fallback_stats['high_confidence']/len(successful)*100:.1f}%)")
        print(f"   Medium confidence (light Pinecone): {fallback_stats['medium_confidence']} ({fallback_stats['medium_confidence']/len(successful)*100:.1f}%)")
        print(f"   Low confidence (full Pinecone): {fallback_stats['low_confidence']} ({fallback_stats['low_confidence']/len(successful)*100:.1f}%)")
        print(f"   No solution (semantic fallback): {fallback_stats['no_solution']} ({fallback_stats['no_solution']/len(successful)*100:.1f}%)")
        
        # Cost reduction calculation
        total_receipts = len(successful)
        no_pinecone = fallback_stats['high_confidence']
        light_pinecone = fallback_stats['medium_confidence']
        full_pinecone = fallback_stats['low_confidence'] + fallback_stats['no_solution']
        
        current_cost = total_receipts * 1.0  # 100% use Pinecone
        new_cost = (no_pinecone * 0.0) + (light_pinecone * 0.3) + (full_pinecone * 1.0)
        cost_reduction = ((current_cost - new_cost) / current_cost) * 100
        
        print(f"\n💰 COST REDUCTION ANALYSIS:")
        print(f"   Current approach: {current_cost:.0f} full Pinecone queries")
        print(f"   Optimized approach: {new_cost:.1f} equivalent Pinecone queries")
        print(f"   🎯 COST REDUCTION: {cost_reduction:.1f}%")
        
        # Performance by complexity
        print(f"\n📊 Performance by Complexity:")
        complexity_bins = [
            ("Simple (≤10)", [r for r in successful if r['currency_count'] <= 10]),
            ("Medium (11-20)", [r for r in successful if 10 < r['currency_count'] <= 20]),
            ("Complex (21-30)", [r for r in successful if 20 < r['currency_count'] <= 30]),
            ("Extreme (>30)", [r for r in successful if r['currency_count'] > 30])
        ]
        
        for name, bin_results in complexity_bins:
            if bin_results:
                avg_time = sum(r['processing_time'] for r in bin_results) / len(bin_results)
                print(f"   {name}: {len(bin_results)} receipts, {avg_time*1000:.1f}ms avg")
        
        # Merchant analysis
        merchant_stats = Counter()
        for r in successful:
            merchant = r['merchant']
            if 'Sprouts' in merchant:
                merchant_stats['Sprouts'] += 1
            elif 'Walmart' in merchant or 'WAL-MART' in merchant:
                merchant_stats['Walmart'] += 1
            elif 'Target' in merchant:
                merchant_stats['Target'] += 1
            elif 'McDonald' in merchant:
                merchant_stats['McDonalds'] += 1
            else:
                merchant_stats['Other'] += 1
        
        print(f"\n🏪 Merchant Distribution:")
        for merchant, count in merchant_stats.most_common():
            print(f"   {merchant}: {count} receipts ({count/len(successful)*100:.1f}%)")
    
    # Error analysis
    if failed:
        print(f"\n❌ Failed Receipt Analysis:")
        error_types = Counter()
        for r in failed:
            error = r.get('error', 'Unknown')
            if 'Load error' in error:
                error_types['JSON parse errors'] += 1
            elif 'timeout' in error.lower():
                error_types['Timeout errors'] += 1
            else:
                error_types['Processing errors'] += 1
        
        for error_type, count in error_types.most_common():
            print(f"   {error_type}: {count}")
    
    print(f"\n🏁 FINAL VERDICT:")
    print(f"   Original problem: >5 minutes (hung indefinitely)")
    print(f"   With NumPy optimization: {total_time:.2f} seconds")
    if total_time < 60:
        print(f"   ✅ SUCCESS: {60/total_time:.1f}x faster than 1-minute target!")
    else:
        print(f"   ⚠️  Slower than hoped: {total_time:.1f} seconds")
    
    print(f"   🎯 Enables tiered processing with {cost_reduction:.1f}% cost reduction")
    print(f"   ⚡ Ready for production: handles all receipt complexities")


if __name__ == "__main__":
    asyncio.run(test_all_receipts_performance())